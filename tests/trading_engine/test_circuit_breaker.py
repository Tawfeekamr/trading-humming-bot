from src.trading_engine.risk.circuit_breaker import CircuitBreaker


def test_allows_trading_initially():
    cb = CircuitBreaker(10000)
    allowed, reason = cb.check()
    assert allowed
    assert reason == ""


def test_trips_on_max_drawdown():
    cb = CircuitBreaker(10000, max_drawdown_pct=10.0, daily_loss_limit_pct=100.0)
    # Simulate a 10% loss
    cb.record_pnl(-1001.0)
    allowed, reason = cb.check()
    assert not allowed
    assert "drawdown" in reason.lower()


def test_trips_on_daily_loss():
    cb = CircuitBreaker(10000, max_drawdown_pct=100.0, daily_loss_limit_pct=5.0)
    cb.record_pnl(-501.0)
    allowed, reason = cb.check()
    assert not allowed
    assert "daily" in reason.lower()


def test_manual_reset():
    cb = CircuitBreaker(10000, max_drawdown_pct=5.0)
    cb.record_pnl(-600.0)
    assert not cb.check()[0]
    cb.reset()
    assert cb.check()[0]


def test_unrealized_pnl_contributes_to_equity():
    cb = CircuitBreaker(10000, max_drawdown_pct=10.0, daily_loss_limit_pct=100.0)
    # Unrealized loss of 5% — below the 10% drawdown threshold
    cb.update_unrealized(-500.0)
    allowed, _ = cb.check()
    assert allowed  # 5% unrealized, threshold is 10%

    # Push unrealized to exactly 10% — should trip
    cb.update_unrealized(-1000.0)
    allowed, _ = cb.check()
    assert not allowed  # 10% unrealized drawdown trips the breaker
