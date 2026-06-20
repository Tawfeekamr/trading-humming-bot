"""Tests that SignalEngine notifies Telegram on EVERY signal rejection/skip path.

Background: several rejection paths only logged and never notified Telegram
(risk-guard block, no-entry-price, zero-budget, buy-failed, no-order-id). A failing
buy path — notably the Decimal-serialization production bug — was therefore silent,
so the operator was never told valid signals were not becoming trades. These tests
pin that each path fires an alert, and that repeats are de-duplicated by a per-key
cooldown so a persistent failure can't spam Telegram.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import src.signals.signal_engine as se_mod  # noqa: E402
from src.signals.signal_engine import SignalEngine  # noqa: E402
from src.signals.signal_parser import (  # noqa: E402
    ParsedSignal,
    SignalAction,
    SignalConfidence,
)


def _valid_signal(pair="DOGE-USDT"):
    return ParsedSignal(
        action=SignalAction.OPEN_LONG,
        pair=pair,
        entry_low=0.10,
        entry_high=0.12,
        stop_loss=0.09,
        take_profits=[0.14, 0.16],
        confidence=SignalConfidence.HIGH,
        quality_score=8,
    )


def _raising_buy(**kwargs):
    raise RuntimeError("exchange exploded")


@pytest.fixture
def engine(monkeypatch, tmp_path):
    # Avoid Gate.io network + Telethon/DeepSeek clients doing real work on init.
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)

    sent = []

    def fake_send(msg):
        sent.append(msg)

    eng = SignalEngine(
        config={"enabled": True, "audit_mode": False, "notify_cooldown_seconds": 60},
        btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
        telegram_send_fn=fake_send,
        buy_fn=lambda **kw: "oid-ok",
        get_price_fn=lambda symbol: 0.11,
    )
    # Deterministic price/equity + no DB/audit/disk side effects in tests.
    eng._get_current_price = lambda conn, pair: 0.11
    eng._get_equity = lambda conn: 10000.0
    eng._log_audit_trade = lambda *a, **k: None
    eng._journal.log_raw_message = lambda *a, **k: None
    eng._risk.get_budget_for_trade = lambda sig, eq: 500.0
    eng._risk.record_trade_opened = lambda: None
    # Position manager persists to disk; isolate from any pre-existing state so
    # every _execute_entry test reaches the buy/budget code under test.
    eng._position_mgr.has_open_position = lambda pair: False
    eng._position_mgr.get_open_positions = lambda: []
    eng._position_mgr.open_position = lambda **kw: None
    # Isolate the message_id dedup set to a temp file per test.
    eng._seen_signal_ids = set()
    eng._seen_signal_ids_path = str(tmp_path / "seen_signal_ids.json")
    return eng, sent


class TestRejectionNotifications:
    def test_buy_failed_notifies(self, engine):
        eng, sent = engine
        eng._buy_fn = _raising_buy
        eng._execute_entry(_valid_signal(), "testchan", None)
        assert any("fail" in m.lower() for m in sent), sent

    def test_no_order_id_notifies(self, engine):
        eng, sent = engine
        eng._buy_fn = lambda **kw: None
        eng._execute_entry(_valid_signal(), "testchan", None)
        assert any("order" in m.lower() for m in sent), sent

    def test_zero_budget_notifies(self, engine):
        eng, sent = engine
        eng._risk.get_budget_for_trade = lambda sig, eq: 0.0
        eng._execute_entry(_valid_signal(), "testchan", None)
        assert any("budget" in m.lower() for m in sent), sent

    def test_no_entry_price_notifies(self, engine):
        eng, sent = engine
        sig = _valid_signal()
        sig.entry_low = None
        sig.entry_high = None
        eng._execute_entry(sig, "testchan", None)
        assert any("entry" in m.lower() for m in sent), sent

    def test_risk_guard_block_notifies_with_reason_and_detail(self, engine, monkeypatch):
        eng, sent = engine
        eng._parser.parse = lambda text: _valid_signal()
        eng._validator.validate = lambda sig: (True, "ok")
        # The engine reads block_reason() (can_trade delegates to it); stub it so
        # the risk-guard branch fires with a concrete reason.
        monkeypatch.setattr(eng._risk, "block_reason", lambda: "cooldown (120s left)")
        eng._process_message({"text": "x", "channel_name": "testchan"}, None)
        assert sent, "risk-guard block must notify"
        msg = sent[-1]
        assert "blocked" in msg.lower() and "risk guard" in msg.lower()
        assert "cooldown (120s left)" in msg       # the reason
        assert "0.1" in msg and "0.12" in msg      # entry zone (0.10-0.12)
        assert "8/10" in msg                       # quality score
        assert "high" in msg                       # confidence
        assert "testchan" in msg                   # channel


class TestNotifyDedupe:
    def test_repeats_within_cooldown_are_suppressed(self, engine, monkeypatch):
        eng, sent = engine
        t = [1000.0]
        monkeypatch.setattr(se_mod.time, "time", lambda: t[0])
        eng._notify_dedupe("buy_failed:DOGE-USDT", "msg A")
        eng._notify_dedupe("buy_failed:DOGE-USDT", "msg A")
        assert len(sent) == 1

    def test_different_keys_both_send(self, engine, monkeypatch):
        eng, sent = engine
        monkeypatch.setattr(se_mod.time, "time", lambda: 1000.0)
        eng._notify_dedupe("buy_failed:DOGE-USDT", "a")
        eng._notify_dedupe("no_budget:DOGE-USDT", "b")
        assert len(sent) == 2

    def test_sends_again_after_cooldown_window(self, engine, monkeypatch):
        eng, sent = engine
        t = [1000.0]
        monkeypatch.setattr(se_mod.time, "time", lambda: t[0])
        eng._notify_dedupe("k", "first")
        t[0] = 1100.0  # past the 60s cooldown configured in the fixture
        eng._notify_dedupe("k", "second")
        assert len(sent) == 2


class TestEntryZoneGate:
    """The engine must only enter while live price is inside [entry_low, entry_high].

    Background: it used to MARKET-buy at the current price the instant a signal
    arrived, ignoring the entry zone. A stale signal (price already well above the
    zone, often above tp1) then got opened above its own first take-profit, so the
    Rust exit logic 'hit tp1' at a loss and closed the position within seconds.
    """

    def _engine_with_price_and_buy_tracker(self, engine, price):
        eng, sent = engine
        eng._get_current_price = lambda conn, pair: price
        buys = []

        def tracking_buy(**kw):
            buys.append(kw)
            return "oid-ok"

        eng._buy_fn = tracking_buy
        return eng, sent, buys

    def test_above_zone_skips_and_notifies(self, engine):
        # valid signal zone is 0.10-0.12; 0.20 is above it (the bug case)
        eng, sent, buys = self._engine_with_price_and_buy_tracker(engine, 0.20)
        eng._execute_entry(_valid_signal(), "testchan", None)
        assert buys == [], "must not chase above the entry zone"
        assert any("above entry zone" in m for m in sent), sent

    def test_below_zone_skips_and_notifies(self, engine):
        eng, sent, buys = self._engine_with_price_and_buy_tracker(engine, 0.05)
        eng._execute_entry(_valid_signal(), "testchan", None)
        assert buys == [], "must not buy before price reaches the entry zone"
        assert any("below entry zone" in m for m in sent), sent

    def test_in_zone_buys(self, engine):
        eng, sent, buys = self._engine_with_price_and_buy_tracker(engine, 0.11)
        eng._execute_entry(_valid_signal(), "testchan", None)
        assert len(buys) == 1, "price inside the zone should enter"


class TestMessageIdDedup:
    """A container restart must not re-execute old channel signals.

    Background: the listener persists every arriving message to signal_queue.jsonl
    but never drains consumed ones, so on restart it replays the whole backlog
    (a 06-19 signal re-ran on 06-20). Engine-level message_id dedup, persisted to
    disk, stops that. Channel edits ('[EDIT] …') share the original message_id, so
    they are exempt — they're usually result updates, not new entries.
    """

    def test_replay_is_skipped_before_parse(self, engine):
        eng, sent = engine
        parse_calls = []

        def fake_parse(text):
            parse_calls.append(text)
            return _valid_signal()

        eng._parser.parse = fake_parse
        eng._validator.validate = lambda sig: (True, "ok")
        msg = {"text": "sig", "channel_name": "c", "message_id": 4242}
        eng._process_message(msg, None)
        eng._process_message(msg, None)  # replay
        assert len(parse_calls) == 1, "replay must be skipped before the LLM parse"

    def test_edits_are_not_deduped(self, engine):
        eng, sent = engine
        parse_calls = []
        eng._parser.parse = lambda text: (parse_calls.append(text), _valid_signal())[1]
        eng._validator.validate = lambda sig: (True, "ok")
        msg = {"text": "[EDIT] sig", "channel_name": "c", "message_id": 4242}
        eng._process_message(msg, None)
        eng._process_message(msg, None)  # edit replay — still parses
        assert len(parse_calls) == 2, "edits must always be processed"

    def test_seen_set_persisted_to_disk(self, engine):
        eng, sent = engine
        eng._parser.parse = lambda text: _valid_signal()
        eng._validator.validate = lambda sig: (True, "ok")
        msg = {"text": "sig", "channel_name": "c", "message_id": 7777}
        eng._process_message(msg, None)
        assert 7777 in eng._seen_signal_ids
        assert os.path.exists(eng._seen_signal_ids_path), "seen set must persist"
