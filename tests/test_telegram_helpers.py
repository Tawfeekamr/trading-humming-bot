"""Tests for telegram_commands helper functions — pure formatting + network fallback."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.notifications.telegram_commands import _fmt_price, _fmt_duration, _signal_price


class TestFmtPrice:
    def test_int_price(self):
        assert _fmt_price(436) == "436"

    def test_decimal_price(self):
        assert _fmt_price(0.198) == "0.198"

    def test_none_returns_question_mark(self):
        assert _fmt_price(None) == "?"


class TestFmtDuration:
    def test_seconds(self):
        assert _fmt_duration(30) == "30s"

    def test_minutes(self):
        assert _fmt_duration(90) == "1m"

    def test_hours(self):
        assert _fmt_duration(3700) == "1h1m"

    def test_days(self):
        assert _fmt_duration(90000) == "1d1h"


class TestSignalPriceFallback:
    def test_returns_zero_on_network_failure(self, monkeypatch):
        def boom(*a, **k):
            raise OSError("network down")
        monkeypatch.setattr("urllib.request.urlopen", boom)
        assert _signal_price("BTC-USDT") == 0.0
