"""Unit tests for src/rl/evaluate.py pure helpers.

These deliberately avoid importing gymnasium / stable-baselines3 / torch — the
metric, round-trip, and OOS-boundary logic must be testable with only numpy,
because the heavy RL stack is not installed in CI.
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np

import pytest


# ---------------------------------------------------------------------------
# Import-ability: the module must load with only numpy present (no
# gymnasium/sb3/torch). If this errors, someone added a top-level heavy import.
# ---------------------------------------------------------------------------
def test_module_importable_without_rl_deps():
    import src.rl.evaluate as ev  # noqa: F401

    assert hasattr(ev, "_diebold_mariano_test")
    assert hasattr(ev, "_count_round_trips")
    assert hasattr(ev, "_check_oos_boundary")


# ---------------------------------------------------------------------------
# _diebold_mariano_test (HAC-robust paired test on per-bar return diff)
# ---------------------------------------------------------------------------
def test_dm_clear_difference_is_significant():
    from src.rl.evaluate import _diebold_mariano_test

    a = np.full(200, 0.001)  # consistently +0.1%/bar
    b = np.zeros(200)
    stat, p = _diebold_mariano_test(a, b)
    assert stat > 0            # A outperforms B
    assert p < 0.05


def test_dm_identical_returns_not_significant():
    from src.rl.evaluate import _diebold_mariano_test

    a = np.array([0.001, -0.002, 0.0005, 0.003, -0.001] * 40)
    stat, p = _diebold_mariano_test(a, a)
    assert stat == 0.0
    assert p == 1.0


def test_dm_hac_path_handles_autocorrelated_diff():
    """A return-diff series with strong positive autocorrelation (long runs)
    must not blow up the HAC variance — finite stat, valid p-range."""
    from src.rl.evaluate import _diebold_mariano_test

    # Runs of 15 positive bars then 5 negative — strongly autocorrelated.
    diff = np.where(np.arange(200) % 20 < 15, 0.001, -0.003)
    a = diff
    b = np.zeros_like(a)
    stat, p = _diebold_mariano_test(a, b)
    assert np.isfinite(stat)
    assert 0.0 <= p <= 1.0


# ---------------------------------------------------------------------------
# _count_round_trips — closed round-trips + wins from an equity curve and a
# per-step in_position flag. Mirrors the semantics of _run_model's loop.
# ---------------------------------------------------------------------------
def test_count_round_trips_wins_and_losses():
    from src.rl.evaluate import _count_round_trips

    # 2 round-trips: first a win (entry 10010 -> exit 10030), second a loss
    # (entry 10030 -> exit 10010).
    equity = [10000, 10010, 10020, 10005, 10030, 10020, 10010]
    in_pos = [False, True, True, False, True, True]
    trades, wins = _count_round_trips(equity, in_pos)
    assert trades == 2
    assert wins == 1


def test_count_round_trips_position_open_at_end_is_counted():
    from src.rl.evaluate import _count_round_trips

    equity = [10000, 10050, 10060]
    in_pos = [True, True]  # never closes -> closed at end
    trades, wins = _count_round_trips(equity, in_pos)
    assert trades == 1
    assert wins == 1  # 10060 > entry 10000


def test_count_round_trips_grid_only_is_invisible():
    """The grid engine never sets in_position, so a grid-only episode yields
    zero counted round-trips. This documents the known gap, not a bug."""
    from src.rl.evaluate import _count_round_trips

    equity = [10000, 10001, 10002, 10003]
    in_pos = [False, False, False]
    trades, wins = _count_round_trips(equity, in_pos)
    assert trades == 0
    assert wins == 0


# ---------------------------------------------------------------------------
# _time_in_market — fraction of bars a strategy was deployed (engine != flat).
# Discloses exposure so PPO/RF Sharpes (which span flat, zero-return bars) are
# interpretable next to B&H's always-invested Sharpe.
# ---------------------------------------------------------------------------
def test_time_in_market_counts_non_flat_engines():
    from src.rl.evaluate import _time_in_market

    assert _time_in_market(["flat", "trend", "trend", "flat", "grid"]) == 0.6


def test_time_in_market_edge_cases():
    from src.rl.evaluate import _time_in_market

    assert _time_in_market([]) == 0.0
    assert _time_in_market(["flat", "flat"]) == 0.0
    assert _time_in_market(["trend"]) == 1.0


# ---------------------------------------------------------------------------
# _check_oos_boundary — refuse / warn if the PPO model was trained on data
# overlapping the OOS window.
# ---------------------------------------------------------------------------
def _write_sidecar(path, data_end):
    path.with_suffix(".json").write_text(json.dumps({"data_end": data_end}))


def test_oos_boundary_passes_when_train_ends_before_oos(tmp_path, capsys):
    from src.rl.evaluate import _check_oos_boundary

    model = tmp_path / "ppo.zip"
    _write_sidecar(model, "2026-05-31 23:00:00+00:00")
    rc = _check_oos_boundary(str(model), date(2026, 6, 1), allow_overlap=False)
    assert rc == 0
    assert "OK" in capsys.readouterr().err or "OK" in capsys.readouterr().out


def test_oos_boundary_violation_returns_error(tmp_path, capsys):
    from src.rl.evaluate import _check_oos_boundary

    model = tmp_path / "ppo.zip"
    _write_sidecar(model, "2026-07-04 23:00:00+00:00")  # overlaps OOS
    rc = _check_oos_boundary(str(model), date(2026, 6, 1), allow_overlap=False)
    assert rc == 1
    err = capsys.readouterr().err
    assert "VIOLATION" in err or "NOT out-of-sample" in err


def test_oos_boundary_allow_overlap_warns_but_passes(tmp_path, capsys):
    from src.rl.evaluate import _check_oos_boundary

    model = tmp_path / "ppo.zip"
    _write_sidecar(model, "2026-07-04 23:00:00+00:00")
    rc = _check_oos_boundary(str(model), date(2026, 6, 1), allow_overlap=True)
    assert rc == 0
    assert "WARNING" in capsys.readouterr().err


def test_oos_boundary_missing_sidecar_warns_and_passes(tmp_path, capsys):
    from src.rl.evaluate import _check_oos_boundary

    model = tmp_path / "ppo.zip"  # no sidecar written
    rc = _check_oos_boundary(str(model), date(2026, 6, 1), allow_overlap=False)
    assert rc == 0
    assert "WARNING" in capsys.readouterr().err
