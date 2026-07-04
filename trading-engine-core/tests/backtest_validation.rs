use trading_engine_core::backtest::validation::{split_is_oos, run_validation, ValidationReport};
use trading_engine_core::backtest::replay::{ReplayConfig, EngineKind};
use trading_engine_core::config::AppConfig;
use trading_engine_core::models::bar::Bar;

fn bars(n: usize) -> Vec<Bar> {
    (0..n).map(|i| Bar::new(100.0, 101.0, 99.0, 100.0, 1.0, i as i64 * 3_600_000)).collect()
}

fn rc_for(grid_cfg: trading_engine_core::config::GridConfig) -> ReplayConfig {
    ReplayConfig {
        symbol: "ETHUSDT".into(), init_cash: 100_000.0, warmup_bars: 50, bar_hours: 1.0,
        engine: EngineKind::Grid, grid: grid_cfg,
        trend: trading_engine_core::config::TrendConfig::default(),
        swing: None,
        mean_reversion: trading_engine_core::config::MeanReversionConfig::default(),
        perp_bars: None, funding_rate: None,
        tick_size: 0.01, step_size: 0.0001,
        taker_fee_bps: 10.0, maker_fee_bps: 10.0, slippage_bps: 0.0,
    }
}

#[tokio::test]
async fn run_validation_produces_three_metric_sets_and_gap() {
    let cfg = AppConfig::load(&format!("{}/../config/strategy.yaml", env!("CARGO_MANIFEST_DIR"))).unwrap();
    let rc = rc_for(cfg.grid.clone());
    // 300-bar ranging sawtooth — same generator the grid smoke uses (trades on hospitable data).
    let bars: Vec<_> = (0..300).map(|i| {
        let p = 100.0 + ((i % 8) as f64 / 2.0);
        Bar::new(p, p + 1.0, p - 1.0, p, 10.0, (i as i64) * 3_600_000)
    }).collect();
    let rep: ValidationReport = run_validation(EngineKind::Grid, &rc, bars, 1.0/3.0, 1.0).await.unwrap();
    // all three slices produce a Metrics (full/IS/OOS); gap is IS_sharpe - OOS_sharpe
    assert!((rep.is_oos_sharpe_gap - (rep.is_metrics.sharpe - rep.oos.sharpe)).abs() < 1e-9);
    // overfit flag is the gap > 1.0 test
    assert_eq!(rep.overfit_suspect, rep.is_oos_sharpe_gap > 1.0);
    // Slices genuinely differ: full saw ≥ the bars of either half; IS (more live bars
    // after warmup) and OOS trade counts differ. Catches a slice-collapse regression
    // (passing `bars` to all three run_engine_on_bars calls) that the gap/overfit
    // tautologies above cannot detect.
    assert!(rep.full.total_trades >= rep.is_metrics.total_trades);
    assert!(rep.full.total_trades >= rep.oos.total_trades);
    assert_ne!(rep.is_metrics.total_trades, rep.oos.total_trades);
}

#[test]
fn split_is_two_thirds_one_third_contiguous_no_overlap() {
    let b = bars(300);
    let (is_b, oos_b) = split_is_oos(&b, 1.0 / 3.0);
    assert_eq!(is_b.len(), 200);
    assert_eq!(oos_b.len(), 100);
    // contiguous: IS ends where OOS begins
    assert_eq!(is_b.last().unwrap().timestamp, 199 * 3_600_000);
    assert_eq!(oos_b.first().unwrap().timestamp, 200 * 3_600_000);
    assert!(oos_b.last().unwrap().timestamp > is_b.last().unwrap().timestamp);
}

#[test]
fn split_empty_input_returns_two_empty_vecs() {
    let (is_b, oos_b) = split_is_oos(&[], 1.0 / 3.0);
    assert!(is_b.is_empty() && oos_b.is_empty());
}
