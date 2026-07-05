# src/rl/features.py
"""Market-feature computation for the RL observation space.

Computes the 8 production regime-classifier features (matching
``src/data/feature_engineering.py`` column-for-column so an RL policy sees the
same distribution the supervised classifier was trained on — verified against
``backtest/ml_walk_forward.py:20-23``) plus 3 cyclical time features for the
observation vector.

Input: OHLCV DataFrame indexed by UTC datetime (as produced by ``src/rl/data.py``).
Output: DataFrame with 11 feature columns on the same index. Warmup NaNs
(~first 30 bars) are forward-filled then zero-filled so the result is NaN-free
and episode-safe.

Note on formula provenance: three formulas differ from the task's English
summary but match the production code (both Python ``feature_engineering.py``
and Rust ``trading-engine-core/src/ml/features.rs`` agree):

* ``trend_strength`` uses ``(sma_20 - sma_50) / sma_50`` (not ``(close - sma_20) / sma_20``).
* ``atr_14`` uses a simple 14-bar rolling mean of true range (not Wilder's smoothing).
* ``close_location_value`` uses ``((close-low) - (high-close)) / (high-low)``
  (the symmetric intra-bar location, not ``(close-low) / (high-low)``).

Matching production is load-bearing: if the RL observation features diverge
from what the regime classifier was trained on, the classifier's outputs
become meaningless inside the env.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

# The 8 market features consumed by the supervised regime classifier
# (backtest/ml_walk_forward.py:20-23). Order matters: the trained classifier
# weights expect features in this exact column order.
MARKET_FEATURE_COLS: list[str] = [
    "returns",
    "volatility_14",
    "volatility_30",
    "normalized_atr",
    "trend_strength",
    "rsi_14",
    "volume_ratio",
    "close_location_value",
]

TIME_FEATURE_COLS: list[str] = ["hour_sin", "hour_cos", "day_of_week"]

FEATURE_COLS: list[str] = MARKET_FEATURE_COLS + TIME_FEATURE_COLS

# Epsilon matching src/data/feature_engineering.py — guards against div-by-zero
# on flat windows (e.g. zero volume, zero high-low spread).
_EPS = 1e-8


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute 8 market features + 3 time features from OHLCV bars.

    Input: DataFrame with columns [open, high, low, close, volume],
           indexed by UTC datetime.
    Output: DataFrame with 11 feature columns, same index.
            NaN rows (warmup) are forward-filled then zero-filled.
    """
    out = pd.DataFrame(index=df.index)
    close = df["close"]
    high = df["high"]
    low = df["low"]
    volume = df["volume"]

    # 1. returns ------------------------------------------------------------
    out["returns"] = close.pct_change()

    # 2/3. rolling volatility (std of returns) ------------------------------
    out["volatility_14"] = out["returns"].rolling(window=14).std()
    out["volatility_30"] = out["returns"].rolling(window=30).std()

    # 4. normalized_atr = ATR(14) / close. ATR is a *simple* 14-bar rolling
    #    mean of true range (matches production; not Wilder's smoothing). ---
    prev_close = close.shift(1)
    true_range = np.maximum(
        high - low,
        np.maximum(
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ),
    )
    atr_14 = true_range.rolling(window=14).mean()
    out["normalized_atr"] = atr_14 / (close + _EPS)

    # 5. trend_strength = (sma_20 - sma_50) / sma_50 ------------------------
    sma_20 = close.rolling(window=20).mean()
    sma_50 = close.rolling(window=50).mean()
    out["trend_strength"] = (sma_20 - sma_50) / (sma_50 + _EPS)

    # 6. RSI(14) — Wilder's smoothing (alpha = 1/14, adjust=False). --------
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = -delta.where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / (avg_loss + _EPS)
    out["rsi_14"] = 100 - (100 / (1 + rs))

    # 7. volume_ratio = volume / volume_sma_20 ------------------------------
    volume_sma_20 = volume.rolling(window=20).mean()
    out["volume_ratio"] = volume / (volume_sma_20 + _EPS)

    # 8. close_location_value — symmetric intra-bar location. --------------
    out["close_location_value"] = (
        ((close - low) - (high - close)) / (high - low + _EPS)
    )

    # 9-11. time features (cyclical hour encoding + raw ISO day-of-week). --
    idx = df.index
    hours = idx.hour  # DatetimeIndex guaranteed by src/rl/data.py contract
    out["hour_sin"] = np.sin(2 * np.pi * hours / 24)
    out["hour_cos"] = np.cos(2 * np.pi * hours / 24)
    out["day_of_week"] = idx.dayofweek  # Monday=0 .. Sunday=6

    # Replace +-inf (from div-by-zero on flat windows) with NaN, then
    # forward-fill warmup NaNs (first ~30 rows have no prior value to ffill
    # from, so those become 0 via the final fillna).
    out = out.replace([np.inf, -np.inf], np.nan)
    out = out.ffill().fillna(0.0)
    return out[FEATURE_COLS]
