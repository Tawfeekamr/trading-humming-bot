# tests/test_headless_engine.py
"""Headless SignalEngine mode (own_listener=False): no ChannelListener created,
drivable via process_one/manage by an external coordinator."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import src.signals.signal_engine as se_mod
from src.signals.signal_engine import SignalEngine
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence
from src.signals.channel_listener import ChannelListener


def _headless_engine(monkeypatch, tmp_path):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    eng = SignalEngine(config={"enabled": True, "audit_mode": False, "allow_shorts": True},
                       btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
                       telegram_send_fn=lambda m: None,
                       buy_fn=lambda **k: "x",
                       own_listener=False)
    eng._get_equity = lambda c: 10000.0
    eng._get_current_price = lambda c, s: 3000.0
    eng._log_audit_trade = lambda *a, **k: None
    eng._journal.log_raw_message = lambda *a, **k: None
    eng._risk.block_reason = lambda: None
    eng._position_mgr.get_open_positions = lambda: []
    eng._seen_signal_ids = set()
    eng._seen_signal_ids_path = str(tmp_path / "seen.json")
    return eng


def test_headless_engine_has_no_listener(monkeypatch, tmp_path):
    eng = _headless_engine(monkeypatch, tmp_path)
    assert eng._own_listener is False
    assert eng._listener is None


def test_headless_start_listener_is_noop(monkeypatch, tmp_path):
    eng = _headless_engine(monkeypatch, tmp_path)
    # Must not raise even though _listener is None.
    eng.start_listener()
    eng.stop_listener()


def test_headless_process_one_routes_to_process_message(monkeypatch, tmp_path):
    eng = _headless_engine(monkeypatch, tmp_path)
    parsed = ParsedSignal(action=SignalAction.NOT_A_SIGNAL, pair="",
                          confidence=SignalConfidence.LOW, quality_score=0)
    calls = []
    eng._parser.parse = lambda *a, **k: (calls.append(a), parsed)[1]
    # Should be callable and route through _process_message (parser reached).
    eng.process_one({"text": "x", "channel_name": "c", "message_id": 0}, None)
    assert calls, "process_one must reach _parser.parse via _process_message"


def test_headless_manage_is_callable(monkeypatch, tmp_path):
    eng = _headless_engine(monkeypatch, tmp_path)
    # Must not raise; with no open positions it is a no-op.
    eng.manage(None)


def test_headless_tick_does_not_crash(monkeypatch, tmp_path):
    eng = _headless_engine(monkeypatch, tmp_path)
    # tick must not throw even though _listener is None.
    eng.tick(None)


def test_default_engine_constructs_listener(monkeypatch, tmp_path):
    # own_listener=True (default) must still build a ChannelListener.
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    eng = SignalEngine(config={"enabled": True, "audit_mode": False},
                       btc_regime_fn=lambda: ("RANGING", 0.0, 0.0))
    assert eng._own_listener is True
    assert isinstance(eng._listener, ChannelListener)
