use trading_engine_core::connector::binance_ws::BinanceWs;

#[tokio::test]
#[ignore] // Requires network access
async fn test_ws_receives_order_book_updates() {
    let ws = BinanceWs::new(true);
    let mut rx = ws.subscribe("BTCUSDT", "1m").await.unwrap();

    let event = tokio::time::timeout(
        tokio::time::Duration::from_secs(10),
        rx.recv()
    ).await.unwrap().unwrap();

    match event {
        trading_engine_core::connector::binance_ws::WsEvent::OrderBookUpdate { symbol, .. } => {
            assert_eq!(symbol, "BTCUSDT");
        }
        trading_engine_core::connector::binance_ws::WsEvent::Trade { symbol, .. } => {
            assert_eq!(symbol, "BTCUSDT");
        }
        _ => {}
    }
}
