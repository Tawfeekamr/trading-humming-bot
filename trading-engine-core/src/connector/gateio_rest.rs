use anyhow::Result;
use crate::connector::Connector;
use crate::connector::types::*;
use crate::models::bar::Bar;
use crate::models::order::OrderSide;
use hmac::{Hmac, Mac};
use reqwest::Client;
use sha2::Sha512;
use std::collections::HashMap;

type HmacSha512 = Hmac<Sha512>;

const BASE_URL: &str = "https://api.gateio.ws/api/v4";
const API_PREFIX: &str = "/api/v4";

/// Convert internal symbol format to Gate.io pair format.
/// "BTCUSDT" → "BTC_USDT", "BTC-USDT" → "BTC_USDT", "BTC_USDT" → "BTC_USDT"
pub fn to_gate_pair(symbol: &str) -> String {
    if symbol.contains('_') {
        return symbol.to_string();
    }
    if symbol.contains('-') {
        return symbol.replace('-', "_");
    }
    // No separator — insert underscore before last 4 chars (USDT, BUSD, etc.)
    // or last 3 chars (BTC, ETH, etc.)
    let quote_coins = ["USDT", "BUSD", "USDC", "TUSD", "BTC", "ETH", "BNB"];
    for quote in &quote_coins {
        if symbol.ends_with(quote) && symbol.len() > quote.len() {
            let base = &symbol[..symbol.len() - quote.len()];
            if !base.is_empty() {
                return format!("{}_{}", base, quote);
            }
        }
    }
    symbol.to_string()
}

/// Convert Gate.io pair format back to internal symbol format.
/// "BTC_USDT" → "BTCUSDT" (no separator, Binance-style)
pub fn from_gate_pair(pair: &str) -> String {
    pair.replace('_', "")
}

pub struct GateioRest {
    client: Client,
    api_key: String,
    api_secret: String,
    base_url: String,
}

impl GateioRest {
    pub fn new(api_key: &str, api_secret: &str) -> Self {
        Self {
            client: Client::new(),
            api_key: api_key.to_string(),
            api_secret: api_secret.to_string(),
            base_url: BASE_URL.to_string(),
        }
    }

    /// Generate authentication headers for a Gate.io API request.
    ///
    /// Signature string format:
    ///   METHOD\nURL_PATH\nQUERY_STRING\nSHA512(BODY)\nTIMESTAMP
    ///
    /// Signature: HexEncode(HMAC_SHA512(secret, signature_string))
    fn sign_request(
        &self,
        method: &str,
        path: &str,
        query: &str,
        body: &str,
    ) -> HashMap<String, String> {
        let timestamp = chrono::Utc::now().timestamp().to_string();

        // Hash the request body (or empty string) with SHA-512
        let body_hash = {
            use sha2::Digest;
            let mut hasher = Sha512::new();
            hasher.update(body.as_bytes());
            hex::encode(hasher.finalize())
        };

        // Build signature string: METHOD\nPATH\nQUERY\nBODY_HASH\nTIMESTAMP
        let sign_string = format!(
            "{}\n{}\n{}\n{}\n{}",
            method, path, query, body_hash, timestamp
        );

        // Compute HMAC-SHA512
        let mut mac =
            HmacSha512::new_from_slice(self.api_secret.as_bytes()).expect("HMAC accepts any key");
        mac.update(sign_string.as_bytes());
        let signature = hex::encode(mac.finalize().into_bytes());

        let mut headers = HashMap::new();
        headers.insert("KEY".to_string(), self.api_key.clone());
        headers.insert("SIGN".to_string(), signature);
        headers.insert("Timestamp".to_string(), timestamp);
        headers
    }

    /// Get order book for a symbol
    pub async fn get_order_book(&self, symbol: &str, limit: u16) -> Result<OrderBook> {
        let pair = to_gate_pair(symbol);
        let url = format!("{}/spot/order_book", self.base_url);

        let resp: serde_json::Value = self
            .client
            .get(&url)
            .query(&[
                ("currency_pair", pair.as_str()),
                ("limit", &limit.to_string()),
            ])
            .send()
            .await?
            .json()
            .await?;

        let bids: Vec<(f64, f64)> = resp["bids"]
            .as_array()
            .unwrap_or(&vec![])
            .iter()
            .filter_map(|b| {
                let price = b[0].as_str()?.parse::<f64>().ok()?;
                let qty = b[1].as_str()?.parse::<f64>().ok()?;
                Some((price, qty))
            })
            .collect();

        let asks: Vec<(f64, f64)> = resp["asks"]
            .as_array()
            .unwrap_or(&vec![])
            .iter()
            .filter_map(|b| {
                let price = b[0].as_str()?.parse::<f64>().ok()?;
                let qty = b[1].as_str()?.parse::<f64>().ok()?;
                Some((price, qty))
            })
            .collect();

        Ok(OrderBook {
            symbol: symbol.to_string(),
            bids,
            asks,
            timestamp: chrono::Utc::now().timestamp_millis(),
        })
    }

    /// Get klines (candlesticks) for a symbol
    pub async fn get_klines(&self, symbol: &str, interval: &str, limit: u16) -> Result<Vec<Bar>> {
        let pair = to_gate_pair(symbol);
        let url = format!("{}/spot/candlesticks", self.base_url);

        // Gate.io interval format uses strings like "1h", "1m", "5m", "1d"
        // Our internal format already uses this style
        let resp: Vec<Vec<serde_json::Value>> = self
            .client
            .get(&url)
            .query(&[
                ("currency_pair", pair.as_str()),
                ("interval", interval),
            ])
            .send()
            .await?
            .json()
            .await?;

        // Gate.io candlesticks response is an array of arrays:
        // [timestamp, volume, close, high, low, open, volume_quote, is_closed]
        // Note: the order is different from Binance!
        let bars: Vec<Bar> = resp
            .iter()
            .take(limit as usize)
            .filter_map(|kline| {
                if kline.len() < 6 {
                    return None;
                }
                Some(Bar::new(
                    kline[5].as_str()?.parse::<f64>().ok()?, // open
                    kline[3].as_str()?.parse::<f64>().ok()?, // high
                    kline[4].as_str()?.parse::<f64>().ok()?, // low
                    kline[2].as_str()?.parse::<f64>().ok()?, // close
                    kline[1].as_str()?.parse::<f64>().ok()?, // volume
                    kline[0].as_str()?.parse::<i64>().ok()?,  // timestamp (seconds)
                ))
            })
            .collect();

        Ok(bars)
    }

    /// Place a new order
    pub async fn place_order(&self, req: &OrderRequest) -> Result<OrderResponse> {
        let pair = to_gate_pair(&req.symbol);
        let path = format!("{}/spot/orders", API_PREFIX);
        let url = format!("{}/spot/orders", self.base_url);

        // Gate.io spot order endpoint supports market/limit (post-only via
        // time_in_force=poc). STOP_MARKET needs the separate conditional-order
        // API and is unsupported here — fail loudly rather than mis-map it.
        let tif_for = |t: TimeInForceReq| -> &'static str {
            match t {
                TimeInForceReq::Gtc => "gtc",
                TimeInForceReq::Ioc => "ioc",
                TimeInForceReq::Fok => "fok",
            }
        };
        let (order_type_str, tif_str): (&str, Option<&str>) = match req.order_type {
            OrderTypeReq::Market => ("market", req.time_in_force.map(tif_for)),
            OrderTypeReq::Limit => ("limit", req.time_in_force.map(tif_for)),
            OrderTypeReq::LimitMaker => ("limit", Some("poc")), // post-only
            OrderTypeReq::StopMarket { .. } => {
                return Err(anyhow::anyhow!(
                    "Gate.io spot does not support STOP_MARKET via /spot/orders (needs conditional-order API)"
                ));
            }
        };

        let body = serde_json::json!({
            "currency_pair": pair,
            "side": match req.side {
                OrderSide::Buy => "buy",
                OrderSide::Sell => "sell",
            },
            "type": order_type_str,
            "amount": req.quantity.to_string(),
            "price": req.price.map(|p| p.to_string()).unwrap_or_default(),
            "time_in_force": tif_str,
            "text": req.client_order_id.clone().unwrap_or_default(),
        });

        let body_str = serde_json::to_string(&body)?;
        let headers = self.sign_request("POST", &path, "", &body_str);

        let mut request = self.client.post(&url);
        for (k, v) in &headers {
            request = request.header(k.as_str(), v.as_str());
        }

        let resp: serde_json::Value = request
            .header("Content-Type", "application/json")
            .body(body_str)
            .send()
            .await?
            .json()
            .await?;

        Ok(OrderResponse {
            order_id: resp["id"].as_str().unwrap_or("").to_string(),
            client_order_id: resp["text"].as_str().map(|s| s.to_string()),
            symbol: req.symbol.clone(),
            side: req.side,
            price: resp["price"]
                .as_str()
                .and_then(|s| s.parse().ok())
                .unwrap_or(0.0),
            quantity: resp["amount"]
                .as_str()
                .and_then(|s| s.parse().ok())
                .unwrap_or(req.quantity),
            status: parse_order_status(resp["status"].as_str().unwrap_or("")),
        })
    }

    /// Cancel an existing order
    pub async fn cancel_order(&self, symbol: &str, order_id: &str) -> Result<()> {
        let pair = to_gate_pair(symbol);
        let path = format!("{}/spot/orders/{}", API_PREFIX, order_id);
        let query = format!("currency_pair={}", pair);
        let url = format!("{}/spot/orders/{}?currency_pair={}", self.base_url, order_id, pair);

        let headers = self.sign_request("DELETE", &path, &query, "");

        let mut request = self.client.delete(&url);
        for (k, v) in &headers {
            request = request.header(k.as_str(), v.as_str());
        }

        request.send().await?;
        Ok(())
    }

    /// Cancel all open orders for a symbol.
    /// Gate.io has no batch cancel for spot, so we list and cancel individually.
    pub async fn cancel_all_orders(&self, symbol: &str) -> Result<Vec<CancelResult>> {
        let open_orders = self.get_open_orders(symbol).await?;
        let mut results = Vec::new();

        for order in &open_orders {
            match self.cancel_order(symbol, &order.order_id).await {
                Ok(()) => results.push(CancelResult {
                    order_id: order.order_id.clone(),
                    symbol: symbol.to_string(),
                }),
                Err(e) => {
                    tracing::warn!("Failed to cancel order {}: {}", order.order_id, e);
                }
            }
        }

        Ok(results)
    }

    /// Get open orders for a symbol
    pub async fn get_open_orders(&self, symbol: &str) -> Result<Vec<OpenOrder>> {
        let pair = to_gate_pair(symbol);
        let path = format!("{}/spot/orders", API_PREFIX);
        let query = format!("currency_pair={}&status=open", pair);
        let url = format!(
            "{}/spot/orders?currency_pair={}&status=open",
            self.base_url, pair
        );

        let headers = self.sign_request("GET", &path, &query, "");

        let mut request = self.client.get(&url);
        for (k, v) in &headers {
            request = request.header(k.as_str(), v.as_str());
        }

        let resp: Vec<serde_json::Value> = request.send().await?.json().await?;

        Ok(resp
            .iter()
            .filter_map(|o| {
                Some(OpenOrder {
                    order_id: o["id"].as_str()?.to_string(),
                    symbol: from_gate_pair(o["currency_pair"].as_str()?),
                    side: match o["side"].as_str()? {
                        "buy" => OrderSide::Buy,
                        _ => OrderSide::Sell,
                    },
                    price: o["price"].as_str()?.parse().ok()?,
                    quantity: o["amount"].as_str()?.parse().ok()?,
                    filled_quantity: o["filled_total"]
                        .as_str()
                        .and_then(|s| s.parse().ok())
                        .unwrap_or(0.0),
                    status: parse_order_status(o["status"].as_str()?),
                })
            })
            .collect())
    }

    /// Get account balances
    pub async fn get_balances(&self) -> Result<HashMap<String, f64>> {
        let path = format!("{}/spot/accounts", API_PREFIX);
        let url = format!("{}/spot/accounts", self.base_url);

        let headers = self.sign_request("GET", &path, "", "");

        let mut request = self.client.get(&url);
        for (k, v) in &headers {
            request = request.header(k.as_str(), v.as_str());
        }

        let resp: Vec<serde_json::Value> = request.send().await?.json().await?;

        let mut balances = HashMap::new();
        for b in &resp {
            let currency = b["currency"].as_str().unwrap_or("").to_string();
            let available: f64 = b["available"]
                .as_str()
                .and_then(|s| s.parse().ok())
                .unwrap_or(0.0);
            let locked: f64 = b["locked"]
                .as_str()
                .and_then(|s| s.parse().ok())
                .unwrap_or(0.0);
            let total = available + locked;
            if total > 0.0 {
                balances.insert(currency, total);
            }
        }
        Ok(balances)
    }

    /// Get exchange info (currency pair details)
    pub async fn get_currency_pairs(&self) -> Result<serde_json::Value> {
        let url = format!("{}/spot/currency_pairs", self.base_url);
        let resp = self.client.get(&url).send().await?.json().await?;
        Ok(resp)
    }
}

/// Wrapper that implements the Connector trait for Gate.io
pub struct GateioConnector {
    rest: GateioRest,
}

impl GateioConnector {
    pub fn new(api_key: &str, api_secret: &str) -> Self {
        Self {
            rest: GateioRest::new(api_key, api_secret),
        }
    }
}

#[async_trait::async_trait]
impl Connector for GateioConnector {
    async fn place_order(&self, req: &OrderRequest) -> Result<OrderResponse> {
        self.rest.place_order(req).await
    }

    async fn cancel_order(&self, symbol: &str, order_id: &str) -> Result<()> {
        self.rest.cancel_order(symbol, order_id).await
    }

    async fn cancel_all_orders(&self, symbol: &str) -> Result<Vec<CancelResult>> {
        self.rest.cancel_all_orders(symbol).await
    }

    async fn get_balances(&self) -> Result<HashMap<String, f64>> {
        self.rest.get_balances().await
    }

    async fn get_open_orders(&self, symbol: &str) -> Result<Vec<OpenOrder>> {
        self.rest.get_open_orders(symbol).await
    }

    async fn get_order_book(&self, symbol: &str, limit: u16) -> Result<OrderBook> {
        self.rest.get_order_book(symbol, limit).await
    }

    async fn get_klines(
        &self,
        symbol: &str,
        interval: &str,
        limit: u16,
    ) -> Result<Vec<crate::models::bar::Bar>> {
        self.rest.get_klines(symbol, interval, limit).await
    }
}

fn parse_order_status(s: &str) -> OrderStatus {
    match s {
        "open" => OrderStatus::New,
        "closed" => OrderStatus::Filled,
        "cancelled" => OrderStatus::Canceled,
        _ => OrderStatus::Rejected,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_to_gate_pair_no_separator() {
        assert_eq!(to_gate_pair("BTCUSDT"), "BTC_USDT");
        assert_eq!(to_gate_pair("ETHUSDT"), "ETH_USDT");
        assert_eq!(to_gate_pair("SOLUSDT"), "SOL_USDT");
    }

    #[test]
    fn test_to_gate_pair_hyphen() {
        assert_eq!(to_gate_pair("BTC-USDT"), "BTC_USDT");
        assert_eq!(to_gate_pair("ETH-BTC"), "ETH_BTC");
    }

    #[test]
    fn test_to_gate_pair_already_underscore() {
        assert_eq!(to_gate_pair("BTC_USDT"), "BTC_USDT");
    }

    #[test]
    fn test_from_gate_pair() {
        assert_eq!(from_gate_pair("BTC_USDT"), "BTCUSDT");
        assert_eq!(from_gate_pair("ETH_BTC"), "ETHBTC");
    }

    #[test]
    fn test_signature_generation() {
        // Verify that sign_request produces deterministic output
        let rest = GateioRest::new("test_key", "test_secret");
        let headers = rest.sign_request(
            "GET",
            "/api/v4/spot/accounts",
            "",
            "",
        );

        assert_eq!(headers.get("KEY").unwrap(), "test_key");
        assert!(headers.contains_key("SIGN"));
        assert!(headers.contains_key("Timestamp"));

        // Timestamp should be a valid Unix timestamp in seconds
        let ts: i64 = headers["Timestamp"].parse().unwrap();
        assert!(ts > 1_700_000_000); // After 2023
    }

    #[test]
    fn test_parse_order_status() {
        assert_eq!(parse_order_status("open"), OrderStatus::New);
        assert_eq!(parse_order_status("closed"), OrderStatus::Filled);
        assert_eq!(parse_order_status("cancelled"), OrderStatus::Canceled);
        assert_eq!(parse_order_status("unknown"), OrderStatus::Rejected);
    }

    #[tokio::test]
    #[ignore] // Requires network access
    async fn test_get_order_book_public() {
        let client = GateioRest::new("", "");
        let book = client.get_order_book("BTC_USDT", 5).await.unwrap();
        assert_eq!(book.symbol, "BTC_USDT");
        assert!(!book.bids.is_empty());
        assert!(!book.asks.is_empty());
        assert!(book.best_bid().unwrap() > 0.0);
    }

    #[tokio::test]
    #[ignore] // Requires network access
    async fn test_get_klines_public() {
        let client = GateioRest::new("", "");
        let bars = client.get_klines("BTCUSDT", "1h", 10).await.unwrap();
        assert!(!bars.is_empty());
        assert!(bars[0].close > 0.0);
        assert!(bars[0].volume > 0.0);
    }
}
