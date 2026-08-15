# tests/test_rl_risk_stats.py
"""Tests for the corrected statistical toolkit (audit Task 7).

The function previously called ``_diebold_mariano_test`` is NOT a
Diebold-Mariano test: DM is defined on forecast-loss differentials, while
this code runs a paired mean-difference test on realised returns with
Newey-West HAC standard errors. It is renamed accordingly.
"""
from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("scipy")


def test_paired_mean_difference_hac_renames_dm():
    """The renamed test exists and the old misleading name is gone."""
    import src.rl.evaluate as ev

    assert hasattr(ev, "paired_mean_difference_hac_test")
    assert not hasattr(ev, "_diebold_mariano_test")


def test_hac_lag_follows_newey_west_rule():
    """Lag = floor(4*(n/100)^(2/9)) — the standard automatic rule."""
    from src.rl.evaluate import newey_west_lag

    for n, expected in [(50, 3), (100, 4), (1000, 6), (4620, 9)]:
        lag = newey_west_lag(n)
        assert lag == expected, f"n={n}: got {lag}, expected {expected}"


def test_hac_test_rejects_consistent_edge():
    from src.rl.evaluate import paired_mean_difference_hac_test

    rng = np.random.default_rng(0)
    a = rng.normal(0.002, 0.01, 2000)
    b = rng.normal(0.000, 0.01, 2000)
    stat, p = paired_mean_difference_hac_test(a, b)
    assert stat > 0 and p < 0.05


def test_hac_test_no_edge_on_identical():
    from src.rl.evaluate import paired_mean_difference_hac_test

    rng = np.random.default_rng(1)
    r = rng.normal(0, 0.01, 2000)
    stat, p = paired_mean_difference_hac_test(r, r.copy())
    assert abs(stat) < 1e-9 and p > 0.99


def test_stationary_bootstrap_ci_covers_truth():
    """Politis-Romano bootstrap: the percentile CI must cover the true
    difference (0) across repeated draws with high probability — checked
    over several seeds rather than a single lucky draw."""
    from src.rl.risk_stats import stationary_bootstrap_ci

    stat_fn = lambda x, y: float(np.mean(x) - np.mean(y))
    covered = 0
    trials = 10
    for t in range(trials):
        rng = np.random.default_rng(100 + t)
        a = rng.normal(0.0, 0.01, 800)
        b = rng.normal(0.0, 0.01, 800)
        est, lo, hi, block = stationary_bootstrap_ci(
            a, b, stat_fn, n_boot=300, seed=t
        )
        assert block >= 1
        if lo <= 0.0 <= hi:
            covered += 1
    # 95% CI: expect ~95% coverage; allow >= 7/10 for test stability
    assert covered >= 7, f"coverage {covered}/{trials}"


def test_max_drawdown_diff_bootstrap():
    from src.rl.risk_stats import max_drawdown, stationary_bootstrap_ci

    rng = np.random.default_rng(4)
    a = rng.normal(0.001, 0.01, 1500)
    b = rng.normal(-0.001, 0.01, 1500)
    est, lo, hi, block = stationary_bootstrap_ci(
        a, b, lambda x, y: max_drawdown(x) - max_drawdown(y), n_boot=300, seed=5
    )
    # a has positive drift -> smaller drawdown than b: negative diff expected
    assert est < 0
    assert lo <= est <= hi


def test_holm_correction():
    from src.rl.risk_stats import holm_correct

    pvals = [0.01, 0.04, 0.03]
    adjusted = holm_correct(pvals)
    # Holm: sort ascending; adj_i = max over j<=i of (m-j)*p_j, monotone
    assert adjusted[0] <= adjusted[2] <= adjusted[1]
    assert adjusted[0] == pytest.approx(0.03)
    assert min(adjusted) < 0.05


def test_sharpe_with_and_without_flat_bars():
    from src.rl.risk_stats import sharpe_annualized

    rng = np.random.default_rng(6)
    rets = rng.normal(0.0005, 0.01, 1000)
    # strategy flat half the time: zero returns interleave
    with_zeros = rets.copy()
    with_zeros[::2] = 0.0
    s_all = sharpe_annualized(with_zeros, bars_per_year=8760)
    s_invested = sharpe_annualized(
        with_zeros[with_zeros != 0.0], bars_per_year=8760
    )
    # Including flat bars dilutes the mean per-bar return -> lower Sharpe
    assert s_all < s_invested
