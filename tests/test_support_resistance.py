import pytest
import pandas as pd
import numpy as np
from src.trend.support_resistance import SupportResistance


@pytest.fixture
def sample_candles():
    """50 candles with clear support at ~90 and resistance at ~100."""
    closes = []
    highs = []
    lows = []
    for i in range(50):
        phase = (i % 10)
        if phase < 5:
            price = 90.0 + phase * 2.0
        else:
            price = 100.0 - (phase - 5) * 2.0
        closes.append(price)
        highs.append(price + 0.5)
        lows.append(price - 0.5)
    return pd.DataFrame({
        "high": highs,
        "low": lows,
        "close": closes,
    })


class TestSupportResistance:
    def test_detect_levels_returns_list(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        assert isinstance(levels, list)

    def test_level_structure(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        for level in levels:
            assert "price" in level
            assert "type" in level
            assert "touches" in level
            assert "strength" in level
            assert level["type"] in ("support", "resistance")
            assert level["touches"] >= 2
            assert isinstance(level["price"], float)

    def test_support_below_current_price(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        current_price = sample_candles["close"].iloc[-1]
        supports = [l for l in levels if l["type"] == "support"]
        for s in supports:
            assert s["price"] <= current_price * 1.01

    def test_resistance_above_current_price(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        current_price = sample_candles["close"].iloc[-1]
        resistances = [l for l in levels if l["type"] == "resistance"]
        for r in resistances:
            assert r["price"] >= current_price * 0.99

    def test_cluster_nearby_pivots(self):
        sr = SupportResistance(cluster_pct=0.005)
        closes = [100.0] * 50
        lows = [99.0 + (i % 5) * 0.1 for i in range(50)]
        highs = [101.0 + (i % 5) * 0.1 for i in range(50)]
        df = pd.DataFrame({"high": highs, "low": lows, "close": closes})
        levels = sr.detect(df)
        support_prices = [l["price"] for l in levels if l["type"] == "support"]
        for i, p1 in enumerate(support_prices):
            for p2 in support_prices[i + 1:]:
                assert abs(p1 - p2) / min(p1, p2) > 0.004

    def test_nearest_support(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        support = sr.nearest_support(levels, 95.0)
        if support is not None:
            assert support["type"] == "support"
            assert support["price"] <= 95.0

    def test_nearest_support_returns_none_when_empty(self):
        sr = SupportResistance()
        assert sr.nearest_support([], 95.0) is None

    def test_nearest_resistance(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        resistance = sr.nearest_resistance(levels, 95.0)
        if resistance is not None:
            assert resistance["type"] == "resistance"
            assert resistance["price"] >= 95.0

    def test_empty_dataframe(self):
        sr = SupportResistance()
        df = pd.DataFrame({"high": [], "low": [], "close": []})
        levels = sr.detect(df)
        assert levels == []

    def test_insufficient_data(self):
        sr = SupportResistance()
        df = pd.DataFrame({"high": [100], "low": [99], "close": [99.5]})
        levels = sr.detect(df)
        assert levels == []
