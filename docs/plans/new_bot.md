# Reversal / Swing Bot — Requirements Document

**Bot name:** Reversal Swing Bot (working title)
**Purpose:** Catch confirmed reversals at swing extremes — buy the bounce off a bottom, sell into a top (exiting long) — in range-bound markets only.
**Version:** 0.2 (Approved Spec)
**Status:** Approved Specification, ready for development

---

## 1. Purpose & scope

### 1.1 Goal
A specialized, low-frequency bot that enters **counter-trend** at range extremes after a reversal is *confirmed*, not predicted. It complements the existing bot suite rather than overlapping it.

### 1.2 In scope
- Detecting exhaustion + reversal at the edges of a trading range.
- Entering long at confirmed bounces (range lows).
- Managing the position with a hard invalidation stop and partial profit-taking.
- Self-disabling when the market is trending.

### 1.3 Out of scope
- **Shorting / entering short positions:** Because the production environment is Binance Spot, shorting is completely out of scope. "Selling the top" collapses strictly into exiting/scaling out of an existing long position. The bot runs as a long-only bounce-buyer.
- Trend-following (handled by the existing [TrendStrategy](file:///Users/amro/WebstormProjects/trading-humming-bot/trading-engine-core/src/strategy/trend.rs)).
- Continuous in-range oscillation harvesting (handled by the [GridStrategy](file:///Users/amro/WebstormProjects/trading-humming-bot/trading-engine-core/src/strategy/grid.rs)).
- High-frequency fading of small deviations from a fair value (handled by the `MeanReversionStrategy`).

### 1.4 Relationship to existing bots
This bot is one of four sub-strategies coordinated by market regime. Only one regime-appropriate strategy should hold directional risk on the same instrument at a time.

| Bot | Active regime | Trade frequency | Target size |
|-----|---------------|-----------------|-------------|
| Trend bot | Trending | Medium | Large |
| Grid bot | Tight range / oscillating | High | Small |
| Mean-reversion bot | Noisy around fair value | High | Small |
| **Reversal swing bot (this)** | **Ranging, at extremes** | **Low** | **Medium–large** |

---

## 2. Core principle

> Do not catch the falling knife. Wait for the bounce to **prove itself** through confluence, then enter with a tight invalidation.

The edge comes from:
1. Only acting in the correct regime (ranging with clear boundaries).
2. Requiring hard, non-negotiable entry gates (location, volume confirmation, candle pattern).
3. A confirmation score booster (RSI, MACD, divergence) to filter out weak setups.
4. Asymmetric risk/reward enforced at entry.

---

## 3. Required indicators

Signals are stacked; the bot acts only when the required set agrees.

| # | Indicator | Role in logic | Timeframe | Implementation Detail |
|---|-----------|---------------|-----------|-----------------------|
| 1 | **Donchian Channel** | Defines the range. Lower band = buy zone. | Higher Timeframe (HTF) | O(1) monotonic deque for rolling min/max lookback. |
| 2 | **RSI** | Oversold and divergence vs price. | Higher Timeframe (HTF) | Score booster. |
| 3 | **MACD** | Histogram flattening or signal-line cross. | Higher Timeframe (HTF) | Score booster. |
| 4 | **Candlestick Reversal** | Hammer or bullish engulfing at the band. | Lower Timeframe (LTF) | Hard Gate (trigger). |
| 5 | **Volume** | Reversal on above-average volume. | Lower Timeframe (LTF) | Hard Gate. LTF Vol >= 1.5x average. |
| 6 | **ATR** | Volatility-based stop placement and target sizing. | Lower Timeframe (LTF) | Risk sizing. |
| 7 | **ADX** | Trend strength. Master switch with hysteresis. | Higher Timeframe (HTF) | Regime gate (prevents repainting). |

---

## 4. Logic flow

### 4.1 Multi-Timeframe (MTF) & Hysteresis Rules
- **Anti-Repainting Guard**: All Higher Timeframe (HTF) indicators (Donchian Channel, ADX, MACD, RSI) **must** be calculated using only *completed* HTF candles. The current in-progress HTF candle is ignored to prevent backtest-vs-live divergence.
- **ADX Hysteresis Gate**:
  - Enable Ranging Mode when `ADX < 22`.
  - Flip to Trending Mode (stand down) when `ADX > 28`.
  - The zone between `22` and `28` acts as a dead zone to prevent thrashing.
- **Location definition**: Lower Timeframe (LTF) price is considered "at the lower band" if `LTF Close <= HTF Lower Band + (BAND_ATR_MULT × LTF ATR)`.

### 4.2 Entry Logic (Long-Only)
An entry requires all **Hard Gates** to be satisfied, and the **Confirmation Score** to meet or exceed the threshold.

#### Hard Gates (Non-Negotiable)
1. **Location**: LTF Close is within `BAND_ATR_MULT × LTF ATR` of the completed HTF Lower Donchian Band.
2. **Trigger Candle**: A bullish reversal candle prints on the LTF (Hammer or Bullish Engulfing).
3. **Volume Confirmation**: LTF Trigger Candle volume `>= VOLUME_MULTIPLIER × LTF average volume` (default 1.5×).

#### Confirmation Score (Need >= 2 Points)
- **RSI Oversold**: HTF RSI is `< 30` (+1 point).
- **RSI Divergence**: HTF price printed a lower low but HTF RSI printed a higher low (+1 point).
- **MACD Turning**: HTF MACD histogram ticked up or crossed above the signal line (+1 point).

### 4.3 Position Management
- **Stop Loss**: Placed beyond the reversal extreme (the bounce low) at a distance of `ATR_STOP_MULT × LTF ATR` (default 1.5×).
- **Reward-to-Risk Minimum**: Reject any setup where the distance from entry to the HTF Middle Band (first target) gives less than `MIN_RR` (default 2.0) reward-to-risk.
- **Scale-out**: Take 50% profit at the HTF midline / fair value. Trail the remainder with a Chandelier Exit (ATR trailing stop).
- **Regime Flip Grace**: If the ADX gate flips to "Trending" while in a position, the bot is disabled from taking *new* entries but **must** continue to manage and close its existing open position via stop loss and targets.

---

## 5. Configurable parameters

| Parameter | Default | Notes |
|-----------|---------|-------|
| `HTF_PERIOD` | 1h | Higher Timeframe for channel & regime |
| `LTF_PERIOD` | 5m | Lower Timeframe for execution trigger |
| `ADX_RANGE_ENTRY` | 22 | Below this = ranging mode enabled |
| `ADX_TREND_EXIT` | 28 | Above this = trending mode enabled |
| `DONCHIAN_PERIOD` | 20 | Lookback for HTF channel |
| `BAND_ATR_MULT` | 0.5 | Tolerance multiplier to define "at the band" |
| `RSI_PERIOD` | 14 | HTF RSI lookback |
| `RSI_OVERSOLD` | 30 | Level below which RSI adds +1 point |
| `VOLUME_MULTIPLIER` | 1.5 | LTF Trigger volume vs average volume |
| `VOLUME_AVG_PERIOD` | 20 | Lookback for LTF average volume |
| `ATR_PERIOD` | 14 | LTF ATR lookback |
| `ATR_STOP_MULT` | 1.5 | Stop distance multiplier |
| `MIN_RR` | 2.0 | Minimum reward:risk to take a trade |
| `RISK_PER_TRADE` | 1.0% | Conservative initial account risk per position |
| `MAX_BARS_IN_TRADE` | 48 (LTF) | Time stop |

---

## 6. Risk management requirements

- Position size derived from `RISK_PER_TRADE` (1.0%) and the stop distance (fixed-fractional sizing).
- One open position per instrument from this bot at a time.
- Disabled bot must always be allowed to manage and close its own open positions (reduce-only orders bypass the halt gate).
- Daily consecutive-loss circuit breaker: halts the bot for the day if 2 consecutive stop-outs occur.
- No averaging down / no adding to losers.

---

## 7. Data & infrastructure requirements

- Real-time and historical OHLCV data for both HTF and LTF timeframes.
- Volume data (required — the bot cannot run without it).
- Reliable order execution with stop-order support.
- Persistence of bot state (open positions, parameters, kill-switch status).
- **SQLite Trade Journal**: Every trade, fill, exit reason, duration, and realized PnL must be logged to a SQLite database (`swing_journal.db`), matching the pattern of [TrendJournal](file:///Users/amro/WebstormProjects/trading-humming-bot/trading-engine-core/src/strategy/trend_journal.rs) and [GridJournal](file:///Users/amro/WebstormProjects/trading-humming-bot/trading-engine-core/src/strategy/grid_journal.rs).
- Decision logging for every LTF tick when in the buy zone, detailing which gates passed/failed.

---

## 8. Backtesting & validation requirements

Before live deployment, the bot must be validated for:
1. **Regime filter effectiveness** — confirm performance degrades sharply if the ADX gate is removed.
2. **Win rate vs reward:risk** — expect a lower win rate (~35-40%); profitability must come from high R:R.
3. **Out-of-sample testing** — separate tuning and validation periods to prevent overfitting.
4. **Slippage & fees** — model realistic execution costs; reversal entries near volatile extremes are slippage-prone.

---

## 9. Edge cases & failure modes

| Risk | Mitigation |
|------|-----------|
| Strong trend mistaken for a range | ADX regime gate with hysteresis |
| Catching a falling knife | Mandatory LTF candle + volume confirmation |
| Whipsaw at the band | `MIN_RR` filter + ATR stop beyond the extreme |
| Conflict with other bots | Single-strategy-per-regime coordinator |
| Low-volume fakeouts | Volume multiplier filter |
| Repainting bias | Strictly evaluate HTF indicators on completed bars |

---

## 10. Design Decisions & Resolutions (v0.2)

- **Long-Only Constraint**: Formally restricted to long entries on Binance Spot. Rejection highs/shorting are eliminated.
- **Scoring System**: Separated into hard gates (location + trigger candle + volume) and booster confirmations (RSI oversold, RSI divergence, MACD turn).
- **Handoff & Safety**: A paused/disabled bot is fully allowed to run its exit/reduce-only loop to close out active trades. ADX hysteresis thresholds (22 / 28) protect against trend thrashing.
- **Donchian Channel**: Must be built as an efficient rolling min/max queue (monotonic deque) inside `trading-engine-core/src/indicators/`.