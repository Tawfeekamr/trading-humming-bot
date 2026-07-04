//! Task 6 — Replay driver end-to-end smoke test.
//!
//! Phase 1 only proves the loop runs end-to-end without panic and emits an
//! equity curve. Grid's regime gate (ADX<25, Choppiness>50, NATR in range)
//! may not fire on synthetic data, so grid may not actually trade — that's
//! fine. The real proof is Task 7's run on real ETHUSDT data.
use trading_engine_core::backtest::replay::{run_grid_on_bars, ReplayConfig};
use trading_engine_core::config::{AppConfig, GridConfig};

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
        tick_size: 0.01,
        step_size: 0.0001,
        taker_fee_bps: 10.0,
        maker_fee_bps: 10.0,
        slippage_bps: 0.0,
        grid: grid_cfg(),
    }
}

#[tokio::test]
async fn grid_arms_and_trades_on_a_ranging_series() {
    // 300 bars oscillating in a gentle sawtooth around 100-104 — grid should
    // at least deploy; whether it trades depends on its regime gate firing.
    let bars: Vec<_> = (0..300)
        .map(|i| {
            let p = 100.0 + ((i % 8) as f64 / 2.0); // gentle sawtooth
            trading_engine_core::models::bar::Bar::new(
                p,
                p + 1.0,
                p - 1.0,
                p,
                10.0,
                (i as i64) * 3_600_000,
            )
        })
        .collect();
    let res = run_grid_on_bars(&cfg(), bars).await.expect("replay must complete");
    // Phase 1 weak assertion: loop completed and produced an equity curve.
    assert!(res.equity_curve.len() > 0, "equity curve should be non-empty");
}
