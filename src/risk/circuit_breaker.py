class CircuitBreaker:
    def __init__(self, max_drawdown_pct: float = 10.0, daily_loss_limit_pct: float = 5.0):
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self._peak_equity: float = 0.0
        self._sod_equity: float = 0.0
        self.halted: bool = False

    def set_peak_equity(self, equity: float) -> None:
        self._peak_equity = equity

    def set_start_of_day_equity(self, equity: float) -> None:
        self._sod_equity = equity

    def update_peak(self, current_equity: float) -> None:
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

    def check(self, current_equity: float) -> bool:
        if self._peak_equity == 0:
            return False
        drawdown_pct = ((self._peak_equity - current_equity) / self._peak_equity) * 100
        if drawdown_pct >= self.max_drawdown_pct:
            self.halted = True
            return True
        return False

    def check_daily(self, current_equity: float) -> bool:
        if self._sod_equity == 0:
            return False
        loss_pct = ((self._sod_equity - current_equity) / self._sod_equity) * 100
        if loss_pct >= self.daily_loss_limit_pct:
            self.halted = True
            return True
        return False
