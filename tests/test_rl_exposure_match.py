# tests/test_rl_exposure_match.py
"""Exposure-matched baseline tests (audit Task 6).

PPO's headline drawdown advantage (5.30% vs 19.22% buy & hold) was observed
at 52.3% time-in-market — lower exposure mechanically reduces drawdown.
These baselines test whether the advantage survives exposure matching.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("numpy")


def test_scaled_buy_hold_matches_exposure_definition():
    """Scaled B&H: same per-bar exposure fraction as the strategy."""
    from src.rl.exposure_match import scaled_buy_hold_returns

    bh = np.array([0.01, 0.02, -0.01, 0.005, -0.02, 0.01])
    exposure = np.array([1.0, 0.0, 1.0, 1.0, 0.0, 1.0])
    scaled = scaled_buy_hold_returns(bh, exposure)
    # Bars where exposure=0 contribute zero return
    assert scaled[1] == 0.0 and scaled[4] == 0.0
    assert scaled[0] == pytest.approx(0.01)


def test_scaled_buy_hold_constant_fraction():
    """Constant-fraction variant: every bar scaled by the same weight."""
    from src.rl.exposure_match import scaled_buy_hold_returns_constant

    bh = np.array([0.01, -0.02, 0.03])
    scaled = scaled_buy_hold_returns_constant(bh, fraction=0.5)
    assert scaled == pytest.approx(np.array([0.005, -0.01, 0.015]))


def test_random_entry_baseline_matches_target_exposure():
    """Random-entry baseline hits the target time-in-market fraction and
    reproduces the trade-count distribution approximately."""
    from src.rl.exposure_match import random_entry_returns

    bh = np.random.default_rng(0).normal(0.0, 0.01, 2000)
    rets, exposure_frac, n_trades = random_entry_returns(
        bh, target_exposure=0.5, avg_trade_length=24, seed=42
    )
    assert abs(exposure_frac - 0.5) < 0.05
    expected_trades = 2000 * 0.5 / 24
    assert abs(n_trades - expected_trades) < expected_trades * 0.5
    # invested bars earn bh, flat bars earn 0
    invested = exposure_frac_of_nonzero(rets)
    assert invested > 0.4


def exposure_frac_of_nonzero(rets):
    return float(np.mean(rets != 0.0))


def test_percentile_reporting():
    from src.rl.exposure_match import percentile_of

    dist = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    # midpoint convention: strictly-below=2/5, at-or-below=3/5 -> 50%
    assert percentile_of(3.0, dist) == 50.0
    assert percentile_of(0.5, dist) == 0.0   # strictly below everything
    assert percentile_of(1.0, dist) == 10.0  # midpoint of 0% and 20%
    assert percentile_of(5.5, dist) == 100.0
