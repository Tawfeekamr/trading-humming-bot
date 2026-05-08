import threading
import logging

logger = logging.getLogger(__name__)


class CircuitBreaker:
    def __init__(self, max_drawdown_pct: float = 10.0, daily_loss_limit_pct: float = 5.0):
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self._peak_equity: float = 0.0
        self._sod_equity: float = 0.0
        self._halted: bool = False
        self._lock = threading.Lock()

    @property
    def halted(self) -> bool:
        return self._halted

    @halted.setter
    def halted(self, value: bool) -> None:
        with self._lock:
            self._halted = value

    def set_peak_equity(self, equity: float) -> None:
        with self._lock:
            self._peak_equity = equity

    def set_start_of_day_equity(self, equity: float) -> None:
        with self._lock:
            self._sod_equity = equity

    def update_peak(self, current_equity: float) -> None:
        with self._lock:
            if current_equity > self._peak_equity:
                self._peak_equity = current_equity

    def check(self, current_equity: float) -> bool:
        with self._lock:
            # Fail-safe: halt trading when peak_equity is uninitialized (0)
            if self._peak_equity == 0:
                logger.warning("Circuit breaker check called with peak_equity=0 (fail-safe mode)")
                self._halted = True
                return True
            drawdown_pct = ((self._peak_equity - current_equity) / self._peak_equity) * 100
            if drawdown_pct >= self.max_drawdown_pct:
                self._halted = True
                return True
            return False

    def check_daily(self, current_equity: float) -> bool:
        with self._lock:
            # Fail-safe: halt trading when sod_equity is uninitialized (0)
            if self._sod_equity == 0:
                logger.warning("Circuit breaker daily check called with sod_equity=0 (fail-safe mode)")
                self._halted = True
                return True
            loss_pct = ((self._sod_equity - current_equity) / self._sod_equity) * 100
            if loss_pct >= self.daily_loss_limit_pct:
                self._halted = True
                return True
            return False

    def reset(self, equity: float) -> None:
        with self._lock:
            self._halted = False
            self._peak_equity = max(equity, 0)
            self._sod_equity = max(equity, 0)
