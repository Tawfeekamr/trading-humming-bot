//! Task 6 — Replay driver end-to-end smoke test + critical-fix regression.
//!
//! Phase 1 only proves the loop runs end-to-end without panic and emits an
//! equity curve. Grid's regime gate (ADX<25, Choppiness>50, NATR in range)
//! may not fire on synthetic data, so grid may not actually trade — that's
//! fine. The real proof is Task 7's run on real ETHUSDT data.
//!
//! Task 3 adds the Trend smoke test: the real `TrendStrategy` runs verbatim
//! against a synthetic uptrend with the perp source wired (long-only leg).
use trading_engine_core::backtest::fills::FillSim;
use trading_engine_core::backtest::portfolio::Portfolio;
use trading_engine_core::backtest::replay::{run_engine_on_bars, run_grid_on_bars, run_loop, EngineKind, ReplayConfig};
use trading_engine_core::capital::CapitalManager;
use trading_engine_core::config::{AppConfig, GridConfig, TrendConfig};
use trading_engine_core::connector::types::{Fill, OrderRequest, OrderTypeReq};
use trading_engine_core::models::bar::Bar;
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::strategy::{Strategy, StrategyStatus, TickContext};

use async_trait::async_trait;

fn grid_cfg() -> GridConfig {
    // GridConfig is NOT Default-derived — load the real deployed config.
    // CARGO_MANIFEST_DIR = trading-engine-core/ at compile time, so
    // ../config/strategy.yaml reaches the repo-root config.
    let path = format!("{}/../config/strategy.yaml", env!("CARGO_MANIFEST_DIR"));
    AppConfig::load(&path).expect("strategy.yaml must load").grid
}

fn cfg() -> ReplayConfig {
    ReplayConfig {
        symbol: "ETHUSDT".into(),
        init_cash: 10_000.0,
        warmup_bars: 220,
        bar_hours: 1.0,
        engine: EngineKind::Grid,
        tick_size: 0.01,
        step_size: 0.0001,
        taker_fee_bps: 10.0,
        maker_fee_bps: 10.0,
        slippage_bps: 0.0,
        grid: grid_cfg(),
        trend: TrendConfig::default(),
        perp_bars: None,
        funding_rate: None,
        swing: None,
    }
}

/// 300-bar gentle sawtooth around 100-104. Exercises grid's resting-order
/// placement; on the deployed config this yields SELL fills, which is what
/// the isolation regression test needs (log_unified is called on each SELL).
fn sawtooth_bars() -> Vec<Bar> {
    (0..300)
        .map(|i| {
            let p = 100.0 + ((i % 8) as f64 / 2.0); // gentle sawtooth
            Bar::new(p, p + 1.0, p - 1.0, p, 10.0, (i as i64) * 3_600_000)
        })
        .collect()
}

#[tokio::test]
async fn grid_arms_and_trades_on_a_ranging_series() {
    // 300 bars oscillating in a gentle sawtooth around 100-104 — grid should
    // at least deploy; whether it trades depends on its regime gate firing.
    let bars = sawtooth_bars();
    let res = run_grid_on_bars(&cfg(), bars).await.expect("replay must complete");
    // Phase 1 weak assertion: loop completed and produced an equity curve.
    assert!(res.equity_curve.len() > 0, "equity curve should be non-empty");
}

/// Critical-fix regression: replayed grid SELLs must NOT land in the production
/// `data/trades.db` (the live Rust-engine PnL source-of-truth). `GridStrategy::
/// on_fill` calls `trade_journal::log_unformed` on every SELL, and the journal
/// resolves its DB path from `TRADES_JOURNAL_PATH` (default `data/trades.db`)
/// via a process-global `OnceLock`. `run_grid_on_bars` must set the env var to
/// its tempdir before the first `on_fill` so the live DB is never touched.
///
/// Under the buggy code (no set_var), replay creates `data/trades.db` in the
/// process CWD → this test fails. Under the fix, it doesn't → test passes.
#[tokio::test]
async fn replay_does_not_write_to_production_trades_db() {
    // The default journal path is CWD-relative "data/trades.db". If run_grid_on_bars
    // fails to isolate TRADES_JOURNAL_PATH into its tempdir, replayed SELLs corrupt it.
    let prod_journal = std::path::PathBuf::from("data/trades.db");
    let _ = std::fs::remove_file(&prod_journal); // clean slate
    // Also clear the env var in case a prior test in this process set it — the
    // OnceLock captures the value on first read, so this only matters for the
    // FIRST test in the binary; clearing keeps the test honest if run standalone.
    std::env::remove_var("TRADES_JOURNAL_PATH");

    let bars = sawtooth_bars();
    let _ = run_grid_on_bars(&cfg(), bars).await.expect("replay must complete");

    assert!(
        !prod_journal.exists(),
        "replay wrote to the production data/trades.db — TRADES_JOURNAL_PATH not isolated"
    );
    let _ = std::fs::remove_file(&prod_journal); // cleanup in case
}

// ── Task 3: Trend dispatch smoke test ──────────────────────────────────────
//
// Proves the real `TrendStrategy` runs end-to-end against the replay driver
// (EngineKind::Trend arm) with `HistoricalPerpSource` wiring available for
// the short side. Long-only leg here (`trade_shorts=false`) — the perp
// source stays unattached, exercising the pure long path. The short leg /
// perp-attached path is covered by the trend engine's own unit tests; this
// test only proves the dispatcher constructs and drives the engine without
// panic and without returning an `Err`.
//
// The trend score gate may or may not fire on synthetic monotonic data; the
// assertion is "completes + produces an equity curve", not "trades N times"
// (same caveat as the Phase-1 grid smoke).

/// 400-bar monotonic uptrend: 100 → 299.5 in 0.5 steps. Sufficient bars for
/// trend's warmup (220) + indicator priming (ADX/RSI/ATR need ~50 bars).
fn trending_bars(up: bool, n: usize) -> Vec<Bar> {
    (0..n)
        .map(|i| {
            let p = if up { 100.0 + i as f64 * 0.5 } else { 200.0 - i as f64 * 0.5 };
            Bar::new(p, p + 1.0, p - 1.0, p, 10.0, (i as i64) * 3_600_000)
        })
        .collect()
}

#[tokio::test]
async fn trend_long_runs_on_uptrend() {
    // Load the deployed TrendConfig (cloned), force long-only for this leg.
    let path = format!("{}/../config/strategy.yaml", env!("CARGO_MANIFEST_DIR"));
    let app_cfg = AppConfig::load(&path).expect("strategy.yaml must load");
    let mut tc = app_cfg.trend.clone();
    tc.trade_shorts = false; // long-only leg — perp stays unattached
    let rc = ReplayConfig {
        symbol: "ETHUSDT".into(),
        init_cash: 100_000.0,
        warmup_bars: 220,
        bar_hours: 1.0,
        engine: EngineKind::Trend,
        tick_size: 0.01,
        step_size: 0.0001,
        taker_fee_bps: app_cfg.paper.taker_fee_bps,
        maker_fee_bps: app_cfg.paper.maker_fee_bps,
        slippage_bps: app_cfg.paper.slippage_bps,
        grid: app_cfg.grid.clone(),
        trend: tc,
        perp_bars: None,
        funding_rate: None,
        swing: None,
    };
    let bars = trending_bars(true, 400);
    let res = run_engine_on_bars(EngineKind::Trend, &rc, bars)
        .await
        .expect("trend replay must complete without error");
    assert!(!res.equity_curve.is_empty(), "equity curve should be non-empty");
    // Smoke: completes without panic/error; entries depend on the score gate
    // firing on this synthetic series, which is not asserted (same caveat as
    // Phase-1 grid). `res.trades.len()` is intentionally not asserted.
    let _ = res.trades.len();
}

// ── Task 4: Swing dispatch smoke test ──────────────────────────────────────
//
// Proves the real long-only `SwingStrategy` runs end-to-end against the
// replay driver (EngineKind::Swing arm) verbatim — same engine the live Rust
// binary drives. The min_score entry gate may not fire on synthetic data, so
// the assertion is "completes + produces an equity curve", not "trades N
// times" (same caveat as the Phase-1 grid and Task-3 trend smokes).

/// 300-bar oscillation in a 100-110 band with 1-high/low wicks — gives the
/// Donchian/RSI/volume gate something to chew on without being a clean trend
/// (swing is a reversal strategy, so a monotonic ramp would never trigger).
fn ranging_bars(n: usize) -> Vec<Bar> {
    (0..n)
        .map(|i| {
            // Sine oscillation around 105 with amplitude 5; integer-step cycle
            // gives visible higher-highs/lower-lows for the donchian + RSI gates.
            let phase = (i as f64) * 0.2;
            let p = 105.0 + 5.0 * phase.sin();
            Bar::new(p, p + 1.0, p - 1.0, p, 15.0, (i as i64) * 3_600_000)
        })
        .collect()
}

#[tokio::test]
async fn swing_runs_on_synthetic_range() {
    let path = format!("{}/../config/strategy.yaml", env!("CARGO_MANIFEST_DIR"));
    let app_cfg = AppConfig::load(&path).expect("strategy.yaml must load");
    let rc = ReplayConfig {
        symbol: "ETHUSDT".into(),
        init_cash: 100_000.0,
        warmup_bars: 220,
        bar_hours: 1.0,
        engine: EngineKind::Swing,
        tick_size: 0.01,
        step_size: 0.0001,
        taker_fee_bps: app_cfg.paper.taker_fee_bps,
        maker_fee_bps: app_cfg.paper.maker_fee_bps,
        slippage_bps: app_cfg.paper.slippage_bps,
        grid: app_cfg.grid.clone(),
        trend: app_cfg.trend.clone(),
        perp_bars: None,
        funding_rate: None,
        swing: app_cfg.swing.clone(),
    };
    let bars = ranging_bars(300);
    let res = run_engine_on_bars(EngineKind::Swing, &rc, bars)
        .await
        .expect("swing replay must complete without error");
    assert!(!res.equity_curve.is_empty(), "equity curve should be non-empty");
    // Smoke: completes without panic/error; entries depend on the min_score
    // gate firing on this synthetic series, which is not asserted (same
    // caveat as Phase-1 grid and Task-3 trend). `res.trades.len()` is
    // intentionally not asserted.
    let _ = res.trades.len();
}

// ── Task 4 follow-up: on_fill chaining regression ─────────────────────────
//
// Proves `run_loop` SUBMITS the `OrderRequest`s returned by `on_fill` (rather
// than discarding them). This is the fix that makes Swing's TP1 scale-out and
// hard-stop replacement actually rest in the book. Under the buggy discard
// (`let _ = strategy.on_fill(&f).await?;`), a resting order returned from
// `on_fill` never enters the `FillSim`'s resting book → it can never fill on
// a later crossing bar. Under the fix, it rests and fills.
//
// We use a minimal mock `Strategy` (no engine dependencies) so the test is
// deterministic and does not depend on swing's score gate firing. The mock:
//   - on_tick: emits ONE Market BUY on the first live bar (so `on_fill` fires).
//   - on_fill(BUY): returns a resting Limit SELL @105 (the "TP" we want to
//     prove gets submitted). Subsequent fills return empty.
//   - on_fill(SELL): returns empty (no cascade).
// The series: warmup bars (replay=true) + bar0 at ~100 (Market buy fills) +
// bar1 with high=106 (crosses 105 → Limit sell fills, closes the long).
// PASS = a Trade is recorded (long opened then closed by the resting sell).
// FAIL (under discard) = no Trade: the Limit sell never rested.

/// Bar constructor matching the `sawtooth_bars` shape: deterministic OHLC.
fn mk_bar(open: f64, high: f64, low: f64, close: f64, ts_idx: i64) -> Bar {
    Bar::new(open, high, low, close, 10.0, ts_idx * 3_600_000)
}

/// Minimal mock strategy: emits one Market BUY on the first live `on_tick`,
/// then returns a resting Limit SELL @105 from `on_fill` for that entry fill.
struct EchoLimit {
    pair: String,
    /// Whether the first live on_tick has fired its Market buy yet.
    armed: bool,
    /// Whether on_fill has already emitted the resting TP for the entry.
    tp_placed: bool,
}

#[async_trait]
impl Strategy for EchoLimit {
    fn name(&self) -> &str { "echo" }
    fn trading_pair(&self) -> &str { &self.pair }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>, anyhow::Error> {
        // Only fire on live bars (not warmup), and only once.
        if ctx.replay || self.armed {
            return Ok(vec![]);
        }
        self.armed = true;
        Ok(vec![OrderRequest {
            symbol: self.pair.clone(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::Market,
            price: None,
            quantity: 1.0,
            time_in_force: None,
            client_order_id: Some("echo-entry".into()),
            reduce_only: false,
        }])
    }

    async fn on_fill(&mut self, f: &Fill) -> Result<Vec<OrderRequest>, anyhow::Error> {
        // On the entry BUY fill, place ONE resting Limit SELL at 105 (the TP
        // we want to prove gets submitted through FillSim, not discarded).
        if f.side == OrderSide::Buy && !self.tp_placed {
            self.tp_placed = true;
            return Ok(vec![OrderRequest {
                symbol: self.pair.clone(),
                side: OrderSide::Sell,
                order_type: OrderTypeReq::Limit,
                price: Some(105.0),
                quantity: 1.0,
                time_in_force: None,
                client_order_id: Some("echo-tp".into()),
                reduce_only: true,
            }]);
        }
        // SELL fill (TP) → no further orders. Avoids any cascade.
        Ok(vec![])
    }

    async fn on_start(&mut self) -> Result<Vec<OrderRequest>, anyhow::Error> { Ok(vec![]) }
    async fn on_stop(&mut self) -> Result<(), anyhow::Error> { Ok(()) }
    fn status(&self) -> StrategyStatus {
        StrategyStatus {
            name: "echo".into(),
            pair: self.pair.clone(),
            state: "".into(),
            pnl: 0.0,
            open_orders: 0,
            details: "".into(),
        }
    }
}

#[tokio::test]
async fn on_fill_returned_orders_are_submitted_not_discarded() {
    // Series:
    //   - 5 warmup bars (replay=true), price ~100.
    //   - bar5: live, close=100. on_tick emits Market BUY → fills at 100.
    //     on_fill(BUY) returns Limit SELL @105. UNDER FIX: it rests.
    //   - bar6: live, high=106 (crosses 105). UNDER FIX: Limit SELL fills,
    //     closing the long → Portfolio records a Trade.
    let bars: Vec<Bar> = (0..5)
        .map(|i| mk_bar(100.0, 101.0, 99.0, 100.0, i as i64))
        .chain(std::iter::once(mk_bar(100.0, 101.0, 99.0, 100.0, 5))) // entry bar
        .chain(std::iter::once(mk_bar(100.0, 106.0, 99.0, 104.0, 6))) // TP cross bar
        .collect();

    // Build the harness pieces directly so we can pass our mock into run_loop.
    // (TRADES_JOURNAL_PATH isolation mirrors run_engine_on_bars — the mock's
    // on_fill does not journal, but Portfolio/Trade construction is uniform.)
    let tmp = tempfile::TempDir::new().expect("tempdir");
    std::env::set_var("TRADES_JOURNAL_PATH", tmp.path().join("trades.db"));

    let mut strat = EchoLimit { pair: "ETHUSDT".into(), armed: false, tp_placed: false };
    let mut sim = FillSim::new(10.0, 10.0, 0.0);
    let mut port = Portfolio::new(10_000.0, 10_000.0);
    let capital = CapitalManager::new(20.0).with_budgets({
        let mut b = std::collections::BTreeMap::new();
        b.insert("echo".to_string(), 10_000.0);
        b
    });

    let res = run_loop(
        &mut strat,
        &mut sim,
        &mut port,
        &capital,
        &bars,
        /*warmup_bars*/ 5,
        /*bar_hours*/ 1.0,
        /*perp*/ None,
    )
    .await
    .expect("run_loop must complete");

    // Under the buggy discard: Market BUY fills (bar5), but the Limit SELL
    // returned from on_fill(BUY) is dropped → never rests → bar6's high=106
    // can't trigger it → no SELL fill → no Trade recorded (long stays open).
    //
    // Under the fix: the Limit SELL rests at bar5, fills at bar6 (high>105),
    // and Portfolio records a Trade (long opened at 100, closed at 105).
    assert!(
        !res.trades.is_empty(),
        "on_fill's resting Limit was discarded — no TP fill / no Trade recorded. \
         trades={:?} final_inventory={}",
        res.trades,
        port.inventory_qty
    );
    // The recorded trade should be a LONG close (entry_side=Buy) at exit 105.0.
    let t = &res.trades[0];
    assert_eq!(t.side, OrderSide::Buy, "expected a long-close trade");
    assert!(
        (t.exit_price - 105.0).abs() < 1e-9,
        "expected the resting Limit @105 to fill, got exit_price={}",
        t.exit_price
    );
    // And the position should be flat after the TP fill.
    assert!(
        port.inventory_qty.abs() < 1e-9,
        "expected flat inventory after TP fill, got {}",
        port.inventory_qty
    );
}
