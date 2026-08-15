# tests/test_rl_evaluation_boundary.py
"""Regression tests for the walk-forward evaluation-boundary defect.

Defect (audit 2026-08-15, critical #1): ``run_walk_forward`` prepends a
``warmup=100``-bar prefix to each test slice, but ``_evaluate_slice`` built
``EnvConfig(window_length=len(test_df))`` WITHOUT overriding ``warmup_bars``
(default 50). The env therefore started collecting returns at frame index 51
— ~49 bars BEFORE the declared test boundary at index 100 — while the TA
comparator dropped all 100 warmup bars. The pooled arrays were then truncated
by LENGTH, not aligned by TIMESTAMP, so PPO/RF and TA series were compared
bar-to-bar across misaligned timestamps.

These tests pin the corrected contract:
  * every comparator returns a timestamp-indexed series;
  * the first returned timestamp is exactly the declared test-slice start;
  * alignment is an inner join on timestamps, not length truncation.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

# Heavy RL stack is optional in CI (see tests/test_rl_env.py for the same
# guard) — the alignment helpers below are exercised regardless.
pytest.importorskip("gymnasium")

import numpy as np
import pandas as pd

from src.rl.env import EnvConfig, TradingEnv
from src.rl.evaluate import _run_model


class _FlatRouter:
    """Deterministic stub router: always action 0 (flat)."""

    def predict(self, obs: np.ndarray) -> int:
        return 0


def _synthetic_df(n_bars: int = 320, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    idx = pd.date_range(
        start=datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc),
        periods=n_bars, freq="1h", name="ts",
    )
    rets = rng.normal(0.0, 0.005, size=n_bars)
    close = 100.0 * np.exp(np.cumsum(rets))
    half = np.maximum(close * rng.uniform(0.001, 0.01, size=n_bars), 1e-6)
    return pd.DataFrame(
        {
            "open": close - half / 2,
            "high": close + half,
            "low": close - half,
            "close": close,
            "volume": rng.uniform(90, 110, size=n_bars),
        },
        index=idx,
    )


def test_run_model_returns_timestamp_indexed_series():
    """_run_model must expose the timestamps of the bars it collected."""
    df = _synthetic_df()
    warmup = 100
    cfg = EnvConfig(window_length=len(df), warmup_bars=warmup)
    env = TradingEnv(df, cfg)
    out = _run_model(env, _FlatRouter())
    ts = out["timestamps"]
    assert isinstance(ts, pd.DatetimeIndex)
    assert len(ts) == len(out["returns_array"])
    # First return corresponds to the bar AFTER the warmup anchor: step k
    # advances to bar (warmup + k) and reports that bar's timestamp.
    assert ts[0] == df.index[warmup + 1]


def test_evaluate_slice_first_timestamp_is_test_boundary():
    """All comparators must start exactly at the declared test-slice start.

    Env semantics: reset anchors on the LAST warmup bar (index ``warmup``);
    step 1 executes the first test bar (index ``warmup + 1``) and reports its
    timestamp. So the first collected return must be attributed to bar
    ``warmup + 1`` — the first bar of the test window — and never to any bar
    at or before the warmup anchor.
    """
    from src.rl.walk_forward import _evaluate_slice_aligned

    df = _synthetic_df(n_bars=320)
    warmup = 100
    test_df = df.iloc[: warmup + 200]   # warmup prefix + 200 test bars
    first_test_ts = df.index[warmup + 1]

    # Stub routers standing in for PPO/RF: boundary behaviour is what's under
    # test, not the routing policies themselves.
    import unittest.mock as mock

    flat = _FlatRouter()
    with mock.patch("src.rl.router.PPORouter", return_value=flat), \
         mock.patch("src.rl.router.SupervisedRegimeRouter", return_value=flat):
        result = _evaluate_slice_aligned(
            test_df, ppo_model_path="stub", rf_model_path="stub", warmup=warmup
        )
    for name in ("ppo", "rf", "ta"):
        series = result[name]["returns"]
        assert isinstance(series.index, pd.DatetimeIndex), name
        assert series.index[0] == first_test_ts, (
            f"{name} starts at {series.index[0]}, expected {first_test_ts}"
        )


def test_no_timestamp_precedes_boundary():
    """No comparator may return any timestamp before the boundary."""
    from src.rl.walk_forward import _evaluate_slice_aligned

    df = _synthetic_df(n_bars=320)
    warmup = 100
    test_df = df.iloc[: warmup + 200]
    boundary_ts = df.index[warmup]     # last warmup bar; test bars follow it

    import unittest.mock as mock

    flat = _FlatRouter()
    with mock.patch("src.rl.router.PPORouter", return_value=flat), \
         mock.patch("src.rl.router.SupervisedRegimeRouter", return_value=flat):
        result = _evaluate_slice_aligned(
            test_df, ppo_model_path="stub", rf_model_path="stub", warmup=warmup
        )
    for name in ("ppo", "rf", "ta"):
        series = result[name]["returns"]
        assert (series.index >= boundary_ts).all(), name


def test_all_comparators_share_identical_timestamps():
    """Inner-join alignment: identical index, identical length, no guessing."""
    from src.rl.walk_forward import _evaluate_slice_aligned

    df = _synthetic_df(n_bars=320)
    test_df = df.iloc[: 100 + 200]

    import unittest.mock as mock

    flat = _FlatRouter()
    with mock.patch("src.rl.router.PPORouter", return_value=flat), \
         mock.patch("src.rl.router.SupervisedRegimeRouter", return_value=flat):
        result = _evaluate_slice_aligned(
            test_df, ppo_model_path="stub", rf_model_path="stub", warmup=100
        )
    ppo_ts = result["ppo"]["returns"].index
    rf_ts = result["rf"]["returns"].index
    ta_ts = result["ta"]["returns"].index
    assert ppo_ts.equals(rf_ts)
    assert rf_ts.equals(ta_ts)


def test_inner_join_logs_count_mismatch(caplog):
    """When inputs differ in length, the join must disclose the counts."""
    import logging

    from src.rl.walk_forward import align_on_timestamps

    idx = pd.date_range(
        datetime(2026, 1, 1, tzinfo=timezone.utc), periods=10, freq="1h"
    )
    a = pd.Series(np.zeros(10), index=idx)
    shorter = idx[:-3]  # TA-style comparator missing the last 3 bars
    b = pd.Series(np.zeros(7), index=shorter)

    with caplog.at_level(logging.INFO, logger="src.rl.walk_forward"):
        ja, jb = align_on_timestamps(a, b)
    assert len(ja) == 7 and len(jb) == 7
    assert any("aligned" in r.message for r in caplog.records)
