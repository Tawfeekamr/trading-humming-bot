//! Task 7 — Metrics + report: TDD test for `compute` from a known equity curve.
//!
//! Curve is monotonically rising 100 → 109 over 10 bars (last = 109, first = 100).
//! `total_return_pct = (last/first - 1) * 100 = 9.0` (NOT 10 — see brief resolution #3).
//! Monotonic rise → max drawdown is 0. No trades → win/profit-factor defaults.
use trading_engine_core::backtest::report::{compute, Metrics};
use trading_engine_core::backtest::replay::RunResult;

#[test]
fn sharpe_and_drawdown_from_known_curve() {
    // monotonically rising equity 100 -> 109 over 10 bars
    let curve: Vec<(i64, f64)> = (0..10).map(|i| (i as i64, 100.0 + i as f64)).collect();
    let run = RunResult {
        equity_curve: curve,
        trades: vec![],
        realized: 9.0,
        final_equity: 109.0,
        hodl_return_pct: 0.0,
    };
    let m: Metrics = compute(&run, 0.0, 1.0);
    // Resolution #3: (109/100 - 1)*100 = 9.0, not 10.0.
    assert!((m.total_return_pct - 9.0).abs() < 1e-6, "got {}", m.total_return_pct);
    // Monotonic rise → zero drawdown.
    assert!(m.max_drawdown_pct.abs() < 1e-6, "got {}", m.max_drawdown_pct);
    // No trades → win_rate_pct = 0, total_trades = 0, profit_factor = 0.
    assert_eq!(m.total_trades, 0);
    assert!((m.win_rate_pct - 0.0).abs() < 1e-6);
    assert!((m.profit_factor - 0.0).abs() < 1e-6);
    // hodl passes through.
    assert!((m.hodl_return_pct - 0.0).abs() < 1e-6);
    // Sharpe is positive on a monotonically rising curve (mean>0, var≈0 →
    // large positive; just sanity-bound it).
    assert!(m.sharpe >= 0.0, "sharpe should be non-negative, got {}", m.sharpe);
}
