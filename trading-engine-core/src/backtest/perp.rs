//! Perpetual price/funding source backed by historical bars (no live network).
use std::collections::BTreeMap;
use std::sync::Mutex;
use async_trait::async_trait;
use crate::connector::perp_price::PerpPriceSource;
use crate::models::bar::Bar;

pub struct HistoricalPerpSource {
    /// ms-timestamp → perp close, for as-of lookup (no lookahead).
    marks: BTreeMap<i64, f64>,
    funding: Option<f64>,
    clock: Mutex<i64>,
}

impl HistoricalPerpSource {
    pub fn from_bars(perp_bars: Vec<Bar>, funding_rate: Option<f64>) -> Self {
        let marks = perp_bars.iter().map(|b| (b.timestamp, b.close)).collect();
        Self { marks, funding: funding_rate, clock: Mutex::new(0) }
    }
    /// Advance the harness clock (ms). Called by the replay driver each bar.
    pub fn set_clock(&self, ts: i64) { *self.clock.lock().unwrap() = ts; }
}

#[async_trait]
impl PerpPriceSource for HistoricalPerpSource {
    async fn mark(&self, _symbol: &str) -> Option<f64> {
        let now = *self.clock.lock().unwrap();
        // greatest key <= now (as-of, no lookahead)
        self.marks.range(..=now).next_back().map(|(_, p)| *p)
    }
    async fn funding_rate(&self, _symbol: &str) -> Option<f64> { self.funding }
}
