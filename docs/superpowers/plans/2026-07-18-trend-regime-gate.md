# Trend Regime Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the ML regime gate into the trend engine's entry path and prove, via a faithful regime-aware replay, that it would have cut the Jul 4–14 ETH whipsaw loss before any real-money consideration.

**Architecture:** Add `regime_gate` + `min_regime_confidence` to `TrendConfig`; in `trend.rs on_tick`, block new entries when regime is Ranging/Danger at sufficient confidence (entries only — open positions keep being managed). Make the replay backtest inject real per-bar regime labels via a new `--regime-file` flag (closing the known "regime=None" gap). Backfill ETH labels for the losing window by reusing the live Python `regime_pusher` pipeline (zero train/serve skew). Run gated-vs-ungated replay over the identical window; the P&L delta is the proof.

**Tech Stack:** Rust (trading-engine-core: `config.rs`, `strategy/trend.rs`, `backtest/replay.rs`, `bin/backtest_replay.rs`), Python (`src/ml/regime_labels_backfill.py` reusing `src.ml.regime_pusher`), YAML config.

## Global Constraints

- Branch: `fix/trend-regime-gate` (already created; spec committed at `28e2e6a`).
- Gate blocks **new entries only** — never suppress position management (TP/SL/trailing/funding). Mirrors existing `entries_suppressed` semantics (`trend.rs:526`).
- Regime ints are fixed: `0=Ranging, 1=Trending, 2=Danger` (matches `RegimeCache` + `strategy/mod.rs:40` `MarketRegime`).
- Back-compat: `regime_gate` defaults `false`; replay without `--regime-file` keeps `regime=None` (current behavior).
- Label generation must reuse `src.ml.regime_pusher.compute_regime` verbatim — do NOT re-implement the feature path in Rust (skew risk).
- TDD: failing test → implement → pass → commit, per task. Frequent commits.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `trading-engine-core/src/config.rs` | `TrendConfig` struct + defaults | Add 2 fields + 1 default fn |
| `config/strategy.yaml` | Live config | Enable gate for trend |
| `trading-engine-core/src/strategy/trend.rs` | Trend entry logic + tests | Insert gate at entry branch; add tests + helper |
| `trading-engine-core/src/backtest/replay.rs` | Replay harness | Add `RegimeTimeline`; thread regime through `build_ctx_from`/`run_loop` |
| `trading-engine-core/src/bin/backtest_replay.rs` | Backtest CLI | Add `--regime-file`, `--start`, `--end` flags |
| `src/ml/regime_labels_backfill.py` | NEW — ETH label generator | Reuse `compute_regime` over Binance 1h bars |
| `docs/superpowers/specs/...` | Already written | — |
| `backtest/results/eth_regime_jul4-14.json` | NEW — generated labels | Output of Task 4 |
| `docs/study/trend_regime_gate_replay_proof.md` | NEW — proof report | Output of Task 5 |

---

### Task 1: Add `regime_gate` config fields

**Files:**
- Modify: `trading-engine-core/src/config.rs` (TrendConfig struct ~line 203; default fns near other `default_*`)
- Modify: `config/strategy.yaml` (trend section)
- Modify: `trading-engine-core/src/strategy/trend.rs:1301-1316` (`base_test_config()` helper)

**Interfaces:**
- Produces: `TrendConfig.regime_gate: bool` (default `false`), `TrendConfig.min_regime_confidence: f64` (default `0.55`). Consumed by Task 2.

- [ ] **Step 1: Write the failing test**

Append to `trading-engine-core/src/config.rs` test module (if no test module exists, add `#[cfg(test)] mod tests { use super::*; ... }` at file end):

```rust
#[cfg(test)]
mod regime_gate_config_tests {
    use super::*;

    #[test]
    fn trend_regime_gate_defaults_false_when_absent() {
        let yaml = "trend:\n  ema_fast: 20\n";
        #[derive(serde::Deserialize)]
        struct Wrap { trend: super::TrendConfig }
        let w: Wrap = serde_yaml::from_str(yaml).unwrap();
        assert!(!w.trend.regime_gate, "regime_gate must default false");
        assert!((w.trend.min_regime_confidence - 0.55).abs() < 1e-9,
            "min_regime_confidence must default 0.55");
    }

    #[test]
    fn trend_regime_gate_reads_true_when_set() {
        let yaml = "trend:\n  ema_fast: 20\n  regime_gate: true\n  min_regime_confidence: 0.7\n";
        #[derive(serde::Deserialize)]
        struct Wrap { trend: super::TrendConfig }
        let w: Wrap = serde_yaml::from_str(yaml).unwrap();
        assert!(w.trend.regime_gate);
        assert!((w.trend.min_regime_confidence - 0.7).abs() < 1e-9);
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib config::regime_gate_config_tests -- --nocapture`
Expected: FAIL — "no field `regime_gate`" (compile error) — field doesn't exist yet.

- [ ] **Step 3: Add the fields + default fn**

In `trading-engine-core/src/config.rs`, add a default fn next to the other `default_*` fns (e.g. near `default_0_2`):

```rust
fn default_0_55() -> f64 { 0.55 }
```

Add two fields to `pub struct TrendConfig` (after `trade_shorts` / `perp_mark_source` group, before the closing brace — match the existing `#[serde(default)]` style):

```rust
    /// ML regime gate: skip NEW entries when regime ∈ {Ranging, Danger}
    /// at confidence ≥ min_regime_confidence. Entries only — open positions
    /// keep being managed. Default false (back-compat).
    #[serde(default)]
    pub regime_gate: bool,
    /// Minimum classifier confidence to trust a Ranging/Danger label and
    /// gate the entry. Below this, fall back to TA (matches regime-pusher
    /// "low-conf → TA-gated" philosophy). Default 0.55.
    #[serde(default = "default_0_55")]
    pub min_regime_confidence: f64,
```

- [ ] **Step 4: Update `base_test_config()` in `trend.rs:1301`**

The struct literal at `trading-engine-core/src/strategy/trend.rs:1303-1316` must include the new fields (else tests won't compile). Add to the literal, before the closing `}`:

```rust
            regime_gate: false,
            min_regime_confidence: 0.55,
```

(Place after `funding_accrual: false,` to match field order — order doesn't matter for struct literals but keep it tidy.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib config::regime_gate_config_tests -- --nocapture`
Expected: PASS (2 tests).

Also run the full trend test module to confirm `base_test_config` compiles:
Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib strategy::trend`
Expected: PASS (existing tests still green).

- [ ] **Step 6: Enable the gate in live config**

In `config/strategy.yaml`, under the `trend:` section, add:

```yaml
  regime_gate: true                 # Skip new entries when ML regime ∈ {Ranging, Danger}
  min_regime_confidence: 0.55       # tuned after Task 4 confidence-distribution check
```

(Place near the other trend tuning knobs; exact line flexible.)

- [ ] **Step 7: Validate config parses**

Run: `cargo run --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay -- --validate ETHUSDT trend 2>&1 | head -5` (or the project's config validator)
Expected: loads without a serde error. (If `--validate` means something else here, instead run `cargo test --manifest-path trading-engine-core/Cargo.toml --lib config` — all green.)

- [ ] **Step 8: Commit**

```bash
git add trading-engine-core/src/config.rs trading-engine-core/src/strategy/trend.rs config/strategy.yaml
git commit -m "feat(trend): add regime_gate + min_regime_confidence config fields"
```

---

### Task 2: Wire the gate into the trend entry path (TDD)

**Files:**
- Modify: `trading-engine-core/src/strategy/trend.rs` (import line 5; entry branch line 787; tests + helper in test module)

**Interfaces:**
- Consumes: `TrendConfig.regime_gate`, `TrendConfig.min_regime_confidence` (Task 1); `ctx.regime: Option<MarketRegime>`, `ctx.regime_confidence: f64` (existing `TickContext`).
- Produces: trend entries are blocked when `regime_gate && regime ∈ {Ranging, Danger} && confidence ≥ min_regime_confidence`. No effect on exits.

**Key location:** the entry branch at `trend.rs:787`:
```rust
if self.position.is_none() && !ctx.replay && !self.entries_suppressed {
```

- [ ] **Step 1: Write the failing tests**

In `trading-engine-core/src/strategy/trend.rs` test module, first add a helper after `tick_at_replay` (~line 1346):

```rust
    fn tick_at_regime(price: f64, regime: MarketRegime, conf: f64) -> TickContext {
        let mut c = tick_at(price);
        c.regime = Some(regime);
        c.regime_confidence = conf;
        c
    }
```

Then add these tests (after `set_paused_suppresses_entry_that_live_tick_takes` ~line 1620):

```rust
    #[test]
    fn regime_gate_off_allows_entry_regardless_of_regime() {
        let last = 100.0 * 1.005_f64.powi(259);
        let is_entry = |o: &[OrderRequest]| o.iter().any(|x| !x.reduce_only);
        let mut s = uptrend_strategy();
        s.config.regime_gate = false; // explicit
        let orders = run_tick(&mut s, tick_at_regime(last, MarketRegime::Ranging, 0.95));
        assert!(is_entry(&orders), "gate OFF → entry taken even in high-conf Ranging");
    }

    #[test]
    fn regime_gate_trending_allows_entry() {
        let last = 100.0 * 1.005_f64.powi(259);
        let is_entry = |o: &[OrderRequest]| o.iter().any(|x| !x.reduce_only);
        let mut s = uptrend_strategy();
        s.config.regime_gate = true;
        let orders = run_tick(&mut s, tick_at_regime(last, MarketRegime::Trending, 0.9));
        assert!(is_entry(&orders), "Trending is the trend-follower's regime → enter");
    }

    #[test]
    fn regime_gate_ranging_high_conf_blocks_entry() {
        let last = 100.0 * 1.005_f64.powi(259);
        let is_entry = |o: &[OrderRequest]| o.iter().any(|x| !x.reduce_only);
        let mut s = uptrend_strategy();
        s.config.regime_gate = true;
        s.config.min_regime_confidence = 0.55;
        let orders = run_tick(&mut s, tick_at_regime(last, MarketRegime::Ranging, 0.8));
        assert!(!is_entry(&orders), "high-conf Ranging must block new entry");
    }

    #[test]
    fn regime_gate_ranging_low_conf_allows_entry() {
        let last = 100.0 * 1.005_f64.powi(259);
        let is_entry = |o: &[OrderRequest]| o.iter().any(|x| !x.reduce_only);
        let mut s = uptrend_strategy();
        s.config.regime_gate = true;
        s.config.min_regime_confidence = 0.55;
        // 0.4 < 0.55 → don't trust the label → TA decides → entry
        let orders = run_tick(&mut s, tick_at_regime(last, MarketRegime::Ranging, 0.4));
        assert!(is_entry(&orders), "low-conf Ranging falls back to TA → entry");
    }

    #[test]
    fn regime_gate_danger_high_conf_blocks_entry() {
        let last = 100.0 * 1.005_f64.powi(259);
        let is_entry = |o: &[OrderRequest]| o.iter().any(|x| !x.reduce_only);
        let mut s = uptrend_strategy();
        s.config.regime_gate = true;
        s.config.min_regime_confidence = 0.55;
        let orders = run_tick(&mut s, tick_at_regime(last, MarketRegime::Danger, 0.7));
        assert!(!is_entry(&orders), "high-conf Danger must block new entry");
    }

    #[test]
    fn regime_gate_none_regime_allows_entry() {
        // tick_at() → regime: None (the replay-without-file / pre-ML path)
        let last = 100.0 * 1.005_f64.powi(259);
        let is_entry = |o: &[OrderRequest]| o.iter().any(|x| !x.reduce_only);
        let mut s = uptrend_strategy();
        s.config.regime_gate = true;
        let orders = run_tick(&mut s, tick_at(last));
        assert!(is_entry(&orders), "regime=None → TA decides → entry (back-compat)");
    }

    #[test]
    fn regime_gate_does_not_suppress_management_of_open_position() {
        // Gate blocks NEW entries only. An open position must keep being managed:
        // no new non-reduce-only order appears, and the position is not closed.
        let last = 100.0 * 1.005_f64.powi(259);
        let mut s = uptrend_strategy();
        s.config.regime_gate = true;
        s.config.min_regime_confidence = 0.55;
        let stop = s.calculate_stop_loss(100.0, OrderSide::Buy);
        s.position = Some(TrendPosition {
            side: OrderSide::Buy, entry_price: 100.0, stop_loss: stop,
            quantity: 2.0, remaining_qty: 2.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        let orders = run_tick(&mut s, tick_at_regime(last, MarketRegime::Ranging, 0.8));
        assert!(orders.iter().all(|o| o.reduce_only),
            "no new (non-reduce-only) entry while regime blocks");
        assert!(s.position.is_some(),
            "open position is not closed just because regime is Ranging");
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib strategy::trend::tests::regime_gate -- --nocapture`
Expected: FAIL — the 3 "blocks entry" cases fail (gate not implemented yet; entries currently fire regardless of regime). The "allows" cases may already pass (correct — they assert back-compat). `MarketRegime` import may also fail to resolve (added in Step 3).

- [ ] **Step 3: Add `MarketRegime` to the import**

In `trading-engine-core/src/strategy/trend.rs:5`, change:

```rust
use crate::strategy::{Strategy, TickContext, StrategyStatus};
```
to:
```rust
use crate::strategy::{Strategy, TickContext, StrategyStatus, MarketRegime};
```

(`MarketRegime` is defined in `strategy/mod.rs:40` and used the same way in `engine.rs:13`.)

- [ ] **Step 4: Insert the gate at the entry branch**

In `trading-engine-core/src/strategy/trend.rs`, replace the entry-branch condition at line 787. Before:

```rust
        if self.position.is_none() && !ctx.replay && !self.entries_suppressed {
```

After:

```rust
        // ML regime gate: skip NEW entries in Ranging/Danger at sufficient
        // confidence. Entries only — the position-management path below this
        // block continues to run (exits, trailing, TP ladder). regime=None
        // (replay without --regime-file, or pre-ML) → TA decides, back-compat.
        let entry_blocked_by_regime = self.config.regime_gate
            && matches!(
                ctx.regime,
                Some(MarketRegime::Ranging) | Some(MarketRegime::Danger)
            )
            && ctx.regime_confidence >= self.config.min_regime_confidence;

        if self.position.is_none() && !ctx.replay && !self.entries_suppressed
            && !entry_blocked_by_regime {
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib strategy::trend::tests::regime_gate -- --nocapture`
Expected: PASS (7 tests).

Run the whole trend module to confirm no regression:
Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib strategy::trend`
Expected: PASS (all pre-existing tests still green).

- [ ] **Step 6: Commit**

```bash
git add trading-engine-core/src/strategy/trend.rs
git commit -m "feat(trend): gate new entries on ML regime (Ranging/Danger), entries-only"
```

---

### Task 3: Make the replay backtest regime-aware

**Files:**
- Modify: `trading-engine-core/src/backtest/replay.rs` (add `RegimeTimeline`; thread through `build_ctx_from`, `run_loop`, `run_engine_on_bars`, `ReplayConfig`)
- Modify: `trading-engine-core/src/bin/backtest_replay.rs` (add `--regime-file`, `--start`, `--end` flags)

**Interfaces:**
- Consumes: `MarketRegime` (from `crate::strategy`); JSON regime timeline `{PAIR: [{ts, regime, confidence}, ...]}`.
- Produces: `ReplayConfig.regime: Option<RegimeTimeline>`; `TickContext.regime` populated from the timeline at each bar's timestamp when a file is supplied.

**Key locations:**
- `build_ctx_from(...)` builds `TickContext` at ~line 432; hardcodes `regime: None` at line 461.
- `run_loop(...)` at line 311; calls `build_ctx_from` at line 373 with `/*replay*/ i < warmup_bars`.
- `run_engine_on_bars(...)` at line 139; calls `run_loop` for each engine kind.
- `ReplayConfig` struct at line 86.

- [ ] **Step 1: Write failing tests for `RegimeTimeline`**

Add a test module at the end of `trading-engine-core/src/backtest/replay.rs`:

```rust
#[cfg(test)]
mod regime_timeline_tests {
    use super::RegimeTimeline;

    #[test]
    fn get_returns_most_recent_label_at_or_before_ts() {
        // Pair keys normalize: "ETHUSDT" and "ETH-USDT" are the same timeline.
        let json = r#"{"ETH-USDT": [
            {"ts": 1000, "regime": 1, "confidence": 0.6},
            {"ts": 2000, "regime": 0, "confidence": 0.8},
            {"ts": 3000, "regime": 0, "confidence": 0.7}
        ]}"#;
        let tl = RegimeTimeline::from_json_str(json).unwrap();
        // Before first label → None
        assert_eq!(tl.get("ETHUSDT", 999), None);
        // Exactly at a label → that label
        assert_eq!(tl.get("ETHUSDT", 2000), Some((0, 0.8)));
        // Between labels → most recent at-or-before
        assert_eq!(tl.get("ETH-USDT", 2500), Some((0, 0.8)));
        // After last label → last label (regime persists until updated, matches live TTL)
        assert_eq!(tl.get("ETHUSDT", 9999), Some((0, 0.7)));
        // Unknown pair → None
        assert_eq!(tl.get("BTCUSDT", 2000), None);
    }

    #[test]
    fn empty_timeline_is_none() {
        let tl = RegimeTimeline::default();
        assert_eq!(tl.get("ETHUSDT", 1000), None);
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib backtest::replay::regime_timeline_tests -- --nocapture`
Expected: FAIL — `RegimeTimeline` not defined (compile error).

- [ ] **Step 3: Implement `RegimeTimeline`**

Near the top of `trading-engine-core/src/backtest/replay.rs` (after the imports / before `ReplayConfig`), add:

```rust
use crate::strategy::MarketRegime;
use serde::Deserialize;

/// Per-pair regime label timeline for replay injection. Pairs normalize by
/// uppercasing and stripping '-', so "ETHUSDT" (backtest symbol) and
/// "ETH-USDT" (regime-pusher key) resolve to the same timeline.
#[derive(Debug, Clone, Default)]
pub struct RegimeTimeline {
    map: std::collections::HashMap<String, Vec<(i64, i32, f64)>>, // pair -> sorted (ts_ms, regime, confidence)
}

#[derive(Deserialize)]
struct RegimeEntry { ts: i64, regime: i32, confidence: f64 }

fn norm_pair(p: &str) -> String {
    p.to_uppercase().replace('-', "")
}

impl RegimeTimeline {
    pub fn from_json_str(s: &str) -> anyhow::Result<Self> {
        let raw: std::collections::HashMap<String, Vec<RegimeEntry>> = serde_json::from_str(s)?;
        let mut map = std::collections::HashMap::new();
        for (pair, mut entries) in raw {
            entries.sort_by_key(|e| e.ts);
            let v: Vec<(i64, i32, f64)> = entries.into_iter().map(|e| (e.ts, e.regime, e.confidence)).collect();
            map.insert(norm_pair(&pair), v);
        }
        Ok(Self { map })
    }

    pub fn from_json_file(path: &std::path::Path) -> anyhow::Result<Self> {
        let s = std::fs::read_to_string(path)?;
        Self::from_json_str(&s)
    }

    /// Most-recent label with ts ≤ ts_ms (regime persists until updated).
    pub fn get(&self, pair: &str, ts_ms: i64) -> Option<(i32, f64)> {
        let v = self.map.get(&norm_pair(pair))?;
        v.iter().rev().find(|(t, _, _)| *t <= ts_ms).map(|(_, r, c)| (*r, *c))
    }

    /// Map a raw int label to MarketRegime (0=Ranging, 1=Trending, else Danger).
    pub fn to_market_regime(label: Option<(i32, f64)>) -> (Option<MarketRegime>, f64) {
        match label {
            Some((0, c)) => (Some(MarketRegime::Ranging), c),
            Some((1, c)) => (Some(MarketRegime::Trending), c),
            Some((_, c)) => (Some(MarketRegime::Danger), c),
            None => (None, 0.0),
        }
    }
}
```

- [ ] **Step 4: Run timeline tests to verify they pass**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --lib backtest::replay::regime_timeline_tests -- --nocapture`
Expected: PASS (2 tests).

- [ ] **Step 5: Add `regime` to `ReplayConfig`**

In `trading-engine-core/src/backtest/replay.rs`, add a field to `pub struct ReplayConfig` (line 86):

```rust
    /// Optional ML regime timeline. When set, each bar's TickContext gets the
    /// regime label active at bar.timestamp (most-recent at-or-before). None
    /// → regime=None (current back-compat behavior).
    pub regime: Option<RegimeTimeline>,
```

- [ ] **Step 6: Thread regime through `run_loop` → `build_ctx_from`**

Change `build_ctx_from` signature (line ~432) to accept the resolved label and inject it. Replace the `regime: None,` / `regime_confidence: 0.0,` lines inside the `TickContext { ... }` literal (line 461-462). New signature + body around the TickContext literal:

```rust
fn build_ctx_from(
    symbol: &str,
    bar: &Bar,
    prior: &[Bar],
    capital: &CapitalManager,
    replay: bool,
    regime: Option<(i32, f64)>,
) -> TickContext {
    // ... (recent / ob / balances unchanged) ...
    let (regime, regime_confidence) = RegimeTimeline::to_market_regime(regime);
    TickContext {
        order_book: ob,
        recent_bars: recent,
        balances,
        open_orders: vec![],
        regime,
        regime_confidence,
        timestamp: bar.timestamp,
        capital: Some(capital.clone()),
        replay,
    }
}
```

(Only the signature line and the two `regime`/`regime_confidence` literal lines change; leave `recent`, `ob`, `balances` blocks untouched.)

In `run_loop` (line 311), add a `regime` parameter and resolve it per bar. Change the signature:

```rust
pub async fn run_loop(
    strategy: &mut dyn Strategy,
    sim: &mut FillSim,
    port: &mut Portfolio,
    capital: &CapitalManager,
    bars: &[Bar],
    warmup_bars: usize,
    _bar_hours: f64,
    perp: Option<&HistoricalPerpSource>,
    regime: Option<&RegimeTimeline>,
) -> anyhow::Result<RunResult> {
```

And change the `build_ctx_from` call site (line 373):

```rust
        let regime_label = regime.and_then(|t| t.get(symbol_of(strategy), bar.timestamp));
        let ctx = build_ctx_from(symbol_of(strategy), bar, &bars[..i], capital, i < warmup_bars, regime_label);
```

- [ ] **Step 7: Update `run_engine_on_bars` to pass regime through**

In `trading-engine-core/src/backtest/replay.rs:139`, `run_engine_on_bars` calls `run_loop` for each engine kind. Each `run_loop(...)` call (Grid, Trend, Swing, MeanReversion branches) must get a trailing `rc.regime.as_ref()` argument. For every `run_loop(` invocation inside `run_engine_on_bars`, add:

```rust
            rc.regime.as_ref(),
```

as the final argument (after the `None` perp arg for non-trend, or after the trend perp arg). Example for the Grid branch:

```rust
            Ok(run_loop(
                &mut grid, &mut sim, &mut port, &capital, &bars,
                rc.warmup_bars, rc.bar_hours, None,
                rc.regime.as_ref(),
            ).await?)
```

Apply the same trailing `rc.regime.as_ref(),` to the Trend, Swing, and MeanReversion `run_loop` calls in this function.

- [ ] **Step 8: Build to confirm it compiles**

Run: `cargo build --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay`
Expected: compiles. If a `run_loop` call site outside `run_engine_on_bars` exists (grep `run_loop(`), update it too — pass `None` for regime where a standalone caller doesn't have a timeline.

- [ ] **Step 9: Add `--regime-file`, `--start`, `--end` flags to the CLI**

In `trading-engine-core/src/bin/backtest_replay.rs`, add three new flag-handling branches in the arg-parsing `while` loop (near the `--config` branch) and three new `let mut` declarations:

Declarations (near `let mut cfg_path`):
```rust
    let mut regime_file: Option<String> = None;
    let mut start_override: Option<String> = None;
    let mut end_override: Option<String> = None;
```

Parsing branches (inside the `while let Some(a) = args.next()` loop, alongside `--config`):
```rust
        } else if a == "--regime-file" {
            regime_file = Some(args.next().ok_or_else(|| anyhow::anyhow!("--regime-file requires a value"))?);
        } else if a == "--start" {
            start_override = Some(args.next().ok_or_else(|| anyhow::anyhow!("--start requires a value (YYYY-MM-DD)"))?);
        } else if a == "--end" {
            end_override = Some(args.next().ok_or_else(|| anyhow::anyhow!("--end requires a value (YYYY-MM-DD)"))?);
        } else if a.starts_with("--") {
```

Then replace the `end`/`start` computation (the `let end = chrono::Utc::now()...; let start = end - ...;` block) with:

```rust
    let parse_day = |s: &str| -> anyhow::Result<chrono::NaiveDate> {
        chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d")
            .map_err(|e| anyhow::anyhow!("bad date '{}': {}", s, e))
    };
    let end = match &end_override { Some(s) => parse_day(s)?, None => chrono::Utc::now().date_naive() };
    let start = match &start_override {
        Some(s) => parse_day(s)?,
        None => end - chrono::Duration::days(30 * months as i64),
    };
```

Load the timeline after `bars` are loaded, and put it on `rc`. After the `println!("{} bars loaded", bars.len());` line:

```rust
    let regime = match &regime_file {
        Some(p) => {
            let tl = trading_engine_core::backtest::replay::RegimeTimeline::from_json_file(
                std::path::Path::new(p))?;
            println!("Loaded regime timeline from {}", p);
            Some(tl)
        }
        None => None,
    };
```

And add `regime,` to the `let rc = ReplayConfig { ... }` literal (matching the field added in Step 5).

- [ ] **Step 10: Build the CLI**

Run: `cargo build --manifest-path trading-engine-core/Cargo.toml --release --bin backtest_replay`
Expected: compiles cleanly.

- [ ] **Step 11: Smoke-test the CLI flag parses (no network needed for --help-style check)**

Run: `cargo run --manifest-path trading-engine-core/Cargo.toml --release --bin backtest_replay -- ETHUSDT 1 trend --start 2026-07-04 --end 2026-07-15 2>&1 | head -3`
Expected: prints `Loading ETHUSDT 1h bars 2026-07-04 → 2026-07-15 (engine=Trend) ...` (confirms flags parse + date override works). It will then try to fetch bars (needs network); reaching the "bars loaded" line confirms the pipeline.

- [ ] **Step 12: Commit**

```bash
git add trading-engine-core/src/backtest/replay.rs trading-engine-core/src/bin/backtest_replay.rs
git commit -m "feat(backtest): inject ML regime into replay via --regime-file (+--start/--end)"
```

---

### Task 4: Backfill ETH regime labels for the losing window

**Files:**
- Create: `src/ml/regime_labels_backfill.py`

**Interfaces:**
- Consumes: `src.ml.regime_pusher.compute_regime`, `src.ml.regime_pusher.load_models` (the live pipeline — zero skew); Binance public klines REST.
- Produces: `backtest/results/eth_regime_jul4-14.json` — `{PAIR: [{ts, regime, confidence}, ...]}`, one label per closed 1h bar, no lookahead (each label computed from bars up to and including that bar).

- [ ] **Step 1: Write the script**

Create `src/ml/regime_labels_backfill.py`:

```python
"""Backfill ML regime labels for a pair over a historical window.

Reuses the LIVE regime-pusher pipeline verbatim (calculate_technical_features +
RegimeClassifier) so labels match what regime-pusher would have produced —
zero train/serve skew. Output is the JSON timeline consumed by
`backtest_replay --regime-file`.

One label per CLOSED 1h bar. Each label is computed from bars up to and
including that bar (no lookahead), matching how the live pusher predicts on the
latest closed bar.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import requests

from src.ml.regime_pusher import compute_regime, load_models

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"


def fetch_history(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Paginated Binance public klines → OHLCV DF indexed by timestamp.

    Column shape matches regime_pusher.fetch_klines output
    ([open, high, low, close, volume], tz-aware timestamp index) so compute_regime
    consumes it unchanged.
    """
    frames: list[list] = []
    cur = start_ms
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cur, "endTime": end_ms, "limit": 1000}
        resp = requests.get(BINANCE_KLINES, params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        frames.append(rows)
        cur = rows[-1][0] + 1  # open_time ms
        if len(rows) < 1000:
            break
    raw = [r for f in frames for r in f]
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbav", "tqav", "ignore"]
    df = pd.DataFrame(raw, columns=cols)
    df["timestamp"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    # Warmup: the RF feature path (calculate_technical_features) drops NaN rows;
    # mirror the production ≥500-bar context by skipping the first 500 bars.
    return df[["open", "high", "low", "close", "volume"]]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pair", default="ETH-USDT", help="regime-pusher pair key, e.g. ETH-USDT")
    ap.add_argument("--symbol", default="ETHUSDT", help="Binance symbol, e.g. ETHUSDT")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--model-dir", default="models")
    ap.add_argument("--out", required=True)
    ap.add_argument("--warmup", type=int, default=500)
    args = ap.parse_args()

    start_ms = int(pd.Timestamp(args.start, tz="UTC").timestamp() * 1000)
    # Fetch extra warmup bars before the window so the first in-window label has full context.
    fetch_start = start_ms - args.warmup * 3600 * 1000
    end_ms = int(pd.Timestamp(args.end, tz="UTC").timestamp() * 1000)

    print(f"Fetching {args.symbol} {args.interval} bars {args.start} → {args.end} (warmup {args.warmup})...")
    df = fetch_history(args.symbol, args.interval, fetch_start, end_ms)
    print(f"  {len(df)} bars fetched")

    models = load_models([args.pair], args.model_dir)
    if args.pair not in models:
        raise SystemExit(f"No clean model for {args.pair} in {args.model_dir}")
    clf = models[args.pair]

    # Emit one label per bar whose timestamp is inside [start, end). Each label
    # uses only bars up to and including that bar (iloc[:i+1]) → no lookahead.
    timeline = []
    confs = []
    for i in range(len(df)):
        ts = df.index[i]
        if ts < pd.Timestamp(args.start, tz="UTC") or ts >= pd.Timestamp(args.end, tz="UTC"):
            continue
        res = compute_regime(df.iloc[: i + 1], clf)
        if res is None:
            continue
        regime, conf = res
        timeline.append({"ts": int(ts.timestamp() * 1000), "regime": int(regime), "confidence": float(conf)})
        confs.append(conf)

    out = {args.pair: timeline}
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out))
    print(f"Wrote {len(timeline)} labels to {args.out}")
    if confs:
        s = pd.Series(confs)
        print(f"Confidence distribution: min={s.min():.2f} p25={s.quantile(.25):.2f} "
              f"median={s.median():.2f} p75={s.quantile(.75):.2f} max={s.max():.2f}")
        from collections import Counter
        c = Counter(t["regime"] for t in timeline)
        print(f"Regime counts (0=Ranging,1=Trending,2=Danger): {dict(c)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run the script to generate ETH labels**

Run (from repo root, with the project's Python env — `python -m src.ml.regime_labels_backfill`):
```bash
python -m src.ml.regime_labels_backfill \
  --pair ETH-USDT --symbol ETHUSDT \
  --start 2026-07-04 --end 2026-07-15 \
  --out backtest/results/eth_regime_jul4-14.json
```
Expected: prints bar count, writes N labels (~264 = 11 days × 24h), and prints the confidence distribution + regime counts. **Inspect the regime counts**: if the window is overwhelmingly `0=Ranging`, that confirms the hypothesis (the gate would have fired). If it's mostly `1=Trending`, the gate would NOT have helped — report this honestly (see Task 5 failure outcome).

If `min_regime_confidence` 0.55 sits above most confidences (e.g. median < 0.55), note it and consider lowering the threshold before Task 5 — the gate only fires on labels at/above the threshold.

- [ ] **Step 3: Commit**

```bash
git add src/ml/regime_labels_backfill.py backtest/results/eth_regime_jul4-14.json
git commit -m "feat(ml): ETH regime-label backfill for replay proof (reuses live pipeline)"
```

---

### Task 5: Run the gated-vs-ungated replay proof

**Files:**
- Create: `docs/study/trend_regime_gate_replay_proof.md`

**Interfaces:** Consumes the binary from Task 3 + labels from Task 4.

**Method:** Both runs are identical trend-only clean sims over the identical window. The ungated run never blocks; the gated run blocks entries where the timeline says Ranging/Danger ≥ threshold. The **P&L delta isolates the gate's effect**. (Note: the replay sim will not exactly reproduce the live realized loss — live had circuit-breaker trips, restarts, MR, and signal-engine interactions the clean trend sim doesn't model. The baseline is a directional sanity check, not an exact match; the gated-vs-ungated delta is the rigorous comparison.)

- [ ] **Step 1: Run (a) ungated baseline**

```bash
cargo run --manifest-path trading-engine-core/Cargo.toml --release --bin backtest_replay -- \
  ETHUSDT 1 trend --start 2026-07-04 --end 2026-07-15 \
  --config config/strategy.yaml 2>&1 | tee /tmp/replay_a.txt
```
Then temporarily disable the gate for this run. The cleanest way: pass a config override. Since the CLI loads `config/strategy.yaml` (where Task 1 set `regime_gate: true`), create a one-line variant for the ungated baseline:

```bash
cp config/strategy.yaml /tmp/strategy_ungated.yaml
# edit /tmp/strategy_ungated.yaml: set trend.regime_gate: false
cargo run --manifest-path trading-engine-core/Cargo.toml --release --bin backtest_replay -- \
  ETHUSDT 1 trend --start 2026-07-04 --end 2026-07-15 \
  --config /tmp/strategy_ungated.yaml 2>&1 | tee /tmp/replay_a_ungated.txt
```
Record: `total_return_pct`, `total_trades`, `max_drawdown_pct`, `win_rate_pct` (printed by the CLI).

- [ ] **Step 2: Run (b) labels-only (gate OFF + regime file) — sanity**

```bash
cargo run --manifest-path trading-engine-core/Cargo.toml --release --bin backtest_replay -- \
  ETHUSDT 1 trend --start 2026-07-04 --end 2026-07-15 \
  --config /tmp/strategy_ungated.yaml \
  --regime-file backtest/results/eth_regime_jul4-14.json 2>&1 | tee /tmp/replay_b.txt
```
Expected: ≈ run (a) — labels don't change behavior when the gate is off (regime is read but not acted on). Confirms the regime injection is non-mutating when gate is off.

- [ ] **Step 3: Run (c) gated + regime file — the fix**

```bash
cargo run --manifest-path trading-engine-core/Cargo.toml --release --bin backtest_replay -- \
  ETHUSDT 1 trend --start 2026-07-04 --end 2026-07-15 \
  --config config/strategy.yaml \
  --regime-file backtest/results/eth_regime_jul4-14.json 2>&1 | tee /tmp/replay_c.txt
```
Record the same metrics. Compute the delta vs (a)/(b).

- [ ] **Step 4: Write the proof report**

Create `docs/study/trend_regime_gate_replay_proof.md` with:
- The three runs' metrics in a table (return %, trades, max DD, win %).
- The P&L delta attributable to the gate.
- The regime-count breakdown from Task 4 (how many bars were Ranging/Trending/Danger, and at what confidence).
- **Verdict** against the spec's success bar: did the gate materially cut the loss AND were skipped trades genuinely in Ranging windows (selective, not blanket suppression)?
- If the gate did NOT help (window was mostly Trending, or threshold too high so the gate rarely fired), state that honestly — it means the regime model isn't yet good enough to protect trend on ETH, and the go-live decision must account for it.

- [ ] **Step 5: Commit**

```bash
git add docs/study/trend_regime_gate_replay_proof.md
git commit -m "docs(proof): trend regime-gate replay results (gated vs ungated, ETH Jul 4-14)"
```

---

### Task 6: Update docs + memory

**Files:**
- Modify: `trading-engine-core/src/backtest/report.rs:156, 227` (the "regime=None optimistic" caveat)
- Modify: `trading-engine-core/src/bin/backtest_replay.rs:24` (the `//! regime = None` doc comment)
- Update: memory file `trend_regime_gate_not_wired.md` + MEMORY.md pointer (flip status to fixed-if-proven)

- [ ] **Step 1: Update the report caveat**

In `trading-engine-core/src/backtest/report.rs:156` and `:227`, change the caveat text from "regime=None — grid/trend ML regime gate is OFF (optimistic; live uses ML regime)" to note that regime IS injected when `--regime-file` is supplied, and only None (optimistic) when omitted.

In `backtest_replay.rs:24` (`* \`regime = None\` in TickContext — replay does not synthesize ML regime.`), append: "Override with `--regime-file <path>` to inject a per-pair regime timeline."

- [ ] **Step 2: Update memory**

Edit `/Users/amro/.claude/projects/-Users-amro-WebstormProjects-trading-humming-bot/memory/trend_regime_gate_not_wired.md`: change the header to reflect FIXED status with the proof outcome (paste the verdict from Task 5). Update the MEMORY.md one-liner similarly.

- [ ] **Step 3: Commit**

```bash
git add trading-engine-core/src/backtest/report.rs trading-engine-core/src/bin/backtest_replay.rs
git commit -m "docs(backtest): note --regime-file regime injection (retires regime=None caveat)"
```

---

## Self-Review (completed)

**1. Spec coverage:** Every spec section maps to a task — Fix 1 (gate) = Tasks 1–2; Fix 2 (replay-aware) = Task 3; Fix 3 (label backfill) = Task 4; Proof = Task 5; docs = Task 6. The "entries-only / management unaffected" requirement is pinned by Task 2's `regime_gate_does_not_suppress_management_of_open_position` test. The pair-name mismatch (ETHUSDT vs ETH-USDT) is handled by `norm_pair` in Task 3.

**2. Placeholder scan:** No TBD/TODO. The 0.55 threshold is explicitly revisited in Task 4 Step 2 (inspect distribution) and tunable in config without code change. All code blocks are complete.

**3. Type consistency:** `RegimeTimeline::get` returns `Option<(i32, f64)>` in Task 3 Step 3, consumed identically in `run_loop` (Step 6) and mapped via `to_market_regime` in `build_ctx_from` (Step 6). `TrendConfig.regime_gate`/`min_regime_confidence` field names are identical in Task 1 (config) and Task 2 (tests + gate logic). `MarketRegime::Ranging|Trending|Danger` variants match `strategy/mod.rs:40` and `engine.rs:250`.

## Execution Handoff

Plan saved to `docs/superpowers/plans/2026-07-18-trend-regime-gate.md`. Two execution options:

1. **Subagent-driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration with clean context per task.
2. **Inline execution** — I execute tasks in this session with checkpoints between tasks for your review.

Which approach?
