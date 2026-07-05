"""Unit tests for scripts/fidelity_check.py::compare.

The fidelity check is the falsification gate for the PPO→live pipeline:
the policy was trained on the toy engines in src/rl/env.py; the paper Rust
instance runs the REAL engines. compare() asks whether the per-bar equity
trajectory the toy env predicted matches what the paper engine produced on
the SAME bars + SAME routing decisions. Divergence => the toy simulator
does not represent production => the trained policy is built on a wrong
simulator => do not promote.

These tests deliberately use only numpy (no RL stack) — the comparison is a
pure numpy function and must be testable in CI without gymnasium/sb3/torch.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# Make the repo root importable so `from scripts.fidelity_check import compare`
# works under pytest without an installed `scripts` package — same pattern as
# tests/test_backtest_reporting.py.
sys.path.insert(0, str(Path(__file__).parent.parent))

from scripts.fidelity_check import compare  # noqa: E402


# ---------------------------------------------------------------------------
# Identical series => perfect correlation, zero per-bar P&L diff, PASS.
# ---------------------------------------------------------------------------
def test_identical_series_passes():
    eq = [10000.0, 10010.0, 10020.0, 10015.0, 10025.0, 10040.0]
    out = compare(eq, eq)
    assert out["pearson"] == pytest.approx(1.0, abs=1e-9)
    assert out["mean_abs_bar_pnl_diff"] == pytest.approx(0.0, abs=1e-12)
    assert out["pass"] is True


# ---------------------------------------------------------------------------
# Nearly-identical series (tiny noise) => Pearson still high, diff small, PASS.
# ---------------------------------------------------------------------------
def test_near_identical_series_passes():
    env = [10000.0, 10010.0, 10020.0, 10030.0, 10040.0]
    paper = [10000.0, 10009.5, 10019.5, 10030.5, 10040.0]
    out = compare(env, paper)
    assert out["pearson"] > 0.99
    assert out["mean_abs_bar_pnl_diff"] < 0.005
    assert out["pass"] is True


# ---------------------------------------------------------------------------
# Scaled series (paper grows 2x faster than env) => high Pearson (same shape)
# but large per-bar P&L diff => FAIL on the band even though Pearson passes.
# ---------------------------------------------------------------------------
def test_scaled_series_fails_on_band():
    env = [10000.0, 10100.0, 10200.0, 10300.0, 10400.0]  # +1% / bar
    paper = [10000.0, 10200.0, 10400.0, 10600.0, 10800.0]  # +2% / bar
    out = compare(env, paper)
    assert out["pearson"] > 0.7  # shape is identical
    assert out["mean_abs_bar_pnl_diff"] > 0.005  # but magnitude is off
    assert out["pass"] is False


# ---------------------------------------------------------------------------
# Divergent series (opposite direction) => negative Pearson => FAIL.
# ---------------------------------------------------------------------------
def test_divergent_series_fails():
    env = [10000.0, 10100.0, 10200.0, 10300.0, 10400.0]  # going up
    paper = [10000.0, 9900.0, 9800.0, 9700.0, 9600.0]  # going down
    out = compare(env, paper)
    assert out["pearson"] < 0  # anti-correlated
    assert out["pass"] is False


# ---------------------------------------------------------------------------
# Uncorrelated noise => Pearson well below the promotion threshold => FAIL.
# We don't pin Pearson to a tight range (two finite-length random walks can
# drift to surprisingly large positive or negative correlations by chance);
# we only assert the verdict fails, which is what the gate actually enforces.
# ---------------------------------------------------------------------------
def test_uncorrelated_series_fails():
    rng = np.random.default_rng(42)
    env = (10000.0 + np.cumsum(rng.normal(0, 10, 200))).tolist()
    paper = (10000.0 + np.cumsum(rng.normal(0, 10, 200))).tolist()
    out = compare(env, paper)
    assert out["pearson"] < 0.7
    assert out["pass"] is False


# ---------------------------------------------------------------------------
# Edge: length mismatch => truncate to the shorter series, no error.
# ---------------------------------------------------------------------------
def test_length_mismatch_truncates_to_shorter():
    env = [10000.0, 10010.0, 10020.0, 10030.0, 10040.0]
    paper = [10000.0, 10010.0, 10020.0]  # shorter
    out = compare(env, paper)
    # Both truncated to length 3 => identical on those bars => PASS.
    assert out["pearson"] == pytest.approx(1.0, abs=1e-9)
    assert out["mean_abs_bar_pnl_diff"] == pytest.approx(0.0, abs=1e-12)
    assert out["pass"] is True


# ---------------------------------------------------------------------------
# Edge: zero-variance series (constant equity) => Pearson undefined => 0.0 =>
# FAIL (a flat line carries no signal about simulator fidelity either way).
# ---------------------------------------------------------------------------
def test_zero_variance_series_returns_zero_pearson_and_fails():
    env = [10000.0, 10000.0, 10000.0, 10000.0]
    paper = [10000.0, 10010.0, 10020.0, 10030.0]
    out = compare(env, paper)
    assert out["pearson"] == 0.0
    assert out["pass"] is False


# ---------------------------------------------------------------------------
# Edge: empty arrays => no bars to compare => diff is NaN/0, pearson 0.0, FAIL.
# ---------------------------------------------------------------------------
def test_empty_arrays_do_not_crash():
    out = compare([], [])
    assert out["pearson"] == 0.0
    assert out["pass"] is False


# ---------------------------------------------------------------------------
# Edge: single point => diff over length-0 array => mean of empty is 0.0 by
# convention here (np.mean of empty raises a warning; we suppress it inside
# compare), pearson 0.0, FAIL.
# ---------------------------------------------------------------------------
def test_single_point_does_not_crash():
    out = compare([10000.0], [10000.0])
    assert out["pearson"] == 0.0
    assert out["pass"] is False


# ---------------------------------------------------------------------------
# Custom pnl_band widens / tightens the pass threshold independently of Pearson.
# Same-shape curves (Pearson = 1.0) but +1 percentage point extra per bar on
# paper => mean-abs-diff ~0.01. Tight band (0.005) => FAIL; loose (0.05) => PASS.
# ---------------------------------------------------------------------------
def test_custom_pnl_band_changes_verdict():
    env = [10000.0, 10100.0, 10200.0, 10300.0, 10400.0]  # +1% / bar
    paper = [10000.0, 10200.0, 10400.0, 10600.0, 10800.0]  # +2% / bar
    tight = compare(env, paper, pnl_band=0.005)
    loose = compare(env, paper, pnl_band=0.05)
    # Same shape => Pearson high on both.
    assert tight["pearson"] > 0.99
    assert loose["pearson"] > 0.99
    assert tight["mean_abs_bar_pnl_diff"] > 0.005  # ~1 percentage point / bar
    # Tight band rejects; loose band accepts — band acts independently of Pearson.
    assert tight["pass"] is False
    assert loose["pass"] is True


# ---------------------------------------------------------------------------
# Verdict dict has exactly the three documented keys with the right types.
# ---------------------------------------------------------------------------
def test_verdict_dict_shape():
    out = compare([10000.0, 10010.0], [10000.0, 10010.0])
    assert set(out.keys()) == {"pearson", "mean_abs_bar_pnl_diff", "pass"}
    assert isinstance(out["pearson"], float)
    assert isinstance(out["mean_abs_bar_pnl_diff"], float)
    assert isinstance(out["pass"], bool)
