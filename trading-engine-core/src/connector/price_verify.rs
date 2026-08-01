//! Cross-source price verification for the price-sanity filter.
//!
//! Binance's public ticker endpoint is used deliberately: verification does
//! not require credentials and therefore cannot accidentally expose API keys.
//! Network, HTTP, and decoding failures are fail-safe and become
//! [`VerifyResult::Unavailable`].

use async_trait::async_trait;
use std::time::Duration;

use crate::price_filter::VerifyResult;

const BINANCE_BASE_URL: &str = "https://api.binance.com";
const REQUEST_TIMEOUT: Duration = Duration::from_millis(1_500);

/// Classify a suspect mid-price against an optional REST reference price.
///
/// A REST response is considered usable only when all values involved in the
/// comparison are finite and strictly positive. If the REST price is close to
/// the suspect level it confirms the new level; if it is instead close to the
/// last trusted level it denies the suspect. A price agreeing with neither
/// source is unknown and therefore unavailable.
pub fn adjudicate(
    rest_price: Option<f64>,
    suspect_mid: f64,
    last_good_mid: f64,
    tolerance_pct: f64,
) -> VerifyResult {
    if !suspect_mid.is_finite()
        || suspect_mid <= 0.0
        || !last_good_mid.is_finite()
        || last_good_mid <= 0.0
        || !tolerance_pct.is_finite()
        || tolerance_pct <= 0.0
    {
        return VerifyResult::Unavailable;
    }

    let Some(rest_price) = rest_price.filter(|price| price.is_finite() && *price > 0.0) else {
        return VerifyResult::Unavailable;
    };

    let tolerance = tolerance_pct / 100.0;
    let near_suspect = (rest_price - suspect_mid).abs() <= tolerance * suspect_mid;
    let near_last_good = (rest_price - last_good_mid).abs() <= tolerance * last_good_mid;

    if near_suspect {
        VerifyResult::Confirmed
    } else if near_last_good {
        VerifyResult::Denied
    } else {
        VerifyResult::Unavailable
    }
}

/// Convert an internal pair name into Binance's compact symbol format.
///
/// The engine accepts both separated (`BNB-USDT`, `BNB_USDT`) and compact
/// symbols. Binance symbols are case-insensitive, but uppercasing here keeps
/// requests deterministic.
pub fn binance_symbol(pair: &str) -> String {
    pair.trim()
        .chars()
        .filter(|character| *character != '-' && *character != '_')
        .flat_map(char::to_uppercase)
        .collect()
}

/// Cross-source verifier backed by Binance's public REST ticker.
pub struct BinancePriceVerifier {
    client: reqwest::Client,
    base_url: &'static str,
    /// Optional secondary venue. Kept as a trait object so a Gate fallback can
    /// be supplied by a later integration without changing this API.
    fallback: Option<Box<dyn PriceVerifier>>,
}

impl BinancePriceVerifier {
    /// Construct a verifier with a bounded request timeout and no credentials.
    pub fn new() -> Self {
        let client = reqwest::Client::builder()
            .timeout(REQUEST_TIMEOUT)
            .build()
            .unwrap_or_else(|_| reqwest::Client::new());

        Self {
            client,
            base_url: BINANCE_BASE_URL,
            fallback: None,
        }
    }
    /// Attach a secondary verifier used when Binance cannot adjudicate.
    pub fn with_fallback(mut self, fallback: Box<dyn PriceVerifier>) -> Self {
        self.fallback = Some(fallback);
        self
    }


    async fn fetch_price(&self, pair: &str) -> Option<f64> {
        let symbol = binance_symbol(pair);
        if symbol.is_empty() {
            return None;
        }

        let response = self
            .client
            .get(format!("{}/api/v3/ticker/price", self.base_url))
            .query(&[("symbol", symbol)])
            .send()
            .await
            .ok()?
            .error_for_status()
            .ok()?;

        let payload: serde_json::Value = response.json().await.ok()?;
        let price = payload.get("price")?.as_str()?.parse::<f64>().ok()?;
        (price.is_finite() && price > 0.0).then_some(price)
    }
}

impl Default for BinancePriceVerifier {
    fn default() -> Self {
        Self::new()
    }
}

/// A source capable of adjudicating a suspect order-book mid-price.
#[async_trait]
pub trait PriceVerifier: Send + Sync {
    async fn verify(
        &self,
        symbol: &str,
        suspect_mid: f64,
        last_good_mid: f64,
        tolerance_pct: f64,
    ) -> VerifyResult;
}

#[async_trait]
impl PriceVerifier for BinancePriceVerifier {
    async fn verify(
        &self,
        symbol: &str,
        suspect_mid: f64,
        last_good_mid: f64,
        tolerance_pct: f64,
    ) -> VerifyResult {
        let primary = adjudicate(
            self.fetch_price(symbol).await,
            suspect_mid,
            last_good_mid,
            tolerance_pct,
        );
        if primary != VerifyResult::Unavailable {
            return primary;
        }

        match &self.fallback {
            Some(fallback) => {
                fallback
                    .verify(symbol, suspect_mid, last_good_mid, tolerance_pct)
                    .await
            }
            None => VerifyResult::Unavailable,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adjudicate_confirms_when_rest_near_suspect() {
        assert_eq!(
            adjudicate(Some(498.0), 497.0, 580.0, 1.0),
            VerifyResult::Confirmed
        );
    }

    #[test]
    fn adjudicate_denies_when_rest_near_last_good() {
        assert_eq!(
            adjudicate(Some(580.0), 497.0, 580.0, 1.0),
            VerifyResult::Denied
        );
    }

    #[test]
    fn adjudicate_unavailable_when_rest_agrees_with_neither() {
        assert_eq!(
            adjudicate(Some(700.0), 497.0, 580.0, 1.0),
            VerifyResult::Unavailable
        );
    }

    #[test]
    fn adjudicate_unavailable_when_rest_missing() {
        assert_eq!(
            adjudicate(None, 497.0, 580.0, 1.0),
            VerifyResult::Unavailable
        );
    }

    #[test]
    fn binance_symbol_strips_dash() {
        assert_eq!(binance_symbol("BNB-USDT"), "BNBUSDT");
        assert_eq!(binance_symbol("BTC-USDT"), "BTCUSDT");
    }

    struct FakeVerifier(pub Option<f64>);

    #[async_trait]
    impl PriceVerifier for FakeVerifier {
        async fn verify(
            &self,
            _symbol: &str,
            suspect_mid: f64,
            last_good_mid: f64,
            tolerance_pct: f64,
        ) -> VerifyResult {
            adjudicate(self.0, suspect_mid, last_good_mid, tolerance_pct)
        }
    }

    #[tokio::test]
    async fn fake_verifier_threads_through_adjudicate() {
        let verifier = FakeVerifier(Some(580.0));
        assert_eq!(
            verifier.verify("BNB-USDT", 497.0, 580.0, 1.0).await,
            VerifyResult::Denied
        );
    }

    #[tokio::test]
    #[ignore = "Requires network access"]
    async fn live_binance_verifier_returns_a_price() {
        let verifier = BinancePriceVerifier::new();
        let result = verifier
            .verify("BNB-USDT", 600.0, 600.0, 100.0)
            .await;
        assert_eq!(
            result,
            VerifyResult::Confirmed,
            "live Binance price should be finite and positive"
        );
    }
}
