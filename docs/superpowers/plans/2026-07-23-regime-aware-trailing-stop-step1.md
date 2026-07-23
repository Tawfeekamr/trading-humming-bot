# Regime-Aware Trailing Stop — Step 1 (fix wiring + tighten) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the trend engine's Chandelier trailing stop read the yaml knob the operator actually edits (`trailing_stop_atr_mult`), delete the three dead config fields that caused a month of silent mis-tuning, tighten the live trail from an effective 3.0×ATR to 2.0×ATR, and harden against a future zero/missing field.

**Architecture:** Step 1 of a two-phase change (spec: `docs/superpowers/specs/2026-07-23-regime-aware-trailing-stop-design.md`). Step 1 is pure Rust config/logic — no ML. The Chandelier multiplier source moves from the unread `atr_trailing_mult` (serde default 3.0) to the yaml-set `trailing_stop_atr_mult`; the three dead fields (`atr_trailing_mult`, `trailing_stop_pct`, `trailing_activation_pct`) are removed everywhere the compiler flags; a `>0` guard is added to `AppConfig::load` so boot + CI reject a bad value. Step 2 (regime-aware multiplier) is a separate plan, gated on Step 1's one-week validation.

**Tech Stack:** Rust (edition per `trading-engine-core/Cargo.toml`), `serde_yaml`, `anyhow`, `tokio` (tests). Tests via `cargo test -p trading-engine-core`. CI gate: `cargo test` + `validate_config`.

## Global Constraints

- **Scope: trend engine only.** Do not modify grid / swing / mean-reversion exit logic. They use different fields (`atr_stop_mult`, `band_atr_mult`) — leave those alone.
- **Do not delete shared default fns.** `default_3` is still used by `RiskConfig` (`config.rs:345, 357`); `default_1_5` by `GridConfig` (`config.rs:169`) and `SwingConfig` (`516, 522`). Removing the trend fields does **not** make these unused — keep them.
- **Keep `trailing_stop_atr_mult` and its serde default `default_2_5`** (`config.rs:234-235, 291`) — this becomes the single live knob.
- **Final trail value: 2.0×ATR** (down from effective 3.0). Set in `config/strategy.yaml`.
- Every commit ends with `Co-Authored-By: Claude <noreply@anthropic.com>`.

---

## File Structure

- **Modify** `trading-engine-core/src/strategy/trend.rs` — line 703 (knob source); lines 183/185/195 (drop dead fields from the `TrendConfig` literal in `new()`); lines 1420-1421/1424 (drop from `base_test_config`).
- **Modify** `trading-engine-core/src/config.rs` — remove 3 dead fields from `TrendConfig` (232-233, 236-237, 255-257); add `impl TrendConfig { fn validate() }` and call it from `AppConfig::load` (395-399); add a unit test.
- **Modify** `config/strategy.yaml` — `trailing_stop_atr_mult: 2.5 → 2.0` (line 170); delete dead `trailing_activation_pct` line (171).
- **Modify** `trading-engine-core/tests/test_trend_exits.rs` — add the knob-binding test; drop dead fields from `default_trend_config()` (23/25/35).
- **Modify** `trading-engine-core/tests/test_trend_strategy.rs` and `tests/test_adx_discrepancy.rs` — drop dead fields from their `TrendConfig` literals (compiler-enforced).

No new files in Step 1.

---

### Task 1: Bind the Chandelier knob to `trailing_stop_atr_mult`

**Files:**
- Modify: `trading-engine-core/src/strategy/trend.rs:703`
- Test: `trading-engine-core/tests/test_trend_exits.rs` (append one test)

**Interfaces:**
- Consumes: `TrendConfig.trailing_stop_atr_mult` (`config.rs:234-235`, serde default `default_2_5` = 2.5).
- Produces: the Chandelier trail at `trend.rs:703` now tracks `trailing_stop_atr_mult`. (The old `atr_trailing_mult` becomes unread — it is removed in Task 3.)

- [ ] **Step 1: Write the failing test**

Append to `trading-engine-core/tests/test_trend_exits.rs`:

```rust
/// Step 1: the Chandelier trail MUST read `trailing_stop_atr_mult` (not the old
/// unread `atr_trailing_mult` default). Two configs differing ONLY in that field
/// must produce different trails — proving the knob binds. Before the fix the
/// code reads `atr_trailing_mult` (=3.0 in default_trend_config) for both, so the
/// trails are identical and `tight > loose` fails.
#[tokio::test]
async fn test_trailing_stop_binds_to_atr_mult_field() {
    async fn trail_for(mult: f64) -> f64 {
        let mut cfg = default_trend_config();
        cfg.trailing_stop_atr_mult = mult;
        let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
        let mut s = TrendStrategy::new("BTCUSDT", &cfg, telegram);
        warmup(&mut s, 50000.0);
        enter_position(&mut s, 50000.0, 0.1).await;
        let mut bars = Vec::new();
        for p in [50500.0, 51000.0, 51500.0, 52000.0] {
            s.update_indicators(&make_bar(p));
            s.on_tick(&make_tick(p, &mut bars)).await.unwrap();
        }
        s.position().unwrap().trailing_stop.expect("trail set after up-move")
    }

    // smaller mult ⇒ higher (tighter) trail for a long; larger mult ⇒ lower (looser)
    let tight = trail_for(2.0).await;
    let loose = trail_for(4.0).await;
    assert!(tight > loose,
        "tighter mult must produce a higher trail for a long: tight={} loose={}", tight, loose);
    // trail = highest - mult·ATR with identical highest/ATR ⇒ gap = (4-2)·ATR ≫ 0
    assert!(tight - loose > 1.0,
        "trail gap must reflect Δmult·ATR (ATR>0 in this setup), got {}", tight - loose);
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p trading-engine-core --test test_trend_exits test_trailing_stop_binds_to_atr_mult_field`
Expected: **FAIL** — `tight > loose` assertion fails (both trails equal because the code reads `atr_trailing_mult=3.0`, ignoring the overridden `trailing_stop_atr_mult`).

- [ ] **Step 3: Write minimal implementation**

In `trading-engine-core/src/strategy/trend.rs:703`, replace:

```rust
            let atr_mult = if self.config.atr_trailing_mult > 0.0 { self.config.atr_trailing_mult } else { 3.0 };
```

with:

```rust
            // Chandelier multiplier: the one knob the operator edits in the yaml.
            // Fallback 2.0 only binds if the field is missing/zero; Task 4 makes
            // AppConfig::load reject <= 0 so this never silently falls back in prod.
            let atr_mult = if self.config.trailing_stop_atr_mult > 0.0 {
                self.config.trailing_stop_atr_mult
            } else {
                2.0
            };
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p trading-engine-core --test test_trend_exits test_trailing_stop_binds_to_atr_mult_field`
Expected: **PASS**.

Then run the full trend exit suite to confirm no regression:
Run: `cargo test -p trading-engine-core --test test_trend_exits`
Expected: all tests PASS (the existing `test_trailing_stop_chandelier_exit` still passes — it asserts a sell fires on a big drop, which holds for any positive mult).

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/strategy/trend.rs trading-engine-core/tests/test_trend_exits.rs
git commit -m "fix(trend): Chandelier trail reads trailing_stop_atr_mult (was dead atr_trailing_mult default 3.0)" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 2: Set the multiplier to 2.0 in the production yaml

**Files:**
- Modify: `config/strategy.yaml:170`

**Interfaces:** none (config value only).

- [ ] **Step 1: Edit the yaml value**

In `config/strategy.yaml`, line 170, change:

```yaml
  trailing_stop_atr_mult: 2.5   # ATR-based Chandelier Exit
```

to:

```yaml
  trailing_stop_atr_mult: 2.0   # Chandelier Exit multiplier. WAS 2.5 (dead field,
                                # # never read); live trail was actually 3.0 via the
                                # # atr_trailing_mult serde default. Tightened to 2.0.
```

(Leave the dead `trailing_activation_pct` line (171) in place for now — Task 3 removes it together with the field deletion so the yaml and the struct stay consistent in one commit.)

- [ ] **Step 2: Verify config still parses + value is live**

Run: `cargo run -p trading-engine-core --bin validate_config`
Expected: `PASS: config/strategy.yaml parsed OK` (the value change is valid; `trailing_activation_pct` is still a known field until Task 3).

Sanity-check the value is read: `cargo test -p trading-engine-core --test test_trend_exits test_trailing_stop_binds_to_atr_mult_field`
Expected: PASS (unaffected — the test sets its own values).

- [ ] **Step 3: Commit**

```bash
git add config/strategy.yaml
git commit -m "config(trend): tighten Chandelier trail 3.0 -> 2.0 ATR (effective value; was mis-tuned via dead field)" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 3: Remove the three dead fields + the dead yaml line

**Files:**
- Modify: `trading-engine-core/src/config.rs` (remove fields 232-233, 236-237, 255-257)
- Modify: `trading-engine-core/src/strategy/trend.rs` (drop from literals at 183, 185, 195, and `base_test_config` 1420-1421, 1424)
- Modify: `trading-engine-core/tests/test_trend_exits.rs` (drop from `default_trend_config` 23, 25, 35)
- Modify: `trading-engine-core/tests/test_trend_strategy.rs` and `tests/test_adx_discrepancy.rs` (drop from their `TrendConfig` literals — exact lines found by the compiler)
- Modify: `config/strategy.yaml:171` (delete the dead `trailing_activation_pct` line)

**Interfaces:**
- Consumes: Task 1's change (the knob now reads `trailing_stop_atr_mult`, so `atr_trailing_mult` is unread and safe to delete).
- Produces: `TrendConfig` has exactly one trailing knob (`trailing_stop_atr_mult`). `default_1_5` and `default_3` remain (shared — see Global Constraints).

- [ ] **Step 1: Remove the fields from `TrendConfig`**

In `trading-engine-core/src/config.rs`, delete these three field declarations (keep `trailing_stop_atr_mult`):

Before (lines 232-237 + 255-257):
```rust
    #[serde(default = "default_1_5")]
    pub trailing_stop_pct: f64,
    #[serde(default = "default_2_5")]
    pub trailing_stop_atr_mult: f64,
    #[serde(default = "default_1_5")]
    pub trailing_activation_pct: f64,
```
… and …
```rust
    #[serde(default = "default_3")]
    pub atr_trailing_mult: f64,
```

After:
```rust
    #[serde(default = "default_2_5")]
    pub trailing_stop_atr_mult: f64,
```
… (`atr_trailing_mult` block removed entirely) …

Do **not** remove `default_1_5` (`config.rs:196`) or `default_3` (`config.rs:381`) — still used by grid/swing/risk configs.

- [ ] **Step 2: Build and let the compiler list every remaining reference**

Run: `cargo build -p trading-engine-core`
Expected: **compile errors** at each `TrendConfig` literal that still sets a deleted field. Fix every one — they are:

- `src/strategy/trend.rs:183` — delete `trailing_stop_pct: config.trailing_stop_pct,`
- `src/strategy/trend.rs:185` — delete `trailing_activation_pct: config.trailing_activation_pct,`
- `src/strategy/trend.rs:195` — delete `atr_trailing_mult: config.atr_trailing_mult,`
- `src/strategy/trend.rs:1420` — change `max_position_pct: 25.0, trailing_stop_pct: 1.5, trailing_stop_atr_mult: 2.5,` → `max_position_pct: 25.0, trailing_stop_atr_mult: 2.5,`
- `src/strategy/trend.rs:1421` — change `trailing_activation_pct: 1.5, exit_signal_threshold: 2, sl_buffer_pct: 0.2,` → `exit_signal_threshold: 2, sl_buffer_pct: 0.2,`
- `src/strategy/trend.rs:1424` — change `rsi_short_min: 35.0, atr_trailing_mult: 3.0, trade_shorts: false,` → `rsi_short_min: 35.0, trade_shorts: false,`
- `tests/test_trend_exits.rs:23` — delete `trailing_stop_pct: 1.5,`
- `tests/test_trend_exits.rs:25` — delete `trailing_activation_pct: 1.5,`
- `tests/test_trend_exits.rs:35` — delete `atr_trailing_mult: 3.0,`
- `tests/test_trend_strategy.rs:21` — delete `trailing_stop_pct: 1.5,`
- `tests/test_trend_strategy.rs:23` — delete `trailing_activation_pct: 1.5,`
- `tests/test_trend_strategy.rs:33` — delete `atr_trailing_mult: 3.0,`
- `tests/test_adx_discrepancy.rs:22` — delete `trailing_stop_pct: 0.0,`
- `tests/test_adx_discrepancy.rs:24` — delete `trailing_activation_pct: 0.0,`
- `tests/test_adx_discrepancy.rs:34` — delete `atr_trailing_mult: 0.0,`

- [ ] **Step 3: Delete the dead yaml line**

In `config/strategy.yaml`, delete line 171:
```yaml
  trailing_activation_pct: 3.0  # Claude: must be > trail distance to avoid immediate stop-out
```
(No `deny_unknown_fields`, so leaving it would be silently ignored — but the field no longer exists, so remove it to stop misleading future readers. This was the "Claude: must be > trail distance" comment on a knob that did nothing.)

- [ ] **Step 4: Build + run the full suite to verify it passes**

Run: `cargo build -p trading-engine-core`
Expected: clean build, no errors, no warnings about the removed fields.

Run: `cargo test -p trading-engine-core`
Expected: all tests PASS. (The `test_trailing_stop_chandelier_exit` comment at `test_trend_exits.rs:216-217` still references `atr_trailing_mult=3.0` in a comment — optionally update the comment to `trailing_stop_atr_mult=2.5`; it does not affect the test.)

Run: `cargo run -p trading-engine-core --bin validate_config`
Expected: `PASS: config/strategy.yaml parsed OK`.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/config.rs trading-engine-core/src/strategy/trend.rs \
        trading-engine-core/tests/test_trend_exits.rs trading-engine-core/tests/test_trend_strategy.rs \
        trading-engine-core/tests/test_adx_discrepancy.rs config/strategy.yaml
git commit -m "refactor(trend): remove 3 dead trailing-stop fields (atr_trailing_mult, trailing_stop_pct, trailing_activation_pct)" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

### Task 4: Harden — reject a non-positive multiplier at load

**Files:**
- Modify: `trading-engine-core/src/config.rs` (add `impl TrendConfig { validate() }`; call from `AppConfig::load` at 395-399)
- Test: `trading-engine-core/src/config.rs` (add a test in the `regime_gate_config_tests` module or a new module)

**Interfaces:**
- Produces: `TrendConfig::validate(&self) -> anyhow::Result<()>`, called by `AppConfig::load`. Reach: bot boot (`main.rs:25`), `validate_config` bin (CI), `backtest_replay` bin.

- [ ] **Step 1: Write the failing test**

Add to the `#[cfg(test)]` section of `trading-engine-core/src/config.rs` (inside the existing `regime_gate_config_tests` module, or a new `trailing_stop_config_tests` module — either is fine; this example adds a new module after the existing one):

```rust
#[cfg(test)]
mod trailing_stop_config_tests {
    use super::*;

    #[test]
    fn trend_trailing_stop_atr_mult_must_be_positive() {
        // Zero and negative must be rejected (would otherwise silently hit the
        // 2.0 fallback in trend.rs and mislead the operator).
        let bad_zero = TrendConfig { trailing_stop_atr_mult: 0.0, ..Default::default() };
        assert!(bad_zero.validate().is_err(), "0.0 must be rejected");

        let bad_neg = TrendConfig { trailing_stop_atr_mult: -1.0, ..Default::default() };
        assert!(bad_neg.validate().is_err(), "negative must be rejected");

        let good = TrendConfig { trailing_stop_atr_mult: 2.0, ..Default::default() };
        assert!(good.validate().is_ok(), "2.0 must be accepted");
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test -p trading-engine-core --lib trailing_stop_config_tests`
Expected: **FAIL** — `no function named validate found for TrendConfig` (compile error).

- [ ] **Step 3: Write minimal implementation**

In `trading-engine-core/src/config.rs`, add an `impl TrendConfig` block (anywhere at module scope; multiple `impl` blocks are allowed):

```rust
impl TrendConfig {
    /// Step 1 hardening: the Chandelier multiplier must be > 0. A missing/zero
    /// value would otherwise silently fall back to the hardcoded 2.0 in
    /// trend.rs and mislead the operator. Called from AppConfig::load so boot,
    /// validate_config (CI), and backtest_replay all enforce it.
    pub fn validate(&self) -> anyhow::Result<()> {
        if self.trailing_stop_atr_mult <= 0.0 {
            anyhow::bail!(
                "trend.trailing_stop_atr_mult must be > 0 (got {})",
                self.trailing_stop_atr_mult
            );
        }
        Ok(())
    }
}
```

And call it from `AppConfig::load` (`config.rs:395-399`):

```rust
    pub fn load(path: &str) -> Result<Self> {
        let content = std::fs::read_to_string(path)?;
        let config: AppConfig = serde_yaml::from_str(&content)?;
        config.trend.validate()?;
        Ok(config)
    }
```

(If `anyhow` is not already in scope in `config.rs`, the `?` operator on `serde_yaml::from_str` and `read_to_string` in the existing `load` already requires it — confirm `use anyhow::Result;` (or equivalent) is present at the top of the file; `anyhow::bail!` is fully-qualified so it needs no separate `use`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test -p trading-engine-core --lib trailing_stop_config_tests`
Expected: **PASS**.

Run the whole suite + validate_config to confirm nothing regressed:
Run: `cargo test -p trading-engine-core && cargo run -p trading-engine-core --bin validate_config`
Expected: all tests PASS; `PASS: config/strategy.yaml parsed OK` (production value is 2.0 > 0).

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/config.rs
git commit -m "feat(config): reject non-positive trend.trailing_stop_atr_mult at load (boot + CI)" \
  -m "Co-Authored-By: Claude <noreply@anthropic.com>"
```

---

## Deploy & Validation (Step 1 → gate for Step 2)

After all four tasks land on `feat/regime-aware-trailing-stop` and CI is green:

1. **Merge + deploy** the branch (the deploy workflow builds the Rust image and SSMs `git pull && compose up`).
2. **Observe ~1 week** of trend exits from `data/trades.db` (live rows, `is_backfilled=0`):
   - Primary metric: `trailing_stop` avg loss should drop from the Jul-22-23 baseline of **-$158** (worst -$677).
   - Trade-off watch: win-rate and the tp vs trailing_stop P&L split — a tighter trail can prematurely stop genuine trends. Compare against the pre-deploy baseline.
3. **Gate Step 2** on Step 1 not regressing win-rate. Only then write the Step 2 plan (regime-aware multiplier: Trending 2.0 / Ranging 1.5 / Danger 1.0, confidence-gated, ratchet-safe — see spec §2).

## Rollback

- One-line: set `trailing_stop_atr_mult` back to `3.0` in `config/strategy.yaml` (the knob now binds, so a yaml-only change suffices; config is volume-mounted, no rebuild).
- Full: `git revert` the Step 1 commits.
