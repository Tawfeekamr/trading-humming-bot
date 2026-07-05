//! Conservative per-engine param sweep + OOS apply-gate (Phase 4).
//!
//! Task 1 surface: `Grid` (typed override list), `grid_for(kind)` (per-engine
//! conservative grid), `sweep_is(kind, rc, is_bars, bar_hours)` (run every
//! grid point on the IS slice). Task 2 will add `SweepResult` + the OOS gate.
//!
//! Compute note: the trade_journal `JOURNAL` is a process-global OnceLock that
//! caches the DB path on the first `log_unified` call. Across the many
//! `run_engine_on_bars` calls a sweep makes, later runs' `TRADES_JOURNAL_PATH`
//! `set_var` is ignored by the cached journal → writes go to a dropped earlier
//! TempDir (phantom, harmless on unix). This has NO metric impact: `Metrics`
//! derive from `port.trades` + `equity_curve`, not the journal DB. Do not
//! "fix" by adding per-call journal handles in this phase.
//!
//! Type note: the brief wrote `i64` for `ema_fast`/`min_score`, but the real
//! `TrendConfig.ema_fast: u32` and `SwingConfig.min_score: usize` — grid_for
//! uses the real types. Labels format identically either way.
use crate::backtest::replay::{run_engine_on_bars, EngineKind, ReplayConfig};
use crate::backtest::report::{compute, Metrics};
use crate::backtest::validation::{run_validation, ValidationReport};
use crate::models::bar::Bar;
use serde::Serialize;

/// One grid point: `(label, apply_override)`. The closure takes a mutable
/// `ReplayConfig` and mutates only the fields this point varies (leaving
/// everything else at the caller's template values). `FnOnce` + `Send` so the
/// (future) parallel sweep can move owns into the closure.
pub type Grid = Vec<(String, Box<dyn FnOnce(&mut ReplayConfig) + Send>)>;

/// Per-engine conservative grid (≤12 points). Grid loosens the gate for inert
/// engines (grid/swing) and varies entry/RR for trend.
///
/// Grid sizes: trend 6 (2 ema_fast × 3 rr), grid 9 (3 adx_max × 3 chop_min),
/// swing 6 (2 min_score × 3 adx_entry). MR panics — it is tick-resolution
/// (30s flush window) and was empirically no-edge in the MR-ML entry-gate
/// investigation; its faithful backtest is the separate `backtest/mean_reversion/`
/// tick-replay, not this 1h harness.
pub fn grid_for(kind: EngineKind) -> Grid {
    match kind {
        EngineKind::Trend => {
            let mut g: Grid = Vec::new();
            for &ema_fast in &[20u32, 30] {
                for &rr in &[1.5_f64, 2.0, 2.5] {
                    let label = format!("ema_fast={},rr={}", ema_fast, rr);
                    g.push((
                        label,
                        Box::new(move |rc: &mut ReplayConfig| {
                            rc.trend.ema_fast = ema_fast;
                            rc.trend.risk_reward_ratio = rr;
                        }),
                    ));
                }
            }
            g // 2*3 = 6
        }
        EngineKind::Grid => {
            let mut g: Grid = Vec::new();
            for &adx in &[22.0_f64, 25.0, 28.0] {
                for &chop in &[45.0_f64, 50.0, 55.0] {
                    let label = format!("adx_max={},chop_min={}", adx, chop);
                    g.push((
                        label,
                        Box::new(move |rc: &mut ReplayConfig| {
                            rc.grid.adx_range_max = adx;
                            rc.grid.chop_range_min = chop;
                        }),
                    ));
                }
            }
            g // 3*3 = 9
        }
        EngineKind::Swing => {
            let mut g: Grid = Vec::new();
            for &min_score in &[2usize, 3] {
                for &adx_entry in &[22.0_f64, 25.0, 28.0] {
                    let label = format!("min_score={},adx_entry={}", min_score, adx_entry);
                    g.push((
                        label,
                        Box::new(move |rc: &mut ReplayConfig| {
                            if let Some(s) = rc.swing.as_mut() {
                                s.min_score = min_score;
                                s.adx_range_entry = adx_entry;
                            }
                        }),
                    ));
                }
            }
            g // 2*3 = 6
        }
        EngineKind::MeanReversion => panic!("MR is not swept (tick-resolution, no-edge)"),
    }
}

/// Run every grid point on the IS slice; return `(label, IS Metrics)` per point.
///
/// For each grid point: clone `rc`, apply the override, force `engine = kind`
/// (the template's `engine` field may differ when the caller reuses one `rc`
/// across engines), run `run_engine_on_bars` on `is_bars`, and `compute`
/// Metrics with `bar_hours` for Sharpe annualization. Order is preserved
/// (grid point N → result N). `risk_free_per_bar = 0.0` matches the existing
/// `run_validation` convention.
pub async fn sweep_is(
    kind: EngineKind,
    rc: &ReplayConfig,
    is_bars: &[Bar],
    bar_hours: f64,
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

// ── Phase 4 Task 2: OOS apply-gate + run_sweep ─────────────────────────────

/// The decision returned by `apply_gate`: whether to apply the swept config to
/// live, plus per-check failure reasons (empty iff `apply`). `Serialize` so
/// Task 3 can emit it in the JSON artifact alongside `SweepResult`.
#[derive(Debug, Clone, Serialize)]
pub struct ApplyDecision {
    pub apply: bool,
    pub gate_reasons: Vec<String>,
}

/// The spec §6 OOS apply-gate. `baseline` = current live config's
/// `ValidationReport`, `candidate` = the IS-best config's `ValidationReport`.
/// Thresholds are VERBATIM from spec §6 — do not tune them here. The 5 checks:
///   1. candidate OOS Sharpe > baseline OOS Sharpe + 0.3 (beat by margin)
///   2. candidate OOS Sharpe > 0.0 (positive)
///   3. candidate OOS trades ≥ 15 (enough sample)
///   4. candidate OOS MaxDD ≤ baseline OOS MaxDD + 5.0 (no extra DD)
///   5. param sanity — guaranteed by grid construction (no out-of-range
///      candidate can exist); no runtime check.
/// `apply` is true ONLY when `gate_reasons` is empty (all checks pass) — this
/// invariant is the single source of truth for "should live ship this candidate".
pub fn apply_gate(baseline: &ValidationReport, candidate: &ValidationReport) -> ApplyDecision {
    let mut reasons: Vec<String> = Vec::new();
    // 1. Beat current by margin 0.3 (strict > — equal is NOT enough).
    //    Negation form so a NaN-Sharpe candidate can't slip: NaN > x is false,
    //    so !(NaN > x) is true → reason pushed.
    if !(candidate.oos.sharpe > baseline.oos.sharpe + 0.3) {
        reasons.push(format!(
            "beat current: candidate OOS Sharpe {:.2} ≤ baseline {:.2} + 0.3",
            candidate.oos.sharpe, baseline.oos.sharpe
        ));
    }
    // 2. Positive OOS (strict > — 0.0 is NOT positive). Negation form for NaN-safety.
    if !(candidate.oos.sharpe > 0.0) {
        reasons.push(format!(
            "positive: candidate OOS Sharpe {:.2} not > 0",
            candidate.oos.sharpe
        ));
    }
    // 3. Enough trades (≥ 15; strict-less-than fails). usize can't be NaN.
    if candidate.oos.total_trades < 15 {
        reasons.push(format!(
            "trades: candidate OOS trades {} < 15",
            candidate.oos.total_trades
        ));
    }
    // 4. DD tolerance vs baseline (candidate_dd ≤ baseline_dd + 5.0).
    //    Negation form so a NaN-DD candidate can't slip.
    if !(candidate.oos.max_drawdown_pct <= baseline.oos.max_drawdown_pct + 5.0) {
        reasons.push(format!(
            "drawdown: candidate OOS MaxDD {:.2}% > baseline {:.2}% + 5",
            candidate.oos.max_drawdown_pct, baseline.oos.max_drawdown_pct
        ));
    }
    // 5. param sanity: guaranteed by grid construction (no out-of-range candidate can exist).
    ApplyDecision {
        apply: reasons.is_empty(),
        gate_reasons: reasons,
    }
}

/// The full sweep + gate outcome for one engine. `candidate` and `best_label`
/// are `None` when no IS candidate cleared the ≥5-trade floor (in which case
/// `decision.apply == false` with the "no IS candidate" reason).
///
/// NOTE: the brief specified `#[derive(..., Serialize)]`. `EngineKind`
/// (in `replay.rs`) is not `Serialize` and the Phase-4 global constraint
/// forbids touching `replay.rs`. Rather than impl `Serialize` for `EngineKind`
/// out of scope, `SweepResult` is `Debug + Clone` only. `ApplyDecision` (no
/// `EngineKind`) keeps `Serialize`. Task 3, when it owns the JSON artifact,
/// can either add `Serialize` to `EngineKind` in scope or write a manual
/// serializer that emits `engine.budget_key()`. The gate's safety does not
/// depend on serialization.
#[derive(Debug, Clone)]
pub struct SweepResult {
    pub engine: EngineKind,
    pub baseline: ValidationReport,
    pub best_label: Option<String>,
    pub candidate: Option<ValidationReport>,
    pub decision: ApplyDecision,
}

/// Full sweep + apply-gate for one engine. Flow:
///   1. baseline = `run_validation(current rc)` — current live config's IS/OOS.
///   2. IS slice of the bars (no lookahead — only IS is swept).
///   3. `sweep_is` → pick best by IS Sharpe with a **≥5 IS-trades** floor
///      (rejects 1-lucky-trade flukes); `None` if no point clears.
///   4. candidate = `run_validation(best rc)` — re-validate the IS-best on
///      full/IS/OOS (the OOS slice here is the unbiased score the gate reads).
///   5. gate candidate vs baseline; return `SweepResult`.
///
/// Grid re-find note: `grid_for(kind)` is called twice (once in `sweep_is`,
/// once here to re-apply the labelled override to `cand_rc`). Grids are small
/// (≤12) and pure, and the alternative — threading the consumed `Box` out of
/// `sweep_is` — fights the borrow checker. The re-find is the clean choice.
///
/// Swing-None guard: swing grid closures no-op when `rc.swing == None`, so a
/// sweep would silently produce 6 identical runs. Bail loudly instead.
pub async fn run_sweep(
    kind: EngineKind,
    rc: &ReplayConfig,
    bars: Vec<Bar>,
    oos_frac: f64,
    bar_hours: f64,
) -> anyhow::Result<SweepResult> {
    if matches!(kind, EngineKind::Swing) && rc.swing.is_none() {
        anyhow::bail!(
            "EngineKind::Swing requires rc.swing to be Some — refusing to sweep a no-op grid"
        );
    }
    let baseline = run_validation(kind, rc, bars.clone(), oos_frac, bar_hours).await?;
    let (is_b, _oos_b) = crate::backtest::validation::split_is_oos(&bars, oos_frac);
    let swept = sweep_is(kind, rc, &is_b, bar_hours).await?;
    // IS-best by Sharpe with ≥5 IS trades (reject 1-lucky-trade flukes); else None.
    // NaN-Sharpe entries are also excluded — `max_by` would otherwise pick them
    // arbitrarily (partial_cmp(NaN, _) → None → Equal), and a NaN-Sharpe IS-best
    // is definitionally invalid even before the gate sees it.
    let best = swept
        .into_iter()
        .filter(|(_, m)| m.total_trades >= 5 && m.sharpe.is_finite())
        .max_by(|a, b| a.1.sharpe.partial_cmp(&b.1.sharpe).unwrap_or(std::cmp::Ordering::Equal));
    let (best_label, candidate, decision) = match best {
        Some((label, _is_m)) => {
            // rebuild the candidate rc by re-applying the labelled override
            let mut cand_rc = rc.clone();
            if let Some((_, apply)) = grid_for(kind).into_iter().find(|(l, _)| *l == label) {
                apply(&mut cand_rc);
            }
            cand_rc.engine = kind;
            let candidate = run_validation(kind, &cand_rc, bars.clone(), oos_frac, bar_hours).await?;
            let decision = apply_gate(&baseline, &candidate);
            (Some(label), Some(candidate), decision)
        }
        None => (
            None,
            None,
            ApplyDecision {
                apply: false,
                gate_reasons: vec!["no IS candidate with ≥5 trades".into()],
            },
        ),
    };
    Ok(SweepResult {
        engine: kind,
        baseline,
        best_label,
        candidate,
        decision,
    })
}
