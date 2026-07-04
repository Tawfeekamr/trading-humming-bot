//! Replay driver: feed a bar stream through a real production engine
//! (`GridStrategy`) wired to a `FillSim` (order → fill simulation) and a
//! `Portfolio` (inventory + realized PnL accounting). The engine runs
//! verbatim — we only build the `TickContext`, drive `on_tick` / `on_fill`,
//! and route its `OrderRequest`s through the `FillSim`.
//!
//! Loop ordering (no lookahead — `FillSim::evaluate` runs against the
//! decision bar before `on_tick` sees it, and `submit` only fills market
//! orders at the same bar's close while limit/stop orders rest for future
//! bars):
//!   for each bar:
//!     1. `fill_sim.evaluate(bar)` → for each fill: `grid.on_fill` + `port.apply_fill`
//!     2. build `TickContext` (recent_bars window, regime=None, replay flag)
//!        and call `grid.on_tick(ctx)` → new orders
//!     3. `fill_sim.submit(new_orders, bar)` → market fills now; limit/stop rest
//!        for each immediate fill: `grid.on_fill` + `port.apply_fill`
//!     4. `fill_sim.cancel(grid.pending_cancels())`
//!     5. record equity at `bar.close`
//!
//! Phase-1 simplification: `on_fill` may itself return `OrderRequest`s (e.g.
//! a stop-loss re placement after a fill). Those are discarded here —
//! `on_tick` re-evaluates every bar and re-emits any orders the strategy
//! still wants. This is fine for grid, whose resting orders are recreated
//! each tick from `grid_layout`. Acceptable for Phase 1; flagged for review.
use std::collections::HashMap;

use crate::capital::CapitalManager;
use crate::config::GridConfig;
use crate::connector::types::OrderBook;
use crate::models::bar::Bar;
use crate::notifications::TelegramBot;
use crate::strategy::grid::GridStrategy;
use crate::strategy::{Strategy, TickContext};

use super::fills::FillSim;
use super::portfolio::{Portfolio, Trade};

/// Replay configuration. `start` / `end` are intentionally absent: this is
/// the bar-fed entry point (`run_grid_on_bars`); a future `run_grid` wrapper
/// can own date-range → bars loading and call into `run_grid_on_bars`.
pub struct ReplayConfig {
    pub symbol: String,
    pub init_cash: f64,
    pub warmup_bars: usize,
    pub grid: GridConfig,
    pub tick_size: f64,
    pub step_size: f64,
    pub taker_fee_bps: f64,
    pub maker_fee_bps: f64,
    pub slippage_bps: f64,
}

pub struct RunResult {
    pub equity_curve: Vec<(i64, f64)>,
    pub trades: Vec<Trade>,
    pub realized: f64,
    pub final_equity: f64,
    pub hodl_return_pct: f64,
}

/// Drive `GridStrategy` over a pre-loaded bar stream. Phase-1 entry point.
pub async fn run_grid_on_bars(rc: &ReplayConfig, bars: Vec<Bar>) -> anyhow::Result<RunResult> {
    // Isolate grid's state file (orders.json / inventory) in a temp dir —
    // NEVER "data", which is the production live directory.
    let tmp = tempfile::TempDir::new()?;
    let mut grid = GridStrategy::new_with_state_dir(
        &rc.symbol,
        &rc.grid,
        rc.tick_size,
        rc.step_size,
        tmp.path().to_str().unwrap(),
        TelegramBot::disabled(),
    );
    let capital = CapitalManager::new(20.0).with_budgets({
        let mut b = std::collections::BTreeMap::new();
        b.insert("grid".to_string(), rc.init_cash);
        b
    });
    let mut sim = FillSim::new(rc.taker_fee_bps, rc.maker_fee_bps, rc.slippage_bps);
    let mut port = Portfolio::new(rc.init_cash, rc.init_cash);
    let mut equity_curve = Vec::with_capacity(bars.len());
    let mut fills_buf = Vec::new();

    for (i, bar) in bars.iter().enumerate() {
        // Capital allocator sees this bar's closing equity before strategy runs.
        capital.sync_equity(port.equity(bar.close), port.cash);
        capital.reset_tick_grants();

        // 1. Evaluate resting orders against this bar (limit/stop crosses).
        sim.evaluate(bar, &mut fills_buf);
        for f in fills_buf.drain(..) {
            // Phase-1 simplification: `on_fill`'s returned orders are discarded.
            let _ = grid.on_fill(&f).await?;
            port.apply_fill(&f);
        }

        // 2. Build context and run the engine for this bar.
        let ctx = build_ctx(&rc.symbol, bar, &bars[..i], &capital, /*replay*/ i < rc.warmup_bars);
        let new_orders = grid.on_tick(&ctx).await?;

        // 3. Submit. Market fills now (at bar.close ± slippage); limit/stop rest.
        sim.submit(new_orders, bar, &mut fills_buf);
        for f in fills_buf.drain(..) {
            let _ = grid.on_fill(&f).await?;
            port.apply_fill(&f);
        }

        // 4. Cancels requested by the strategy this tick.
        sim.cancel(&grid.pending_cancels());

        // 5. Record equity at this bar's close.
        equity_curve.push((bar.timestamp, port.equity(bar.close)));
    }

    let first_close = bars.first().map(|b| b.close).unwrap_or(1.0);
    let last_close = bars.last().map(|b| b.close).unwrap_or(1.0);
    Ok(RunResult {
        equity_curve,
        trades: port.trades.clone(),
        realized: port.realized,
        final_equity: port.equity(last_close),
        hodl_return_pct: (last_close / first_close - 1.0) * 100.0,
    })
}

/// Construct the per-bar `TickContext`. `recent_bars` is the trailing window
/// of up to 200 prior bars (excluding the current bar — no lookahead).
/// `balances` carries a large fake USDT balance so the engine's internal
/// "do I have anything to spend" guards don't trip (real sizing flows through
/// `CapitalManager`). `regime=None` — replay does not synthesize ML regime.
fn build_ctx(
    symbol: &str,
    bar: &Bar,
    prior: &[Bar],
    capital: &CapitalManager,
    replay: bool,
) -> TickContext {
    let recent: Vec<Bar> = prior.iter().rev().take(200).cloned().rev().collect();
    let ob = OrderBook {
        symbol: symbol.into(),
        bids: vec![(bar.close * 0.9999, 1.0)],
        asks: vec![(bar.close * 1.0001, 1.0)],
        timestamp: bar.timestamp,
    };
    let mut balances = HashMap::new();
    balances.insert("USDT".to_string(), 1e9);
    TickContext {
        order_book: ob,
        recent_bars: recent,
        balances,
        open_orders: vec![],
        regime: None,
        regime_confidence: 0.0,
        timestamp: bar.timestamp,
        capital: Some(capital.clone()),
        replay,
    }
}
