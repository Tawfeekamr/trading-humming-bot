"""Nov 2026 audit fixes: entry-chase guard, honest exit labels, one unified
trades.db row per completed signal trade, and a journal summary that does not
double-count partial take-profits."""
import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from src.signals.signal_engine import entry_chase_reason, exit_label
from src.signals.signal_journal import SignalJournal, SignalTrade
from src.signals.unified_trades import estimate_fees, write_trade


# ── entry-chase guard ────────────────────────────────────────────────────────
def test_long_at_or_above_tp1_is_rejected():
    # The real UNI-like case: price already past the first take-profit.
    assert "TP1" in entry_chase_reason("long", 4.3645, 3.56, 3.60, [3.78, 4.0])


def test_long_far_above_zone_is_rejected():
    assert "above the entry zone" in entry_chase_reason("long", 104.0, 99.0, 100.0, [120.0])


def test_long_within_tolerance_is_allowed():
    assert entry_chase_reason("long", 102.0, 99.0, 100.0, [120.0]) is None


def test_short_mirrors_long():
    assert "TP1" in entry_chase_reason("short", 89.0, 100.0, 101.0, [90.0])
    assert "below the entry zone" in entry_chase_reason("short", 96.0, 100.0, 101.0, [80.0])
    assert entry_chase_reason("short", 99.0, 100.0, 101.0, [80.0]) is None


def test_missing_price_never_blocks():
    assert entry_chase_reason("long", 0.0, 99.0, 100.0, [120.0]) is None


# ── exit labels ──────────────────────────────────────────────────────────────
def _pos(tp1=False, tp2=False):
    return SimpleNamespace(tp1_hit=tp1, tp2_hit=tp2)


def test_plain_stop_loss_keeps_its_name():
    assert exit_label("stop_loss", _pos()) == "stop_loss"


def test_stop_after_tp1_is_breakeven():
    assert exit_label("stop_loss", _pos(tp1=True)) == "breakeven_stop"


def test_stop_after_tp2_on_spot_is_trailing():
    assert exit_label("stop_loss", _pos(tp1=True, tp2=True)) == "trailing_stop"
    assert exit_label("stop_loss", _pos(tp1=True, tp2=True), futures_mode=True) == "breakeven_stop"


def test_other_reasons_untouched():
    assert exit_label("tp3", _pos(tp1=True, tp2=True)) == "tp3"


# ── unified trades.db writer ─────────────────────────────────────────────────
def test_fee_estimate_long():
    # entry notional 1000, exit notional 1100, 5 bps → 1.05
    assert estimate_fees("long", 100.0, 10.0, 100.0, 5) == pytest.approx(1.05)


def test_fee_estimate_short_and_zero_bps():
    assert estimate_fees("short", 100.0, 10.0, 100.0, 5) == pytest.approx(0.95)
    assert estimate_fees("long", 100.0, 10.0, 100.0, 0) == 0.0


def test_write_trade_stores_net_pnl_as_live_row(tmp_path):
    db = tmp_path / "trades.db"
    ok = write_trade(engine="signal", pair="SOL-USDT", side="long", entry_price=100.0,
                     exit_price=110.0, quantity=10.0, gross_pnl=75.0, fees=1.0,
                     exit_reason="trailing_stop", duration_mins=42, sl_price=95.0,
                     tp_price=105.0, r_multiple=1.48, context={"a": 1}, db_path=db)
    assert ok
    row = sqlite3.connect(db).execute(
        "SELECT engine, side, pnl, fees, duration_mins, is_backfilled, exit_reason FROM trades").fetchone()
    assert row == ("signal", "LONG", 74.0, 1.0, 42, 0, "trailing_stop")


def _engine(tmp_path, futures=False):
    from src.signals.signal_engine import SignalEngine
    eng = SignalEngine.__new__(SignalEngine)
    eng._audit_mode = False
    eng._futures_mode = futures
    eng._unified_engine = "signal_futures" if futures else "signal"
    eng._unified_db = tmp_path / "trades.db"
    eng._fee_bps = 0.0 if futures else 5.0
    eng._risk = SimpleNamespace(record_trade_closed=lambda pnl: None)
    eng._log_position_trade = lambda *a, **k: None
    eng._notify = lambda *a, **k: None
    return eng


def _signal_pos(**kw):
    from src.signals.signal_position import SignalPosition
    p = SignalPosition(symbol="SOL-USDT", entry_price=100.0, amount=10.0, stop_loss=95.0,
                       take_profits=[105.0, 110.0, 120.0], signal_confidence="HIGH",
                       raw_message="", channel_name="chan", entry_timestamp=0.0)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _rows(db):
    if not db.exists():
        return []
    return sqlite3.connect(db).execute("SELECT engine, pnl, exit_reason FROM trades").fetchall()


def test_partial_take_profit_writes_no_unified_row(tmp_path):
    eng = _engine(tmp_path)
    eng._record_close(_signal_pos(tp1_hit=True, realized_pnl=16.5), 105.0, "tp1", 16.5)
    assert _rows(tmp_path / "trades.db") == []


def test_full_close_writes_one_row_with_cumulative_pnl(tmp_path):
    eng = _engine(tmp_path)
    pos = _signal_pos(tp1_hit=True, tp2_hit=True, is_closed=True, realized_pnl=60.0)
    eng._record_close(pos, 105.0, "stop_loss", 60.0)
    rows = _rows(tmp_path / "trades.db")
    assert len(rows) == 1
    engine, pnl, reason = rows[0]
    assert engine == "signal" and reason == "trailing_stop"
    assert pnl == pytest.approx(60.0 - estimate_fees("long", 100.0, 10.0, 60.0, 5))


def test_futures_close_goes_to_its_own_engine_without_fees(tmp_path):
    eng = _engine(tmp_path, futures=True)
    pos = _signal_pos(is_closed=True, realized_pnl=-50.0, side="short")
    eng._record_close(pos, 105.0, "stop_loss", -50.0)
    assert _rows(tmp_path / "trades.db") == [("signal_futures", -50.0, "stop_loss")]


def test_audit_mode_writes_nothing(tmp_path):
    eng = _engine(tmp_path)
    eng._audit_mode = True
    eng._record_close(_signal_pos(is_closed=True, realized_pnl=10.0), 101.0, "tp3", 10.0)
    assert _rows(tmp_path / "trades.db") == []


# ── journal summary counts each trade once ───────────────────────────────────
def _jrow(ts, sym, action, pnl):
    return SignalTrade(timestamp=ts, symbol=sym, channel_name="chan", action=action,
                       entry_price=100.0, current_price=101.0, quantity=1.0, realized_pnl=pnl,
                       exit_reason="x", signal_confidence="HIGH", stop_loss=95.0, take_profits="[]",
                       tp1_hit=0, tp2_hit=0, tp3_hit=0, raw_message="", parse_reasoning="",
                       is_audit=0)


def test_summary_does_not_double_count_partials(tmp_path):
    j = SignalJournal(db_path=tmp_path / "j.db")
    def t():
        return datetime.now(timezone.utc).isoformat()
    # Completed trade: two partial slices then the cumulative final row (75).
    for action, pnl in (("CLOSE_tp1", 30.0), ("CLOSE_tp2", 40.0), ("CLOSE_trailing_stop", 75.0)):
        j.log_trade(_jrow(t(), "SOL-USDT", action, pnl))
    # Still-open trade with one banked partial (10).
    j.log_trade(_jrow(t(), "ADA-USDT", "CLOSE_tp1", 10.0))
    s = j.summary(days=-1)
    assert s["total_pnl"] == pytest.approx(85.0)
    assert s["total_trades"] == 1
