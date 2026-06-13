use trading_engine_core::connector::paper::PaperTradeEngine;
use trading_engine_core::connector::types::{OrderRequest, OrderTypeReq, TimeInForceReq};
use trading_engine_core::models::order::OrderSide;
use std::collections::HashMap;

fn buy(symbol: &str, price: f64, qty: f64) -> OrderRequest {
    OrderRequest {
        symbol: symbol.to_string(),
        side: OrderSide::Buy,
        order_type: OrderTypeReq::Limit,
        price: Some(price),
        quantity: qty,
        time_in_force: Some(TimeInForceReq::Gtc),
        client_order_id: None,
    }
}

fn sell(symbol: &str, price: f64, qty: f64) -> OrderRequest {
    OrderRequest {
        symbol: symbol.to_string(),
        side: OrderSide::Sell,
        order_type: OrderTypeReq::Limit,
        price: Some(price),
        quantity: qty,
        time_in_force: Some(TimeInForceReq::Gtc),
        client_order_id: None,
    }
}

/// A BNB buy (limit $608) must NOT fill when the XRP orderbook price ($1.15)
/// is checked. Before the fix, `try_fill_at_price` evaluated every open order
/// against the passed-in price, so the BNB buy filled instantly ($1.15 <= $608),
/// producing cross-pair contamination.
#[test]
fn test_fills_are_symbol_isolated() {
    let mut engine = PaperTradeEngine::new(HashMap::new());

    // XRP sell that SHOULD fill at the XRP mid (1.15 >= 1.10).
    engine.place_order(&sell("XRP-USDT", 1.10, 100.0)).unwrap();
    // BNB buy that must NOT fill against the XRP price.
    engine.place_order(&buy("BNB-USDT", 608.0, 8.62)).unwrap();

    // Check the XRP orderbook at $1.15.
    let fills = engine.try_fill_at_price("XRP-USDT", 1.15);

    // Exactly one fill — the XRP sell. The BNB buy stays open.
    assert_eq!(fills.len(), 1, "only the XRP order should fill, got {} fills", fills.len());
    assert_eq!(fills[0].symbol, "XRP-USDT");
    assert_eq!(engine.open_order_count(), 1, "BNB order should remain open");
}

/// A buy only fills against its own pair's price, and a non-matching price
/// leaves it open.
#[test]
fn test_buy_fills_only_when_own_price_crosses() {
    let mut engine = PaperTradeEngine::new(HashMap::new());
    engine.place_order(&buy("BNB-USDT", 608.0, 8.62)).unwrap();

    // XRP price must not fill the BNB buy.
    assert!(engine.try_fill_at_price("XRP-USDT", 1.15).is_empty());
    assert_eq!(engine.open_order_count(), 1, "BNB buy survives XRP price check");

    // BNB price at/under the limit fills it.
    let fills = engine.try_fill_at_price("BNB-USDT", 607.0);
    assert_eq!(fills.len(), 1);
    assert_eq!(fills[0].symbol, "BNB-USDT");
    assert_eq!(engine.open_order_count(), 0);
}

/// Symbol formats "BNB-USDT" and "BNBUSDT" are treated as the same pair.
#[test]
fn test_symbol_normalization() {
    let mut engine = PaperTradeEngine::new(HashMap::new());
    engine.place_order(&buy("BNB-USDT", 608.0, 8.62)).unwrap();

    // Order placed as "BNB-USDT", queried with raw "BNBUSDT".
    let fills = engine.try_fill_at_price("BNBUSDT", 607.0);
    assert_eq!(fills.len(), 1, "dash-less symbol should match dashed order");
}
