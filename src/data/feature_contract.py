"""Canonical feature-column contract for regime ML and PPO routing.

The supervised regime classifier and the RL observation builder both consume
the same 14 market features in this exact order. Keep the ordered list here so
model training, live inference, signal decision-state capture, and tests cannot
drift silently.
"""
from __future__ import annotations

FEATURE_SCHEMA_VERSION = 1

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


def assert_market_feature_contract(
    columns: list[str] | tuple[str, ...] | None,
) -> None:
    """Raise if ``columns`` does not exactly match the contract."""
    if list(columns or []) != MARKET_FEATURE_COLS:
        raise ValueError(
            "feature contract mismatch: expected "
            f"{MARKET_FEATURE_COLS}, got {list(columns or [])}"
        )
