"""Unit tests for src/rl/walk_forward.py pure helpers.

Like test_rl_evaluate.py, these run with only numpy+pytest — no gymnasium /
sb3 / torch. The orchestration (subprocess training) is validated separately
in a tiny end-to-end smoke, not here.
"""
from __future__ import annotations

import numpy as np


def test_walk_forward_slices_basic():
    from src.rl.walk_forward import walk_forward_slices

    slices = walk_forward_slices(
        series_len=1000, train_bars=400, test_bars=100, step_bars=100
    )
    assert len(slices) == 6  # i = 0,100,200,300,400,500
    assert slices[0] == (0, 400, 400, 500)
    assert slices[-1] == (500, 900, 900, 1000)


def test_walk_forward_slices_train_strictly_before_test():
    from src.rl.walk_forward import walk_forward_slices

    for ts, te, vs, ve in walk_forward_slices(1000, 400, 100, 100):
        assert te == vs  # contiguous, no gap, no overlap
        assert te > ts and ve > vs
        assert ve <= 1000


def test_walk_forward_slices_preserve_embargo_gap():
    from src.rl.walk_forward import walk_forward_slices

    slices = walk_forward_slices(600, 300, 100, 100, embargo_bars=24)
    assert slices[0] == (0, 300, 324, 424)
    for _, train_end, test_start, _ in slices:
        assert test_start - train_end == 24


def test_walk_forward_slices_empty_when_data_too_short():
    from src.rl.walk_forward import walk_forward_slices

    assert walk_forward_slices(400, 400, 100, 100) == []
    assert walk_forward_slices(499, 400, 100, 100) == []
    assert walk_forward_slices(0, 400, 100, 100) == []


def test_walk_forward_slices_step_one():
    from src.rl.walk_forward import walk_forward_slices

    slices = walk_forward_slices(500, 300, 100, 1)
    assert slices[0] == (0, 300, 300, 400)
    assert slices[1] == (1, 301, 301, 401)
    assert slices[-1][3] == 500  # last test ends exactly at series end


def test_pool_returns_concatenates_aligned():
    from src.rl.walk_forward import pool_returns

    a = [np.array([0.01, 0.02]), np.array([0.03])]
    b = [np.array([0.0, 0.0]), np.array([0.0])]
    pa, pb = pool_returns(a, b)
    assert len(pa) == 3 and len(pb) == 3
    assert list(pa) == [0.01, 0.02, 0.03]


def test_aggregate_dm_picks_up_consistent_edge():
    from src.rl.walk_forward import aggregate_dm

    # PPO consistently +0.001/bar across 2 slices; RF flat. Pooled DM should
    # be large positive and significant.
    ppo = [np.full(100, 0.001), np.full(100, 0.001)]
    rf = [np.zeros(100), np.zeros(100)]
    stat, p, n = aggregate_dm(ppo, rf)
    assert stat > 0
    assert p < 0.05
    assert n == 200


def test_aggregate_dm_no_edge_when_identical():
    from src.rl.walk_forward import aggregate_dm

    ppo = [np.array([0.001, -0.002, 0.0]), np.array([0.0, 0.001, -0.001])]
    stat, p, n = aggregate_dm(ppo, ppo)
    assert stat == 0.0
    assert p == 1.0
    assert n == 6
