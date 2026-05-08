import pytest
from src.risk.circuit_breaker import CircuitBreaker


class TestCircuitBreakerFailSafe:
    """Test fail-safe behavior when uninitialized."""

    def test_check_fails_safe_when_peak_equity_zero(self):
        """Should halt trading when peak_equity is 0 (fail-safe)."""
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        # Don't set peak_equity, it defaults to 0
        result = cb.check(1000.0)
        # Should return True (halt) when uninitialized
        assert result is True

    def test_check_daily_fails_safe_when_sod_equity_zero(self):
        """Should halt trading when sod_equity is 0 (fail-safe)."""
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        # Don't set sod_equity, it defaults to 0
        result = cb.check_daily(1000.0)
        # Should return True (halt) when uninitialized
        assert result is True

    def test_check_operates_normally_after_peak_set(self):
        """Should work normally after peak_equity is set."""
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_peak_equity(1000.0)
        # Now it should work as expected (no trip)
        result = cb.check(950.0)
        assert result is False

    def test_check_daily_operates_normally_after_sod_set(self):
        """Should work normally after sod_equity is set."""
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_start_of_day_equity(1000.0)
        # Now it should work as expected (no trip)
        result = cb.check_daily(960.0)
        assert result is False


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
