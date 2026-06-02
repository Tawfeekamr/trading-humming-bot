//! Shared strategy status cache — updated by engine, read by API.
//!
//! Uses `Arc<RwLock<...>>` so the engine can write status after each tick
//! and the HTTP API can read it on demand without blocking.

use std::collections::HashMap;
use std::sync::Arc;
use tokio::sync::RwLock;

use super::StrategyStatus;

/// Shared cache of all strategy statuses, safe to clone across tasks.
#[derive(Clone)]
pub struct StrategyStatusCache {
    inner: Arc<RwLock<Vec<StrategyStatus>>>,
}

impl StrategyStatusCache {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(RwLock::new(Vec::new())),
        }
    }

    /// Replace the entire status snapshot (called by engine after each tick).
    pub async fn update(&self, statuses: Vec<StrategyStatus>) {
        let mut guard = self.inner.write().await;
        *guard = statuses;
    }

    /// Read a snapshot of all strategy statuses (called by API handler).
    pub async fn snapshot(&self) -> Vec<StrategyStatus> {
        self.inner.read().await.clone()
    }

    /// Build a map of pair → status, useful for Python queries.
    pub async fn by_pair(&self) -> HashMap<String, StrategyStatus> {
        self.inner
            .read()
            .await
            .iter()
            .map(|s| (s.pair.clone(), s.clone()))
            .collect()
    }
}
