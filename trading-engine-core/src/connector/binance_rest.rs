use anyhow::Result;
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
}
