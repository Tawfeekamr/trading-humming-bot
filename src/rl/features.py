# src/rl/features.py
"""Market-feature computation for the RL observation space.

Computes the 14 production regime-classifier features (matching
``src/data/feature_engineering.py`` column-for-column so an RL policy sees the
same distribution the supervised classifier was trained on) plus 3 cyclical time
features for the observation vector.

Input: OHLCV DataFrame indexed by UTC datetime (as produced by ``src/rl/data.py``).
Output: DataFrame with 17 feature columns on the same index. Warmup NaNs
(~first 50 bars) are forward-filled then zero-filled so the result is NaN-free
and episode-safe.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.data.feature_engineering import calculate_technical_features

# The 14 market features consumed by the supervised regime classifier.
# Order matters: the trained classifier weights expect features in this exact column order.
MARKET_FEATURE_COLS: list[str] = [
    "returns",
    "volatility_ratio",
    "normalized_atr",
    "trend_strength",
    "rsi_14",
    "volume_ratio",
    "close_location_value",
    "adx_14",
    "macd_histogram",
    "distance_to_vwap",
    "obv_roc_14",
    "choppiness_index",
    "fractal_dimension_index",
    "aroon_oscillator",
]

TIME_FEATURE_COLS: list[str] = ["hour_sin", "hour_cos", "day_of_week"]

FEATURE_COLS: list[str] = MARKET_FEATURE_COLS + TIME_FEATURE_COLS


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute 14 market features + 3 time features from OHLCV bars.

    Input: DataFrame with columns [open, high, low, close, volume],
           indexed by UTC datetime.
    Output: DataFrame with 17 feature columns, same index.
            NaN rows (warmup) are forward-filled then zero-filled.
    """
    # Use the unified feature engineering module
    out = calculate_technical_features(df)

    # Re-index to ensure alignment with the original df in case calculate_technical_features drops rows
    out = out.reindex(df.index)

    # Add time features (cyclical hour encoding + raw ISO day-of-week). --
    idx = df.index
    hours = idx.hour  # DatetimeIndex guaranteed by src/rl/data.py contract
    out["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    out["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    out["day_of_week"] = idx.dayofweek  # Monday=0 .. Sunday=6

    # Replace +-inf (from div-by-zero on flat windows) with NaN, then
    # forward-fill warmup NaNs (first ~50 rows have no prior value to ffill
    # from, so those become 0 via the final fillna).
    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.ffill().fillna(0.0)

    return out[FEATURE_COLS]
