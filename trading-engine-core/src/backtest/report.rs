//! Metrics + JSON/markdown report from a `RunResult`.
//!
//! `compute` derives standard backtest metrics from the equity curve and
//! trade journal: total return, annualized Sharpe (1h bars → ×sqrt(24*365)),
//! max drawdown (peak-to-trough %), win rate, profit factor, HODL benchmark.
//! `write_report` emits `<SYMBOL>_results.json` (structured) + `report.md`
//! (human) into a directory.
use std::path::Path;

use anyhow::Result;
use serde::Serialize;

use super::replay::{EngineKind, RunResult};
use super::sweep::SweepResult;
use super::validation::ValidationReport;

#[derive(Debug, Clone, Serialize)]
pub struct Metrics {
    pub total_return_pct: f64,
    pub sharpe: f64,
    pub max_drawdown_pct: f64,
    pub win_rate_pct: f64,
    pub total_trades: usize,
    pub profit_factor: f64,
    pub hodl_return_pct: f64,
}

/// Compute metrics from a `RunResult`. `risk_free_per_bar` is reserved for a
/// future risk-free-rate adjustment to the Sharpe numerator; currently unused
/// (the strategies are short-lived and the adjustment is negligible vs the
/// annualization factor). `bar_hours` is the bar interval in hours (1h bars
/// → ×sqrt(24*365) annualization); parametrized so non-1h engines pass their
/// own interval.
pub fn compute(run: &RunResult, _risk_free_per_bar: f64, bar_hours: f64) -> Metrics {
    let eq: Vec<f64> = run.equity_curve.iter().map(|(_, e)| *e).collect();
    let first = eq.first().copied().unwrap_or(0.0);
    let last = eq.last().copied().unwrap_or(0.0);
    let total_return_pct = if first > 0.0 { (last / first - 1.0) * 100.0 } else { 0.0 };

    // Per-bar returns → Sharpe (annualized × sqrt(bars_per_year); 1h bars
    // → bars_per_year = 24*365, reproducing the previous hard-coded factor).
    let mut rets: Vec<f64> = Vec::with_capacity(eq.len().saturating_sub(1));
    for w in eq.windows(2) {
        if w[0] > 0.0 {
            rets.push(w[1] / w[0] - 1.0);
        }
    }
    let n = rets.len().max(1);
    let mean = rets.iter().sum::<f64>() / n as f64;
    let var = rets.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / n as f64;
    let std: f64 = var.sqrt();
    let bars_per_year = (24.0 / bar_hours) * 365.0;
    let sharpe = if std > 0.0 { mean / std * bars_per_year.sqrt() } else { 0.0 };

    // Max drawdown (% peak-to-trough). Monotonic rise → 0.
    let mut peak = f64::NEG_INFINITY;
    let mut max_dd: f64 = 0.0;
    for e in &eq {
        peak = peak.max(*e);
        max_dd = max_dd.max((peak - *e) / peak.max(1e-9) * 100.0);
    }

    let wins = run.trades.iter().filter(|t| t.pnl > 0.0).count();
    let win_rate_pct = if run.trades.is_empty() {
        0.0
    } else {
        wins as f64 / run.trades.len() as f64 * 100.0
    };
    let gross_win = run.trades.iter().filter(|t| t.pnl > 0.0).map(|t| t.pnl).sum::<f64>();
    let gross_loss = (-run.trades.iter().filter(|t| t.pnl < 0.0).map(|t| t.pnl).sum::<f64>()).max(0.0);
    let profit_factor = if gross_loss > 0.0 {
        gross_win / gross_loss
    } else if gross_win > 0.0 {
        f64::INFINITY
    } else {
        0.0
    };

    Metrics {
        total_return_pct,
        sharpe,
        max_drawdown_pct: max_dd,
        win_rate_pct,
        total_trades: run.trades.len(),
        profit_factor,
        hodl_return_pct: run.hodl_return_pct,
    }
}

/// Write `<dir>/<SYMBOL>_results.json` (pretty JSON of `Metrics`) and
/// `<dir>/report.md` (human-readable summary). Creates `dir` if missing.
pub fn write_report(dir: &Path, symbol: &str, _run: &RunResult, m: &Metrics) -> Result<()> {
    std::fs::create_dir_all(dir)?;
    let json = serde_json::to_string_pretty(m)?;
    std::fs::write(dir.join(format!("{}_results.json", symbol)), json)?;
    let md = format!(
        "# Backtest: {}\n\n\
         - Return: {:.2}%\n\
         - Sharpe: {:.2}\n\
         - MaxDD: {:.2}%\n\
         - Win: {:.0}%\n\
         - Trades: {}\n\
         - HODL: {:.2}%\n",
        symbol,
        m.total_return_pct,
        m.sharpe,
        m.max_drawdown_pct,
        m.win_rate_pct,
        m.total_trades,
        m.hodl_return_pct,
    );
    std::fs::write(dir.join("report.md"), md)?;
    Ok(())
}

/// Write the IS/OOS validation artifacts:
/// - `<dir>/<SYMBOL>_<engine>_validation.json` — pretty-printed `ValidationReport`.
/// - `<dir>/validation_report.md` — human-readable IS/OOS table, the IS→OOS
///   Sharpe gap with the overfit flag, and the three fidelity-gap stamps that
///   apply uniformly to every metric in the table (regime gate off,
///   perp-as-spot-proxy + flat funding for trend shorts, MR excluded from this
///   1h harness). Creates `dir` if missing.
pub fn write_validation_report(
    dir: &Path,
    symbol: &str,
    kind: EngineKind,
    rep: &ValidationReport,
) -> Result<()> {
    std::fs::create_dir_all(dir)?;
    let json = serde_json::to_string_pretty(rep)?;
    std::fs::write(
        dir.join(format!("{}_{}_validation.json", symbol, kind.budget_key())),
        json,
    )?;

    let row = |label: &str, m: &Metrics| {
        format!(
            "| {} | {:.2}% | {:.2} | {:.2}% | {:.0}% | {} |",
            label, m.total_return_pct, m.sharpe, m.max_drawdown_pct, m.win_rate_pct, m.total_trades
        )
    };
    let flag = if rep.overfit_suspect {
        "⚠️ Overfit suspected"
    } else {
        "✅ no overfit flag"
    };
    let md = format!(
        "# Validation: {} {} (IS/OOS)\n\n\
         | Slice | Return | Sharpe | MaxDD | Win | Trades |\n\
         |---|---|---|---|---|---|\n\
         {}\n\
         {}\n\
         {}\n\n\
         - IS→OOS Sharpe gap: **{:.2}** → {}\n\n\
         ## Fidelity gaps (apply to every metric above)\n\
         - **regime=None** — grid/trend ML regime gate is OFF (optimistic; live uses ML regime).\n\
         - **perp-as-spot-proxy + flat funding** — trend-short MTM uses spot≈perp, funding accrual = 0 (trend shorts approximate).\n\
         - **MR excluded** — MR is tick-resolution (30s flush window); its faithful backtest is the separate `backtest/mean_reversion/` tick-replay, not this 1h harness.\n",
        symbol,
        kind.budget_key(),
        row("Full", &rep.full),
        row("IS", &rep.is_metrics),
        row("OOS", &rep.oos),
        rep.is_oos_sharpe_gap,
        flag
    );
    std::fs::write(dir.join(format!("{}_{}_validation.md", symbol, kind.budget_key())), md)?;
    Ok(())
}

/// Write the Phase-4 sweep artifacts:
/// - `<dir>/<SYMBOL>_<engine>_sweep.json` — pretty-printed `SweepResult` (the
///   structured artifact Phase 5's auto-apply consumes: baseline + best_label +
///   candidate `ValidationReport`s + the gate decision + reasons).
/// - `<dir>/<SYMBOL>_<engine>_sweep.md` — human-readable baseline-vs-candidate
///   OOS table, the APPLY/KEEP decision, the gate reasons (or "all passed"), and
///   the fidelity-gap stamps that apply to every metric in the table.
///
/// `SweepResult.engine`'s default `Serialize` form is the variant name (e.g.
/// `"Trend"`); the artifact filename uses `budget_key()` (e.g. `trend`) to match
/// the live budget keys and the validation artifacts. Creates `dir` if missing.
pub fn write_sweep_report(dir: &Path, symbol: &str, rep: &SweepResult) -> Result<()> {
    std::fs::create_dir_all(dir)?;
    let json = serde_json::to_string_pretty(rep)?;
    std::fs::write(
        dir.join(format!("{}_{}_sweep.json", symbol, rep.engine.budget_key())),
        json,
    )?;

    let decision_str = if rep.decision.apply {
        "**APPLY**"
    } else {
        "**KEEP**"
    };
    let cand_block = match (&rep.best_label, &rep.candidate) {
        (Some(label), Some(c)) => format!(
            "| Candidate ({}) | {:.2}% | {:.2} | {:.2}% | {:.0}% | OOS Sharpe {:.2}, {} trades |\n",
            label,
            c.oos.total_return_pct,
            c.oos.sharpe,
            c.oos.max_drawdown_pct,
            c.oos.win_rate_pct,
            c.oos.sharpe,
            c.oos.total_trades,
        ),
        _ => String::from("| No candidate (none had ≥5 IS trades) | — | — | — | — | — |\n"),
    };
    let reasons = if rep.decision.gate_reasons.is_empty() {
        "_(all 5 gate checks passed)_".to_string()
    } else {
        rep.decision
            .gate_reasons
            .iter()
            .map(|r| format!("- {}", r))
            .collect::<Vec<_>>()
            .join("\n")
    };
    let md = format!(
        "# Sweep: {} {} (IS-tune → OOS-gate)\n\n\
         Decision: {}\n\n\
         | Config | Return | Sharpe | MaxDD | Win | Notes |\n\
         |---|---|---|---|---|---|\n\
         | Baseline (live) | {:.2}% | {:.2} | {:.2}% | {:.0}% | current deployed config |\n\
         {}\n\n\
         ## Gate reasons\n{}\n\n\
         ## Fidelity gaps\n\
         - regime=None (grid/trend ML gate off — optimistic).\n\
         - perp-as-spot-proxy + flat funding (trend-short MTM approximate).\n\
         - MR not swept (tick-resolution; separate tick-replay backtest).\n\
         - trade_journal OnceLock caches across sweep runs — no metric impact (Metrics come from in-memory P&L, not the DB).\n",
        symbol,
        rep.engine.budget_key(),
        decision_str,
        rep.baseline.oos.total_return_pct,
        rep.baseline.oos.sharpe,
        rep.baseline.oos.max_drawdown_pct,
        rep.baseline.oos.win_rate_pct,
        cand_block,
        reasons,
    );
    std::fs::write(
        dir.join(format!("{}_{}_sweep.md", symbol, rep.engine.budget_key())),
        md,
    )?;
    Ok(())
}
