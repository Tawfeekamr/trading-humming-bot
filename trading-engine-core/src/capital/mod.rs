//! Centralized capital accounting + allocation for the Rust engine.
//!
//! Phase A: visibility (total equity, reserve, free capital, per-strategy
//! deployed capital). Phase B: allocation — strategies `request_capital` before
//! entry so the shared free-capital pool caps how much any one can deploy.
//!
//! Model:
//!   total_equity      = USDT + Σ(base × mid)          [portfolio_equity_mtm]
//!   locked_in_positions = total_equity − USDT          (inventory MTM value)
//!   reserve           = reserve_limit_pct × total_equity
//!   free_capital      = max(0, USDT − reserve)         (deployable USDT)
//!   deployed[name]    = Σ each strategy's open-position cost basis
//!                       (Strategy::deployed_capital) — real, per strategy
//!
//! `request_capital(name, desired)` grants min(desired, free − already-granted
//! this tick). A per-tick grant map prevents two strategies that tick in the same
//! cycle from both spending the same free capital; it is reset each tick_strategies.
//! Cross-tick truth comes from real open positions (deployed[]), so closing a
//! position replenishes free automatically — no explicit release call needed.

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
    /// Real capital deployed in open positions, per strategy (cost basis).
    pub deployed_capital: BTreeMap<String, f64>,
}

#[derive(Debug, Clone)]
struct CapitalState {
    total_equity: f64,
    usdt_balance: f64,
    /// Real deployed capital per strategy (from Strategy::deployed_capital).
    deployed: BTreeMap<String, f64>,
    /// Capital granted via request_capital in the current tick (prevents intra-tick
    /// over-allocation). Reset each tick by the engine.
    tick_grants: BTreeMap<String, f64>,
}

/// Shared capital accountant + allocator. Clone-cheap (Arc inside) so the engine,
/// strategies (via TickContext), and the API server share one instance.
#[derive(Clone)]
pub struct CapitalManager {
    reserve_limit_pct: f64,
    /// Per-strategy max cumulative deployed capital. A strategy whose deployed +
    /// this-tick grant reaches its budget gets no more — preventing one strategy
    /// (e.g. grid accumulating inventory) from monopolizing the shared pool and
    /// starving the others (which sized to 0). Empty = uncapped (back-compat).
    budgets: BTreeMap<String, f64>,
    /// Per-strategy size multiplier (default 1.0). The PPO router sets this each
    /// tick for the active engine (0.5 / 1.0 / 1.5) to scale the per-strategy
    /// budget ceiling, so a "smaller" routing decision shrinks the grant cap and
    /// a "larger" one grows it. Absent entry ⇒ 1.0 (back-compat).
    size_mults: BTreeMap<String, f64>,
    state: Arc<RwLock<CapitalState>>,
}

impl CapitalManager {
    pub fn new(reserve_limit_pct: f64) -> Self {
        Self {
            reserve_limit_pct,
            budgets: BTreeMap::new(),
            size_mults: BTreeMap::new(),
            state: Arc::new(RwLock::new(CapitalState {
                total_equity: 0.0,
                usdt_balance: 0.0,
                deployed: BTreeMap::new(),
                tick_grants: BTreeMap::new(),
            })),
        }
    }

    /// Latest mark-to-market equity + USDT balance (engine pushes each tick).
    pub fn sync_equity(&self, total_equity: f64, usdt_balance: f64) {
        if let Ok(mut s) = self.state.write() {
            s.total_equity = total_equity;
            s.usdt_balance = usdt_balance;
        }
    }

    /// Real per-strategy deployed capital (engine pushes each tick from
    /// Strategy::deployed_capital).
    pub fn set_deployed(&self, deployed: BTreeMap<String, f64>) {
        if let Ok(mut s) = self.state.write() {
            s.deployed = deployed;
        }
    }

    /// Clear the per-tick grant map (engine calls at the start of each tick cycle).
    pub fn reset_tick_grants(&self) {
        if let Ok(mut s) = self.state.write() {
            s.tick_grants.clear();
        }
    }

    /// Set per-strategy cumulative-deployment budgets (max deployed capital each
    /// strategy may hold). Builder-style; called once at construction from config.
    pub fn with_budgets(mut self, budgets: BTreeMap<String, f64>) -> Self {
        self.budgets = budgets;
        self
    }

    /// Set the per-strategy size multiplier for the active engine. The PPO router
    /// calls this each tick with `r.size_mult` (0.5 / 1.0 / 1.5); `request_capital`
    /// then scales the strategy's remaining budget ceiling by this factor. Negative
    /// values clamp to 0.0 (zero-grant "flat-ish" sizing). Default 1.0 when unset.
    pub fn set_size_mult(&mut self, name: &str, mult: f64) {
        self.size_mults.insert(name.to_string(), mult.max(0.0));
    }

    /// Grant up to `desired` USDT to `name` for entry sizing this tick, capped by
    /// free capital not already granted this tick AND by the strategy's remaining
    /// budget (cumulative deployed + this-tick grant). Returns the granted amount
    /// (0 if nothing free or budget exhausted). Mutates the grant map, so two
    /// strategies ticking in the same cycle can't both spend the same free capital,
    /// and no single strategy can exceed its budget.
    pub fn request_capital(&self, name: &str, desired: f64) -> f64 {
        if desired <= 0.0 {
            return 0.0;
        }
        if let Ok(mut s) = self.state.write() {
            let reserve = self.reserve_limit_pct / 100.0 * s.total_equity;
            let free = (s.usdt_balance - reserve).max(0.0);
            let already_granted: f64 = s.tick_grants.values().sum();
            let available = (free - already_granted).max(0.0);
            // Per-strategy budget ceiling: cumulative deployed + already granted
            // this tick can't exceed the configured budget. Scaled by the PPO
            // router's per-strategy `size_mult` (default 1.0) so a 0.5 routing
            // decision halves the cap and 1.5 grows it. Only strategies with a
            // budget entry are scaled (uncapped strategies have nothing to scale;
            // this also avoids INFINITY * 0.0 = NaN).
            let size_mult = self.size_mults.get(name).copied().unwrap_or(1.0);
            let budget_remaining = match self.budgets.get(name) {
                Some(b) => {
                    let deployed = s.deployed.get(name).copied().unwrap_or(0.0);
                    let granted_this_tick = s.tick_grants.get(name).copied().unwrap_or(0.0);
                    ((b - deployed - granted_this_tick).max(0.0)) * size_mult
                }
                None => f64::INFINITY, // no budget entry → uncapped (back-compat)
            };
            let grant = desired.min(available).min(budget_remaining);
            *s.tick_grants.entry(name.to_string()).or_insert(0.0) += grant;
            grant
        } else {
            0.0
        }
    }

    /// Public snapshot. `reserve = pct × equity`; `free = max(0, USDT − reserve)`;
    /// `locked = max(0, equity − USDT)`.
    pub fn snapshot(&self) -> CapitalSnapshot {
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
            deployed_capital: s.deployed.clone(),
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
    fn deployed_is_reported_per_strategy() {
        let m = mgr();
        m.sync_equity(10_000.0, 4_000.0);
        let mut d = BTreeMap::new();
        d.insert("grid".to_string(), 3_000.0);
        d.insert("swing".to_string(), 1_000.0);
        m.set_deployed(d);
        let snap = m.snapshot();
        assert_eq!(snap.deployed_capital.get("grid"), Some(&3_000.0));
        assert_eq!(snap.deployed_capital.get("swing"), Some(&1_000.0));
        // free is from USDT, NOT reduced by deployed (deployed is already non-USDT)
        assert!((snap.free_capital - 2_000.0).abs() < 1e-6);
    }

    #[test]
    fn shares_state_across_clones() {
        let m = mgr();
        let clone = m.clone();
        m.sync_equity(5_000.0, 1_000.0);
        assert!((clone.snapshot().total_equity - 5_000.0).abs() < 1e-6);
    }

    #[test]
    fn request_capital_grants_up_to_free() {
        let m = mgr();
        m.sync_equity(10_000.0, 4_000.0); // free = 2_000
        assert!((m.request_capital("swing", 5_000.0) - 2_000.0).abs() < 1e-6);
    }

    #[test]
    fn request_capital_prevents_intra_tick_double_spend() {
        let m = mgr();
        m.sync_equity(10_000.0, 4_000.0); // free = 2_000
        // First strategy takes the whole free pool this tick
        assert!((m.request_capital("grid", 2_000.0) - 2_000.0).abs() < 1e-6);
        // Second strategy, same tick, sees nothing left
        assert!((m.request_capital("trend", 2_000.0) - 0.0).abs() < 1e-6);
    }

    #[test]
    fn reset_tick_grants_replenishes_next_tick() {
        let m = mgr();
        m.sync_equity(10_000.0, 4_000.0); // free = 2_000
        assert!((m.request_capital("grid", 2_000.0) - 2_000.0).abs() < 1e-6);
        m.reset_tick_grants();
        // Next tick: free pool available again
        assert!((m.request_capital("trend", 2_000.0) - 2_000.0).abs() < 1e-6);
    }

    #[test]
    fn request_capital_grants_zero_when_nothing_free() {
        let m = mgr();
        m.sync_equity(10_000.0, 1_000.0); // reserve 2k > USDT 1k → free 0
        assert!((m.request_capital("swing", 500.0) - 0.0).abs() < 1e-6);
    }

    #[test]
    fn budget_caps_a_strategy_at_its_remaining_budget() {
        // grid already deployed 950 of a 1000 budget → only 50 more allowed, even
        // though 8000 is free. This is the fix for grid monopolizing the pool.
        let mut budgets = BTreeMap::new();
        budgets.insert("grid".to_string(), 1_000.0);
        budgets.insert("mean_reversion".to_string(), 500.0);
        let m = CapitalManager::new(20.0).with_budgets(budgets);
        m.sync_equity(10_000.0, 10_000.0); // free = 8000
        let mut deployed = BTreeMap::new();
        deployed.insert("grid".to_string(), 950.0);
        m.set_deployed(deployed);
        assert!((m.request_capital("grid", 200.0) - 50.0).abs() < 1e-6,
            "grid capped at remaining budget (1000 - 950 = 50), not the 200 desired");
        // mean_reversion has its own 500 budget, 0 deployed → gets its full 100 ask.
        assert!((m.request_capital("mean_reversion", 100.0) - 100.0).abs() < 1e-6,
            "MR is not starved by grid's budget — each strategy has its own slice");
    }

    #[test]
    fn strategy_with_no_budget_entry_is_uncapped() {
        // Back-compat: a strategy absent from the budgets map is uncapped (old behavior).
        let m = CapitalManager::new(20.0);
        m.sync_equity(10_000.0, 4_000.0); // free = 2000
        assert!((m.request_capital("swing", 2_000.0) - 2_000.0).abs() < 1e-6);
    }

    #[test]
    fn test_request_capital_scales_by_size_mult() {
        // grid budget=1000; size_mult=0.5 ⇒ budget ceiling halves to 500, so a
        // 1000-desired ask (with 8000 free) is capped at 500 by the scaled budget.
        let mut budgets = BTreeMap::new();
        budgets.insert("grid".to_string(), 1_000.0);
        let mut m = CapitalManager::new(20.0).with_budgets(budgets);
        m.sync_equity(10_000.0, 10_000.0); // reserve 2000 ⇒ free 8000 (budget is binding)
        m.set_size_mult("grid", 0.5);
        let granted = m.request_capital("grid", 1_000.0);
        assert!((granted - 500.0).abs() < 1e-6,
            "size_mult=0.5 must halve the budget ceiling: expected 500, got {}", granted);
    }

    #[test]
    fn size_mult_default_is_no_op_when_unset() {
        // No set_size_mult call ⇒ multiplier defaults to 1.0 ⇒ grant unchanged.
        let mut budgets = BTreeMap::new();
        budgets.insert("grid".to_string(), 1_000.0);
        let m = CapitalManager::new(20.0).with_budgets(budgets);
        m.sync_equity(10_000.0, 10_000.0); // free = 8000
        // 200 desired, 1000 budget, no mult ⇒ full 200 granted.
        assert!((m.request_capital("grid", 200.0) - 200.0).abs() < 1e-6);
    }

    #[test]
    fn size_mult_clamps_negative_to_zero() {
        // A negative mult is clamped to 0.0 ⇒ zero grant (intentional "flat-ish" sizing).
        let mut budgets = BTreeMap::new();
        budgets.insert("grid".to_string(), 1_000.0);
        let mut m = CapitalManager::new(20.0).with_budgets(budgets);
        m.sync_equity(10_000.0, 10_000.0);
        m.set_size_mult("grid", -0.5); // clamped to 0.0 internally
        assert!((m.request_capital("grid", 500.0) - 0.0).abs() < 1e-6,
            "negative size_mult must clamp to 0 ⇒ zero grant");
    }

    /// I2: clearing a non-active engine's size_mult to 0 must zero its capital
    /// grant, so a paused engine whose on_tick still runs (managing exits) can't
    /// draw capital. Defense-in-depth alongside set_paused.
    #[test]
    fn size_mult_zero_clears_capital_grant_for_non_active_engine() {
        let mut budgets = BTreeMap::new();
        budgets.insert("trend".to_string(), 1_000.0);
        budgets.insert("grid".to_string(), 1_000.0);
        let mut m = CapitalManager::new(20.0).with_budgets(budgets);
        m.sync_equity(10_000.0, 10_000.0); // free = 8000, both budgets = 1000

        // Active engine keeps its mult; non-active is cleared to 0 (I2).
        m.set_size_mult("trend", 1.5);
        m.set_size_mult("grid", 0.0);

        // trend (active): 1.5 × 1000 budget = 1500 cap; ask 1000 → granted 1000.
        assert!((m.request_capital("trend", 1_000.0) - 1_000.0).abs() < 1e-6,
            "active engine draws capital normally");

        // grid (non-active): 0 × 1000 budget = 0 cap; ask anything → granted 0.
        assert!((m.request_capital("grid", 500.0) - 0.0).abs() < 1e-6,
            "non-active engine with cleared size_mult draws ZERO capital");
    }
}
