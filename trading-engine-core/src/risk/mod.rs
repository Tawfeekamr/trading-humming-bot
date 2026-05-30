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

    pub fn on_fill(&mut self, _fill: &Fill) {
        // Update equity tracking — will be wired in engine integration
    }
}
