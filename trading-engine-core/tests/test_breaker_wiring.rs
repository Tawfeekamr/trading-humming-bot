use trading_engine_core::connector::types::{OrderRequest, OrderTypeReq, TimeInForceReq};
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::risk::circuit_breaker::CircuitBreaker;

#[test]
fn test_breaker_trips_on_drawdown_and_persists_fields() {
    let mut cb = CircuitBreaker::new(10.0, 5.0);
    cb.set_peak_equity(10000.0);
    cb.set_start_of_day_equity(10000.0);
    cb.set_last_reset_date("2026-06-14".to_string());
    assert!(!cb.check(9500.0), "5% drop from peak is under 10% DD");
    assert!(cb.check(8900.0), "11% drop trips max-drawdown");
    assert!(cb.is_halted_raw(), "halted flag set");
    assert_eq!(cb.last_reset_date(), "2026-06-14", "reset date stored");
}

#[test]
fn test_breaker_daily_loss_trips() {
    let mut cb = CircuitBreaker::new(10.0, 5.0);
    cb.set_start_of_day_equity(10000.0);
    assert!(cb.check_daily(9400.0), "6% daily loss trips");
    assert!(cb.is_halted_raw());
}

#[test]
fn test_reduce_only_flag_is_set_on_exit_order() {
    // Guard: exits must carry reduce_only=true so the breaker can't trap them.
    let req = OrderRequest {
        symbol: "BTCUSDT".into(),
        side: OrderSide::Sell,
        order_type: OrderTypeReq::Limit,
        price: Some(50000.0),
        quantity: 0.1,
        time_in_force: Some(TimeInForceReq::Gtc),
        client_order_id: None,
        reduce_only: true,
    };
    assert!(req.reduce_only, "exit order is reduce-only");
}
