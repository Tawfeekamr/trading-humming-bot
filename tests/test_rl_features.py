# tests/test_rl_features.py
"""Unit tests for src/rl/features.py — RL observation-space feature pipeline.

Validates the 8 market features + 3 time features produced by ``compute_features``:
shape/columns, value bounds (RSI, volume_ratio), warmup-NaN filling, and the
cyclical time encoding at known timestamps.
"""
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from src.rl.features import (
    FEATURE_COLS,
    MARKET_FEATURE_COLS,
    TIME_FEATURE_COLS,
    compute_features,
)


def _ohlcv(n: int = 100, start_hour: int = 0) -> pd.DataFrame:
    """Synthetic OHLCV n-bar frame indexed by hourly UTC datetimes.

    Uses a gentle random-walk close (deterministic seed) so vol/RSI/ATR are
    well-defined and non-degenerate. ``start_hour`` lets time-feature tests
    pin a known hour at row 0.
    """
    rng = np.random.default_rng(42)
    idx = pd.date_range(
        start=datetime(2026, 1, 1, start_hour, 0, tzinfo=timezone.utc),
        periods=n,
        freq="1h",
        name="ts",
    )
    rets = rng.normal(0, 0.005, size=n)
    close = 100 * np.exp(np.cumsum(rets))
    high = close * (1 + rng.uniform(0, 0.01, size=n))
    low = close * (1 - rng.uniform(0, 0.01, size=n))
    open_ = (high + low) / 2
    volume = rng.uniform(100, 1000, size=n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def test_compute_features_shape_and_columns():
    """Output has the 11 expected columns + same row count and index as input."""
    df = _ohlcv(n=100)
    out = compute_features(df)

    assert list(out.columns) == FEATURE_COLS
    assert len(out) == len(df)
    pd.testing.assert_index_equal(out.index, df.index)
    # Sanity: 8 market + 3 time = 11.
    assert len(MARKET_FEATURE_COLS) == 8
    assert len(TIME_FEATURE_COLS) == 3
    assert len(FEATURE_COLS) == 11


def test_rsi_bounded_0_100():
    """All RSI values (after warmup) lie in [0, 100]."""
    df = _ohlcv(n=200)
    out = compute_features(df)

    # Skip the first 14 rows (Wilder's min_periods=14 warmup); the remaining
    # RSI values are real Wilder's-smoothed and must be in [0, 100].
    rsi_post_warmup = out["rsi_14"].iloc[14:]
    assert (rsi_post_warmup >= 0).all(), "RSI below 0"
    assert (rsi_post_warmup <= 100).all(), "RSI above 100"


def test_returns_first_row_is_nan_or_zero():
    """Row 0 has no prior bar -> returns is NaN pre-fill -> 0 post-fill."""
    df = _ohlcv(n=50)
    out = compute_features(df)

    # Leading NaN cannot be forward-filled (nothing before it), so the final
    # zero-fill collapses it to 0.0.
    assert out["returns"].iloc[0] == 0.0


def test_volume_ratio_positive():
    """volume_ratio = volume / (mean+eps) is non-negative for non-negative vol."""
    df = _ohlcv(n=100)
    out = compute_features(df)

    assert (out["volume_ratio"] >= 0).all()


def test_time_features_correct():
    """00:00 -> sin=0, cos=1; 06:00 -> sin=1, cos=0; day_of_week is integer."""
    # Frame starting at 00:00 UTC, 12 hourly bars -> hour 0 at row 0, hour 6
    # at row 6.
    df = _ohlcv(n=12, start_hour=0)
    out = compute_features(df)

    assert np.isclose(out["hour_sin"].iloc[0], 0.0, atol=1e-9)
    assert np.isclose(out["hour_cos"].iloc[0], 1.0, atol=1e-9)

    assert np.isclose(out["hour_sin"].iloc[6], 1.0, atol=1e-9)
    assert np.isclose(out["hour_cos"].iloc[6], 0.0, atol=1e-9)

    # day_of_week for 2026-01-01 (a Thursday) = 3.
    assert out["day_of_week"].iloc[0] == 3


def test_no_nan_after_fill():
    """Output is fully NaN-free after ffill + zero-fill."""
    df = _ohlcv(n=100)
    out = compute_features(df)

    assert not out.isna().any().any(), "output must be NaN-free after fill"


def test_short_frame_does_not_crash():
    """A frame shorter than the longest (50-bar) window still returns all 11
    columns, NaN-free, with the same row count. Warmup NaNs are zero-filled."""
    df = _ohlcv(n=10)
    out = compute_features(df)

    assert list(out.columns) == FEATURE_COLS
    assert len(out) == 10
    assert not out.isna().any().any()
