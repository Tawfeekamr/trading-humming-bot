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

    pub fn sign_request(&self, params: &mut HashMap<String, String>) {
        let timestamp = chrono::Utc::now().timestamp_millis().to_string();
        params.insert("timestamp".to_string(), timestamp);
        params.insert("recvWindow".to_string(), self.recv_window.to_string());

        let query: String = params
            .iter()
            .map(|(k, v)| format!("{}={}", k, v))
            .collect::<Vec<_>>()
            .join("&");

        let mut mac = HmacSha256::new_from_slice(self.api_secret.as_bytes())
            .expect("HMAC can take key of any size");
        mac.update(query.as_bytes());
        let signature = hex::encode(mac.finalize().into_bytes());
        params.insert("signature".to_string(), signature);
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
        let mut params = HashMap::new();
        params.insert("symbol".to_string(), req.symbol.clone());
        params.insert("side".to_string(), match req.side {
            OrderSide::Buy => "BUY".to_string(),
            OrderSide::Sell => "SELL".to_string(),
        });
        params.insert("type".to_string(), match req.order_type {
            OrderTypeReq::Market => "MARKET".to_string(),
            OrderTypeReq::Limit => "LIMIT".to_string(),
        });
        params.insert("quantity".to_string(), req.quantity.to_string());
        if let Some(price) = req.price {
            params.insert("price".to_string(), price.to_string());
        }
        if let Some(tif) = &req.time_in_force {
            params.insert("timeInForce".to_string(), match tif {
                TimeInForceReq::Gtc => "GTC".to_string(),
                TimeInForceReq::Ioc => "IOC".to_string(),
                TimeInForceReq::Fok => "FOK".to_string(),
            });
        }
        if let Some(ref id) = req.client_order_id {
            params.insert("newClientOrderId".to_string(), id.clone());
        }
        self.sign_request(&mut params);

        let resp: OrderResponse = self.client
            .post(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .form(&params)
            .send()
            .await?
            .json()
            .await?;

        Ok(resp)
    }

    /// Cancel an existing order
    pub async fn cancel_order(&self, symbol: &str, order_id: &str) -> Result<()> {
        let url = format!("{}/api/v3/order", self.base_url);
        let mut params = HashMap::new();
        params.insert("symbol".to_string(), symbol.to_string());
        params.insert("orderId".to_string(), order_id.to_string());
        self.sign_request(&mut params);

        self.client
            .delete(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .form(&params)
            .send()
            .await?;

        Ok(())
    }

    /// Cancel all open orders for a symbol
    pub async fn cancel_all_orders(&self, symbol: &str) -> Result<Vec<CancelResult>> {
        let url = format!("{}/api/v3/openOrders", self.base_url);
        let mut params = HashMap::new();
        params.insert("symbol".to_string(), symbol.to_string());
        self.sign_request(&mut params);

        let resp: Vec<serde_json::Value> = self.client
            .delete(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .form(&params)
            .send()
            .await?
            .json()
            .await?;

        Ok(resp.iter().map(|o| CancelResult {
            order_id: o["orderId"].as_u64().unwrap_or(0).to_string(),
            symbol: o["symbol"].as_str().unwrap_or("").to_string(),
        }).collect())
    }

    /// Get open orders for a symbol
    pub async fn get_open_orders(&self, symbol: &str) -> Result<Vec<OpenOrder>> {
        let url = format!("{}/api/v3/openOrders", self.base_url);
        let mut params = HashMap::new();
        params.insert("symbol".to_string(), symbol.to_string());
        self.sign_request(&mut params);

        let resp: Vec<serde_json::Value> = self.client
            .get(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .query(&params)
            .send()
            .await?
            .json()
            .await?;

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
        let url = format!("{}/api/v3/account", self.base_url);
        let mut params = HashMap::new();
        self.sign_request(&mut params);

        let resp: serde_json::Value = self.client
            .get(&url)
            .header("X-MBX-APIKEY", &self.api_key)
            .query(&params)
            .send()
            .await?
            .json()
            .await?;

        let mut balances = HashMap::new();
        if let Some(balances_arr) = resp["balances"].as_array() {
            for b in balances_arr {
                let asset = b["asset"].as_str().unwrap_or("").to_string();
                let free: f64 = b["free"].as_str().unwrap_or("0").parse().unwrap_or(0.0);
                if free > 0.0 {
                    balances.insert(asset, free);
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
