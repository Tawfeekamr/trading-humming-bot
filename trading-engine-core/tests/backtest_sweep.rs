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
use trading_engine_core::backtest::report::Metrics;
use trading_engine_core::backtest::sweep::{apply_gate, grid_for, sweep_is, ApplyDecision, Grid};
use trading_engine_core::backtest::validation::ValidationReport;
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
    assert!(g.iter().all(|(l, _, _)| !l.is_empty()), "every grid point needs a label");
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

// ── Phase 4 Task 2: the OOS apply-gate (spec §6) ────────────────────────────
// The gate is the ONLY thing protecting the live config from a bad sweep
// winner — every branch below pins one of the 5 verbatim checks. A change in
// any constant here is a live-config safety regression.

fn vr(oos_sharpe: f64, oos_trades: usize, oos_dd: f64) -> ValidationReport {
    let m = |s: f64, t: usize, dd: f64| Metrics {
        total_return_pct: 1.0,
        sharpe: s,
        max_drawdown_pct: dd,
        win_rate_pct: 50.0,
        total_trades: t,
        profit_factor: 1.0,
        hodl_return_pct: 0.0,
    };
    ValidationReport {
        full: m(oos_sharpe, oos_trades, oos_dd),
        is_metrics: m(oos_sharpe, oos_trades, oos_dd),
        oos: m(oos_sharpe, oos_trades, oos_dd),
        is_oos_sharpe_gap: 0.0,
        overfit_suspect: false,
    }
}

#[test]
fn gate_applies_when_candidate_beats_baseline_oos_by_margin_positive_and_enough_trades() {
    // baseline oos sharpe 0.5, dd 4%; candidate oos sharpe 1.0 (>0.5+0.3), 20 trades (>=15), dd 5% (<=4+5)
    let d: ApplyDecision = apply_gate(&vr(0.5, 20, 4.0), &vr(1.0, 20, 5.0));
    assert!(d.apply);
    assert!(d.gate_reasons.is_empty(), "no failures: {:?}", d.gate_reasons);
}

#[test]
fn gate_rejects_when_candidate_does_not_beat_baseline_by_margin() {
    // candidate oos sharpe 0.7 vs baseline 0.5 → diff 0.2 < 0.3 margin
    let d = apply_gate(&vr(0.5, 20, 4.0), &vr(0.7, 20, 5.0));
    assert!(!d.apply);
    assert!(d.gate_reasons.iter().any(|r| r.contains("beat current") || r.contains("margin")));
}

#[test]
fn gate_rejects_when_candidate_oos_sharpe_not_positive() {
    let d = apply_gate(&vr(-1.0, 5, 4.0), &vr(0.0, 20, 5.0)); // baseline very negative; candidate 0 (not >0)
    assert!(!d.apply);
    assert!(d.gate_reasons.iter().any(|r| r.contains("positive")));
}

#[test]
fn gate_rejects_when_too_few_oos_trades() {
    let d = apply_gate(&vr(-1.0, 5, 4.0), &vr(2.0, 10, 5.0)); // 10 < 15
    assert!(!d.apply);
    assert!(d.gate_reasons.iter().any(|r| r.contains("trade") || r.contains("15")));
}

#[test]
fn gate_rejects_when_drawdown_exceeds_tolerance() {
    // baseline dd 4%, candidate dd 12% → 12 > 4+5=9
    let d = apply_gate(&vr(-1.0, 30, 4.0), &vr(2.0, 30, 12.0));
    assert!(!d.apply);
    assert!(d.gate_reasons.iter().any(|r| r.contains("drawdown") || r.contains("DD")));
}

#[test]
fn gate_rejects_nan_sharpe_candidate_instead_of_slipping() {
    // candidate oos sharpe = NaN (degenerate), 20 trades (>=15), dd 5% (within tolerance).
    // Under the old direct-comparison code, NaN comparisons are all false → no reason → APPLY (BUG).
    // Under the negation fix, !(NaN > 0.5+0.3) == !false == true → reason pushed → KEEP.
    let baseline = vr(0.5, 20, 4.0);
    let mut cand = vr(0.5, 20, 5.0);
    cand.oos.sharpe = f64::NAN;
    let d = apply_gate(&baseline, &cand);
    assert!(!d.apply, "NaN-Sharpe candidate must NOT slip the gate");
    assert!(!d.gate_reasons.is_empty(), "NaN-Sharpe must produce a gate reason");
}

// ── Phase 4 Task 3: write_sweep_report (JSON artifact + APPLY/KEEP markdown) ─

use trading_engine_core::backtest::report::write_sweep_report;
use trading_engine_core::backtest::sweep::SweepResult;
use tempfile::TempDir;

#[test]
fn sweep_report_markdown_has_decision_reasons_and_gaps() {
    let baseline = vr(0.5, 20, 4.0);
    let candidate = vr(0.7, 20, 5.0); // does NOT beat by 0.3 margin → KEEP
    let rep = SweepResult {
        engine: EngineKind::Trend, baseline, best_label: Some("ema_fast=20,rr=2.0".into()),
        candidate: Some(candidate),
        decision: ApplyDecision { apply: false, gate_reasons: vec!["beat current: candidate OOS Sharpe 0.70 ≤ baseline 0.50 + 0.3".into()] },
        param_deltas: vec![("ema_fast".into(), "20".into()), ("rr".into(), "2.0".into())],
    };
    let tmp = TempDir::new().unwrap();
    write_sweep_report(tmp.path(), "ETHUSDT", &rep).unwrap();
    let md = std::fs::read_to_string(tmp.path().join("ETHUSDT_trend_sweep.md")).unwrap();
    assert!(md.contains("KEEP") || md.contains("Apply") || md.contains("apply"));
    assert!(md.contains("beat current") || md.contains("margin"));     // the reason
    assert!(md.contains("ema_fast=20,rr=2.0"));                        // the candidate label
    assert!(md.contains("fidelity") || md.contains("regime"));         // gap stamps
    assert!(tmp.path().join("ETHUSDT_trend_sweep.json").exists());
}

// ── Phase 5 Task 1: param_deltas contract on Grid + SweepResult ─────────────
// Phase 5's Python `apply_sweep.py` reads structured param values from
// `param_deltas` instead of parsing the human-readable `best_label`. The grid
// closure is still the apply source of truth — `param_deltas` is metadata
// shipped alongside it for the JSON consumer.

#[test]
fn grid_yields_structured_param_deltas_matching_labels() {
    let g = grid_for(trading_engine_core::backtest::replay::EngineKind::Trend);
    // every grid point carries non-empty param_deltas
    assert!(g.iter().all(|(_, d, _)| !d.is_empty()));
    // the deltas' values reconstruct the label (e.g. deltas
    // [("ema_fast","20"),("rr","2.0")] → label "ema_fast=20,rr=2.0")
    for (label, deltas, _) in &g {
        let reconstructed: String = deltas
            .iter()
            .map(|(k, v)| format!("{}={}", k, v))
            .collect::<Vec<_>>()
            .join(",");
        assert_eq!(
            label, &reconstructed,
            "label must match param_deltas (Phase-5 Python parses neither — \
             it reads param_deltas directly; the label is for humans)"
        );
    }
}

#[tokio::test]
async fn sweep_result_carries_best_arm_param_deltas() {
    // run a tiny sweep where trend's IS-best is well-defined, then assert
    // param_deltas is non-empty and matches one of the grid's arms.
    let rc = rc();
    let bars: Vec<trading_engine_core::models::bar::Bar> = (0..300)
        .map(|i| {
            let p = 100.0 + ((i % 8) as f64 / 2.0);
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
    let res = trading_engine_core::backtest::sweep::run_sweep(
        trading_engine_core::backtest::replay::EngineKind::Trend,
        &rc,
        bars,
        1.0 / 3.0,
        1.0,
    )
    .await
    .unwrap();
    // param_deltas is non-empty iff there was an IS candidate with ≥5 trades;
    // on this sawtooth trend trades, so assert non-empty + that it's one of the
    // declared grid arms.
    if res.best_label.is_some() {
        assert!(
            !res.param_deltas.is_empty(),
            "param_deltas must be populated when there's a best arm"
        );
        let grid = grid_for(trading_engine_core::backtest::replay::EngineKind::Trend);
        let arms: Vec<_> = grid.into_iter().map(|(_, d, _)| d).collect();
        assert!(
            arms.contains(&res.param_deltas),
            "param_deltas must equal a declared grid arm"
        );
    }
}
