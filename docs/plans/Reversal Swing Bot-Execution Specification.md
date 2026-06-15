# Reversal Swing Bot — Order-Execution Specification

**Companion to:** `reversal-swing-bot-requirements.md`
**Purpose:** Define exactly which order type each leg uses (maker vs taker), the entry order-type A/B test, the exit order state machine, and the stop-width sweep — so the backtest evaluates these as clean variables rather than a hardcoded guess.
**Status:** Specification, pre-development

---

## 0. The finding that shapes this spec — fee tier

The original cost analysis treated "taker fees + slippage" as recoverable by switching to maker orders. **On a standard Binance.com account this is wrong: maker and taker fees are identical (0.10% each).** Switching to maker recovers *slippage only*, not fees.

| Account / tier | Maker | Taker | Maker advantage |
|----------------|-------|-------|-----------------|
| Binance.com — regular | 0.10% | 0.10% | **none on fees — slippage only** |
| Binance.com — regular + BNB (25% off) | 0.075% | 0.075% | none on fees — slippage only |
| Binance.com — high VIP | ~0.025% | ~0.031% | small fee gap + slippage |
| Binance.com — top VIP | ~0.00825% | ~0.01725% | meaningful fee gap + slippage |
| Binance.US — all users | 0% | 0.02% | full taker fee + slippage (not available outside US) |

**Implication:** unless the account is VIP or on Binance.US, the maker switch is a slippage play, not a fee play. The bar for a maker entry to beat a taker entry is therefore high — it must save more in slippage than it loses to non-fills and adverse selection (Section 3). The backtest cost model (Section 6) MUST use the account's *actual* tier, and MUST NOT assume `maker_fee < taker_fee`.

---

## 1. Order-type matrix by leg

| Leg | Order type | Liquidity | Fill guaranteed? | Why |
|-----|-----------|-----------|------------------|-----|
| Entry — Mode A (baseline) | `MARKET` | Taker | Yes | Guaranteed fill on confirmation |
| Entry — Mode B (test) | `LIMIT_MAKER` + timeout | Maker | No | Saves slippage; risks no fill / adverse selection |
| TP1 (midline, 50%) | `LIMIT_MAKER` resting | Maker | No (passive) | Known target; saves slippage; always worth it |
| Hard stop loss | `STOP_MARKET` | Taker | Yes | Must guarantee exit — never a stop-limit (gap risk) |
| Chandelier runner exit | `STOP_MARKET` (amended up) | Taker | Yes | Trailing stop; must fill |
| Time-stop exit | `MARKET` | Taker | Yes | Forced exit at expiry |
| Regime-flip exit | `MARKET`, reduce-only | Taker | Yes | Forced reduce-only close |

Every sell carries `reduce_only: true` (spot long-only — the bot can never open or flip a short).

---

## 2. The principle

Three legs are **taker by necessity** and will never change: the hard SL, the chandelier trail, and the forced exits (time-stop, regime-flip). A stop that might not fill is not a stop. So a fixed portion of the cost drag is unavoidable.

The two legs where maker is *possible* are the **entry** (contentious — see Section 3) and the **midline TP** (safe — see Section 4).

---

## 3. Entry execution — the A/B test

The entry is the contested leg because a resting maker order changes the strategy, not just the cost.

### 3.1 Mode A — taker (baseline, current spec)
- **Trigger:** last closed LTF bar passes all hard gates and confirmation score ≥ threshold.
- **Action:** `MARKET` buy, sized per Section 5 of the requirements doc.
- **Fill:** guaranteed. **Cost:** taker fee + entry slippage.

### 3.2 Mode B — post-only maker (test variant)
- **Trigger:** identical to Mode A.
- **Action:** place `LIMIT_MAKER` buy at `entry_limit_price = min(trigger_candle.close, best_bid)`.
  - `LIMIT_MAKER` is rejected by Binance if it would cross the book, so the price must rest at/below best bid. If the bounce is already running (price above the reference), the order rests and may never fill.
- **Fill timeout:** `ENTRY_FILL_TIMEOUT` (default 1–2 LTF bars, or N seconds).
- **On timeout, unfilled → ABANDON** (cancel, no trade). Chasing reintroduces the taker cost and worse fills; the edge was *at* the band, not above it.

### 3.3 Mode B′ — post-only with taker escalation (optional)
- As Mode B, but on timeout escalate to a `MARKET` buy instead of abandoning. Test as a separate arm.

### 3.4 The adverse-selection warning (must be measured)
A resting buy fills **only when price trades down through it**. This means Mode B/B′ is preferentially filled right before *failed* bounces (price kept dropping) and *skips* the clean bounces that ran away from the limit. The per-fill price looks better, but realized win rate can drop. **The backtest must compare realized win rate and net expectancy per mode — not entry price in isolation.** On a standard fee tier, Mode B must save more slippage than it loses to this effect to be worth adopting.

---

## 4. Exit execution — order state machine

On **entry fill**, immediately establish two resting orders:
1. `LIMIT_MAKER` sell @ `midline` for `TP1_qty` (50% of position; if 50% rounds below `step_size`, set `TP1_qty = 100%`).
2. `STOP_MARKET` sell @ `hard_SL` for `full_qty` (protects the entire position; `hard_SL = trigger_candle.low − ATR_STOP_MULT × ATR_LTF`, set once, never trails).

On **TP1 fill**:
- Cancel the full-qty `STOP_MARKET`; replace with `STOP_MARKET` for `runner_qty` at `hard_SL` (or breakeven — `RUNNER_SL_TO_BREAKEVEN`, config, default false).
- Begin chandelier trailing on the runner.

**Chandelier (engine, each closed LTF bar):**
```
chandelier_stop = max(prev_chandelier_stop,
                      highest_high_since_entry − CHANDELIER_MULT × ATR_LTF)
```
Ratchets up only. When `chandelier_stop > current runner stop`, cancel/replace the runner `STOP_MARKET` upward. Runner exit mode follows `RUNNER_EXIT` from the requirements doc (`band_or_chandelier` default).

**Time stop:** at `MAX_BARS_IN_TRADE`, `MARKET`-close all remaining qty and cancel resting orders, regardless of P&L.

**Regime flip (ADX → trending):** block new entries; switch the open position to reduce-only management (keep TP + trailing, take no adds) via the existing pause-but-still-exit path.

**Resilience note:** the TP1 `LIMIT_MAKER` and the `STOP_MARKET` rest on the exchange so they fill even if the bot process is down. The chandelier amendment is engine-driven, so a process outage freezes the trail at its last level (the resting stop still protects). Reconcile resting orders against own state on startup.

---

## 5. Stop-width sweep

The cost-as-a-fraction-of-risk is driven by stop width, because position size = `R ÷ stop_distance`. A tighter stop → larger notional → larger absolute cost against a fixed `R`. So stop width is a cost lever, not just a risk lever.

**Sweep `ATR_STOP_MULT` ∈ {1.0, 1.25, 1.5, 1.75, 2.0, 2.5}** (1.5 = current spec), jointly with entry mode {A, B, B′} → a 2-D grid.

For each cell, report:
- Position notional = `min(R ÷ stop_distance, max_notional_usdt)`
- Round-trip cost as a fraction of R (using the real fee tier)
- R:R to midline (TP1)
- Realized win rate, trade count, net expectancy/trade, max drawdown

**Interaction to model:** below some stop width the position hits `max_notional_usdt` and decouples from stop distance — so cost-fraction stops improving past that point. The sweep will show a sweet spot balancing invalidation tightness, R:R, and cost drag.

**Objective:** maximize net expectancy/trade subject to (a) trade count ≥ a usable minimum and (b) max drawdown ≤ the circuit-breaker limit.

---

## 6. Backtest cost model (must be honest)

Parameterize every cost; default to the **account's actual tier**:

| Parameter | Default (std Binance.com) | Notes |
|-----------|---------------------------|-------|
| `maker_fee` | 0.10% (0.075% if paying in BNB) | **equals taker on std tier** |
| `taker_fee` | 0.10% (0.075% if paying in BNB) | 0.095% on some USDC taker pairs |
| `entry_slippage_bps` | 3–5 bps | higher — entering into a volatile extreme |
| `exit_slippage_bps` | 2–4 bps | taker exits (SL / chandelier / forced) |
| `maker_slippage_bps` | 0 | maker legs get their price or better |

**Hard rule:** do not set `maker_fee < taker_fee` unless the account is verified VIP or Binance.US. The whole maker-entry question collapses to "does avoided slippage beat lost fills" on a standard tier.

---

## 7. Decision rule

1. **Always adopt the maker midline TP** (Section 4 leg 1) — it saves slippage at zero strategy cost, regardless of tier.
2. **Keep the SL, chandelier, and forced exits taker** — non-negotiable.
3. **Adopt maker entry (Mode B/B′) only if** the backtest, run with the real fee tier, shows its net expectancy/trade beating Mode A *and* trade count remaining usable. On a standard tier this is a high bar.
4. **Fund the bot only if** the best-cell net expectancy clears breakeven *after* these costs (per the requirements-doc validation gate). A negative-expectancy diversifier is a bleed, not a hedge.

---

## 8. New / changed parameters introduced here

| Parameter | Default | Section |
|-----------|---------|---------|
| `ENTRY_ORDER_MODE` | `A` (taker) until backtest says otherwise | 3 |
| `ENTRY_FILL_TIMEOUT` | 1–2 LTF bars | 3 |
| `ENTRY_TIMEOUT_POLICY` | `abandon` (Mode B) / `escalate_market` (Mode B′) | 3 |
| `RUNNER_SL_TO_BREAKEVEN` | false | 4 |
| `maker_fee` / `taker_fee` / slippage bps | per real tier | 6 |
| `ATR_STOP_MULT` sweep range | {1.0 … 2.5} | 5 |

---

*Strategy/engineering specification, not trading advice. Backtest with the real fee tier before allocating capital.*