"""Tests for the signal-order bridge `_signal_order` (run_signal_listener.py).

Background: `signal_engine._execute_entry` passes the order amount as a `Decimal`
(`run_signal_listener` wires `buy_fn=lambda ...: _signal_order("BUY", symbol, amount, price)`
where `amount = Decimal(str(...))`). `_signal_order` builds a JSON body for the Rust
engine API; `json.dumps` cannot serialize `Decimal`, so every live signal buy raised
`Object of type Decimal is not JSON serializable` and silently failed (production
bug observed 2026-06-19/20: ZEC-USDT, XLM-USDT). These tests pin that a Decimal
amount serializes to a JSON number.

Retry behavior (2026-10-02): the Rust engine 502s signal BUYs while the symbol's
book is price-suspect — which is exactly when channel signals fire (fast-moving
alts: HYPE/RENDER/ICP buys failed 2026-09-25..10-01 this way). A suspect block
rejects BEFORE the connector places anything, so one retry after a short delay is
provably safe. Every other failure (dropped engine response, timeout, transport)
leaves execution ambiguous and must NOT be retried (double-buy risk).
"""
import io
import json
import sys
import urllib.error
from decimal import Decimal
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import src.run_signal_listener as rsl  # noqa: E402
from src.run_signal_listener import _signal_order  # noqa: E402


@pytest.fixture(autouse=True)
def _engine_streams_everything(monkeypatch, request):
    """The HTTP-bridge tests below exercise the engine call, so treat every
    symbol as streamed unless a test opts out with the `real_streams` marker."""
    if "real_streams" not in request.keywords:
        monkeypatch.setattr(rsl, "_engine_streams", lambda symbol: True)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()


def _http_error_502(error_text):
    body = json.dumps({"error": error_text}).encode()
    return urllib.error.HTTPError(
        "http://rust-bot:3030/api/v1/order", 502, "Bad Gateway",
        {}, io.BytesIO(body))


class TestSignalOrderSerialization:
    def _capture(self, monkeypatch):
        captured = {}

        def fake_urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            if "/api/v1/capital" in url:
                return _FakeResp({"free_capital": 1_000_000.0, "total_equity": 1_000_000.0})
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


class TestSignalOrderRetry:
    """One retry on a price-suspect 502 (safe: rejected pre-execution);
    never on ambiguous failures (double-buy risk)."""

    def _patch(self, monkeypatch, outcomes):
        """outcomes: list of payloads/exceptions returned per ORDER-endpoint call.
        The BUY capital pre-check hits /api/v1/capital first — route it to a
        benign rich-wallet response so it never consumes an outcome."""
        calls = {"n": 0, "sleeps": []}

        def fake_urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            if "/api/v1/capital" in url:
                return _FakeResp({"free_capital": 1_000_000.0, "total_equity": 1_000_000.0})
            i = min(calls["n"], len(outcomes) - 1)
            outcome = outcomes[i]
            calls["n"] += 1
            if isinstance(outcome, Exception):
                raise outcome
            return _FakeResp(outcome)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        monkeypatch.setattr("time.sleep", lambda s: calls["sleeps"].append(s))
        return calls

    def test_price_suspect_block_retries_once_and_succeeds(self, monkeypatch):
        calls = self._patch(monkeypatch, [
            _http_error_502("order blocked: price suspect"),
            {"orderId": "oid-456"},
        ])
        oid = _signal_order("BUY", "HYPE-USDT", Decimal("1"), Decimal("90"))
        assert oid == "oid-456"
        assert calls["n"] == 2, "must retry the suspect block exactly once"
        assert calls["sleeps"] == [3.0], "must back off before the retry"

    def test_price_suspect_block_twice_returns_none(self, monkeypatch):
        calls = self._patch(monkeypatch, [
            _http_error_502("order blocked: price suspect"),
        ])
        oid = _signal_order("BUY", "HYPE-USDT", Decimal("1"), Decimal("90"))
        assert oid is None
        assert calls["n"] == 2, "second suspect block also gets its one retry"

    def test_dropped_response_is_not_retried(self, monkeypatch):
        """'engine dropped order response' means the engine may have executed
        the order after our timeout — retrying could double-buy."""
        calls = self._patch(monkeypatch, [_http_error_502("engine dropped order response")])
        oid = _signal_order("BUY", "HYPE-USDT", Decimal("1"), Decimal("90"))
        assert oid is None
        assert calls["n"] == 1, "ambiguous execution state must not be retried"
        assert calls["sleeps"] == []

    def test_transport_error_is_not_retried(self, monkeypatch):
        calls = self._patch(monkeypatch, [
            urllib.error.URLError("timed out"),
        ])
        oid = _signal_order("BUY", "HYPE-USDT", Decimal("1"), Decimal("90"))
        assert oid is None
        assert calls["n"] == 1
        assert calls["sleeps"] == []


class TestSignalOrderHardening:
    """2026-11-28 order-flow hardening: fail-closed capital check, and no ghost
    engine orders for coins the engine does not stream."""

    def _wallet(self, monkeypatch, free=None, error=None):
        calls = {"orders": 0}

        def fake_urlopen(req, timeout=None):
            url = req if isinstance(req, str) else req.full_url
            if "/api/v1/capital" in url:
                if error:
                    raise error
                return _FakeResp({"free_capital": free})
            calls["orders"] += 1
            return _FakeResp({"orderId": "oid-1"})

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        return calls

    def test_buy_refused_when_free_capital_is_zero(self, monkeypatch):
        calls = self._wallet(monkeypatch, free=0.0)
        assert _signal_order("BUY", "XRP-USDT", Decimal("10"), Decimal("1")) is None
        assert calls["orders"] == 0

    def test_buy_refused_when_capital_check_errors(self, monkeypatch):
        calls = self._wallet(monkeypatch, error=urllib.error.URLError("engine down"))
        assert _signal_order("BUY", "XRP-USDT", Decimal("10"), Decimal("1")) is None
        assert calls["orders"] == 0

    def test_buy_refused_when_notional_exceeds_free_capital(self, monkeypatch):
        calls = self._wallet(monkeypatch, free=5.0)
        assert _signal_order("BUY", "XRP-USDT", Decimal("10"), Decimal("1")) is None
        assert calls["orders"] == 0

    def test_buy_within_free_capital_is_sent(self, monkeypatch):
        calls = self._wallet(monkeypatch, free=1_000.0)
        assert _signal_order("BUY", "XRP-USDT", Decimal("10"), Decimal("1")) == "oid-1"
        assert calls["orders"] == 1

    @pytest.mark.real_streams
    def test_unstreamed_coin_is_simulated_not_sent(self, monkeypatch):
        monkeypatch.setattr(rsl, "_ENGINE_PAIRS", {"XRPUSDT", "BNBUSDT"})
        calls = self._wallet(monkeypatch, free=1_000_000.0)
        oid = _signal_order("BUY", "ENA-USDT", Decimal("100"), Decimal("0.1"))
        assert oid.startswith("sim_ENA_USDT_") and calls["orders"] == 0
        sell = _signal_order("SELL", "ENA-USDT", Decimal("100"), Decimal("0.1"))
        assert sell.startswith("sim_ENA_USDT_") and calls["orders"] == 0

    @pytest.mark.real_streams
    def test_engine_streams_reads_enabled_pairs_from_config(self, monkeypatch):
        monkeypatch.setattr(rsl, "_ENGINE_PAIRS", None)
        monkeypatch.setattr(rsl, "load_config", lambda: {"pairs": [
            {"symbol": "XRP-USDT", "enabled": True}, {"symbol": "BTC-USDT", "enabled": False}]})
        assert rsl._engine_streams("XRPUSDT") and rsl._engine_streams("XRP-USDT")
        assert not rsl._engine_streams("BTC-USDT")
        assert not rsl._engine_streams("ENA-USDT")

