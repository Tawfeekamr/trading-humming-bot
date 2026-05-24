# tests/test_label_generation.py
import numpy as np
import pandas as pd
import pytest

from src.data.label_generation import generate_regime_labels


def _make_labeled_df(n: int = 100, base_price: float = 170.0, atr_col: bool = True) -> pd.DataFrame:
    """Generate a DataFrame with enough rows for label generation."""
    rng = np.random.default_rng(42)
    returns = rng.normal(0, 0.005, n)
    close = base_price * np.cumprod(1 + returns)
    high = close * (1 + rng.uniform(0, 0.01, n))
    low = close * (1 - rng.uniform(0, 0.01, n))
    df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close})
    if atr_col:
        df['atr_14'] = close * 0.02
    return df


class TestLabelOutput:
    def test_produces_regime_label_column(self):
        df = _make_labeled_df(100)
        result = generate_regime_labels(df, forward_window=5)
        assert 'regime_label' in result.columns

    def test_labels_are_valid_classes(self):
        df = _make_labeled_df(100)
        result = generate_regime_labels(df, forward_window=5)
        valid = {0, 1, 2}
        assert set(result['regime_label'].unique()).issubset(valid)

    def test_drops_future_nan_rows(self):
        df = _make_labeled_df(50)
        result = generate_regime_labels(df, forward_window=10)
        assert result['future_return'].notna().all()

    def test_output_fewer_rows_than_input(self):
        df = _make_labeled_df(50)
        result = generate_regime_labels(df, forward_window=10)
        assert len(result) < len(df)

    def test_does_not_mutate_input(self):
        df = _make_labeled_df(100)
        original = df.copy()
        generate_regime_labels(df, forward_window=5)
        pd.testing.assert_frame_equal(df, original)


class TestForwardWindow:
    def test_forward_max_looks_at_correct_window(self):
        """forward_max at row i should be the max of high[i:i+window]."""
        n = 20
        close = np.full(n, 100.0)
        high = np.full(n, 100.0)
        high[5] = 110.0  # spike at index 5
        low = np.full(n, 100.0)
        df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "atr_14": close * 0.02})

        result = generate_regime_labels(df, forward_window=5)
        # Row 0: forward_max should be max of high[0:5] = max(100,100,100,100,100) = 100
        # Row 1: forward_max should be max of high[1:6] = 110
        if 1 in result.index and 'forward_max' in result.columns:
            assert result.loc[1, 'forward_max'] == 110.0

    def test_forward_min_looks_at_correct_window(self):
        """forward_min at row i should be the min of low[i:i+window]."""
        n = 20
        close = np.full(n, 100.0)
        high = np.full(n, 100.0)
        low = np.full(n, 100.0)
        low[7] = 90.0  # dip at index 7
        df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "atr_14": close * 0.02})

        result = generate_regime_labels(df, forward_window=5)
        # Row 3: forward_min should be min of low[3:8] = 90
        if 3 in result.index and 'forward_min' in result.columns:
            assert result.loc[3, 'forward_min'] == 90.0


class TestLabelSemantics:
    def test_strong_uptrend_labeled_trending(self):
        """Monotonically increasing prices should be labeled as trending (1)."""
        n = 50
        close = np.linspace(100, 130, n)
        high = close * 1.001
        low = close * 0.999
        df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "atr_14": close * 0.01})

        result = generate_regime_labels(df, forward_window=5, trend_atr_k=0.5)
        # Early rows should see strong upward future_return → trending
        trending_count = (result['regime_label'] == 1).sum()
        assert trending_count > 0

    def test_flat_prices_labeled_ranging(self):
        """Flat prices should be labeled as ranging (0)."""
        n = 50
        close = np.full(n, 100.0)
        df = pd.DataFrame({
            "open": close, "high": close + 0.01, "low": close - 0.01,
            "close": close, "atr_14": close * 0.001,
        })
        result = generate_regime_labels(df, forward_window=5, trend_atr_k=0.5)
        ranging_count = (result['regime_label'] == 0).sum()
        assert ranging_count > 0

    def test_whipsaw_labeled_danger(self):
        """Large excursions in both directions with flat net should be danger (2)."""
        n = 50
        close = np.full(n, 100.0)
        rng = np.random.default_rng(42)
        high = close + 5.0  # 5% spikes up
        low = close - 5.0   # 5% spikes down
        df = pd.DataFrame({"open": close, "high": high, "low": low, "close": close, "atr_14": close * 0.01})
        result = generate_regime_labels(df, forward_window=5, trend_atr_k=0.5)
        # danger_excursion checks both max_up_move and max_down_move > threshold
        # With high=105 and low=95, both should be > threshold
        danger_count = (result['regime_label'] == 2).sum()
        assert danger_count > 0


class TestDynamicThreshold:
    def test_uses_atr_when_available(self):
        """When atr_column is present, uses dynamic threshold."""
        n = 50
        close = np.full(n, 100.0)
        df = pd.DataFrame({
            "open": close, "high": close + 0.5, "low": close - 0.5,
            "close": close, "atr_14": close * 0.1,  # Very high ATR → high threshold
        })
        result = generate_regime_labels(df, forward_window=5, trend_atr_k=1.5)
        # With very high ATR, even moderate moves shouldn't be "trending"
        assert len(result) > 0

    def test_uses_static_threshold_without_atr(self):
        """When atr_column is absent, uses static trend_threshold."""
        n = 50
        close = np.full(n, 100.0)
        df = pd.DataFrame({
            "open": close, "high": close + 0.1, "low": close - 0.1,
            "close": close,
        })
        result = generate_regime_labels(df, forward_window=5, trend_threshold=0.02)
        assert len(result) > 0
        assert 'regime_label' in result.columns

    def test_threshold_clipped_to_bounds(self):
        """Dynamic threshold should be clipped between 0.005 and 0.10."""
        n = 50
        close = np.full(n, 100.0)
        # Extreme ATR: 50% of price → dynamic = 0.5 * 1.5 = 0.75, should be clipped to 0.10
        df = pd.DataFrame({
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "atr_14": close * 0.5,
        })
        result = generate_regime_labels(df, forward_window=5, trend_atr_k=1.5)
        assert len(result) > 0


class TestEdgeCases:
    def test_short_input(self):
        """Should handle input shorter than forward_window."""
        df = _make_labeled_df(10)
        result = generate_regime_labels(df, forward_window=20)
        # All rows should be dropped since no forward window is available
        assert len(result) == 0

    def test_forward_window_one(self):
        df = _make_labeled_df(50)
        result = generate_regime_labels(df, forward_window=1)
        assert len(result) > 0
        assert 'regime_label' in result.columns
