"""
signal_risk.py — Risk management for signal copy trading.
"""

import logging
import time
from datetime import datetime, timezone

from .signal_parser import ParsedSignal, SignalConfidence

logger = logging.getLogger(__name__)


class SignalRiskGuard:
    """Risk management for signal copy trading."""

    def __init__(self, config: dict):
        self._capital_pct = config.get("capital_pct", 10.0)
        self._max_capital = config.get("max_capital_usdt", 1000)
        self._max_positions = config.get("max_positions", 3)
        self._per_trade_pct = config.get("per_trade_risk_pct", 3.0)
        self._max_position_pct = config.get("max_position_pct", 25.0)
        self._daily_loss_limit_pct = config.get("daily_loss_limit_pct", 5.0)
        self._max_trades_per_day = config.get("max_trades_per_day", 10)
        self._cooldown_seconds = config.get("cooldown_minutes", 5) * 60

        self._trades_today = 0
        self._daily_pnl = 0.0
        self._signal_budget = 0.0
        self._last_trade_time = 0
        self._halted = False
        self._last_reset_date = ""

    def can_trade(self) -> bool:
        self._maybe_reset_daily()
        if self._halted:
            return False
        if self._trades_today >= self._max_trades_per_day:
            return False
        if time.time() - self._last_trade_time < self._cooldown_seconds:
            return False
        return True

    def get_budget_for_trade(self, signal: ParsedSignal, total_equity: float) -> float:
        """Calculate position size based on confidence and risk per trade."""
        total_budget = min(self._max_capital, total_equity * self._capital_pct / 100)
        self._signal_budget = total_budget

        conf_multiplier = {
            SignalConfidence.HIGH: 1.0,
            SignalConfidence.MEDIUM: 0.66,
            SignalConfidence.LOW: 0.33,
        }
        mult = conf_multiplier.get(signal.confidence, 0.33)
        risk_amount = total_budget * self._per_trade_pct / 100 * mult

        # Per-trade notional cap: the most capital deployed into a single
        # signal. Replaces the old total_budget/max_positions flat slice so a
        # favorable risk:reward can deploy up to max_position_pct of the budget.
        # (risk_amount / sl_distance still bounds the $ loss at per_trade_risk_pct
        # of capital on a stop-out — raising this cap deploys more notional at
        # the same $ risk, it does not increase the loss if stopped.)
        max_notional = total_budget * self._max_position_pct / 100

        if signal.stop_loss and signal.entry_high:
            sl_distance_pct = (signal.entry_high - signal.stop_loss) / signal.entry_high
            if sl_distance_pct > 0:
                position_size = risk_amount / sl_distance_pct
                return min(position_size, max_notional)

        return max_notional

    def record_trade_opened(self):
        self._trades_today += 1
        self._last_trade_time = time.time()

    def record_trade_closed(self, pnl: float):
        self._daily_pnl += pnl
        if (self._signal_budget > 0 and
                self._daily_pnl <= -(self._signal_budget * self._daily_loss_limit_pct / 100)):
            self._halted = True
            logger.warning(f"Signal engine halted: daily loss {self._daily_pnl:.2f} exceeded limit")

    def get_status(self) -> dict:
        self._maybe_reset_daily()
        return {
            "trades_today": self._trades_today,
            "max_trades": self._max_trades_per_day,
            "daily_pnl": self._daily_pnl,
            "budget": self._signal_budget,
            "halted": self._halted,
            "cooldown_remaining": max(0, self._cooldown_seconds - (time.time() - self._last_trade_time)),
        }

    def _maybe_reset_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_reset_date:
            self._trades_today = 0
            self._daily_pnl = 0.0
            self._halted = False
            self._last_reset_date = today
