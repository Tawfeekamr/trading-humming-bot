# tests/test_signal_futures_engine.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import src.signals.signal_engine as se_mod
from src.signals.signal_engine import SignalEngine
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence


def _short():
    return ParsedSignal(action=SignalAction.OPEN_SHORT, pair="ETH-USDT",
        entry_low=3000.0, entry_high=3000.0, stop_loss=3150.0,
        take_profits=[2900.0, 2800.0], confidence=SignalConfidence.HIGH, quality_score=8)


class FakeConn:
    def __init__(self): self.calls = []
    def set_leverage(self, s, l): self.calls.append(("lev", s, l))
    def set_margin_type(self, s, m="ISOLATED"): self.calls.append(("margin", s))
    def open(self, s, side, qty, **k): self.calls.append(("open", s, side, qty)); return {"orderId": "1"}
    def close(self, s, side, qty): self.calls.append(("close", s, side, qty)); return {"orderId": "9"}
    def get_price(self, s): return 3000.0
    def get_position(self, s): return None


def _futures_engine(monkeypatch, tmp_path, connector=None):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    conn = connector or FakeConn()
    sent = []
    eng = SignalEngine(config={"enabled": True, "audit_mode": False, "allow_shorts": True},
                       btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
                       telegram_send_fn=sent.append, buy_fn=lambda **k: "x",
                       get_price_fn=conn.get_price,
                       futures_mode=True, futures_connector=conn, leverage=3)
    eng._get_equity = lambda c: 10000.0
    eng._get_current_price = lambda c, s: 3000.0
    eng._log_audit_trade = lambda *a, **k: None
    eng._journal.log_raw_message = lambda *a, **k: None
    eng._risk.get_budget_for_trade = lambda sig, eq, leverage=3: 600.0
    eng._risk.record_trade_opened = lambda: None
    eng._position_mgr.has_open_position = lambda p: False
    eng._position_mgr.get_open_positions = lambda: []
    eng._position_mgr.open_position = lambda **k: None
    eng._seen_signal_ids = set(); eng._seen_signal_ids_path = str(tmp_path / "seen.json")
    return eng, conn, sent


def test_futures_opens_short_via_connector(monkeypatch, tmp_path):
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    eng._execute_entry(_short(), "chan", eng._futures_connector)
    assert any(c[0] == "open" and c[2] == "short" for c in conn.calls)
    assert any(c[0] == "lev" and c[2] == 3 for c in conn.calls)
    assert any("FUTURES" in m and "SHORT" in m for m in sent)


def test_futures_long_uses_buy(monkeypatch, tmp_path):
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    sig = ParsedSignal(action=SignalAction.OPEN_LONG, pair="BTC-USDT", entry_low=100.0,
        entry_high=100.0, stop_loss=80.0, take_profits=[130.0],
        confidence=SignalConfidence.HIGH, quality_score=8)
    eng._execute_entry(sig, "chan", eng._futures_connector)
    assert any(c[0] == "open" and c[2] == "long" for c in conn.calls)


def test_futures_manage_closes_short_on_tp_via_reduce_only(monkeypatch, tmp_path):
    # Open a short, then manage with price fallen to TP1 → reduce-only close.
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    from src.signals.signal_position import SignalPosition
    pos = SignalPosition("ETHUSDT", 3000.0, 2.0, 3150.0, [2900.0, 2800.0],
                         "high", "x", "chan", entry_timestamp=0, side="short")
    eng._position_mgr.get_open_positions = lambda: [pos]
    eng._position_mgr.partial_close = lambda sym, pct, price, reason: (2.0 * pct, 0.0)
    eng._position_mgr.update_stop_loss = lambda sym, sl: None
    eng._get_current_price = lambda c, s: 2900.0  # == TP1 for the short
    eng._manage_positions(eng._futures_connector)
    assert any(c[0] == "close" and c[2] == "short" for c in conn.calls)


def test_futures_skips_duplicate_open_position(monkeypatch, tmp_path):
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    eng._position_mgr.has_open_position = lambda p: True
    eng._execute_entry(_short(), "chan", eng._futures_connector)
    assert not any(c[0] == "open" for c in conn.calls)
    assert any("already open" in m for m in sent)


def test_futures_skips_when_max_positions_reached(monkeypatch, tmp_path):
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    eng._position_mgr._max_positions = 1
    eng._position_mgr.get_open_positions = lambda: ["one"]
    eng._execute_entry(_short(), "chan", eng._futures_connector)
    assert not any(c[0] == "open" for c in conn.calls)
    assert any("max positions" in m for m in sent)


def test_spot_mode_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    buys = []
    eng = SignalEngine(config={"enabled": True, "audit_mode": False},
                       btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
                       telegram_send_fn=lambda m: None,
                       buy_fn=lambda **k: (buys.append(k), "oid")[1],
                       get_price_fn=lambda s: 0.11)
    eng._get_current_price = lambda c, s: 0.11; eng._get_equity = lambda c: 10000.0
    eng._log_audit_trade = lambda *a, **k: None; eng._journal.log_raw_message = lambda *a, **k: None
    eng._risk.get_budget_for_trade = lambda sig, eq, leverage=None: 500.0
    eng._risk.record_trade_opened = lambda: None
    eng._position_mgr.has_open_position = lambda p: False
    eng._position_mgr.get_open_positions = lambda: []
    eng._position_mgr.open_position = lambda **k: None
    assert eng._futures_mode is False
    sig = ParsedSignal(action=SignalAction.OPEN_LONG, pair="DOGE-USDT", entry_low=0.10,
        entry_high=0.12, stop_loss=0.09, take_profits=[0.14],
        confidence=SignalConfidence.HIGH, quality_score=8)
    eng._execute_entry(sig, "chan", None)
    assert len(buys) == 1
