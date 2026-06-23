# tests/signals/test_binance_futures_connector.py
import sys, pathlib, json, urllib.request, urllib.error, io
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from src.signals.binance_futures_connector import BinanceFuturesConnector


def _mock_resp(payload):
    m = MagicMock(); m.__enter__.return_value = m
    m.read.return_value = json.dumps(payload).encode(); return m


def test_set_leverage_posts_signed_request():
    conn = BinanceFuturesConnector("key", "secret", testnet=True)
    captured = {}
    def fake(req, timeout=None):
        captured["url"] = req.full_url; captured["headers"] = req.headers
        return _mock_resp({"leverage": 3})
    with patch("urllib.request.urlopen", side_effect=fake):
        out = conn.set_leverage("BTCUSDT", 3)
    assert out["leverage"] == 3
    assert "/fapi/v1/leverage" in captured["url"]
    assert "symbol=BTCUSDT" in captured["url"] and "leverage=3" in captured["url"]
    assert "signature=" in captured["url"]
    assert captured["headers"].get("X-mbx-apikey") == "key"


def test_set_margin_type_swallows_no_change_error():
    conn = BinanceFuturesConnector("key", "secret")
    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {},
            io.BytesIO(b'{"code":-4046,"msg":"No need to change margin type."}'))
    with patch("urllib.request.urlopen", side_effect=fake):
        out = conn.set_margin_type("BTCUSDT", "ISOLATED")
    assert out["msg"] == "no change needed"


def test_other_errors_surface():
    import pytest
    conn = BinanceFuturesConnector("key", "secret")
    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {},
            io.BytesIO(b'{"code":-10221,"msg":"timestamp"}'))
    with patch("urllib.request.urlopen", side_effect=fake):
        with pytest.raises(RuntimeError):
            conn.set_leverage("BTCUSDT", 3)


def test_open_long_uses_buy():
    conn = BinanceFuturesConnector("k", "s")
    cap = {}
    def fake(req, timeout=None):
        cap["url"] = req.full_url
        return _mock_resp({"orderId": "11"})
    with patch("urllib.request.urlopen", side_effect=fake):
        conn.open("BTCUSDT", "long", 0.01)
    assert "side=BUY" in cap["url"] and "quantity=0.01" in cap["url"]


def test_open_short_uses_sell():
    conn = BinanceFuturesConnector("k", "s")
    cap = {}
    def fake(req, timeout=None):
        cap["url"] = req.full_url
        return _mock_resp({"orderId": "12"})
    with patch("urllib.request.urlopen", side_effect=fake):
        conn.open("ETHUSDT", "short", 1.0)
    assert "side=SELL" in cap["url"]


def test_close_is_reduce_only_opposite():
    conn = BinanceFuturesConnector("k", "s")
    cap = {}
    def fake(req, timeout=None):
        cap["url"] = req.full_url
        return _mock_resp({"orderId": "13"})
    with patch("urllib.request.urlopen", side_effect=fake):
        conn.close("BTCUSDT", "long", 0.01)
    assert "side=SELL" in cap["url"] and "reduceOnly=true" in cap["url"]


def test_get_position_parses_and_flat_none():
    conn = BinanceFuturesConnector("k", "s")
    with patch("urllib.request.urlopen",
               side_effect=lambda req, timeout=None: _mock_resp([{"symbol":"BTCUSDT",
               "positionAmt":"0.5","entryPrice":"100","liquidationPrice":"67",
               "unRealizedProfit":"5","positionSide":"BOTH"}])):
        pos = conn.get_position("BTCUSDT")
    assert pos["qty"] == 0.5 and pos["liquidation_price"] == 67.0 and pos["side"] == "long"
    with patch("urllib.request.urlopen",
               side_effect=lambda req, timeout=None: _mock_resp([{"symbol":"BTCUSDT",
               "positionAmt":"0","entryPrice":"0.0","liquidationPrice":"0",
               "unRealizedProfit":"0","positionSide":"BOTH"}])):
        assert conn.get_position("BTCUSDT") is None


def test_get_price_mark():
    conn = BinanceFuturesConnector("k", "s")
    with patch("urllib.request.urlopen",
               side_effect=lambda req, timeout=None: _mock_resp({"markPrice": "101.5"})):
        assert conn.get_price("BTCUSDT") == 101.5
