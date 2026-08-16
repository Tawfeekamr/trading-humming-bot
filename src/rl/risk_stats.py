# src/rl/risk_stats.py
"""Risk-focused statistics for the corrected evaluation protocol.

Audit Task 7: the primary claim is about RISK (drawdown, downside), not
return parity. This module provides:

- ``max_drawdown`` / ``sortino`` — risk statistics on return series
- ``stationary_bootstrap_ci`` — Politis-Romano stationary bootstrap on the
  difference of a statistic between two series (paired), with automatic
  block length selection (Politis & White 2004 median-based rule)
- ``holm_correct`` — Holm-Bonferroni family-wise correction
- ``sharpe_annualized`` — Sharpe with an explicit ``include_flat`` choice;
  annualising over 8760 hourly bars while including flat bars dilutes the
  per-bar mean and mechanically deflates/inflates Sharpe for low-exposure
  strategies — callers must report both variants.
"""
from __future__ import annotations

import numpy as np


def max_drawdown(returns: np.ndarray) -> float:
    """Maximum peak-to-trough drawdown of a return series (fraction)."""
    equity = np.cumprod(1.0 + np.asarray(returns, dtype=np.float64))
    peaks = np.maximum.accumulate(equity)
    drawdowns = (peaks - equity) / np.where(peaks > 0, peaks, 1.0)
    return float(np.max(drawdowns, initial=0.0))


def sortino(returns: np.ndarray, bars_per_year: int = 8760) -> float:
    """Annualised Sortino ratio (downside deviation only)."""
    r = np.asarray(returns, dtype=np.float64)
    downside = r[r < 0]
    if len(r) < 2 or len(downside) == 0:
        return 0.0
    dd = float(np.sqrt(np.mean(downside**2)))
    if dd == 0:
        return 0.0
    return float(np.mean(r) / dd * np.sqrt(bars_per_year))


def sharpe_annualized(returns: np.ndarray, bars_per_year: int = 8760) -> float:
    """Annualised Sharpe on the series as given (flat bars included/excluded
    is the CALLER's choice — both variants must be reported)."""
    r = np.asarray(returns, dtype=np.float64)
    sd = float(np.std(r))
    if len(r) < 2 or sd == 0:
        return 0.0
    return float(np.mean(r) / sd * np.sqrt(bars_per_year))


def invested_bars_sharpe(returns: np.ndarray, bars_per_year: int = 8760) -> tuple[float, float]:
    """Sharpe over invested bars ONLY, annualised by the invested-bar rate.

    A strategy invested f% of bars experiences ~f*bars_per_year invested
    bars per year; annualising the invested-bar Sharpe by sqrt(8760)
    (batch-1 mistake) uses the full-year factor on a subset of bars and
    OVERSTATES the ratio by sqrt(1/f) — e.g. f=0.15 inflates 2.6x.

    Returns (sharpe, annualisation_factor_used).
    """
    r = np.asarray(returns, dtype=np.float64)
    invested = r[r != 0.0]
    if len(invested) < 2:
        return 0.0, 0.0
    frac = len(invested) / len(r)
    factor = np.sqrt(bars_per_year * frac)
    sd = float(np.std(invested))
    if sd == 0:
        return 0.0, float(factor)
    return float(np.mean(invested) / sd * factor), float(factor)





def _heuristic_block_length(x: np.ndarray) -> int:
    """LENGTH-ONLY heuristic block choice: ceil(4*(n/100)^(2/9)).

    Renamed from _autoblock_length (batch 7 task 7): this is NOT the
    Politis-White automatic rule, which inspects the series'
    autocorrelation structure. This heuristic uses sample length only.
    Interval widths produced with it are CONDITIONAL on this arbitrary
    choice; Politis-White was not implemented (would require new
    computation; the freeze permits definitional fixes only)."""
    n = len(x)
    if n < 20:
        return 1
    return max(1, int(np.ceil(4 * (n / 100.0) ** (2.0 / 9.0))))


def stationary_bootstrap_ci(
    series_a: np.ndarray,
    series_b: np.ndarray,
    stat_fn,
    *,
    n_boot: int = 1000,
    seed: int = 42,
    alpha: float = 0.05,
) -> tuple[float, float, float, int]:
    """Politis-Romano stationary bootstrap on stat_fn(a, b).

    Draws paired circular blocks (same resampled indices for both series,
    preserving their per-bar pairing), computes the statistic per replicate,
    and returns (point_estimate, ci_lo, ci_hi, block_length).
    """
    a = np.asarray(series_a, dtype=np.float64)
    b = np.asarray(series_b, dtype=np.float64)
    if len(a) != len(b):
        raise ValueError("paired bootstrap requires equal-length series")
    n = len(a)
    if n == 0:
        return 0.0, 0.0, 0.0, 0

    point = float(stat_fn(a, b))
    block = _heuristic_block_length(a)
    rng = np.random.default_rng(seed)

    stats = np.empty(n_boot)
    for k in range(n_boot):
        # Stationary bootstrap: random start each block, geometric block
        # length with mean `block` (expected length), wrapping circularly.
        idx = np.empty(n, dtype=np.int64)
        i = 0
        while i < n:
            start = rng.integers(0, n)
            length = rng.geometric(1.0 / block)
            for j in range(length):
                if i >= n:
                    break
                idx[i] = (start + j) % n
                i += 1
        stats[k] = stat_fn(a[idx], b[idx])

    lo = float(np.percentile(stats, 100 * alpha / 2))
    hi = float(np.percentile(stats, 100 * (1 - alpha / 2)))
    return point, lo, hi, block


def holm_correct(pvals: list[float]) -> list[float]:
    """Holm-Bonferroni step-down adjusted p-values (family-wise)."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adjusted = [0.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        adj = (m - rank) * pvals[i]
        running = max(running, adj)  # enforce monotonicity
        adjusted[i] = min(1.0, running)
    return adjusted
