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
use super::perp::HistoricalPerpSource;
use super::portfolio::{Portfolio, Trade};

/// Which production engine to drive in the replay loop. Grid is Phase-1;
/// Trend added in Task 3; Swing/MeanReversion come in Tasks 4/5.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EngineKind {
    Grid,
    Trend,
}

impl EngineKind {
    /// CapitalManager budget key — matches what each engine's `Strategy::name`
    /// returns AND the keys configured for `with_budgets`. Centralized here so
    /// `build_capital` doesn't depend on each strategy's `name()` impl.
    pub fn budget_key(self) -> &'static str {
        match self {
            EngineKind::Grid => "grid",
            EngineKind::Trend => "trend",
        }
    }
}

/// Replay configuration. `start` / `end` are intentionally absent: this is
/// the bar-fed entry point (`run_grid_on_bars` / `run_engine_on_bars`); a
/// future wrapper can own date-range → bars loading and call into these.
///
/// `engine` selects which production strategy to drive; `bar_hours` is the
/// bar interval in hours (used for Sharpe annualization downstream). Per-
/// engine configs: `grid` (Phase 1) + `trend`/`perp_bars`/`funding_rate`
/// (Task 3). Swing/MR fields append in Tasks 4/5.
#[derive(Debug)]
pub struct ReplayConfig {
    pub symbol: String,
    pub init_cash: f64,
    pub warmup_bars: usize,
    pub bar_hours: f64,
    pub engine: EngineKind,
    pub grid: GridConfig,
    pub tick_size: f64,
    pub step_size: f64,
    pub taker_fee_bps: f64,
    pub maker_fee_bps: f64,
    pub slippage_bps: f64,
    /// Trend dispatch inputs. `trend` is the production `TrendConfig`
    /// (cloned from AppConfig for replay). `perp_bars`/`funding_rate`
    /// construct the `HistoricalPerpSource` used for short-side MTM and
    /// funding accrual when `trend.trade_shorts == true`; both `None` for
    /// the long-only leg.
    pub trend: crate::config::TrendConfig,
    pub perp_bars: Option<Vec<Bar>>,
    pub funding_rate: Option<f64>,
}

#[derive(Debug)]
pub struct RunResult {
    pub equity_curve: Vec<(i64, f64)>,
    pub trades: Vec<Trade>,
    pub realized: f64,
    pub final_equity: f64,
    pub hodl_return_pct: f64,
}

/// Drive `GridStrategy` over a pre-loaded bar stream. Phase-1 entry point,
/// kept as a thin wrapper so existing callers/tests compile unchanged.
pub async fn run_grid_on_bars(rc: &ReplayConfig, bars: Vec<Bar>) -> anyhow::Result<RunResult> {
    run_engine_on_bars(EngineKind::Grid, rc, bars).await
}

/// Engine-agnostic dispatcher: construct the strategy + state for `kind`,
/// then hand off to `run_loop`. Construction (TempDir, set_var, strategy
/// ctor) happens here. `run_loop` itself returns `anyhow::Result<RunResult>`:
/// unlike Phase 1 (grid-only, which never errors), Trend/Swing/MR can return
/// `Err` from `on_tick`/`on_fill` (disk writes, indicator paths) — those
/// propagate as `anyhow::Error` so callers see a real error instead of a panic.
pub async fn run_engine_on_bars(
    kind: EngineKind,
    rc: &ReplayConfig,
    bars: Vec<Bar>,
) -> anyhow::Result<RunResult> {
    // Isolate state files (orders.json / inventory) in a temp dir — NEVER
    // "data", which is the production live directory.
    let tmp = tempfile::TempDir::new()?;

    // Route trade_journal::log_unified away from the live data/trades.db
    // (the production source-of-truth) into this run's isolated tempdir. Must
    // be set before the first on_fill, which lazily initializes the journal's
    // OnceLock-connected DB path. NOTE: set_var is process-global — safe here
    // because the backtest binary is single-purpose (no other engine runs in
    // this process). See task-6 critical-fix note.
    std::env::set_var("TRADES_JOURNAL_PATH", tmp.path().join("trades.db"));

    let capital = build_capital(rc);
    let mut sim = FillSim::new(rc.taker_fee_bps, rc.maker_fee_bps, rc.slippage_bps);
    let mut port = Portfolio::new(rc.init_cash, rc.init_cash);

    match kind {
        EngineKind::Grid => {
            let mut grid = GridStrategy::new_with_state_dir(
                &rc.symbol,
                &rc.grid,
                rc.tick_size,
                rc.step_size,
                tmp.path().to_str().unwrap(),
                TelegramBot::disabled(),
            );
            Ok(run_loop(
                &mut grid,
                &mut sim,
                &mut port,
                &capital,
                &bars,
                rc.warmup_bars,
                rc.bar_hours,
                None,
            )
            .await?)
        }
        EngineKind::Trend => {
            let mut trend = crate::strategy::trend::TrendStrategy::new(
                &rc.symbol,
                &rc.trend,
                TelegramBot::disabled(),
            );
            // Attach a `HistoricalPerpSource` iff shorts are enabled — the
            // trend engine uses it for short-side MTM (`perp_mark`) and
            // funding accrual. Long-only legs skip perp entirely. The same
            // `Arc` is handed to both `with_perp` (as `Arc<dyn PerpPriceSource>`)
            // and `run_loop` (as `Option<&HistoricalPerpSource>` via
            // `as_deref`) so `set_clock(bar.timestamp)` can drive it each bar.
            let perp = if rc.trend.trade_shorts {
                let p = std::sync::Arc::new(crate::backtest::perp::HistoricalPerpSource::from_bars(
                    rc.perp_bars.clone().unwrap_or_default(),
                    rc.funding_rate,
                ));
                trend = trend.with_perp(p.clone());
                Some(p)
            } else {
                None
            };
            Ok(run_loop(
                &mut trend,
                &mut sim,
                &mut port,
                &capital,
                &bars,
                rc.warmup_bars,
                rc.bar_hours,
                perp.as_deref(),
            )
            .await?)
        }
    }
}

/// Build the `CapitalManager` with the per-engine budget cap keyed off
/// `EngineKind::budget_key`. Live uses budget caps to keep any single
/// strategy from monopolizing the pool; replay must do the same so multi-
/// engine Phase-2 caps behave like live.
fn build_capital(rc: &ReplayConfig) -> CapitalManager {
    CapitalManager::new(20.0).with_budgets({
        let mut b = std::collections::BTreeMap::new();
        b.insert(rc.engine.budget_key().to_string(), rc.init_cash);
        b
    })
}

/// The engine-agnostic bar loop extracted from the Phase-1 grid-only
/// `run_grid_on_bars`. Identical ordering to Phase 1 (no lookahead):
///   for each bar:
///     1. `fill_sim.evaluate(bar)` → for each fill: `on_fill` + `port.apply_fill`
///     2. build `TickContext` (recent_bars window, regime=None, replay flag)
///        and call `on_tick(ctx)` → new orders
///     3. `fill_sim.submit(new_orders, bar)` → market fills now; limit/stop rest
///        for each immediate fill: `on_fill` + `port.apply_fill`
///     4. `fill_sim.cancel(pending_cancels())`
///     5. record equity at `bar.close`
///
/// Two additions over Phase 1, both BEFORE `on_tick` (so the engine sees them
/// this bar): `perp.set_clock(bar.timestamp)` advances the perp harness clock
/// (needed for trend-short MTM/funding; grid passes `None`), and
/// `capital.set_deployed({name → port.deployed(bar.close)})` mirrors live's
/// per-tick deployment push so CapitalManager budget caps behave correctly.
async fn run_loop(
    strategy: &mut dyn Strategy,
    sim: &mut FillSim,
    port: &mut Portfolio,
    capital: &CapitalManager,
    bars: &[Bar],
    warmup_bars: usize,
    _bar_hours: f64,
    perp: Option<&HistoricalPerpSource>,
) -> anyhow::Result<RunResult> {
    let mut equity_curve = Vec::with_capacity(bars.len());
    let mut fills_buf = Vec::new();

    for (i, bar) in bars.iter().enumerate() {
        // Capital allocator sees this bar's closing equity before strategy runs.
        capital.sync_equity(port.equity(bar.close), port.cash);
        capital.reset_tick_grants();

        // Advance the perp clock BEFORE on_tick so short MTM/funding are
        // as-of this bar (no lookahead). Grid passes None (no perp needed).
        if let Some(p) = perp {
            p.set_clock(bar.timestamp);
        }
        // Track cumulative deployment so CapitalManager budget caps behave
        // like live. `strategy.name()` is the same key build_capital used.
        let mut deployed = std::collections::BTreeMap::new();
        deployed.insert(strategy.name().to_string(), port.deployed(bar.close));
        capital.set_deployed(deployed);

        // 1. Evaluate resting orders against this bar (limit/stop crosses).
        sim.evaluate(bar, &mut fills_buf);
        for f in fills_buf.drain(..) {
            // Phase-1 simplification: `on_fill`'s returned orders are discarded
            // (on_tick re-emits them next bar). Errors, however, propagate via
            // `?` — grid never errors here, but Trend/Swing/MR can (disk writes,
            // indicator paths). Replaces the Phase-1 `.expect` panic.
            let _ = strategy.on_fill(&f).await?;
            port.apply_fill(&f);
        }

        // 2. Build context and run the engine for this bar.
        let ctx = build_ctx_from(symbol_of(strategy), bar, &bars[..i], &capital, /*replay*/ i < warmup_bars);
        let new_orders = strategy.on_tick(&ctx).await?;

        // 3. Submit. Market fills now (at bar.close ± slippage); limit/stop rest.
        sim.submit(new_orders, bar, &mut fills_buf);
        for f in fills_buf.drain(..) {
            let _ = strategy.on_fill(&f).await?;
            port.apply_fill(&f);
        }

        // 4. Cancels requested by the strategy this tick.
        sim.cancel(&strategy.pending_cancels());

        // 5. Record equity at this bar's close.
        equity_curve.push((bar.timestamp, port.equity(bar.close)));
    }

    let first_close = bars.first().map(|b| b.close).filter(|p| *p > 0.0).unwrap_or(1.0);
    let last_close = bars.last().map(|b| b.close).filter(|p| *p > 0.0).unwrap_or(1.0);
    Ok(RunResult {
        equity_curve,
        trades: port.trades.clone(),
        realized: port.realized,
        final_equity: port.equity(last_close),
        hodl_return_pct: (last_close / first_close - 1.0) * 100.0,
    })
}

/// Helper: pull the trading pair out of a `dyn Strategy` for `build_ctx_from`.
/// (`GridStrategy::trading_pair()` returns the configured `pair`; we don't
/// have the original `rc.symbol` here without threading it through, and
/// threading it would couple the loop to per-engine symbol semantics. The
/// `TickContext.order_book.symbol` is the only consumer and grid compares
/// it against its own pair, so they match.)
fn symbol_of(strategy: &dyn Strategy) -> &str {
    strategy.trading_pair()
}

/// Construct the per-bar `TickContext`. `recent_bars` is the trailing window
/// of up to 200 prior bars (excluding the current bar — no lookahead).
/// `balances` carries a large fake USDT balance so the engine's internal
/// "do I have anything to spend" guards don't trip (real sizing flows through
/// `CapitalManager`). `regime=None` — replay does not synthesize ML regime.
///
/// Warmup gate: when `replay == true` (the bar index is in the warmup
/// window), we synthesize the `OrderBook` with **empty `bids` and `asks`** so
/// `mid_price()` returns `None`. This matters because `GridStrategy::on_tick`
/// ignores `ctx.replay` entirely — its only entry gate is
/// `evaluate_state_with_ml` which flips `GridState::Active`. Grid updates its
/// stateful indicators (ADX / Choppiness / ATR / SupportResistance) from
/// `recent_bars` *before* the `mid_price` None-check (grid.rs ~533-554), then
/// early-returns on `None` mid_price (grid.rs ~557-565) without ever calling
/// `evaluate_state_with_ml`. So warmup bars prime indicators but cannot arm
/// the grid → zero warmup trades, indicators warm by the time live bars
/// arrive. (Equivalent to a trend-style replay gate without needing the
/// strategy to read the flag.)
fn build_ctx_from(
    symbol: &str,
    bar: &Bar,
    prior: &[Bar],
    capital: &CapitalManager,
    replay: bool,
) -> TickContext {
    let recent: Vec<Bar> = prior.iter().rev().take(200).cloned().rev().collect();
    let ob = if replay {
        // Empty book → mid_price() = None → grid early-returns after priming
        // indicators. Suppresses all warmup entries.
        OrderBook {
            symbol: symbol.into(),
            bids: vec![],
            asks: vec![],
            timestamp: bar.timestamp,
        }
    } else {
        OrderBook {
            symbol: symbol.into(),
            bids: vec![(bar.close * 0.9999, 1.0)],
            asks: vec![(bar.close * 1.0001, 1.0)],
            timestamp: bar.timestamp,
        }
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
