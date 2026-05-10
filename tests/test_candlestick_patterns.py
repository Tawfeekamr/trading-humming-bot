import pytest
import pandas as pd
from src.trend.candlestick_patterns import CandlestickPatterns


def make_candles(data: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(data)


class TestCandlestickPatterns:
    def test_hammer_detected(self):
        df = make_candles([
            {"open": 100.0, "high": 100.2, "low": 95.0, "close": 99.5},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert "hammer" in [r["name"] for r in result]

    def test_bullish_engulfing_detected(self):
        df = make_candles([
            {"open": 101.0, "high": 101.5, "low": 99.0, "close": 99.5},
            {"open": 99.0, "high": 102.0, "low": 98.5, "close": 101.5},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert "bullish_engulfing" in [r["name"] for r in result]

    def test_bullish_harami_detected(self):
        df = make_candles([
            {"open": 102.0, "high": 102.5, "low": 98.0, "close": 98.5},
            {"open": 99.0, "high": 100.5, "low": 98.5, "close": 100.0},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert "bullish_harami" in [r["name"] for r in result]

    def test_morning_star_detected(self):
        df = make_candles([
            {"open": 102.0, "high": 102.5, "low": 98.0, "close": 98.5},
            {"open": 98.5, "high": 99.5, "low": 97.5, "close": 99.0},
            {"open": 99.0, "high": 103.0, "low": 98.5, "close": 102.5},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert "morning_star" in [r["name"] for r in result]

    def test_bullish_marubozu_detected(self):
        df = make_candles([
            {"open": 95.0, "high": 100.0, "low": 94.8, "close": 100.0},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert "bullish_marubozu" in [r["name"] for r in result]

    def test_no_pattern_returns_empty(self):
        df = make_candles([
            {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert result == []

    def test_result_structure(self):
        df = make_candles([
            {"open": 100.0, "high": 101.0, "low": 95.0, "close": 99.5},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        for r in result:
            assert "name" in r
            assert "type" in r
            assert r["type"] == "bullish"
            assert "signal" in r
            assert r["signal"] == "bull"

    def test_insufficient_data(self):
        df = pd.DataFrame({"open": [], "high": [], "low": [], "close": []})
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert result == []

    def test_bearish_patterns_not_returned(self):
        df = make_candles([
            {"open": 100.0, "high": 105.0, "low": 99.5, "close": 100.5},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        names = [r["name"] for r in result]
        assert "shooting_star" not in names
