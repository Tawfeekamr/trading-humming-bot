"""Circuit breaker — halts trading when drawdown or daily loss exceeds thresholds.

Shared across all strategies in a StrategyHost. All strategies report
their PnL to the same instance so losses in one strategy count against
the risk budget of all strategies.
"""
import time


class CircuitBreaker:
    def __init__(
        self,
        initial_capital: float,
        max_drawdown_pct: float = 10.0,
        daily_loss_limit_pct: float = 5.0,
    ):
        self.initial_capital = initial_capital
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct

        self.equity_peak = initial_capital
        self.current_equity = initial_capital
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0

        self.daily_starting_equity = initial_capital
        self.daily_realized_pnl = 0.0
        self._last_daily_reset_day = self._current_day()

        self.tripped = False
        self.trip_reason: str | None = None

    def _current_day(self) -> int:
        return int(time.time()) // 86400

    def _maybe_reset_daily(self):
        current_day = self._current_day()
        if current_day > self._last_daily_reset_day:
            self.daily_starting_equity = self.current_equity
            self.daily_realized_pnl = 0.0
            self._last_daily_reset_day = current_day

    def _update_equity(self):
        self.current_equity = self.initial_capital + self.realized_pnl + self.unrealized_pnl
        if self.current_equity > self.equity_peak:
            self.equity_peak = self.current_equity

    def _evaluate(self):
        # Check max drawdown
        if self.equity_peak > 0:
            drawdown = (self.equity_peak - self.current_equity) / self.equity_peak * 100
            if drawdown >= self.max_drawdown_pct:
                self.tripped = True
                self.trip_reason = f"Max drawdown reached: {drawdown:.1f}%"
                return

        # Check daily loss
        if self.daily_starting_equity > 0:
            daily_loss_pct = abs(self.daily_realized_pnl / self.daily_starting_equity * 100)
            if self.daily_realized_pnl < 0 and daily_loss_pct >= self.daily_loss_limit_pct:
                self.tripped = True
                self.trip_reason = f"Daily loss limit reached: {daily_loss_pct:.1f}%"

    def check(self) -> tuple[bool, str]:
        """Check if trading is allowed. Returns (allowed, reason)."""
        if self.tripped:
            return False, self.trip_reason or "Circuit breaker tripped"
        return True, ""

    def record_pnl(self, amount: float):
        """Record a realized PnL change."""
        self._maybe_reset_daily()
        self.realized_pnl += amount
        self.daily_realized_pnl += amount
        self._update_equity()
        self._evaluate()

    def update_unrealized(self, unrealized_pnl: float):
        """Update estimated unrealized PnL."""
        self.unrealized_pnl = unrealized_pnl
        self._update_equity()
        self._evaluate()

    def reset(self):
        """Manually reset the circuit breaker."""
        self.tripped = False
        self.trip_reason = None
