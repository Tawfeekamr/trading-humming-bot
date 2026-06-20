"""Tests that SignalEngine notifies Telegram on EVERY signal rejection/skip path.

Background: several rejection paths only logged and never notified Telegram
(risk-guard block, no-entry-price, zero-budget, buy-failed, no-order-id). A failing
buy path — notably the Decimal-serialization production bug — was therefore silent,
so the operator was never told valid signals were not becoming trades. These tests
pin that each path fires an alert, and that repeats are de-duplicated by a per-key
cooldown so a persistent failure can't spam Telegram.
"""
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
def engine(monkeypatch):
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
    # Deterministic price/equity + no DB/audit side effects in tests.
    eng._get_current_price = lambda conn, pair: 0.11
    eng._get_equity = lambda conn: 10000.0
    eng._log_audit_trade = lambda *a, **k: None
    eng._journal.log_raw_message = lambda *a, **k: None
    eng._risk.get_budget_for_trade = lambda sig, eq: 500.0
    # Position manager persists to disk; isolate from any pre-existing state so
    # every _execute_entry test reaches the buy/budget code under test.
    eng._position_mgr.has_open_position = lambda pair: False
    eng._position_mgr.get_open_positions = lambda: []
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

    def test_risk_guard_block_notifies(self, engine, monkeypatch):
        eng, sent = engine
        eng._parser.parse = lambda text: _valid_signal()
        eng._validator.validate = lambda sig: (True, "ok")
        # Bypass can_trade() internals (its daily-reset clears _halted) — we only
        # want to prove the risk-guard *branch* notifies when trading is blocked.
        monkeypatch.setattr(eng._risk, "can_trade", lambda: False)
        eng._process_message({"text": "x", "channel_name": "testchan"}, None)
        assert any("risk" in m.lower() or "blocked" in m.lower() for m in sent), sent


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
