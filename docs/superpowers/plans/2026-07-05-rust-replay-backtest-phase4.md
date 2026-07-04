# Rust Replay Backtest — Phase 4 Implementation Plan (param sweep + OOS apply-gate)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a conservative param sweep that, for each 1h engine, searches a small grid on the IS slice, validates the best candidate on OOS, and applies the **OOS apply-gate** (spec §6) — only shipping a config change if it beats the current live config out-of-sample by a margin, with positive OOS Sharpe, enough trades, and bounded drawdown. This is the safety-critical phase: a gate bug can ship bad params to live config.

**Architecture:** A new `backtest/sweep.rs` defines per-engine grids (typed closures that override one `ReplayConfig` field), runs each grid point on the IS slice only (cheap), picks the best by IS Sharpe (with a min-trade sanity floor), runs the winner's full IS/OOS validation, and applies the 5-check gate vs. the current live config's OOS metrics. `report.rs` gains `write_sweep_report` (results.json + markdown). The CLI gains a `--sweep` flag. No engine changes; MR excluded (tick-resolution).

**Tech Stack:** Rust (existing `trading-engine-core` crate); reuses Phase 1-3 `run_engine_on_bars`, `split_is_oos`, `run_validation`, `ValidationReport`, `Metrics`, `compute`. No new dependencies.

## Global Constraints

(Carried from the spec §6 + §7 + Phase 1-3. Every task inherits these. The apply-gate thresholds are **verbatim** from spec §6 — do not change them.)

- **The OOS apply-gate (spec §6, exact values):** a candidate is applied ONLY if ALL hold:
  1. `candidate.oos.sharpe > baseline.oos.sharpe + 0.3` (beat current by a margin)
  2. `candidate.oos.sharpe > 0.0` (positive OOS)
  3. `candidate.oos.total_trades >= 15` (statistical sanity — reject 1-lucky-trade winners)
  4. `candidate.oos.max_drawdown_pct <= baseline.oos.max_drawdown_pct + 5.0` (DD tolerance vs current)
  5. param sanity: all swept values within the grid's declared ranges (always true by construction — the grid defines the ranges; no out-of-range candidate can exist)
  Else → decision `Keep`, with the failed-check names in `gate_reasons`.
- **Engines run verbatim.** No engine/trait/portfolio/fills changes. Only `backtest/sweep.rs` (new), `backtest/validation.rs` (+`Clone`), `backtest/report.rs` (+Clone + write_sweep_report), the bin, and tests change.
- **No lookahead / isolation** — each sweep point is a `run_engine_on_bars` call (own TempDir + warmup); the IS-sweep runs on the IS slice only (no OOS leakage during tuning); only the single IS-best is validated on OOS. The `TRADES_JOURNAL_PATH` OnceLock caches across runs (Phase-3 review I2) — **no metric impact** (Metrics come from `port.trades`/`equity_curve`, not the journal DB); add a code comment, do not attempt a per-call journal handle in this phase.
- **Conservative grids (spec §5.8 + the MR call-count lesson):** ≤ ~9-12 configs per engine, few params, wide steps. De-overfit, not exhaustive. Grids tuned to each engine's known issue: grid/swing are *inert* (gate never fires) → grids loosen the regime/score gates; trend is *active but loses* → grids vary entry/RR/EMA.
- **Per-engine policy (spec §7):** grid + trend + swing are sweep candidates; **MR is NOT swept** (disabled, tick-resolution, no-edge). The sweep covers grid/trend/swing only.
- Run cargo with `--manifest-path trading-engine-core/Cargo.toml`. Frequent commits, one per task. Branch: `feat/rust-replay-backtest`.

---

## File Structure (Phase 4)

| File | Phase 4 change |
|---|---|
| `backtest/sweep.rs` (new) | per-engine grids (`trend_grid`/`grid_grid`/`swing_grid`), `sweep_is`, `ApplyDecision`, `SweepResult`, `run_sweep`, the apply-gate |
| `backtest/mod.rs` | `pub mod sweep;` |
| `backtest/validation.rs` | add `Clone` to `ValidationReport` (needed so `SweepResult` can hold baseline + candidate) |
| `backtest/report.rs` | add `Clone` to `Metrics`; `write_sweep_report` (results.json + markdown) |
| `bin/backtest_replay.rs` | `--sweep` flag → `run_sweep`, write report |
| `tests/backtest_sweep.rs` (new) | grid + apply-gate unit tests |

---

## Task 1: Per-engine grids + `sweep_is` + Clone derives

**Files:**
- Create: `trading-engine-core/src/backtest/sweep.rs`
- Modify: `trading-engine-core/src/backtest/mod.rs` (add `pub mod sweep;`)
- Modify: `trading-engine-core/src/backtest/validation.rs` (add `Clone` to `ValidationReport` derive)
- Modify: `trading-engine-core/src/backtest/report.rs` (add `Clone` to `Metrics` derive)
- Test: `trading-engine-core/tests/backtest_sweep.rs`

**Interfaces:**
- Consumes: `ReplayConfig` (Phase 2), `EngineKind`, `run_engine_on_bars(kind, rc, bars) -> Result<RunResult>` (Phase 2), `report::compute(run, rf, bar_hours) -> Metrics` (Phase 1), `split_is_oos` (Phase 3 Task 1).
- Produces:
  - `pub type Grid = Vec<(String, Box<dyn FnOnce(&mut ReplayConfig) + Send>)>` — each entry is `(label, apply_override)`.
  - `pub fn grid_for(kind: EngineKind) -> Grid` — returns the per-engine grid (trend/grid/swing); panics on MR (not swept).
  - `pub async fn sweep_is(kind: EngineKind, rc: &ReplayConfig, is_bars: &[Bar], bar_hours: f64) -> anyhow::Result<Vec<(String, Metrics)>>` — for each grid point: clone `rc`, apply the override, run `run_engine_on_bars` on `is_bars`, `compute` Metrics. Returns `(label, is_metrics)` per point.

- [ ] **Step 1: Add `Clone` to `Metrics` and `ValidationReport`**

In `backtest/report.rs`, change `#[derive(Debug, Serialize)]` on `Metrics` → `#[derive(Debug, Clone, Serialize)]`.
In `backtest/validation.rs`, change `#[derive(Debug, Serialize)]` on `ValidationReport` → `#[derive(Debug, Clone, Serialize)]`.
(`Metrics` is all `f64`/`usize` primitives → Clone is trivial. `ValidationReport` holds three `Metrics` + two `f64`/`bool` → Clone now works once Metrics is Clone.)

- [ ] **Step 2: Write the failing test**

`tests/backtest_sweep.rs`:
```rust
use trading_engine_core::backtest::sweep::{grid_for, sweep_is, Grid};
use trading_engine_core::backtest::replay::{ReplayConfig, EngineKind};
use trading_engine_core::config::AppConfig;
use trading_engine_core::models::bar::Bar;

fn rc() -> ReplayConfig {
    let cfg = AppConfig::load(format!("{}/../config/strategy.yaml", env!("CARGO_MANIFEST_DIR"))).unwrap();
    ReplayConfig {
        symbol: "ETHUSDT".into(), init_cash: 100_000.0, warmup_bars: 50, bar_hours: 1.0,
        engine: EngineKind::Trend, grid: cfg.grid.clone(), trend: cfg.trend.clone(),
        swing: cfg.swing.clone(), mean_reversion: cfg.mean_reversion.clone(),
        perp_bars: None, funding_rate: None,
        tick_size: 0.01, step_size: 0.0001,
        taker_fee_bps: 10.0, maker_fee_bps: 10.0, slippage_bps: 0.0,
    }
}

#[test]
fn trend_grid_yields_typed_overrides() {
    let g: Grid = grid_for(EngineKind::Trend);
    assert!(g.len() >= 4 && g.len() <= 12, "trend grid must stay small (4-12): got {}", g.len());
    assert!(g.iter().all(|(l, _)| !l.is_empty()), "every grid point needs a label");
}

#[tokio::test]
async fn sweep_is_runs_each_grid_point_on_the_is_slice() {
    let is_bars: Vec<Bar> = (0..200).map(|i| {
        let p = 100.0 + ((i % 8) as f64 / 2.0);
        Bar::new(p, p + 1.0, p - 1.0, p, 10.0, (i as i64) * 3_600_000)
    }).collect();
    let results = sweep_is(EngineKind::Trend, &rc(), &is_bars, 1.0).await.unwrap();
    assert_eq!(results.len(), grid_for(EngineKind::Trend).len());
    // every result has a label + a Metrics (Sharpe is finite)
    assert!(results.iter().all(|(_, m)| m.sharpe.is_finite()));
}
```

- [ ] **Step 3: Run to verify it fails**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_sweep`
Expected: FAIL — `cannot find backtest::sweep`.

- [ ] **Step 4: Implement `sweep.rs`**

`backtest/sweep.rs`:
```rust
//! Conservative per-engine param sweep + OOS apply-gate (Phase 4).
//!
//! Compute note: the trade_journal `JOURNAL` is a process-global OnceLock that caches
//! the DB path on the first `log_unified` call. Across the many run_engine_on_bars
//! calls a sweep makes, later runs' TRADES_JOURNAL_PATH set_var is ignored by the
//! cached journal → writes go to a dropped earlier TempDir (phantom, harmless on unix).
//! This has NO metric impact: Metrics derive from port.trades + equity_curve, not the
//! journal DB. Do not "fix" by adding per-call journal handles in this phase.
use crate::backtest::replay::{run_engine_on_bars, EngineKind, ReplayConfig};
use crate::backtest::report::{compute, Metrics};
use crate::models::bar::Bar;

pub type Grid = Vec<(String, Box<dyn FnOnce(&mut ReplayConfig) + Send>)>;

/// Per-engine conservative grid (≤12 points). Grid loosens the gate for inert
/// engines (grid/swing) and varies entry/RR for trend.
pub fn grid_for(kind: EngineKind) -> Grid {
    match kind {
        EngineKind::Trend => {
            let mut g = Vec::new();
            for &ema_fast in &[20i64, 30] {
                for &rr in &[1.5_f64, 2.0, 2.5] {
                    let label = format!("ema_fast={},rr={}", ema_fast, rr);
                    g.push((label, Box::new(move |rc: &mut ReplayConfig| {
                        rc.trend.ema_fast = ema_fast;
                        rc.trend.risk_reward_ratio = rr;
                    })));
                }
            }
            g // 2*3 = 6
        }
        EngineKind::Grid => {
            let mut g = Vec::new();
            for &adx in &[22.0_f64, 25.0, 28.0] {
                for &chop in &[45.0_f64, 50.0, 55.0] {
                    let label = format!("adx_max={},chop_min={}", adx, chop);
                    g.push((label, Box::new(move |rc: &mut ReplayConfig| {
                        rc.grid.adx_range_max = adx;
                        rc.grid.chop_range_min = chop;
                    })));
                }
            }
            g // 3*3 = 9
        }
        EngineKind::Swing => {
            let mut g = Vec::new();
            for &min_score in &[2_i64, 3] {
                for &adx_entry in &[22.0_f64, 25.0, 28.0] {
                    let label = format!("min_score={},adx_entry={}", min_score, adx_entry);
                    g.push((label, Box::new(move |rc: &mut ReplayConfig| {
                        if let Some(s) = rc.swing.as_mut() { s.min_score = min_score; s.adx_range_entry = adx_entry; }
                    })));
                }
            }
            g // 2*3 = 6
        }
        EngineKind::MeanReversion => panic!("MR is not swept (tick-resolution, no-edge)"),
    }
}

/// Run every grid point on the IS slice; return (label, IS Metrics) per point.
pub async fn sweep_is(
    kind: EngineKind, rc: &ReplayConfig, is_bars: &[Bar], bar_hours: f64,
) -> anyhow::Result<Vec<(String, Metrics)>> {
    let mut out = Vec::new();
    for (label, apply) in grid_for(kind) {
        let mut point_rc = rc.clone();
        apply(&mut point_rc);
        point_rc.engine = kind;
        let run = run_engine_on_bars(kind, &point_rc, is_bars.to_vec()).await?;
        out.push((label, compute(&run, 0.0, bar_hours)));
    }
    Ok(out)
}
```
Add `pub mod sweep;` to `backtest/mod.rs`.

- [ ] **Step 5: Run test → GREEN**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_sweep`
Expected: PASS (2 tests). `cargo build --bin backtest_replay` clean.

- [ ] **Step 6: Commit**

```bash
git add trading-engine-core/src/backtest/{sweep.rs,mod.rs,validation.rs,report.rs} \
        trading-engine-core/tests/backtest_sweep.rs
git commit -m "feat(backtest): per-engine sweep grids + sweep_is (Phase 4 Task 1)"
```

---

## Task 2: `run_sweep` + the OOS apply-gate (CRUX)

**Files:**
- Modify: `trading-engine-core/src/backtest/sweep.rs`
- Modify: `trading-engine-core/tests/backtest_sweep.rs`

**Interfaces:**
- Consumes: `sweep_is` (Task 1), `run_validation(kind, rc, bars, oos_frac, bar_hours) -> Result<ValidationReport>` (Phase 3), `ValidationReport { full, is_metrics, oos, is_oos_sharpe_gap, overfit_suspect }`, `Metrics { sharpe, total_trades, max_drawdown_pct, ... }`.
- Produces:
  - `#[derive(Debug, Clone, Serialize)] pub struct ApplyDecision { pub apply: bool, pub gate_reasons: Vec<String> }`
  - `#[derive(Debug, Clone, Serialize)] pub struct SweepResult { pub engine: EngineKind, pub baseline: ValidationReport, pub best_label: Option<String>, pub candidate: Option<ValidationReport>, pub decision: ApplyDecision }`
  - `pub async fn run_sweep(kind: EngineKind, rc: &ReplayConfig, bars: Vec<Bar>, oos_frac: f64, bar_hours: f64) -> anyhow::Result<SweepResult>` — (1) baseline = run_validation(current rc); (2) is_b = split_is_oos IS half; (3) sweep_is → pick best by IS Sharpe with **IS trades ≥ 5** floor (else None); (4) candidate = run_validation(best rc); (5) apply the 5-check gate; (6) return SweepResult.

- [ ] **Step 1: Write the failing test (apply-gate logic is the crux — test each branch)**

Append to `tests/backtest_sweep.rs`:
```rust
use trading_engine_core::backtest::sweep::{apply_gate, ApplyDecision};
use trading_engine_core::backtest::report::Metrics;
use trading_engine_core::backtest::validation::ValidationReport;

fn vr(oos_sharpe: f64, oos_trades: usize, oos_dd: f64) -> ValidationReport {
    let m = |s: f64, t: usize, dd: f64| Metrics {
        total_return_pct: 1.0, sharpe: s, max_drawdown_pct: dd, win_rate_pct: 50.0,
        total_trades: t, profit_factor: 1.0, hodl_return_pct: 0.0,
    };
    ValidationReport { full: m(s, t, dd), is_metrics: m(s, t, dd), oos: m(oos_sharpe, oos_trades, oos_dd),
                       is_oos_sharpe_gap: 0.0, overfit_suspect: false }
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_sweep`
Expected: FAIL — `cannot find apply_gate`.

- [ ] **Step 3: Implement `apply_gate` + `ApplyDecision` + `SweepResult` + `run_sweep`**

Append to `backtest/sweep.rs`:
```rust
use crate::backtest::validation::{run_validation, ValidationReport};
use crate::backtest::replay::EngineKind;
use serde::Serialize;

#[derive(Debug, Clone, Serialize)]
pub struct ApplyDecision { pub apply: bool, pub gate_reasons: Vec<String> }

/// The spec §6 OOS apply-gate. `baseline` = current live config's ValidationReport,
/// `candidate` = the IS-best config's ValidationReport. Thresholds are VERBATIM from
/// spec §6 — do not tune them here.
pub fn apply_gate(baseline: &ValidationReport, candidate: &ValidationReport) -> ApplyDecision {
    let mut reasons: Vec<String> = Vec::new();
    // 1. Beat current by margin 0.3
    if candidate.oos.sharpe <= baseline.oos.sharpe + 0.3 {
        reasons.push(format!("beat current: candidate OOS Sharpe {:.2} ≤ baseline {:.2} + 0.3",
            candidate.oos.sharpe, baseline.oos.sharpe));
    }
    // 2. Positive OOS
    if candidate.oos.sharpe <= 0.0 {
        reasons.push(format!("positive: candidate OOS Sharpe {:.2} not > 0", candidate.oos.sharpe));
    }
    // 3. Enough trades
    if candidate.oos.total_trades < 15 {
        reasons.push(format!("trades: candidate OOS trades {} < 15", candidate.oos.total_trades));
    }
    // 4. DD tolerance vs baseline
    if candidate.oos.max_drawdown_pct > baseline.oos.max_drawdown_pct + 5.0 {
        reasons.push(format!("drawdown: candidate OOS MaxDD {:.2}% > baseline {:.2}% + 5",
            candidate.oos.max_drawdown_pct, baseline.oos.max_drawdown_pct));
    }
    // 5. param sanity: guaranteed by grid construction (no out-of-range candidate can exist)
    ApplyDecision { apply: reasons.is_empty(), gate_reasons: reasons }
}

#[derive(Debug, Clone, Serialize)]
pub struct SweepResult {
    pub engine: EngineKind,
    pub baseline: ValidationReport,
    pub best_label: Option<String>,
    pub candidate: Option<ValidationReport>,
    pub decision: ApplyDecision,
}

/// Full sweep + apply-gate for one engine. (1) baseline validation; (2) sweep IS;
/// (3) pick IS-best with a min-trade floor; (4) validate the IS-best; (5) gate it.
pub async fn run_sweep(
    kind: EngineKind, rc: &ReplayConfig, bars: Vec<Bar>, oos_frac: f64, bar_hours: f64,
) -> anyhow::Result<SweepResult> {
    let baseline = run_validation(kind, rc, bars.clone(), oos_frac, bar_hours).await?;
    let (is_b, _oos_b) = crate::backtest::validation::split_is_oos(&bars, oos_frac);
    let swept = sweep_is(kind, rc, &is_b, bar_hours).await?;
    // IS-best by Sharpe with ≥5 IS trades (reject 1-lucky-trade flukes); else no candidate.
    let best = swept.into_iter()
        .filter(|(_, m)| m.total_trades >= 5)
        .max_by(|a, b| a.1.sharpe.partial_cmp(&b.1.sharpe).unwrap_or(std::cmp::Ordering::Equal));
    let (best_label, candidate, decision) = match best {
        Some((label, _is_m)) => {
            // rebuild the candidate rc by re-applying the labelled override
            let mut cand_rc = rc.clone();
            if let Some((_, apply)) = grid_for(kind).into_iter().find(|(l, _)| l == label) {
                apply(&mut cand_rc);
            }
            cand_rc.engine = kind;
            let candidate = run_validation(kind, &cand_rc, bars.clone(), oos_frac, bar_hours).await?;
            let decision = apply_gate(&baseline, &candidate);
            (Some(label), Some(candidate), decision)
        }
        None => (None, None, ApplyDecision { apply: false, gate_reasons: vec!["no IS candidate with ≥5 trades".into()] }),
    };
    Ok(SweepResult { engine: kind, baseline, best_label, candidate, decision })
}
```
(If `grid_for(kind)` is called twice (once in `sweep_is`, once here to re-find the override), that's fine — grids are small and pure. The alternative — threading the `Box` out of `sweep_is` — fights the borrow checker; the re-find is cleaner.)

- [ ] **Step 4: Run test → GREEN (all 5 gate tests + the Task-1 tests)**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_sweep`
Expected: PASS (7 tests). `cargo build` clean.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/backtest/sweep.rs trading-engine-core/tests/backtest_sweep.rs
git commit -m "feat(backtest): run_sweep + OOS apply-gate (spec §6) — Phase 4 Task 2 crux"
```

---

## Task 3: `write_sweep_report` (results.json + markdown)

**Files:**
- Modify: `trading-engine-core/src/backtest/report.rs`
- Modify: `trading-engine-core/tests/backtest_sweep.rs`

**Interfaces:**
- Consumes: `SweepResult` (Task 2).
- Produces: `pub fn write_sweep_report(dir: &Path, symbol: &str, rep: &SweepResult) -> Result<()>` — writes `<dir>/<SYMBOL>_<engine>_sweep.json` (the SweepResult serialized) + `<dir>/<SYMBOL>_<engine>_sweep.md` (baseline vs candidate OOS table + the decision APPLY/KEEP + gate_reasons + the fidelity-gap stamps).

- [ ] **Step 1: Write the failing test**

Append to `tests/backtest_sweep.rs`:
```rust
use trading_engine_core::backtest::report::write_sweep_report;
use trading_engine_core::backtest::sweep::{SweepResult, ApplyDecision};
use tempfile::TempDir;

#[test]
fn sweep_report_markdown_has_decision_reasons_and_gaps() {
    let baseline = vr(0.5, 20, 4.0);
    let candidate = vr(0.7, 20, 5.0); // does NOT beat by 0.3 margin → KEEP
    let rep = SweepResult {
        engine: EngineKind::Trend, baseline, best_label: Some("ema_fast=20,rr=2.0".into()),
        candidate: Some(candidate),
        decision: ApplyDecision { apply: false, gate_reasons: vec!["beat current: candidate OOS Sharpe 0.70 ≤ baseline 0.50 + 0.3".into()] },
    };
    let tmp = TempDir::new().unwrap();
    write_sweep_report(tmp.path(), "ETHUSDT", &rep).unwrap();
    let md = std::fs::read_to_string(tmp.path().join("ETHUSDT_trend_sweep.md")).unwrap();
    assert!(md.contains("KEEP") || md.contains("Apply").or(Some(md.contains("apply"))).unwrap_or(false));
    assert!(md.contains("beat current") || md.contains("margin"));     // the reason
    assert!(md.contains("ema_fast=20,rr=2.0"));                        // the candidate label
    assert!(md.contains("fidelity") || md.contains("regime"));         // gap stamps
    assert!(tmp.path().join("ETHUSDT_trend_sweep.json").exists());
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_sweep`
Expected: FAIL — `cannot find write_sweep_report`.

- [ ] **Step 3: Implement `write_sweep_report`**

Append to `backtest/report.rs` (add `use crate::backtest::sweep::SweepResult;` to the imports):
```rust
pub fn write_sweep_report(dir: &Path, symbol: &str, rep: &SweepResult) -> Result<()> {
    std::fs::create_dir_all(dir)?;
    let json = serde_json::to_string_pretty(rep)?;
    std::fs::write(dir.join(format!("{}_{}_sweep.json", symbol, rep.engine.budget_key())), json)?;
    let decision_str = if rep.decision.apply { "**APPLY**" } else { "**KEEP**" };
    let cand_block = match (&rep.best_label, &rep.candidate) {
        (Some(label), Some(c)) => format!(
            "| Candidate ({}) | {:.2}% | {:.2} | {:.2}% | {} | OOS Sharpe {:.2}, {} trades |\n",
            label, c.oos.total_return_pct, c.oos.sharpe, c.oos.max_drawdown_pct, c.oos.win_rate_pct,
            c.oos.sharpe, c.oos.total_trades),
        _ => String::from("| No candidate (none had ≥5 IS trades) | — | — | — | — | — |\n"),
    };
    let reasons = if rep.decision.gate_reasons.is_empty() { "_(all 5 gate checks passed)_".to_string() }
                  else { rep.decision.gate_reasons.iter().map(|r| format!("- {}", r)).collect::<Vec<_>>().join("\n") };
    let md = format!(
        "# Sweep: {} {} (IS-tune → OOS-gate)\n\n\
         Decision: {}\n\n\
         | Config | Return | Sharpe | MaxDD | Win | Notes |\n|---|---|---|---|---|---|\n\
         | Baseline (live) | {:.2}% | {:.2} | {:.2}% | {:.0}% | current deployed config |\n\
         {}\n\n\
         ## Gate reasons\n{}\n\n\
         ## Fidelity gaps\n\
         - regime=None (grid/trend ML gate off — optimistic).\n\
         - perp-as-spot-proxy + flat funding (trend-short MTM approximate).\n\
         - MR not swept (tick-resolution; separate tick-replay backtest).\n\
         - trade_journal OnceLock caches across sweep runs — no metric impact (Metrics come from in-memory P&L, not the DB).\n",
        symbol, rep.engine.budget_key(), decision_str,
        rep.baseline.oos.total_return_pct, rep.baseline.oos.sharpe, rep.baseline.oos.max_drawdown_pct, rep.baseline.oos.win_rate_pct,
        cand_block, reasons);
    std::fs::write(dir.join(format!("{}_{}_sweep.md", symbol, rep.engine.budget_key())), md)?;
    Ok(())
}
```

- [ ] **Step 4: Run test → GREEN**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_sweep`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/backtest/report.rs trading-engine-core/tests/backtest_sweep.rs
git commit -m "feat(backtest): write_sweep_report — results.json + APPLY/KEEP markdown (Phase 4 Task 3)"
```

---

## Task 4: CLI `--sweep` flag

**Files:**
- Modify: `trading-engine-core/src/bin/backtest_replay.rs`

**Interfaces:**
- Produces: a `--sweep` CLI flag that, for the selected `--engine`, fetches bars, calls `run_sweep`, writes the sweep report, prints the headline (decision + best_label + baseline/candidate OOS Sharpe + gate_reasons).

- [ ] **Step 1: Add the `--sweep` flag + branch**

In `bin/backtest_replay.rs`: add `sweep: bool` parsed in the same args loop as `--engine`/`--config`/`--validate`. After bars + rc + kind are built, branch (alongside the existing `--validate` branch):
```rust
if sweep {
    let rep = trading_engine_core::backtest::sweep::run_sweep(kind, &rc, bars, 1.0/3.0, bar_hours).await?;
    let cand_sharpe = rep.candidate.as_ref().map(|c| c.oos.sharpe).unwrap_or(0.0);
    println!("sweep engine={:?} decision={} best={} baseline_oos_sharpe={:.2} candidate_oos_sharpe={:.2} reasons={:?}",
        kind, if rep.decision.apply {"APPLY"} else {"KEEP"}, rep.best_label.as_deref().unwrap_or("none"),
        rep.baseline.oos.sharpe, cand_sharpe, rep.decision.gate_reasons);
    trading_engine_core::backtest::report::write_sweep_report(
        std::path::Path::new("backtest/results/replay"), &pair, &rep)?;
} else if validate {
    // existing --validate branch (unchanged)
} else {
    // existing single-run path (unchanged)
}
```

- [ ] **Step 2: Build**

Run: `cargo build --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add trading-engine-core/src/bin/backtest_replay.rs
git commit -m "feat(backtest): --sweep CLI flag (IS-tune → OOS-gate per engine) — Phase 4 Task 4"
```

---

## Task 5: Real-data sweep smoke (trend; the active engine)

**Files:** none (manual smoke + report)

- [ ] **Step 1: Run the sweep for trend on a meaningful window**

From the repo ROOT:
```
cargo run --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay -- ETHUSDT 12 trend --sweep
```
This sweeps the 6 trend configs on the IS slice (~8mo), validates the IS-best on OOS, runs the apply-gate vs the current trend config's OOS. Prints the decision headline + writes `backtest/results/replay/ETHUSDT_trend_sweep.{json,md}`.

- [ ] **Step 2: Optionally run grid + swing sweeps**

```
cargo run --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay -- ETHUSDT 12 grid --sweep
cargo run --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay -- ETHUSDT 12 swing --sweep
```
(These will likely KEEP — grid/swing are inert, so no candidate will have ≥5 IS trades → "no IS candidate". That's the gate correctly refusing to tune an engine that doesn't trade. Report it honestly.)

- [ ] **Step 3: Record the results**

Write `/Users/amro/WebstormProjects/trading-humming-bot/.superpowers/sdd/task-5-report.md` (Phase 4): per-engine decision (APPLY/KEEP), the best_label, baseline vs candidate OOS Sharpe, the gate_reasons. Note: given Phase 3's baseline (no positive IS edge), the expected outcome is KEEP for most/all engines — the gate protecting live config, which is the correct conservative behavior.

- [ ] **Step 4: Commit (only if the smoke revealed a harness bug; otherwise the deliverable is the recorded results)**

---

## Phase 4 Exit Criteria

- All `cargo test --test backtest_*` pass (sweep grid + the 5 apply-gate branch tests + write_sweep_report + the Phase 1-3 suite).
- `cargo run --bin backtest_replay -- ETHUSDT 12 <engine> --sweep` runs clean for trend (and grid/swing) and writes the sweep report.
- The apply-gate verbatim implements spec §6's 5 checks (the unit tests pin each branch).
- No engine modified; MR not swept; no lookahead (IS-only tuning, OOS-only validation of the single IS-best).

Once Phase 4 is green, write the Phase 5 plan (extend `apply_sweep.py` to consume `*_sweep.json` + write `config/strategy.yaml`; the `backtest-rust.yml` workflow with weekly cron + Telegram; retire `sweep.yml`).

---

## Self-Review

1. **Spec coverage:** §5.8 param grids → Task 1; §6 apply-gate (5 checks, verbatim) → Task 2; §7 per-engine policy (grid/trend/swing swept, MR not) → Task 1 grid_for + Task 2; results.json → Task 3; CLI → Task 4; baseline-for-live → Task 5. ✅
2. **Placeholder scan:** Task 5 Step 4 is conditional (no-code smoke) with a specified deliverable. Grid sizes are concrete (6/9/6). No bare TODOs. The OnceLock note is a documented limitation, not a placeholder.
3. **Type consistency:** `Grid = Vec<(String, Box<dyn FnOnce(&mut ReplayConfig) + Send>)>` (Task 1) used by `sweep_is` (Task 1) and re-found in `run_sweep` (Task 2). `ApplyDecision { apply, gate_reasons }` + `SweepResult { engine, baseline, best_label, candidate, decision }` (Task 2) consumed by `write_sweep_report` (Task 3) + the CLI (Task 4). `apply_gate(baseline, candidate)` signature matches the Task-2 tests. `Metrics`/`ValidationReport` `Clone` (Task 1 Step 1) unblocks `SweepResult` Clone. `kind.budget_key()` used for sweep filenames (matches Phase 2-3).
