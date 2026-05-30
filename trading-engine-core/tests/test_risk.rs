use trading_engine_core::risk::position_guard::PositionGuard;
use trading_engine_core::risk::circuit_breaker::CircuitBreaker;

#[test]
fn test_position_guard_rejects_over_exposure() {
    let guard = PositionGuard::new(80.0, 50.0, 1000.0);
    assert!(!guard.can_place_order(700.0, 1.0, 200.0, 900.0, 1000.0));
}

#[test]
fn test_position_guard_allows_within_limits() {
    let guard = PositionGuard::new(80.0, 50.0, 1000.0);
    assert!(guard.can_place_order(100.0, 1.0, 800.0, 100.0, 1000.0));
}

#[test]
fn test_position_guard_rejects_below_reserve() {
    let guard = PositionGuard::new(80.0, 50.0, 1000.0);
    // Only $60 USDT left, order costs $20, but reserve is $50
    assert!(!guard.can_place_order(100.0, 1.0, 60.0, 20.0, 1000.0));
}

#[test]
fn test_circuit_breaker_triggers_on_drawdown() {
    let mut cb = CircuitBreaker::new(10.0, 5.0);
    cb.set_peak_equity(1000.0);
    cb.set_start_of_day_equity(1000.0);

    assert!(!cb.check(950.0));  // 5% drawdown — no halt
    assert!(!cb.is_halted());

    assert!(cb.check(890.0));   // 11% drawdown — halt!
    assert!(cb.is_halted());
}

#[test]
fn test_circuit_breaker_daily_loss() {
    let mut cb = CircuitBreaker::new(10.0, 5.0);
    cb.set_peak_equity(1000.0);
    cb.set_start_of_day_equity(1000.0);

    assert!(cb.check_daily(940.0));  // 6% daily loss — halt
    assert!(cb.is_halted());
}

#[test]
fn test_circuit_breaker_reset() {
    let mut cb = CircuitBreaker::new(10.0, 5.0);
    cb.set_peak_equity(1000.0);
    cb.set_start_of_day_equity(1000.0);
    cb.check(800.0);
    assert!(cb.is_halted());

    cb.reset(900.0);
    assert!(!cb.is_halted());
}

#[test]
fn test_circuit_breaker_update_peak() {
    let mut cb = CircuitBreaker::new(10.0, 5.0);
    cb.set_peak_equity(1000.0);
    cb.update_peak(1200.0);
    assert_eq!(cb.peak_equity(), 1200.0);

    cb.update_peak(1100.0);  // Should NOT decrease
    assert_eq!(cb.peak_equity(), 1200.0);
}
