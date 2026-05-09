# Trading Strategy — TA-Enhanced BTC/USDT Grid Bot

## Overview

The bot runs a **dynamic grid trading** strategy on BTC/USDT. Unlike static grids that use fixed price levels, this strategy recalculates grid boundaries every hour using four technical indicators. The grid shifts to follow the market, pauses when conditions are unfavorable, and re-enters on oversold bounces.

**Pair:** BTC/USDT
**Timeframe:** 1-hour candles (fetches every 55 minutes)
**Exchange:** Binance (paper trading or live)

---

## How Grid Trading Works

Grid trading profits from price oscillation within a range. The bot places limit buy orders below the current price and limit sell orders above it. Each time price bounces between levels, a round-trip completes:

```
Sell 6 @ $103,500  ─┐
Sell 5 @ $103,000   │  Sell zone (above mid)
Sell 4 @ $102,500   │
Sell 3 @ $102,000   │
Sell 2 @ $101,500   │
Sell 1 @ $101,000   │
                    │
═══════ $100,000 ═══╪════  Mid price (Bollinger SMA)
                    │
Buy  1 @ $99,000    │
Buy  2 @ $98,000    │
Buy  3 @ $97,000    │  Buy zone (below mid)
Buy  4 @ $96,000    │
Buy  5 @ $95,000    │
Buy  6 @ $94,000   ─┘

RSI > 60 → skip buy levels  |  RSI < 40 → skip sell levels
When price drops to $98,000 → BUY fills
When price bounces to $102,000 → SELL fills
Profit = ($102,000 - $98,000) × quantity - fees
```

Each grid level is spaced by **ATR × 1.5**, which adapts to current volatility. In calm markets, levels are closer together (more fills, smaller profit per trade). In volatile markets, levels spread apart (fewer fills, larger profit per trade). The 1.5 multiplier ensures each round-trip captures enough spread to comfortably beat exchange fees.

---

## Technical Indicators

### 1. Bollinger Bands (20, 2.0 std dev)

```
Upper = SMA(20) + 2 × σ
Mid   = SMA(20)
Lower = SMA(20) - 2 × σ
```

**Purpose:** Defines the grid's operating range.

- Buy levels are placed between Mid and Lower Band
- Sell levels are placed between Mid and Upper Band
- No orders are placed outside the bands

The bands expand during high volatility (wider grid) and contract during low volatility (tighter grid). This naturally adapts order spacing to market conditions.

### 2. RSI (14-period, Wilder's smoothing)

```
RS = Average Gain(14) / Average Loss(14)
RSI = 100 - (100 / (1 + RS))
```

**Purpose:** Momentum filter — controls grid state transitions.

| RSI Value | Action |
|-----------|--------|
| Above 70 | Grid **PAUSED** — market is overbought, likely to pull back |
| Below 35 | Grid **REACTIVATING** — market is oversold, bounce expected |
| 35–70 | Grid **ACTIVE** — normal trading conditions |

RSI prevents the grid from placing buy orders in a euphoric market (where price is likely to reverse down) and triggers re-entry after a selloff.

### 3. EMA 200 (Exponential Moving Average)

```
EMA = price × (2/201) + EMA_prev × (1 - 2/201)
```

**Purpose:** Trend filter — only runs the grid in an uptrend.

| Condition | Action |
|-----------|--------|
| Price above EMA200 | Grid allowed — market is in an uptrend |
| Price below EMA200 | Grid **PAUSED** — market is bearish, avoid buying dips in a downtrend |

This is the primary safety filter. BTC below EMA200 typically means extended bearish conditions where buying the dip is dangerous.

### 4. ATR (14-period, Average True Range)

```
TR  = max(High - Low, |High - PrevClose|, |Low - PrevClose|)
ATR = EMA(TR, 14)
```

**Purpose:** Sets grid spacing.

```
Grid Spacing = ATR × 1.5
```

ATR measures how much BTC moves in a typical hour. With a 1.5 multiplier, the grid is wider than the average move, ensuring each round-trip captures enough spread to beat exchange fees. Fewer but more profitable trades.

---

## Grid State Machine

The bot operates in three states with deterministic transitions:

```
                     RSI > 70 OR Price < EMA200
       ACTIVE  ──────────────────────────────────────►  PAUSED
          ▲                                               │
          │               RSI < 35 AND Price ≤ BB Lower × 1.02
          │   REACTIVATING ◄──────────────────────────────┘
          │       │
          └───────┘  RSI normalizes AND Price > EMA200
```

### ACTIVE
- Full grid deployed — buy and sell orders placed
- Profits from price oscillation within Bollinger range
- This is the normal operating state

### PAUSED
- All orders cancelled, holding USDT
- Triggers: RSI overbought (>70) or price below EMA200
- Bot waits for re-entry signal

### REACTIVATING
- Transitional state — re-entering after oversold conditions
- Triggers: RSI < 35 AND price near lower Bollinger Band
- Grid redeployed at new (lower) levels
- Returns to ACTIVE once conditions normalize

---

## Order Execution

### Grid Calculation
```
For each level i (1 to 6):
  Buy price  = BB Mid - (ATR × 1.5 × i)   [clamped to BB Lower]
  Sell price = BB Mid + (ATR × 1.5 × i)   [clamped to BB Upper]

  Buy level skipped if RSI > 60 (overbought)
  Sell level skipped if RSI < 40 (oversold)

Order size = (Capital - Reserve) / (2 × Levels) / Price
```

### Order Lifecycle
1. Every tick (1 second), Hummingbot calls `on_tick()`
2. Every 55 minutes, fresh candles are fetched and indicators recalculated
3. If indicators changed, the grid is marked dirty
4. Old orders are cancelled, new grid orders placed
5. Hummingbot's connector handles order matching on the exchange

### Round-Trip Tracking
- Each BUY fill is stored with its indicator snapshot
- When a SELL fills, the bot matches it to the corresponding BUY by grid level
- If no exact level match, the oldest open BUY is used (FIFO)
- Round-trip P&L = (Sell Price - Buy Price) × Quantity - Fees

---

## Risk Management

### Circuit Breaker
Halts all trading when portfolio drawdown exceeds safe limits.

| Condition | Threshold | Action |
|-----------|-----------|--------|
| Peak-to-current drawdown | 10% | Halt + cancel all orders |
| Daily loss from midnight | 5% | Halt + cancel all orders |

Once halted, the bot stays stopped until manually reset via Telegram `/reset`. This prevents catastrophic losses during flash crashes or unexpected events.

### Position Guard
Blocks individual orders that would create dangerous exposure.

| Rule | Default | Purpose |
|------|---------|---------|
| Max BTC exposure | 80% of equity | Don't go all-in on BTC |
| Min USDT reserve | $50 (configurable) | Always keep dry powder |

Each order is checked before placement. If it would violate either limit, the order is blocked and logged.

---

## Configuration

All parameters are in `config/strategy.yaml`:

```yaml
grid:
  levels: 6               # Orders per side (buy + sell)
  capital_usdt: 1000      # USDT allocated
  min_usdt_reserve: 100   # Minimum USDT to keep
  order_refresh_time: 60  # Seconds between order refresh

indicators:
  bollinger:
    period: 20
    std_dev: 2.0
  rsi:
    period: 14
    oversold: 35           # Reactivation threshold
    overbought: 70         # Pause threshold
  ema:
    period: 200            # Trend filter
  atr:
    period: 14
    spacing_multiplier: 1.5  # Grid spacing = ATR × this

risk:
  max_drawdown_pct: 10     # Circuit breaker: peak drawdown
  daily_loss_limit_pct: 5  # Circuit breaker: daily loss
  max_btc_exposure_pct: 80 # Position guard: max BTC allocation
```

Environment variables override config values:
- `GRID_LEVELS`, `GRID_CAPITAL_USDT`, `MIN_USDT_RESERVE`
- `MAX_DRAWDOWN_PCT`, `MAX_BTC_EXPOSURE_PCT`
- `ENV` (paper/live)

---

## Auto-Compound & Capital Management

### Auto-Compound

The bot automatically reinvests profits by scaling order sizes based on equity growth:

```
compound_capital = base_capital × (current_equity / initial_equity)
```

- **At startup:** base capital captured, initial equity snapshot taken
- **On each grid refresh:** growth ratio calculated, compound capital updated
- **Floor protected:** never goes below your original base capital
- **Paper trading safe:** growth ratio normalizes regardless of starting balance ($180K paper vs $1K live)

### Updating Capital

No redeploy needed. Change grid capital directly from Telegram:

```
/capital 5000       → sets grid to $5,000
/capital 25000      → sets grid to $25,000
```

Order sizes recalculate on the next grid refresh (within 1 hour).

---

## Monitoring

### Telegram Alerts (automatic)
- Bot startup/shutdown with mode and capital
- Grid state changes with indicator values and trigger reason
- Trade fills with full P&L breakdown
- Daily P&L report auto-sent at midnight UTC (trades, win rate, P&L, fees, equity)
- Crash and error alerts with traceback

### Telegram Commands
| Command | Description |
|---------|-------------|
| `/status` | Grid state, mode, uptime, levels, spacing, capital |
| `/pnl` | P&L summary — today, week, month, all-time with win rates |
| `/balance` | USDT, BTC, equity, and grid capital with growth % |
| `/capital <amount>` | Update grid capital on the fly (no redeploy needed) |
| `/price` | Live BTC/USDT price from Binance + RSI, EMA, Bollinger Bands |
| `/trades` | Last 5 closed trades |
| `/pending` | Open orders with prices and amounts |
| `/fees` | Total fees paid today / this week / this month |
| `/system` | CPU, memory, disk usage |
| `/errors` | Recent errors from crash log |
| `/logs` | Last 30 lines from today's bot log |
| `/pause` | Manually pause grid trading |
| `/resume` | Resume grid from manual pause |
| `/reset` | Reset circuit breaker after halt |
| `/clear` | Clear logs and grid state (preserves trade history) |
| `/help` | Command reference |

### Log Files
All logs persist in `./logs/` (Docker volume mount):

| File | Contents |
|------|----------|
| `bot_YYYY-MM-DD.log` | Full bot log (INFO+), daily rotation |
| `crashes.log` | Errors only (ERROR+), quick debugging |
| `events_YYYY-MM-DD.jsonl` | Structured events for analysis |

---

## Expected Behavior

**Normal market (ranging):**
- Grid places 6 buy + 6 sell levels within Bollinger Bands
- Price oscillates → fills occur → round-trip P&L captured
- Grid recalculates hourly, shifting with the market

**Rising market (uptrend):**
- Sell levels fill as price rises
- Grid shifts upward on recalculation
- New sell levels placed above, buy levels moved up
- Bot captures upside through sequential sell fills

**Overbought (RSI > 70):**
- Grid pauses, all orders cancelled
- Holds USDT until RSI normalizes
- Prevents buying at the top

**Bearish (price < EMA200):**
- Grid pauses immediately
- No buy orders in a downtrend
- Waits for price to recover above EMA200

**Oversold bounce (RSI < 35 + near BB Lower):**
- Grid reactivates at lower levels
- Buys near the bottom of the range
- Captures the bounce as price recovers

**Flash crash (drawdown > 10%):**
- Circuit breaker halts everything
- Requires manual `/reset` via Telegram
- Prevents catastrophic losses
