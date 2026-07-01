use async_trait::async_trait;
use std::collections::HashMap;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

/// Perpetual mark + funding source. Behind a trait so tests inject a fake.
#[async_trait]
pub trait PerpPriceSource: Send + Sync {
    /// Current perp mark price for the symbol, or None if unavailable.
    async fn mark(&self, symbol: &str) -> Option<f64>;
    /// Current funding rate (e.g. 0.0001 = 0.01%), or None if unavailable.
    async fn funding_rate(&self, symbol: &str) -> Option<f64>;
}

/// Parse Gate.io `/futures/usdt/tickers` JSON for one contract's last + funding.
/// Body is an array: [{"contract":"BTC_USDT","last":"50000","funding_rate":"0.0001", ...}]
/// Returns (last, funding_rate) for `contract`, or None.
pub fn parse_gate_ticker(body: &str, contract: &str) -> Option<(f64, f64)> {
    let v: Vec<serde_json::Value> = serde_json::from_str(body).ok()?;
    for obj in v {
        if obj.get("contract").and_then(|c| c.as_str()) == Some(contract) {
            let last = obj.get("last").and_then(|l| l.as_str()).and_then(|s| s.parse::<f64>().ok())?;
            let fr = obj.get("funding_rate")
                .and_then(|f| f.as_str())
                .and_then(|s| s.parse::<f64>().ok())
                .unwrap_or(0.0);
            return Some((last, fr));
        }
    }
    None
}

/// Gate.io USDT-perpetual source. Caches per-symbol (mark, funding) for `ttl`.
pub struct GateioPerpSource {
    client: reqwest::Client,
    base: String,
    ttl: Duration,
    cache: Mutex<HashMap<String, (f64, f64, Instant)>>, // (mark, funding, fetched_at)
}

impl GateioPerpSource {
    pub fn new() -> Self {
        Self {
            client: reqwest::Client::new(),
            base: "https://api.gateio.ws/api/v4".to_string(),
            ttl: Duration::from_secs(5),
            cache: Mutex::new(HashMap::new()),
        }
    }

    fn contract(symbol: &str) -> String {
        // "BTC-USDT" / "BTCUSDT" -> "BTC_USDT"
        let s = symbol.replace('-', "");
        if let Some(pos) = s.find("USDT") {
            format!("{}_USDT", &s[..pos])
        } else {
            s
        }
    }

    async fn fetch(&self, symbol: &str) -> Option<(f64, f64)> {
        let contract = Self::contract(symbol);
        // Cache hit?
        {
            let c = self.cache.lock().await;
            if let Some(&(m, f, t)) = c.get(&contract) {
                if t.elapsed() < self.ttl { return Some((m, f)); }
            }
        }
        let url = format!("{}/futures/usdt/tickers?contract={}", self.base, contract);
        match self.client.get(&url).send().await {
            Ok(resp) => match resp.text().await {
                Ok(body) => {
                    if let Some((m, f)) = parse_gate_ticker(&body, &contract) {
                        self.cache.lock().await.insert(contract.clone(), (m, f, Instant::now()));
                        Some((m, f))
                    } else { None }
                }
                Err(_) => None,
            },
            Err(_) => None,
        }
    }
}

#[async_trait]
impl PerpPriceSource for GateioPerpSource {
    async fn mark(&self, symbol: &str) -> Option<f64> { self.fetch(symbol).await.map(|(m, _)| m) }
    async fn funding_rate(&self, symbol: &str) -> Option<f64> { self.fetch(symbol).await.map(|(_, f)| f) }
}

/// Test double shared with strategy (trend) tests. `cfg(test)` + `pub(crate)`
/// so trend's test module can `use crate::connector::perp_price::FakePerp;`.
#[cfg(test)]
pub(crate) struct FakePerp {
    pub mark: f64,
    pub funding: f64,
}
#[cfg(test)]
#[async_trait]
impl PerpPriceSource for FakePerp {
    async fn mark(&self, _symbol: &str) -> Option<f64> { Some(self.mark) }
    async fn funding_rate(&self, _symbol: &str) -> Option<f64> { Some(self.funding) }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_last_and_funding_for_contract() {
        let body = r#"[
            {"contract":"ETH_USDT","last":"3000","funding_rate":"0.00005"},
            {"contract":"BTC_USDT","last":"50000","funding_rate":"0.0001"}
        ]"#;
        let (last, fr) = parse_gate_ticker(body, "BTC_USDT").unwrap();
        assert!((last - 50_000.0).abs() < 1e-9);
        assert!((fr - 0.0001).abs() < 1e-9);
    }

    #[test]
    fn returns_none_for_missing_contract() {
        let body = r#"[{"contract":"BTC_USDT","last":"50000","funding_rate":"0.0001"}]"#;
        assert!(parse_gate_ticker(body, "DOGE_USDT").is_none());
    }

    #[test]
    fn missing_funding_defaults_to_zero() {
        let body = r#"[{"contract":"BTC_USDT","last":"50000"}]"#;
        let (last, fr) = parse_gate_ticker(body, "BTC_USDT").unwrap();
        assert!((last - 50_000.0).abs() < 1e-9);
        assert!((fr - 0.0).abs() < 1e-9);
    }

    #[tokio::test]
    async fn fake_source_returns_configured_values() {
        let f = super::FakePerp { mark: 1234.0, funding: 0.0002 };
        assert_eq!(f.mark("BTC-USDT").await, Some(1234.0));
        assert_eq!(f.funding_rate("BTC-USDT").await, Some(0.0002));
    }
}
