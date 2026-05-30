use anyhow::Result;
use futures::StreamExt;
use tracing::{info, warn, error};

use crate::models::bar::Bar;

/// Messages received from Binance WebSocket streams
#[derive(Debug, Clone)]
pub enum WsEvent {
    OrderBookUpdate {
        symbol: String,
        bids: Vec<(f64, f64)>,
        asks: Vec<(f64, f64)>,
    },
    Kline {
        symbol: String,
        bar: Bar,
        is_closed: bool,
    },
    Trade {
        symbol: String,
        price: f64,
        quantity: f64,
        buyer_is_maker: bool,
    },
    AccountUpdate(serde_json::Value),
}

/// Manages Binance WebSocket connections
pub struct BinanceWs {
    base_url: String,
}

impl BinanceWs {
    pub fn new(testnet: bool) -> Self {
        let base_url = if testnet {
            "wss://testnet.binance.vision".to_string()
        } else {
            "wss://stream.binance.com:9443".to_string()
        };
        Self { base_url }
    }

    /// Subscribe to combined streams for a trading pair
    pub async fn subscribe(
        &self,
        symbol: &str,
        kline_interval: &str,
    ) -> Result<tokio::sync::mpsc::Receiver<WsEvent>> {
        let streams = format!(
            "{}/@depth20@100ms/{}@kline_{}/{}@trade",
            symbol.to_lowercase(),
            symbol.to_lowercase(),
            kline_interval,
            symbol.to_lowercase()
        );
        let url = format!("{}/stream?streams={}", self.base_url, streams);
        let (tx, rx) = tokio::sync::mpsc::channel(1000);

        info!("Connecting to Binance WS: {}", url);

        let url_clone = url.clone();
        tokio::spawn(async move {
            let mut retry_delay = std::time::Duration::from_secs(5);
            let max_delay = std::time::Duration::from_secs(60);

            loop {
                match tokio_tungstenite::connect_async(&url_clone).await {
                    Ok((ws_stream, _)) => {
                        info!("Binance WebSocket connected");
                        retry_delay = std::time::Duration::from_secs(5); // reset on success
                        let (_, mut read) = ws_stream.split();

                        while let Some(msg) = read.next().await {
                            match msg {
                                Ok(tokio_tungstenite::tungstenite::Message::Text(text)) => {
                                    if let Some(event) = parse_ws_message(&text) {
                                        if tx.send(event).await.is_err() {
                                            info!("WebSocket receiver dropped, closing connection");
                                            return;
                                        }
                                    }
                                }
                                Ok(tokio_tungstenite::tungstenite::Message::Close(_)) => {
                                    warn!("WebSocket closed by server");
                                    break;
                                }
                                Err(e) => {
                                    error!("WebSocket read error: {}", e);
                                    break;
                                }
                                _ => {}
                            }
                        }
                    }
                    Err(e) => {
                        error!("WebSocket connect failed: {}", e);
                    }
                }

                warn!("Reconnecting in {} seconds...", retry_delay.as_secs());
                tokio::time::sleep(retry_delay).await;
                retry_delay = (retry_delay * 2).min(max_delay);
            }
        });

        Ok(rx)
    }

    /// Subscribe to combined streams for multiple trading pairs
    pub async fn subscribe_multi(
        &self,
        symbols: &[String],
        kline_interval: &str,
    ) -> Result<tokio::sync::mpsc::Receiver<WsEvent>> {
        let streams: Vec<String> = symbols.iter().flat_map(|symbol| {
            let sym = symbol.to_lowercase().replace('-', "");
            vec![
                format!("{}/@depth20@100ms", sym),
                format!("{}@kline_{}", sym, kline_interval),
                format!("{}@trade", sym),
            ]
        }).collect();

        let streams_path = streams.join("/");
        let url = format!("{}/stream?streams={}", self.base_url, streams_path);
        let (tx, rx) = tokio::sync::mpsc::channel(1000);

        info!("Connecting to Binance WS ({} pairs): {}", symbols.len(), url);

        let url_clone = url.clone();
        tokio::spawn(async move {
            let mut retry_delay = std::time::Duration::from_secs(5);
            let max_delay = std::time::Duration::from_secs(60);

            loop {
                match tokio_tungstenite::connect_async(&url_clone).await {
                    Ok((ws_stream, _)) => {
                        info!("Binance WebSocket connected (multi-pair)");
                        retry_delay = std::time::Duration::from_secs(5); // reset on success
                        let (_, mut read) = ws_stream.split();

                        while let Some(msg) = read.next().await {
                            match msg {
                                Ok(tokio_tungstenite::tungstenite::Message::Text(text)) => {
                                    if let Some(event) = parse_ws_message(&text) {
                                        if tx.send(event).await.is_err() {
                                            info!("WebSocket receiver dropped, closing connection");
                                            return;
                                        }
                                    }
                                }
                                Ok(tokio_tungstenite::tungstenite::Message::Close(_)) => {
                                    warn!("WebSocket closed by server");
                                    break;
                                }
                                Err(e) => {
                                    error!("WebSocket read error: {}", e);
                                    break;
                                }
                                _ => {}
                            }
                        }
                    }
                    Err(e) => {
                        error!("WebSocket connect failed: {}", e);
                    }
                }

                warn!("Reconnecting in {} seconds...", retry_delay.as_secs());
                tokio::time::sleep(retry_delay).await;
                retry_delay = (retry_delay * 2).min(max_delay);
            }
        });

        Ok(rx)
    }
}

fn parse_ws_message(text: &str) -> Option<WsEvent> {
    let msg: serde_json::Value = serde_json::from_str(text).ok()?;
    let data = msg.get("data")?;
    let stream = msg.get("stream")?.as_str()?;

    if stream.contains("@depth") {
        let symbol = extract_symbol(stream);
        let bids = parse_price_levels(data.get("bids")?);
        let asks = parse_price_levels(data.get("asks")?);
        Some(WsEvent::OrderBookUpdate { symbol, bids, asks })
    } else if stream.contains("@kline") {
        let symbol = extract_symbol(stream);
        let k = data.get("k")?;
        let is_closed = k.get("x")?.as_bool()?;
        let bar = Bar::new(
            k.get("o")?.as_str()?.parse().ok()?,
            k.get("h")?.as_str()?.parse().ok()?,
            k.get("l")?.as_str()?.parse().ok()?,
            k.get("c")?.as_str()?.parse().ok()?,
            k.get("v")?.as_str()?.parse().ok()?,
            k.get("t")?.as_i64()?,
        );
        Some(WsEvent::Kline { symbol, bar, is_closed })
    } else if stream.contains("@trade") {
        let symbol = extract_symbol(stream);
        Some(WsEvent::Trade {
            symbol,
            price: data.get("p")?.as_str()?.parse().ok()?,
            quantity: data.get("q")?.as_str()?.parse().ok()?,
            buyer_is_maker: data.get("m")?.as_bool()?,
        })
    } else {
        None
    }
}

fn extract_symbol(stream: &str) -> String {
    stream.split('@').next().unwrap_or("").to_uppercase()
}

fn parse_price_levels(arr: &serde_json::Value) -> Vec<(f64, f64)> {
    arr.as_array()
        .unwrap_or(&vec![])
        .iter()
        .filter_map(|level| {
            let price = level[0].as_str()?.parse::<f64>().ok()?;
            let qty = level[1].as_str()?.parse::<f64>().ok()?;
            Some((price, qty))
        })
        .collect()
}
