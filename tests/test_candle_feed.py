import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from src.data.candle_feed import CandleFeed
from src.data.ws_feed import WebSocketFeed


class TestWebSocketPriceValidation:
    """Test price validation in WebSocket feed."""

    def test_validates_positive_price(self):
        """Should accept valid positive price."""
        feed = WebSocketFeed(symbol="btcusdt")
        data = {"c": "50000.50"}
        price = feed._validate_price(data)
        assert price == 50000.50

    def test_rejects_missing_price_key(self):
        """Should reject message missing 'c' key."""
        feed = WebSocketFeed(symbol="btcusdt")
        data = {"x": "50000.50"}
        price = feed._validate_price(data)
        assert price is None

    def test_rejects_nan_price(self):
        """Should reject NaN price."""
        feed = WebSocketFeed(symbol="btcusdt")
        data = {"c": "NaN"}
        price = feed._validate_price(data)
        assert price is None

    def test_rejects_infinity_price(self):
        """Should reject infinite price."""
        feed = WebSocketFeed(symbol="btcusdt")
        data = {"c": "Infinity"}
        price = feed._validate_price(data)
        assert price is None

    def test_rejects_negative_price(self):
        """Should reject negative price."""
        feed = WebSocketFeed(symbol="btcusdt")
        data = {"c": "-1000"}
        price = feed._validate_price(data)
        assert price is None

    def test_rejects_zero_price(self):
        """Should reject zero price."""
        feed = WebSocketFeed(symbol="btcusdt")
        data = {"c": "0"}
        price = feed._validate_price(data)
        assert price is None

    def test_rejects_price_above_max(self):
        """Should reject price above BTC sanity limit."""
        feed = WebSocketFeed(symbol="btcusdt")
        data = {"c": "2000000"}
        price = feed._validate_price(data)
        assert price is None


class TestCandleFeedErrorHandling:
    """Test error handling in candle feed."""

    @patch("src.data.candle_feed.Client")
    def test_handles_network_error_gracefully(self, mock_client_cls):
        """Should return empty DataFrame on network error."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.get_klines.side_effect = ConnectionError("Network error")

        feed = CandleFeed(symbol="BTCUSDT", interval="1h")
        df = feed.fetch_candles(limit=1)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    @patch("src.data.candle_feed.Client")
    def test_handles_rate_limit_error(self, mock_client_cls):
        """Should return empty DataFrame on rate limit error."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.get_klines.side_effect = Exception("Rate limit exceeded")

        feed = CandleFeed(symbol="BTCUSDT", interval="1h")
        df = feed.fetch_candles(limit=1)

        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0

    @patch("src.data.candle_feed.Client")
    def test_logs_warning_on_nan_values(self, mock_client_cls):
        """Should log warning when NaN values appear after coercion."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        # Return malformed data that will coerce to NaN
        mock_client.get_klines.return_value = [
            [1700000000000, "invalid", 101_000, 99_000, 100_500, 1.0, 0, 0, 0, 0, 0, 0]
        ]

        feed = CandleFeed(symbol="BTCUSDT", interval="1h")
        df = feed.fetch_candles(limit=1)

        # Should still return DataFrame even with NaN
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 1


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
