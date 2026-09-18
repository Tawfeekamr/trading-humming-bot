use std::time::Duration;

use futures::{SinkExt, StreamExt};
use trading_engine_core::connector::binance_ws::BinanceWs;

/// Binance pings every ~20s and disconnects non-responding clients within
/// ~1 min. The client's read loop never writes explicitly — it relies on
/// tungstenite auto-queueing the pong and read() flushing it. This pins
/// that behavior so an upgrade can't silently break keepalive.
#[tokio::test]
async fn ws_client_answers_server_ping_with_pong() {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    let server = tokio::spawn(async move {
        let (stream, _) = listener.accept().await.unwrap();
        let mut ws = tokio_tungstenite::accept_async(stream).await.unwrap();
        ws.send(tokio_tungstenite::tungstenite::Message::Ping(b"keepalive".to_vec()))
            .await
            .unwrap();

        let deadline = tokio::time::sleep(Duration::from_secs(5));
        tokio::pin!(deadline);
        loop {
            tokio::select! {
                _ = &mut deadline => panic!("client never answered the ping with a pong"),
                msg = ws.next() => match msg {
                    Some(Ok(tokio_tungstenite::tungstenite::Message::Pong(p))) => {
                        assert_eq!(p, b"keepalive".to_vec());
                        return;
                    }
                    Some(Ok(_)) => {}
                    Some(Err(e)) => panic!("server read error: {}", e),
                    None => panic!("client closed the connection without ponging"),
                },
            }
        }
    });

    let client = BinanceWs::with_base_url(format!("ws://{}", addr));
    let _rx = client
        .subscribe_multi(&["BTC-USDT".to_string()], "1m")
        .await
        .unwrap();

    tokio::time::timeout(Duration::from_secs(10), server)
        .await
        .unwrap()
        .unwrap();
}

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
