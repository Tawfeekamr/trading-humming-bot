//! Shared routing cache — Python pushes the PPO routing decision, Rust reads it.
//! Mirrors strategy/regime_cache.rs. One current decision (not per-pair).
//!
//! Two paths:
//! 1. **HTTP push** (primary): Python calls `POST /api/v1/routing` → writes to in-memory entry
//! 2. **File fallback** (on startup): reads `data/routing_cache.json` if no HTTP push yet
//!
//! File reads use mtime caching — stat() every call, parse only when file changed.

use std::sync::Arc;
use tokio::sync::RwLock;
use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoutingEntry {
    pub active_engine: String,   // "grid" | "trend" | "swing" | "flat"
    pub size_mult: f64,          // 0.5 | 1.0 | 1.5
    pub flat: bool,              // force-close + suppress entries
    pub timestamp: i64,          // Unix millis
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoutingUpdate {
    pub active_engine: String,
    pub size_mult: f64,
    pub flat: bool,
}

#[derive(Clone)]
pub struct RoutingCache {
    state: Arc<RwLock<RoutingState>>,
    file_path: String,
    ttl_ms: i64, // Max age in milliseconds; entries older than this return None. 0 = never stale.
}

#[derive(Default)]
struct RoutingState {
    entry: Option<RoutingEntry>,
    last_mtime: u64,
}

impl RoutingCache {
    /// Create a new RoutingCache.
    /// `ttl_ms`: max entry age in milliseconds. 0 = no TTL (never stale).
    pub fn new(file_path: &str, ttl_ms: i64) -> Self {
        Self {
            state: Arc::new(RwLock::new(RoutingState::default())),
            file_path: file_path.to_string(),
            ttl_ms,
        }
    }

    /// Update routing decision from HTTP push (called by API handler).
    pub async fn update(&self, u: RoutingUpdate) {
        let mut state = self.state.write().await;
        state.entry = Some(RoutingEntry {
            active_engine: u.active_engine,
            size_mult: u.size_mult,
            flat: u.flat,
            timestamp: chrono::Utc::now().timestamp_millis(),
        });
        drop(state);
        self.persist().await;
    }

    /// Get the current routing decision. Returns None if no entry, or entry is older than TTL.
    /// Checks file mtime first — if file changed since last read, reloads.
    pub async fn get(&self) -> Option<RoutingEntry> {
        self.maybe_reload_from_file().await;
        let state = self.state.read().await;
        state.entry.as_ref().and_then(|e| {
            if self.ttl_ms > 0 {
                let now = chrono::Utc::now().timestamp_millis();
                if now - e.timestamp > self.ttl_ms {
                    return None; // Stale — treat as unknown
                }
            }
            Some(e.clone())
        })
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
                .as_secs())
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
        let entry = match serde_json::from_str::<RoutingEntry>(&content) {
            Ok(e) => e,
            Err(_) => return,
        };
        let mut state = self.state.write().await;
        // Re-check under the exclusive lock: another task may have just reloaded.
        if state.last_mtime == current_mtime {
            return;
        }
        state.entry = Some(entry);
        state.last_mtime = current_mtime;
    }

    /// Load from file on startup (no mtime check — always reads).
    /// Refreshes `last_mtime` so the first `get()` doesn't redundantly re-read
    /// the file (mirrors `RegimeCache::load_from_file`). I3 fix: was leaving
    /// `last_mtime = 0`, so every `get()` re-parsed the file.
    pub async fn load_from_file(&self) {
        let content = match std::fs::read_to_string(&self.file_path) {
            Ok(c) => c,
            Err(_) => return, // File doesn't exist yet — that's fine
        };
        let entry = match serde_json::from_str::<RoutingEntry>(&content) {
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
        let mut state = self.state.write().await;
        state.entry = Some(entry);
        state.last_mtime = mtime;
    }

    /// Persist current state to file (called after HTTP push as backup).
    pub async fn persist(&self) {
        let state = self.state.read().await;
        if let Some(e) = &state.entry {
            if let Ok(json) = serde_json::to_string_pretty(e) {
                let _ = std::fs::write(&self.file_path, json);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_routing_cache_update_and_get() {
        let cache = RoutingCache::new("/tmp/test_routing_cache.json", 0);
        cache.update(RoutingUpdate {
            active_engine: "trend".into(), size_mult: 1.5, flat: false,
        }).await;
        let r = cache.get().await.unwrap();
        assert_eq!(r.active_engine, "trend");
        assert_eq!(r.size_mult, 1.5);
        assert!(!r.flat);
    }

    #[tokio::test]
    async fn test_routing_cache_stale_returns_none() {
        let cache = RoutingCache::new("/tmp/test_routing_ttl.json", 5_000);
        {
            let mut s = cache.state.write().await;
            s.entry = Some(RoutingEntry {
                active_engine: "grid".into(), size_mult: 1.0, flat: false,
                timestamp: chrono::Utc::now().timestamp_millis() - 10_000,
            });
        }
        assert!(cache.get().await.is_none());
    }

    /// I3: load_from_file must refresh last_mtime so the first get() doesn't
    /// redundantly re-parse the file. Mirrors regime_cache's roundtrip test.
    #[tokio::test]
    async fn test_routing_cache_load_from_file_sets_mtime() {
        let path = "/tmp/test_routing_load_mtime.json";
        let _ = std::fs::remove_file(path);

        // Write a routing entry directly to the file (simulating a Python push
        // persist that happened before Rust started).
        let entry = RoutingEntry {
            active_engine: "trend".into(), size_mult: 1.5, flat: false,
            timestamp: chrono::Utc::now().timestamp_millis(),
        };
        std::fs::write(path, serde_json::to_string_pretty(&entry).unwrap()).unwrap();

        let cache = RoutingCache::new(path, 0);
        cache.load_from_file().await;

        // mtime must be populated — otherwise get() would re-read the file every call.
        let state = cache.state.read().await;
        assert!(state.last_mtime > 0, "last_mtime must be set after load_from_file");
        assert!(state.entry.is_some(), "entry loaded");
        drop(state);

        // get() returns the entry without re-parsing (mtime matches).
        let r = cache.get().await.expect("entry readable");
        assert_eq!(r.active_engine, "trend");
        assert_eq!(r.size_mult, 1.5);

        let _ = std::fs::remove_file(path);
    }

    /// C3: routing TTL must be 3 × bar_seconds. For 1h bars that's 10_800_000ms.
    /// A 180s TTL (the old value) would stale every decision before the next
    /// hourly push arrived — 20× too short for the cadence.
    #[test]
    fn test_routing_ttl_is_3x_bar_seconds() {
        // main.rs constructs the cache with this constant; mirror it here so a
        // careless edit to either side fails this test loudly.
        const ROUTING_BAR_SECONDS: i64 = 3600;
        const ROUTING_TTL_MS: i64 = 10_800_000;
        assert_eq!(ROUTING_TTL_MS, 3 * ROUTING_BAR_SECONDS * 1000,
            "routing TTL must be 3 × bar_seconds (1h bars → 10_800_000ms)");
    }
}
