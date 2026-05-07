import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from src.data.candle_feed import CandleFeed


class TestCandleFeed:
    @patch("src.data.candle_feed.Client")
    def test_fetch_candles_returns_dataframe(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.get_klines.return_value = [
            [0, 100_000, 101_000, 99_000, 100_500, 1.0, 0, 0, 0, 0, 0, 0]
        ]
        feed = CandleFeed(symbol="BTCUSDT", interval="1h")
        df = feed.fetch_candles(limit=1)
        assert isinstance(df, pd.DataFrame)
        assert "open" in df.columns
        assert "high" in df.columns
        assert "low" in df.columns
        assert "close" in df.columns

    @patch("src.data.candle_feed.Client")
    def test_candle_columns_correct(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.get_klines.return_value = [
            [1700000000000, 100_000, 101_000, 99_000, 100_500, 1.0, 0, 0, 0, 0, 0, 0]
        ]
        feed = CandleFeed(symbol="BTCUSDT", interval="1h")
        df = feed.fetch_candles(limit=1)
        assert len(df) == 1
        assert df.iloc[0]["close"] == 100_500.0
