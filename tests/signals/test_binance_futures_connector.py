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
            io.BytesIO(b'{"code":-1021,"msg":"timestamp"}'))
    with patch("urllib.request.urlopen", side_effect=fake):
        with pytest.raises(RuntimeError):
            conn.set_leverage("BTCUSDT", 3)
