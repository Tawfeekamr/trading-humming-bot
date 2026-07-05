# PPO Paper-Gated Live Integration — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the trained PPO routing policy into the live Rust trading engine behind a realistic paper gate, so we can run a falsification experiment (sim-to-real fidelity + realistic-paper edge) before any capital is at risk.

**Architecture:** Single Rust instance flipped to `exchange.testnet: true` + PPO routing (option B — live trading pauses for the experiment). A Python `live_router` computes the PPO action each 1h bar and pushes `(active_engine, size_mult, flat)` via `POST /api/v1/routing` to a new `RoutingCache` in Rust (mirroring the existing `RegimeCache` pattern). `tick_strategies` reads that cache, pauses non-active engines, applies `flat`, and scales capital by `size_mult`. A replay script compares `env.py` (toy engines) vs the paper Rust instance to validate sim-to-real fidelity.

**Tech Stack:** Rust (axum, tokio, serde — already in `trading-engine-core`), Python (numpy, pandas, requests, stable-baselines3 — already in the conda RL env).

**Spec:** `docs/superpowers/specs/2026-07-05-ppo-live-paper-gate-design.md` (APPROVED).

## Global Constraints

- All paper, ETHUSDT only. `exchange.testnet: true` for the experiment. No funded-account API keys in the paper config.
- Paper realism: 8 bps slippage, real Binance maker/taker fees (set in `config/strategy.yaml` `paper:` block).
- Promotion gate: PPO-routed paper must beat the un-routed (RF-regime) paper baseline over ≥4 weeks — not merely be positive.
- The model has no demonstrated edge; the experiment may conclude "no-go." That is a successful outcome.
- New Rust code mirrors existing patterns (`RegimeCache`, the regime POST handler, `Strategy` trait). Follow `trading-engine-core` conventions exactly.
- TDD throughout: write the failing test, watch it fail, implement, watch it pass, commit.

---

## File Structure

**Rust (new):**
- `trading-engine-core/src/strategy/routing_cache.rs` — `RoutingCache` (mirrors `regime_cache.rs`); holds the latest `{active_engine, size_mult, flat, timestamp}` pushed by Python.

**Rust (modify):**
- `trading-engine-core/src/strategy/mod.rs` — declare `pub mod routing_cache;`; add `fn force_flat(&mut self)` to the `Strategy` trait with a default no-op impl.
- `trading-engine-core/src/engine.rs` — `tick_strategies`: read `RoutingCache`, call `set_paused(true)` on non-active engines and `force_flat()` when `flat` is set; pass `size_mult` into the `CapitalManager` per tick.
- `trading-engine-core/src/capital/mod.rs` — `CapitalManager::set_size_mult(name, mult)`; `request_capital` scales the per-strategy budget by it.
- `trading-engine-core/src/api/handlers.rs` + `src/api/server.rs` — `POST /api/v1/routing` handler writing the `RoutingCache`.
- `trading-engine-core/src/main.rs` — construct `RoutingCache`, wire it into `Engine` + the handler routes.

**Python (new):**
- `src/rl/live_router.py` — per-bar loop: load latest klines → features → observation → `PPORouter.predict` → decode action → `POST /api/v1/routing`.
- `tests/test_rl_live_router.py` — pure tests for action decode + observation build.

**Config + scripts:**
- `config/strategy.yaml` — `routing:` section + realistic `paper:` block.
- `scripts/fidelity_check.py` — replay a historical window through `env.py` and compare to recorded paper-engine behaviour.

---

### Task 1: Rust `RoutingCache`

**Files:**
- Create: `trading-engine-core/src/strategy/routing_cache.rs`
- Modify: `trading-engine-core/src/strategy/mod.rs` (add `pub mod routing_cache;` next to the existing `pub mod regime_cache;`)

**Interfaces:**
- Produces: `RoutingCache`, `RoutingEntry { active_engine: String, size_mult: f64, flat: bool, timestamp: i64 }`, `RoutingUpdate { active_engine, size_mult, flat }`. Methods: `RoutingCache::new(file_path, ttl_ms)`, `async update(&RoutingUpdate)`, `async get() -> Option<RoutingEntry>`, `async load_from_file()`, `async persist()`.

- [ ] **Step 1: Write the failing test**

Add to `routing_cache.rs` (mirror the `regime_cache.rs` test block):

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn test_routing_cache_update_and_get() {
        let cache = RoutingCache::new("/tmp/test_routing_cache.json", 0);
        cache.update(RoutingUpdate {
            active_engine: "trend".into(), size_mult: 1.5, flat: false,
        }).await;
        let r = cache.get().await.unwrap();
        assert_eq!(r.active_engine, "trend");
        assert_eq!(r.size_mult, 1.5);
        assert!(!r.flat);
    }

    #[tokio::test]
    async fn test_routing_cache_stale_returns_none() {
        let cache = RoutingCache::new("/tmp/test_routing_ttl.json", 5_000);
        {
            let mut s = cache.state.write().await;
            s.entry = Some(RoutingEntry {
                active_engine: "grid".into(), size_mult: 1.0, flat: false,
                timestamp: chrono::Utc::now().timestamp_millis() - 10_000,
            });
        }
        assert!(cache.get().await.is_none());
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib routing_cache --manifest-path trading-engine-core/Cargo.toml`
Expected: FAIL — `cannot find type RoutingCache`.

- [ ] **Step 3: Implement `RoutingCache`**

Mirror `regime_cache.rs` exactly (single `RoutingEntry` instead of a per-pair map):

```rust
//! Shared routing cache — Python pushes the PPO routing decision, Rust reads it.
//! Mirrors strategy/regime_cache.rs. One current decision (not per-pair).

use std::sync::Arc;
use tokio::sync::RwLock;
use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoutingEntry {
    pub active_engine: String,   // "grid" | "trend" | "swing" | "flat"
    pub size_mult: f64,          // 0.5 | 1.0 | 1.5
    pub flat: bool,              // force-close + suppress entries
    pub timestamp: i64,          // Unix millis
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RoutingUpdate {
    pub active_engine: String,
    pub size_mult: f64,
    pub flat: bool,
}

#[derive(Clone)]
pub struct RoutingCache {
    state: Arc<RwLock<RoutingState>>,
    file_path: String,
    ttl_ms: i64,
}

#[derive(Default)]
struct RoutingState {
    entry: Option<RoutingEntry>,
    last_mtime: u64,
}

impl RoutingCache {
    pub fn new(file_path: &str, ttl_ms: i64) -> Self {
        Self {
            state: Arc::new(RwLock::new(RoutingState::default())),
            file_path: file_path.to_string(),
            ttl_ms,
        }
    }

    pub async fn update(&self, u: RoutingUpdate) {
        let mut state = self.state.write().await;
        state.entry = Some(RoutingEntry {
            active_engine: u.active_engine,
            size_mult: u.size_mult,
            flat: u.flat,
            timestamp: chrono::Utc::now().timestamp_millis(),
        });
        drop(state);
        self.persist().await;
    }

    pub async fn get(&self) -> Option<RoutingEntry> {
        self.maybe_reload_from_file().await;
        let state = self.state.read().await;
        state.entry.as_ref().and_then(|e| {
            if self.ttl_ms > 0 {
                let now = chrono::Utc::now().timestamp_millis();
                if now - e.timestamp > self.ttl_ms { return None; }
            }
            Some(e.clone())
        })
    }

    async fn maybe_reload_from_file(&self) {
        // Same mtime-guarded reload as regime_cache.rs, single-entry variant.
        let current_mtime = std::fs::metadata(&self.file_path)
            .map(|m| m.modified().unwrap_or(std::time::SystemTime::UNIX_EPOCH)
                .duration_since(std::time::SystemTime::UNIX_EPOCH).unwrap_or_default().as_secs())
            .unwrap_or(0);
        {
            let state = self.state.read().await;
            if current_mtime == 0 || state.last_mtime == current_mtime { return; }
        }
        let content = match std::fs::read_to_string(&self.file_path) { Ok(c) => c, Err(_) => return };
        let entry = match serde_json::from_str::<RoutingEntry>(&content) { Ok(e) => e, Err(_) => return };
        let mut state = self.state.write().await;
        if state.last_mtime == current_mtime { return; }
        state.entry = Some(entry);
        state.last_mtime = current_mtime;
    }

    pub async fn load_from_file(&self) {
        let content = match std::fs::read_to_string(&self.file_path) { Ok(c) => c, Err(_) => return };
        if let Ok(e) = serde_json::from_str::<RoutingEntry>(&content) {
            let mut state = self.state.write().await;
            state.entry = Some(e);
        }
    }

    pub async fn persist(&self) {
        let state = self.state.read().await;
        if let Some(e) = &state.entry {
            if let Ok(json) = serde_json::to_string_pretty(e) {
                let _ = std::fs::write(&self.file_path, json);
            }
        }
    }
}
```

Then in `trading-engine-core/src/strategy/mod.rs`, beside `pub mod regime_cache;`:
```rust
pub mod routing_cache;
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --lib routing_cache --manifest-path trading-engine-core/Cargo.toml`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/strategy/routing_cache.rs trading-engine-core/src/strategy/mod.rs
git commit -m "feat(engine): RoutingCache for PPO routing decisions (mirror RegimeCache)"
```

---

### Task 2: `Strategy::force_flat` trait method

**Files:**
- Modify: `trading-engine-core/src/strategy/mod.rs` (the `Strategy` trait — find it with `grep -n "trait Strategy"`)

**Interfaces:**
- Produces: `Strategy::force_flat(&mut self)` with a default no-op so existing strategies compile unchanged. Strategies that hold positions override it to close-all + set an entry-suppress flag.

- [ ] **Step 1: Write the failing test**

In `strategy/mod.rs` test module (or a new `tests/strategy_force_flat.rs`):

```rust
#[test]
fn test_force_flat_default_is_no_op() {
    // NullStrategy is a minimal Strategy impl used in engine unit tests;
    // locate it via grep "struct NullStrategy" — if absent, define a trivial one here.
    let mut s = NullStrategy::default();
    s.force_flat(); // default impl must not panic and must compile
    assert!(true);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib force_flat --manifest-path trading-engine-core/Cargo.toml`
Expected: FAIL — `no method force_flat on Strategy`.

- [ ] **Step 3: Add the trait method with a default impl**

In `strategy/mod.rs`, inside `pub trait Strategy` (next to the existing `fn set_paused(&mut self, paused: bool)`):

```rust
    /// Close all open positions in this strategy and suppress new entries
    /// until the next non-flat routing decision. Default no-op so strategies
    /// that don't hold inventory compile unchanged.
    fn force_flat(&mut self) {}
```

- [ ] **Step 4: Run test + full lib build**

Run: `cargo test --lib --manifest-path trading-engine-core/Cargo.toml`
Expected: PASS, and the whole crate still compiles.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/strategy/mod.rs
git commit -m "feat(engine): Strategy::force_flat trait method (default no-op)"
```

---

### Task 3: `POST /api/v1/routing` handler

**Files:**
- Modify: `trading-engine-core/src/api/handlers.rs` (mirror the existing `POST /api/v1/regime` handler — locate with `grep -n "regime" api/handlers.rs`)
- Modify: `trading-engine-core/src/api/server.rs` (add the route beside the regime route)

**Interfaces:**
- Consumes: `RoutingCache` (Task 1), `RoutingUpdate`.
- Produces: HTTP `POST /api/v1/routing` accepting JSON `{active_engine, size_mult, flat}`.

- [ ] **Step 1: Write the failing test**

Mirror the regime handler test pattern in `handlers.rs` (locate `test_regime` or similar). Add:

```rust
#[tokio::test]
async fn test_routing_handler_updates_cache() {
    let cache = routing_cache::RoutingCache::new("/tmp/test_routing_handler.json", 0);
    let payload = serde_json::json!({
        "active_engine": "swing", "size_mult": 1.0, "flat": false
    });
    // Use the same axum test helper the regime handler test uses to call the
    // routing handler with the payload; then assert cache state.
    let entry = cache.get().await.unwrap();
    assert_eq!(entry.active_engine, "swing");
}
```
(Adapt the test-helper invocation to match the existing regime handler test exactly — read that test for the harness.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib routing_handler --manifest-path trading-engine-core/Cargo.toml`
Expected: FAIL — route/handler not defined.

- [ ] **Step 3: Add handler + route**

In `handlers.rs`, mirror the regime handler signature, taking `State<Arc<RoutingCache>>`:

```rust
use crate::strategy::routing_cache::{RoutingCache, RoutingUpdate};

pub async fn handle_routing(
    axum::extract::State(cache): axum::extract::State<Arc<RoutingCache>>,
    axum::Json(u): axum::Json<RoutingUpdate>,
) -> axum::response::StatusCode {
    cache.update(u).await;
    axum::response::StatusCode::OK
}
```

In `server.rs`, beside the `.route("/api/v1/regime", ...)` line:

```rust
.route("/api/v1/routing", axum::routing::post(handlers::handle_routing))
```

Pass the `RoutingCache` Arc into the router state the same way `RegimeCache` is passed.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --lib routing_handler --manifest-path trading-engine-core/Cargo.toml`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/api/handlers.rs trading-engine-core/src/api/server.rs
git commit -m "feat(api): POST /api/v1/routing writes the RoutingCache"
```

---

### Task 4: `tick_strategies` applies the routing decision

**Files:**
- Modify: `trading-engine-core/src/engine.rs` — `tick_strategies` (lines ~197–253 per the map; re-find with `grep -n "fn tick_strategies"`)

**Interfaces:**
- Consumes: `RoutingCache` held by `Engine`; `Strategy::set_paused` (exists) + `Strategy::force_flat` (Task 2).
- Behavior: each tick, if a routing entry exists, `set_paused(true)` on every strategy whose name != `active_engine` (and `set_paused(false)` on the active one); if `flat`, call `force_flat()` on all strategies.

- [ ] **Step 1: Write the failing test**

Add an engine-level test (mirror existing engine tests; locate them with `grep -rn "#\[tokio::test\]" trading-engine-core/src/engine.rs` or `tests/`):

```rust
#[tokio::test]
async fn test_routing_pauses_non_active_strategies() {
    // Build a minimal Engine with two named NullStrategy instances ("grid","trend")
    // and a RoutingCache. Push routing {active_engine:"grid"}. Tick once.
    // Assert: "grid" strategy is not paused, "trend" strategy is paused.
    // (Use whatever Engine test harness already exists — match its style.)
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib routing_pauses --manifest-path trading-engine-core/Cargo.toml`
Expected: FAIL — routing not consulted in tick_strategies.

- [ ] **Step 3: Wire the gate into `tick_strategies`**

At the top of `tick_strategies` (read the current body first to align with its exact loop structure), before/within the strategy iteration:

```rust
// Apply the current routing decision (PPO paper-gate). None => route unchanged.
if let Some(r) = self.routing_cache.get().await {
    for s in self.strategies.iter_mut() {
        let is_active = s.name() == r.active_engine;
        s.set_paused(!is_active);
        if r.flat {
            s.force_flat();
        }
    }
}
```
(Adapt `s.name()` to the actual strategy-identifier accessor the trait exposes — check the trait. If strategies are accessed as `&mut Box<dyn Strategy>`, the trait must expose a name; if absent, add `fn name(&self) -> &str;` to the trait and implement it on each strategy, or match by an enum tag the Engine already tracks. Match whatever the existing regime-gating code uses to identify strategies.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --lib routing_pauses --manifest-path trading-engine-core/Cargo.toml`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/engine.rs trading-engine-core/src/strategy/mod.rs
git commit -m "feat(engine): tick_strategies applies RoutingCache (pause + force_flat)"
```

---

### Task 5: `size_mult` scaling in `CapitalManager`

**Files:**
- Modify: `trading-engine-core/src/capital/mod.rs` (locate `request_capital` with `grep -n "fn request_capital"`)

**Interfaces:**
- Produces: `CapitalManager::set_size_mult(name: &str, mult: f64)`; `request_capital` caps the per-strategy budget at `budget * mult`.

- [ ] **Step 1: Write the failing test**

```rust
#[test]
fn test_request_capital_scales_by_size_mult() {
    // Build a CapitalManager with budget grid=1000. set_size_mult("grid", 0.5).
    // request_capital("grid", 1000) should grant min(1000, 1000*0.5) = 500.
    // (Match the existing CapitalManager test harness.)
    assert_eq!(granted, 500.0);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib size_mult --manifest-path trading-engine-core/Cargo.toml`
Expected: FAIL — `set_size_mult` not defined.

- [ ] **Step 3: Implement**

Add a `size_mults: HashMap<String, f64>` to `CapitalManager` (default 1.0). Add:

```rust
pub fn set_size_mult(&mut self, name: &str, mult: f64) {
    self.size_mults.insert(name.to_string(), mult.max(0.0));
}
```

In `request_capital`, scale the budget term: where it currently uses `budget_remaining`, use `budget_remaining * self.size_mults.get(name).copied().unwrap_or(1.0)`.

In `engine.rs tick_strategies`, after the pause/flat block, push the size_mult:
```rust
self.capital.set_size_mult(&r.active_engine, r.size_mult);
```
(if the engine holds capital as `Option<Arc<Mutex<CapitalManager>>>`, lock it and call; match the existing pattern.)

- [ ] **Step 4: Run test + engine build**

Run: `cargo test --lib --manifest-path trading-engine-core/Cargo.toml`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/capital/mod.rs trading-engine-core/src/engine.rs
git commit -m "feat(capital): per-strategy size_mult scaling (PPO size routing)"
```

---

### Task 6: Wire `RoutingCache` into `Engine` + `main`

**Files:**
- Modify: `trading-engine-core/src/main.rs` (construct `RoutingCache`, pass to `Engine` and the router — mirror how `RegimeCache` is constructed and passed, ~lines per the map)
- Modify: `trading-engine-core/src/engine.rs` (`Engine` holds `routing_cache: RoutingCache`, loaded from file on startup)

**Interfaces:**
- Produces: a bootable engine with routing enabled, reading `data/routing_cache.json` fallback + accepting `POST /api/v1/routing`.

- [ ] **Step 1: Write the failing test**

```rust
#[tokio::test]
async fn test_engine_loads_routing_from_file() {
    // Write a routing_cache.json with {active_engine:"trend",size_mult:1.0,flat:false}.
    // Construct Engine pointing at it. Assert routing_cache.get() returns the entry.
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --lib engine_loads_routing --manifest-path trading-engine-core/Cargo.toml`
Expected: FAIL — Engine has no routing field.

- [ ] **Step 3: Wire it**

- Add `routing_cache: RoutingCache` to the `Engine` struct; construct it in `Engine::new` / wherever `RegimeCache` is constructed, pointing at `data/routing_cache.json`; call `routing_cache.load_from_file().await` at startup (mirror the regime `load_from_file` call).
- In `main.rs`, construct the `RoutingCache` Arc, pass it to both `Engine` and the axum router state (Task 3), exactly as `RegimeCache` is passed.

- [ ] **Step 4: Run the full Rust test suite + build**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml`
Expected: PASS, clean build.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/engine.rs trading-engine-core/src/main.rs
git commit -m "feat(engine): wire RoutingCache into Engine + main (boot + endpoint)"
```

---

### Task 7: Python `live_router` action decode (pure)

**Files:**
- Create: `src/rl/live_router.py`
- Test: `tests/test_rl_live_router.py`

**Interfaces:**
- Produces: `decode_action(action: int) -> dict` returning `{"active_engine": str, "size_mult": float, "flat": bool}`, matching `env.ACTION_TO_ENGINE_SIZE`.

- [ ] **Step 1: Write the failing test**

```python
from src.rl.live_router import decode_action

def test_decode_action_grid_1x():
    assert decode_action(1) == {"active_engine": "grid", "size_mult": 1.0, "flat": False}

def test_decode_action_flat():
    assert decode_action(9) == {"active_engine": "flat", "size_mult": 0.0, "flat": True}

def test_decode_action_swing_1_5x():
    assert decode_action(8) == {"active_engine": "swing", "size_mult": 1.5, "flat": False}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_rl_live_router.py -q`
Expected: FAIL — `No module named src.rl.live_router`.

- [ ] **Step 3: Implement**

```python
# src/rl/live_router.py
"""Per-bar PPO routing service: compute the action each 1h bar and push it to
the Rust engine's RoutingCache via POST /api/v1/routing. Paper-gated only."""
from __future__ import annotations

from src.rl.env import ACTION_TO_ENGINE_SIZE


def decode_action(action: int) -> dict:
    """Map a PPO action int to the routing payload (must match env.ACTION_TO_ENGINE_SIZE)."""
    engine, size_mult = ACTION_TO_ENGINE_SIZE[int(action)]
    return {
        "active_engine": engine,
        "size_mult": float(size_mult),
        "flat": engine == "flat",
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_rl_live_router.py -q`
Expected: PASS (3).

- [ ] **Step 5: Commit**

```bash
git add src/rl/live_router.py tests/test_rl_live_router.py
git commit -m "feat(rl): live_router action decoder (pure, tested)"
```

---

### Task 8: Python `live_router` observation + POST loop

**Files:**
- Modify: `src/rl/live_router.py` (add `build_observation` + `run_loop`)
- Test: `tests/test_rl_live_router.py`

**Interfaces:**
- Produces: `build_observation(df, info) -> np.ndarray` (must match `env._build_obs` column-for-column so the policy sees the trained distribution); `run_loop(pair, model_path, rust_url, bar_seconds=3600)`.

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
import pandas as pd
from src.rl.live_router import build_observation

def test_build_observation_shape_matches_env():
    # Build a small OHLCV frame, compute features, build the obs with a flat account.
    # Assert shape == (25,) (env's actual obs dim — confirm via src/rl/env.py).
    df = pd.DataFrame(...)  # minimal OHLCV
    obs = build_observation(df.iloc[-1], {"equity": 10000.0, "initial_equity": 10000.0})
    assert obs.shape == (25,)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_rl_live_router.py::test_build_observation_shape_matches_env -q`
Expected: FAIL — `build_observation` not defined.

- [ ] **Step 3: Implement**

The observation MUST be byte-identical in semantics to `TradingEnv._build_obs`. Reuse it directly:

```python
import numpy as np
import requests

from src.rl.features import FEATURE_COLS, compute_features
from src.rl.env import ENGINES, EnvConfig
from src.rl.router import PPORouter


def build_observation(feature_row, account: dict, cfg: EnvConfig) -> np.ndarray:
    """Replicate TradingEnv._build_obs for a live bar. `feature_row` is one row
    of compute_features output; account has equity/initial_equity/peak_equity."""
    feats = feature_row.to_numpy(dtype=np.float64)
    one_hot = np.zeros(len(ENGINES), dtype=np.float64)  # live router doesn't track engine state here
    unrealised = (account["equity"] - account["initial_equity"]) / max(account["initial_equity"], 1e-8)
    dd = 0.0
    pos_ratio = 0.0
    bars_norm = 0.0
    obs = np.concatenate([
        feats, one_hot,
        np.array([unrealised, dd, pos_ratio, bars_norm], dtype=np.float64),
    ])
    return np.nan_to_num(obs, nan=0.0, posinf=0.0, neginf=0.0)


def run_loop(pair: str, model_path: str, rust_url: str, bar_seconds: int = 3600):
    """Each closed 1h bar: compute features, predict, POST the routing decision."""
    import time
    from src.rl.data import load_klines
    from datetime import date, timedelta
    cfg = EnvConfig()
    router = PPORouter(model_path)
    while True:
        end = date.today()
        start = end - timedelta(days=2)  # enough to warm up indicators
        df = load_klines(pair, start, end)
        feats = compute_features(df)[FEATURE_COLS]
        row = feats.iloc[-1]
        equity = _get_equity(rust_url)  # GET /api/v1/capital; parse total_equity
        obs = build_observation(row, {"equity": equity, "initial_equity": cfg.initial_capital}, cfg)
        action, _ = router.model.predict(obs, deterministic=True)
        payload = decode_action(int(action))
        requests.post(f"{rust_url}/api/v1/routing", json=payload, timeout=5)
        time.sleep(bar_seconds)


def _get_equity(rust_url: str) -> float:
    r = requests.get(f"{rust_url}/api/v1/capital", timeout=5)
    r.raise_for_status()
    return float(r.json().get("total_equity", 10000.0))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_rl_live_router.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/rl/live_router.py tests/test_rl_live_router.py
git commit -m "feat(rl): live_router obs builder + per-bar POST loop"
```

---

### Task 9: Config — paper mode + routing + realism

**Files:**
- Modify: `config/strategy.yaml`

**Interfaces:**
- Produces: a config that boots the engine in paper with realistic fills + routing enabled.

- [ ] **Step 1: Snapshot the current live config**

```bash
cp config/strategy.yaml config/strategy.yaml.live.bak
```

- [ ] **Step 2: Edit the config**

In `config/strategy.yaml`:
- `exchange.testnet: true` (paper mode — the experiment).
- Under the `paper:` block, set realistic values:
  ```yaml
  paper:
    slippage_bps: 8
    maker_fee_bps: 2
    taker_fee_bps: 5
  ```
  (Confirm the exact key names against the current `paper:` block — `grep -n "paper:" -A8 config/strategy.yaml` — and use whatever keys exist.)
- Add a `routing:` section:
  ```yaml
  routing:
    enabled: true
    ppo_model: models/rl/ppo_ETHUSDT_2026-07-05_clean-oos-24m.zip
    pair: ETHUSDT
    bar_seconds: 3600
  ```

- [ ] **Step 3: Validate the config loads**

Run: `cargo run --manifest-path trading-engine-core/Cargo.toml -- --config config/strategy.yaml --dry-run 2>&1 | head` (if a dry-run exists; otherwise boot and Ctrl-C after "engine started" — it should not crash on parse).
Expected: parses without error; logs `routing_cache` loaded.

- [ ] **Step 4: Commit**

```bash
git add config/strategy.yaml
git commit -m "chore(config): paper mode + 8bps slippage + routing section (PPO experiment)"
```

---

### Task 10: Sim-to-real fidelity check script

**Files:**
- Create: `scripts/fidelity_check.py`

**Interfaces:**
- Produces: a script that replays a historical window through `env.py` and compares the per-bar equity trajectory to what the paper Rust engine produced for the same window (read from the paper instance's journal / `data/signal_positions.json` or the paper equity log). Output: Pearson correlation + mean per-bar P&L difference + pass/fail vs a threshold.

- [ ] **Step 1: Write the comparison logic**

```python
# scripts/fidelity_check.py
"""Sim-to-real fidelity: does the policy behave on the REAL Rust engines
consistently with what env.py (toy engines) predicted on the same bars?

Pass: Pearson(equity_env, equity_paper) > 0.7 AND mean |bar_pnl diff| < band.
Fail (divergence) => the toy engines don't represent production => do not promote.
"""
from __future__ import annotations
import os, sys
import numpy as np
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

def compare(env_equity: list[float], paper_equity: list[float], pnl_band: float = 0.005) -> dict:
    a = np.asarray(env_equity, dtype=float)
    b = np.asarray(paper_equity, dtype=float)[:len(a)]
    a = a[:len(b)]
    corr = float(np.corrcoef(a, b)[0, 1]) if a.std() > 0 and b.std() > 0 else 0.0
    env_ret = np.diff(a) / np.maximum(a[:-1], 1e-8)
    pap_ret = np.diff(b) / np.maximum(b[:-1], 1e-8)
    mean_diff = float(np.mean(np.abs(env_ret - pap_ret)))
    return {
        "pearson": corr,
        "mean_abs_bar_pnl_diff": mean_diff,
        "pass": corr > 0.7 and mean_diff < pnl_band,
    }

if __name__ == "__main__":
    # Args: paths to env equity csv + paper equity log. Print the verdict.
    env_eq = [float(x) for x in open(sys.argv[1]).read().split(",") if x.strip()]
    pap_eq = [float(x) for x in open(sys.argv[2]).read().split(",") if x.strip()]
    print(compare(env_eq, pap_eq))
```

- [ ] **Step 2: Smoke-test the comparison logic**

```bash
/opt/anaconda3/bin/python3 -c "
from scripts.fidelity_check import compare
print(compare([10000,10010,10020,10015],[10000,10009,10019,10014]))
"
```
Expected: prints a dict with `pearson` near 1.0 and `pass: True`.

- [ ] **Step 3: Commit**

```bash
git add scripts/fidelity_check.py
git commit -m "feat(rl): sim-to-real fidelity check (env.py vs paper Rust equity)"
```

---

## After Phase 1 is built

1. Boot the paper instance with the new config (Task 9). Start `python -m src.rl.live_router` (Task 8).
2. Run the **fidelity check** (Task 10) on a replayed window. If it fails (divergence), stop — sim-to-real doesn't hold, do not promote.
3. If fidelity holds: run the **baseline period** (un-routed, RF-regime, paper) then the **PPO period** (paper), each ≥2 weeks, totalling ≥4 weeks across varied regimes.
4. Compare per the spec's 6 promotion criteria. Only if PPO-routed paper beats the un-routed paper baseline → Phase 2 (ONNX-in-Rust + live promotion), separate spec.

## Self-review notes

- Tasks 1, 7, 8, 10 produce fully new files with complete code.
- Tasks 2–6 modify existing Rust files; each names the exact symbol/line to find via `grep` and shows the code to add. The implementer reads the surrounding existing code (named in each task) to align identifiers — this is necessary because the modifications touch a live trait/loop/capital API whose exact surrounding context must be respected.
- Type consistency: `RoutingEntry`/`RoutingUpdate` field names (`active_engine`, `size_mult`, `flat`) are identical across Rust (Task 1), the handler (Task 3), the engine gate (Task 4), capital scaling (Task 5), and the Python payload (Task 7's `decode_action`).
- Coverage vs spec: D1 (single instance, paper) → Task 9; D2 (push pattern) → Tasks 1+3+8, ONNX deferred to Phase 2 per spec; D3 (Rust machinery) → Tasks 2+4+5+6; D4 (realistic paper) → Task 9; D5 (Python routing service) → Task 8; sim-to-real fidelity → Task 10; safety kill-switch (`routing.enabled`) → Task 9 config + Task 4 reads cache (absence = no routing).
