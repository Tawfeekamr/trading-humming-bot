# tests/signals/test_paper_futures_connector.py
import sys
import pathlib
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from src.signals.paper_futures_connector import PaperFuturesConnector


def _mock_resp(payload):
    m = MagicMock()
    m.__enter__.return_value = m
    m.read.return_value = json.dumps(payload).encode()
    return m


def test_get_price_maps_symbol_and_reads_mark():
    conn = PaperFuturesConnector()
    captured = {}

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_resp([{"contract": "ICP_USDT", "mark_price": "2.17", "last": "2.18"}])

    with patch("urllib.request.urlopen", side_effect=fake):
        assert conn.get_price("ICP-USDT") == 2.17
    assert "contract=ICP_USDT" in captured["url"]
    assert "futures/usdt/tickers" in captured["url"]


def test_get_price_falls_back_to_last_when_no_mark():
    conn = PaperFuturesConnector()
    with patch("urllib.request.urlopen",
               side_effect=lambda req, timeout=None:
                   _mock_resp([{"contract": "FET_USDT", "last": "0.18"}])):
        assert conn.get_price("FET-USDT") == 0.18


def test_get_price_maps_btc_dash_to_underscore():
    conn = PaperFuturesConnector()
    captured = {}

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_resp([{"contract": "BTC_USDT", "mark_price": "60000", "last": "60001"}])

    with patch("urllib.request.urlopen", side_effect=fake):
        assert conn.get_price("BTC-USDT") == 60000.0
    assert "contract=BTC_USDT" in captured["url"]


def test_get_price_returns_zero_on_empty_or_transport_error():
    conn = PaperFuturesConnector()
    # Empty payload (unknown contract) -> 0.0, no raise.
    with patch("urllib.request.urlopen",
               side_effect=lambda req, timeout=None: _mock_resp([])):
        assert conn.get_price("NOPE-USDT") == 0.0
    # Transport failure -> 0.0, no raise.
    def boom(req, timeout=None):
        raise OSError("network down")
    with patch("urllib.request.urlopen", side_effect=boom):
        assert conn.get_price("ICP-USDT") == 0.0


def test_set_leverage_and_margin_type_are_noops():
    conn = PaperFuturesConnector()
    assert conn.set_leverage("BTC-USDT", 3) == {"msg": "paper"}
    assert conn.set_margin_type("BTC-USDT", "ISOLATED") == {"msg": "paper"}


def test_open_returns_unique_synthetic_order_ids():
    conn = PaperFuturesConnector()
    a = conn.open("ICP-USDT", "long", 100.0)
    b = conn.open("FET-USDT", "short", 50.0)
    assert a["orderId"].startswith("paper_fut_") and a["status"] == "FILLED"
    assert b["orderId"].startswith("paper_fut_") and a["orderId"] != b["orderId"]


def test_close_returns_synthetic_order_id():
    conn = PaperFuturesConnector()
    out = conn.close("ICP-USDT", "long", 100.0)
    assert out["orderId"].startswith("paper_fut_") and out["status"] == "FILLED"


def test_get_position_returns_none():
    # Positions live in the engine's position_mgr; the connector holds no state.
    assert PaperFuturesConnector().get_position("BTC-USDT") is None
