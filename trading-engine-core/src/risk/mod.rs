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

    /// The legitimate maximum for the breaker peak: initial capital + realized
    /// + unrealised, with 1% headroom.
    pub fn equity_invariant_bound(initial_capital: f64, realized_pnl: f64, unrealised_pnl: f64) -> f64 {
        (initial_capital + realized_pnl + unrealised_pnl) * 1.01
    }

    /// Peak-equity invariant (Task 6): the breaker's peak can never
    /// legitimately exceed (initial capital + cumulative realized PnL +
    /// current unrealised PnL) by more than 1%. On violation this NEVER trips
    /// the breaker — a phantom peak must not halt trading — it freezes peak
    /// updates pending review and returns `(suspect_peak, legitimate_bound)`
    /// for the caller to alert on. Returns `None` when the peak is inside the
    /// bound.
    pub fn check_equity_invariant(
        &mut self,
        initial_capital: f64,
        realized_pnl: f64,
        unrealised_pnl: f64,
    ) -> Option<(f64, f64)> {
        let bound = Self::equity_invariant_bound(initial_capital, realized_pnl, unrealised_pnl);
        let peak = self.circuit_breaker.peak_equity();
        if peak > bound {
            self.circuit_breaker.freeze_peak();
            Some((peak, bound))
        } else {
            None
        }
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

/// Whether the balance book at boot continues from the previous run's state
/// (live mode, balances persisted at the exchange) or was wiped and reseeded
/// to the configured capital (paper/testnet mode, where balances live only in
/// memory and every restart reseeds them).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum BookContinuity {
    Persisted,
    Reseeded,
}

/// The baseline discarded when a reseeded book's persisted breaker state is
/// rebased at boot. Returned (not logged here) so the caller can surface it
/// loudly with mode-specific context.
#[derive(Clone, Copy, Debug)]
pub struct DiscardedBaseline {
    pub peak_equity: f64,
    pub start_of_day_equity: f64,
    pub was_halted: bool,
}

/// Load breaker state.
///
/// Paper/testnet (`BookContinuity::Reseeded`): the in-memory book was reseeded
/// at boot, so a persisted baseline describes a book that no longer exists.
/// Restoring it latched the breaker against an unreachable phantom peak
/// (Oct 2026 incident: peak 124,933.84 vs a flat reseeded 100k book, re-tripping
/// at every UTC midnight). Instead, rebase peak/sod to boot equity, clear any
/// halt, and return the discarded baseline for the caller to log.
///
/// Live (`BookContinuity::Persisted`): balances persist, so a persisted
/// baseline may be real and is never rebased silently. A latched halt refuses
/// to start (`Err`) and demands an explicit operator reset; healthy state
/// loads normally.
///
/// On missing/corrupt file, or a state recorded under an older equity metric
/// (realized-PnL based), initialize fresh from `current_equity` in both modes.
pub fn load_state(
    cb: &mut CircuitBreaker,
    path: &str,
    current_equity: f64,
    continuity: BookContinuity,
) -> Result<Option<DiscardedBaseline>, String> {
    let today = chrono::Utc::now().format("%Y-%m-%d").to_string();
    if let Ok(content) = std::fs::read_to_string(path) {
        if let Ok(s) = serde_json::from_str::<RiskState>(&content) {
            if s.metric == "mtm" {
                let diverged = (s.peak_equity - current_equity).abs() > 1e-9
                    || (s.start_of_day_equity - current_equity).abs() > 1e-9
                    || s.halted;
                match continuity {
                    BookContinuity::Reseeded => {
                        if !diverged {
                            cb.set_peak_equity(s.peak_equity);
                            cb.set_start_of_day_equity(s.start_of_day_equity);
                            cb.set_halted_state(false, None);
                            cb.set_last_reset_date(s.last_reset_date);
                            return Ok(None);
                        }
                        let discarded = DiscardedBaseline {
                            peak_equity: s.peak_equity,
                            start_of_day_equity: s.start_of_day_equity,
                            was_halted: s.halted,
                        };
                        // The book this baseline measured was wiped; start over
                        // from what the reseeded book is actually worth.
                        cb.set_peak_equity(current_equity);
                        cb.set_start_of_day_equity(current_equity);
                        cb.set_halted_state(false, None);
                        cb.set_last_reset_date(today);
                        return Ok(Some(discarded));
                    }
                    BookContinuity::Persisted => {
                        if s.halted {
                            return Err(format!(
                                "latched circuit-breaker halt (peak={:.2}, tripped at {}) vs boot equity {:.2} — \
                                 balances persist in live mode so this drawdown may be real; refusing to start. \
                                 Explicit operator reset required: back up and remove {}, then restart.",
                                s.peak_equity,
                                s.halted_at_unix
                                    .map(|t| t.to_string())
                                    .unwrap_or_else(|| "unknown time".into()),
                                current_equity,
                                path
                            ));
                        }
                        cb.set_peak_equity(if s.peak_equity > 0.0 { s.peak_equity } else { current_equity });
                        cb.set_start_of_day_equity(if s.start_of_day_equity > 0.0 { s.start_of_day_equity } else { current_equity });
                        cb.set_halted_state(false, None);
                        cb.set_last_reset_date(s.last_reset_date);
                        return Ok(None);
                    }
                }
            }
            // metric mismatch (legacy realized-based state) — discard, re-init below.
        } else {
            warn!("Corrupt risk_state.json — initializing fresh");
        }
    }
    cb.set_peak_equity(current_equity);
    cb.set_start_of_day_equity(current_equity);
    cb.set_halted_state(false, None);
    cb.set_last_reset_date(today);
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::risk::PositionGuard;

    fn fresh_path(tag: &str) -> String {
        let p = std::env::temp_dir().join(format!("risk_state_test_{tag}_{}.json",
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()));
        let _ = std::fs::remove_file(&p);
        p.to_string_lossy().to_string()
    }

    // Root cause A (Oct 2026): risk_state.json persisted peak=124,933.84 from
    // a paper book that was wiped and reseeded to 100k at the next boot; the
    // stale peak latched the breaker at every restart and re-tripped at every
    // UTC midnight. A reseeded book's persisted baseline describes a book that
    // no longer exists and must be rebased.
    #[test]
    fn paper_reseed_rebases_stale_peak_and_clears_halt() {
        let path = fresh_path("paper_rebase");
        let mut stale = CircuitBreaker::new(10.0, 5.0);
        stale.set_peak_equity(124_933.84);
        stale.set_start_of_day_equity(100_000.0);
        stale.check(100_000.0); // 19.9% "drawdown" from the phantom peak → trip
        assert!(stale.is_halted());
        save_state(&stale, &path);

        let mut c = CircuitBreaker::new(10.0, 5.0);
        let d = load_state(&mut c, &path, 100_000.0, BookContinuity::Reseeded)
            .expect("paper boot must never refuse to start")
            .expect("a diverging baseline must be reported as discarded");
        assert!((d.peak_equity - 124_933.84).abs() < 1e-6, "report the discarded peak");
        assert!(d.was_halted);
        assert!((c.peak_equity() - 100_000.0).abs() < 1e-9, "peak must rebase to boot equity");
        assert!((c.start_of_day_equity() - 100_000.0).abs() < 1e-9);
        assert!(!c.is_halted(), "a baseline from a wiped book must not halt this instance");
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn paper_reseed_with_matching_baseline_loads_quietly() {
        let path = fresh_path("paper_clean");
        let mut c0 = CircuitBreaker::new(10.0, 5.0);
        c0.set_peak_equity(100_000.0);
        c0.set_start_of_day_equity(100_000.0);
        save_state(&c0, &path);

        let mut c = CircuitBreaker::new(10.0, 5.0);
        let discarded = load_state(&mut c, &path, 100_000.0, BookContinuity::Reseeded).unwrap();
        assert!(discarded.is_none(), "no divergence → nothing to discard");
        assert!(!c.is_halted());
        let _ = std::fs::remove_file(&path);
    }

    // Live-mode counterpart: balances persist there, so a persisted baseline may
    // be real. Never rebase silently — surface it and refuse to start.
    #[test]
    fn live_mode_refuses_to_start_on_latched_halt() {
        let path = fresh_path("live_refuse");
        let mut latched = CircuitBreaker::new(10.0, 5.0);
        latched.set_peak_equity(124_933.84);
        latched.set_start_of_day_equity(100_000.0);
        latched.check(100_000.0);
        assert!(latched.is_halted());
        save_state(&latched, &path);

        let mut c = CircuitBreaker::new(10.0, 5.0);
        let err = load_state(&mut c, &path, 100_000.0, BookContinuity::Persisted)
            .expect_err("live boot with a latched halt must refuse to start");
        assert!(
            err.to_lowercase().contains("operator"),
            "error must demand an explicit operator reset, got: {err}"
        );
        let _ = std::fs::remove_file(&path);
    }

    #[test]
    fn live_mode_healthy_state_loads_normally() {
        let path = fresh_path("live_ok");
        let mut c0 = CircuitBreaker::new(10.0, 5.0);
        c0.set_peak_equity(100_000.0);
        c0.set_start_of_day_equity(99_000.0);
        save_state(&c0, &path);

        let mut c = CircuitBreaker::new(10.0, 5.0);
        load_state(&mut c, &path, 99_500.0, BookContinuity::Persisted).unwrap();
        assert!(!c.is_halted());
        assert!((c.peak_equity() - 100_000.0).abs() < 1e-9);
        assert!((c.start_of_day_equity() - 99_000.0).abs() < 1e-9);
        let _ = std::fs::remove_file(&path);
    }

    /// Task 6 spec test: inject an inflated peak → the invariant fires, the
    /// alert names both the suspect peak and the legitimate bound, the breaker
    /// is NOT tripped, and peak updates freeze until reviewed.
    #[test]
    fn inflated_peak_raises_invariant_alert_without_tripping() {
        let mut rm = RiskManager::new(
            PositionGuard::new(80.0, 100.0, 10_000.0),
            CircuitBreaker::new(10.0, 5.0),
        );
        rm.circuit_breaker.set_peak_equity(124_933.84);
        rm.circuit_breaker.set_start_of_day_equity(100_000.0);

        // bound = (100,000 initial + 4,800 realized + 0 unrealised) × 1.01 = 105,848
        let (peak, bound) = rm
            .check_equity_invariant(100_000.0, 4_800.0, 0.0)
            .expect("a phantom peak 19% above the legitimate max must violate the invariant");
        assert!((peak - 124_933.84).abs() < 1e-6, "alert names the suspect peak");
        assert!((bound - 105_848.0).abs() < 1e-6, "alert names the legitimate bound, got {}", bound);

        assert!(!rm.circuit_breaker.is_halted(), "the invariant must never trip the breaker");
        assert!(rm.circuit_breaker.is_peak_frozen(), "peak updates frozen pending review");

        // Even a further phantom equity spike must not ratchet the peak.
        rm.record_equity(999_999.0);
        assert!(
            (rm.circuit_breaker.peak_equity() - 124_933.84).abs() < 1e-6,
            "frozen peak ignores further phantom spikes"
        );
    }

    #[test]
    fn healthy_peak_passes_the_invariant_quietly() {
        let mut rm = RiskManager::new(
            PositionGuard::new(80.0, 100.0, 10_000.0),
            CircuitBreaker::new(10.0, 5.0),
        );
        rm.circuit_breaker.set_peak_equity(104_000.0);
        rm.circuit_breaker.set_start_of_day_equity(100_000.0);

        // bound = (100,000 + 4,800 + 1,000) × 1.01 = 106,878 — 104,000 is inside.
        let alert = rm.check_equity_invariant(100_000.0, 4_800.0, 1_000.0);
        assert!(alert.is_none(), "a legitimate peak must not raise the invariant");
        assert!(!rm.circuit_breaker.is_peak_frozen());
    }

    #[test]
    fn load_missing_file_initializes_unhalted() {
        let path = fresh_path("missing");
        let mut c = CircuitBreaker::new(10.0, 5.0);
        load_state(&mut c, &path, 100_000.0, BookContinuity::Persisted).unwrap();
        assert!(!c.is_halted());
        assert!((c.peak_equity() - 100_000.0).abs() < 1e-9);
    }
}
