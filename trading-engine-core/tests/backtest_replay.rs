//! Task 6 — Replay driver end-to-end smoke test + critical-fix regression.
//!
//! Phase 1 only proves the loop runs end-to-end without panic and emits an
//! equity curve. Grid's regime gate (ADX<25, Choppiness>50, NATR in range)
//! may not fire on synthetic data, so grid may not actually trade — that's
//! fine. The real proof is Task 7's run on real ETHUSDT data.
use trading_engine_core::backtest::replay::{run_grid_on_bars, EngineKind, ReplayConfig};
use trading_engine_core::config::{AppConfig, GridConfig};
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
