"""Tests for SignalEngine decision-state capture (Phase 2).

Persists market + portfolio state at each signal decision for offline RL: the
14 regime features for the signal pair + BTC, plus equity / open positions /
notional / drawdown-from-peak. Capture is best-effort — features need klines +
the ML deps (present in the signal container); a failure must never break a
signal tick, and portfolio state is captured even when features aren't.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.signals.signal_engine import SignalEngine  # noqa: E402
from src.signals.signal_parser import (  # noqa: E402
    ParsedSignal,
    SignalAction,
    SignalConfidence,
)


def _signal(pair="DOGE-USDT"):
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


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    eng = SignalEngine(
        config={"enabled": True, "audit_mode": False},
        btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
        telegram_send_fn=lambda msg: None,
        buy_fn=lambda **kw: "oid",
        get_price_fn=lambda symbol: 0.11,
    )
    eng._get_equity = lambda conn: 10000.0
    eng._position_mgr.has_open_position = lambda pair: False
    eng._position_mgr.get_open_positions = lambda: []
    eng._current_connector = None
    return eng


class TestDecisionStateCapture:
    def test_captures_portfolio_state_without_features(self, engine, monkeypatch):
        engine._peak_equity = 12000.0  # drawdown vs equity 10000
        captured = {}
        engine._journal.log_decision_state = lambda **kw: captured.update(kw)
        monkeypatch.setattr(engine, "_fetch_klines", lambda p, limit=250: [])
        monkeypatch.setattr(engine, "_compute_features", lambda k: None)
        engine._capture_decision_state(_signal(), "testchan", "OPEN_LONG", "2024-01-01T00:00:00+00:00")
        assert captured["decision"] == "OPEN_LONG"
        assert captured["symbol"] == "DOGE-USDT"
        assert captured["equity"] == 10000.0
        assert captured["open_positions"] == 0
        assert captured["drawdown_pct"] > 0
        assert captured["pair_features"] is None  # features unavailable -> still captured

    def test_captures_features_when_available(self, engine, monkeypatch):
        captured = {}
        engine._journal.log_decision_state = lambda **kw: captured.update(kw)
        monkeypatch.setattr(engine, "_fetch_klines", lambda p, limit=250: [{"close": 1}])
        monkeypatch.setattr(engine, "_compute_features", lambda k: [0.5] * 14)
        engine._capture_decision_state(_signal(), "testchan", "rejected", "ts")
        assert json.loads(captured["pair_features"]) == [0.5] * 14
        assert json.loads(captured["btc_features"]) == [0.5] * 14
        assert captured["btc_regime"] == "RANGING"

    def test_never_raises_on_failure(self, engine, monkeypatch):
        def boom(conn):
            raise RuntimeError("equity blew up")
        engine._get_equity = boom
        engine._journal.log_decision_state = lambda **kw: None
        engine._capture_decision_state(_signal(), "c", "x", "ts")  # must not raise

    def test_drawdown_peak_tracks_up_only(self, engine, monkeypatch):
        engine._peak_equity = 0.0
        engine._journal.log_decision_state = lambda **kw: None
        monkeypatch.setattr(engine, "_fetch_klines", lambda p, limit=250: [])
        monkeypatch.setattr(engine, "_compute_features", lambda k: None)
        engine._capture_decision_state(_signal(), "c", "x", "ts")  # equity 10000 -> peak 10000
        assert engine._peak_equity == 10000.0
        engine._peak_equity = 15000.0  # a prior peak must NOT be dragged down
        engine._capture_decision_state(_signal(), "c", "x", "ts2")
        assert engine._peak_equity == 15000.0

    def test_log_audit_trade_also_captures_state(self, engine, monkeypatch):
        # _log_audit_trade (the single decision hook) must persist a state row too.
        captured = {}
        engine._journal.log_decision_state = lambda **kw: captured.update(kw)
        engine._journal.log_trade = lambda trade: None
        monkeypatch.setattr(engine, "_fetch_klines", lambda p, limit=250: [])
        monkeypatch.setattr(engine, "_compute_features", lambda k: None)
        # _log_audit_trade submits the capture to a thread pool; run it inline here
        # so the assertion is deterministic.
        import src.signals.signal_engine as se_mod
        monkeypatch.setattr(se_mod._CAPTURE_POOL, "submit", lambda fn, *a, **k: fn(*a, **k))
        engine._log_audit_trade(_signal(), "testchan", "blocked_risk", 0, "risk_limit")
        assert captured.get("decision") == "blocked_risk"
