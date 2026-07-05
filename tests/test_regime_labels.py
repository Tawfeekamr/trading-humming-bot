"""Tests for src/data/label_generation.generate_regime_labels.

3-class forward-looking regime labels. The lookahead is the supervised TARGET
(correct); the trainer enforces a temporal train/test split so it never leaks.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def _labels(df, **kw):
    from src.data.label_generation import generate_regime_labels

    return generate_regime_labels(df, **kw)


def test_trending_uptrend_is_labeled_trending():
    # +30% linear ramp over 100 bars: early bars see future gains -> trending (1).
    df = pd.DataFrame({"close": np.linspace(100, 130, 100)})
    out = _labels(df, horizon=10, trend_thr=0.02, danger_thr=-0.03)
    assert out["regime_label"].iloc[0] == 1


def test_danger_crash_ahead_is_labeled_danger():
    # Flat then a -15% crash; a bar ~5 before the crash sees danger ahead (2).
    close = np.concatenate([np.full(60, 100.0), np.linspace(100, 85, 15)])
    df = pd.DataFrame({"close": close})
    out = _labels(df, horizon=10, trend_thr=0.02, danger_thr=-0.03)
    assert out["regime_label"].iloc[55] == 2


def test_ranging_flat_noise_is_labeled_ranging():
    rng = np.random.RandomState(0)
    close = 100 + rng.randn(200) * 0.005  # tiny noise, no trend / no crash
    df = pd.DataFrame({"close": close})
    out = _labels(df, horizon=10, trend_thr=0.02, danger_thr=-0.03)
    assert (out["regime_label"].iloc[:180] == 0).all()


def test_last_horizon_bears_have_no_label():
    # The final `horizon` bars cannot see the future -> sentinel -1.
    df = pd.DataFrame({"close": np.linspace(100, 130, 50)})
    out = _labels(df, horizon=10, trend_thr=0.02, danger_thr=-0.03)
    assert out["regime_label"].iloc[-1] == -1
    assert out["regime_label"].iloc[-10] == -1


def test_danger_takes_precedence_over_trending():
    # A window that both trends down sharply and crashes should be danger (2),
    # not trending (1) — stop ahead of a violent move matters more than direction.
    # Crash spans bars 20-31; bar 15's 10-bar horizon reaches into the crash.
    close = np.concatenate([np.full(20, 100.0), np.linspace(100, 88, 12)])
    df = pd.DataFrame({"close": close})
    out = _labels(df, horizon=10, trend_thr=0.02, danger_thr=-0.03)
    assert out["regime_label"].iloc[15] == 2
