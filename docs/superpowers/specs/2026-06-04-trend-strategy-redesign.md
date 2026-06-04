# Trend Strategy Redesign — 5-Layer Pipeline

**Date:** 2026-06-04
**Status:** Approved

## Problem

The current trend strategy has three structural flaws:

1. **No ADX gate** — cannot distinguish "flat/choppy" from "strong downtrend." Both show as 0/3 WAITING, so the bot sits idle during the best trending moves.
2. **Correlated inputs** — EMA cross, trend filter, and RSI all key off price-vs-average. Effectively one signal counted three times. No volume/participation check, no momentum confirmation.
3. **Long-only entry logic** — only asks "is this a good long?" In downtrends the honest answer is "no," and it sits out every down-move. Worse, it cannot use the downtrend signal to *exit* existing longs or *block* new entries.

## Design: 5-Layer Decision Pipeline

### Layer 1 — GATE (trend exists?)

```
trend_exists = (ADX_14 > 25) AND (Choppiness_Index_14 < 38)
```

If the gate fails → WAITING state, no entry evaluation. The bot knows the difference between "no trend" (ranging, safe to wait) and "trend exists" (proceed to direction).

### Layer 2 — DIRECTION (+1 / -1 / 0)

```
dir = +1  if  EMA_fast > EMA_slow  AND  close > EMA_slow
     -1  if  EMA_fast < EMA_slow  AND  close < EMA_slow
      0  otherwise (mixed signals → no trade)
```

Direction is the *only* long vs short decision point. All downstream logic uses `dir`.

**Spot long-only rule:** `dir == -1` does NOT open short positions. Instead:
- If already in a long → **exit signal** (dir flipped against position)
- If flat → **block new longs** (sit in cash until direction turns +1)

This gives the bot half the benefit of short-awareness (exit downtrends, don't fight them) without needing futures/margin infrastructure.

### Layer 3 — SCORE (0–3 confirmations, volume mandatory)

Each confirmation is independent and confirms the direction:

```
S1 (momentum):    sign(MACD_histogram) == dir
S2 (participation): volume_ratio > 1.2       // MANDATORY — best fake-breakout filter
S3 (entry timing):  if dir=+1 → RSI_14 < 65   // not chasing overbought
                    if dir=-1 → RSI_14 > 35   // not chasing oversold
                    (only used when actively trading that direction)
```

**Why volume is mandatory:** S1 (MACD histogram) is partially correlated with direction (both are EMA-derived). While the histogram measures *acceleration* not just alignment, when EMA says "up," MACD is usually positive too. Without mandatory volume, `S1 + S3` can trigger entry on below-average participation — precisely the fake breakout this bot should avoid. Volume is the strongest independent confirmation available.

### Layer 4 — ACTIVATE

```
TREND_ACTIVE = trend_exists AND (dir == +1) AND S2 AND (S1 OR S3)
```

Entry requires: gate passes, direction is bullish, volume confirms participation, AND at least one other confirmation (momentum OR entry timing) agrees.

For `dir == -1`: no new entries (spot long-only). Only exit logic runs.

### Layer 5 — EXIT

Three independent exit triggers, any one fires:

```
exit if  ADX_14 < 20                        // trend dying
     OR dir flips (was +1, now -1 or 0)     // direction reversed
     OR price crosses ATR trailing stop      // stopped out

ATR trailing stop (for longs):
  trail = highest_close_since_entry - 3.0 * ATR_14
  trail only ratchets up, never down

Future short support (for when TRADE_SHORTS=true):
  trail = lowest_close_since_entry + 3.0 * ATR_14
  trail only ratchets down, never up
```

## New Indicator: MACD

**File:** `indicators/macd.rs` (NEW)

Standard MACD with EMA-based computation:
- `Macd::new(fast: u32, slow: u32, signal: u32)` — default (12, 26, 9)
- `update(price: f64)` — feeds close price
- `macd_line() -> f64` — fast EMA minus slow EMA
- `signal_line() -> f64` — EMA of MACD line
- `histogram() -> f64` — MACD line minus signal line (the momentum indicator)
- `is_initialized() -> bool` — true after `slow + signal` periods (~35 for 12/26/9)

**Warm-up note:** The MACD histogram is not stable until the signal line (a 9-period EMA of the MACD line) has had enough data. The MACD line itself needs `slow` (26) bars, then the signal line needs ~9 more bars to stabilize. Tie `is_initialized()` to bar count >= `slow + signal`, NOT just `slow`. Returning `true` too early produces a half-baked histogram that fires false signals.

## Warm-Up Guard (All Indicators)

The pipeline adds 4 new indicators with different warm-up periods:

| Indicator | Bars to initialize |
|---|---|
| ADX (period=14) | 29 bars |
| Choppiness (period=14) | 14 bars |
| MACD (12/26/9) | ~35 bars |
| VolumeSma (period=20) | 20 bars |

**Global readiness gate:** Before all indicators report `is_initialized()`, the pipeline holds the bot in WAITING state. Pre-warmup MUST default to WAITING, never to a state that permits entry (same trap as the grid's "unknown → Ranging" bug). The `indicators_ready()` method checks ALL indicators:

```rust
fn indicators_ready(&self) -> bool {
    self.ema_fast.is_initialized()
        && self.ema_slow.is_initialized()
        && self.adx.is_initialized()
        && self.choppiness.is_initialized()
        && self.macd.is_initialized()
        && self.rsi.is_initialized()
        && self.atr.is_initialized()
        && self.volume_sma.is_initialized()
}
```

## Indicator Math Verification (Rust vs Python)

Confirmed that Rust indicators match Python pandas_ta conventions:

| Indicator | Rust implementation | Match |
|---|---|---|
| ADX | Wilder seeding (simple sums first `period` bars), then `prev * (period-1) + new` smoothing. ADX itself uses `(prev * (period-1) + dx) / period`. Same as pandas_ta. | ✅ |
| Choppiness | Rolling window recomputation (NOT smoothed). Formula `100 * log10(sum_TR / (HH-LL)) / log10(period)`. Exact match to Python `100 * np.log10(atr_sum / (hh - ll)) / np.log10(period)`. | ✅ |
| ATR | Used raw (not NATR) for trailing stops. Consistent between Rust and Python. | ✅ |

## What Gets Removed

| Current component | Reason |
|---|---|
| `ema_trend` (200-period EMA) | Replaced by ADX + Choppiness for trend detection |
| `trend_filter` signal | Correlated with EMA cross — replaced by ADX gate |
| `at_support` signal (+2) | S/R is range-bound logic, inappropriate for trend following |
| `candlestick` pattern detection | Low signal-to-noise for trend following |
| `confirm_count` / `confirmation_ticks` | Replaced by score-based system |

## What Gets Added

| Component | File |
|---|---|
| `Macd` indicator | `indicators/macd.rs` (NEW) |
| `Adx` indicator (existing) | Added to `TrendStrategy` fields |
| `Choppiness` indicator (existing) | Added to `TrendStrategy` fields |
| `VolumeSma` indicator (existing) | Added to `TrendStrategy` fields |
| `highest_since_entry` / `lowest_since_entry` tracking | Added to `TrendPosition` |

## Files Changed

| File | Change |
|---|---|
| `indicators/macd.rs` | NEW — MACD(12, 26, 9) indicator |
| `indicators/mod.rs` | Register `pub mod macd;` |
| `strategy/trend.rs` | Rewrite: 5-layer pipeline replaces evaluate_signals + should_enter/should_exit |
| `config.rs` TrendConfig | Add `adx_threshold`, `choppiness_threshold`, `volume_ratio_threshold`; remove obsolete fields |

## Direction-Aware Status Reporting

The `status()` method now shows the 5-layer state clearly:

```
WAITING | ADX: 18.2❌ Chop: 45.3✅ | dir: +1 | S: S1❌ S2❌ S3✅ | No trend gate
WAITING | ADX: 32.1✅ Chop: 29.8✅ | dir: -1 | S: S1✅ S2✅ S3✅ | dir=-1 blocks longs
WAITING | ADX: 31.5✅ Chop: 34.2✅ | dir: +1 | S: S1✅ S2❌ S3✅ | No volume (S2 mandatory)
POSITION LONG 0.5 BTC @ $68,400 | Trail: $66,200 | ADX: 41.3✅ | dir: +1
```

## Constants / Defaults

| Parameter | Value | Notes |
|---|---|---|
| ADX gate threshold | 25.0 | Standard, >25 = trend exists |
| ADX exit threshold | 20.0 | Trend dying |
| Choppiness gate threshold | 38.0 | <38 = trending |
| MACD params | 12, 26, 9 | Industry standard |
| Volume ratio threshold | 1.2 | 20% above average |
| RSI chase guard (long) | < 65 | Don't buy overbought |
| ATR trailing multiplier | 3.0 | Chandelier exit |
| Minimum score to activate | S2 AND (S1 OR S3) | Volume mandatory + one other |
| `TRADE_SHORTS` | `false` | Flip when futures ready |
