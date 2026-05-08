import numpy as np
import pandas as pd
from dataclasses import dataclass


@dataclass
class BacktestResult:
    strategy_returns: pd.Series
    benchmark_returns: pd.Series
    trades: int
    sharpe: float
    max_drawdown_pct: float
    total_return_pct: float
    win_rate: float
    total_fees: float
    slippage_cost: float


def compute_benchmark(close: pd.Series) -> pd.Series:
    """HODL benchmark: buy-and-hold cumulative return."""
    return (close / close.iloc[0] - 1) * 100


def add_slippage(close: pd.Series, entries: pd.Series, exits: pd.Series,
                 slip_bps: float = 5.0) -> tuple[pd.Series, pd.Series]:
    """Adjust entry/exit signals for slippage (in basis points).

    Note: For vectorbt Portfolio.from_signals, slippage is better modeled
    by passing the `slippage` parameter directly to the constructor.
    This function returns signals unchanged since vectorbt handles slippage
    via its built-in slippage parameter.

    Args:
        close: Price series
        entries: Boolean entry signals
        exits: Boolean exit signals
        slip_bps: Slippage in basis points (e.g., 5.0 = 0.05%)

    Returns:
        Tuple of (entries, exits) boolean masks (unchanged)
    """
    if slip_bps <= 0:
        return entries, exits
    # Slippage is handled by vectorbt's slippage parameter in Portfolio.from_signals
    # This function is kept for API compatibility but returns signals unchanged
    return entries, exits


def monte_carlo_simulation(returns: pd.Series, n_sims: int = 1000,
                           n_days: int = 90) -> pd.DataFrame:
    """Resample daily returns to build confidence intervals.
    Returns DataFrame with columns [p5, p25, p50, p75, p95] indexed by day.
    """
    daily_returns = returns.resample('D').sum() if hasattr(returns.index, 'freq') else returns
    sims = np.zeros((n_sims, n_days))
    for i in range(n_sims):
        sample = np.random.choice(daily_returns.values, size=n_days, replace=True)
        sims[i] = np.cumsum(sample)

    percentiles = np.percentile(sims, [5, 25, 50, 75, 95], axis=0)
    return pd.DataFrame({
        'p5': percentiles[0], 'p25': percentiles[1], 'p50': percentiles[2],
        'p75': percentiles[3], 'p95': percentiles[4],
    }, index=pd.RangeIndex(n_days, name='day'))


def format_report(result: BacktestResult, label: str = "") -> str:
    """Format a backtest result as a printable table."""
    header = f"=== {label} ===" if label else "=== Backtest Result ==="
    return (
        f"{header}\n"
        f"  Total Return:  {result.total_return_pct:.2f}%\n"
        f"  Sharpe Ratio:  {result.sharpe:.2f}\n"
        f"  Max Drawdown:  {result.max_drawdown_pct:.2f}%\n"
        f"  Win Rate:      {result.win_rate:.1f}%\n"
        f"  Total Trades:  {result.trades}\n"
        f"  Total Fees:    ${result.total_fees:.2f}\n"
        f"  Slippage Cost: ${result.slippage_cost:.2f}\n"
        f"  HODL Return:   {result.benchmark_returns.iloc[-1]:.2f}%\n"
    )


def save_report(results: list[BacktestResult], path: str = "reports/backtest_report.csv"):
    """Save results to CSV."""
    import os
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    rows = [{
        'trades': r.trades, 'sharpe': r.sharpe,
        'max_drawdown_pct': r.max_drawdown_pct,
        'total_return_pct': r.total_return_pct,
        'win_rate': r.win_rate, 'total_fees': r.total_fees,
        'slippage_cost': r.slippage_cost,
        'benchmark_return_pct': r.benchmark_returns.iloc[-1] if len(r.benchmark_returns) > 0 else 0,
    } for r in results]
    pd.DataFrame(rows).to_csv(path, index=False)
