use crate::config::TrendConfig;
use crate::indicators::{Ema, Rsi, Atr, Adx, Choppiness, Macd, VolumeSma};
use crate::models::bar::Bar;
use crate::models::order::OrderSide;
use crate::strategy::{Strategy, TickContext, StrategyStatus};
// Journal removed — trades go to the unified trades.db via log_unified.
use crate::connector::types::{OrderRequest, Fill, OrderTypeReq, TimeInForceReq};
use crate::notifications::TelegramBot;
use async_trait::async_trait;
use anyhow::Result;
use serde::{Serialize, Deserialize};
use std::fs;
use std::sync::Arc;
use tracing::{warn, debug};
use crate::connector::perp_price::PerpPriceSource;

/// Direction from EMA cross + price position.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Direction {
    Up,    // +1: EMA fast > slow AND close > slow
    Down,  // -1: EMA fast < slow AND close < slow
    Flat,  //  0: mixed signals
}

/// Weighted signal scores (0–9 total).
/// ADX(0-3) + CHOP(0-2) + VOL(0-2) + MACD(0-1) + RSI(0-1) = max 9.
#[derive(Debug, Clone, Copy, Default)]
pub struct SignalScores {
    pub adx: u8,
    pub chop: u8,
    pub volume: u8,
    pub macd: u8,
    pub rsi: u8,
    pub total: u8,
}

/// Take-profit level with close percentage
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TpLevel {
    pub price: f64,
    pub close_pct: f64,
    pub filled: bool,
}

/// A trend position with direction-aware trailing stop.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrendPosition {
    pub side: OrderSide,
    pub entry_price: f64,
    pub stop_loss: f64,
    pub quantity: f64,
    pub remaining_qty: f64,
    pub trailing_stop: Option<f64>,
    pub highest_since_entry: f64,
    pub lowest_since_entry: f64,
    pub tp_levels: Vec<TpLevel>,
    /// Unix-seconds timestamp of the opening fill (serde default keeps old state files loadable).
    #[serde(default)]
    pub entry_time: i64,
    /// True when the position was just loaded from disk. The first on_tick after
    /// load reconciles overdue TPs without firing a catch-up exit burst.
    #[serde(default)]
    pub restored: bool,
    /// Unix-milliseconds of the last funding accrual for this short. serde
    /// default 0 so old state files load; the first accrual fires after one
    /// full 8h interval.
    #[serde(default)]
    pub last_funding_time: i64,
}

impl TrendPosition {
    pub fn calculate_tp_levels(entry_price: f64, stop_loss: f64, risk_reward_ratio: f64, runner_pct: f64, side: OrderSide) -> Vec<TpLevel> {
        // Signed per-unit risk, always positive in the trade's profit direction:
        // long → stop below entry (entry−stop > 0); short → stop above (stop−entry > 0).
        let risk = match side {
            OrderSide::Buy => entry_price - stop_loss,
            OrderSide::Sell => stop_loss - entry_price,
        }.max(0.0);
        // Guard: a missing/zero RR would place TP3 at the entry price, making it
        // fire instantly. Fall back to 2:1 so the position always has real targets.
        let rr = if risk_reward_ratio > 0.0 { risk_reward_ratio } else { 2.0 };
        let tp3_close = if runner_pct > 0.0 { 1.0 - runner_pct } else { 1.0 };
        // Profit lies in the trade's direction: above entry for longs, below for shorts.
        let sign = match side { OrderSide::Buy => 1.0, OrderSide::Sell => -1.0 };
        vec![
            TpLevel { price: entry_price + sign * risk * 1.0, close_pct: 0.33, filled: false },
            TpLevel { price: entry_price + sign * risk * 1.5, close_pct: 0.50, filled: false },
            TpLevel { price: entry_price + sign * risk * rr, close_pct: tp3_close, filled: false },
        ]
    }
}

/// Persisted state for trend strategy (position + PnL tracking).
#[derive(Serialize, Deserialize)]
struct TrendPositionState {
    position: TrendPosition,
    realized_pnl: f64,
}

pub struct TrendStrategy {
    pair: String,
    config: TrendConfig,
    // Direction indicators
    ema_fast: Ema,
    ema_slow: Ema,
    // Gate indicators
    adx: Adx,
    choppiness: Choppiness,
    // Confirmation indicators
    macd: Macd,
    volume_sma: VolumeSma,
    rsi: Rsi,
    // Exit indicator
    atr: Atr,
    // State
    position: Option<TrendPosition>,
    /// Side of the entry order currently outstanding (None when none). on_fill
    /// consumes this to distinguish an opening fill from a closing one — the Fill
    /// struct carries no reduce_only, so this is the only signal available.
    /// `pub` so tests can inject an entry intent before calling on_fill directly.
    pub pending_entry: Option<OrderSide>,
    last_bar_count: usize,
    // Capital tracking
    initial_capital: f64,
    realized_pnl: f64,
    /// Last live order-book price seen by on_tick. status() uses this (not the
    /// lagging ema_fast) to compute direction + unrealized MTM, so "Ready" only
    /// shows when an entry would actually fire (price must be > ema_slow).
    last_price: f64,
    telegram: TelegramBot,
    /// Optional perp price source. When set, open SHORT positions are marked /
    /// triggered / exited against the perpetual mark instead of the spot mid.
    /// Longs and no-position ticks always use the spot mid.
    perp: Option<Arc<dyn PerpPriceSource>>,
    /// Unified entry-suppression flag — set by `set_paused(true)` (C1) and by
    /// `force_flat()` (C2). Stops NEW entries but lets exits/order-management
    /// (TP/SL/trailing/funding) keep running so a paused engine can still
    /// unwind. Cleared by `set_paused(false)` from `tick_strategies` when this
    /// engine becomes the active routing target on a non-flat decision.
    entries_suppressed: bool,
}

impl TrendStrategy {
    pub fn new(pair: &str, config: &TrendConfig, telegram: TelegramBot) -> Self {
        let capital = config.capital;
        let mut me = Self {
            pair: pair.to_string(),
            config: TrendConfig {
                ema_fast: config.ema_fast,
                ema_slow: config.ema_slow,
                ema_trend: config.ema_trend,
                rsi_period: config.rsi_period,
                rsi_min: config.rsi_min,
                rsi_max: config.rsi_max,
                min_signal_score: config.min_signal_score,
                confirmation_ticks: config.confirmation_ticks,
                risk_reward_ratio: config.risk_reward_ratio,
                capital: config.capital,
                risk_per_trade_pct: config.risk_per_trade_pct,
                max_position_pct: config.max_position_pct,
                trailing_stop_pct: config.trailing_stop_pct,
                trailing_stop_atr_mult: config.trailing_stop_atr_mult,
                trailing_activation_pct: config.trailing_activation_pct,
                exit_signal_threshold: config.exit_signal_threshold,
                sl_buffer_pct: config.sl_buffer_pct,
                adx_gate_threshold: config.adx_gate_threshold,
                adx_exit_threshold: config.adx_exit_threshold,
                choppiness_threshold: config.choppiness_threshold,
                volume_ratio_threshold: config.volume_ratio_threshold,
                entry_score_threshold: config.entry_score_threshold,
                rsi_long_max: config.rsi_long_max,
                rsi_short_min: config.rsi_short_min,
                atr_trailing_mult: config.atr_trailing_mult,
                trade_shorts: config.trade_shorts,
                perp_mark_source: config.perp_mark_source.clone(),
                funding_accrual: config.funding_accrual,
            },
            ema_fast: Ema::new(config.ema_fast),
            ema_slow: Ema::new(config.ema_slow),
            adx: Adx::new(14),
            choppiness: Choppiness::new(14),
            macd: Macd::default_12_26_9(),
            volume_sma: VolumeSma::new(20),
            rsi: Rsi::new(config.rsi_period),
            atr: Atr::new(14),
            position: None,
            pending_entry: None,
            last_bar_count: 0,
            initial_capital: capital,
            realized_pnl: 0.0,
            last_price: 0.0,
            telegram,
            perp: None,
            entries_suppressed: false,
        };
        me.load_position();
        me
    }

    /// Attach a perp price source so open shorts are marked against the
    /// perpetual instead of the spot mid. Longs are unaffected.
    pub fn with_perp(mut self, perp: Arc<dyn PerpPriceSource>) -> Self {
        self.perp = Some(perp);
        self
    }

    /// Log a close event to the journal (no-op if the journal is unavailable).
    #[allow(clippy::too_many_arguments)]
    fn log_close(
        &self,
        side: OrderSide,
        entry_price: f64,
        exit_price: f64,
        amount: f64,
        pnl: f64,
        stop_loss: f64,
        take_profit: f64,
        exit_reason: &str,
        now_ts: i64,
        entry_time: i64,
    ) {
        let duration = duration_minutes(now_ts, entry_time);
        let side_str = match side { OrderSide::Buy => "BUY", OrderSide::Sell => "SELL" };
        crate::strategy::trade_journal::log_unified("trend", &self.pair, Some(side_str), Some(entry_price), Some(exit_price), Some(amount), pnl, Some(exit_reason), Some(duration));
    }

    pub fn update_indicators(&mut self, bar: &Bar) {
        self.ema_fast.update(bar.close);
        self.ema_slow.update(bar.close);
        self.adx.update_bar(bar.open, bar.high, bar.low, bar.close);
        self.choppiness.update_bar(bar.open, bar.high, bar.low, bar.close, None);
        self.macd.update(bar.close);
        self.volume_sma.update(bar.volume);
        self.rsi.update(bar.close);
        self.atr.update_bar(bar.open, bar.high, bar.low, bar.close);
    }

    /// Global readiness gate — all indicators must be initialized.
    fn indicators_ready(&self) -> bool {
        self.ema_fast.is_initialized()
            && self.ema_slow.is_initialized()
            && self.adx.is_initialized()
            && self.choppiness.is_initialized()
            && self.macd.is_initialized()
            && self.rsi.is_initialized()
            && self.atr.is_initialized()
            && self.volume_sma.is_initialized()
    }

    /// Unified scoring — replaces binary gate + score with weighted 0–9 system.
    ///   ADX(0-3)  + CHOP(0-2) + VOL(0-2) + MACD(0-1) + RSI(0-1) = max 9
    fn compute_score(&self, dir: Direction) -> SignalScores {
        // ADX: trend strength (higher ADX = stronger trend)
        let adx_val = self.adx.adx();
        let adx = if adx_val > 50.0 { 3 }
                  else if adx_val > 30.0 { 2 }
                  else if adx_val > 20.0 { 1 }
                  else { 0 };

        // CHOP: trend quality (lower = cleaner trend, higher = choppy)
        let chop_val = self.choppiness.value();
        let chop = if chop_val < 30.0 { 2 }
                   else if chop_val < 50.0 { 1 }
                   else { 0 };

        // Volume: market participation
        let vol_ratio = self.volume_sma.volume_ratio();
        let volume = if vol_ratio > 1.5 { 2 }
                     else if vol_ratio > 0.9 { 1 }
                     else { 0 };

        // MACD: momentum alignment with direction
        let dir_sign = match dir {
            Direction::Up => 1.0,
            Direction::Down => -1.0,
            Direction::Flat => 0.0,
        };
        let macd = if dir != Direction::Flat
            && self.macd.histogram().signum() == dir_sign { 1 } else { 0 };

        // RSI: entry timing (not overbought for longs, not oversold for shorts)
        let rsi_val = self.rsi.value();
        let rsi_long_max = if self.config.rsi_long_max > 0.0 { self.config.rsi_long_max } else { 65.0 };
        let rsi = match dir {
            Direction::Up if rsi_val < rsi_long_max => 1,
            Direction::Down if rsi_val > 35.0 => 1,
            _ => 0,
        };

        let total = adx + chop + volume + macd + rsi;
        SignalScores { adx, chop, volume, macd, rsi, total }
    }

    /// Layer 2: DIRECTION — +1 / -1 / 0
    fn direction(&self, price: f64) -> Direction {
        let ema_fast_val = self.ema_fast.value();
        let ema_slow_val = self.ema_slow.value();
        if ema_fast_val > ema_slow_val && price > ema_slow_val {
            Direction::Up
        } else if ema_fast_val < ema_slow_val && price < ema_slow_val {
            Direction::Down
        } else {
            Direction::Flat
        }
    }

    /// Activate — enter when score meets threshold AND direction is clear.
    fn should_activate(&self, price: f64) -> bool {
        let dir = self.direction(price);
        if self.config.trade_shorts {
            if dir == Direction::Flat { return false; }
        } else {
            if dir != Direction::Up { return false; }
        }
        // Trend-strength gate: never enter a pair with no real trend, even if
        // the weighted score clears the threshold on volume/RSI alone. Without
        // this a ranging pair (ADX≈0) enters then immediately trips the
        // "ADX dying" exit → an entry/exit churn loop (XRP logged 1,807 such
        // trades in one day on a no-trend pair).
        let adx_gate = if self.config.adx_gate_threshold > 0.0 { self.config.adx_gate_threshold } else { 20.0 };
        if self.adx.adx() < adx_gate { return false; }
        let threshold = if self.config.entry_score_threshold > 0 { self.config.entry_score_threshold } else { 5 };
        let scores = self.compute_score(dir);
        scores.total >= threshold
    }

    /// Layer 5: EXIT — ADX dying OR direction flipped.
    fn should_exit_signal(&self, price: f64, entry_dir: Direction) -> (bool, String) {
        let adx_exit = if self.config.adx_exit_threshold > 0.0 { self.config.adx_exit_threshold } else { 20.0 };
        if self.adx.adx() < adx_exit {
            return (true, format!("ADX dying ({:.1}<{:.0})", self.adx.adx(), adx_exit));
        }
        let current_dir = self.direction(price);
        if current_dir != entry_dir && current_dir != Direction::Flat {
            return (true, "Direction flipped".to_string());
        }
        (false, String::new())
    }

    pub fn calculate_stop_loss(&self, entry_price: f64, side: OrderSide) -> f64 {
        // Stop sits on the losing side of entry: below for longs, above for shorts.
        let dist = 2.0 * self.atr.value();
        match side {
            OrderSide::Buy => entry_price - dist,
            OrderSide::Sell => entry_price + dist,
        }
    }

    fn calculate_quantity(&self, entry_price: f64, stop_loss: f64, side: OrderSide) -> f64 {
        // Stop distance is always positive regardless of side (stop is on the
        // losing side; for shorts that's above entry, so stop−entry not entry−stop).
        let sl_distance = match side {
            OrderSide::Buy => entry_price - stop_loss,
            OrderSide::Sell => stop_loss - entry_price,
        };
        if sl_distance <= 0.0 { return 0.0; }
        let current_capital = self.config.capital + self.realized_pnl;
        let risk_amount = current_capital * (self.config.risk_per_trade_pct / 100.0);
        let max_position_value = current_capital * (self.config.max_position_pct / 100.0);
        let qty_by_risk = risk_amount / sl_distance;
        let max_qty = max_position_value / entry_price;
        qty_by_risk.min(max_qty)
    }

    pub fn position(&self) -> Option<&TrendPosition> { self.position.as_ref() }

    fn position_file_path(pair: &str) -> std::path::PathBuf {
        std::path::PathBuf::from(format!("data/{}_trend_position.json", pair.replace("-", "_")))
    }

    fn save_position(&self) {
        let path = Self::position_file_path(&self.pair);
        if let Some(pos) = &self.position {
            let state = TrendPositionState {
                position: pos.clone(),
                realized_pnl: self.realized_pnl,
            };
            match serde_json::to_string_pretty(&state) {
                Ok(json) => { let _ = fs::write(&path, json); }
                Err(e) => warn!("Failed to serialize trend position for {}: {}", self.pair, e),
            }
        } else {
            let _ = fs::remove_file(&path);
        }
    }

    fn load_position(&mut self) {
        let path = Self::position_file_path(&self.pair);
        if !path.exists() { return; }
        match fs::read_to_string(&path) {
            Ok(content) => {
                match serde_json::from_str::<TrendPositionState>(&content) {
                    Ok(state) => {
                        let mut pos = state.position;
                        pos.restored = true; // reconcile on the first on_tick after load
                        // Back-fill the funding clock for old state files written
                        // before last_funding_time existed (serde default 0). A
                        // literal 0 would make the first post-restart tick pass
                        // the 8h gate immediately and charge a full interval on
                        // a position held only minutes. Measure from entry instead.
                        if pos.last_funding_time == 0 && pos.entry_time > 0 {
                            pos.last_funding_time = pos.entry_time;
                        }
                        self.position = Some(pos);
                        self.realized_pnl = state.realized_pnl;
                    }
                    Err(e) => warn!("Failed to parse trend position for {}: {}", self.pair, e),
                }
            }
            Err(e) => warn!("Failed to read trend position for {}: {}", self.pair, e),
        }
    }

    fn notify_exit(&self, exit_price: f64, pnl: f64, reason: &str) {
        let msg = trend_exit_message(&self.pair, reason, exit_price, pnl, self.realized_pnl);
        // Fire-and-forget: never block the tick loop on Telegram latency.
        let tg = self.telegram.clone_for_signal();
        tokio::spawn(async move { let _ = tg.send(&msg).await; });
    }

    /// Ping on position open so a new trade is visible immediately (trend
    /// previously only notified on exit). Mirrors notify_exit's fire-and-forget.
    fn notify_entry(&self, entry_price: f64, qty: f64, stop_loss: f64, side: OrderSide) {
        let msg = trend_entry_message(&self.pair, side, entry_price, qty, stop_loss);
        let tg = self.telegram.clone_for_signal();
        tokio::spawn(async move { let _ = tg.send(&msg).await; });
    }
}

#[async_trait]
impl Strategy for TrendStrategy {
    fn name(&self) -> &str { "trend" }
    fn trading_pair(&self) -> &str { &self.pair }

    fn realized_pnl(&self) -> f64 { self.realized_pnl }
    fn deployed_capital(&self) -> f64 { self.position.as_ref().map_or(0.0, |p| p.remaining_qty * p.entry_price) }

    /// C1: suppress new entries while exits/management keep running. The flag is
    /// the same one `force_flat` sets, so resume is a single `set_paused(false)`.
    fn set_paused(&mut self, paused: bool) {
        self.entries_suppressed = paused;
    }

    /// C2: close any open position at the current mid (Market, reduce-only),
    /// cancel nothing (trend places no resting orders — exits are reactive),
    /// and suppress new entries until the next non-flat routing decision.
    /// Mirrors the in-tick exit path: optimistic state clear + journal + notify.
    fn force_flat(&mut self) {
        self.entries_suppressed = true;
        // No reactive cancel: trend emits Market exits in-tick with no cid the
        // engine tracks; there is no resting order book to cancel here.
    }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>> {
        let mut orders = Vec::new();

        // Only process NEW bars since last tick (same pattern as GridStrategy)
        let bars_to_process = if ctx.recent_bars.len() > self.last_bar_count {
            &ctx.recent_bars[self.last_bar_count..]
        } else if ctx.recent_bars.len() < self.last_bar_count {
            &ctx.recent_bars[..] // buffer was reset (e.g. engine restart)
        } else {
            &ctx.recent_bars[0..0] // no new bars
        };

        for bar in bars_to_process {
            self.update_indicators(bar);
        }
        self.last_bar_count = ctx.recent_bars.len();

        if !self.indicators_ready() { return Ok(orders); }

        // Require a live order-book quote. The previous fallback to
        // recent_bars.last().close let the strategy trade at a stale bar close
        // when the order book was empty — most importantly during bar-replay
        // warmup on restart, where the replay ctx has an empty order book, so
        // every historical bar's close became "current_price" and fired phantom
        // TP exits at prices the live market never reached (ETH "exits" at
        // $1832.52 while real ETH was ~$1.6k). No live quote => hold.
        let mut current_price = ctx.order_book.mid_price().unwrap_or(0.0);
        if current_price <= 0.0 { return Ok(orders); }
        // Open SHORT positions are marked against the perp feed (configurable),
        // so short MTM/triggers/exits reflect the perpetual, not spot. Longs and
        // no-position ticks keep the spot mid. On perp fetch failure, fall back
        // to spot and warn. The perp override is intentionally short-only: a
        // paper spot short is a naked-balance fiction whose only honest signal
        // is the perp mark; longs already trade real spot.
        if let Some(p) = &self.perp {
            let is_short = self.position.as_ref().map_or(false, |pos| pos.side == OrderSide::Sell);
            if is_short {
                match p.mark(&self.pair).await {
                    Some(mark) if mark > 0.0 => { current_price = mark; }
                    _ => warn!("perp mark unavailable for {}; using spot mid", self.pair),
                }
            }
        }
        self.last_price = current_price;
        // A perp source is attached AND this is (or becomes) a SHORT position →
        // exits must be Market, not Limit-at-perp (see exit_order_req).
        let perp_attached = self.perp.is_some();

        // C2: routing layer forced flat — close any open position at market
        // immediately and return. Entries are also suppressed (force_flat set
        // the flag), so we won't reopen on this or subsequent ticks until the
        // engine clears entries_suppressed via set_paused(false). Mirrors the
        // in-tick exit path: optimistic state clear + journal + notify + Market.
        if self.entries_suppressed {
            if let Some(pos) = self.position.take() {
                let side = pos.side;
                let entry = pos.entry_price;
                let qty = pos.remaining_qty;
                let sl = pos.stop_loss;
                let tp3 = pos.tp_levels.last().map(|t| t.price).unwrap_or(entry);
                let et = pos.entry_time;
                let pnl = trade_pnl(side, entry, current_price, qty);
                self.realized_pnl += pnl;
                self.pending_entry = None;
                self.log_close(side, entry, current_price, qty, pnl, sl, tp3, "force_flat", ctx.timestamp, et);
                self.notify_exit(current_price, pnl, "force_flat");
                let (_ot, price_opt) = exit_order_req(perp_attached, side, current_price);
                orders.push(OrderRequest {
                    symbol: self.pair.clone(), side: close_side(side), reduce_only: true,
                    order_type: OrderTypeReq::Market, price: price_opt,
                    quantity: qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                });
                self.save_position();
            }
            return Ok(orders);
        }

        // Funding accrual on open shorts, once per 8h (28_800_000 ms — ctx.timestamp
        // and entry_time are both milliseconds), using the perp funding rate.
        // Positive rate → short PAYS (negative pnl); negative rate → short RECEIVES.
        // Two-phase borrow: read side+last_funding_time via as_ref, await the rate
        // (no borrow held across .await), then as_mut to mutate + journal.
        if self.config.funding_accrual {
            if let Some(p) = &self.perp {
                // Three states: gate closed (None, false), gate open + rate fetched
                // (Some(rate), _), gate open + rate unavailable (None, true → warn).
                let mut accrue: Option<f64> = None;
                let mut gate_open_but_unavailable = false;
                if let Some(pos) = self.position.as_ref() {
                    if pos.side == OrderSide::Sell && ctx.timestamp - pos.last_funding_time >= 28_800_000 {
                        match p.funding_rate(&self.pair).await {
                            Some(rate) => accrue = Some(rate),
                            None => gate_open_but_unavailable = true,
                        }
                    }
                }
                if gate_open_but_unavailable {
                    // Persistent perp-feed outage would otherwise accrue zero
                    // funding with zero log signal. Surface it so operators can
                    // see the feed is down (deployment note tells them to watch).
                    // Do NOT advance last_funding_time — retry next tick.
                    warn!("funding rate unavailable for {}; will retry next tick", self.pair);
                }
                if let Some(rate) = accrue {
                    if let Some(pos) = self.position.as_mut() {
                        let notional = pos.entry_price * pos.remaining_qty;
                        let funding_pnl = -rate * notional; // positive rate -> short pays
                        self.realized_pnl += funding_pnl;
                        pos.last_funding_time = ctx.timestamp;
                        crate::strategy::trade_journal::log_unified(
                            "trend", &self.pair, Some("SELL"),
                            None, None, Some(0.0), funding_pnl, Some("funding"), None,
                        );
                        debug!("funding accrued on {}: rate={} pnl={}", self.pair, rate, funding_pnl);
                    }
                }
            }
        }

        // ── If in position: check exits ──
        // Snapshot entry metadata for journaling (all fields Copy; fixed at entry,
        // so valid for the whole tick). Captured before the mutable borrow below.
        let snap = self.position.as_ref().map(|p| (
            p.side,
            p.entry_price,
            p.stop_loss,
            p.tp_levels.last().map(|t| t.price).unwrap_or(p.entry_price),
            p.entry_time,
        ));
        // Restore reconciliation: a position just loaded from disk may have TP
        // levels already below the current price (they "happened" while we were
        // down). Mark those TPs filled WITHOUT re-firing notifications/orders,
        // and skip exit evaluation for this one tick — a restart must not
        // liquidate the position with a catch-up burst.
        let mut skip_exits_for_restore = false;
        if let Some(pos) = &mut self.position {
            if pos.restored {
                for tp in pos.tp_levels.iter_mut() {
                    if !tp.filled && tp_hit(pos.side, current_price, tp.price) {
                        tp.filled = true;
                    }
                }
                pos.restored = false;
                skip_exits_for_restore = true;
            }
        }
        if skip_exits_for_restore {
            self.save_position();
            return Ok(orders);
        }

        if let Some(pos) = &mut self.position {
            if current_price > pos.highest_since_entry { pos.highest_since_entry = current_price; }
            if current_price < pos.lowest_since_entry { pos.lowest_since_entry = current_price; }

            // ── Breakeven + trailing stop (merged: let winners run) ──────────
            // Replaces the old separate hard-stop + trailing-stop checks.
            // Flow: initial stop (-2·ATR) → promote to breakeven (entry) at +1R
            // → trail ratchets tighter as the trend extends. The effective stop
            // is the TIGHTER of {stop_loss, trailing_stop}. One check, one exit.
            //
            // 1. Breakeven promotion: once price reaches +1R, move stop_loss to
            //    entry. This eliminates downside risk after the trade proves
            //    itself (+1R = entry+risk). Fires at most once — after stop_loss
            //    == entry, `not_yet_promoted` is false and it never re-fires.
            let risk = match pos.side {
                OrderSide::Buy => (pos.entry_price - pos.stop_loss).max(0.0),
                OrderSide::Sell => (pos.stop_loss - pos.entry_price).max(0.0),
            };
            if risk > 0.0 {
                let reached_1r = match pos.side {
                    OrderSide::Buy => current_price >= pos.entry_price + risk,
                    OrderSide::Sell => current_price <= pos.entry_price - risk,
                };
                if reached_1r {
                    let not_yet_promoted = match pos.side {
                        OrderSide::Buy => pos.stop_loss < pos.entry_price,
                        OrderSide::Sell => pos.stop_loss > pos.entry_price,
                    };
                    if not_yet_promoted {
                        pos.stop_loss = pos.entry_price;
                    }
                }
            }

            // 2. Update trailing stop (Chandelier Exit from highest/lowest).
            let atr_mult = if self.config.atr_trailing_mult > 0.0 { self.config.atr_trailing_mult } else { 3.0 };
            let atr_val = self.atr.value();
            let new_trail = match pos.side {
                OrderSide::Buy => pos.highest_since_entry - atr_mult * atr_val,
                OrderSide::Sell => pos.lowest_since_entry + atr_mult * atr_val,
            };
            pos.trailing_stop = Some(match pos.trailing_stop {
                Some(prev) => match pos.side {
                    OrderSide::Buy => new_trail.max(prev),    // ratchet up for longs
                    OrderSide::Sell => new_trail.min(prev),   // ratchet down for shorts
                },
                None => new_trail,
            });

            // 3. Effective stop = the TIGHTER of stop_loss and trailing_stop.
            //    Before +1R: stop_loss (initial -2·ATR) is tighter than the trail
            //    (which starts wider). After +1R: stop_loss = entry (breakeven);
            //    trail ratchets above entry as the trend extends → trail becomes
            //    tighter. One exit point.
            let effective_stop = match pos.trailing_stop {
                Some(ts) => match pos.side {
                    OrderSide::Buy => pos.stop_loss.max(ts),   // higher = tighter for longs
                    OrderSide::Sell => pos.stop_loss.min(ts),   // lower = tighter for shorts
                },
                None => pos.stop_loss,
            };

            // 4. Check the effective stop — full remaining qty, Market (gap-safe).
            let hit = match pos.side {
                OrderSide::Buy => current_price <= effective_stop,
                OrderSide::Sell => current_price >= effective_stop,
            };
            if hit {
                let side = pos.side;
                let entry = pos.entry_price;
                let qty = pos.remaining_qty;
                let pnl = trade_pnl(side, entry, current_price, qty);
                let is_breakeven = (effective_stop - pos.entry_price).abs() < 1e-9;
                let reason = if is_breakeven { "breakeven_stop" } else { "trailing_stop" };
                self.realized_pnl += pnl;
                self.position = None;
                self.pending_entry = None;
                if let Some((s, ep, sl, tp3, et)) = snap {
                    self.log_close(s, ep, current_price, qty, pnl, sl, tp3, reason, ctx.timestamp, et);
                }
                self.notify_exit(current_price, pnl, reason);
                orders.push(OrderRequest {
                    symbol: self.pair.clone(), side: close_side(side), reduce_only: true,
                    order_type: OrderTypeReq::Market, price: None,
                    quantity: qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                });
                self.save_position();
                return Ok(orders);
            }

            // TP partial exits — collect events to journal after the mutable borrow ends.
            let tp_side = pos.side;
            let tp_entry = pos.entry_price;
            let mut tp_exits: Vec<(f64, f64, &'static str)> = Vec::new();
            for (idx, tp) in pos.tp_levels.iter_mut().enumerate() {
                if tp.filled { continue; }
                if tp_hit(tp_side, current_price, tp.price) {
                    let sell_qty = pos.remaining_qty * tp.close_pct;
                    if sell_qty > 0.0 {
                        tp.filled = true;
                        pos.remaining_qty -= sell_qty;
                        let pnl = trade_pnl(tp_side, tp_entry, current_price, sell_qty);
                        self.realized_pnl += pnl;
                        let reason = match idx { 0 => "tp1", 1 => "tp2", _ => "tp3" };
                        tp_exits.push((sell_qty, pnl, reason));
                        let (ot, price_opt) = exit_order_req(perp_attached, tp_side, current_price);
                        orders.push(OrderRequest {
                            symbol: self.pair.clone(), side: close_side(tp_side), reduce_only: true,
                            order_type: ot, price: price_opt,
                            quantity: sell_qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                        });
                        if pos.remaining_qty <= 0.0001 { self.position = None; self.pending_entry = None; self.save_position(); break; }
                    }
                }
            }
            // Journal TP fills now that `pos` is no longer borrowed.
            for (qty, pnl, reason) in &tp_exits {
                if let Some((s, ep, sl, tp3, et)) = snap {
                    self.log_close(s, ep, current_price, *qty, *pnl, sl, tp3, reason, ctx.timestamp, et);
                }
                self.notify_exit(current_price, *pnl, reason);
            }
            if self.position.is_none() { return Ok(orders); }

            // Direction flip / ADX exit
            if let Some(pos) = &self.position {
                let entry_dir = match pos.side { OrderSide::Buy => Direction::Up, OrderSide::Sell => Direction::Down };
                let (exit, _reason) = self.should_exit_signal(current_price, entry_dir);
                if exit {
                    let side = pos.side;
                    let entry = pos.entry_price;
                    let qty = pos.remaining_qty;
                    let pnl = trade_pnl(side, entry, current_price, qty);
                    self.realized_pnl += pnl;
                    self.position = None;
                    self.pending_entry = None;
                    if let Some((s, ep, sl, tp3, et)) = snap {
                        self.log_close(s, ep, current_price, qty, pnl, sl, tp3, "signal_exit", ctx.timestamp, et);
                    }
                    self.notify_exit(current_price, pnl, "signal_exit");
                    let (ot, price_opt) = exit_order_req(perp_attached, side, current_price);
                    orders.push(OrderRequest {
                        symbol: self.pair.clone(), side: close_side(side), reduce_only: true,
                        order_type: ot, price: price_opt,
                        quantity: qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                    });
                    self.save_position();
                    return Ok(orders);
                }
            }
        }

        // Persist any trailing stop / TP updates from this tick
        self.save_position();

        // ── No position: check for entry ──
        // Skip during warmup replay (ctx.replay) so restarts don't regenerate
        // ghost positions/trades from historical bars — indicators still warm.
        // Skip when entries are suppressed (paused / force_flat) — the engine's
        // routing layer set this; exits above still ran, so open positions unwind.
        if self.position.is_none() && !ctx.replay && !self.entries_suppressed {
            if self.should_activate(current_price) {
                let side = entry_side_for(self.direction(current_price));
                let stop_loss = self.calculate_stop_loss(current_price, side);
                let quantity = self.calculate_quantity(current_price, stop_loss, side);
                // Phase B2: cap to available free capital (compute-then-cap).
                let quantity = match &ctx.capital {
                    Some(cm) => {
                        let notional = quantity * current_price;
                        let granted = cm.request_capital("trend", notional);
                        if notional > 0.0 { quantity * granted / notional } else { 0.0 }
                    }
                    None => quantity,
                };
                if quantity > 0.0 {
                    // Record entry intent: the Fill struct carries no reduce_only,
                    // so on_fill can't otherwise tell this opening fill from a
                    // closing one. Consumed by on_fill when the entry fills.
                    self.pending_entry = Some(side);
                    orders.push(OrderRequest {
                        symbol: self.pair.clone(), side, reduce_only: false,
                        order_type: OrderTypeReq::Limit, price: Some(current_price),
                        quantity, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                    });
                }
            } else {
                // Log WHY entry was skipped — score breakdown, direction, threshold
                let dir = self.direction(current_price);
                let scores = self.compute_score(dir);
                let threshold = if self.config.entry_score_threshold > 0 { self.config.entry_score_threshold } else { 5 };
                debug!(
                    "[{}] Entry skipped: dir={:?} score={}/{} need≥={} | ADX={:.1} CHOP={:.0} VOL={:.2} MACD={} RSI={:.1}",
                    self.pair, dir, scores.total, 9, threshold,
                    self.adx.adx(), self.choppiness.value(),
                    self.volume_sma.volume_ratio(),
                    scores.macd, self.rsi.value()
                );
            }
        }
        Ok(orders)
    }

    async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>> {
        // An entry fill opens a position in the side recorded at order time. The
        // Fill struct carries no reduce_only, so `pending_entry` (set when the
        // entry order was placed) is the only way to tell an opening fill from a
        // closing one when the position is already flat. Any other fill reconciles
        // remaining quantity — exit paths already booked PnL at detection time.
        if let Some(side) = self.pending_entry.take() {
            let stop_loss = self.calculate_stop_loss(fill.price, side);
            // No fixed TPs — let winners run via breakeven promotion + Chandelier
            // trail (exit redesign). calculate_tp_levels is retained for the
            // restore-reconciliation path below (old state files with TPs) and
            // optional far-TPs in the future.
            let tp_levels = Vec::new();
            self.position = Some(TrendPosition {
                side, entry_price: fill.price, stop_loss,
                quantity: fill.quantity, remaining_qty: fill.quantity,
                trailing_stop: None, highest_since_entry: fill.price,
                lowest_since_entry: fill.price, tp_levels,
                entry_time: fill.timestamp,
                restored: false,
                // Start the funding clock at entry so the first 8h interval is
                // measured from open, not from epoch (0) — which would charge a
                // full 8h immediately. Restored positions back-fill this in load_position.
                last_funding_time: fill.timestamp,
            });
            self.save_position();
            self.notify_entry(fill.price, fill.quantity, stop_loss, side);
            return Ok(Vec::new());
        }
        // Exit / reduce fill: reconcile remaining quantity.
        if let Some(mut pos) = self.position.take() {
            pos.remaining_qty -= fill.quantity;
            if pos.remaining_qty <= 0.0001 { self.position = None; }
            else { self.position = Some(pos); }
        }
        self.save_position();
        Ok(Vec::new())
    }

    async fn on_start(&mut self) -> Result<Vec<OrderRequest>> {
        // Reconstitute cumulative realized P&L from the authoritative journal.
        // The position file is deleted on close, so without this a restart
        // while flat would silently reset realized_pnl to $0 — hiding the
        // engine's real result from /trend's total and from position-sizing
        // capital (calculate_qty uses capital + realized_pnl).
        if true {
            self.realized_pnl = crate::strategy::trade_journal::realized_pnl("trend", &self.pair);
        }
        Ok(Vec::new())
    }
    async fn on_stop(&mut self) -> Result<()> { Ok(()) }

    fn status(&self) -> StrategyStatus {
        // Reference price for direction + mark-to-market: the last LIVE tick
        // price, not the lagging ema_fast. Otherwise "Ready / dir:+1" shows
        // whenever ema_fast > ema_slow, even when the live price has dropped
        // below ema_slow (where on_tick's entry check would skip). Falls back
        // to ema_fast before the first live tick.
        let ref_price = if self.last_price > 0.0 { self.last_price } else { self.ema_fast.value() };
        let (state, details, pnl) = if let Some(pos) = &self.position {
            let unrealized = match pos.side {
                OrderSide::Buy => (ref_price - pos.entry_price) * pos.remaining_qty,
                OrderSide::Sell => (pos.entry_price - ref_price) * pos.remaining_qty,
            };
            let side_str = match pos.side { OrderSide::Buy => "LONG", OrderSide::Sell => "SHORT" };
            let trail_str = match pos.trailing_stop {
                Some(ts) => format!(" | Trail: ${:.2}", ts), None => String::new(),
            };
            let dir_str = match self.direction(ref_price) {
                Direction::Up => "+1", Direction::Down => "-1", Direction::Flat => "0",
            };
            (
                "POSITION".to_string(),
                format!("{} {:.4} @ ${:.2} | SL: ${:.2}{} | ADX: {:.1} | dir: {}\nRealized: ${:.0} | Unrealized: ${:.2}",
                    side_str, pos.remaining_qty, pos.entry_price, pos.stop_loss, trail_str, self.adx.adx(), dir_str,
                    self.realized_pnl, unrealized),
                self.realized_pnl + unrealized,
            )
        } else if !self.indicators_ready() {
            ("WAITING".to_string(), "⏳ All indicators warming up".to_string(), 0.0)
        } else {
            let dir = self.direction(ref_price);
            let scores = self.compute_score(dir);
            let threshold = if self.config.entry_score_threshold > 0 { self.config.entry_score_threshold } else { 5 };
            let dir_str = match dir { Direction::Up => "+1", Direction::Down => "-1", Direction::Flat => "0" };
            let reason = if dir == Direction::Flat { "Mixed direction".to_string() }
                         else if dir == Direction::Down && !self.config.trade_shorts { "dir=-1 blocks longs".to_string() }
                         else if scores.total < threshold { format!("Need {} more", threshold - scores.total) }
                         else { "Ready".to_string() };
            (
                "WAITING".to_string(),
                format!("Score:{}/9 (A:{} C:{} V:{} M:{} R:{}) need≥{} | dir:{} | ADX={:.1} CHOP={:.0} RSI={:.1} | {} | Realized: ${:.0}",
                    scores.total,
                    scores.adx, scores.chop, scores.volume, scores.macd, scores.rsi,
                    threshold,
                    dir_str,
                    self.adx.adx(), self.choppiness.value(), self.rsi.value(), reason,
                    self.realized_pnl),
                self.realized_pnl,
            )
        };
        StrategyStatus { name: self.name().to_string(), pair: self.pair.clone(), state, pnl, open_orders: 0, details }
    }

    fn current_capital(&self) -> f64 { self.config.capital + self.realized_pnl }
    fn initial_capital(&self) -> f64 { self.initial_capital }
}

/// The order side to ENTER with for a given EMA direction: Buy on Up, Sell on
/// Down (and Sell for the unreachable Flat case — `should_activate` filters it).
/// Extracted so the entry-side decision is unit-testable independent of on_tick.
fn entry_side_for(dir: Direction) -> OrderSide {
    match dir { Direction::Up => OrderSide::Buy, Direction::Down | Direction::Flat => OrderSide::Sell }
}

/// The order side that CLOSES a position of the given side: Sell closes a long,
/// Buy covers a short.
fn close_side(side: OrderSide) -> OrderSide {
    match side { OrderSide::Buy => OrderSide::Sell, OrderSide::Sell => OrderSide::Buy }
}

/// Realized PnL of a closed slice, sign-correct for both long and short.
/// Long: (exit-entry)·qty. Short: (entry-exit)·qty.
fn trade_pnl(side: OrderSide, entry_price: f64, exit_price: f64, qty: f64) -> f64 {
    match side {
        OrderSide::Buy => (exit_price - entry_price) * qty,
        OrderSide::Sell => (entry_price - exit_price) * qty,
    }
}

/// Has the hard stop been touched? Long stops below entry (price falls into it);
/// short stops above entry (price rises into it).
///
/// After the exit redesign, the merged breakeven+trail block inlines this check
/// (it needs `effective_stop = max(stop, trail)` for longs, not just stop), so
/// `stop_hit` is no longer called from `on_tick`. Retained for the unit test
/// `stop_and_tp_hit_conditions_are_side_aware` and as documentation of the
/// direction-aware comparison convention shared with `tp_hit` below.
#[cfg(test)]
fn stop_hit(side: OrderSide, price: f64, stop: f64) -> bool {
    match side { OrderSide::Buy => price <= stop, OrderSide::Sell => price >= stop }
}

/// Has a take-profit level been reached? Long TPs above entry (price rises to
/// it); short TPs below entry (price falls to it).
fn tp_hit(side: OrderSide, price: f64, tp: f64) -> bool {
    match side { OrderSide::Buy => price >= tp, OrderSide::Sell => price <= tp }
}

/// Order type + price for a reduce-only exit. Longs (or shorts with no perp
/// attached) push a Limit at the current price — `current_price` is the spot
/// mid for longs, so the paper fill gate (`spot <= limit`) is satisfied and the
/// fill is correct. But an open SHORT with a perp source attached is marked at
/// the perp price, so a Limit-at-perp exit would be gated against the spot mid
/// and misfire when perp and spot diverge (perp<spot → never fills; perp>spot →
/// fills spuriously at a price the spot book never reached). For that case we
/// push Market instead — it always fills (`paper.rs (_, None) => true`) at the
/// spot mark with taker slippage, the honest model for an aggressive short
/// cover, and keeps the paper balance consistent with the strategy's bookkeeping.
fn exit_order_req(perp_attached: bool, side: OrderSide, current_price: f64) -> (OrderTypeReq, Option<f64>) {
    if perp_attached && side == OrderSide::Sell {
        (OrderTypeReq::Market, None)
    } else {
        (OrderTypeReq::Limit, Some(current_price))
    }
}

/// Hold time in minutes from two millisecond timestamps. 0 if either is unset.
///
/// Timestamps here are ms (Binance/chrono epoch-ms). The previous code divided by
/// 60, inflating durations 1000× — a 6-hour ETH hold logged as ~250 "days" in
/// trades.db. MR and swing already divide by 60_000; this brings trend in line.
fn duration_minutes(now_ts: i64, entry_time: i64) -> i64 {
    if now_ts > 0 && entry_time > 0 {
        ((now_ts - entry_time) / 60_000).max(0)
    } else {
        0
    }
}

/// Telegram message for a trend ENTRY (position open). Extracted so the format
/// is unit-testable without a network send.
fn trend_entry_message(pair: &str, side: OrderSide, entry_price: f64, qty: f64, stop_loss: f64) -> String {
    let (emoji, verb) = match side { OrderSide::Buy => ("🚀", "Buy"), OrderSide::Sell => ("🔻", "Short") };
    format!(
        "{} Trend ENTRY {} | {} @ ${:.2} | Qty {:.4} | Stop ${:.2}",
        emoji, pair, verb, entry_price, qty, stop_loss
    )
}

/// Telegram message for a trend EXIT. Losses are loud (🛑 LOSS) and carry the
/// engine's running realized P&L so each alert is self-explanatory. Extracted
/// so the format is unit-testable without a network send.
fn trend_exit_message(pair: &str, reason: &str, exit_price: f64, pnl: f64, running_pnl: f64) -> String {
    let marker = if pnl < 0.0 { "🛑 LOSS Trend" } else { "📈 Trend" };
    format!(
        "{} {} {} @ ${:.2} | this: ${:+.2} | Trend running: ${:+.2}",
        marker, pair, reason, exit_price, pnl, running_pnl
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duration_minutes_uses_millisecond_timestamps() {
        let entry = 1_700_000_000_000_i64;
        // 2 hours = 7,200,000 ms.
        assert_eq!(duration_minutes(entry + 7_200_000, entry), 120, "2h hold = 120 min");
        // Unset timestamps → 0, never negative.
        assert_eq!(duration_minutes(0, entry), 0);
        assert_eq!(duration_minutes(entry, 0), 0);
    }

    #[test]
    fn entry_message_flags_a_new_trade_with_price_and_stop() {
        let msg = trend_entry_message("ETH-USDT", OrderSide::Buy, 1795.93, 1.10, 1563.91);
        assert!(msg.contains("ENTRY"), "must flag a new trade: {}", msg);
        assert!(msg.contains("ETH-USDT"), "must name the pair: {}", msg);
        assert!(msg.contains("1795.9"), "must show entry price: {}", msg);
        assert!(msg.contains("1563.9"), "must show stop: {}", msg);
        assert!(msg.contains("Buy"), "long entry says Buy: {}", msg);
    }

    // ── Short-side unit tests (trade_shorts). The math below is the danger zone:
    // a single sign flip inverts P&L or places the stop on the wrong side. ──

    #[test]
    fn entry_side_for_maps_direction_to_order_side() {
        assert_eq!(entry_side_for(Direction::Up), OrderSide::Buy);
        assert_eq!(entry_side_for(Direction::Down), OrderSide::Sell);
    }

    #[test]
    fn close_side_flips_long_and_short() {
        assert_eq!(close_side(OrderSide::Buy), OrderSide::Sell, "close a long by selling");
        assert_eq!(close_side(OrderSide::Sell), OrderSide::Buy, "close a short by buying");
    }

    #[test]
    fn trade_pnl_sign_correct_for_both_sides() {
        // Long: exit above entry is profit.
        assert!((trade_pnl(OrderSide::Buy, 100.0, 110.0, 2.0) - 20.0).abs() < 1e-9);
        // Long: exit below entry is loss.
        assert!((trade_pnl(OrderSide::Buy, 100.0, 90.0, 2.0) - (-20.0)).abs() < 1e-9);
        // Short: exit BELOW entry is profit (sold high, bought low).
        assert!((trade_pnl(OrderSide::Sell, 100.0, 90.0, 2.0) - 20.0).abs() < 1e-9,
            "short profit when price falls: {}", trade_pnl(OrderSide::Sell, 100.0, 90.0, 2.0));
        // Short: exit above entry is loss.
        assert!((trade_pnl(OrderSide::Sell, 100.0, 110.0, 2.0) - (-20.0)).abs() < 1e-9);
    }

    #[test]
    fn stop_and_tp_hit_conditions_are_side_aware() {
        // Long stop (below entry) hit when price falls to it.
        assert!(stop_hit(OrderSide::Buy, 90.0, 95.0));
        assert!(!stop_hit(OrderSide::Buy, 100.0, 95.0));
        // Short stop (above entry) hit when price rises to it.
        assert!(stop_hit(OrderSide::Sell, 110.0, 105.0));
        assert!(!stop_hit(OrderSide::Sell, 100.0, 105.0));
        // Long TP (above) hit when price rises to it.
        assert!(tp_hit(OrderSide::Buy, 110.0, 105.0));
        // Short TP (below) hit when price falls to it.
        assert!(tp_hit(OrderSide::Sell, 90.0, 95.0));
        assert!(!tp_hit(OrderSide::Sell, 100.0, 95.0));
    }

    fn warmed_strategy(trade_shorts: bool) -> TrendStrategy {
        let mut config = base_test_config();
        config.trade_shorts = trade_shorts;
        let tg = crate::notifications::TelegramBot::new("", "");
        let mut s = TrendStrategy::new("TESTPAIR-USDT", &config, tg);
        for i in 0..260 {
            let p = 100.0 + (i as f64 * 0.01); // gentle drift, keeps ATR modest
            s.update_indicators(&Bar::new(p - 0.5, p + 0.5, p - 0.25, p, 100.0, 0));
        }
        s.last_bar_count = 260;
        s
    }

    #[test]
    fn short_stop_loss_is_above_entry_long_stop_below() {
        let s = warmed_strategy(true);
        let atr = s.atr.value();
        assert!(atr > 0.0, "ATR should be warmed: {}", atr);
        // Long stop below entry, short stop above entry, symmetric around entry.
        let long_stop = s.calculate_stop_loss(100.0, OrderSide::Buy);
        let short_stop = s.calculate_stop_loss(100.0, OrderSide::Sell);
        assert!(long_stop < 100.0, "long stop below entry: {}", long_stop);
        assert!(short_stop > 100.0, "SHORT STOP MUST BE ABOVE ENTRY: {}", short_stop);
        // Each stop is exactly 2·ATR from entry (long below, short above).
        assert!((long_stop - (100.0 - 2.0 * atr)).abs() < 1e-6, "long stop = entry − 2·ATR");
        assert!((short_stop - (100.0 + 2.0 * atr)).abs() < 1e-6, "short stop = entry + 2·ATR");
    }

    #[test]
    fn short_tp_levels_are_below_entry_and_descending() {
        let s = warmed_strategy(true);
        let short_stop = s.calculate_stop_loss(100.0, OrderSide::Sell); // > 100
        let tps = TrendPosition::calculate_tp_levels(100.0, short_stop, 2.0, 0.10, OrderSide::Sell);
        assert_eq!(tps.len(), 3);
        assert!(tps[0].price < 100.0, "short TP1 below entry: {}", tps[0].price);
        assert!(tps[2].price < tps[1].price && tps[1].price < tps[0].price,
            "short TPs descend in profit direction: {:?}",
            tps.iter().map(|t| t.price).collect::<Vec<_>>());
        let risk = short_stop - 100.0; // short risk is stop-entry (>0)
        assert!((tps[2].price - (100.0 - risk * 2.0)).abs() < 1e-6, "TP3 = entry − risk·RR");
        // Long path unchanged: above entry, ascending.
        let long_stop = s.calculate_stop_loss(100.0, OrderSide::Buy);
        let ltps = TrendPosition::calculate_tp_levels(100.0, long_stop, 2.0, 0.10, OrderSide::Buy);
        assert!(ltps[0].price > 100.0 && ltps[2].price > ltps[1].price, "long TPs unchanged");
    }

    #[test]
    fn short_quantity_is_positive_with_stop_above_entry() {
        let s = warmed_strategy(true);
        let short_stop = s.calculate_stop_loss(100.0, OrderSide::Sell); // > 100
        let qty = s.calculate_quantity(100.0, short_stop, OrderSide::Sell);
        assert!(qty > 0.0, "short sizing must be positive (stop above entry): {}", qty);
        // Long path unchanged.
        let long_stop = s.calculate_stop_loss(100.0, OrderSide::Buy);
        assert!(s.calculate_quantity(100.0, long_stop, OrderSide::Buy) > 0.0);
    }

    #[test]
    fn short_entry_message_says_short_not_buy() {
        let msg = trend_entry_message("ETH-USDT", OrderSide::Sell, 1795.93, 1.10, 1863.0);
        assert!(msg.contains("ENTRY"), "{}", msg);
        assert!(msg.contains("Short"), "short entry must say Short: {}", msg);
        assert!(!msg.contains("Buy @"), "short entry must not say Buy @: {}", msg);
    }

    #[test]
    fn short_position_stops_out_with_buy_order_and_negative_pnl() {
        let mut s = warmed_strategy(true);
        let short_stop = s.calculate_stop_loss(100.0, OrderSide::Sell); // > 100, e.g. ~102
        // No TPs in the new exit design — initial stop is the only exit before +1R.
        s.position = Some(TrendPosition {
            side: OrderSide::Sell, entry_price: 100.0, stop_loss: short_stop,
            quantity: 2.0, remaining_qty: 2.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        let realized_before = s.realized_pnl;
        // Price rises ABOVE the short stop → effective_stop hit ( Sell: price>=stop).
        let ctx = tick_at(short_stop + 5.0);
        let orders = run_tick(&mut s, ctx);
        assert!(s.position.is_none(), "stop must close the short");
        assert!(s.realized_pnl < realized_before, "short stopped out at a loss: {}", s.realized_pnl);
        assert_eq!(orders.len(), 1, "exactly one stop-loss exit order");
        assert_eq!(orders[0].side, OrderSide::Buy, "closing a short BUYS to cover (got {:?})", orders[0].side);
        assert!(orders[0].reduce_only, "exit must be reduce_only");
        assert_eq!(orders[0].order_type, OrderTypeReq::Market, "gap-safe Market exit");
    }

    #[test]
    fn short_position_promotes_to_breakeven_at_1r() {
        // Exit redesign: with no TPs, a SHORT that reaches +1R no longer fires a
        // partial TP1 — instead the merged exit block promotes stop_loss to
        // entry (breakeven) and the position stays open to let the trail run.
        let mut s = warmed_strategy(true);
        let short_stop = s.calculate_stop_loss(100.0, OrderSide::Sell); // > 100
        let risk = short_stop - 100.0;
        s.position = Some(TrendPosition {
            side: OrderSide::Sell, entry_price: 100.0, stop_loss: short_stop,
            quantity: 2.0, remaining_qty: 2.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        // Price falls to +1R (entry − risk) → breakeven promotion fires; no exit.
        let ctx = tick_at(100.0 - risk);
        let _ = run_tick(&mut s, ctx);
        let pos = s.position.as_ref().expect("position stays open at +1R (no TPs to fire)");
        assert!((pos.stop_loss - pos.entry_price).abs() < 1e-9,
            "SHORT +1R: stop_loss must be promoted to breakeven (entry): got stop={}",
            pos.stop_loss);
        assert!(pos.trailing_stop.is_some(), "trail initialized after +1R");
    }

    // ── Exit-redesign tests (let winners run: breakeven @ +1R + Chandelier trail) ──

    #[test]
    fn breakeven_promoted_when_position_reaches_1r() {
        // LONG: entry 100, stop entry−2·ATR. Push price to entry+risk → promote.
        let mut s = warmed_strategy(false);
        let long_stop = s.calculate_stop_loss(100.0, OrderSide::Buy);
        let risk = 100.0 - long_stop;
        s.position = Some(TrendPosition {
            side: OrderSide::Buy, entry_price: 100.0, stop_loss: long_stop,
            quantity: 1.0, remaining_qty: 1.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        let ctx = tick_at(100.0 + risk);
        let _ = run_tick(&mut s, ctx);
        let pos = s.position.as_ref().expect("position stays open at +1R (no TPs)");
        assert!((pos.stop_loss - pos.entry_price).abs() < 1e-9,
            "LONG +1R: stop_loss promoted to breakeven (entry): got stop={}", pos.stop_loss);
    }

    #[test]
    fn breakeven_not_promoted_before_1r() {
        // LONG: push price to +0.5R (half of the +1R threshold) → no promotion.
        let mut s = warmed_strategy(false);
        let long_stop = s.calculate_stop_loss(100.0, OrderSide::Buy);
        let risk = 100.0 - long_stop;
        s.position = Some(TrendPosition {
            side: OrderSide::Buy, entry_price: 100.0, stop_loss: long_stop,
            quantity: 1.0, remaining_qty: 1.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        let ctx = tick_at(100.0 + risk * 0.5);
        let _ = run_tick(&mut s, ctx);
        let pos = s.position.as_ref().expect("position stays open before +1R");
        assert!((pos.stop_loss - long_stop).abs() < 1e-9,
            "stop_loss unchanged before +1R: got {} expected {}", pos.stop_loss, long_stop);
    }

    #[test]
    fn trailing_stop_exits_full_quantity() {
        // LONG: push price up (ratchet trail), then reverse below trail → exit
        // fires on the FULL remaining qty (no partial-TP ladder ahead of it).
        let mut s = warmed_strategy(false);
        let long_stop = s.calculate_stop_loss(100.0, OrderSide::Buy);
        s.position = Some(TrendPosition {
            side: OrderSide::Buy, entry_price: 100.0, stop_loss: long_stop,
            quantity: 2.0, remaining_qty: 2.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        // 1) Up move ratchets the trail above entry; no exit (price > trail).
        let _ = run_tick(&mut s, tick_at(110.0));
        assert!(s.position.is_some(), "no exit on the up move");
        let trail_after_up = s.position.as_ref().unwrap()
            .trailing_stop.expect("trail set after up move");
        assert!(trail_after_up > 100.0,
            "trail ratcheted above entry after up move: got {}", trail_after_up);
        // 2) Reverse below the trail → trailing-stop exit fires, full quantity.
        let orders = run_tick(&mut s, tick_at(trail_after_up - 1.0));
        assert!(s.position.is_none(), "trailing stop closed the position");
        assert_eq!(orders.len(), 1, "exactly one exit order");
        assert!((orders[0].quantity - 2.0).abs() < 1e-9,
            "full remaining quantity exited (no partial TP ahead of trail): got {}",
            orders[0].quantity);
        assert_eq!(orders[0].side, OrderSide::Sell, "long close sells");
        assert!(orders[0].reduce_only, "exit is reduce_only");
    }

    #[test]
    fn stop_loss_exits_before_breakeven() {
        // LONG: price drops to the initial stop (before any +1R move) → merged
        // block fires with effective_stop = stop_loss (the tighter one pre-trail).
        let mut s = warmed_strategy(false);
        let long_stop = s.calculate_stop_loss(100.0, OrderSide::Buy);
        s.position = Some(TrendPosition {
            side: OrderSide::Buy, entry_price: 100.0, stop_loss: long_stop,
            quantity: 2.0, remaining_qty: 2.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        let ctx = tick_at(long_stop);
        let orders = run_tick(&mut s, ctx);
        assert!(s.position.is_none(), "initial stop must close the position");
        assert_eq!(orders.len(), 1, "exactly one exit order");
        assert_eq!(orders[0].side, OrderSide::Sell, "long close sells");
        assert!(orders[0].reduce_only, "exit is reduce_only");
        assert_eq!(orders[0].order_type, OrderTypeReq::Market, "gap-safe Market exit");
        assert!((orders[0].quantity - 2.0).abs() < 1e-9, "full quantity exited");
    }

    // ── helpers for the on_tick flow tests above ──

    fn base_test_config() -> crate::config::TrendConfig {
        crate::config::TrendConfig {
            ema_fast: 20, ema_slow: 50, ema_trend: 200, rsi_period: 14,
            rsi_min: 40.0, rsi_max: 80.0, min_signal_score: 3, confirmation_ticks: 2,
            risk_reward_ratio: 2.0, capital: 10000.0, risk_per_trade_pct: 2.0,
            max_position_pct: 25.0, trailing_stop_pct: 1.5, trailing_stop_atr_mult: 2.5,
            trailing_activation_pct: 1.5, exit_signal_threshold: 2, sl_buffer_pct: 0.2,
            adx_gate_threshold: 25.0, adx_exit_threshold: 20.0, choppiness_threshold: 38.0,
            volume_ratio_threshold: 1.2, entry_score_threshold: 5, rsi_long_max: 65.0,
            rsi_short_min: 35.0, atr_trailing_mult: 3.0, trade_shorts: false,
            perp_mark_source: None, funding_accrual: false,
        }
    }

    fn tick_at(price: f64) -> TickContext {
        use crate::connector::types::OrderBook;
        let ob = OrderBook {
            symbol: "TESTPAIR-USDT".to_string(),
            bids: vec![(price - 0.5, 10.0)],
            asks: vec![(price + 0.5, 10.0)],
            timestamp: 1_700_000_001_000,
        };
        TickContext {
            order_book: ob,
            recent_bars: Vec::new(),
            balances: std::collections::HashMap::new(),
            open_orders: Vec::new(),
            regime: None,
            regime_confidence: 0.0,
            timestamp: 1_700_000_001_000,
            capital: None,
            replay: false,
        }
    }

    fn run_tick(s: &mut TrendStrategy, ctx: TickContext) -> Vec<OrderRequest> {
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        rt.block_on(s.on_tick(&ctx)).unwrap()
    }

    fn tick_at_replay(price: f64) -> TickContext {
        let mut c = tick_at(price);
        c.replay = true;
        c
    }

    /// Strong smooth uptrend with entry gates lowered so should_activate fires.
    /// (We're testing the *replay* gate, not the entry conditions — so make
    /// entry deterministic.) Proves replay suppresses an entry live would take.
    fn uptrend_strategy() -> TrendStrategy {
        let mut config = base_test_config();
        config.entry_score_threshold = 1;
        config.adx_gate_threshold = 0.1; // effectively disable ADX gate for the test
        let tg = crate::notifications::TelegramBot::new("", "");
        let mut s = TrendStrategy::new("TESTPAIR-USDT", &config, tg);
        for i in 0..260 {
            let p = 100.0 * 1.005_f64.powi(i as i32); // smooth compounding uptrend
            s.update_indicators(&Bar::new(p * 0.999, p * 1.001, p * 0.9985, p, 100.0, 0));
        }
        s.last_bar_count = 260;
        s.position = None; // ignore any stale position file loaded by new()
        s
    }

    #[test]
    fn replay_tick_suppresses_entry_that_live_tick_takes() {
        let last = 100.0 * 1.005_f64.powi(259);
        let is_entry = |orders: &[OrderRequest]| orders.iter().any(|o| !o.reduce_only);

        let mut live = uptrend_strategy();
        let live_orders = run_tick(&mut live, tick_at(last));
        assert!(is_entry(&live_orders),
            "live tick should place an entry order (setup must trigger entry for this test to mean anything)");

        let mut rep = uptrend_strategy();
        let rep_orders = run_tick(&mut rep, tick_at_replay(last));
        assert!(!is_entry(&rep_orders), "replay tick must NOT place an entry order (ghost-entry guard)");
    }

    #[test]
    fn trend_exit_message_loss_is_loud_with_running_total() {
        let msg = trend_exit_message("ETH-USDT", "stop_loss", 1800.0, -259.25, -472.20);
        assert!(msg.starts_with("🛑 LOSS Trend ETH-USDT stop_loss"), "got: {msg}");
        assert!(msg.contains("$-259.25"), "should show this trade pnl: {msg}");
        assert!(msg.contains("Trend running: $-472.20"), "should show running total: {msg}");
    }

    #[test]
    fn trend_exit_message_win_is_rocket_no_loss_marker() {
        let msg = trend_exit_message("BNB-USDT", "tp1", 614.5, 30.02, 93.49);
        assert!(msg.starts_with("📈 Trend BNB-USDT tp1"), "got: {msg}");
        assert!(!msg.contains("LOSS"), "wins must not be marked LOSS: {msg}");
        assert!(msg.contains("Trend running: $+93.49"), "should show running total: {msg}");
    }

    #[test]
    fn short_unrealized_uses_perp_mark_not_spot() {
        use crate::connector::perp_price::FakePerp;
        // warmed_strategy(true) builds a short-enabled strategy around price ~100.
        // Attach a FakePerp returning mark 50; inject a SHORT at entry 100.
        let mut s = warmed_strategy(true)
            .with_perp(std::sync::Arc::new(FakePerp { mark: 50.0, funding: 0.0 }));
        s.position = Some(TrendPosition {
            side: OrderSide::Sell, entry_price: 100.0, stop_loss: 110.0,
            quantity: 1.0, remaining_qty: 1.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        // Spot mid is 100 here; the perp override must force current_price to 50.
        let _ = run_tick(&mut s, tick_at(100.0));
        let st = s.status();
        // Unrealized at perp mark 50: (entry 100 - 50) * 1 = +50. Spot (100) would be 0.
        assert!(st.details.contains("Unrealized: $50.00"),
            "short must be marked at perp 50, not spot 100: {}", st.details);
    }

    /// Exit-redesign regression: a SHORT trailing/breakeven exit whose
    /// effective_stop is hit at the perp mark (perp ABOVE the trail) must push
    /// a Market order, not a Limit at the perp price. A Limit at the perp price
    /// would misfire against the paper fill engine, which gates Buy-to-cover on
    /// `spot_mid <= limit_price` (false when perp > spot). The merged exit block
    /// hardcodes Market for this reason.
    #[test]
    fn short_trailing_exit_with_perp_is_market_not_limit() {
        use crate::connector::perp_price::FakePerp;
        // Spot via tick_at(100). Perp mark 95 — divergent, ABOVE the trail.
        let mut s = warmed_strategy(true)
            .with_perp(std::sync::Arc::new(FakePerp { mark: 95.0, funding: 0.0 }));
        // SHORT already at breakeven (stop_loss == entry), trail ratcheted to 93
        // after price earlier visited 90 (captured by lowest_since_entry). With
        // perp mark 95 >= trail 93, the trailing stop fires.
        s.position = Some(TrendPosition {
            side: OrderSide::Sell, entry_price: 100.0, stop_loss: 100.0, // breakeven
            quantity: 2.0, remaining_qty: 2.0, trailing_stop: Some(93.0),
            highest_since_entry: 100.0, lowest_since_entry: 90.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 1_700_000_001_000,
        });
        let orders = run_tick(&mut s, tick_at(100.0));
        // current_price = 95 (perp override for SHORT).
        // trail = lowest(90) + 3·ATR(≈1) = 93. prev=93 → stays 93.
        // effective_stop = min(stop=100, trail=93) = 93. hit(Sell): 95>=93 → TRUE.
        assert!(orders.iter().any(|o| o.reduce_only && o.side == OrderSide::Buy),
            "expected a buy-to-cover exit order: {:?}", orders);
        for o in &orders {
            if o.reduce_only && o.side == OrderSide::Buy {
                assert_eq!(o.order_type, OrderTypeReq::Market,
                    "SHORT trailing exit must be Market (gap-safe), not Limit at perp: {:?}", o.order_type);
                assert!(o.price.is_none(), "Market exit carries no price: {:?}", o.price);
            }
        }
    }

    /// Exit-redesign control: a LONG stop / trailing exit (no perp attached)
    /// now also uses Market (gap-safe) under the merged exit block. The old
    /// design used Limit-at-spot for longs; the redesign unifies on Market so
    /// trailing stops can't miss on a fast reversal.
    #[test]
    fn long_stop_exit_without_perp_is_market_not_limit() {
        let mut s = warmed_strategy(false); // no perp, longs only
        let long_stop = s.calculate_stop_loss(100.0, OrderSide::Buy); // < 100
        s.position = Some(TrendPosition {
            side: OrderSide::Buy, entry_price: 100.0, stop_loss: long_stop,
            quantity: 2.0, remaining_qty: 2.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 1_700_000_001_000,
        });
        // Price drops below the initial stop → merged block fires a Market exit.
        let ctx = tick_at(long_stop - 1.0);
        let orders = run_tick(&mut s, ctx);
        assert!(orders.iter().any(|o| o.reduce_only && o.side == OrderSide::Sell
            && o.order_type == OrderTypeReq::Market
            && o.price.is_none()),
            "long stop exit must be Market (gap-safe) under the merged block: {:?}", orders);
    }

    /// Finding 2 regression: a position restored from disk with last_funding_time: 0
    /// (an old state file from before the field existed) and a recent entry_time
    /// must NOT accrue a full 8h funding on the first post-restart tick — only
    /// after 8h have actually elapsed since entry. The fix back-fills
    /// last_funding_time = entry_time in load_position.
    #[test]
    fn restored_short_with_zero_last_funding_no_immediate_accrual() {
        use crate::connector::perp_price::FakePerp;
        // Serialize a legacy-style state via the real structs but then strip the
        // last_funding_time key from the JSON to emulate an old state file.
        let pos = TrendPosition {
            side: OrderSide::Sell, entry_price: 100.0, stop_loss: 110.0,
            quantity: 1.0, remaining_qty: 1.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        };
        let mut v = serde_json::to_value(&TrendPositionState {
            position: pos, realized_pnl: 0.0,
        }).unwrap();
        // Remove the key so serde's default (0) kicks in on load — exactly the
        // legacy-file scenario this fix guards against. (as_mut Object remove,
        // since `take()` would leave an explicit null which serde_default ignores.)
        if let Some(obj) = v["position"].as_object_mut() {
            obj.remove("last_funding_time");
        }
        let state_json = v.to_string();

        // Use a dedicated pair name so parallel sibling tests using warmed_strategy
        // (which save_position() into data/TESTPAIR_USDT_trend_position.json on
        // every run_tick) can't race this test's write→load window. The position
        // file path is derived from pair, so a unique pair = a unique file.
        let path = std::path::PathBuf::from("data/TESTRESTOR_USDT_trend_position.json");
        std::fs::create_dir_all("data").ok();
        std::fs::write(&path, state_json).unwrap();

        let config = {
            let mut c = base_test_config();
            c.trade_shorts = true;
            c.funding_accrual = true;
            c
        };
        let tg = crate::notifications::TelegramBot::new("", "");
        // new() calls load_position, which must back-fill last_funding_time.
        let mut s = TrendStrategy::new("TESTRESTOR-USDT", &config, tg)
            .with_perp(std::sync::Arc::new(FakePerp { mark: 100.0, funding: 0.0001 }));
        let _ = std::fs::remove_file(&path); // clean up the temp state file

        // Back-fill happened on load:
        assert_eq!(s.position.as_ref().unwrap().last_funding_time, 1_700_000_000_000,
            "load_position back-fills last_funding_time to entry_time for legacy files");
        // Warm indicators so on_tick proceeds past the ready gate.
        for i in 0..260 {
            let p = 100.0 + (i as f64 * 0.01);
            s.update_indicators(&Bar::new(p - 0.5, p + 0.5, p - 0.25, p, 100.0, 0));
        }
        s.last_bar_count = 260;

        let before = s.realized_pnl;
        let _ = run_tick(&mut s, tick_at(100.0));
        // Only 1s elapsed since entry — must NOT accrue a full 8h funding.
        assert!((s.realized_pnl - before).abs() < 1e-9,
            "no funding accrual expected on first tick after restart: delta={}",
            s.realized_pnl - before);
    }

    #[test]
    fn funding_accrues_negatively_for_short_on_positive_rate() {
        use crate::connector::perp_price::FakePerp;
        let mut s = warmed_strategy(true)
            .with_perp(std::sync::Arc::new(FakePerp { mark: 100.0, funding: 0.0001 }));
        s.config.funding_accrual = true; // test module can mutate private fields
        s.position = Some(TrendPosition {
            side: OrderSide::Sell, entry_price: 100.0, stop_loss: 110.0,
            quantity: 1.0, remaining_qty: 1.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        let before = s.realized_pnl;
        let _ = run_tick(&mut s, tick_at(100.0));
        // Positive funding 0.0001 on notional 100*1 = -0.01 to a short.
        assert!((s.realized_pnl - before - (-0.01)).abs() < 1e-9,
            "realized_pnl should drop by 0.01: before={} after={}", before, s.realized_pnl);
        // last_funding_time advanced to the tick's ms timestamp; won't re-fire next tick.
        assert_eq!(s.position.as_ref().unwrap().last_funding_time, 1_700_000_001_000);
    }

    // ── C1/C2: set_paused + force_flat (routing layer pause / go-flat) ──

    /// C1: set_paused(true) must suppress an entry that a live (unpaused) tick
    /// would take. Mirrors the replay-suppression test's shape.
    #[test]
    fn set_paused_suppresses_entry_that_live_tick_takes() {
        let last = 100.0 * 1.005_f64.powi(259);
        let is_entry = |orders: &[OrderRequest]| orders.iter().any(|o| !o.reduce_only);

        let mut live = uptrend_strategy();
        let live_orders = run_tick(&mut live, tick_at(last));
        assert!(is_entry(&live_orders),
            "live tick should place an entry order (setup must trigger entry for this test to mean anything)");

        let mut paused = uptrend_strategy();
        paused.set_paused(true);
        let paused_orders = run_tick(&mut paused, tick_at(last));
        assert!(!is_entry(&paused_orders), "paused tick must NOT place an entry order");
        assert!(paused.entries_suppressed, "entries_suppressed flag stays set after a paused tick");

        // Resuming re-enables entries.
        paused.set_paused(false);
        let resumed_orders = run_tick(&mut paused, tick_at(last));
        assert!(is_entry(&resumed_orders), "after set_paused(false) entries resume");
    }

    /// C2: force_flat() on an open position emits a Market reduce-only close,
    /// books P&L, clears the position, and suppresses re-entry.
    #[test]
    fn force_flat_closes_open_position_and_suppresses_reentry() {
        let mut s = warmed_strategy(false);
        let long_stop = s.calculate_stop_loss(100.0, OrderSide::Buy);
        s.position = Some(TrendPosition {
            side: OrderSide::Buy, entry_price: 100.0, stop_loss: long_stop,
            quantity: 2.0, remaining_qty: 2.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        let realized_before = s.realized_pnl;

        s.force_flat();
        assert!(s.entries_suppressed, "force_flat sets entries_suppressed");

        // Tick at a price above entry → close realizes a profit (long).
        let orders = run_tick(&mut s, tick_at(110.0));
        assert!(s.position.is_none(), "position cleared by force_flat");
        assert!(s.realized_pnl > realized_before, "force_flat booked P&L: {}", s.realized_pnl);
        assert_eq!(orders.len(), 1, "one Market close order");
        assert_eq!(orders[0].side, OrderSide::Sell, "closing a long sells");
        assert!(orders[0].reduce_only, "close is reduce_only");
        assert_eq!(orders[0].order_type, OrderTypeReq::Market, "gap-safe Market close");
        assert!((orders[0].quantity - 2.0).abs() < 1e-9, "closes full remaining qty");

        // A second tick at an entry-triggering price must NOT re-enter.
        let last = 100.0 * 1.005_f64.powi(259);
        let orders2 = run_tick(&mut s, tick_at(last));
        assert!(orders2.iter().all(|o| o.reduce_only), "no new entry while suppressed");
    }
}
