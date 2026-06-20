//! Reversal-Swing backtest sweep — 2-D (stop_mult × entry mode), IS/OOS split.
//!
//! Reuses the REAL rewired `SwingStrategy` (which now places resting LIMIT_MAKER
//! TP1 + STOP_LOSS at entry, replaces the stop on TP1 fill, cancels on reactive
//! exit). The harness owns only the EXECUTION + cost layer: it captures the
//! resting orders the strategy returns from on_fill, simulates them bar-by-bar
//! (maker TP1 fills when a bar trades up through the limit; STOP_LOSS fills when
//! a bar trades down through the stop), and drains pending_cancels — exactly
//! what the live paper engine does, plus a cost model.
//!
//! Faithfulness rules:
//!  * No look-ahead: on_tick's decision on bar i executes at bar i+1's open for
//!    marketable orders; resting orders resolve against the bar they're live in.
//!  * Pessimistic maker fills: a LIMIT_MAKER (entry Mode B or TP1) fills only if a
//!    bar trades through the limit. A bounce that runs away is a NO-FILL — Mode B
//!    abandons, Mode B′ escalates. This is the adverse-selection measurement.
//!  * Real fee tier: maker = taker = 0.10% on standard Binance.com. Taker legs
//!    add slippage; maker legs (Mode B entry fill, TP1) do not.
//!  * Chronological OOS split (last 40%) reported per cell.

use std::collections::HashMap;
use std::fs::File;
use std::io::{BufRead, BufReader};
use trading_engine_core::config::{RunnerExitMode, SwingConfig};
use trading_engine_core::connector::types::{Fill, OrderBook, OrderRequest, OrderTypeReq};
use trading_engine_core::models::bar::Bar;
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::notifications::TelegramBot;
use trading_engine_core::strategy::swing::SwingStrategy;
use trading_engine_core::strategy::{Strategy, TickContext};

const WINDOW: usize = 600;
const ENTRY_TIMEOUT_BARS: i64 = 2;
const OOS_FRAC: f64 = 0.40;

#[derive(Clone, Copy, Debug)]
enum EntryMode { A, B, Bprime }

#[derive(Clone, Copy)]
struct CostModel { maker_fee: f64, taker_fee: f64, entry_slip: f64, exit_slip: f64 }
impl CostModel {
    const STD_BINANCE: CostModel = CostModel {
        maker_fee: 0.001, taker_fee: 0.001, entry_slip: 0.0004, exit_slip: 0.0003,
    };
}

#[derive(Default, Clone, Copy)]
struct CellResult { trades: usize, wins: usize, gross_pnl: f64, costs: f64, max_dd: f64 }

#[derive(Clone, Copy)]
struct HPos { entry_price: f64, remaining: f64 }
struct RestingEntry { limit: f64, qty: f64, bars_left: i64 }      // Mode B maker ENTRY
struct PendingMarket { qty: f64, is_buy: bool }
/// A resting protective order the strategy placed: TP1 (maker, fills on high≥price)
/// or STOP_LOSS (taker, fills on low≤price).
#[derive(Clone)]
struct RestProtect { price: f64, qty: f64, cid: String }

fn base_config(atr_stop_mult: f64) -> SwingConfig {
    SwingConfig {
        enabled: true, runner_exit: RunnerExitMode::BandOrChandelier,
        htf_period: "1h".to_string(), ltf_period: "5m".to_string(),
        donchian_period: 20, band_atr_mult: 0.5, rsi_period: 14, rsi_oversold: 30.0,
        volume_multiplier: 1.5, volume_avg_period: 20, atr_period: 14, atr_stop_mult,
        min_rr: 2.0, risk_per_trade_pct: 1.0, adx_range_entry: 22.0, adx_trend_exit: 28.0,
        capital: 10_000.0, max_bars_in_trade: 48,
        enabled_pairs: vec![], step_size: None, tick_size: None,
    }
}

fn load_csv(path: &str) -> Vec<Bar> {
    let f = File::open(path).expect("open csv");
    let mut bars = Vec::new();
    for line in BufReader::new(f).lines().skip(1) {
        let ln = line.expect("read");
        let p: Vec<&str> = ln.split(',').collect();
        if p.len() < 6 { continue; }
        bars.push(Bar::new(
            p[1].parse().unwrap_or(0.0), p[2].parse().unwrap_or(0.0),
            p[3].parse().unwrap_or(0.0), p[4].parse().unwrap_or(0.0),
            p[5].parse().unwrap_or(0.0), p[0].parse::<i64>().unwrap_or(0),
        ));
    }
    bars
}

async fn apply_fill(strat: &mut SwingStrategy, side: OrderSide, price: f64, qty: f64, ts: i64, cid: &str) -> Vec<OrderRequest> {
    let fill = Fill {
        fill_id: format!("bt_{}", ts), order_id: format!("o_{}", ts),
        client_order_id: Some(cid.to_string()), symbol: "SWINGBT".to_string(),
        side, price, quantity: qty, fee: 0.0, timestamp: ts,
    };
    strat.on_fill(&fill).await.unwrap_or_default()
}

/// Capture any resting TP1 / STOP_LOSS the strategy returned from on_fill.
fn capture_resting(orders: &[OrderRequest], rtp1: &mut Option<RestProtect>, rstop: &mut Option<RestProtect>) {
    for o in orders {
        match o.order_type {
            OrderTypeReq::LimitMaker => {
                *rtp1 = Some(RestProtect {
                    price: o.price.unwrap_or(0.0), qty: o.quantity,
                    cid: o.client_order_id.clone().unwrap_or_default(),
                });
            }
            OrderTypeReq::StopMarket { stop_price } => {
                *rstop = Some(RestProtect {
                    price: stop_price, qty: o.quantity,
                    cid: o.client_order_id.clone().unwrap_or_default(),
                });
            }
            _ => {}
        }
    }
}

/// Apply strategy-requested cancels to the resting-protect book.
fn apply_cancels(rtp1: &mut Option<RestProtect>, rstop: &mut Option<RestProtect>, cancels: Vec<String>) {
    for cid in cancels {
        if rtp1.as_ref().map(|r| r.cid == cid).unwrap_or(false) { *rtp1 = None; }
        if rstop.as_ref().map(|r| r.cid == cid).unwrap_or(false) { *rstop = None; }
    }
}

/// Run one cell. Returns (full-period, oos-period) stats. IS = full − oos.
async fn run_cell(
    bars: &[Bar], cfg: &SwingConfig, mode: EntryMode, cost: CostModel, oos_start: usize,
) -> (CellResult, CellResult) {
    let mut strat = SwingStrategy::new("SWINGBT", cfg, TelegramBot::new("", ""));
    let _ = strat.on_start().await;
    let mut res = CellResult::default();
    let mut oos = CellResult::default();

    let mut hpos: Option<HPos> = None;
    let mut resting_entry: Option<RestingEntry> = None;
    let mut pending: Option<PendingMarket> = None;
    let mut rtp1: Option<RestProtect> = None;
    let mut rstop: Option<RestProtect> = None;
    let mut cash: f64 = 0.0;
    let mut peak: f64 = 0.0;
    let mut trade_net: f64 = 0.0;
    let mut cur_entry_idx: Option<usize> = None;
    let mut win_buf: Vec<Bar> = Vec::with_capacity(WINDOW);

    let in_oos = |eidx: Option<usize>| eidx.map(|e| e >= oos_start).unwrap_or(false);
    let mut book_trade = |res: &mut CellResult, oos: &mut CellResult, gross: f64, costs: f64,
                          net_inc: f64, eidx: Option<usize>, is_close: bool,
                          trade_net: &mut f64| {
        res.gross_pnl += gross; res.costs += costs;
        if in_oos(eidx) { oos.gross_pnl += gross; oos.costs += costs; }
        *trade_net += net_inc;
        if is_close {
            res.trades += 1; if *trade_net > 0.0 { res.wins += 1; }
            if in_oos(eidx) { oos.trades += 1; if *trade_net > 0.0 { oos.wins += 1; } }
            *trade_net = 0.0;
        }
    };

    let n = bars.len();
    let mut i = 0;
    while i < n {
        let (open, high, low, close, ts) = {
            let b = &bars[i]; (b.open, b.high, b.low, b.close, b.timestamp)
        };

        // 1) Fill a marketable order decided on bar i-1, at bar i's OPEN.
        if let Some(pm) = pending.take() {
            if pm.is_buy {
                let price = open + open * cost.entry_slip;
                let fee = price * pm.qty * cost.taker_fee;
                let slipc = price * pm.qty * cost.entry_slip;
                cash -= price * pm.qty + fee;
                cur_entry_idx = Some(i);
                trade_net = -(fee + slipc);
                let new_orders = apply_fill(&mut strat, OrderSide::Buy, price, pm.qty, ts, "entry").await;
                hpos = Some(HPos { entry_price: price, remaining: pm.qty });
                book_trade(&mut res, &mut oos, 0.0, fee + slipc, 0.0, Some(i), false, &mut trade_net);
                capture_resting(&new_orders, &mut rtp1, &mut rstop); // resting TP1 + stop placed
                apply_cancels(&mut rtp1, &mut rstop, strat.pending_cancels());
            } else {
                // Reactive Market exit (chandelier / opposite / time-stop).
                let price = open - open * cost.exit_slip;
                let fee = price * pm.qty * cost.taker_fee;
                let slipc = price * pm.qty * cost.exit_slip;
                cash += price * pm.qty - fee;
                let gross = hpos.map(|p| (price - p.entry_price) * pm.qty).unwrap_or(0.0);
                apply_fill(&mut strat, OrderSide::Sell, price, pm.qty, ts, "exit").await;
                if let Some(p) = hpos.as_mut() { p.remaining -= pm.qty; }
                let closed = hpos.map(|p| p.remaining <= 1e-8).unwrap_or(true);
                book_trade(&mut res, &mut oos, gross, fee + slipc, gross - (fee + slipc),
                    cur_entry_idx, closed, &mut trade_net);
                apply_cancels(&mut rtp1, &mut rstop, strat.pending_cancels());
                if closed { cur_entry_idx = None; hpos = None; }
            }
        }

        // 2) Resolve the position's resting TP1 / hard stop against bar i.
        if hpos.is_some() {
            // Hard stop first (adverse protection): fills if bar low <= stop.
            if let Some(rs) = rstop.clone() {
                if low <= rs.price {
                    let price = rs.price; // triggers ~at the stop (taker)
                    let fee = price * rs.qty * cost.taker_fee;
                    let slipc = price * rs.qty * cost.exit_slip;
                    cash += price * rs.qty - fee;
                    let gross = hpos.map(|p| (price - p.entry_price) * rs.qty).unwrap_or(0.0);
                    apply_fill(&mut strat, OrderSide::Sell, price, rs.qty, ts, &rs.cid).await;
                    rtp1 = None; rstop = None;
                    book_trade(&mut res, &mut oos, gross, fee + slipc, gross - (fee + slipc),
                        cur_entry_idx, true, &mut trade_net);
                    apply_cancels(&mut rtp1, &mut rstop, strat.pending_cancels()); // stale TP1 cancel
                    cur_entry_idx = None; hpos = None;
                    i += 1; continue;
                }
            }
            // TP1 (maker): fills if bar high >= tp1 limit, at the limit (no slip).
            if let Some(rt) = rtp1.clone() {
                if high >= rt.price {
                    let price = rt.price;
                    let fee = price * rt.qty * cost.maker_fee;
                    cash += price * rt.qty - fee;
                    let gross = hpos.map(|p| (price - p.entry_price) * rt.qty).unwrap_or(0.0);
                    let new_orders = apply_fill(&mut strat, OrderSide::Sell, price, rt.qty, ts, &rt.cid).await;
                    if let Some(p) = hpos.as_mut() { p.remaining -= rt.qty; }
                    rtp1 = None;
                    book_trade(&mut res, &mut oos, gross, fee, gross - fee,
                        cur_entry_idx, false, &mut trade_net);
                    apply_cancels(&mut rtp1, &mut rstop, strat.pending_cancels()); // old-stop cancel
                    capture_resting(&new_orders, &mut rtp1, &mut rstop); // runner stop placed
                }
            }
        }

        // 3) Resolve a resting maker ENTRY (Mode B/B′) against bar i (pessimistic).
        if let Some(re) = resting_entry.as_mut() {
            if low <= re.limit {
                let price = re.limit; // maker fill
                let fee = price * re.qty * cost.maker_fee;
                cash -= price * re.qty + fee;
                cur_entry_idx = Some(i);
                trade_net = -fee;
                let new_orders = apply_fill(&mut strat, OrderSide::Buy, price, re.qty, ts, "entry").await;
                hpos = Some(HPos { entry_price: price, remaining: re.qty });
                book_trade(&mut res, &mut oos, 0.0, fee, 0.0, Some(i), false, &mut trade_net);
                capture_resting(&new_orders, &mut rtp1, &mut rstop);
                apply_cancels(&mut rtp1, &mut rstop, strat.pending_cancels());
                resting_entry = None;
                i += 1; continue;
            } else {
                re.bars_left -= 1;
                if re.bars_left < 0 {
                    if matches!(mode, EntryMode::Bprime) {
                        let price = open + open * cost.entry_slip;
                        let fee = price * re.qty * cost.taker_fee;
                        let slipc = price * re.qty * cost.entry_slip;
                        cash -= price * re.qty + fee;
                        cur_entry_idx = Some(i);
                        trade_net = -(fee + slipc);
                        let new_orders = apply_fill(&mut strat, OrderSide::Buy, price, re.qty, ts, "entry").await;
                        hpos = Some(HPos { entry_price: price, remaining: re.qty });
                        book_trade(&mut res, &mut oos, 0.0, fee + slipc, 0.0, Some(i), false, &mut trade_net);
                        capture_resting(&new_orders, &mut rtp1, &mut rstop);
                        apply_cancels(&mut rtp1, &mut rstop, strat.pending_cancels());
                    }
                    resting_entry = None;
                }
                i += 1; continue;
            }
        }

        // 4) MTM equity / drawdown (full-period equity curve).
        let mtm = cash + hpos.map(|p| p.remaining * close).unwrap_or(0.0);
        if mtm > peak { peak = mtm; }
        let dd = peak - mtm;
        if dd > res.max_dd { res.max_dd = dd; }

        // 5) Feed bar i to the real strategy.
        let start = i.saturating_sub(WINDOW - 1);
        win_buf.clear();
        win_buf.extend_from_slice(&bars[start..=i]);
        let ctx = TickContext {
            order_book: OrderBook {
                symbol: "SWINGBT".to_string(),
                bids: vec![(close, 1.0)], asks: vec![(close, 1.0)], timestamp: ts,
            },
            recent_bars: std::mem::take(&mut win_buf),
            balances: HashMap::new(), open_orders: vec![],
            regime: None, regime_confidence: 0.0, timestamp: ts,
            capital: None,
        };
        let orders = match strat.on_tick(&ctx).await { Ok(o) => o, Err(_) => { win_buf = ctx.recent_bars; i += 1; continue; } };
        win_buf = ctx.recent_bars;
        for o in orders {
            let qty = o.quantity;
            if o.side == OrderSide::Buy {
                match mode {
                    EntryMode::A => pending = Some(PendingMarket { qty, is_buy: true }),
                    EntryMode::B | EntryMode::Bprime => {
                        resting_entry = Some(RestingEntry { limit: close, qty, bars_left: ENTRY_TIMEOUT_BARS });
                    }
                }
            } else {
                pending = Some(PendingMarket { qty, is_buy: false }); // reactive Market exit
            }
        }
        apply_cancels(&mut rtp1, &mut rstop, strat.pending_cancels()); // reactive exit cancelled resting
        i += 1;
    }

    // Force-liquidate anything still open at end-of-data (taker, last close).
    if let Some(p) = hpos {
        if p.remaining > 1e-8 {
            let last = bars.last().unwrap();
            let price = last.close - last.close * cost.exit_slip;
            let fee = price * p.remaining * cost.taker_fee;
            let slipc = price * p.remaining * cost.exit_slip;
            cash += price * p.remaining - fee;
            let gross = (price - p.entry_price) * p.remaining;
            apply_fill(&mut strat, OrderSide::Sell, price, p.remaining, last.timestamp, "exit").await;
            book_trade(&mut res, &mut oos, gross, fee + slipc, gross - (fee + slipc),
                cur_entry_idx, true, &mut trade_net);
        }
    }
    let _ = cash;
    (res, oos)
}

#[tokio::main]
async fn main() {
    std::env::set_var("SWING_JOURNAL_PATH", "/tmp/swing_backtest_sweep.db");
    let _ = std::fs::remove_file("/tmp/swing_backtest_sweep.db");

    let pairs = ["BNBUSDT", "ETHUSDT", "DOGEUSDT", "XRPUSDT"];
    let span = "2025-01-01_2026-05-31";
    let cost = CostModel::STD_BINANCE;

    let stops: Vec<f64> = vec![1.0, 1.25, 1.5, 1.75, 2.0, 2.5];
    let modes = [EntryMode::A, EntryMode::B, EntryMode::Bprime];

    println!("swing sweep (REWIFIED: resting TP1+STOP_LOSS) | maker=taker=0.10% | entry_slip 4bps exit_slip 3bps | 0 slip on maker | OOS=last {}%",
        (OOS_FRAC * 100.0) as u32);
    println!("  full-period  ||  OOS-period (last 40%)");
    println!("{:<8} {:<6} {:>4} {:>5} {:>9} {:>9} || {:>4} {:>4} {:>9} {:>10}",
        "pair", "st/md", "trd", "win%", "netFull$", "expTrd$", "trd", "win%", "netOOS$", "expOOS$");

    for pair in pairs {
        let path = format!("../backtest/data_cache/{}_5m_{}.csv", pair, span);
        let bars = load_csv(&path);
        let oos_start = ((bars.len() as f64) * (1.0 - OOS_FRAC)) as usize;
        for &s in &stops {
            for &m in &modes {
                let cfg = base_config(s);
                let (full, oos) = run_cell(&bars, &cfg, m, cost, oos_start).await;
                let mode_s = match m { EntryMode::A => "A", EntryMode::B => "B", EntryMode::Bprime => "B'" };
                let wpf = if full.trades > 0 { 100.0 * full.wins as f64 / full.trades as f64 } else { 0.0 };
                let netf = full.gross_pnl - full.costs;
                let expf = if full.trades > 0 { netf / full.trades as f64 } else { 0.0 };
                let wpo = if oos.trades > 0 { 100.0 * oos.wins as f64 / oos.trades as f64 } else { 0.0 };
                let neto = oos.gross_pnl - oos.costs;
                let expo = if oos.trades > 0 { neto / oos.trades as f64 } else { 0.0 };
                println!("{:<8} {:>4}/{:<1} {:>4} {:>4.0}% {:>9.1} {:>9.2} || {:>4} {:>3.0}% {:>9.1} {:>10.2}",
                    pair, s, mode_s, full.trades, wpf, netf, expf, oos.trades, wpo, neto, expo);
            }
        }
    }
}
