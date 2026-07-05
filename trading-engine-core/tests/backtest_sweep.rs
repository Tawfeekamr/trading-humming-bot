//! Phase 4 Task 1: per-engine param grids + `sweep_is`.
//!
//! `trend_grid_yields_typed_overrides` — `grid_for` returns a small, fully-
//! labelled grid (the actual override values are exercised end-to-end in the
//! `sweep_is` test below; here we only assert the grid is small and every
//! point has a non-empty label so failures point at the grid, not the engine).
//!
//! `sweep_is_runs_each_grid_point_on_the_is_slice` — every grid point is run
//! on the IS slice via `run_engine_on_bars`; we get one `(label, Metrics)`
//! per point and every Metrics has a finite Sharpe (the synthetic ranging
//! sawtooth is hospitable enough to produce a well-defined Sharpe for all
//! trend variants).
//!
//! Type note: the brief wrote `i64` for `ema_fast`/`min_score`, but the real
//! structs are `TrendConfig.ema_fast: u32` and `SwingConfig.min_score: usize`
//! — the grid below (and `grid_for` in sweep.rs) use the real types. This
//! test does not depend on the literal integer types (labels are formatted
//! the same either way); it only checks count + label + Sharpe finiteness.
use trading_engine_core::backtest::replay::{EngineKind, ReplayConfig};
use trading_engine_core::backtest::sweep::{grid_for, sweep_is, Grid};
use trading_engine_core::config::AppConfig;
use trading_engine_core::models::bar::Bar;

fn rc() -> ReplayConfig {
    let cfg = AppConfig::load(&format!("{}/../config/strategy.yaml", env!("CARGO_MANIFEST_DIR"))).unwrap();
    ReplayConfig {
        symbol: "ETHUSDT".into(),
        init_cash: 100_000.0,
        warmup_bars: 50,
        bar_hours: 1.0,
        engine: EngineKind::Trend,
        grid: cfg.grid.clone(),
        trend: cfg.trend.clone(),
        swing: cfg.swing.clone(),
        mean_reversion: cfg.mean_reversion.clone(),
        perp_bars: None,
        funding_rate: None,
        tick_size: 0.01,
        step_size: 0.0001,
        taker_fee_bps: 10.0,
        maker_fee_bps: 10.0,
        slippage_bps: 0.0,
    }
}

#[test]
fn trend_grid_yields_typed_overrides() {
    let g: Grid = grid_for(EngineKind::Trend);
    assert!(
        g.len() >= 4 && g.len() <= 12,
        "trend grid must stay small (4-12): got {}",
        g.len()
    );
    assert!(g.iter().all(|(l, _)| !l.is_empty()), "every grid point needs a label");
}

#[tokio::test]
async fn sweep_is_runs_each_grid_point_on_the_is_slice() {
    let is_bars: Vec<Bar> = (0..200)
        .map(|i| {
            let p = 100.0 + ((i % 8) as f64 / 2.0);
            Bar::new(p, p + 1.0, p - 1.0, p, 10.0, (i as i64) * 3_600_000)
        })
        .collect();
    let results = sweep_is(EngineKind::Trend, &rc(), &is_bars, 1.0).await.unwrap();
    assert_eq!(results.len(), grid_for(EngineKind::Trend).len());
    // every result has a label + a Metrics (Sharpe is finite)
    assert!(results.iter().all(|(_, m)| m.sharpe.is_finite()));
}
