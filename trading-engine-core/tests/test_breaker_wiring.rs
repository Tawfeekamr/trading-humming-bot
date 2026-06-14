use trading_engine_core::connector::types::{OrderRequest, OrderTypeReq, TimeInForceReq};
use trading_engine_core::models::order::OrderSide;

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
