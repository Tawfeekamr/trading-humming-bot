# Regime-aware trailing stop (phased: fix wiring, then add ML)

**Date:** 2026-07-23
**Status:** Approved (design), pending implementation
**Scope:** `trading-engine-core/src/strategy/trend.rs`, `trading-engine-core/src/config.rs`, `trading-engine-core/src/bin/validate_config.rs`, `trading-engine-core/tests/test_trend_exits.rs` (+ other trend tests), `config/strategy.yaml`

## Context — why

The trend engine is the system's biggest loser: **-$610 all-time (33 trades)**, and
**-$1,197 in the last 7 days (3 trades, 0 wins)**. The loss is concentrated almost
entirely in one exit type — `trailing_stop` exits are **-$1,420 of -$1,516 gross
losses (94%)**, averaging **-$158 each** (worst: -$677 on a BNB long). The TP ladder
makes money (+$906); the trailing stop gives entire moves back on reversals.

While diagnosing this, a **config-wiring bug** was found (same class as the
`signal_status.json` fossil fixed the same day):

- The Chandelier trailing stop is computed at `trend.rs:703` from
  **`self.config.atr_trailing_mult`**.
- `atr_trailing_mult` is **not set in `config/strategy.yaml`** → it takes its serde
  default **3.0** (`config.rs:256`, `default_3`).
- The yaml knob that *looks* like it controls the trail —
  `trailing_stop_atr_mult: 2.5` (`strategy.yaml:170`) — is a **dead field**: it is
  stored into the strategy struct (`trend.rs:184`) but **never read** by any logic.
- Two further dead knobs: `trailing_stop_pct` and `trailing_activation_pct` (the
  latter carries a "Claude: must be > trail distance" comment — a prior session
  tuned a field that does nothing).

**Net:** the live trail runs at **3.0×ATR**, not the 2.5 the config implies — wider
than intended, directly worsening the whipsaw bleed. Anyone editing these yaml fields
believes they are tuning the trail; they are not.

This also answers a strategic question: the ML/RL layer cannot help here because it
operates in the **entry/regime** layer, while the money leaks from the **exit**
layer. This spec moves ML into the exit layer — but only after the field wiring is
correct.

## Goal

1. **(Step 1, no ML)** Make the one real trailing-stop knob actually work;
   eliminate the dead fields; set a tighter, validated multiplier. Immediate relief
   on the whipsaw loss-per-trade.
2. **(Step 2, ML — validated follow-up)** Make that multiplier regime-aware
   (tighter when the regime model says choppy/reversing) so ML touches the exit
   layer where the bleed is.

## Non-goals (YAGNI)

- Do **not** change grid / swing / mean-reversion exit logic (they have their own).
- Do **not** retrain or alter the regime RF model.
- Do **not** deploy or wire the RL router (separate workstream; its own validation
  says no return edge).
- Do **not** change the trend entry gate.
- Do **not** introduce a new ATR indicator or change the Chandelier formula — only
  the multiplier source and value.

---

## Step 1 — fix wiring + tighten (no ML)

### 1.1 Consolidate to one live field

Make `trailing_stop_atr_mult` the single source of truth for the Chandelier
multiplier.

- `trend.rs:703` — change the read from `atr_trailing_mult` to `trailing_stop_atr_mult`:
  ```rust
  // before
  let atr_mult = if self.config.atr_trailing_mult > 0.0 { self.config.atr_trailing_mult } else { 3.0 };
  // after
  let atr_mult = if self.config.trailing_stop_atr_mult > 0.0 {
      self.config.trailing_stop_atr_mult
  } else {
      2.0 // safe fallback; validate_config rejects <= 0 so this never binds in prod
  };
  ```
- Remove the redundant `atr_trailing_mult` field from `TrendConfig`
  (`config.rs:255-257`). It is **not** shared with any other engine (swing uses
  `atr_stop_mult` / `band_atr_mult`; verified absent from `SwingConfig`). **Leave
  `default_3` in place** — still used by `RiskConfig` (`config.rs:345, 357`).
- Remove the two dead fields from `TrendConfig`:
  - `trailing_stop_pct` (`config.rs:232-233`, `default_1_5`)
  - `trailing_activation_pct` (`config.rs:236-237`, `default_1_5`)
  - Both confirmed dead in logic (only struct-init at `trend.rs:183/185` + the
    default-config builder at `trend.rs:1420-1421` + test init lines). **Leave
    `default_1_5` in place** — still used by `GridConfig` (`config.rs:169`) and
    `SwingConfig` (`516, 522`).
- Remove the corresponding struct-init lines in `trend.rs` (`183, 185, 195` for
  `atr_trailing_mult`) and the default builder (`1420-1421`).

The compiler enforces completeness: any lingering reference to a removed field fails
the build.

### 1.2 Set the value

`config/strategy.yaml:170`:
```yaml
trailing_stop_atr_mult: 2.0   # was 2.5 (dead field); live trail was actually 3.0.
```
Remove `strategy.yaml:171` (`trailing_activation_pct` — dead). `trailing_stop_pct`
is not in the yaml (defaults only), so nothing to remove there.

Effective trail distance: **3.0 → 2.0 ×ATR**.

### 1.3 Harden with validate_config

`src/bin/validate_config.rs` — add a check that `trend.trailing_stop_atr_mult > 0.0`
(fail boot otherwise). This prevents a future missing/zero field from silently
falling back, which is exactly how the 3.0-default went unnoticed for a month.

### 1.4 Tests (Step 1)

- Update `tests/test_trend_exits.rs`: it currently documents
  `atr_trailing_mult=3.0` (lines 216-217) and sets both `trailing_stop_atr_mult: 2.5`
  (24) and `atr_trailing_mult: 3.0` (35). After consolidation only
  `trailing_stop_atr_mult` exists and is read. **Recompute the expected trail
  values** for the field's value (2.5 in the test's own config) and assert the trail
  tracks `trailing_stop_atr_mult`, not a hardcoded 3.0.
- Update `tests/test_trend_strategy.rs` (21-23, 33) and
  `tests/test_adx_discrepancy.rs` (22-24, 34): remove the deleted field-init lines;
  keep `trailing_stop_atr_mult`.
- Add a focused test: with `trailing_stop_atr_mult = X`, the computed trail equals
  `highest_since_entry - X * atr` (long) / `lowest + X * atr` (short), for two
  different X values — proving the knob now binds.
- `validate_config` test: a config with `trailing_stop_atr_mult <= 0` is rejected.

### 1.5 Step 1 rollout & validation

Deploy, then observe **~1 week** of trend exits:
- Expect `trailing_stop` avg loss to drop from the current **-$158** (and the
  per-trade worst case from -$677).
- Watch the trade-off: a tighter trail can prematurely stop genuine trends. Compare
  win-rate and the tp vs trailing_stop P&L split against the Jul-22-23 baseline.
- Gate Step 2 on Step 1 not regressing win-rate.

---

## Step 2 — regime-aware multiplier (on the corrected field)

Deployed **only after** Step 1 is validated. Goal: ML touches the exit layer.

### 2.1 Mapping (approved values)

| Regime (int) | Label     | `trailing_stop_atr_mult` | Rationale              |
|--------------|-----------|--------------------------|------------------------|
| 1            | Trending  | 2.0 (base)               | let winners run        |
| 0            | Ranging   | 1.5                      | chop → cut faster      |
| 2            | Danger    | 1.0                      | reversing → protect    |

### 2.2 Confidence gate (mirrors the entry gate)

Only override the base multiplier when `regime_confidence >= min_regime_confidence`
(existing field, `0.55`). Below the threshold → use the base (2.0). This gracefully
handles today's low-confidence labels (e.g. DOGE danger@0.49, XRP danger@0.51),
which would otherwise over-tighten on a weak signal. It reuses the entry gate's
"don't trust low-conf labels → fall back to TA/base" philosophy and the same
`min_regime_confidence` knob.

### 2.3 Implementation

At `trend.rs:703`, replace the single `atr_mult` line with a regime-aware lookup:
```rust
let base = self.config.trailing_stop_atr_mult;
let atr_mult = regime_trail_mult(
    base,
    ctx.regime,                     // Option<MarketRegime> (strategy::MarketRegime) — same source as the entry gate (trend.rs:832)
    ctx.regime_confidence,          // f64
    self.config.min_regime_confidence,
    self.config.regime_trail_mult.as_ref(), // Option<&RegimeTrailMult>
);
```
`ctx.regime: Option<MarketRegime>` (`strategy/mod.rs:24`) and `ctx.regime_confidence: f64`
(`:27`) are already populated per-tick (the entry gate has read them live since
2026-07-18), so no new plumbing. `MarketRegime` is `{Ranging=0, Trending=1, Danger=2}`
(`strategy/mod.rs:40`, mirrors `ml/regime.rs:8`). `regime_trail_mult` is a pure helper:
```rust
fn regime_trail_mult(
    base: f64,
    regime: Option<MarketRegime>,
    conf: f64,
    min_conf: f64,
    mapping: Option<&RegimeTrailMult>,
) -> f64 {
    // No mapping, no regime, or low confidence → base (full back-compat).
    let (Some(m), Some(r)) = (mapping, regime) else { return base; };
    if conf < min_conf { return base; }
    let v = match r {
        MarketRegime::Trending => m.trending,
        MarketRegime::Ranging => m.ranging,
        MarketRegime::Danger => m.danger,
    };
    if v > 0.0 { v } else { base }
}
```

### 2.4 Ratchet safety (no change to ratchet logic)

The trailing stop only ever moves tighter — longs `.max(prev)` (`trend.rs:711`),
shorts `.min(prev)` (`trend.rs:712`). A regime shift to a **smaller** multiplier
(danger) produces a **tighter** `new_trail`, which the ratchet accepts; a shift back
to a larger multiplier (trending) produces a looser `new_trail`, which the ratchet
**rejects** (keeps the tighter prev). So the regime can only snap the trail closer,
never loosen it. This is the desired, safe behavior — no ratchet change needed.

### 2.5 Config shape (optional block; absent = no-op)

`config.rs` — add to `TrendConfig`:
```rust
#[serde(default)]
pub regime_trail_mult: Option<RegimeTrailMult>,
```
with
```rust
#[derive(Debug, Clone, Deserialize)]
pub struct RegimeTrailMult {
    #[serde(default = "default_2")]  pub trending: f64,
    #[serde(default = "default_1_5")] pub ranging: f64,
    #[serde(default = "default_1")]  pub danger: f64,
}
```
`config/strategy.yaml` (trend block) — add:
```yaml
  regime_trail_mult: { trending: 2.0, ranging: 1.5, danger: 1.0 }
```
When the block is absent, or all three equal the base, behavior is **identical to
Step 1** (full back-compat). No separate enable flag — presence + confidence gate is
sufficient and keeps rollback to a one-line yaml edit.

### 2.6 Tests (Step 2)

- Unit test `regime_trail_mult`: for each regime int (0/1/2) at confidence ≥
  threshold, returns the mapped value; sub-threshold confidence → base; `None`
  regime/conf/mapping → base; mapped value `<= 0` → base.
- Integration in `test_trend_exits`: under an injected `danger` regime (conf ≥ 0.55)
  the trail ratchets tighter than under `trending` for the same price path; a
  subsequent regime flip to `trending` does **not** loosen it.

### 2.7 Step 2 rollout & validation

Deploy, observe ~1 week:
- Trail should visibly tighten on `ranging`/`danger` labels.
- Watch for over-stopping (premature exits in genuine trends) — the risk of coupling
  to a descriptive (now-cast) model that lags.
- Compare MaxDD and the trailing_stop P&L split vs the Step-1 baseline.

---

## Rollback

- **Step 1:** `git revert` the PR, or set `trailing_stop_atr_mult` back to a wider
  value (e.g. 3.0) in the yaml — the knob now actually binds, so a yaml-only change
  suffices.
- **Step 2:** remove the `regime_trail_mult` block from the yaml → instantly reverts
  to Step-1 (base) behavior. One-line, no redeploy of logic needed (config is
  volume-mounted).

## Risks

- **Step 1 — tighter trail cuts winners:** 2.0 vs the effective 3.0 may stop out
  some trends that would have resumed. Mitigation: 2.0 is moderate; the 1-week
  observation gate catches a win-rate regression before Step 2.
- **Step 2 — lagging regime labels over-stop:** the regime model is a now-cast
  (labels recent action, not future), so a `danger` label can arrive after the
  reversal. Mitigation: confidence gate (low-conf → base) + ratchet-only-tightens
  (can't loosen into a loss) + one-line rollback.

## Out of scope

Grid/swing/MR exits, regime-model retraining, RL deployment, entry-gate changes.
