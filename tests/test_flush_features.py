# tests/test_flush_features.py
import pandas as pd
from src.ml.flush_features import features_at_flush, FEATURE_COLUMNS
from backtest.mean_reversion.features import compute_features


def _bars(prices):
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="1s")
    return pd.DataFrame({"close": prices, "buy_vol": 1.0, "sell_vol": 1.0}, index=idx)


def test_feature_vector_has_all_columns_and_no_nan():
    bars = _bars([100.0] * 35 + [94.0])  # flush at last bar
    feats = compute_features(bars, bar="1s")
    vec = features_at_flush(bars, feats, idx=bars.index[-1])
    assert set(FEATURE_COLUMNS).issubset(vec.keys())
    for c in FEATURE_COLUMNS:
        assert pd.notna(vec[c]), f"{c} is NaN"


def test_volume_spike_and_rsi_are_sane():
    bars = _bars([100.0] * 35 + [94.0])
    feats = compute_features(bars, bar="1s")
    vec = features_at_flush(bars, feats, idx=bars.index[-1])
    assert 0.0 <= vec["rsi"] <= 100.0
    assert vec["volume_spike"] >= 0.0
