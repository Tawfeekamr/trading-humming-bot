pub mod grid;
pub mod trend;
pub mod mean_reversion;
pub mod trend_journal;
pub mod grid_journal;
pub mod status_cache;
pub mod regime_cache;

use async_trait::async_trait;
use anyhow::Result;
use std::collections::HashMap;
use crate::connector::types::{OrderRequest, Fill};
use crate::models::bar::Bar;
use crate::connector::types::OrderBook;

/// Context provided to strategies on each tick
pub struct TickContext {
    pub order_book: OrderBook,
    pub recent_bars: Vec<Bar>,
    pub balances: HashMap<String, f64>,
    pub open_orders: Vec<OrderRequest>,
    pub regime: Option<MarketRegime>,
    /// Real ML regime confidence (0.0–1.0). Passed through from regime_cache
    /// instead of being fabricated per-regime. 0.0 when no regime data available.
    pub regime_confidence: f64,
    pub timestamp: i64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum MarketRegime {
    Ranging,
    Trending,
    Danger,
}

/// Status snapshot for Telegram reporting
#[derive(Debug, Clone, serde::Serialize)]
pub struct StrategyStatus {
    pub name: String,
    pub pair: String,
    pub state: String,
    pub pnl: f64,
    pub open_orders: usize,
    pub details: String,
}

/// Main strategy trait — all strategies implement this
#[async_trait]
pub trait Strategy: Send {
    fn name(&self) -> &str;
    fn trading_pair(&self) -> &str;

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>>;
    async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>>;
    async fn on_start(&mut self) -> Result<Vec<OrderRequest>>;
    async fn on_stop(&mut self) -> Result<()>;

    fn status(&self) -> StrategyStatus;

    /// Pause or resume the strategy
    fn set_paused(&mut self, _paused: bool) {}
    /// Current capital including compounded profits
    fn current_capital(&self) -> f64 { 0.0 }
    /// Initial capital before compounding
    fn initial_capital(&self) -> f64 { 0.0 }
}
