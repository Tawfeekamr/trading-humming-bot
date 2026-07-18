# Trend Regime Gate — Design Spec

**Date:** 2026-07-18
**Status:** Approved (design) → pending implementation plan
**Author:** Pair session (Tawfeek + Claude)

## Problem

The bot lost **−$1,038.81 net over the trailing 14 days (Jul 4–18)**, of which the **trend engine lost −$1,099.10** across 22 trades (9 wins, 41% win rate). Almost the entire loss was **ETH-USDT (−$1,046.39)**; every other pair netted +$8 combined. Trend lost on ETH in **both directions** (longs −$595, shorts −$371) — the classic whipsaw signature of a trend-following strategy firing into a ranging market.

Root cause (verified in current code, see `memory/trend_regime_gate_not_wired.md`):

- `config.rs:416` — `regime_gate` defaults to `false`.
- `config/strategy.yaml` — no `regime_gate` key is set, so trend runs with the gate **OFF**.
- `trading-engine-core/src/strategy/trend.rs` (1,669 lines) **never reads `ctx.regime`** in its decision logic.
- Only `mean_reversion.rs:205` consumes `config.regime_gate` — and MR is disabled.
- `grid.rs` *does* read regime (`:620`, `:737`, pauses on `Danger`) — which is why grid made one safe trade (+$96). The plumbing works; it is just not connected to the engine doing 85% of the trades.
- The ML regime classifier **is** computed and pushed every 180s (`regime-pusher` sidecar) **and** loaded into `TickContext` (`engine.rs:248`) — then discarded by trend.

RL is **not deployed** (docker-compose has `rust-bot`, `signal-listener`, `regime-pusher`; no `live_router`) and has no proven return edge (walk-forward p=0.77 vs clean RF). It is out of scope for this fix.

**Consequence:** enabling real money today would reproduce the same losses. The ML guardrail that should have prevented the ETH whipsaw is computed but not wired into the trend entry.

## Goal

Make the trend engine refuse new entries when the ML regime says the market is **Ranging** or **Danger** (with sufficient confidence), and prove with a faithful replay that this would have cut the Jul 4–14 ETH loss — before any consideration of real money.

## Non-goals (out of scope)

- Grid engine (already gates on `Danger`; profitable this window).
- Mean-reversion engine (disabled since 2026-07-02).
- Signal engine, capital allocation, position sizing.
- RL deployment / the live_router falsification experiment (separate decision).
- The trend **SHORT** paper-fiction problem (spot shorts are not live-realizable). The regime gate applies to long and short entries equally and helps both, but whether shorts should exist at all on the spot engine is a separate decision to revisit before go-live.

## Design

### Fix 1 — Regime gate on trend entries

**Config (`config.rs` + `strategy.yaml`):**

Add two fields to `TrendConfig`, mirroring the existing `mean_reversion` pattern:

```rust
pub regime_gate: bool,            // default false (back-compat)
pub min_regime_confidence: f64,   // default 0.55
```

Enable for trend in `config/strategy.yaml`:

```yaml
trend:
  regime_gate: true
  min_regime_confidence: 0.55   # tuned after inspecting real confidence distribution
```

**Gate logic (`trend.rs`, inside `on_tick` before a new entry is taken):**

```rust
let entry_blocked_by_regime = self.config.regime_gate
    && matches!(
        ctx.regime,
        Some(MarketRegime::Ranging) | Some(MarketRegime::Danger)
    )
    && ctx.regime_confidence >= self.config.min_regime_confidence;
```

Semantics:

| `ctx.regime` | confidence | New entry? | Why |
|---|---|---|---|
| `None` | — | **allowed** | no regime info (back-compat; replay without regime file) |
| `Trending` | any | **allowed** | this is the trend-follower's preferred regime |
| `Ranging` / `Danger` | `< min` | **allowed** | low-confidence label → fall back to TA (matches regime-pusher philosophy) |
| `Ranging` / `Danger` | `>= min` | **blocked** | trust the bad-regime label, stay flat |

**Critical: gates NEW entries only.** Open positions continue to be managed — funding, breakeven, trailing, and the full TP/SL ladder run unchanged. This mirrors the existing `set_paused` / `entries_suppressed` semantics (see the `force_close_pending` vs `entries_suppressed` distinction already documented in `trend.rs`). A regime change must not strand a position without its stops.

The gate check is inserted at the entry-decision site, returning an empty order vector for the entry branch (no new position opened) while leaving the position-management branch intact.

**Unit tests (`trend.rs` test module):**

1. Gate OFF → entry taken regardless of regime (back-compat).
2. Gate ON, regime = `Trending` → entry taken.
3. Gate ON, regime = `Ranging`, confidence ≥ min → entry **blocked**.
4. Gate ON, regime = `Ranging`, confidence < min → entry taken (low-conf fallback).
5. Gate ON, regime = `Danger`, confidence ≥ min → entry **blocked**.
6. Gate ON, regime = `Ranging`, confidence ≥ min, **with an open position** → exits/TP/SL still fire (management not suppressed).
7. regime = `None` (replay-without-file path) → entry taken.

### Fix 2 — Regime-aware replay backtest

**Current state:** `backtest/replay.rs:461` hardcodes `regime: None, regime_confidence: 0.0` in every `TickContext`. `backtest_replay.rs:24` documents this as a known limitation.

**Change:** thread an optional regime timeline through replay.

- Timeline format (one file, all pairs):

  ```json
  {
    "ETH-USDT": [
      {"ts": 1720000000000, "regime": 0, "confidence": 0.71},
      {"ts": 1720000180000, "regime": 0, "confidence": 0.68},
      ...
    ]
  }
  ```

  (`regime`: 0=Ranging, 1=Trending, 2=Danger — matches `RegimeCache` ints.)

- New CLI flag on `backtest_replay`: `--regime-file <path>`.
- When loaded, for each `TickContext` built at tick time `T`, inject the most-recent timeline entry with `ts ≤ T` for that pair (linear scan or pre-built cursor — the timeline is sorted).
- When the flag is absent or the pair has no timeline → `regime: None` (current behavior, fully back-compatible).
- Update the `backtest/report.rs` caveat and the `//!` doc comments to say regime is injected when `--regime-file` is supplied.

### Fix 3 — Generate ETH regime labels for the losing window

A small Python script (`src/ml/regime_labels_backfill.py` or a one-off in this workstream) that **reuses the exact live pipeline** so labels match what `regime-pusher` would have produced:

1. Fetch historical 1h ETH-USDT bars for **Jul 4 00:00 → Jul 15 00:00 UTC** from the Binance public API (no auth needed for klines). Sample densely enough to support replay tick granularity.
2. For each prediction timestamp (every 180s, matching the live push cadence), build the feature row via `src.data.feature_engineering.calculate_technical_features` and predict with `RegimeClassifier(model_path="models/regime_ETH-USDT_clean.pkl", model_type="random_forest").predict_proba_full`.
3. Emit the timeline JSON above.

**Why Python, not the Rust classifier:** the live `regime-pusher` is Python and uses this exact feature pipeline. Reproducing it in Rust would risk feature divergence and invalidate the proof. Matching the live path is the entire point.

The script also prints the **confidence distribution** over the window so `min_regime_confidence` (default 0.55) can be tuned to a real percentile before the gated replay.

### Proof — gated vs ungated replay

Run `backtest_replay` on ETH-USDT, Jul 4–14:

| Run | `regime_gate` | `--regime-file` | Expected |
|---|---|---|---|
| (a) baseline | OFF | none | reproduces ~−$966 trend-ETH loss (sanity check that replay is faithful) |
| (b) labels-only | OFF | ETH labels | ≈ baseline (labels don't change behavior when gate is off) |
| (c) **the fix** | ON | ETH labels | materially smaller loss; show P&L delta and trade list |

**Success criteria:**

1. Run (a) reproduces the realized trend-ETH loss within reasonable tolerance → replay is trustworthy.
2. Run (c) shows a **material P&L improvement** (target: cuts the loss substantially, not marginally).
3. The trades skipped by the gate were **genuinely in Ranging/Danger windows** — i.e. the gate is selective, not a blunt "trade nothing" switch. We verify this by listing each skipped trade's regime label. If the gate suppresses winning Trending trades too, that is a failure and we revisit the threshold/logic.

**Failure outcome:** if run (c) does not materially help (e.g. the classifier labeled those ETH windows `Trending`, so the gate never fired), that is itself a valid, important result — it means the regime model is not yet good enough to protect trend on ETH, and the go-live decision must account for that. We report it honestly rather than ship a gate that does nothing.

## Risks & mitigations

- **Gate too aggressive** (kills winners): mitigated by the confidence threshold and the selectivity check in the proof; tunable in config without code change.
- **Gate too lax** (doesn't help): exposed by run (c); threshold tunable; if the model itself is the problem, reported honestly.
- **Replay fidelity drift** when adding regime: mitigated by back-compat (no file = old behavior) and the run (a) sanity check.
- **Live parity of labels**: guaranteed by reusing the Python `regime-pusher` pipeline verbatim.
- **Open positions stranded on regime flip**: prevented because the gate blocks entries only, never management.

## Deliverables

1. `trend.rs` — `regime_gate` / `min_regime_confidence` fields + gate logic + unit tests.
2. `config.rs` / `strategy.yaml` — fields + `regime_gate: true` for trend.
3. `backtest/replay.rs` + `backtest_replay.rs` — `--regime-file` injection.
4. `src/ml/regime_labels_backfill.py` — ETH label generator (reuses live pipeline).
5. Replay comparison report (runs a/b/c) committed to `docs/`.
6. Updated `backtest/report.rs` and doc comments re: regime injection.

## Follow-ups (not in this fix)

- Decide RL's fate (no proven edge; not deployed).
- Decide whether trend SHORT entries should exist on the spot engine at all (paper-fiction).
- Consider the same regime gate treatment for any future engine that trades blind to regime.
