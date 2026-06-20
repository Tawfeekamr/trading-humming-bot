use std::collections::HashMap;
use trading_engine_core::engine::Engine;
use trading_engine_core::connector::types::OrderBook;

#[test]
fn test_portfolio_equity_mtm_usdt_plus_inventory() {
    let mut balances = HashMap::new();
    balances.insert("USDT".to_string(), 5000.0);
    balances.insert("BTC".to_string(), 0.5);
    let mut order_books = HashMap::new();
    order_books.insert("BTC-USDT".to_string(), OrderBook {
        symbol: "BTC-USDT".to_string(),
        bids: vec![(60000.0, 1.0)],
        asks: vec![(60100.0, 1.0)],
        timestamp: 0,
    });
    // equity = 5000 USDT + 0.5 BTC × mid(60050) = 5000 + 30025 = 35025
    let equity = Engine::portfolio_equity_mtm(&balances, &order_books);
    assert!((equity - 35025.0).abs() < 1e-6, "got {}", equity);
}

#[test]
fn test_portfolio_equity_pure_usdt() {
    let mut balances = HashMap::new();
    balances.insert("USDT".to_string(), 10000.0);
    let order_books = HashMap::new();
    let equity = Engine::portfolio_equity_mtm(&balances, &order_books);
    assert!((equity - 10000.0).abs() < 1e-6);
}
