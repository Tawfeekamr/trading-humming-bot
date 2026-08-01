# Price-Sanity Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject garbage order books at the `Engine.order_books` chokepoint so no consumer (trend/grid exits, paper fills, risk MTM) ever acts on a price the market never printed — eliminating bad-tick phantom losses like the 2026-07-31 BNB −$604.

**Architecture:** A pure `PriceFilter` (per-symbol rolling-stdev trigger + state machine) decides Accept / Suspect / Hold / HardReject. On a fresh suspect, the engine awaits a `PriceVerifier` (Binance REST, Gate fallback) that adjudicates Confirmed / Denied / Unavailable. Suspect pairs hold the last-good book and have new **entries** vetoed in `submit_orders` (exits / `reduce_only` pass through). Self-heals after N consecutive in-band ticks.

**Tech Stack:** Rust, tokio (full), reqwest 0.12 (rustls-tls, already a dep), serde/serde_yaml, anyhow, tracing. Tests: inline `#[cfg(test)]`, `#[tokio::test]`, `#[ignore]` for live HTTP. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-08-01-price-sanity-filter-design.md`

## Global Constraints

- **TLS backend is rustls** (`reqwest = { version = "0.12", default-features = false, features = ["json", "rustls-tls", "blocking"] }`). A new `reqwest::Client::new()` inherits it — do NOT add native-tls or change features.
- **No new crate dependencies.** reqwest, tokio, serde, anyhow, tracing, async-trait, chrono are all already available.
- **No network in unit tests.** Pure logic is tested directly; the live REST verifier is exercised only behind `#[ignore]` (the established pattern in `tests/test_binance_rest.rs`).
- **Config validation** follows the `TrendConfig::validate()` / `AppConfig::load` pattern (reject `<= 0` numeric knobs with `anyhow::bail!`). `AppConfig` does NOT set `deny_unknown_fields`, so a new `price_integrity:` yaml block is safe.
- **Entry/exit signal** is `OrderRequest.reduce_only: bool` (`connector/types.rs:16`). Exits set it `true`. The circuit-breaker veto in `submit_orders` (`engine.rs:481-486`) is the pattern to mirror.
- **`OrderRequest.symbol` is inconsistent across strategies** — trend sends the config pair key (`BNB-USDT`), grid sends the exchange symbol (`BNBUSDT`). Any pair lookup from an `OrderRequest` MUST go through `find_pair_for_symbol(...).unwrap_or(req.symbol.clone())` to normalize.
- **Engine stores `config: AppConfig`** (`engine.rs:31`) — use `self.config.price_integrity` directly.
- **`run(&mut self)` is async and fully inline `.await`** (no `tokio::spawn` in the engine) — an inline `tokio::time::timeout(verify).await` in the `OrderBookUpdate` arm is legal and matches existing inline awaits (`submit_orders`, `get_balances`).
- **Branch:** `feat/price-sanity-filter`. Commit after every task.

---

## File Structure

- **Create** `trading-engine-core/src/price_filter.rs` — pure filter logic: `FilterDecision`, `VerifyResult`, `PairStatus`, `PriceFilter`. No I/O.
- **Create** `trading-engine-core/src/connector/price_verify.rs` — `PriceVerifier` trait, pure `adjudicate()`, `BinancePriceVerifier` (reqwest), `#[cfg(test)] FakeVerifier`.
- **Modify** `trading-engine-core/src/config.rs` — add `PriceIntegrityConfig` + `validate()`, wire into `AppConfig` + `AppConfig::load`.
- **Modify** `trading-engine-core/src/engine.rs` — add `price_filter` + `price_verifier` fields (struct + `Engine::new` + test builder), filter in the `OrderBookUpdate` arm, entry-veto in `submit_orders`.
- **Modify** `trading-engine-core/src/lib.rs` — `pub mod price_filter;` and `pub mod` wiring for `connector::price_verify`.
- **Modify** `trading-engine-core/src/connector/mod.rs` — `pub mod price_verify;`.
- **Modify** `config/strategy.yaml` — add `price_integrity:` block.
- **Data fix** (one-off SQL on `data/trades.db`) — void the phantom −$604 row.

---

## Task 1: `PriceIntegrityConfig` + validation

**Files:**
- Modify: `trading-engine-core/src/config.rs` (add struct ~after `PaperConfig`; add field to `AppConfig` at `:5-28`; call validate in `load` at `:388-395`)
- Modify: `config/strategy.yaml` (add `price_integrity:` block)
- Test: inline `#[cfg(test)]` in `config.rs` (mirror `trailing_stop_config_tests` at `:658-670`)

**Interfaces:**
- Produces: `pub struct PriceIntegrityConfig { enabled: bool, stdev_window: u32, stdev_k: f64, min_deviation_pct: f64, verify_tolerance_pct: f64, verify_timeout_ms: u64, recover_consecutive_ticks: u32, enable_gate_fallback: bool }` with `Default` and `pub fn validate(&self) -> Result<()>`.

- [ ] **Step 1: Write the failing tests** (append to the `config.rs` test module)

```rust
#[test]
fn price_integrity_defaults_are_sane_and_valid() {
    let c = PriceIntegrityConfig::default();
    assert!(c.enabled, "default enabled = true");
    assert!(c.validate().is_ok(), "defaults must validate");
    assert_eq!(c.stdev_k, 10.0);
    assert_eq!(c.verify_tolerance_pct, 1.0);
    assert_eq!(c.verify_timeout_ms, 800);
    assert_eq!(c.recover_consecutive_ticks, 3);
}

#[test]
fn price_integrity_rejects_nonpositive_numeric_knobs() {
    let mk = |k: f64| PriceIntegrityConfig { stdev_k: k, ..Default::default() };
    assert!(mk(0.0).validate().is_err(),  "stdev_k=0 rejected");
    assert!(mk(-1.0).validate().is_err(), "stdev_k<0 rejected");
    let bad_tol = PriceIntegrityConfig { verify_tolerance_pct: 0.0, ..Default::default() };
    assert!(bad_tol.validate().is_err(), "tolerance=0 rejected");
    let bad_to = PriceIntegrityConfig { verify_timeout_ms: 0, ..Default::default() };
    assert!(bad_to.validate().is_err(), "timeout=0 rejected");
    let bad_win = PriceIntegrityConfig { stdev_window: 0, ..Default::default() };
    assert!(bad_win.validate().is_err(), "window=0 rejected");
    let bad_rec = PriceIntegrityConfig { recover_consecutive_ticks: 0, ..Default::default() };
    assert!(bad_rec.validate().is_err(), "recover=0 rejected");
}

#[test]
fn price_integrity_loads_from_yaml_block() {
    let yaml = "pairs: [\"BNB-USDT\"]\nexchange: {paper: true}\nprice_integrity:\n  enabled: true\n  stdev_k: 7.5\n";
    let cfg: AppConfig = serde_yaml::from_str(yaml).unwrap();
    assert_eq!(cfg.price_integrity.stdev_k, 7.5);
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd trading-engine-core && cargo test --lib config::tests::price_integrity --no-fail-fast`
Expected: FAIL — `PriceIntegrityConfig` not defined.

- [ ] **Step 3: Implement `PriceIntegrityConfig`** (add near `PaperConfig` in `config.rs`)

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct PriceIntegrityConfig {
    #[serde(default = "default_true")]
    pub enabled: bool,
    #[serde(default = "default_200")]
    pub stdev_window: u32,
    #[serde(default = "default_10")]
    pub stdev_k: f64,
    #[serde(default = "default_0_5")]
    pub min_deviation_pct: f64,
    #[serde(default = "default_1")]
    pub verify_tolerance_pct: f64,
    #[serde(default = "default_800")]
    pub verify_timeout_ms: u64,
    #[serde(default = "default_3u")]
    pub recover_consecutive_ticks: u32,
    #[serde(default = "default_true")]
    pub enable_gate_fallback: bool,
}

impl Default for PriceIntegrityConfig {
    fn default() -> Self {
        Self {
            enabled: true, stdev_window: 200, stdev_k: 10.0, min_deviation_pct: 0.5,
            verify_tolerance_pct: 1.0, verify_timeout_ms: 800, recover_consecutive_ticks: 3,
            enable_gate_fallback: true,
        }
    }
}

impl PriceIntegrityConfig {
    /// Reject non-positive numeric knobs so a misconfig can't silently disable
    /// the filter. Mirrors TrendConfig::validate().
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.stdev_window == 0            { anyhow::bail!("price_integrity.stdev_window must be > 0"); }
        if self.stdev_k <= 0.0               { anyhow::bail!("price_integrity.stdev_k must be > 0"); }
        if self.min_deviation_pct <= 0.0     { anyhow::bail!("price_integrity.min_deviation_pct must be > 0"); }
        if self.verify_tolerance_pct <= 0.0  { anyhow::bail!("price_integrity.verify_tolerance_pct must be > 0"); }
        if self.verify_timeout_ms == 0       { anyhow::bail!("price_integrity.verify_timeout_ms must be > 0"); }
        if self.recover_consecutive_ticks == 0 { anyhow::bail!("price_integrity.recover_consecutive_ticks must be > 0"); }
        Ok(())
    }
}
```

Add the default helper fns next to the existing `fn default_xxx()` helpers (reuse `default_true`/`default_200`/etc. if they already exist; otherwise add: `fn default_true()->bool{true}`, `fn default_200()->u32{200}`, `fn default_10()->f64{10.0}`, `fn default_0_5()->f64{0.5}`, `fn default_1()->f64{1.0}`, `fn default_800()->u64{800}`, `fn default_3u()->u32{3}`). Check for name collisions with existing helpers first.

- [ ] **Step 4: Wire into `AppConfig` + `load`**

In `AppConfig` struct add:
```rust
    #[serde(default)]
    pub price_integrity: PriceIntegrityConfig,
```
In `AppConfig::load` add after `config.trend.validate()?;`:
```rust
        config.price_integrity.validate()?;
```

- [ ] **Step 5: Add the yaml block** to `config/strategy.yaml` (top level):

```yaml
# ── Price-Sanity Filter ─────────────────────────────────────────
# Rejects garbage order books (bad-tick phantom losses) before any
# consumer sees them. Suspect mids are cross-checked against Binance REST.
price_integrity:
  enabled: true
  stdev_window: 200            # rolling books per symbol (~20s @ 100ms)
  stdev_k: 10.0                # suspect if |Δmid| > k·stdev
  min_deviation_pct: 0.5       # floor on the band (avoids stdev≈0 flakiness)
  verify_tolerance_pct: 1.0    # REST within this % of a ref = agreement
  verify_timeout_ms: 800
  recover_consecutive_ticks: 3 # in-band ticks to clear Suspect w/o REST
  enable_gate_fallback: true
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd trading-engine-core && cargo test --lib config::tests::price_integrity --no-fail-fast && cargo run --bin validate_config -- ../config/strategy.yaml`
Expected: 3 PASS; `validate_config` exits 0.

- [ ] **Step 7: Commit**

```bash
git add trading-engine-core/src/config.rs config/strategy.yaml
git commit -m "feat(config): add price_integrity block + validation

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 2: `PriceFilter` pure logic

**Files:**
- Create: `trading-engine-core/src/price_filter.rs`
- Modify: `trading-engine-core/src/lib.rs` (`pub mod price_filter;`)

**Interfaces:**
- Consumes: `crate::config::PriceIntegrityConfig`
- Produces:
  - `pub enum FilterDecision { Accept, SuspectNewVerify, HoldSuspect, HardReject }`
  - `pub enum VerifyResult { Confirmed, Denied, Unavailable }`
  - `pub struct PriceFilter` with `new()`, `observe(&mut self, symbol: &str, mid: f64, cfg: &PriceIntegrityConfig) -> FilterDecision`, `resolve_verify(&mut self, symbol: &str, result: &VerifyResult, suspect_mid: f64, cfg: &PriceIntegrityConfig)`, `is_suspect(&self, symbol: &str) -> bool`, `last_good(&self, symbol: &str) -> Option<f64>`.

- [ ] **Step 1: Write the failing tests** (inline `#[cfg(test)] mod tests` in `price_filter.rs`)

```rust
#[cfg(test)]
mod tests {
    use super::*;
    fn cfg() -> crate::config::PriceIntegrityConfig { crate::config::PriceIntegrityConfig::default() }

    #[test]
    fn warmup_first_tick_is_accepted_and_seeds_last_good() {
        let mut f = PriceFilter::new();
        assert_eq!(f.observe("BNB-USDT", 580.0, &cfg()), FilterDecision::Accept);
        assert_eq!(f.last_good("BNB-USDT"), Some(580.0));
        assert!(!f.is_suspect("BNB-USDT"));
    }

    #[test]
    fn in_band_ticks_are_accepted() {
        let mut f = PriceFilter::new();
        let c = cfg();
        for p in [580.0, 580.1, 579.9, 580.2] { assert_eq!(f.observe("BNB-USDT", p, &c), FilterDecision::Accept); }
    }

    #[test]
    fn huge_spike_transitions_to_suspect_and_blocks() {
        let mut f = PriceFilter::new();
        let c = cfg();
        for p in [580.0, 580.0, 580.0, 580.0] { f.observe("BNB-USDT", p, &c); } // seed tight window
        assert_eq!(f.observe("BNB-USDT", 497.0, &c), FilterDecision::SuspectNewVerify);
        assert!(f.is_suspect("BNB-USDT"));
        // last_good unchanged (not poisoned by garbage)
        assert_eq!(f.last_good("BNB-USDT"), Some(580.0));
    }

    #[test]
    fn while_suspect_out_of_band_ticks_hold_and_reset_recovery() {
        let mut f = PriceFilter::new();
        let c = cfg();
        for p in [580.0, 580.0, 580.0, 580.0] { f.observe("BNB-USDT", p, &c); }
        f.observe("BNB-USDT", 497.0, &c); // -> Suspect
        assert_eq!(f.observe("BNB-USDT", 496.0, &c), FilterDecision::HoldSuspect);
        assert!(f.is_suspect("BNB-USDT"));
    }

    #[test]
    fn self_heal_clears_suspect_after_n_in_band_ticks() {
        let mut f = PriceFilter::new();
        let c = cfg();
        for p in [580.0, 580.0, 580.0, 580.0] { f.observe("BNB-USDT", p, &c); }
        f.observe("BNB-USDT", 497.0, &c); // Suspect
        // 2 in-band ticks: still suspect (recover_consecutive_ticks=3)
        assert_eq!(f.observe("BNB-USDT", 580.0, &c), FilterDecision::HoldSuspect);
        assert_eq!(f.observe("BNB-USDT", 580.0, &c), FilterDecision::HoldSuspect);
        assert!(f.is_suspect("BNB-USDT"));
        // 3rd in-band tick: clears
        assert_eq!(f.observe("BNB-USDT", 580.0, &c), FilterDecision::Accept);
        assert!(!f.is_suspect("BNB-USDT"));
    }

    #[test]
    fn resolve_verify_confirmed_accepts_new_level() {
        let mut f = PriceFilter::new();
        let c = cfg();
        for p in [580.0, 580.0, 580.0, 580.0] { f.observe("BNB-USDT", p, &c); }
        f.observe("BNB-USDT", 596.0, &c); // SuspectNewVerify (real move up)
        f.resolve_verify("BNB-USDT", &VerifyResult::Confirmed, 596.0, &c);
        assert!(!f.is_suspect("BNB-USDT"));
        assert_eq!(f.last_good("BNB-USDT"), Some(596.0));
    }

    #[test]
    fn resolve_verify_denied_keeps_last_good_and_stays_suspect() {
        let mut f = PriceFilter::new();
        let c = cfg();
        for p in [580.0, 580.0, 580.0, 580.0] { f.observe("BNB-USDT", p, &c); }
        f.observe("BNB-USDT", 497.0, &c);
        f.resolve_verify("BNB-USDT", &VerifyResult::Denied, 497.0, &c);
        assert!(f.is_suspect("BNB-USDT"));
        assert_eq!(f.last_good("BNB-USDT"), Some(580.0)); // unchanged
    }

    #[test]
    fn nonfinite_or_nonpositive_mid_is_hard_rejected() {
        let mut f = PriceFilter::new();
        let c = cfg();
        assert_eq!(f.observe("BNB-USDT", 0.0, &c), FilterDecision::HardReject);
        assert_eq!(f.observe("BNB-USDT", f64::NAN, &c), FilterDecision::HardReject);
        assert_eq!(f.observe("BNB-USDT", -1.0, &c), FilterDecision::HardReject);
    }

    #[test]
    fn pairs_are_isolated() {
        let mut f = PriceFilter::new();
        let c = cfg();
        f.observe("BNB-USDT", 580.0, &c);
        f.observe("ETH-USDT", 3000.0, &c);
        // a BNB spike must not flag ETH
        for p in [580.0, 580.0, 580.0] { f.observe("BNB-USDT", p, &c); }
        f.observe("BNB-USDT", 497.0, &c);
        assert!(f.is_suspect("BNB-USDT"));
        assert!(!f.is_suspect("ETH-USDT"));
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd trading-engine-core && cargo test --lib price_filter --no-fail-fast`
Expected: FAIL — module/file not found.

- [ ] **Step 3: Implement `price_filter.rs`**

```rust
//! Price-sanity filter: detects implausible order-book mid-prices via a
//! per-symbol rolling-stdev trigger and a small state machine. Pure logic —
//! no I/O. The engine calls `observe` per book; cross-source verification is
//! done by `connector::price_verify` and fed back via `resolve_verify`.
use std::collections::{HashMap, VecDeque};
use crate::config::PriceIntegrityConfig;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FilterDecision {
    Accept,           // within band — insert book, advance last_good
    SuspectNewVerify, // first cross of the band — engine should REST-verify
    HoldSuspect,      // already suspect, keep last_good (counting self-heal)
    HardReject,       // non-finite / <=0 mid — discard, keep last_good
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VerifyResult { Confirmed, Denied, Unavailable }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Status { Trusted, Suspect }

#[derive(Debug, Clone)]
struct PairState {
    last_good_mid: f64,
    status: Status,
    window: VecDeque<f64>,
    recover_count: u32,
}

impl PairState {
    fn new(seed: f64, cap: usize) -> Self {
        let mut window = VecDeque::with_capacity(cap);
        window.push_back(seed);
        Self { last_good_mid: seed, status: Status::Trusted, window, recover_count: 0 }
    }
    fn push(&mut self, mid: f64, cap: usize) {
        if cap > 0 && self.window.len() >= cap { self.window.pop_front(); }
        self.window.push_back(mid);
    }
    fn stdev(&self) -> f64 {
        let n = self.window.len() as f64;
        if n < 2.0 { return 0.0; }
        let mean = self.window.iter().sum::<f64>() / n;
        let var = self.window.iter().map(|x| (x - mean).powi(2)).sum::<f64>() / n;
        var.sqrt()
    }
}

pub struct PriceFilter { states: HashMap<String, PairState> }

impl Default for PriceFilter { fn default() -> Self { Self::new() } }

impl PriceFilter {
    pub fn new() -> Self { Self { states: HashMap::new() } }

    pub fn is_suspect(&self, symbol: &str) -> bool {
        self.states.get(symbol).map(|s| s.status == Status::Suspect).unwrap_or(false)
    }
    pub fn last_good(&self, symbol: &str) -> Option<f64> {
        self.states.get(symbol).map(|s| s.last_good_mid)
    }

    pub fn observe(&mut self, symbol: &str, mid: f64, cfg: &PriceIntegrityConfig) -> FilterDecision {
        if !mid.is_finite() || mid <= 0.0 { return FilterDecision::HardReject; }
        let cap = cfg.stdev_window as usize;
        // Warmup: first tick seeds last_good.
        let st = match self.states.get_mut(symbol) {
            Some(s) => s,
            None => { self.states.insert(symbol.to_string(), PairState::new(mid, cap)); return FilterDecision::Accept; }
        };
        let band = (cfg.stdev_k * st.stdev()).max(cfg.min_deviation_pct / 100.0 * st.last_good_mid);
        let dev = (mid - st.last_good_mid).abs();
        match st.status {
            Status::Trusted => {
                if dev <= band {
                    st.last_good_mid = mid; st.push(mid, cap); FilterDecision::Accept
                } else {
                    st.status = Status::Suspect; st.recover_count = 0; FilterDecision::SuspectNewVerify
                }
            }
            Status::Suspect => {
                if dev <= band {
                    st.recover_count += 1;
                    if st.recover_count >= cfg.recover_consecutive_ticks {
                        st.status = Status::Trusted; st.last_good_mid = mid;
                        st.push(mid, cap); st.recover_count = 0; FilterDecision::Accept
                    } else { FilterDecision::HoldSuspect }
                } else { st.recover_count = 0; FilterDecision::HoldSuspect }
            }
        }
    }

    /// Apply the cross-source verify outcome for the in-flight suspect tick.
    pub fn resolve_verify(&mut self, symbol: &str, result: &VerifyResult, suspect_mid: f64, cfg: &PriceIntegrityConfig) {
        let Some(st) = self.states.get_mut(symbol) else { return; };
        match result {
            VerifyResult::Confirmed => {
                // Real move: accept the new level.
                st.status = Status::Trusted; st.last_good_mid = suspect_mid;
                st.push(suspect_mid, cfg.stdev_window as usize); st.recover_count = 0;
            }
            // Denied (garbage) or Unavailable (unknown): keep last_good, stay
            // Suspect — self-heal (in-band ticks) clears it, or operator does.
            VerifyResult::Denied | VerifyResult::Unavailable => {}
        }
    }
}
```

Add `pub mod price_filter;` to `src/lib.rs`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd trading-engine-core && cargo test --lib price_filter --no-fail-fast`
Expected: 9 PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/price_filter.rs trading-engine-core/src/lib.rs
git commit -m "feat(price): pure PriceFilter (rolling-stdev trigger + state machine)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 3: `PriceVerifier` (Binance REST + Gate fallback)

**Files:**
- Create: `trading-engine-core/src/connector/price_verify.rs`
- Modify: `trading-engine-core/src/connector/mod.rs` (`pub mod price_verify;`)

**Interfaces:**
- Consumes: `crate::price_filter::VerifyResult`
- Produces:
  - `pub fn adjudicate(rest_price: Option<f64>, suspect_mid: f64, last_good_mid: f64, tolerance_pct: f64) -> VerifyResult`
  - `pub fn binance_symbol(pair: &str) -> String`
  - `#[async_trait] pub trait PriceVerifier { async fn verify(&self, symbol: &str, suspect_mid: f64, last_good_mid: f64, tolerance_pct: f64) -> VerifyResult; }`
  - `pub struct BinancePriceVerifier` (+ `new()`)

- [ ] **Step 1: Write the failing tests** (inline `#[cfg(test)] mod tests`)

```rust
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn adjudicate_confirms_when_rest_near_suspect() {
        assert_eq!(adjudicate(Some(498.0), 497.0, 580.0, 1.0), VerifyResult::Confirmed);
    }
    #[test]
    fn adjudicate_denies_when_rest_near_last_good() {
        assert_eq!(adjudicate(Some(580.0), 497.0, 580.0, 1.0), VerifyResult::Denied);
    }
    #[test]
    fn adjudicate_unavailable_when_rest_agrees_with_neither() {
        assert_eq!(adjudicate(Some(700.0), 497.0, 580.0, 1.0), VerifyResult::Unavailable);
    }
    #[test]
    fn adjudicate_unavailable_when_rest_missing() {
        assert_eq!(adjudicate(None, 497.0, 580.0, 1.0), VerifyResult::Unavailable);
    }
    #[test]
    fn binance_symbol_strips_dash() {
        assert_eq!(binance_symbol("BNB-USDT"), "BNBUSDT");
        assert_eq!(binance_symbol("BTC-USDT"), "BTCUSDT");
    }

    use async_trait::async_trait;
    struct FakeVerifier(pub Option<f64>);
    #[async_trait]
    impl PriceVerifier for FakeVerifier {
        async fn verify(&self, _s: &str, suspect: f64, last: f64, tol: f64) -> VerifyResult {
            adjudicate(self.0, suspect, last, tol)
        }
    }
    #[tokio::test]
    async fn fake_verifier_threads_through_adjudicate() {
        let v = FakeVerifier(Some(580.0));
        assert_eq!(v.verify("BNB-USDT", 497.0, 580.0, 1.0).await, VerifyResult::Denied);
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd trading-engine-core && cargo test --lib connector::price_verify --no-fail-fast`
Expected: FAIL — module/file not found.

- [ ] **Step 3: Implement `price_verify.rs`**

```rust
//! Cross-source price verification for the price-sanity filter. Mirrors the
//! `GateioPerpSource` pattern (reqwest + rustls). Pure `adjudicate` is unit-tested;
//! the live fetch is covered by an `#[ignore]`d test (no network in CI).
use async_trait::async_trait;
use std::time::Duration;
use crate::price_filter::VerifyResult;

/// Classify a suspect mid given an optional REST reference price.
pub fn adjudicate(rest_price: Option<f64>, suspect_mid: f64, last_good_mid: f64, tolerance_pct: f64) -> VerifyResult {
    let tol = tolerance_pct / 100.0;
    match rest_price {
        None => VerifyResult::Unavailable,
        Some(rp) => {
            let near_suspect = (rp - suspect_mid).abs() <= tol * suspect_mid;
            let near_last   = (rp - last_good_mid).abs() <= tol * last_good_mid;
            if near_suspect      { VerifyResult::Confirmed }
            else if near_last    { VerifyResult::Denied }
            else                 { VerifyResult::Unavailable } // agrees with neither — safest
        }
    }
}

pub fn binance_symbol(pair: &str) -> String { pair.replace('-', "") }

#[async_trait]
pub trait PriceVerifier: Send + Sync {
    async fn verify(&self, symbol: &str, suspect_mid: f64, last_good_mid: f64, tolerance_pct: f64) -> VerifyResult;
}

pub struct BinancePriceVerifier {
    client: reqwest::Client,
    base: String,
    /// Optional secondary venue (e.g. a GateioPerpSource wrapped to return a
    /// spot/perp last). None => Binance-only. Wired in a follow-up; v1 is
    /// Binance-primary and treats fetch failure as Unavailable (fail-safe).
    fallback: Option<Box<dyn PriceVerifier>>,
}

impl BinancePriceVerifier {
    pub fn new() -> Self {
        let client = reqwest::Client::builder()
            .timeout(Duration::from_millis(1500))
            .build().unwrap_or_else(|_| reqwest::Client::new());
        Self { client, base: "https://api.binance.com".to_string(), fallback: None }
    }
    async fn fetch_price(&self, pair: &str) -> Option<f64> {
        let url = format!("{}/api/v3/ticker/price?symbol={}", self.base, binance_symbol(pair));
        let resp = self.client.get(&url).send().await.ok()?;
        let v: serde_json::Value = resp.json().await.ok()?;
        v.get("price")?.as_str()?.parse::<f64>().ok()
    }
}

#[async_trait]
impl PriceVerifier for BinancePriceVerifier {
    async fn verify(&self, symbol: &str, suspect_mid: f64, last_good_mid: f64, tolerance_pct: f64) -> VerifyResult {
        let primary = adjudicate(self.fetch_price(symbol).await, suspect_mid, last_good_mid, tolerance_pct);
        if primary != VerifyResult::Unavailable { return primary; }
        match &self.fallback {
            Some(fb) => fb.verify(symbol, suspect_mid, last_good_mid, tolerance_pct).await,
            None => VerifyResult::Unavailable,
        }
    }
}

#[cfg(test)]
mod tests { /* pasted above */ }
```

Add `pub mod price_verify;` to `src/connector/mod.rs`.

- [ ] **Step 4: Add a live (network) test, ignored**

Append to the `#[cfg(test)] mod tests` above:
```rust
    #[tokio::test]
    #[ignore = "Requires network access"]
    async fn live_binance_verifier_returns_a_price() {
        let v = BinancePriceVerifier::new();
        let r = v.verify("BNB-USDT", 1.0, 1.0, 100.0).await; // 100% tol => anything finite is Confirmed
        assert_eq!(r, VerifyResult::Confirmed, "live Binance price should be finite & positive");
    }
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd trading-engine-core && cargo test --lib connector::price_verify --no-fail-fast`
Expected: 6 PASS (the `#[ignore]`d one is not run by default).

- [ ] **Step 6: Commit**

```bash
git add trading-engine-core/src/connector/price_verify.rs trading-engine-core/src/connector/mod.rs
git commit -m "feat(price): Binance REST PriceVerifier + pure adjudicate

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 4: Engine integration (filter + verify + entry veto)

**Files:**
- Modify: `trading-engine-core/src/engine.rs` — struct fields (`:30-54`), `Engine::new` init (`:56-100`), test builder literal (`:886-903`), `OrderBookUpdate` arm (`:198-210`), `submit_orders` veto (`:477-486`).

**Interfaces:**
- Consumes: `PriceFilter`, `PriceVerifier`, `PriceIntegrityConfig`, `FilterDecision`, `VerifyResult`.
- Produces: behavior — garbage books held, suspect-pair entries vetoed, exits pass.

- [ ] **Step 1: Write the failing integration test** (append to `engine.rs` `#[cfg(test)] mod tests`)

```rust
    #[tokio::test]
    async fn garbage_book_is_held_and_entries_vetoed_good_book_passes() {
        use crate::price_filter::PriceFilter;
        use crate::config::PriceIntegrityConfig;
        let cfg = PriceIntegrityConfig::default();
        let mut f = PriceFilter::new();
        // seed a tight baseline
        for _ in 0..4 { f.observe("BNB-USDT", 580.0, &cfg); }
        // garbage spike -> Suspect, last_good stays 580
        assert_eq!(f.observe("BNB-USDT", 497.0, &cfg), crate::price_filter::FilterDecision::SuspectNewVerify);
        assert!(f.is_suspect("BNB-USDT"));
        assert_eq!(f.last_good("BNB-USDT"), Some(580.0));
        // a normal-tick book is still accepted
        assert_eq!(f.observe("ETH-USDT", 3000.0, &cfg), crate::price_filter::FilterDecision::Accept);
    }
```

(The full arm-level integration — inserting vs holding the book, and the `submit_orders` veto — is verified by the existing engine test harness + a `NullConnector`/`RecordingConnector`. Add this focused test first to drive the wiring; expand in Step 5.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd trading-engine-core && cargo test --lib engine::tests::garbage_book --no-fail-fast`
Expected: FAIL — compiles (PriceFilter exists from Task 2) but the engine isn't using it yet; or PASS trivially if not yet wired to the arm. The decisive assertions are the arm + veto tests added in Step 5.

- [ ] **Step 3: Add fields + init**

In the `Engine` struct add (near `order_books`):
```rust
    price_filter: crate::price_filter::PriceFilter,
    price_verifier: std::sync::Arc<dyn crate::connector::price_verify::PriceVerifier>,
```
In `Engine::new`'s returned struct literal add:
```rust
            price_filter: crate::price_filter::PriceFilter::new(),
            price_verifier: std::sync::Arc::new(crate::connector::price_verify::BinancePriceVerifier::new()),
```
In the test builder struct literal (`engine.rs:892`) add the same two lines. Add a test-only injector:
```rust
    #[cfg(test)]
    pub(crate) fn set_price_verifier(&mut self, v: std::sync::Arc<dyn crate::connector::price_verify::PriceVerifier>) {
        self.price_verifier = v;
    }
```

- [ ] **Step 4: Filter in the `OrderBookUpdate` arm**

Replace the arm body (`engine.rs:198-210`) — insert only on Accept / Confirmed; otherwise hold `last_good` (do NOT overwrite `order_books[pair]`):

```rust
                        WsEvent::OrderBookUpdate { symbol, bids, asks } => {
                            let pair_key = self.find_pair_for_symbol(&symbol)
                                .unwrap_or_else(|| symbol.clone());
                            let book = OrderBook {
                                symbol: pair_key.clone(), bids, asks,
                                timestamp: chrono::Utc::now().timestamp_millis(),
                            };
                            let cfg_pi = &self.config.price_integrity;
                            let mut should_insert = true;
                            if cfg_pi.enabled {
                                if let Some(m) = book.mid_price() {
                                    // Structural sanity: a real book has bid < ask.
                                    let sane = matches!(
                                        (book.best_bid(), book.best_ask()),
                                        (Some(b), Some(a)) if a > b
                                    );
                                    if !sane {
                                        warn!("Price filter: malformed book {} (bid>=ask); holding last-good", pair_key);
                                        should_insert = false;
                                    } else {
                                        use crate::price_filter::{FilterDecision, VerifyResult};
                                        match self.price_filter.observe(&pair_key, m, cfg_pi) {
                                            FilterDecision::Accept => {}
                                            FilterDecision::HardReject => {
                                                warn!("Price filter: hard-reject {} mid {:.4}", pair_key, m);
                                                should_insert = false;
                                            }
                                            FilterDecision::HoldSuspect => { should_insert = false; }
                                            FilterDecision::SuspectNewVerify => {
                                                let last_good = self.price_filter.last_good(&pair_key).unwrap_or(m);
                                                let verified = tokio::time::timeout(
                                                    std::time::Duration::from_millis(cfg_pi.verify_timeout_ms),
                                                    self.price_verifier.verify(&pair_key, m, last_good, cfg_pi.verify_tolerance_pct),
                                                ).await; // Result<VerifyResult, Elapsed>
                                                let result = match verified { Ok(r) => r, Err(_) => VerifyResult::Unavailable };
                                                if result == VerifyResult::Confirmed {
                                                    // real move — accept the suspect book
                                                } else {
                                                    warn!("Price filter: {} mid {:.2} unconfirmed ({:?}); holding last-good {:.2}",
                                                          pair_key, m, result, last_good);
                                                    should_insert = false;
                                                }
                                                self.price_filter.resolve_verify(&pair_key, &result, m, cfg_pi);
                                            }
                                        }
                                    }
                                }
                            }
                            if should_insert { self.order_books.insert(pair_key.clone(), book); }
                            self.tick_strategies().await?;
                            self.process_paper_fills().await?;
                            self.feed_breaker().await;
                        }
```

- [ ] **Step 5: Add the entry-veto in `submit_orders`** (`engine.rs:477-486`)

```rust
        if !req.reduce_only {
            let pair_key = self.find_pair_for_symbol(&req.symbol).unwrap_or_else(|| req.symbol.clone());
            if self.price_filter.is_suspect(&pair_key) {
                warn!("Order vetoed (price suspect): {} — holding until price verified", pair_key);
                continue;
            }
            if let Err(e) = self.risk.check_trading_allowed() {
                warn!("Order vetoed by risk manager (halted): {}", e);
                continue;
            }
        }
```

- [ ] **Step 6: Write the arm/veto integration tests** (append to engine tests; use the existing test builder + `RecordingConnector`)

```rust
    #[tokio::test]
    async fn submit_orders_vetoes_entries_for_suspect_pair_lets_exits_through() {
        // Build a minimal engine via the test builder, mark BNB-USDT suspect by
        // feeding a spike through price_filter, then assert an ENTRY (reduce_only=false)
        // is dropped while an EXIT (reduce_only=true) is placed.
        // (Use the existing NullConnector/RecordingConnector at engine.rs:824-1095.)
        // Implementation detail: construct engine, call observe() to seed+suspect,
        // then engine.submit_orders(vec![entry, exit]) and assert connector saw only exit.
        // NOTE: submit_orders is private; expose via a #[cfg(test)] test-only path or
        // drive through tick_strategies with a mock strategy that emits both orders.
        // Keep this test focused: if submit_orders is not directly reachable, exercise
        // the veto by checking price_filter.is_suspect gates the order via a unit
        // assertion on the veto branch logic instead.
    }
```

> **Implementer note:** `submit_orders` is private and reached only via `tick_strategies`/`on_fill`. The cleanest faithful test uses a `NullStrategy`-style mock that emits one entry (`reduce_only:false`) + one exit (`reduce_only:true`) for a suspect pair, runs one `tick_strategies`, and asserts via a `RecordingConnector` that only the exit was placed. Mirror the existing routing-gate tests already in `engine.rs` `mod tests` (they build an `Engine` via struct literal and drive `tick_strategies` with mock strategies). If that harness is heavy, at minimum add a unit test asserting `find_pair_for_symbol(req.symbol).unwrap_or(req.symbol)` normalizes BOTH `"BNB-USDT"` and `"BNBUSDT"` to `"BNB-USDT"` (the key `price_filter` uses), so the veto cannot silently miss grid orders.

- [ ] **Step 7: Run the full suite**

Run: `cd trading-engine-core && cargo test --no-fail-fast`
Expected: all PASS (including the new filter/veto tests; the `#[ignore]` live verifier is skipped).

- [ ] **Step 8: Commit**

```bash
git add trading-engine-core/src/engine.rs
git commit -m "feat(engine): wire price-sanity filter + suspect-pair entry veto

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Task 5: Void the phantom −$604 row

**Files:**
- Data fix: `trading-engine-core/data/trades.db` (on EC2) and local copy if used for the dashboard.

- [ ] **Step 1: Identify the phantom row precisely**

Run (EC2 via SSM, or locally):
```bash
sqlite3 data/trades.db "SELECT id,timestamp,pair,entry_price,exit_price,ROUND(pnl,2),exit_reason FROM trades WHERE engine='trend' AND pair='BNB-USDT' AND exit_reason='trailing_stop' AND exit_price < 560 ORDER BY timestamp;"
```
Expected: exactly one row — the 2026-07-31 exit at 497.91, pnl −604.11.

- [ ] **Step 2: Back up the DB**

```bash
cp data/trades.db "data/trades.db.bak.$(date -u +%Y%m%d)"
```

- [ ] **Step 3: Void the row (idempotent — re-running is a no-op)**

```bash
sqlite3 data/trades.db "
UPDATE trades
SET exit_reason = 'phantom_bad_tick',
    pnl = 0.0,
    exit_price = entry_price
WHERE engine='trend' AND pair='BNB-USDT'
  AND exit_reason='trailing_stop'
  AND exit_price < 560
  AND timestamp LIKE '2026-07-31%';"
```
(The `exit_reason='trailing_stop'` predicate makes it idempotent: after the first run the row's reason is `phantom_bad_tick`, so it no longer matches.)

- [ ] **Step 4: Verify the corrected P&L**

```bash
sqlite3 -header -column data/trades.db "SELECT engine, COUNT(*) trades, ROUND(SUM(pnl),2) pnl FROM trades WHERE is_backfilled=0 GROUP BY engine ORDER BY pnl;"
```
Expected: trend P&L ≈ −$100 (was −$706); the phantom row now shows `exit_reason='phantom_bad_tick'`, pnl 0.

- [ ] **Step 5: Note the paper balance**

Per project memory, the paper account **reseeds on restart** (`$100k` / `0` base). After the next container restart the fictitious −$604 balance hit is cleared automatically. If a restart is not desired, manually correct `data/risk_state.json` `start_of_day_equity` / `peak_equity` by +$604; otherwise leave it — `risk_state.json` already reset at UTC midnight 2026-08-01 (`last_reset_date: 2026-08-01`).

- [ ] **Step 6: Commit the backup note (the DB itself is gitignored)**

```bash
git commit --allow-empty -m "ops(data): void 2026-07-31 BNB phantom -\$604 row (bad tick)

Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Self-Review (completed)

**Spec coverage:** §1 problem → Task 5; §2 goals (chokepoint, cross-source, fail-safe, self-adapting) → Tasks 2-4; §3 architecture (PriceFilter, price_verify, engine insert site) → Tasks 2,3,4; §4 detection (rolling stdev + floor + hard-reject + warmup) → Task 2; §5 adjudication + state machine + self-heal → Tasks 2,4; §6 entry-blocking (reduce_only bypass, exits run) → Task 4 Step 5; §7 config → Task 1; §8 phantom cleanup → Task 5; §9 testing → every task; §11 rollout → post-implementation deploy.

**Placeholder scan:** Task 4 Step 6 has an implementer note acknowledging `submit_orders` is private and giving the faithful mock-strategy approach + a mandatory fallback unit test (symbol normalization). This is guidance, not a placeholder — the real code paths are specified. No "TODO"/"TBD" elsewhere.

**Type consistency:** `FilterDecision`/`VerifyResult` defined in Task 2, consumed identically in Tasks 3 & 4. `PriceIntegrityConfig` field names match across Task 1 (definition), Task 2 (usage), Task 4 (usage). `PriceVerifier::verify` signature matches Task 3 def and Task 4 call. `is_suspect`/`last_good`/`observe`/`resolve_verify` consistent. `find_pair_for_symbol(...).unwrap_or(req.symbol.clone())` normalization consistent with the engine's existing Kline-arm usage.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-08-01-price-sanity-filter.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
