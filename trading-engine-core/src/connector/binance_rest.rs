use anyhow::Result;
use crate::connector::Connector;
use crate::connector::types::*;
use crate::models::bar::Bar;
use crate::models::order::OrderSide;
use hmac::{Hmac, Mac};
use reqwest::Client;
use sha2::Sha256;
use std::collections::HashMap;

type HmacSha256 = Hmac<Sha256>;

pub struct BinanceRest {
    client: Client,
    api_key: String,
    api_secret: String,
    base_url: String,
    recv_window: u64,
}

impl BinanceRest {
    pub fn new(api_key: &str, api_secret: &str, testnet: bool) -> Self {
        let base_url = if testnet {
            "https://testnet.binance.vision".to_string()
        } else {
            "https://api.binance.com".to_string()
        };
        Self {
            client: Client::new(),
            api_key: api_key.to_string(),
            api_secret: api_secret.to_string(),
            base_url,
            recv_window: 5000,
        }
    }

    pub fn build_signed_query(&self, params: &HashMap<String, String>) -> String {
        let mut sorted_params: Vec<_> = params.iter().collect();
        sorted_params.sort_by_key(|a| a.0);
        
        let mut query_parts = Vec::new();
        for (k, v) in sorted_params {
            query_parts.push(format!("{}={}", k, urlencoding::encode(v)));
        }
        
        query_parts.push(format!("timestamp={}", chrono::Utc::now().timestamp_millis()));
        query_parts.push(format!("recvWindow={}", self.recv_window));
        
        let query = query_parts.join("&");
        
        let mut mac = HmacSha256::new_from_slice(self.api_secret.as_bytes())
            .expect("HMAC can take key of any size");
        mac.update(query.as_bytes());
        let signature = hex::encode(mac.finalize().into_bytes());
        
        format!("{}&signature={}", query, signature)
    }

    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    pub fn client(&self) -> &Client {
        &self.client
    }

    pub fn api_key(&self) -> &str {
        &self.api_key
    }

    /// Get order book for a symbol
    pub async fn get_order_book(&self, symbol: &str, limit: u16) -> Result<OrderBook> {
        let url = format!("{}/api/v3/depth", self.base_url);
        let resp: serde_json::Value = self.client
            .get(&url)
            .query(&[("symbol", symbol), ("limit", &limit.to_string())])
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

    /// Get klines (candlestick data) for a symbol
    pub async fn get_klines(&self, symbol: &str, interval: &str, limit: u16) -> Result<Vec<Bar>> {
        let url = format!("{}/api/v3/klines", self.base_url);
        let resp: Vec<Vec<serde_json::Value>> = self.client
            .get(&url)
            .query(&[
                ("symbol", symbol),
                ("interval", interval),
                ("limit", &limit.to_string()),
            ])
            .send()
            .await?
            .json()
            .await?;

        let bars: Vec<Bar> = resp.iter().filter_map(|kline| {
            Some(Bar::new(
                kline[1].as_str()?.parse::<f64>().ok()?,
                kline[2].as_str()?.parse::<f64>().ok()?,
                kline[3].as_str()?.parse::<f64>().ok()?,
                kline[4].as_str()?.parse::<f64>().ok()?,
                kline[5].as_str()?.parse::<f64>().ok()?,
                kline[0].as_i64()?,
            ))
        }).collect();

        Ok(bars)
    }

    /// Get exchange info (tick/step sizes)
    pub async fn get_exchange_info(&self) -> Result<serde_json::Value> {
        let url = format!("{}/api/v3/exchangeInfo", self.base_url);
        let resp = self.client.get(&url).send().await?.json().await?;
        Ok(resp)
    }

    /// Place a new order
    pub async fn place_order(&self, req: &OrderRequest) -> Result<OrderResponse> {
        let url = format!("{}/api/v3/order", self.base_url);
        let params = build_spot_order_params(req);
        let body = self.build_signed_query(&params);

        let resp = self.client
            .post(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .header(reqwest::header::CONTENT_TYPE, "application/x-www-form-urlencoded")
            .body(body)
            .send()
            .await?;

        if !resp.status().is_success() {
            let err_text = resp.text().await?;
            anyhow::bail!("Place order rejected: {}", err_text);
        }

        let resp: OrderResponse = resp.json().await?;
        Ok(resp)
    }

    /// Cancel an existing order
    pub async fn cancel_order(&self, symbol: &str, order_id: &str) -> Result<()> {
        let mut params = HashMap::new();
        params.insert("symbol".to_string(), symbol.to_string());
        params.insert("orderId".to_string(), order_id.to_string());
        let query = self.build_signed_query(&params);
        let url = format!("{}/api/v3/order?{}", self.base_url, query);

        let resp = self.client
            .delete(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .send()
            .await?;

        if !resp.status().is_success() {
            let err_text = resp.text().await?;
            anyhow::bail!("Cancel order rejected: {}", err_text);
        }

        Ok(())
    }

    /// Cancel all open orders for a symbol
    pub async fn cancel_all_orders(&self, symbol: &str) -> Result<Vec<CancelResult>> {
        let mut params = HashMap::new();
        params.insert("symbol".to_string(), symbol.to_string());
        let query = self.build_signed_query(&params);
        let url = format!("{}/api/v3/openOrders?{}", self.base_url, query);

        let resp = self.client
            .delete(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .send()
            .await?;
            
        if !resp.status().is_success() {
            let err_text = resp.text().await?;
            anyhow::bail!("Cancel all orders rejected: {}", err_text);
        }
        
        let resp: Vec<serde_json::Value> = resp.json().await?;

        Ok(resp.iter().map(|o| CancelResult {
            order_id: o["orderId"].as_u64().unwrap_or(0).to_string(),
            symbol: o["symbol"].as_str().unwrap_or("").to_string(),
        }).collect())
    }

    /// Get open orders for a symbol
    pub async fn get_open_orders(&self, symbol: &str) -> Result<Vec<OpenOrder>> {
        let mut params = HashMap::new();
        params.insert("symbol".to_string(), symbol.to_string());
        let query = self.build_signed_query(&params);
        let url = format!("{}/api/v3/openOrders?{}", self.base_url, query);

        let resp = self.client
            .get(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .send()
            .await?;
            
        if !resp.status().is_success() {
            let err_text = resp.text().await?;
            anyhow::bail!("Get open orders rejected: {}", err_text);
        }
        
        let resp: Vec<serde_json::Value> = resp.json().await?;

        Ok(resp.iter().filter_map(|o| {
            Some(OpenOrder {
                order_id: o["orderId"].as_u64()?.to_string(),
                symbol: o["symbol"].as_str()?.to_string(),
                side: match o["side"].as_str()? {
                    "BUY" => OrderSide::Buy,
                    _ => OrderSide::Sell,
                },
                price: o["price"].as_str()?.parse().ok()?,
                quantity: o["origQty"].as_str()?.parse().ok()?,
                filled_quantity: o["executedQty"].as_str()?.parse().ok()?,
                status: parse_order_status(o["status"].as_str()?),
            })
        }).collect())
    }

    /// Get account balances
    pub async fn get_balances(&self) -> Result<HashMap<String, f64>> {
        let params = HashMap::new();
        let query = self.build_signed_query(&params);
        let url = format!("{}/api/v3/account?{}", self.base_url, query);

        let resp = self.client
            .get(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .send()
            .await?;
            
        if !resp.status().is_success() {
            let err_text = resp.text().await?;
            anyhow::bail!("Get balances rejected: {}", err_text);
        }
        
        let resp: serde_json::Value = resp.json().await?;

        let mut balances = HashMap::new();
        if let Some(balances_arr) = resp["balances"].as_array() {
            for b in balances_arr {
                let asset = b["asset"].as_str().unwrap_or("").to_string();
                let free: f64 = b["free"].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                let locked: f64 = b["locked"].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                let total = free + locked;
                if total > 0.0 {
                    balances.insert(asset, total);
                }
            }
        }
        Ok(balances)
    }
}

/// Wrapper that implements the Connector trait for Binance
pub struct BinanceConnector {
    rest: BinanceRest,
}

impl BinanceConnector {
    pub fn new(api_key: &str, api_secret: &str, testnet: bool) -> Self {
        Self {
            rest: BinanceRest::new(api_key, api_secret, testnet),
        }
    }
}

#[async_trait::async_trait]
impl Connector for BinanceConnector {
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

    async fn get_klines(&self, symbol: &str, interval: &str, limit: u16) -> Result<Vec<crate::models::bar::Bar>> {
        self.rest.get_klines(symbol, interval, limit).await
    }
}

fn parse_order_status(s: &str) -> OrderStatus {
    match s {
        "NEW" => OrderStatus::New,
        "PARTIALLY_FILLED" => OrderStatus::PartiallyFilled,
        "FILLED" => OrderStatus::Filled,
        "CANCELED" => OrderStatus::Canceled,
        _ => OrderStatus::Rejected,
    }
}

/// Build the order-type-specific params for the Binance **Spot** order endpoint
/// (`POST /api/v3/order`) — everything except `timestamp`/`recvWindow`/`signature`,
/// which `sign_request` appends. Pure and unit-tested so a rejected param set can
/// never slip through unnoticed.
///
/// Two spot-specific traps this encodes (both reject the order silently if wrong):
///   - `reduceOnly` is **futures-only**. Spot has no such param; sending it
///     trips `-1104 "Not all sent parameters were read"`. On spot, long-only is
///     enforced by sizing (never sell more base than held), not a server flag.
///     The engine still reads `OrderRequest.reduce_only` locally to bypass the
///     circuit breaker on exits — that path is independent of this wire param.
///   - There is **no `STOP_MARKET` on spot**. The market-on-trigger stop is
///     `STOP_LOSS` (params: `quantity` + `stopPrice`; no `price`, no `timeInForce`).
pub fn build_spot_order_params(req: &OrderRequest) -> HashMap<String, String> {
    let mut params = HashMap::new();
    params.insert("symbol".to_string(), req.symbol.clone());
    params.insert("side".to_string(), match req.side {
        OrderSide::Buy => "BUY".to_string(),
        OrderSide::Sell => "SELL".to_string(),
    });
    params.insert("type".to_string(), match req.order_type {
        OrderTypeReq::Market => "MARKET",
        OrderTypeReq::Limit => "LIMIT",
        OrderTypeReq::LimitMaker => "LIMIT_MAKER",
        // STOP_MARKET is futures-only; the spot market-on-trigger stop is STOP_LOSS.
        OrderTypeReq::StopMarket { .. } => "STOP_LOSS",
    }.to_string());
    let fmt_float = |f: f64| -> String {
        let mut s = format!("{:.8}", f);
        s = s.trim_end_matches('0').to_string();
        s.trim_end_matches('.').to_string()
    };
    params.insert("quantity".to_string(), fmt_float(req.quantity));
    // LIMIT / LIMIT_MAKER carry a price. STOP_LOSS is marketable on trigger and
    // must NOT send `price` — it carries `stopPrice` instead.
    if let Some(price) = req.price {
        if !matches!(req.order_type, OrderTypeReq::StopMarket { .. }) {
            params.insert("price".to_string(), fmt_float(price));
        }
    }
    if let OrderTypeReq::StopMarket { stop_price } = req.order_type {
        params.insert("stopPrice".to_string(), fmt_float(stop_price));
    }
    // timeInForce is valid for LIMIT here; omit for LIMIT_MAKER (implied
    // post-only) and STOP_LOSS (market-on-trigger).
    if !matches!(req.order_type, OrderTypeReq::LimitMaker | OrderTypeReq::StopMarket { .. }) {
        if let Some(tif) = &req.time_in_force {
            params.insert("timeInForce".to_string(), match tif {
                TimeInForceReq::Gtc => "GTC".to_string(),
                TimeInForceReq::Ioc => "IOC".to_string(),
                TimeInForceReq::Fok => "FOK".to_string(),
            });
        }
    }
    // reduceOnly is deliberately NOT sent — see the doc comment above.
    if let Some(ref id) = req.client_order_id {
        params.insert("newClientOrderId".to_string(), id.clone());
    }
    params
}

#[cfg(test)]
mod tests {
    use super::*;

    fn req(order_type: OrderTypeReq, reduce_only: bool) -> OrderRequest {
        OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Sell,
            order_type,
            price: None,
            quantity: 0.5,
            time_in_force: Some(TimeInForceReq::Gtc),
            client_order_id: Some("c1".to_string()),
            reduce_only,
        }
    }

    #[test]
    fn reduce_only_is_never_sent_on_spot() {
        // Every order type must omit reduceOnly — it's futures-only and Binance
        // Spot rejects it with -1104. This is the test a silently-rejected stop
        // can never pass.
        for ot in [
            OrderTypeReq::Market,
            OrderTypeReq::Limit,
            OrderTypeReq::LimitMaker,
            OrderTypeReq::StopMarket { stop_price: 50_000.0 },
        ] {
            let params = build_spot_order_params(&req(ot, true));
            assert!(!params.contains_key("reduceOnly"),
                "reduceOnly must not be sent on spot for {:?}", ot);
        }
    }

    #[test]
    fn stop_market_maps_to_spot_stop_loss() {
        let params = build_spot_order_params(&req(
            OrderTypeReq::StopMarket { stop_price: 50_000.0 },
            true,
        ));
        assert_eq!(params.get("type").unwrap(), "STOP_LOSS",
            "spot has no STOP_MARKET — must emit STOP_LOSS");
        assert_eq!(params.get("stopPrice").unwrap(), "50000");
        // STOP_LOSS must not carry a price (marketable on trigger).
        assert!(!params.contains_key("price"));
        // timeInForce is not valid for a market-on-trigger stop.
        assert!(!params.contains_key("timeInForce"));
    }

    #[test]
    fn limit_maker_omits_time_in_force() {
        let mut r = req(OrderTypeReq::LimitMaker, false);
        r.price = Some(50_000.0);
        let params = build_spot_order_params(&r);
        assert_eq!(params.get("type").unwrap(), "LIMIT_MAKER");
        assert_eq!(params.get("price").unwrap(), "50000");
        assert!(!params.contains_key("timeInForce"),
            "LIMIT_MAKER is post-only; timeInForce must be omitted");
    }

    #[test]
    fn limit_carries_price_and_time_in_force() {
        let mut r = req(OrderTypeReq::Limit, false);
        r.price = Some(50_000.0);
        let params = build_spot_order_params(&r);
        assert_eq!(params.get("type").unwrap(), "LIMIT");
        assert_eq!(params.get("price").unwrap(), "50000");
        assert_eq!(params.get("timeInForce").unwrap(), "GTC");
    }

    #[test]
    fn market_order_params() {
        let params = build_spot_order_params(&req(OrderTypeReq::Market, false));
        assert_eq!(params.get("type").unwrap(), "MARKET");
        assert_eq!(params.get("quantity").unwrap(), "0.5");
        assert!(!params.contains_key("price"));
        assert!(!params.contains_key("stopPrice"));
    }
}
