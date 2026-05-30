use trading_engine_core::connector::paper::PaperTradeEngine;
use trading_engine_core::connector::types::*;
use trading_engine_core::models::order::OrderSide;
use std::collections::HashMap;

fn new_engine() -> PaperTradeEngine {
    let mut balances = HashMap::new();
    balances.insert("USDT".to_string(), 10000.0);
    balances.insert("BTC".to_string(), 0.5);
    PaperTradeEngine::new(balances)
}

#[test]
fn test_limit_buy_fills_when_price_drops() {
    let mut engine = new_engine();
    let req = OrderRequest {
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Buy,
        order_type: OrderTypeReq::Limit,
        price: Some(50000.0),
        quantity: 0.1,
        time_in_force: Some(TimeInForceReq::Gtc),
        client_order_id: Some("test_buy_1".to_string()),
    };

    engine.place_order(&req).unwrap();
    assert_eq!(engine.open_order_count(), 1);

    // Market price at 51000 — should NOT fill
    let fills = engine.try_fill_at_price(51000.0);
    assert!(fills.is_empty());

    // Market price drops to 49900 — should fill
    let fills = engine.try_fill_at_price(49900.0);
    assert_eq!(fills.len(), 1);
    assert_eq!(fills[0].price, 50000.0);
    assert_eq!(fills[0].quantity, 0.1);
}

#[test]
fn test_limit_sell_fills_when_price_rises() {
    let mut engine = new_engine();
    let req = OrderRequest {
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Sell,
        order_type: OrderTypeReq::Limit,
        price: Some(55000.0),
        quantity: 0.1,
        time_in_force: Some(TimeInForceReq::Gtc),
        client_order_id: Some("test_sell_1".to_string()),
    };

    engine.place_order(&req).unwrap();

    let fills = engine.try_fill_at_price(54000.0);
    assert!(fills.is_empty());

    let fills = engine.try_fill_at_price(55100.0);
    assert_eq!(fills.len(), 1);
    assert_eq!(fills[0].price, 55000.0);
}

#[test]
fn test_market_order_fills_immediately() {
    let mut engine = new_engine();
    let req = OrderRequest {
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Buy,
        order_type: OrderTypeReq::Market,
        price: None,
        quantity: 0.1,
        time_in_force: None,
        client_order_id: Some("test_market_1".to_string()),
    };

    engine.place_order(&req).unwrap();
    let fills = engine.try_fill_at_price(50000.0);
    assert_eq!(fills.len(), 1);
    assert_eq!(fills[0].price, 50000.0);
}

#[test]
fn test_balance_updates_on_fill() {
    let mut engine = new_engine();
    let req = OrderRequest {
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Buy,
        order_type: OrderTypeReq::Limit,
        price: Some(50000.0),
        quantity: 0.1,
        time_in_force: Some(TimeInForceReq::Gtc),
        client_order_id: Some("test_balance".to_string()),
    };

    engine.place_order(&req).unwrap();
    engine.try_fill_at_price(49900.0);

    let balances = engine.balances();
    assert_eq!(*balances.get("BTC").unwrap(), 0.6);       // 0.5 + 0.1
    // USDT = 10000 - (50000 * 0.1) - (50000 * 0.1 * 0.001) = 10000 - 5000 - 5 = 4995
    assert_eq!(*balances.get("USDT").unwrap(), 4995.0);
}

#[test]
fn test_cancel_order() {
    let mut engine = new_engine();
    let req = OrderRequest {
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Buy,
        order_type: OrderTypeReq::Limit,
        price: Some(50000.0),
        quantity: 0.1,
        time_in_force: Some(TimeInForceReq::Gtc),
        client_order_id: Some("test_cancel".to_string()),
    };

    let order = engine.place_order(&req).unwrap();
    assert_eq!(engine.open_order_count(), 1);

    engine.cancel_order(&order.order_id).unwrap();
    assert_eq!(engine.open_order_count(), 0);
}
