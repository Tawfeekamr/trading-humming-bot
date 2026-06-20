//! Centralized capital accounting for the Rust engine (Phase A: visibility).
//!
//! Tracks total portfolio equity, a configurable global reserve, free (deployable)
//! capital, and a per-strategy capital snapshot. This is READ-ONLY accounting — it
//! does not move money or change strategy behavior (that is Phase B). The engine
//! pushes balances + strategy capital here each tick; the API and the `/capital`
//! Telegram command read the snapshot.
//!
//! Model (derived from balances, no strategy edits required):
//!   total_equity      = USDT + Σ(base × mid)          [portfolio_equity_mtm]
//!   locked_in_positions = total_equity − USDT          (inventory MTM value)
//!   reserve           = reserve_limit_pct × total_equity
//!   free_capital      = max(0, USDT − reserve)         (deployable USDT)
//!
//! `strategy_capital` (each strategy's `current_capital()`) is reported separately
//! as budgets and is deliberately NOT summed into `free_capital`: budgets are config
//! amounts, not deployed capital, so summing them would understate free capital.

use std::collections::BTreeMap;
use std::sync::{Arc, RwLock};

use serde::Serialize;

/// Immutable view of the engine's capital at a point in time (returned by the API).
#[derive(Debug, Clone, Serialize)]
pub struct CapitalSnapshot {
    pub total_equity: f64,
    pub usdt_balance: f64,
    pub locked_in_positions: f64,
    pub reserve_limit_pct: f64,
    pub reserve: f64,
    pub free_capital: f64,
    /// Per-strategy working capital (`Strategy::current_capital`), labelled as
    /// budgets. Mean-reversion reports 0.0 (it has no budget concept).
    pub strategy_capital: BTreeMap<String, f64>,
}

#[derive(Debug, Clone)]
struct CapitalState {
    total_equity: f64,
    usdt_balance: f64,
    strategy_capital: BTreeMap<String, f64>,
}

/// Shared capital accountant. Clone-cheap (Arc inside) so the engine and the API
/// server share one instance — engine writes, API reads.
#[derive(Clone)]
pub struct CapitalManager {
    reserve_limit_pct: f64,
    state: Arc<RwLock<CapitalState>>,
}

impl CapitalManager {
    pub fn new(reserve_limit_pct: f64) -> Self {
        Self {
            reserve_limit_pct,
            state: Arc::new(RwLock::new(CapitalState {
                total_equity: 0.0,
                usdt_balance: 0.0,
                strategy_capital: BTreeMap::new(),
            })),
        }
    }

    /// Record the latest mark-to-market equity + USDT balance (engine, each tick).
    pub fn sync_equity(&self, total_equity: f64, usdt_balance: f64) {
        if let Ok(mut s) = self.state.write() {
            s.total_equity = total_equity;
            s.usdt_balance = usdt_balance;
        }
    }

    /// Record each strategy's working capital (engine, each tick).
    pub fn set_strategy_capital(&self, strategy_capital: BTreeMap<String, f64>) {
        if let Ok(mut s) = self.state.write() {
            s.strategy_capital = strategy_capital;
        }
    }

    /// Public snapshot. `reserve = pct × equity`; `free = max(0, USDT − reserve)`;
    /// `locked = max(0, equity − USDT)`.
    pub fn snapshot(&self) -> CapitalSnapshot {
        // Recover from a poisoned lock rather than panicking the API/engine.
        let s = self.state.read().unwrap_or_else(|e| e.into_inner());
        let reserve = self.reserve_limit_pct / 100.0 * s.total_equity;
        let locked = (s.total_equity - s.usdt_balance).max(0.0);
        let free = (s.usdt_balance - reserve).max(0.0);
        CapitalSnapshot {
            total_equity: s.total_equity,
            usdt_balance: s.usdt_balance,
            locked_in_positions: locked,
            reserve_limit_pct: self.reserve_limit_pct,
            reserve,
            free_capital: free,
            strategy_capital: s.strategy_capital.clone(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mgr() -> CapitalManager {
        CapitalManager::new(20.0)
    }

    #[test]
    fn free_capital_is_usdt_minus_reserve() {
        let m = mgr();
        m.sync_equity(10_000.0, 4_000.0); // 4k USDT, 6k inventory
        let snap = m.snapshot();
        assert!((snap.reserve - 2_000.0).abs() < 1e-6); // 20% of 10k
        assert!((snap.free_capital - 2_000.0).abs() < 1e-6); // 4k USDT − 2k reserve
        assert!((snap.locked_in_positions - 6_000.0).abs() < 1e-6); // 10k − 4k
    }

    #[test]
    fn free_clamps_to_zero_when_reserve_exceeds_usdt() {
        let m = mgr();
        m.sync_equity(10_000.0, 1_000.0); // reserve (2k) > USDT (1k)
        let snap = m.snapshot();
        assert!((snap.free_capital - 0.0).abs() < 1e-6);
        assert!((snap.reserve - 2_000.0).abs() < 1e-6);
    }

    #[test]
    fn strategy_budgets_are_not_summed_into_free() {
        let m = mgr();
        m.sync_equity(10_000.0, 4_000.0);
        let mut sc = BTreeMap::new();
        sc.insert("grid".to_string(), 10_000.0); // budgets far exceed equity
        sc.insert("trend".to_string(), 10_000.0);
        m.set_strategy_capital(sc);
        let snap = m.snapshot();
        assert!((snap.free_capital - 2_000.0).abs() < 1e-6); // unchanged by budgets
        assert_eq!(snap.strategy_capital.get("grid"), Some(&10_000.0));
    }

    #[test]
    fn shares_state_across_clones() {
        let m = mgr();
        let clone = m.clone();
        m.sync_equity(5_000.0, 1_000.0);
        assert!((clone.snapshot().total_equity - 5_000.0).abs() < 1e-6);
    }
}
