//! Shared bar buffer cache accessible by both the Engine and HTTP API.
//!
//! Uses `Arc<RwLock<...>>` so the engine can write bars from the WebSocket
//! event loop while the API handler reads them concurrently — no contention
//! issue because writes are tiny and rare (~1/min per pair).

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

use crate::models::bar::Bar;

/// Maximum bars kept per symbol (rolling window).
pub const MAX_BARS_PER_PAIR: usize = 500;

/// Thread-safe, cloneable bar cache shared between Engine and AppState.
#[derive(Clone)]
pub struct BarCache {
    inner: Arc<RwLock<HashMap<String, Vec<Bar>>>>,
}

impl BarCache {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    /// Replace the vector for a given key (used by preload).
    pub async fn set(&self, key: String, bars: Vec<Bar>) {
        let mut map = self.inner.write().await;
        map.insert(key, bars);
    }

    /// Append a closed bar and trim to `MAX_BARS_PER_PAIR`.
    /// Cheap — no `await` inside the guard beyond the initial lock.
    pub async fn push_closed_bar(&self, key: String, bar: Bar) {
        let mut map = self.inner.write().await;
        let v = map.entry(key).or_default();
        v.push(bar);
        if v.len() > MAX_BARS_PER_PAIR {
            v.drain(0..v.len() - MAX_BARS_PER_PAIR);
        }
    }

    /// Lookup with dash/no-dash fallback. Returns up to `limit` most recent.
    pub async fn get(&self, symbol: &str, limit: usize) -> Vec<Bar> {
        let map = self.inner.read().await;
        let v = map
            .get(symbol)
            .or_else(|| map.get(&symbol.replace('-', "")))
            .or_else(|| {
                let with_dash = insert_dash(symbol);
                map.get(&with_dash)
            });
        match v {
            None => Vec::new(),
            Some(bars) => {
                let start = bars.len().saturating_sub(limit);
                bars[start..].to_vec()
            }
        }
    }

    /// Snapshot all entries — used by `save_bar_buffers` persistence.
    pub async fn snapshot(&self) -> HashMap<String, Vec<Bar>> {
        self.inner.read().await.clone()
    }

    /// Bulk load — used by `load_bar_buffers` at startup.
    pub async fn bulk_load(&self, entries: HashMap<String, Vec<Bar>>) {
        let mut map = self.inner.write().await;
        for (k, v) in entries {
            map.insert(k, v);
        }
    }

    /// Check emptiness (used by replay gating).
    pub async fn is_empty(&self) -> bool {
        self.inner.read().await.is_empty()
    }

    /// Direct lookup by *exact* key (strategy tick / replay which already
    /// know the canonical config-key form). Avoids the fallback cost.
    pub async fn get_exact(&self, key: &str) -> Option<Vec<Bar>> {
        self.inner.read().await.get(key).cloned()
    }
}

/// Try to insert a dash before common quote currencies.
/// "DOGEUSDT" → "DOGE-USDT"
fn insert_dash(symbol: &str) -> String {
    for quote in &["USDT", "BUSD", "BTC", "ETH"] {
        if let Some(pos) = symbol.find(quote) {
            if pos > 0 && !symbol[..pos].ends_with('-') {
                return format!("{}-{}", &symbol[..pos], &symbol[pos..]);
            }
        }
    }
    symbol.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn make_bar(ts: i64) -> Bar {
        Bar {
            open: 1.0,
            high: 1.0,
            low: 1.0,
            close: 1.0,
            volume: 1.0,
            timestamp: ts,
        }
    }

    #[tokio::test]
    async fn test_set_and_get_exact() {
        let cache = BarCache::new();
        cache
            .set("DOGE-USDT".into(), vec![make_bar(1), make_bar(2)])
            .await;
        let bars = cache.get_exact("DOGE-USDT").await.unwrap();
        assert_eq!(bars.len(), 2);
        assert_eq!(bars[0].timestamp, 1);
    }

    #[tokio::test]
    async fn test_get_with_no_dash_fallback() {
        let cache = BarCache::new();
        // Stored with dash, queried without
        cache
            .set("DOGE-USDT".into(), vec![make_bar(42)])
            .await;
        let bars = cache.get("DOGEUSDT", 10).await;
        assert_eq!(bars.len(), 1);
        assert_eq!(bars[0].timestamp, 42);
    }

    #[tokio::test]
    async fn test_get_with_dash_fallback() {
        let cache = BarCache::new();
        // Stored without dash, queried with dash
        cache
            .set("DOGEUSDT".into(), vec![make_bar(99)])
            .await;
        let bars = cache.get("DOGE-USDT", 10).await;
        assert_eq!(bars.len(), 1);
        assert_eq!(bars[0].timestamp, 99);
    }

    #[tokio::test]
    async fn test_push_trims_to_max() {
        let cache = BarCache::new();
        for i in 0..(MAX_BARS_PER_PAIR + 10) {
            cache.push_closed_bar("TEST".into(), make_bar(i as i64)).await;
        }
        let bars = cache.get_exact("TEST").await.unwrap();
        assert_eq!(bars.len(), MAX_BARS_PER_PAIR);
        // Oldest bar should be trimmed
        assert_eq!(bars[0].timestamp, 10);
    }

    #[tokio::test]
    async fn test_limit_returns_tail() {
        let cache = BarCache::new();
        for i in 0..10 {
            cache.push_closed_bar("TEST".into(), make_bar(i as i64)).await;
        }
        let bars = cache.get("TEST", 3).await;
        assert_eq!(bars.len(), 3);
        assert_eq!(bars[0].timestamp, 7);
        assert_eq!(bars[2].timestamp, 9);
    }

    #[tokio::test]
    async fn test_snapshot_round_trip() {
        let cache = BarCache::new();
        cache
            .set("A".into(), vec![make_bar(1)])
            .await;
        let snap = cache.snapshot().await;
        assert_eq!(snap.len(), 1);
        // Bulk load into a fresh cache
        let cache2 = BarCache::new();
        cache2.bulk_load(snap).await;
        assert_eq!(cache2.get_exact("A").await.unwrap().len(), 1);
    }

    #[test]
    fn test_insert_dash() {
        assert_eq!(insert_dash("DOGEUSDT"), "DOGE-USDT");
        assert_eq!(insert_dash("BTCUSDT"), "BTC-USDT");
        assert_eq!(insert_dash("ETHBTC"), "ETH-BTC");
        assert_eq!(insert_dash("DOGE-USDT"), "DOGE-USDT"); // already has dash
    }
}
