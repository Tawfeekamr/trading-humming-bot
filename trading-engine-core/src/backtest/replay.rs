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
//!     1. `fill_sim.evaluate(bar)` → for each fill:
//!          a. `strategy.on_fill(fill)` → returns reactive `OrderRequest`s
//!          b. `port.apply_fill(fill)`
//!          c. submit reactive orders through `fill_sim.submit(.., bar, ..)`
//!             (resting for our engines; Market would same-bar fill — guarded
//!             by a `debug_assert!` since no engine returns Market today)
//!     2. build `TickContext` (recent_bars window, regime=None, replay flag)
//!        and call `strategy.on_tick(ctx)` → new orders
//!     3. `fill_sim.submit(new_orders, bar)` → market fills now; limit/stop rest
//!        for each immediate fill: same a/b/c capture-and-submit as step 1
//!     4. `fill_sim.cancel(strategy.pending_cancels())`
//!     5. record equity at `bar.close`
//!
//! `on_fill`'s returned orders are SUBMITTED (not discarded) at both sites.
//! This matters for Swing: its `on_fill` returns resting TP1 (LimitMaker) and
//! hard-stop (StopMarket) orders on entry-fill, and a runner-stop replacement
//! on TP1-fill — `on_tick` does NOT re-emit these. Grid/Trend/MR return empty
//! from `on_fill` today, so the extra `submit` call is a no-op for them. The
//! `debug_assert!` at each site guards against a future engine returning a
//! Market order from `on_fill` (which would same-bar fill and could cascade).
use std::collections::HashMap;

use serde::{Deserialize, Serialize};

use crate::capital::CapitalManager;
use crate::config::GridConfig;
use crate::connector::types::OrderBook;
use crate::models::bar::Bar;
use crate::notifications::TelegramBot;
use crate::strategy::grid::GridStrategy;
use crate::strategy::{MarketRegime, Strategy, TickContext};

use super::fills::FillSim;
use super::perp::HistoricalPerpSource;
use super::portfolio::{Portfolio, Trade};

/// Which production engine to drive in the replay loop. Grid is Phase-1;
/// Trend added in Task 3; Swing added in Task 4; MeanReversion added in Task 5.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineKind {
    Grid,
    Trend,
    Swing,
    MeanReversion,
}

impl EngineKind {
    /// CapitalManager budget key — matches what each engine's `Strategy::name`
    /// returns AND the keys configured for `with_budgets`. Centralized here so
    /// `build_capital` doesn't depend on each strategy's `name()` impl.
    pub fn budget_key(self) -> &'static str {
        match self {
            EngineKind::Grid => "grid",
            EngineKind::Trend => "trend",
            EngineKind::Swing => "swing",
            EngineKind::MeanReversion => "mean_reversion",
        }
    }
}

#[derive(Deserialize)]
struct RegimeEntry {
    ts: i64,
    regime: i32,
    confidence: f64,
}

fn norm_pair(p: &str) -> String {
    p.to_uppercase().replace('-', "")
}

/// Per-pair regime label timeline for replay injection. Pairs normalize by
/// uppercasing and stripping '-', so "ETHUSDT" (backtest symbol) and
/// "ETH-USDT" (regime-pusher key) resolve to the same timeline.
#[derive(Debug, Clone, Default)]
pub struct RegimeTimeline {
    map: HashMap<String, Vec<(i64, i32, f64)>>, // pair -> sorted (ts_ms, regime, confidence)
}

impl RegimeTimeline {
    pub fn from_json_str(s: &str) -> anyhow::Result<Self> {
        let raw: HashMap<String, Vec<RegimeEntry>> = serde_json::from_str(s)?;
        let mut map = HashMap::new();
        for (pair, mut entries) in raw {
            entries.sort_by_key(|e| e.ts);
            let v: Vec<(i64, i32, f64)> = entries
                .into_iter()
                .map(|e| (e.ts, e.regime, e.confidence))
                .collect();
            map.insert(norm_pair(&pair), v);
        }
        Ok(Self { map })
    }

    pub fn from_json_file(path: &std::path::Path) -> anyhow::Result<Self> {
        let s = std::fs::read_to_string(path)?;
        Self::from_json_str(&s)
    }

    /// Most-recent label with ts ≤ ts_ms (regime persists until updated, like
    /// the live cache TTL). Returns None if no label is at-or-before ts_ms.
    pub fn get(&self, pair: &str, ts_ms: i64) -> Option<(i32, f64)> {
        let v = self.map.get(&norm_pair(pair))?;
        v.iter()
            .rev()
            .find(|(t, _, _)| *t <= ts_ms)
            .map(|(_, r, c)| (*r, *c))
    }

    /// Map a raw int label to MarketRegime (0=Ranging, 1=Trending, else Danger).
    pub fn to_market_regime(label: Option<(i32, f64)>) -> (Option<MarketRegime>, f64) {
        match label {
            Some((0, c)) => (Some(MarketRegime::Ranging), c),
            Some((1, c)) => (Some(MarketRegime::Trending), c),
            Some((_, c)) => (Some(MarketRegime::Danger), c),
            None => (None, 0.0),
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
/// (Task 3) + `swing` (Task 4). MR fields append in Task 5.
/// `Clone` so Phase 4's `sweep_is` can clone `rc` per grid point, apply the
/// override, and run that variant on the IS slice without mutating the caller's
/// template. All fields are already `Clone` (configs, `Bar`, `EngineKind`,
/// primitives) — this derive is a capability addition, not a logic change.
#[derive(Debug, Clone)]
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
    /// Swing dispatch input: production `SwingConfig` (cloned from AppConfig).
    /// `tick_size`/`step_size` are injected from `ReplayConfig` before
    /// construction (mirrors main.rs:151-153). Swing is long-only, so no
    /// perp is needed — the signed Portfolio (Task 2) handles it directly.
    pub swing: Option<crate::config::SwingConfig>,
    /// MeanReversion dispatch input: production `MeanReversionConfig` (cloned
    /// from AppConfig). MR is long-only (buy flush / sell revert) and its
    /// `on_fill` returns empty (exits decided in `on_tick`), so the run_loop
    /// `on_fill`-chaining fix (Task 4) is a no-op for it. No perp needed.
    pub mean_reversion: crate::config::MeanReversionConfig,
    /// Optional ML regime timeline. When set, each bar's TickContext gets the
    /// regime label active at bar.timestamp (most-recent at-or-before). None
    /// → regime=None (current back-compat behavior).
    pub regime: Option<RegimeTimeline>,
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
                rc.regime.as_ref(),
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
                rc.regime.as_ref(),
            )
            .await?)
        }
        EngineKind::Swing => {
            // Mirror main.rs:151-153 — clone the deployed SwingConfig and
            // inject tick/step before construction so resting-order rounding
            // (round_step) doesn't no-op on `None`. Long-only: no perp needed,
            // so the signed Portfolio (Task 2) handles fills directly.
            let mut sc = rc
                .swing
                .clone()
                .ok_or_else(|| anyhow::anyhow!("swing config required for EngineKind::Swing"))?;
            sc.tick_size = Some(rc.tick_size);
            sc.step_size = Some(rc.step_size);
            let mut swing = crate::strategy::swing::SwingStrategy::new(
                &rc.symbol,
                &sc,
                crate::notifications::TelegramBot::disabled(),
            );
            Ok(run_loop(
                &mut swing,
                &mut sim,
                &mut port,
                &capital,
                &bars,
                rc.warmup_bars,
                rc.bar_hours,
                None,
                rc.regime.as_ref(),
            )
            .await?)
        }
        EngineKind::MeanReversion => {
            // MR's state file (data/<pair>_mean_reversion_state.json) is CWD-relative and NOT
            // env-driven, so unlike grid state it can't be redirected to the TempDir. Remove any
            // pre-existing file for a fresh start (load_state silently ignores missing). MR writes
            // a small idempotent state file to CWD data/ during the run — documented isolation gap
            // (grid state + trade journal ARE isolated).
            // FIDELITY GAP: build_ctx synthesizes a mid-only order book (single 1-unit bid/ask
            // around bar.close); MR's `calculate_bid_depth` signal is degenerate in replay (depth
            // always ~1 unit). Faithful depth needs a real historical L2 book — out of scope here.
            let _ = std::fs::remove_file(format!("data/{}_mean_reversion_state.json", rc.symbol));
            let mut mr = crate::strategy::mean_reversion::MeanReversionStrategy::new(
                &rc.symbol,
                &rc.mean_reversion,
                crate::notifications::TelegramBot::disabled(),
            );
            Ok(run_loop(
                &mut mr,
                &mut sim,
                &mut port,
                &capital,
                &bars,
                rc.warmup_bars,
                rc.bar_hours,
                None,
                rc.regime.as_ref(),
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
///     1. `fill_sim.evaluate(bar)` → for each fill:
///          `on_fill` (capture returned orders) + `port.apply_fill`, then
///          `fill_sim.submit(returned_orders, bar)` so they enter the resting
///          book (or same-bar fill if Market — `debug_assert!` guards this).
///     2. build `TickContext` (recent_bars window, regime=None, replay flag)
///        and call `on_tick(ctx)` → new orders
///     3. `fill_sim.submit(new_orders, bar)` → market fills now; limit/stop rest
///        for each immediate fill: same capture-and-submit as step 1.
///     4. `fill_sim.cancel(pending_cancels())`
///     5. record equity at `bar.close`
///
/// Two additions over Phase 1, both BEFORE `on_tick` (so the engine sees them
/// this bar): `perp.set_clock(bar.timestamp)` advances the perp harness clock
/// (needed for trend-short MTM/funding; grid passes `None`), and
/// `capital.set_deployed({name → port.deployed(bar.close)})` mirrors live's
/// per-tick deployment push so CapitalManager budget caps behave correctly.
///
/// `on_fill`'s returned orders are wired back through `fill_sim.submit` (not
/// discarded). Swing's `on_fill` returns resting TP1/stop orders that
/// `on_tick` does NOT re-emit — submitting them here is the only way they
/// reach the resting book. Grid/Trend/MR return empty, so this is a no-op for
/// them.
pub async fn run_loop(
    strategy: &mut dyn Strategy,
    sim: &mut FillSim,
    port: &mut Portfolio,
    capital: &CapitalManager,
    bars: &[Bar],
    warmup_bars: usize,
    _bar_hours: f64,
    perp: Option<&HistoricalPerpSource>,
    regime: Option<&RegimeTimeline>,
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
        // Capture `on_fill`'s returned orders instead of discarding them. Grid
        // re-emits its resting orders each `on_tick` from `grid_layout`, so it
        // returns empty here; trend also returns empty. But Swing returns
        // non-empty resting orders (entry-buy fill → LimitMaker TP1 + StopMarket
        // hard stop; TP1 fill → runner-stop replacement) that `on_tick` does NOT
        // re-emit — discarding them meant TP1 scale-out never fired and the hard
        // stop drifted. Submitting them through `sim.submit(.., bar, ..)` puts
        // them in the resting book (or, if any future engine returns Market,
        // fills at bar.close). Errors still propagate via `?`.
        let mut on_fill_orders = Vec::new();
        for f in fills_buf.drain(..) {
            on_fill_orders.extend(strategy.on_fill(&f).await?);
            port.apply_fill(&f);
        }
        // Submit reactive orders from on_fill (resting for our engines).
        // decision_bar = bar — same bar that just produced the fill.
        let mut reactive = Vec::new();
        sim.submit(on_fill_orders, bar, &mut reactive);
        // No engine returns Market from on_fill today; if one did, `reactive`
        // would hold same-bar market fills that we must NOT also re-feed (infinite
        // loop). The assert catches a future regression. (reactive market fills,
        // if a future engine needs them, must be held for next-bar-open — out of
        // scope.)
        debug_assert!(reactive.is_empty(), "on_fill returned a Market order — same-bar cascade not supported");

        // 2. Build context and run the engine for this bar.
        let regime_label = regime.and_then(|t| t.get(symbol_of(strategy), bar.timestamp));
        let ctx = build_ctx_from(symbol_of(strategy), bar, &bars[..i], capital, /*replay*/ i < warmup_bars, regime_label);
        let new_orders = strategy.on_tick(&ctx).await?;

        // 3. Submit. Market fills now (at bar.close ± slippage); limit/stop rest.
        sim.submit(new_orders, bar, &mut fills_buf);
        // Same capture-and-submit as site 1 — see comment there for rationale.
        let mut on_fill_orders = Vec::new();
        for f in fills_buf.drain(..) {
            on_fill_orders.extend(strategy.on_fill(&f).await?);
            port.apply_fill(&f);
        }
        let mut reactive = Vec::new();
        sim.submit(on_fill_orders, bar, &mut reactive);
        debug_assert!(reactive.is_empty(), "on_fill returned a Market order — same-bar cascade not supported");

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
    regime: Option<(i32, f64)>,
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
    let (regime, regime_confidence) = RegimeTimeline::to_market_regime(regime);
    TickContext {
        order_book: ob,
        recent_bars: recent,
        balances,
        open_orders: vec![],
        regime,
        regime_confidence,
        timestamp: bar.timestamp,
        capital: Some(capital.clone()),
        replay,
    }
}

#[cfg(test)]
mod regime_timeline_tests {
    use super::RegimeTimeline;

    #[test]
    fn get_returns_most_recent_label_at_or_before_ts() {
        // Pair keys normalize: "ETHUSDT" and "ETH-USDT" resolve to the same timeline.
        let json = r#"{"ETH-USDT": [
            {"ts": 1000, "regime": 1, "confidence": 0.6},
            {"ts": 2000, "regime": 0, "confidence": 0.8},
            {"ts": 3000, "regime": 0, "confidence": 0.7}
        ]}"#;
        let tl = RegimeTimeline::from_json_str(json).unwrap();
        // Before first label → None
        assert_eq!(tl.get("ETHUSDT", 999), None);
        // Exactly at a label → that label
        assert_eq!(tl.get("ETHUSDT", 2000), Some((0, 0.8)));
        // Between labels → most recent at-or-before
        assert_eq!(tl.get("ETH-USDT", 2500), Some((0, 0.8)));
        // After last label → last label (persists until updated, like live TTL)
        assert_eq!(tl.get("ETHUSDT", 9999), Some((0, 0.7)));
        // Unknown pair → None
        assert_eq!(tl.get("BTCUSDT", 2000), None);
    }

    #[test]
    fn empty_timeline_is_none() {
        let tl = RegimeTimeline::default();
        assert_eq!(tl.get("ETHUSDT", 1000), None);
    }
}
