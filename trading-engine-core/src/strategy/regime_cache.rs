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
    /// Optional model provenance; omitted by older cache files.
    #[serde(default)]
    pub model_version: Option<String>,
    #[serde(default)]
    pub artifact_sha256: Option<String>,
    #[serde(default)]
    pub feature_contract_hash: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RegimeUpdate {
    pub pair: String,
    pub regime: i32,
    pub confidence: f64,
    /// A caller-supplied positive timestamp is retained; invalid/missing values
    /// use the receipt time so old producers remain compatible.
    #[serde(default)]
    pub timestamp: Option<i64>,
    #[serde(default)]
    pub model_version: Option<String>,
    #[serde(default)]
    pub artifact_sha256: Option<String>,
    #[serde(default)]
    pub feature_contract_hash: Option<String>,
}

#[derive(Clone)]
pub struct RegimeCache {
    /// In-memory regime map + last-loaded file mtime, guarded by a single lock
    /// so the mtime check, file reload, and map update are atomic.
    state: Arc<RwLock<RegimeState>>,
    file_path: String,
    ttl_ms: i64, // Max age in milliseconds; entries older than this return None. 0 = never stale.
}

#[derive(Default)]
struct RegimeState {
    map: HashMap<String, RegimeEntry>,
    last_mtime: u64,
}

impl RegimeCache {
    /// Create a new RegimeCache.
    /// `ttl_ms`: max entry age in milliseconds. 0 = no TTL (never stale).
    /// Recommended: 3× poll interval (e.g., 180_000 for 60s polling).
    pub fn new(file_path: &str, ttl_ms: i64) -> Self {
        Self {
            state: Arc::new(RwLock::new(RegimeState::default())),
            file_path: file_path.to_string(),
            ttl_ms,
        }
    }

    /// Update regime from HTTP push (called by API handler).
    pub async fn update(&self, updates: &[RegimeUpdate]) {
        let mut state = self.state.write().await;
        let now = chrono::Utc::now().timestamp_millis();
        for u in updates {
            state.map.insert(u.pair.clone(), RegimeEntry {
                regime: u.regime,
                confidence: u.confidence,
                timestamp: u.timestamp.filter(|timestamp| *timestamp > 0).unwrap_or(now),
                model_version: u.model_version.clone(),
                artifact_sha256: u.artifact_sha256.clone(),
                feature_contract_hash: u.feature_contract_hash.clone(),
            });
        }
    }

    /// Return a TTL-validated entry, including optional model provenance.
    pub async fn get_entry(&self, pair: &str) -> Option<RegimeEntry> {
        self.maybe_reload_from_file().await;
        let state = self.state.read().await;
        state.map.get(pair).and_then(|entry| {
            if self.ttl_ms > 0 {
                let now = chrono::Utc::now().timestamp_millis();
                if now - entry.timestamp > self.ttl_ms {
                    return None;
                }
            }
            Some(entry.clone())
        })
    }

    /// Get regime for a pair. Returns (regime, confidence).
    /// Returns None if: pair not found, or entry is older than TTL.
    /// Checks file mtime first — if file changed since last read, reloads.
    pub async fn get(&self, pair: &str) -> Option<(i32, f64)> {
        self.get_entry(pair).await.map(|entry| (entry.regime, entry.confidence))
    }


    /// Check if file has been modified since last read. If so, reload.
    /// Acquires a single write lock across stat → read → parse → insert → mtime
    /// update so concurrent callers can't race or clobber a fresh HTTP push.
    async fn maybe_reload_from_file(&self) {
        let current_mtime = std::fs::metadata(&self.file_path)
            .map(|m| m.modified()
                .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
                .duration_since(std::time::SystemTime::UNIX_EPOCH)
                .unwrap_or_default()
                .as_nanos() as u64)
            .unwrap_or(0);

        // Fast path under a read lock: skip if nothing changed.
        {
            let state = self.state.read().await;
            if current_mtime == 0 || state.last_mtime == current_mtime {
                return;
            }
        }

        // File changed — read + parse outside the lock, then apply atomically.
        let content = match std::fs::read_to_string(&self.file_path) {
            Ok(c) => c,
            Err(_) => return,
        };
        let entries = match serde_json::from_str::<HashMap<String, RegimeEntry>>(&content) {
            Ok(e) => e,
            Err(_) => return,
        };
        let mut state = self.state.write().await;
        // Re-check under the exclusive lock: another task may have just reloaded.
        if state.last_mtime == current_mtime {
            return;
        }
        for (k, v) in entries {
            state.map.insert(k, v);
        }
        state.last_mtime = current_mtime;
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
                .as_nanos() as u64)
            .unwrap_or(0);

        let mut state = self.state.write().await;
        for (k, v) in entries {
            state.map.insert(k, v);
        }
        state.last_mtime = mtime;
    }

    /// Persist current state to file (called after HTTP push as backup).
    pub async fn persist(&self) {
        let map = { self.state.read().await.map.clone() };
        if map.is_empty() { return; }
        if let Ok(json) = serde_json::to_string_pretty(&map) {
            if std::fs::write(&self.file_path, json).is_ok() {
                let mtime = std::fs::metadata(&self.file_path)
                    .map(|m| m.modified()
                        .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
                        .duration_since(std::time::SystemTime::UNIX_EPOCH)
                        .unwrap_or_default()
                        .as_nanos() as u64)
                    .unwrap_or(0);
                self.state.write().await.last_mtime = mtime;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_regime_cache_update_and_get() {
        let cache = RegimeCache::new("/tmp/test_regime_cache.json", 0);
        cache.update(&[
            RegimeUpdate { pair: "BTC-USDT".into(), regime: 0, confidence: 0.85, timestamp: None, model_version: None, artifact_sha256: None, feature_contract_hash: None },
            RegimeUpdate { pair: "ETH-USDT".into(), regime: 1, confidence: 0.7, timestamp: None, model_version: None, artifact_sha256: None, feature_contract_hash: None },
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
        let cache = RegimeCache::new(path, 0);
        cache.update(&[
            RegimeUpdate { pair: "BTC-USDT".into(), regime: 2, confidence: 0.9, timestamp: None, model_version: None, artifact_sha256: None, feature_contract_hash: None },
        ]).await;
        cache.persist().await;

        // Load into fresh cache
        let cache2 = RegimeCache::new(path, 0);
        cache2.load_from_file().await;
        let btc = cache2.get("BTC-USDT").await;
        assert_eq!(btc, Some((2, 0.9)));

        let _ = std::fs::remove_file(path);
    }

    #[tokio::test]
    async fn test_regime_persist_refreshes_mtime() {
        let path = "/tmp/test_regime_persist_mtime.json";
        let _ = std::fs::remove_file(path);
        let cache = RegimeCache::new(path, 0);
        cache.update(&[
            RegimeUpdate { pair: "BTC-USDT".into(), regime: 1, confidence: 0.8, timestamp: None, model_version: None, artifact_sha256: None, feature_contract_hash: None },
        ]).await;
        cache.persist().await;

        let state = cache.state.read().await;
        assert!(state.last_mtime > 0, "persist must refresh last_mtime after write");
        let _ = std::fs::remove_file(path);
    }

    #[tokio::test]
    async fn test_regime_cache_no_file_is_ok() {
        let cache = RegimeCache::new("/tmp/nonexistent_regime.json", 0);
        cache.load_from_file().await; // should not panic
        let result = cache.get("BTC-USDT").await;
        assert_eq!(result, None);
    }

    #[tokio::test]
    async fn test_regime_cache_ttl_expiry() {
        let cache = RegimeCache::new("/tmp/test_regime_ttl.json", 5000); // 5s TTL

        // Insert entry with timestamp 10 seconds ago
        let mut state = cache.state.write().await;
        state.map.insert("BTC-USDT".to_string(), RegimeEntry {
            regime: 0,
            confidence: 0.9,
            timestamp: chrono::Utc::now().timestamp_millis() - 10_000, // 10s ago
            model_version: None,
            artifact_sha256: None,
            feature_contract_hash: None,
        });
        drop(state);

        // Entry should be expired → None
        let result = cache.get("BTC-USDT").await;
        assert_eq!(result, None);
    }

    #[tokio::test]
    async fn test_regime_cache_ttl_fresh_entry() {
        let cache = RegimeCache::new("/tmp/test_regime_ttl_fresh.json", 180_000); // 3min TTL

        cache.update(&[
            RegimeUpdate { pair: "BTC-USDT".into(), regime: 1, confidence: 0.8, timestamp: None, model_version: None, artifact_sha256: None, feature_contract_hash: None },
        ]).await;


        // Just inserted — should be fresh
        let result = cache.get("BTC-USDT").await;
        assert_eq!(result, Some((1, 0.8)));
    }
    #[tokio::test]
    async fn regime_metadata_round_trips_and_old_payload_defaults() {
        let cache = RegimeCache::new("target/test-regime-cache.json", 60_000);
        cache.update(&[RegimeUpdate {
            pair: "BNB-USDT".into(),
            regime: 0,
            confidence: 0.81,
            timestamp: Some(chrono::Utc::now().timestamp_millis()),
            model_version: Some("rf-bnb-20260801".into()),
            artifact_sha256: Some("abc".into()),
            feature_contract_hash: Some("features-v1".into()),
        }]).await;
        let entry = cache.get_entry("BNB-USDT").await.unwrap();
        assert_eq!(entry.model_version.as_deref(), Some("rf-bnb-20260801"));
        assert_eq!(entry.artifact_sha256.as_deref(), Some("abc"));
        let old: RegimeUpdate =
            serde_json::from_str(r#"{"pair":"BNB-USDT","regime":0,"confidence":0.5}"#).unwrap();
        assert!(old.model_version.is_none());
        assert!(old.timestamp.is_none());
    }
}
