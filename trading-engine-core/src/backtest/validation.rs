//! In-sample / out-of-sample validation for the 1h engines.
use anyhow::Result;
use serde::Serialize;

use crate::backtest::replay::{run_engine_on_bars, EngineKind, ReplayConfig};
use crate::backtest::report::{compute, Metrics};
use crate::models::bar::Bar;

/// Contiguous IS/OOS split. IS = first (1 - oos_frac) of bars, OOS = the rest.
/// Empty input → two empty vecs. No shared bars.
pub fn split_is_oos(bars: &[Bar], oos_frac: f64) -> (Vec<Bar>, Vec<Bar>) {
    if bars.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let split = (bars.len() as f64 * (1.0 - oos_frac.clamp(0.0, 1.0))).round() as usize;
    let split = split.min(bars.len());
    let (is_b, oos_b) = bars.split_at(split);
    (is_b.to_vec(), oos_b.to_vec())
}

/// Per-slice metrics + the IS→OOS Sharpe-gap overfit flag. `Serialize` for
/// Task 3's JSON artifact. (`Clone` omitted: `Metrics` isn't `Clone` and the
/// global constraint forbids changing `report.rs`. Add `Clone` to both when
/// Task 3 / a later refactor permits it — the test does not require it.)
#[derive(Debug, Serialize)]
pub struct ValidationReport {
    /// Full-sample metrics (re-run end-to-end on the entire bar stream).
    pub full: Metrics,
    /// In-sample metrics (first (1 - oos_frac) of bars).
    pub is_metrics: Metrics,
    /// Out-of-sample metrics (last oos_frac of bars).
    pub oos: Metrics,
    /// `is_metrics.sharpe - oos.sharpe`. Positive ⇒ IS performance did not
    /// survive OOS; large positive values are the classic overfit signature.
    pub is_oos_sharpe_gap: f64,
    /// IS→OOS Sharpe gap > 1.0 ⇒ suspected overfit (matches the MR
    /// tick-replay convention used in the existing tuning workflow).
    pub overfit_suspect: bool,
}

/// Run the engine's current live config on full / IS / OOS slices and return
/// per-slice `Metrics` + the IS→OOS Sharpe gap. Each slice is a self-contained
/// `run_engine_on_bars` call (own TempDir + trade-journal isolation, own
/// warmup) — no cross-slice bar or state leakage. `bar_hours` is forwarded to
/// `report::compute` for correct Sharpe annualization.
pub async fn run_validation(
    kind: EngineKind,
    rc: &ReplayConfig,
    bars: Vec<Bar>,
    oos_frac: f64,
    bar_hours: f64,
) -> Result<ValidationReport> {
    let (is_b, oos_b) = split_is_oos(&bars, oos_frac);
    let full_run = run_engine_on_bars(kind, rc, bars).await?;
    let is_run = run_engine_on_bars(kind, rc, is_b).await?;
    let oos_run = run_engine_on_bars(kind, rc, oos_b).await?;
    let full = compute(&full_run, 0.0, bar_hours);
    let is_metrics = compute(&is_run, 0.0, bar_hours);
    let oos = compute(&oos_run, 0.0, bar_hours);
    let gap = is_metrics.sharpe - oos.sharpe;
    Ok(ValidationReport {
        full,
        is_metrics,
        oos,
        is_oos_sharpe_gap: gap,
        overfit_suspect: gap > 1.0,
    })
}
