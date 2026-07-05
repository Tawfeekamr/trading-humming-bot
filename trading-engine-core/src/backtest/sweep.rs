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
use crate::models::bar::Bar;

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
