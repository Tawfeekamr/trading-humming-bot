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

use super::replay::RunResult;

#[derive(Debug, Serialize)]
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
