import pytest
from src.risk.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_no_trip_when_below_threshold(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_peak_equity(1000.0)
        assert not cb.check(950.0)

    def test_trips_at_threshold(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_peak_equity(1000.0)
        assert cb.check(890.0)

    def test_peak_updates_on_new_high(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_peak_equity(1000.0)
        cb.update_peak(1100.0)
        assert not cb.check(1000.0)

    def test_daily_loss_limit(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_start_of_day_equity(1000.0)
        assert cb.check_daily(940.0)

    def test_daily_safe_below_limit(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_start_of_day_equity(1000.0)
        assert not cb.check_daily(960.0)

    def test_halted_flag(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_peak_equity(1000.0)
        cb.check(800.0)
        assert cb.halted
