"""Tests for SignalRiskGuard.block_reason — the human-readable reason a trade is
blocked. can_trade() delegates to it so the reported reason always matches the
decision, and the Telegram block alert can say WHY (halted / max-trades /
cooldown) instead of a bare pair name.
"""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.signals.signal_risk import SignalRiskGuard  # noqa: E402


def _guard(**overrides):
    cfg = {
        "max_trades_per_day": 3,
        "cooldown_minutes": 5,
        "daily_loss_limit_pct": 5.0,
        "max_capital_usdt": 1000,
        "capital_pct": 10.0,
    }
    cfg.update(overrides)
    g = SignalRiskGuard(cfg)
    # Stabilize the daily-reset guard so _maybe_reset_daily (called inside
    # can_trade / block_reason) doesn't wipe freshly-set state on first call.
    g._last_reset_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return g


class TestBlockReason:
    def test_not_blocked_returns_none(self):
        g = _guard()
        assert g.block_reason() is None
        assert g.can_trade() is True

    def test_halted_reports_halt_reason(self):
        g = _guard()
        g._halted = True
        reason = g.block_reason()
        assert reason is not None and "halt" in reason.lower()
        assert g.can_trade() is False

    def test_max_trades_reports_count(self):
        g = _guard(max_trades_per_day=3)
        for _ in range(3):
            g.record_trade_opened()
        reason = g.block_reason()
        assert "max trades" in reason.lower()
        assert "3/3" in reason
        assert g.can_trade() is False

    def test_cooldown_reports_seconds_remaining(self):
        g = _guard(cooldown_minutes=5)
        g.record_trade_opened()  # starts the cooldown window now
        reason = g.block_reason()
        assert "cooldown" in reason.lower()
        assert "s left" in reason
        assert g.can_trade() is False


import pytest
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence
from src.signals.signal_risk import SignalRiskGuard, LiquidationBufferError


def _sig(entry=100.0, sl=80.0):
    return ParsedSignal(action=SignalAction.OPEN_LONG, pair="X-USDT",
        entry_low=entry, entry_high=entry, stop_loss=sl,
        take_profits=[130.0], confidence=SignalConfidence.HIGH, quality_score=8)


def test_leverage_sizing_returns_notional():
    g = SignalRiskGuard({"capital_pct":100,"max_capital_usdt":1000,"per_trade_risk_pct":1.0,"max_position_pct":100})
    assert g.get_budget_for_trade(_sig(100.0, 80.0), 1000.0, leverage=3) > 0


def test_rejects_when_sl_beyond_liquidation():
    g = SignalRiskGuard({"capital_pct":100,"max_capital_usdt":1000,"per_trade_risk_pct":1.0,"max_position_pct":100})
    with pytest.raises(LiquidationBufferError):
        g.get_budget_for_trade(_sig(100.0, 60.0), 1000.0, leverage=3)  # SL below liq ~67


def test_no_leverage_keeps_legacy_behavior():
    g = SignalRiskGuard({"capital_pct":10,"max_capital_usdt":1000,"per_trade_risk_pct":3.0,"max_position_pct":25})
    assert g.get_budget_for_trade(_sig(100.0, 80.0), 1000.0) > 0  # no leverage arg
