# Price-Sanity Filter — Design

**Date:** 2026-08-01
**Status:** Approved (pending spec review)
**Scope:** `trading-engine-core` (Rust engine). Paper + live.

---

## 1. Problem

On 2026-07-31 the trend engine recorded a BNB exit at **$497.51**, producing a
**−$604.11** loss (−6.46R) — 86% of trend's apparent all-time loss. BNB never
traded near 497 that day (Binance 1-min low 583.99; week low 565). The loss is a
**phantom**: the engine was fed a price that did not exist in the market.

### Root cause (verified)

- The live price source is the **order-book mid-price** from Binance's
  `@depth20@100ms` stream — **not** trade ticks (trades are subscribed but
  discarded, `engine.rs:222-224`).
- `OrderBook::mid_price()` is `(best_bid + best_ask) / 2.0` with **zero
  validation** (`connector/types.rs:120`). Raw `.parse::<f64>()` on every level,
  no finite/positive/NaN guard (`connector/binance_ws.rs:195-199, 207`).
- A single garbage book flowed straight into `Engine.order_books` at
  `engine.rs:202-207` and was consumed by **all three** price readers:
  - trend/grid exits via `ctx.order_book.mid_price()` (`trend.rs:566`, `grid.rs:570`)
  - paper fills via `ob.mid_price()` (`engine.rs:660` → `paper.rs:128`)
  - risk/circuit-breaker MTM via `portfolio_equity_mtm` (`engine.rs:427,440`)
- There is **no last-good-price fallback anywhere**. The garbage mid tripped the
  trailing stop (497 ≤ 566 trail) and closed the position at the fictitious price.

### Why this blocks live trading

In paper mode the damage is a corrupt balance. In **live** mode, the same path
would place a real market order at a fictitious price — i.e. a real catastrophic
loss from a single malformed WS message. This is a hard blocker for any real-money
deployment.

---

## 2. Goals & Non-Goals

**Goals**
- Reject garbage order books so no consumer (exits, paper fills, risk MTM) ever
  acts on a price the market never printed.
- Do it at the **single chokepoint** so every consumer is covered in one place.
- Verify suspect prices against an **independent source** (user choice) before
  accepting.
- **Fail safe**: when verification is unavailable, hold the last-good price and
  block new entries on that pair (user choice), never trade on an unverified
  suspect price.
- Be self-adapting across very different instruments (BNB/ETH majors **and**
  3000+ low-cap signal tokens) without per-coin tuning.

**Non-Goals (out of scope)**
- The perp-mark short-side path (`connector/perp_price.rs` → `trend.rs:581`)
  bypasses `order_books`. It is **inert** while `trade_shorts: false`. TODO if
  shorts migrate to the futures engine.
- General feed-health / reconnection logic (a separate `feed_breaker()` concern).
- Replaying/auditing historical books.

---

## 3. Architecture

### Insertion point

The filter is applied in `engine.rs`, `run()`, the `WsEvent::OrderBookUpdate`
arm, immediately at/after the `self.order_books.insert(...)` call
(`engine.rs:202-207`) and **before** the three downstream calls at
`engine.rs:208-210` (`tick_strategies`, `process_paper_fills`, `feed_breaker`).

Because `Engine` owns `order_books: HashMap<String, OrderBook>` persistently
(`engine.rs:38`), per-symbol trusted state lives naturally on `Engine` — unlike
`OrderBook::mid_price()` (a method on a value type cloned into `TickContext`),
which cannot keep cross-call state and would miss direct bid/ask reads.

### Components

1. **`PriceFilter`** (new module `src/price_filter.rs`) — owns per-pair trusted
   state + the rolling-stdev window. Pure, synchronous, testable.
   Method: `fn observe(&mut self, symbol, book) -> FilterDecision`.
   (Lives at crate root, not under `engine/`, because `engine` is a single
   `src/engine.rs` file.)

2. **`price_verify`** (new, `connector/price_verify.rs`) — async REST cross-check.
   `async fn verify_price(symbol, suspect_mid, ctx) -> VerifyResult`.

`Engine` holds a `PriceFilter` and, on each `OrderBookUpdate`, calls `observe`;
on a `NeedsVerify` decision it spawns the async verify and updates state on
completion.

---

## 4. Detection (cheap local trigger)

We cannot REST-verify every 100ms tick. A local trigger flags suspects; only
suspects pay the REST cost.

Per symbol, maintain a **rolling window of recent mid-prices** (default 200 books
≈ 20s). Derive `stdev` from the window.

A book's mid is **suspect** when:

```
|mid - last_good_mid|  >  max( K * stdev , floor_pct * last_good_mid )
```

- `K = 10.0` (≈10σ — BNB's 100ms stdev is tiny, so a 497-type spike is ~20σ and
  flagged instantly; normal noise stays well inside).
- `floor_pct = 0.5%` — prevents flakiness when stdev collapses near zero.
- Self-adapts per coin: a volatile memecoin's larger stdev yields a wider band, so
  real low-cap moves pass; BNB's tight stdev catches the garbage.

**Hard reject** (no REST needed — obviously malformed): non-finite mid, `mid <= 0`,
`best_bid >= best_ask`, or empty sides.

**Warmup:** the first book for a symbol has no `last_good` and no window → accept
and seed.

---

## 5. Adjudication (cross-source verify) + State Machine

Per-pair state: `last_good_mid: f64`, `last_good_book: OrderBook`,
`status: PriceStatus { Trusted, Suspect }`, plus the rolling window.

```
on each incoming book (mid):
  if hard-reject           -> discard, keep last_good, alert, block entries
                             (do NOT REST-verify obvious garbage)
  if !suspect (within band):
     accept -> insert book, last_good := book, status := Trusted
  else  // suspect
     do NOT insert garbage; keep last_good
     block new entries on this pair
     if status == Trusted: spawn async verify_price(symbol, mid)   // once
     status := Suspect

verify_result arrives:
  Confirmed (REST price within tolerance of SUSPECT mid):
     -> real move: accept the suspect book, last_good := it, status := Trusted
  Denied (REST price within tolerance of LAST_GOOD, far from suspect):
     -> garbage confirmed: discard, keep last_good; status stays Suspect
        (recovery via self-heal below)
  Ambiguous (REST agrees with NEITHER ref value within tolerance):
     -> treat as Unavailable: hold last_good + block entries + alert
        (safest; the truth is unknown)
  Unavailable / timeout:
     -> hold last_good + block entries + alert   (user-chosen fail-safe)
        recovery via self-heal below

self-heal recovery (clears Suspect without depending on REST):
  while Suspect, if WS mid returns within band of last_good for
  `recover_consecutive_ticks` (default 3) ticks -> status := Trusted, clear block
```

The self-heal is necessary so a transient spike combined with a REST outage does
not strand a pair in `Suspect` forever. It is a **recovery** mechanism, distinct
from the cross-source **verification** the user selected as primary.

### Verify sources (priority order)

1. **Binance REST** `GET /api/v3/ticker/price?symbol=<SYM>` — same venue, exact
   spot semantics. This is what rules out a WS-message/parsing glitch (the actual
   failure mode). Tolerance `verify_tolerance_pct` (default **1.0%**).
2. **Gate USDT-M perp fallback** via the existing `connector/perp_price.rs`
   fetcher — independent venue; wider tolerance (perp basis), used only if Binance
   REST fails and `enable_gate_fallback` is true.

`VerifyResult::Confirmed` / `Denied` / `Unavailable` is decided by which reference
value (suspect mid vs last_good) the REST price agrees with; `Unavailable` when no
source returns within `verify_timeout_ms` (default **800ms**).

---

## 6. Entry-blocking

When a pair is `Suspect`, new **entries** on that pair are suppressed (the engine
already has a `set_paused`/suppress pattern; we add a per-pair price-suspect gate
the entry path checks). **Exits and position management keep running** against
`last_good_book` — the safe price — so an open position is never abandoned during
a suspect window (mirrors the existing `trend.rs:596-598` "pausing suppresses
entries, not management" contract).

---

## 7. Configuration

New block in `strategy.yaml` + matching fields in `config.rs` (under the root
`PriceIntegrity` config), validated by `AppConfig::load` (reject any value ≤ 0),
matching the existing knob-hardening pattern (`trailing_stop_atr_mult` etc.):

```yaml
price_integrity:
  enabled: true
  stdev_window: 200          # rolling books kept per symbol (~20s at 100ms)
  stdev_k: 10.0              # suspect if |Δmid| > k·stdev
  min_deviation_pct: 0.5     # floor on the band (avoids stdev≈0 flakiness)
  verify_tolerance_pct: 1.0  # REST within this of a ref value = agreement
  verify_timeout_ms: 800
  recover_consecutive_ticks: 3
  enable_gate_fallback: true # Gate perp as secondary verify source
```

---

## 8. Phantom-loss cleanup

The 2026-07-31 BNB row (exit 497.91, pnl −604.11) remains in `data/trades.db`
and `TrendStrategy.realized_pnl`, corrupting the paper balance. As part of this
work: **mark that row as phantom** (e.g. set `exit_reason = 'phantom_bad_tick'`,
zero the realized P&L contribution, or delete the row) and adjust the engine's
persisted realized P&L / `risk_state.json` start-of-day so the paper account
reflects reality. Exact mechanism decided during implementation (idempotent,
re-runnable).

---

## 9. Testing (TDD)

Failing tests first, then implementation, following the existing
`connector/paper.rs` and `strategy/trend.rs` unit-test style.

1. Garbage book (mid far outside band) → `last_good` retained, **not** inserted,
   entries blocked.
2. Hard-reject cases (NaN, ≤0, bid ≥ ask) → discarded, no REST call.
3. Suspect that REST **confirms** (real move) → book accepted, block cleared.
4. Suspect that REST **denies** (garbage) → discarded, `last_good` kept.
5. Verify **timeout / unavailable** → held + blocked + alert; no trade on suspect.
6. **Self-heal**: after `recover_consecutive_ticks` in-band ticks, `Trusted`
   restored, block cleared — even with REST down.
7. Normal book (within band) → passes through untouched (no REST, no block).
8. Warmup: first book for a symbol accepted and seeds `last_good`.
9. Rolling-stdev adapts: a move that is suspect at low stdev passes at high stdev.

`price_verify` HTTP tested with a mock/in-process responder (no network in CI).

---

## 10. Open decisions resolved

| Decision | Resolution |
|---|---|
| Detection method | Cross-source verify (user) — composed as local trigger + REST verify |
| Fail-safe when verify unavailable | Hold last-good + block entries + alert; self-heal recovers (user + necessary) |
| Verify source priority | Binance REST primary, Gate perp fallback |
| Trigger metric | Rolling per-symbol stdev (adaptive across majors + low-caps) |
| Phantom −$604 | Void/reconcile as part of this work |
| Perp short-side path | Out of scope (inert while `trade_shorts: false`) |

---

## 11. Rollout

1. Land behind `price_integrity.enabled` (default `true`).
2. Deploy to EC2 (paper). Confirm via logs: no spurious blocks on normal flow; the
   497-class spike would now be rejected.
3. Observe one cycle of normal volatility + at least one real fast move to
   validate the trigger band and verify path before considering live.
