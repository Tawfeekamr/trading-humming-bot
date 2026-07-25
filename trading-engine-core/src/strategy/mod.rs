pub mod grid;
pub mod trend;
pub mod trade_journal;
pub mod status_cache;
pub mod regime_cache;
pub mod routing_cache;

use async_trait::async_trait;
use anyhow::Result;
use std::collections::HashMap;
use crate::connector::types::{OrderRequest, Fill};
use crate::models::bar::Bar;
use crate::connector::types::OrderBook;
use crate::capital::CapitalManager;

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
    /// Central capital allocator (Phase B). None when capital management isn't
    /// wired (strategies then size from their own config, uncapped).
    pub capital: Option<CapitalManager>,
    /// True while the engine replays historical bars to warm indicators on
    /// startup. Strategies must NOT open NEW positions/entries when set (they'd
    /// regenerate ghost trades on every restart); managing existing positions
    /// and warming indicators is fine.
    pub replay: bool,
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
    /// Close all open positions in this strategy and suppress new entries
    /// until the next non-flat routing decision. Default no-op so strategies
    /// that don't hold inventory compile unchanged.
    fn force_flat(&mut self) {}
    /// Client-order-ids (as the strategy set them, pre-owner-tag) of resting
    /// orders this strategy wants cancelled on the next engine cycle. Default
    /// empty — strategies that don't place resting orders never cancel.
    fn pending_cancels(&mut self) -> Vec<String> { Vec::new() }
    /// Current capital including compounded profits
    fn current_capital(&self) -> f64 { 0.0 }
    /// Initial capital before compounding
    fn initial_capital(&self) -> f64 { 0.0 }
    /// Cumulative realized PnL (closed trades only). Used by the engine to feed
    /// the portfolio circuit breaker on a stable (non-MTM) basis.
    fn realized_pnl(&self) -> f64 { 0.0 }
    /// Real capital currently deployed in this strategy's open positions (cost
    /// basis). Used by the CapitalManager for per-strategy visibility. Default 0.
    fn deployed_capital(&self) -> f64 { 0.0 }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Minimal Strategy impl for unit-testing default trait behavior. Similar
    /// in shape to the `EchoLimit` mock in `tests/backtest_replay.rs` but
    /// trimmed to just the required methods.
    #[derive(Default)]
    struct NullStrategy;

    #[async_trait]
    impl Strategy for NullStrategy {
        fn name(&self) -> &str { "null" }
        fn trading_pair(&self) -> &str { "TESTUSDT" }

        async fn on_tick(&mut self, _ctx: &TickContext) -> Result<Vec<OrderRequest>> {
            Ok(vec![])
        }
        async fn on_fill(&mut self, _fill: &Fill) -> Result<Vec<OrderRequest>> {
            Ok(vec![])
        }
        async fn on_start(&mut self) -> Result<Vec<OrderRequest>> {
            Ok(vec![])
        }
        async fn on_stop(&mut self) -> Result<()> {
            Ok(())
        }

        fn status(&self) -> StrategyStatus {
            StrategyStatus {
                name: "null".into(),
                pair: "TESTUSDT".into(),
                state: "idle".into(),
                pnl: 0.0,
                open_orders: 0,
                details: String::new(),
            }
        }
    }

    #[test]
    fn test_force_flat_default_is_no_op() {
        let mut s = NullStrategy::default();
        // Default impl must compile without an override and must not panic.
        s.force_flat();
        assert!(true);
    }
}
