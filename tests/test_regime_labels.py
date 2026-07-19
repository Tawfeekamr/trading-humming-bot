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


# ─── now-cast (trailing-window) labels ────────────────────────────────────────


def _nowcast(df, **kw):
    from src.data.label_generation import generate_regime_labels_nowcast

    return generate_regime_labels_nowcast(df, **kw)


def test_nowcast_trending_uptrend_is_trending():
    # +30% ramp: the trailing 24-bar return exceeds 2% -> trending (1) at the end.
    df = pd.DataFrame({"close": np.linspace(100, 130, 60)})
    out = _nowcast(df, window=24, trend_thr=0.02, danger_thr=-0.03)
    assert out["regime_label"].iloc[-1] == 1


def test_nowcast_ranging_flat_noise_is_ranging():
    rng = np.random.RandomState(0)
    close = 100 + rng.randn(200) * 0.005  # tiny noise, <2% move, <3% drawdown
    df = pd.DataFrame({"close": close})
    out = _nowcast(df, window=24, trend_thr=0.02, danger_thr=-0.03)
    assert (out["regime_label"].iloc[24:180] == 0).all()


def test_nowcast_danger_crash_is_danger_and_takes_precedence():
    # Ramp up (would be trending) then a -10% crash in the last bars: danger wins
    # (max drawdown within the trailing window <= -3%, checked before trending).
    close = np.concatenate([np.linspace(100, 115, 40), np.linspace(115, 103.5, 10)])
    df = pd.DataFrame({"close": close})
    out = _nowcast(df, window=24, trend_thr=0.02, danger_thr=-0.03)
    assert out["regime_label"].iloc[-1] == 2


def test_nowcast_first_window_minus_one_bars_are_sentinel():
    df = pd.DataFrame({"close": np.linspace(100, 130, 60)})
    out = _nowcast(df, window=24)
    assert (out["regime_label"].iloc[:23] == -1).all()
    assert out["regime_label"].iloc[23] != -1


def test_nowcast_has_no_lookahead():
    # Truncating the series must not change labels on the retained prefix
    # (now-cast only ever looks at past bars).
    full = pd.DataFrame(
        {"close": np.concatenate([np.linspace(100, 130, 60), np.linspace(130, 110, 30)])}
    )
    trunc = full.iloc[:70]
    a = _nowcast(full, window=24)["regime_label"].iloc[:70].to_numpy()
    b = _nowcast(trunc, window=24)["regime_label"].to_numpy()
    assert np.array_equal(a, b)
