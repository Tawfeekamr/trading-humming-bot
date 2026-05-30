use trading_engine_core::connector::binance_rest::BinanceRest;

#[tokio::test]
#[ignore] // Requires network access
async fn test_get_order_book() {
    let client = BinanceRest::new("", "", true);
    let book = client.get_order_book("BTCUSDT", 5).await.unwrap();
    assert!(!book.bids.is_empty());
    assert!(!book.asks.is_empty());
    assert!(book.best_bid().unwrap() < book.best_ask().unwrap());
}

#[tokio::test]
#[ignore]
async fn test_get_klines() {
    let client = BinanceRest::new("", "", true);
    let bars = client.get_klines("BTCUSDT", "1h", 10).await.unwrap();
    assert!(!bars.is_empty());
    assert!(bars.len() <= 10);
    for bar in &bars {
        assert!(bar.high >= bar.low);
        assert!(bar.volume >= 0.0);
    }
}
