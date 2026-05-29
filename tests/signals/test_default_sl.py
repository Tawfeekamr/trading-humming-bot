"""Tests for ATR-based default stop-loss in SignalEngine."""
import pytest
from unittest.mock import MagicMock, patch
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence
from src.signals.signal_engine import SignalEngine


def _make_engine(config=None):
    """Create a SignalEngine with mocked dependencies."""
    cfg = config or {
        "enabled": True,
        "audit_mode": True,
        "default_sl_atr_multiplier": 2.0,
        "max_sl_distance_pct": 10.0,
        "session_name": "test",
    }
    engine = SignalEngine(
        config=cfg,
        btc_regime_fn=lambda: ("RANGING", 0.5, 0.0),
        telegram_send_fn=lambda msg: None,
        buy_fn=lambda **kw: "order-123",
        sell_fn=lambda **kw: "order-456",
        get_price_fn=lambda symbol: 60.0,
    )
    return engine


def _make_signal(pair="HYPE-USDT", stop_loss=None, entry_low=None, entry_high=None):
    """Create a minimal OPEN_LONG signal."""
    return ParsedSignal(
        action=SignalAction.OPEN_LONG,
        pair=pair,
        entry_low=entry_low,
        entry_high=entry_high,
        stop_loss=stop_loss,
        take_profits=[65.0, 68.0],
        confidence=SignalConfidence.HIGH,
        quality_score=7,
    )


class TestFetchATR:
    """Test the _fetch_atr method."""

    @patch("src.signals.signal_engine.urllib.request.urlopen")
    def test_compute_atr_from_candles(self, mock_urlopen):
        """ATR should be computed from Gate.io candlestick data."""
        # Mock 15 candlesticks: [timestamp, volume, close, high, low, amount]
        candles = []
        for i in range(15):
            candles.append([
                f"170000000{i}", "100",  # timestamp, volume
                str(60 + i * 0.5),        # close
                str(61 + i * 0.5),        # high
                str(59 + i * 0.5),        # low
                "1000",                    # amount
            ])
        mock_resp = MagicMock()
        mock_resp.read.return_value = __import__("json").dumps(candles).encode()
        mock_resp.__enter__ = lambda s: mock_resp
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        engine = _make_engine()
        atr = engine._fetch_atr("HYPE-USDT")

        assert atr > 0, "ATR should be positive with valid candle data"

    @patch("src.signals.signal_engine.urllib.request.urlopen")
    def test_atr_returns_zero_on_error(self, mock_urlopen):
        """ATR should return 0 when API call fails."""
        mock_urlopen.side_effect = Exception("network error")

        engine = _make_engine()
        atr = engine._fetch_atr("FAKE-USDT")

        assert atr == 0.0


class TestFillDefaultSL:
    """Test the _fill_default_sl method."""

    @patch.object(SignalEngine, "_fetch_atr", return_value=1.5)
    def test_fills_sl_when_missing(self, mock_atr):
        """Should set SL = entry - ATR × multiplier when signal has no SL."""
        engine = _make_engine()
        signal = _make_signal(entry_low=60.0, entry_high=60.0)

        engine._fill_default_sl(signal, connector=None)

        assert signal.stop_loss is not None
        # SL = 60.0 - (1.5 * 2.0) = 57.0
        assert signal.stop_loss == 57.0

    @patch.object(SignalEngine, "_fetch_atr", return_value=1.5)
    def test_uses_market_price_when_no_entry(self, mock_atr):
        """Should use current market price when signal has no entry."""
        engine = _make_engine({"default_sl_atr_multiplier": 2.0, "max_sl_distance_pct": 10.0})
        signal = _make_signal(entry_low=None, entry_high=None)
        # get_price_fn returns 60.0

        engine._fill_default_sl(signal, connector=None)

        assert signal.stop_loss is not None
        assert signal.entry_low == 60.0  # Set from market price
        assert signal.is_market_entry is True
        # SL = 60.0 - (1.5 * 2.0) = 57.0
        assert signal.stop_loss == 57.0

    @patch.object(SignalEngine, "_fetch_atr", return_value=0.0)
    def test_does_nothing_when_atr_unavailable(self, mock_atr):
        """Should not modify signal when ATR can't be computed."""
        engine = _make_engine()
        signal = _make_signal(entry_low=60.0, entry_high=60.0)

        engine._fill_default_sl(signal, connector=None)

        assert signal.stop_loss is None  # Unchanged

    @patch.object(SignalEngine, "_fetch_atr", return_value=10.0)
    def test_caps_at_max_sl_distance(self, mock_atr):
        """SL should be capped at max_sl_distance_pct when ATR-based SL is too wide."""
        engine = _make_engine({"default_sl_atr_multiplier": 2.0, "max_sl_distance_pct": 10.0})
        signal = _make_signal(entry_low=60.0, entry_high=60.0)

        engine._fill_default_sl(signal, connector=None)

        assert signal.stop_loss is not None
        # ATR×2 = 20 → SL would be 40, but max 10% of 60 = 54
        assert signal.stop_loss == 54.0

    def test_skips_when_signal_already_has_sl(self):
        """Should not modify signal that already has a stop-loss."""
        engine = _make_engine()
        signal = _make_signal(entry_low=60.0, entry_high=60.0, stop_loss=55.0)

        engine._fill_default_sl(signal, connector=None)

        assert signal.stop_loss == 55.0  # Unchanged

    def test_skips_when_signal_has_no_pair(self):
        """Should not modify signal without a pair."""
        engine = _make_engine()
        signal = _make_signal(pair=None, entry_low=60.0, entry_high=60.0)

        engine._fill_default_sl(signal, connector=None)

        assert signal.stop_loss is None  # Unchanged
