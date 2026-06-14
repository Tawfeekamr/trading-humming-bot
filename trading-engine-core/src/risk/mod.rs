pub mod position_guard;
pub mod circuit_breaker;

pub use position_guard::PositionGuard;
pub use circuit_breaker::CircuitBreaker;

use anyhow::Result;
use serde::{Serialize, Deserialize};
use tracing::warn;
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

    /// Feed current portfolio equity to the breaker (called every tick by the engine).
    pub fn record_equity(&mut self, current_equity: f64) {
        self.circuit_breaker.update_peak(current_equity);
        let _ = self.circuit_breaker.check(current_equity) || self.circuit_breaker.check_daily(current_equity);
    }
}

/// Persisted circuit-breaker state (loaded on startup, saved on changes).
#[derive(Serialize, Deserialize, Default)]
struct RiskState {
    peak_equity: f64,
    start_of_day_equity: f64,
    halted: bool,
    halted_at_unix: Option<i64>,
    last_reset_date: String,
    /// Equity metric this state was recorded under. Older state (realized-PnL
    /// based, pre-MTM) has no/empty metric and is discarded on load so an
    /// inflated legacy peak can't trigger a false drawdown halt.
    #[serde(default)]
    metric: String,
}

/// Persist breaker state atomically (temp write + rename).
pub fn save_state(cb: &CircuitBreaker, path: &str) {
    let state = RiskState {
        peak_equity: cb.peak_equity(),
        start_of_day_equity: cb.start_of_day_equity(),
        halted: cb.is_halted_raw(),
        halted_at_unix: cb.halted_at_unix(),
        last_reset_date: cb.last_reset_date().to_string(),
        metric: "mtm".to_string(),
    };
    let p = std::path::PathBuf::from(path);
    if let Some(parent) = p.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let tmp = p.with_extension("json.tmp");
    if let Ok(json) = serde_json::to_string_pretty(&state) {
        if std::fs::write(&tmp, json).is_ok() {
            let _ = std::fs::rename(&tmp, &p);
        }
    }
}

/// Load breaker state. On missing/corrupt file, or a state recorded under an
/// older equity metric (realized-PnL based), initialize fresh from
/// `current_equity` so a stale/inflated peak can't cause a false halt.
pub fn load_state(cb: &mut CircuitBreaker, path: &str, current_equity: f64) {
    if let Ok(content) = std::fs::read_to_string(path) {
        if let Ok(s) = serde_json::from_str::<RiskState>(&content) {
            if s.metric == "mtm" {
                cb.set_peak_equity(if s.peak_equity > 0.0 { s.peak_equity } else { current_equity });
                cb.set_start_of_day_equity(if s.start_of_day_equity > 0.0 { s.start_of_day_equity } else { current_equity });
                cb.set_halted_state(s.halted, s.halted_at_unix);
                cb.set_last_reset_date(s.last_reset_date);
                return;
            }
            // metric mismatch (legacy realized-based state) — discard, re-init below.
        } else {
            warn!("Corrupt risk_state.json — initializing fresh");
        }
    }
    cb.set_peak_equity(current_equity);
    cb.set_start_of_day_equity(current_equity);
    cb.set_halted_state(false, None);
    cb.set_last_reset_date(chrono::Utc::now().format("%Y-%m-%d").to_string());
}
