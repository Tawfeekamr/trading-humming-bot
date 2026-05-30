pub mod position_guard;
pub mod circuit_breaker;

pub use position_guard::PositionGuard;
pub use circuit_breaker::CircuitBreaker;

use anyhow::Result;
use crate::connector::types::Fill;

pub struct RiskManager {
    pub position_guard: PositionGuard,
    pub circuit_breaker: CircuitBreaker,
}

impl RiskManager {
    pub fn new(pg: PositionGuard, cb: CircuitBreaker) -> Self {
        Self { position_guard: pg, circuit_breaker: cb }
    }

    pub fn check_trading_allowed(&self) -> Result<()> {
        if self.circuit_breaker.is_halted() {
            anyhow::bail!("Trading halted by circuit breaker");
        }
        Ok(())
    }

    pub fn on_fill(&mut self, fill: &Fill) {
        // Estimate PnL from fill: fee is negative impact, price * qty gives notional
        // For a sell fill, PnL is approximated; accurate tracking needs entry price context
        let pnl = -(fill.fee); // Conservative: only account for fees as negative PnL
        let equity_estimate = 0.0; // Engine should call record_pnl with actual equity
        let _ = (pnl, equity_estimate); // Suppress unused warnings until engine wires this
    }

    /// Record realized PnL and check circuit breaker
    pub fn record_pnl(&mut self, pnl: f64, current_equity: f64) -> bool {
        self.circuit_breaker.record_pnl(pnl, current_equity)
    }
}
