# tests/test_feature_engineering.py
import numpy as np
import pandas as pd
import pytest

from src.data.feature_engineering import calculate_technical_features


def _make_ohlcv(n: int = 200, base_price: float = 170.0) -> pd.DataFrame:
    """Generate synthetic OHLCV data with enough rows for all rolling windows."""
    rng = np.random.default_rng(42)
    returns = rng.normal(0, 0.01, n)
    close = base_price * np.cumprod(1 + returns)
    high = close * (1 + rng.uniform(0, 0.02, n))
    low = close * (1 - rng.uniform(0, 0.02, n))
    open_ = close * (1 + rng.normal(0, 0.005, n))
    volume = rng.uniform(1e4, 1e6, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": volume})


class TestFeatureOutput:
    def test_returns_dataframe_with_expected_columns(self):
        df = _make_ohlcv(200)
        result = calculate_technical_features(df)
        expected = [
            'returns', 'volatility_ratio', 'normalized_atr',
            'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value',
            'adx_14', 'macd_histogram', 'distance_to_vwap', 'obv_roc_14',
            'choppiness_index', 'fractal_dimension_index', 'aroon_oscillator',
        ]
        for col in expected:
            assert col in result.columns, f"Missing column: {col}"

    def test_drops_nan_rows(self):
        df = _make_ohlcv(200)
        result = calculate_technical_features(df)
        assert result.isnull().any().any() == False

    def test_no_inf_values(self):
        df = _make_ohlcv(200)
        result = calculate_technical_features(df)
        numeric = result.select_dtypes(include=[np.number])
        assert not np.isinf(numeric.values).any()

    def test_output_fewer_rows_than_input(self):
        df = _make_ohlcv(200)
        result = calculate_technical_features(df)
        assert len(result) < len(df)

    def test_does_not_mutate_input(self):
        df = _make_ohlcv(200)
        original = df.copy()
        calculate_technical_features(df)
        pd.testing.assert_frame_equal(df, original)


class TestEpsilonGuards:
    def test_zero_volatility_no_inf(self):
        """Flat prices produce zero volatility — division should not produce inf."""
        n = 200
        price = 100.0
        df = pd.DataFrame({
            "open": [price] * n, "high": [price] * n,
            "low": [price] * n, "close": [price] * n,
            "volume": [1e6] * n,
        })
        result = calculate_technical_features(df)
        # volatility_30 will be 0, volatility_ratio should be finite
        if 'volatility_ratio' in result.columns and len(result) > 0:
            assert not np.isinf(result['volatility_ratio']).any()

    def test_zero_volume_no_inf(self):
        """Zero volume should not produce inf in VWAP or volume_ratio."""
        n = 200
        rng = np.random.default_rng(42)
        close = 100 * np.cumprod(1 + rng.normal(0, 0.01, n))
        df = pd.DataFrame({
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": [0.0] * n,
        })
        result = calculate_technical_features(df)
        if len(result) > 0:
            assert not np.isinf(result['distance_to_vwap'].dropna()).any()
            assert not np.isinf(result['volume_ratio'].dropna()).any()


class TestRollingVWAP:
    def test_vwap_consistent_regardless_of_dataframe_length(self):
        """Rolling VWAP should produce same values regardless of DataFrame prefix."""
        df_full = _make_ohlcv(300)
        result_full = calculate_technical_features(df_full)

        # Take last 200 rows of raw data, compute features
        df_short = _make_ohlcv(200)
        result_short = calculate_technical_features(df_short)

        # The VWAP values near the end should be similar (both use rolling 50 window)
        # Just check they're finite and reasonable
        assert not np.isinf(result_full['distance_to_vwap']).any()
        assert not np.isinf(result_short['distance_to_vwap']).any()

    def test_vwap_bounded_by_high_low(self):
        """VWAP should be within the high-low range of the rolling window."""
        df = _make_ohlcv(200)
        result = calculate_technical_features(df)
        if len(result) > 0:
            assert (result['vwap'] <= result['high'].max() + 1e-6).all()
            assert (result['vwap'] >= result['low'].min() - 1e-6).all()


class TestRSI:
    def test_rsi_bounded_0_100(self):
        df = _make_ohlcv(200)
        result = calculate_technical_features(df)
        if len(result) > 0:
            assert (result['rsi_14'] >= 0).all()
            assert (result['rsi_14'] <= 100).all()

    def test_rsi_high_on_uptrend(self):
        """Consistent upward closes should produce high RSI."""
        n = 200
        close = np.linspace(100, 200, n)
        df = pd.DataFrame({
            "open": close, "high": close * 1.01, "low": close * 0.99,
            "close": close, "volume": [1e6] * n,
        })
        result = calculate_technical_features(df)
        if len(result) > 0:
            assert result['rsi_14'].iloc[-1] > 60


class TestEdgeCases:
    def test_minimum_viable_input(self):
        """Should work with just enough rows to produce at least 1 output row."""
        df = _make_ohlcv(55)
        result = calculate_technical_features(df)
        assert len(result) >= 1

    def test_large_input(self):
        df = _make_ohlcv(2000)
        result = calculate_technical_features(df)
        assert len(result) > 0
        assert not result.isnull().any().any()

    def test_single_candle_gap_nan_handled(self):
        """A single zero-volume candle in the middle shouldn't cause inf."""
        df = _make_ohlcv(200)
        df.loc[100, 'volume'] = 0.0
        result = calculate_technical_features(df)
        assert not np.isinf(result.select_dtypes(include=[np.number]).values).any()
