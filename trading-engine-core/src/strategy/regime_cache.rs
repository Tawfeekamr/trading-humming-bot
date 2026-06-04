//! Shared regime cache — Python pushes ML regime predictions, Rust reads them.
//!
//! Two paths:
//! 1. **HTTP push** (primary): Python calls `POST /api/v1/regime` → writes to in-memory map
//! 2. **File fallback** (on startup): reads `data/regime_cache.json` if no HTTP push yet
//!
//! File reads use mtime caching — stat() every call, parse only when file changed.

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;
use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegimeEntry {
    pub regime: i32,       // 0=Ranging, 1=Trending, 2=Danger
    pub confidence: f64,
    pub timestamp: i64,    // Unix millis
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegimeUpdate {
    pub pair: String,
    pub regime: i32,
    pub confidence: f64,
}

#[derive(Clone)]
pub struct RegimeCache {
    inner: Arc<RwLock<HashMap<String, RegimeEntry>>>,
    file_path: String,
    last_mtime: Arc<RwLock<u64>>,
}

impl RegimeCache {
    pub fn new(file_path: &str) -> Self {
        Self {
            inner: Arc::new(RwLock::new(HashMap::new())),
            file_path: file_path.to_string(),
            last_mtime: Arc::new(RwLock::new(0)),
        }
    }

    /// Update regime from HTTP push (called by API handler).
    pub async fn update(&self, updates: &[RegimeUpdate]) {
        let mut map = self.inner.write().await;
        let now = chrono::Utc::now().timestamp_millis();
        for u in updates {
            map.insert(u.pair.clone(), RegimeEntry {
                regime: u.regime,
                confidence: u.confidence,
                timestamp: now,
            });
        }
    }

    /// Get regime for a pair. Returns (regime, confidence).
    /// Checks file mtime first — if file changed since last read, reloads.
    pub async fn get(&self, pair: &str) -> Option<(i32, f64)> {
        self.maybe_reload_from_file().await;
        let map = self.inner.read().await;
        map.get(pair).map(|e| (e.regime, e.confidence))
    }

    /// Check if file has been modified since last read. If so, reload.
    async fn maybe_reload_from_file(&self) {
        // Quick stat to check mtime — no lock contention on the map
        let current_mtime = std::fs::metadata(&self.file_path)
            .map(|m| m.modified()
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
                .duration_since(std::time::SystemTime::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs())
            .unwrap_or(0);

        let last = *self.last_mtime.read().await;
        if current_mtime > 0 && current_mtime != last {
            // File changed — reload
            if let Ok(content) = std::fs::read_to_string(&self.file_path) {
                if let Ok(entries) = serde_json::from_str::<HashMap<String, RegimeEntry>>(&content) {
                    let mut map = self.inner.write().await;
                    for (k, v) in entries {
                        map.insert(k, v);
                    }
                    *self.last_mtime.write().await = current_mtime;
                }
            }
        }
    }

    /// Load from file on startup (no mtime check — always reads).
    pub async fn load_from_file(&self) {
        let content = match std::fs::read_to_string(&self.file_path) {
            Ok(c) => c,
            Err(_) => return, // File doesn't exist yet — that's fine
        };
        let entries: HashMap<String, RegimeEntry> = match serde_json::from_str(&content) {
            Ok(e) => e,
            Err(_) => return,
        };
        let mtime = std::fs::metadata(&self.file_path)
            .map(|m| m.modified()
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
                .duration_since(std::time::SystemTime::UNIX_EPOCH)
                .unwrap_or_default()
                .as_secs())
            .unwrap_or(0);

        let mut map = self.inner.write().await;
        for (k, v) in entries {
            map.insert(k, v);
        }
        *self.last_mtime.write().await = mtime;
    }

    /// Persist current state to file (called after HTTP push as backup).
    pub async fn persist(&self) {
        let map = self.inner.read().await;
        if map.is_empty() { return; }
        if let Ok(json) = serde_json::to_string_pretty(&*map) {
            let _ = std::fs::write(&self.file_path, json);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_regime_cache_update_and_get() {
        let cache = RegimeCache::new("/tmp/test_regime_cache.json");
        cache.update(&[
            RegimeUpdate { pair: "BTC-USDT".into(), regime: 0, confidence: 0.85 },
            RegimeUpdate { pair: "ETH-USDT".into(), regime: 1, confidence: 0.7 },
        ]).await;

        let btc = cache.get("BTC-USDT").await;
        assert_eq!(btc, Some((0, 0.85)));

        let eth = cache.get("ETH-USDT").await;
        assert_eq!(eth, Some((1, 0.7)));

        let unknown = cache.get("DOGE-USDT").await;
        assert_eq!(unknown, None);
    }

    #[tokio::test]
    async fn test_regime_cache_file_roundtrip() {
        let path = "/tmp/test_regime_roundtrip.json";
        let _ = std::fs::remove_file(path);

        // Write via cache
        let cache = RegimeCache::new(path);
        cache.update(&[
            RegimeUpdate { pair: "BTC-USDT".into(), regime: 2, confidence: 0.9 },
        ]).await;
        cache.persist().await;

        // Load into fresh cache
        let cache2 = RegimeCache::new(path);
        cache2.load_from_file().await;
        let btc = cache2.get("BTC-USDT").await;
        assert_eq!(btc, Some((2, 0.9)));

        let _ = std::fs::remove_file(path);
    }

    #[tokio::test]
    async fn test_regime_cache_no_file_is_ok() {
        let cache = RegimeCache::new("/tmp/nonexistent_regime.json");
        cache.load_from_file().await; // should not panic
        let result = cache.get("BTC-USDT").await;
        assert_eq!(result, None);
    }
}
