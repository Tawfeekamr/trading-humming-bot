# src/rl/exposure_match.py
"""Exposure-matched baselines (audit Task 6).

PPO's observed drawdown advantage over buy & hold may be entirely explained
by lower time-in-market rather than timing skill. These constructors build
baselines over the IDENTICAL timestamps as the strategy's return series:

- ``scaled_buy_hold_returns_constant``: buy & hold scaled to the strategy's
  realised average exposure (constant fractional position).
- ``scaled_buy_hold_returns``: per-bar fractional exposure (1.0 when the
  strategy was invested, 0.0 when flat — for per-bar matched exposure).
- ``random_entry_returns``: random entries matching the strategy's
  time-in-market fraction and trade-length distribution.
- ``percentile_of``: where a strategy's statistic falls in a seed
  distribution.
"""
from __future__ import annotations

import numpy as np


def scaled_buy_hold_returns_constant(
    bh_returns: np.ndarray, fraction: float
) -> np.ndarray:
    """Buy & hold at a constant fractional position ``fraction``."""
    return np.asarray(bh_returns, dtype=np.float64) * float(fraction)


def scaled_buy_hold_returns(
    bh_returns: np.ndarray, exposure: np.ndarray
) -> np.ndarray:
    """Buy & hold with per-bar exposure weights (1.0 invested, 0.0 flat)."""
    bh = np.asarray(bh_returns, dtype=np.float64)
    exp = np.asarray(exposure, dtype=np.float64)
    if len(bh) != len(exp):
        raise ValueError("bh_returns and exposure must align per bar")
    return bh * exp


def random_entry_returns(
    bh_returns: np.ndarray,
    target_exposure: float,
    avg_trade_length: int,
    seed: int,
) -> tuple[np.ndarray, float, int]:
    """Random-entry strategy return series.

    Randomly opens positions with geometrically-distributed lengths so the
    realised time-in-market approximates ``target_exposure`` and the average
    trade length approximates ``avg_trade_length`` bars. Invested bars earn
    the buy & hold return; flat bars earn 0.

    Returns ``(returns, realised_exposure_fraction, trade_count)``.
    """
    bh = np.asarray(bh_returns, dtype=np.float64)
    n = len(bh)
    rng = np.random.default_rng(seed)

    # Alternating renewal: invested runs of mean length L, flat gaps of mean
    # length G. Steady-state exposure = L / (L + G). Solve G from the target:
    #   target = L / (L + G)  ->  G = L * (1 - target) / target
    # A flat gap is geometric with mean G, i.e. per-bar exit-from-gap
    # probability 1/G.
    length = max(1, int(avg_trade_length))
    target = float(target_exposure)
    if target <= 0:
        return np.zeros(n), 0.0, 0
    if target >= 1:
        return bh.copy(), 1.0, 1
    mean_gap = length * (1.0 - target) / target
    p_entry = 1.0 / mean_gap

    returns = np.zeros(n, dtype=np.float64)
    trades = 0
    i = 0
    while i < n:
        if rng.random() < p_entry:
            hold = max(1, int(rng.geometric(1.0 / length)))
            end = min(n, i + hold)
            returns[i:end] = bh[i:end]
            trades += 1
            i = end
        else:
            i += 1
    exposure_frac = float(np.mean(returns != 0.0)) if n else 0.0
    return returns, exposure_frac, trades


def percentile_of(value: float, distribution: np.ndarray) -> float:
    """Percentile of ``value`` within ``distribution`` (0=at min, 100=at max).

    Midpoint convention for values present in the distribution: the median
    member of [1,2,3,4,5] sits at the 50th percentile.
    """
    d = np.asarray(distribution, dtype=np.float64)
    if len(d) == 0:
        return float("nan")
    below = float(np.mean(d < value))
    at_or_below = float(np.mean(d <= value))
    return 100.0 * (below + at_or_below) / 2.0
