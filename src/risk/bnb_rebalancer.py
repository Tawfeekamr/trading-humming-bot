# src/risk/bnb_rebalancer.py
"""BNB rebalancer — maintains BNB balance within target range for fee payments."""
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RebalanceResult:
    action: str  # "buy", "sell", or "none"
    amount_usdt: float
    reason: str = ""


class BNBRebalancer:
    def __init__(
        self,
        bnb_min_usdt: float = 10.0,
        bnb_target_usdt: float = 20.0,
        bnb_max_usdt: float = 50.0,
        cooldown_seconds: float = 3600.0,
    ):
        self._min = bnb_min_usdt
        self._target = bnb_target_usdt
        self._max = bnb_max_usdt
        self._cooldown = cooldown_seconds
        self._last_rebalance_ts: float = 0.0

    def evaluate(
        self, bnb_balance_usdt: float, available_usdt: float = float("inf")
    ) -> RebalanceResult:
        now = time.time()
        if now - self._last_rebalance_ts < self._cooldown:
            return RebalanceResult(action="none", amount_usdt=0.0, reason="cooldown")

        if bnb_balance_usdt < self._min:
            buy_amount = min(self._target - bnb_balance_usdt, available_usdt)
            if buy_amount < 1.0:
                return RebalanceResult(action="none", amount_usdt=0.0, reason="insufficient_usdt")
            self._last_rebalance_ts = now
            return RebalanceResult(
                action="buy", amount_usdt=round(buy_amount, 2),
                reason=f"BNB ${bnb_balance_usdt:.2f} < min ${self._min:.2f}",
            )

        if bnb_balance_usdt > self._max:
            sell_amount = bnb_balance_usdt - self._target
            self._last_rebalance_ts = now
            return RebalanceResult(
                action="sell", amount_usdt=round(sell_amount, 2),
                reason=f"BNB ${bnb_balance_usdt:.2f} > max ${self._max:.2f}",
            )

        return RebalanceResult(action="none", amount_usdt=0.0)
