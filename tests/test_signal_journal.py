"""Tests for SignalJournal CRUD — covers raw_messages, signal_trades,
signal_decision_states, channel_stats, recent_signals, summary."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.signals.signal_journal import SignalJournal, SignalTrade


def _trade(**kw):
    base = dict(
        timestamp="2024-01-01T00:00:00+00:00", symbol="BTC-USDT",
        channel_name="TestCh", action="OPEN_LONG", entry_price=100.0,
        current_price=100.0, quantity=1.0, realized_pnl=0.0,
        exit_reason="", signal_confidence="high", stop_loss=95.0,
        take_profits="[105]", tp1_hit=0, tp2_hit=0, tp3_hit=0,
        raw_message="BUY", parse_reasoning="", is_audit=0,
    )
    base.update(kw)
    return SignalTrade(**base)


def test_log_and_query_roundtrip(tmp_path):
    j = SignalJournal(db_path=tmp_path / "test.db")
    j.log_raw_message(1, "TestCh", 100, "BUY BTC", "OPEN_LONG", "BTC-USDT", "rsn", 8, "good")
    j.log_trade(_trade())
    j.log_decision_state("2024-01-01T00:00:00+00:00", "BTC-USDT", "TestCh", "OPEN_LONG",
                         10000.0, 0, 0.0, 0.0, "[0.1]", "[0.2]", "RANGING")

    stats = j.channel_stats()
    assert len(stats) == 1
    assert stats[0]["channel"] == "TestCh"
    assert stats[0]["messages"] == 1
    assert stats[0]["trades_approved"] == 1

    recent = j.recent_signals(limit=5)
    assert len(recent) == 1
    assert recent[0]["pair"] == "BTC-USDT"
    assert recent[0]["quality_score"] == 8

    states = j.decision_states()
    assert len(states) == 1
    assert states[0]["symbol"] == "BTC-USDT"

    summary = j.summary(days=-1)
    assert summary["total_trades"] >= 1


def test_summary_by_channel(tmp_path):
    j = SignalJournal(db_path=tmp_path / "test.db")
    j.log_trade(_trade(realized_pnl=50.0))
    by_ch = j.summary_by_channel(days=10000)  # all time (trade ts is 2024)
    assert "TestCh" in by_ch
    assert by_ch["TestCh"]["pnl"] == 50.0


def test_empty_db_returns_safe_defaults(tmp_path):
    j = SignalJournal(db_path=tmp_path / "empty.db")
    assert j.channel_stats() == []
    assert j.recent_signals() == []
    assert j.decision_states() == []
    s = j.summary()
    assert s["total_trades"] == 0
