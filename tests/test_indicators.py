import numpy as np
import pandas as pd
import pytest

from src.indicators.bollinger import BollingerBands
from src.indicators.rsi import RSI
from src.indicators.ema import EMA
from src.indicators.atr import ATR


@pytest.fixture
def sample_candles():
    np.random.seed(42)
    base = 100_000.0
    noise = np.random.normal(0, 500, 30)
    closes = pd.Series(base + noise)
    return closes


@pytest.fixture
def sample_ohlcv():
    np.random.seed(42)
    n = 30
    base = 100_000.0
    highs = pd.Series(base + np.random.uniform(100, 1000, n))
    lows = pd.Series(base - np.random.uniform(100, 1000, n))
    closes = pd.Series(base + np.random.normal(0, 300, n))
    return highs, lows, closes


class TestBollingerBands:
    def test_calculate_returns_upper_mid_lower(self, sample_candles):
        bb = BollingerBands(period=20, std_dev=2.0)
        result = bb.calculate(sample_candles)
        assert hasattr(result, "upper")
        assert hasattr(result, "mid")
        assert hasattr(result, "lower")

    def test_upper_greater_than_mid_greater_than_lower(self, sample_candles):
        bb = BollingerBands(period=20, std_dev=2.0)
        result = bb.calculate(sample_candles)
        assert result.upper > result.mid > result.lower

    def test_insufficient_data_returns_none(self):
        bb = BollingerBands(period=20, std_dev=2.0)
        closes = pd.Series([100_000, 101_000, 99_000])
        result = bb.calculate(closes)
        assert result is None

    def test_mid_equals_sma(self, sample_candles):
        bb = BollingerBands(period=20, std_dev=2.0)
        result = bb.calculate(sample_candles)
        expected_mid = sample_candles.iloc[-20:].mean()
        assert abs(result.mid - expected_mid) < 0.01


class TestRSI:
    def test_calculate_returns_float(self, sample_candles):
        rsi = RSI(period=14)
        result = rsi.calculate(sample_candles)
        assert isinstance(result, float)

    def test_rsi_between_0_and_100(self, sample_candles):
        rsi = RSI(period=14)
        result = rsi.calculate(sample_candles)
        assert 0 <= result <= 100

    def test_insufficient_data_returns_none(self):
        rsi = RSI(period=14)
        closes = pd.Series([100.0, 101.0, 99.0])
        result = rsi.calculate(closes)
        assert result is None

    def test_all_gains_rsi_100(self):
        closes = pd.Series(range(1, 20), dtype=float)
        rsi = RSI(period=14)
        result = rsi.calculate(closes)
        assert result == 100.0


class TestEMA:
    def test_calculate_returns_float(self, sample_candles):
        ema = EMA(period=20)
        result = ema.calculate(sample_candles)
        assert isinstance(result, float)

    def test_insufficient_data_returns_none(self):
        ema = EMA(period=20)
        closes = pd.Series([100.0] * 5)
        result = ema.calculate(closes)
        assert result is None

    def test_ema_smoothing_less_than_last_close(self, sample_candles):
        ema = EMA(period=20)
        result = ema.calculate(sample_candles)
        assert isinstance(result, float)

    def test_constant_price_returns_same(self):
        closes = pd.Series([50_000.0] * 250)
        ema = EMA(period=200)
        result = ema.calculate(closes)
        assert abs(result - 50_000.0) < 0.01


class TestATR:
    def test_calculate_returns_float(self, sample_ohlcv):
        highs, lows, closes = sample_ohlcv
        atr = ATR(period=14)
        result = atr.calculate(highs, lows, closes)
        assert isinstance(result, float)

    def test_atr_positive(self, sample_ohlcv):
        highs, lows, closes = sample_ohlcv
        atr = ATR(period=14)
        result = atr.calculate(highs, lows, closes)
        assert result > 0

    def test_grid_spacing_calculation(self, sample_ohlcv):
        highs, lows, closes = sample_ohlcv
        atr = ATR(period=14, spacing_multiplier=0.8)
        result = atr.calculate(highs, lows, closes)
        spacing = atr.grid_spacing(result)
        assert spacing == result * 0.8

    def test_insufficient_data_returns_none(self):
        highs = pd.Series([101_000.0, 100_500.0])
        lows = pd.Series([99_000.0, 99_500.0])
        closes = pd.Series([100_000.0, 100_200.0])
        atr = ATR(period=14)
        result = atr.calculate(highs, lows, closes)
        assert result is None
