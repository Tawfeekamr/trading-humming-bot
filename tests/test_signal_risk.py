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
