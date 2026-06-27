# tests/signals/test_futures_paper_execution.py
"""Regression: the fapi -1121 'Invalid symbol' failure.

Non-Binance coins (ICP/FET) are not tradable on Binance's testnet, so every
futures entry crashed at set_leverage(). They now open PAPER positions via
PaperFuturesConnector (Gate.io perp pricing) with no exception. This wires the
REAL connector into the engine (not the FakeConn used in test_signal_futures_engine)
and exercises the real risk guard (leverage sizing + SL-before-liquidation).
"""
import sys
import pathlib
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from src.signals.signal_engine import SignalEngine
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence
from src.signals.paper_futures_connector import PaperFuturesConnector


def _mock_gate(payload):
    m = MagicMock()
    m.__enter__.return_value = m
    m.read.return_value = json.dumps(payload).encode()
    return m


def _engine_with_paper_connector(monkeypatch, tmp_path):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    conn = PaperFuturesConnector()
    eng = SignalEngine(
        config={
            "enabled": True, "audit_mode": False, "allow_shorts": True,
            "per_trade_risk_pct": 1.0, "max_capital_usdt": 10000,
            "capital_pct": 100.0, "max_position_pct": 25.0,
            "max_positions": 2,
        },
        btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
        telegram_send_fn=lambda m: None,
        buy_fn=lambda **k: "x",
        sell_fn=lambda **k: "x",
        get_price_fn=conn.get_price,
        get_equity_fn=lambda: 10000.0,
        futures_mode=True, futures_connector=conn, leverage=3,
    )
    eng._get_equity = lambda c: 10000.0
    eng._log_audit_trade = lambda *a, **k: None
    eng._journal.log_raw_message = lambda *a, **k: None
    eng._risk.record_trade_opened = lambda: None
    eng._position_mgr.has_open_position = lambda p: False
    eng._position_mgr.get_open_positions = lambda: []
    opened = []
    eng._position_mgr.open_position = lambda **k: opened.append(k)
    eng._seen_signal_ids = set()
    eng._seen_signal_ids_path = str(tmp_path / "seen.json")
    return eng, conn, opened


def test_non_binance_icp_opens_paper_position(monkeypatch, tmp_path):
    """ICP (Binance testnet rejects it with -1121) opens a paper long via Gate
    perp pricing. Before the fix this raised at set_leverage."""
    eng, conn, opened = _engine_with_paper_connector(monkeypatch, tmp_path)
    sig = ParsedSignal(
        action=SignalAction.OPEN_LONG, pair="ICP-USDT",
        entry_low=2.17, entry_high=2.18, stop_loss=1.985,
        take_profits=[2.285, 2.385, 2.5],
        confidence=SignalConfidence.HIGH, quality_score=9,
    )
    gate = _mock_gate([{"contract": "ICP_USDT", "mark_price": "2.18", "last": "2.18"}])
    with patch("urllib.request.urlopen", side_effect=lambda *a, **k: gate):
        eng._execute_entry(sig, "Binance Killers VIP Signals", conn)  # must NOT raise
    assert len(opened) == 1
    assert opened[0]["symbol"] == "ICP-USDT"
    assert opened[0]["side"] == "long"


def test_major_btc_also_opens_via_gate(monkeypatch, tmp_path):
    """A major (BTC) takes the same paper path — Gate.io prices everything."""
    eng, conn, opened = _engine_with_paper_connector(monkeypatch, tmp_path)
    sig = ParsedSignal(
        action=SignalAction.OPEN_LONG, pair="BTC-USDT",
        entry_low=60000, entry_high=60500, stop_loss=58000,
        take_profits=[62000, 64000, 66000],
        confidence=SignalConfidence.HIGH, quality_score=8,
    )
    gate = _mock_gate([{"contract": "BTC_USDT", "mark_price": "60500", "last": "60500"}])
    with patch("urllib.request.urlopen", side_effect=lambda *a, **k: gate):
        eng._execute_entry(sig, "chan", conn)  # must NOT raise
    assert len(opened) == 1
    assert opened[0]["symbol"] == "BTC-USDT"


def test_non_binance_short_opens_paper_position(monkeypatch, tmp_path):
    """A SHORT on a non-Binance coin (FET) opens via Gate perp pricing with
    side-aware sizing. (Called via _execute_entry directly, like the existing
    test_futures_opens_short_via_connector — _process_message would otherwise
    return early on non-OPEN_LONG actions.)"""
    eng, conn, opened = _engine_with_paper_connector(monkeypatch, tmp_path)
    sig = ParsedSignal(
        action=SignalAction.OPEN_SHORT, pair="FET-USDT",
        entry_low=0.173, entry_high=0.175, stop_loss=0.20,
        take_profits=[0.16, 0.15, 0.14],
        confidence=SignalConfidence.HIGH, quality_score=8,
    )
    gate = _mock_gate([{"contract": "FET_USDT", "mark_price": "0.175", "last": "0.175"}])
    with patch("urllib.request.urlopen", side_effect=lambda *a, **k: gate):
        eng._execute_entry(sig, "chan", conn)  # must NOT raise
    assert len(opened) == 1
    assert opened[0]["symbol"] == "FET-USDT"
    assert opened[0]["side"] == "short"
