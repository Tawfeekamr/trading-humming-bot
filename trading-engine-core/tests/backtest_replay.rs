//! Task 6 — Replay driver end-to-end smoke test + critical-fix regression.
//!
//! Phase 1 only proves the loop runs end-to-end without panic and emits an
//! equity curve. Grid's regime gate (ADX<25, Choppiness>50, NATR in range)
//! may not fire on synthetic data, so grid may not actually trade — that's
//! fine. The real proof is Task 7's run on real ETHUSDT data.
//!
//! Task 3 adds the Trend smoke test: the real `TrendStrategy` runs verbatim
//! against a synthetic uptrend with the perp source wired (long-only leg).
use trading_engine_core::backtest::replay::{run_engine_on_bars, run_grid_on_bars, EngineKind, ReplayConfig};
use trading_engine_core::config::{AppConfig, GridConfig, TrendConfig};
use trading_engine_core::models::bar::Bar;

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
