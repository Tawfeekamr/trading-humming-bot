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


def test_spot_tp_flag_not_marked_when_close_order_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    eng = SignalEngine(config={"enabled": True, "audit_mode": False},
                       btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
                       telegram_send_fn=lambda m: None,
                       sell_fn=lambda **k: None,
                       get_price_fn=lambda s: 110.0)
    from src.signals.signal_position import SignalPosition
    pos = SignalPosition("BTC-USDT", 100.0, 1.0, 90.0, [110.0], "high", "x", "chan")
    mark_calls = []
    partial_calls = []
    eng._position_mgr.get_open_positions = lambda: [pos]
    eng._position_mgr.mark_tp_hit = lambda sym, level: mark_calls.append((sym, level))
    eng._position_mgr.partial_close = lambda *a: partial_calls.append(a) or (0.0, 0.0)
    eng._position_mgr.update_stop_loss = lambda *a: None
    eng._record_close = lambda *a, **k: None
    eng._get_current_price = lambda c, s: 110.0
    eng._execute_close = lambda *a, **k: False

    eng._manage_positions(None)

    assert mark_calls == []
    assert partial_calls == []


def test_futures_close_failure_does_not_mutate_tracker(monkeypatch, tmp_path):
    class FailingCloseConn(FakeConn):
        def close(self, s, side, qty):
            self.calls.append(("close_failed", s, side, qty))
            raise RuntimeError("close failed")

    eng, conn, sent = _futures_engine(monkeypatch, tmp_path, connector=FailingCloseConn())
    from src.signals.signal_position import SignalPosition
    pos = SignalPosition("ETH-USDT", 3000.0, 2.0, 2950.0, [3100.0], "high", "x", "chan")
    partial_calls = []
    mark_calls = []
    recorded = []
    eng._position_mgr.get_open_positions = lambda: [pos]
    eng._position_mgr.partial_close = lambda *a: partial_calls.append(a) or (0.0, 0.0)
    eng._position_mgr.mark_tp_hit = lambda *a: mark_calls.append(a)
    eng._position_mgr.update_stop_loss = lambda *a: None
    eng._record_close = lambda *a: recorded.append(a)
    eng._get_current_price = lambda c, s: 3100.0

    eng._manage_positions(eng._futures_connector)

    assert any(c[0] == "close_failed" for c in conn.calls)
    assert partial_calls == []
    assert mark_calls == []
    assert recorded == []


def test_process_message_allows_futures_short(monkeypatch, tmp_path):
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    sig = _short()
    eng._parser.parse = lambda *a, **k: sig
    eng._validator.validate = lambda s: (True, "")
    eng._risk.block_reason = lambda: None
    eng._journal.log_raw_message = lambda *a, **k: None
    eng._get_current_price = lambda c, s: 3000.0

    eng._process_message({"text": "short ETH", "channel_name": "chan", "message_id": 0}, eng._futures_connector)

    assert any(c[0] == "open" and c[2] == "short" for c in conn.calls)


def test_futures_entry_rechecks_risk_before_open(monkeypatch, tmp_path):
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    eng._risk.block_reason = lambda: "cooldown"

    eng._execute_futures_entry(_short(), "chan")

    assert not any(c[0] == "open" for c in conn.calls)


def test_manual_close_uses_position_manager_close(monkeypatch, tmp_path):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    eng = SignalEngine(config={"enabled": True, "audit_mode": True},
                       btc_regime_fn=lambda: ("RANGING", 0.0, 0.0))
    from src.signals.signal_position import SignalPosition
    pos = SignalPosition("ETH-USDT", 3000.0, 1.0, 2900.0, [3100.0], "high", "x", "chan")
    close_calls = []
    eng._position_mgr.get_position = lambda sym: pos
    eng._position_mgr.close_position = lambda sym, price, reason: close_calls.append((sym, price, reason)) or 0.0

    assert eng.manual_close("ETH-USDT") == "manual"
    assert close_calls == [("ETH-USDT", 3000.0, "manual")]


def _futures_manage_engine(monkeypatch, tmp_path, price, side="long",
                           tps_long=(3100.0, 3200.0, 3300.0), tps_short=(2900.0, 2800.0, 2700.0),
                           sl_long=2950.0, sl_short=3050.0,
                           tp1_hit=False, tp2_hit=False, tp3_hit=False):
    """Build a futures engine with one open position and instrumented close hooks.

    Records what _record_close was called with via the recorded list so the test
    can assert TPs are journaled.
    """
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    from src.signals.signal_position import SignalPosition
    recorded = []
    if side == "long":
        tps, sl = tps_long, sl_long
    else:
        tps, sl = tps_short, sl_short
    pos = SignalPosition("ETHUSDT", 3000.0, 2.0, sl, tps,
                         "high", "x", "chan", entry_timestamp=0, side=side)
    pos.tp1_hit = tp1_hit; pos.tp2_hit = tp2_hit; pos.tp3_hit = tp3_hit
    # Keep a stable qty snapshot for assertions regardless of accounting order.
    eng._position_mgr.get_open_positions = lambda: [pos]
    close_position_calls = []
    def fake_close_position(symbol, close_price, reason):
        close_position_calls.append((symbol, close_price, reason))
        pos.is_closed = True
        return 42.0
    eng._position_mgr.close_position = fake_close_position
    partial_close_calls = []
    def fake_partial_close(symbol, pct, close_price, reason):
        partial_close_calls.append((symbol, pct, close_price, reason))
        return (pos.remaining_amount * pct, 1.5)
    eng._position_mgr.partial_close = fake_partial_close
    eng._position_mgr.update_stop_loss = lambda sym, s: None
    eng._record_close = lambda p, pr, reason, pnl: recorded.append((reason, pnl))
    eng._get_current_price = lambda c, s: price
    eng._manage_positions(eng._futures_connector)
    return eng, conn, sent, pos, recorded, close_position_calls, partial_close_calls


def test_futures_tp3_full_closes_position_and_journals(monkeypatch, tmp_path):
    # Price reaches TP3 on a long -> full close (not partial), connector.close
    # called with the pre-close qty, and a journal row recorded.
    eng, conn, sent, pos, recorded, close_calls, _ = _futures_manage_engine(
        monkeypatch, tmp_path, price=3300.0, side="long", tp1_hit=True, tp2_hit=True)
    assert pos.is_closed is True
    assert any(c[2] == "tp3" for c in close_calls), \
        f"close_position must be called with tp3; got {close_calls}"
    assert any(c[0] == "close" and c[2] == "long" and c[3] == 2.0
               for c in conn.calls), f"connector.close(long, 2.0) expected; got {conn.calls}"
    assert ("tp3", 42.0) in recorded, f"tp3 must be journaled; got {recorded}"


def test_futures_tp3_short_full_closes_position(monkeypatch, tmp_path):
    # Symmetric: short reaching TP3 also full-closes + journals.
    eng, conn, sent, pos, recorded, close_calls, _ = _futures_manage_engine(
        monkeypatch, tmp_path, price=2700.0, side="short", tp1_hit=True, tp2_hit=True)
    assert pos.is_closed is True
    assert any(c[2] == "tp3" for c in close_calls), f"got {close_calls}"
    assert any(c[0] == "close" and c[2] == "short" for c in conn.calls)
    assert ("tp3", 42.0) in recorded, f"got {recorded}"


def test_futures_tp1_partial_close_is_journaled(monkeypatch, tmp_path):
    # TP1 partial close must call _record_close (journal), like the spot path.
    eng, conn, sent, pos, recorded, _, partial_calls = _futures_manage_engine(
        monkeypatch, tmp_path, price=3100.0, side="long")
    assert pos.is_closed is False  # only partial
    assert any(c[3] == "tp1" for c in partial_calls), f"got {partial_calls}"
    assert any(r[0] == "tp1" for r in recorded), \
        f"tp1 partial close must be journaled; got {recorded}"


def test_futures_tp2_partial_close_is_journaled(monkeypatch, tmp_path):
    # TP2 likewise journaled.
    eng, conn, sent, pos, recorded, _, partial_calls = _futures_manage_engine(
        monkeypatch, tmp_path, price=3200.0, side="long", tp1_hit=True)
    assert any(c[3] == "tp2" for c in partial_calls), f"got {partial_calls}"
    assert any(r[0] == "tp2" for r in recorded), f"got {recorded}"
