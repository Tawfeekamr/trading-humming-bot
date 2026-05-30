use anyhow::Result;
use crate::connector::types::*;
use crate::models::bar::Bar;
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
}
