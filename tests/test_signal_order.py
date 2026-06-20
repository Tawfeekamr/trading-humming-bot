"""Tests for the signal-order bridge `_signal_order` (run_signal_listener.py).

Background: `signal_engine._execute_entry` passes the order amount as a `Decimal`
(`run_signal_listener` wires `buy_fn=lambda ...: _signal_order("BUY", symbol, amount, price)`
where `amount = Decimal(str(...))`). `_signal_order` builds a JSON body for the Rust
engine API; `json.dumps` cannot serialize `Decimal`, so every live signal buy raised
`Object of type Decimal is not JSON serializable` and silently failed (production
bug observed 2026-06-19/20: ZEC-USDT, XLM-USDT). These tests pin that a Decimal
amount serializes to a JSON number.
"""
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.run_signal_listener import _signal_order  # noqa: E402


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()


class TestSignalOrderSerialization:
    def _capture(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["url"] = req.full_url
            captured["body"] = json.loads(req.data)
            return _FakeResp({"orderId": "oid-123"})

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        return captured

    def test_decimal_amount_serializes_to_number(self, monkeypatch):
        """The regression: a Decimal amount must not crash json.dumps."""
        captured = self._capture(monkeypatch)
        oid = _signal_order("BUY", "XLM-USDT", Decimal("47.123456"), Decimal("0.20"))
        assert oid == "oid-123"
        # quantity must be a JSON number (float), not a Decimal that blew up dumps
        assert captured["body"]["quantity"] == 47.123456
        assert isinstance(captured["body"]["quantity"], float)

    def test_float_amount_still_serializes(self, monkeypatch):
        captured = self._capture(monkeypatch)
        _signal_order("BUY", "BTC-USDT", 0.5, 60000.0)
        assert captured["body"]["quantity"] == 0.5

    def test_symbol_normalized_and_reduce_only_for_sells(self, monkeypatch):
        captured = self._capture(monkeypatch)
        _signal_order("SELL", "ETH-USDT", Decimal("1.0"), Decimal("3000"))
        assert captured["body"]["symbol"] == "ETHUSDT"
        assert captured["body"]["side"] == "SELL"
        assert captured["body"]["reduce_only"] is True
        assert captured["body"]["client_order_id"].startswith("sig_ETH_USDT_")
