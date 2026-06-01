use anyhow::Result;
use futures::{SinkExt, StreamExt};
use tracing::{info, warn, error};

use crate::connector::binance_ws::WsEvent;
use crate::models::bar::Bar;

/// Manages Gate.io WebSocket connections.
///
/// Gate.io uses a single connection URL with JSON-based channel subscriptions:
/// - URL: `wss://api.gateio.ws/ws/v4/`
/// - Subscribe: `{"time": ts, "channel": "spot.order_book", "event": "subscribe", "payload": ["BTC_USDT"]}`
/// - Response format: `{"time": ts, "channel": "spot.order_book", "event": "update", ...}`
pub struct GateioWs {
    base_url: String,
}

impl GateioWs {
    pub fn new() -> Self {
        Self {
            base_url: "wss://api.gateio.ws/ws/v4/".to_string(),
        }
    }

    /// Subscribe to real-time streams for a trading pair.
    ///
    /// Subscribes to order book, candlesticks, and trades for the given symbol.
    /// The symbol is converted from internal format (e.g. "BTCUSDT") to Gate.io format ("BTC_USDT").
    pub async fn subscribe(
        &self,
        symbol: &str,
        kline_interval: &str,
    ) -> Result<tokio::sync::mpsc::Receiver<WsEvent>> {
        let pair = crate::connector::gateio_rest::to_gate_pair(symbol);
        let (tx, rx) = tokio::sync::mpsc::channel(1000);

        info!("Connecting to Gate.io WS for {}", pair);

        let url = self.base_url.clone();
        let pair_clone = pair.clone();
        let interval = kline_interval.to_string();

        tokio::spawn(async move {
            let mut retry_delay = std::time::Duration::from_secs(5);
            let max_delay = std::time::Duration::from_secs(60);

            loop {
                match tokio_tungstenite::connect_async(&url).await {
                    Ok((ws_stream, _)) => {
                        info!("Gate.io WebSocket connected");
                        retry_delay = std::time::Duration::from_secs(5);
                        let (mut sink, mut read) = ws_stream.split();

                        // Send subscription messages
                        let ts = chrono::Utc::now().timestamp();

                        // Subscribe to order book
                        let sub_book = serde_json::json!({
                            "time": ts,
                            "channel": "spot.order_book",
                            "event": "subscribe",
                            "payload": [pair_clone.clone(), "20", "100ms"]
                        });
                        if let Ok(msg) = serde_json::to_string(&sub_book) {
                            let _ = sink
                                .send(tokio_tungstenite::tungstenite::Message::Text(msg))
                                .await;
                        }

                        // Subscribe to candlesticks
                        let sub_kline = serde_json::json!({
                            "time": ts,
                            "channel": "spot.candlesticks",
                            "event": "subscribe",
                            "payload": [interval, pair_clone.clone()]
                        });
                        if let Ok(msg) = serde_json::to_string(&sub_kline) {
                            let _ = sink
                                .send(tokio_tungstenite::tungstenite::Message::Text(msg))
                                .await;
                        }

                        // Subscribe to trades
                        let sub_trades = serde_json::json!({
                            "time": ts,
                            "channel": "spot.trades",
                            "event": "subscribe",
                            "payload": [pair_clone.clone()]
                        });
                        if let Ok(msg) = serde_json::to_string(&sub_trades) {
                            let _ = sink
                                .send(tokio_tungstenite::tungstenite::Message::Text(msg))
                                .await;
                        }

                        // Read loop
                        let mut ping_interval =
                            tokio::time::interval(std::time::Duration::from_secs(30));

                        loop {
                            tokio::select! {
                                msg = read.next() => {
                                    match msg {
                                        Some(Ok(tokio_tungstenite::tungstenite::Message::Text(text))) => {
                                            if let Some(event) = parse_gate_ws_message(&text) {
                                                if tx.send(event).await.is_err() {
                                                    info!("Gate.io WS receiver dropped");
                                                    return;
                                                }
                                            }
                                        }
                                        Some(Ok(tokio_tungstenite::tungstenite::Message::Close(_))) => {
                                            warn!("Gate.io WebSocket closed by server");
                                            break;
                                        }
                                        Some(Err(e)) => {
                                            error!("Gate.io WS read error: {}", e);
                                            break;
                                        }
                                        None => {
                                            warn!("Gate.io WS stream ended");
                                            break;
                                        }
                                        _ => {}
                                    }
                                }
                                _ = ping_interval.tick() => {
                                    // Send keepalive ping
                                    let ts = chrono::Utc::now().timestamp();
                                    let ping = serde_json::json!({
                                        "time": ts,
                                        "channel": "spot.order_book",
                                        "event": "ping"
                                    });
                                    if let Ok(msg) = serde_json::to_string(&ping) {
                                        let _ = sink
                                            .send(tokio_tungstenite::tungstenite::Message::Text(msg))
                                            .await;
                                    }
                                }
                            }
                        }
                    }
                    Err(e) => {
                        error!("Gate.io WS connect failed: {}", e);
                    }
                }

                warn!(
                    "Gate.io WS reconnecting in {} seconds...",
                    retry_delay.as_secs()
                );
                tokio::time::sleep(retry_delay).await;
                retry_delay = (retry_delay * 2).min(max_delay);
            }
        });

        Ok(rx)
    }

    /// Subscribe to combined streams for multiple trading pairs.
    ///
    /// Subscribes to order book, candlesticks, and trades for all given symbols
    /// over a single WebSocket connection.
    pub async fn subscribe_multi(
        &self,
        symbols: &[String],
        kline_interval: &str,
    ) -> Result<tokio::sync::mpsc::Receiver<WsEvent>> {
        let (tx, rx) = tokio::sync::mpsc::channel(1000);

        let pairs: Vec<String> = symbols
            .iter()
            .map(|s| crate::connector::gateio_rest::to_gate_pair(s))
            .collect();

        info!(
            "Connecting to Gate.io WS for {} pairs",
            pairs.len()
        );

        let url = self.base_url.clone();
        let pairs_clone = pairs.clone();
        let interval = kline_interval.to_string();

        tokio::spawn(async move {
            let mut retry_delay = std::time::Duration::from_secs(5);
            let max_delay = std::time::Duration::from_secs(60);

            loop {
                match tokio_tungstenite::connect_async(&url).await {
                    Ok((ws_stream, _)) => {
                        info!("Gate.io WebSocket connected (multi-pair, {} pairs)", pairs_clone.len());
                        retry_delay = std::time::Duration::from_secs(5);
                        let (mut sink, mut read) = ws_stream.split();

                        // Subscribe each pair to all channels
                        let ts = chrono::Utc::now().timestamp();
                        for pair in &pairs_clone {
                            // Order book
                            let sub_book = serde_json::json!({
                                "time": ts,
                                "channel": "spot.order_book",
                                "event": "subscribe",
                                "payload": [pair.clone(), "20", "100ms"]
                            });
                            if let Ok(msg) = serde_json::to_string(&sub_book) {
                                let _ = sink
                                    .send(tokio_tungstenite::tungstenite::Message::Text(msg))
                                    .await;
                            }

                            // Candlesticks
                            let sub_kline = serde_json::json!({
                                "time": ts,
                                "channel": "spot.candlesticks",
                                "event": "subscribe",
                                "payload": [interval, pair.clone()]
                            });
                            if let Ok(msg) = serde_json::to_string(&sub_kline) {
                                let _ = sink
                                    .send(tokio_tungstenite::tungstenite::Message::Text(msg))
                                    .await;
                            }

                            // Trades
                            let sub_trades = serde_json::json!({
                                "time": ts,
                                "channel": "spot.trades",
                                "event": "subscribe",
                                "payload": [pair.clone()]
                            });
                            if let Ok(msg) = serde_json::to_string(&sub_trades) {
                                let _ = sink
                                    .send(tokio_tungstenite::tungstenite::Message::Text(msg))
                                    .await;
                            }
                        }

                        // Read loop with ping
                        let mut ping_interval =
                            tokio::time::interval(std::time::Duration::from_secs(30));

                        loop {
                            tokio::select! {
                                msg = read.next() => {
                                    match msg {
                                        Some(Ok(tokio_tungstenite::tungstenite::Message::Text(text))) => {
                                            if let Some(event) = parse_gate_ws_message(&text) {
                                                if tx.send(event).await.is_err() {
                                                    info!("Gate.io WS receiver dropped");
                                                    return;
                                                }
                                            }
                                        }
                                        Some(Ok(tokio_tungstenite::tungstenite::Message::Close(_))) => {
                                            warn!("Gate.io WS closed by server");
                                            break;
                                        }
                                        Some(Err(e)) => {
                                            error!("Gate.io WS read error: {}", e);
                                            break;
                                        }
                                        None => {
                                            warn!("Gate.io WS stream ended");
                                            break;
                                        }
                                        _ => {}
                                    }
                                }
                                _ = ping_interval.tick() => {
                                    let ts = chrono::Utc::now().timestamp();
                                    let ping = serde_json::json!({
                                        "time": ts,
                                        "channel": "spot.order_book",
                                        "event": "ping"
                                    });
                                    if let Ok(msg) = serde_json::to_string(&ping) {
                                        let _ = sink
                                            .send(tokio_tungstenite::tungstenite::Message::Text(msg))
                                            .await;
                                    }
                                }
                            }
                        }
                    }
                    Err(e) => {
                        error!("Gate.io WS connect failed: {}", e);
                    }
                }

                warn!("Gate.io WS reconnecting in {} seconds...", retry_delay.as_secs());
                tokio::time::sleep(retry_delay).await;
                retry_delay = (retry_delay * 2).min(max_delay);
            }
        });

        Ok(rx)
    }
}

fn parse_gate_ws_message(text: &str) -> Option<WsEvent> {
    let msg: serde_json::Value = serde_json::from_str(text).ok()?;

    let channel = msg.get("channel")?.as_str()?;
    let event = msg.get("event").and_then(|e| e.as_str()).unwrap_or("");

    // Ignore subscription confirmations and pings
    if event == "subscribe" || event == "ping" || event == "pong" {
        return None;
    }

    match channel {
        "spot.order_book" => {
            let pair = msg.get("result")?.get("s")?.as_str()?;
            let symbol = crate::connector::gateio_rest::from_gate_pair(pair);
            let result = msg.get("result")?;

            let bids = parse_price_levels(result.get("bids"));
            let asks = parse_price_levels(result.get("asks"));

            Some(WsEvent::OrderBookUpdate {
                symbol,
                bids,
                asks,
            })
        }
        "spot.candlesticks" => {
            // Gate.io candlesticks: result is a string with format "n,ts,o,h,l,c,v"
            // where n is the interval name
            let result = msg.get("result")?.as_str()?;
            let parts: Vec<&str> = result.split(',').collect();
            if parts.len() < 7 {
                return None;
            }
            let bar = Bar::new(
                parts[2].parse().ok()?,
                parts[3].parse().ok()?,
                parts[4].parse().ok()?,
                parts[5].parse().ok()?,
                parts[6].parse().ok()?,
                parts[1].parse().ok()?,
            );
            // We don't know the symbol from this response alone — Gate.io embeds it in a generic way
            // The subscribe payload was [interval, pair], but the response doesn't echo the pair directly.
            // For now, return with empty symbol — caller should map via subscription context
            Some(WsEvent::Kline {
                symbol: String::new(), // Caller maps via subscription context
                bar,
                is_closed: false, // Gate.io doesn't flag closed candles explicitly in WS
            })
        }
        "spot.trades" => {
            let result = msg.get("result")?;
            let pair = result.get("currency_pair")?.as_str()?;
            let symbol = crate::connector::gateio_rest::from_gate_pair(pair);

            Some(WsEvent::Trade {
                symbol,
                price: result.get("price")?.as_str()?.parse().ok()?,
                quantity: result.get("amount")?.as_str()?.parse().ok()?,
                buyer_is_maker: false, // Gate.io doesn't provide this field
            })
        }
        _ => None,
    }
}

fn parse_price_levels(arr: Option<&serde_json::Value>) -> Vec<(f64, f64)> {
    arr.and_then(|a| a.as_array())
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|level| {
            // Gate.io order book levels can be arrays [price, qty] or objects {p: "...", s: "..."}
            if level.is_array() {
                let price = level[0].as_str()?.parse::<f64>().ok()?;
                let qty = level[1].as_str()?.parse::<f64>().ok()?;
                Some((price, qty))
            } else {
                let price = level.get("p")?.as_str()?.parse::<f64>().ok()?;
                let qty = level.get("s")?.as_str()?.parse::<f64>().ok()?;
                Some((price, qty))
            }
        })
        .collect()
}
