# Trend-Following Engine — Original Requirements

Reference spec used to design the trend engine. Captures what was adopted vs deferred.

---

## Market & Mode

| Requirement | Status |
|---|---|
| Exchange: Binance (spot) | **Adopted** — spot only, no futures |
| Pairs: SOL-USDT | **Adopted** — single pair, configurable |
| Timeframe: 1h candles | **Adopted** — configurable via strategy.yaml |
| Mode: Paper trading default | **Adopted** — ENV=paper, toggle to live |

---

## Signal Scoring System

**Adopted:** Bot scores bull signals. Trade opens only when bull score >= 3.

| Signal | Points | Status |
|---|---|---|
| Support/Resistance hit | +2 | **Adopted** |
| Candlestick pattern | +2 | **Adopted** (5 bullish patterns) |
| Trendline breakout | +2 | **Deferred** — complex, noisy on 1h |
| Moving average signal | +1 | **Adopted** (EMA cross + trend filter) |
| RSI confirmation | +1 | **Adopted** (added beyond original spec) |
| Maximum possible score | 7 | **Adopted** |

---

## Indicators

### Support/Resistance (Adopted)
- Scan last 50 candles for swing highs and swing lows
- Cluster nearby pivots within 0.5% into one level
- Valid only if touched >= 2 times
- Strength score based on touches
- Signals: "At support" -> bull +2

### Candlestick Patterns (Adopted — subset)
**Implemented (5 patterns):**
- Single: Hammer, Bullish Marubozu
- Double: Bullish Engulfing, Bullish Harami
- Triple: Morning Star

**Deferred (expand later):**
- Dragonfly Doji, Inverted Hammer, Tweezer Bottom, Piercing Line, Three White Soldiers
- All bearish patterns (long-only for now)

### Trendlines & Breakouts (Deferred)
- Linear regression trendlines with R² filter
- Complex to implement correctly on 1h candles
- Can add in v2 if backtesting shows value

### Moving Averages (Adopted)
- EMA 20 / EMA 50 / EMA 200
- Golden cross (EMA20 > EMA50): +1
- Trend filter (price > EMA200 AND EMA20 > EMA50): +1

---

## Entry Logic

| Requirement | Status |
|---|---|
| LONG when bull_score >= 3 | **Adopted** |
| SHORT signals | **Not adopted** — spot only, no shorting |
| 2-tick confirmation | **Adopted** — filters noise |
| Max 2 open positions | **Adopted** (reduced from 3) |

---

## Stop Loss & Take Profit

| Requirement | Status |
|---|---|
| SL below nearest support - 0.2% buffer | **Adopted** |
| TP = entry + risk * 2.0 (2:1 R:R) | **Adopted** |
| Trailing stop: 1.5% | **Adopted** — activates after +1.5% profit |
| ATR fallback for SL | **Adopted** — added beyond original spec |

---

## Risk Management

| Requirement | Status |
|---|---|
| 2% risk per trade | **Adopted** |
| Max 25% per position | **Adopted** |
| Max drawdown 10% -> halt | **Adopted** |
| Daily loss limit 5% | **Adopted** |

---

## Output & Logging

| Requirement | Status |
|---|---|
| Log every signal with score, entry, SL, TP | **Adopted** — trend_journal.db |
| Log every close with P&L | **Adopted** |
| Performance summary (win rate, profit factor) | **Adopted** — /trend_pnl command |
| Telegram notifications | **Adopted** — /trend_* commands |

---

## Tech Stack

| Requirement | Actual |
|---|---|
| Python | **Adopted** |
| ccxt (binance) | Using Hummingbot connector instead |
| pandas DataFrame | **Adopted** |
| Config file | strategy.yaml (YAML) |
| Paper trading | Hummingbot paper trade mode |
| Run loop every 60s | Every 55 ticks (~55s) via on_tick() |

---

## What Was Added Beyond Original Spec

- Dual-engine architecture (grid + trend in one container)
- Separate capital pools with Telegram control
- Circuit breaker integration
- State persistence (trend_state.json)
- 7-point signal scoring with confirmation ticks
- Support-based smart stop-loss placement
