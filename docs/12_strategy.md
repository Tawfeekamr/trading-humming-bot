# Trading Strategy — TA-Enhanced Multi-Pair Grid + Trend Bot

## Overview

The bot runs a **dual-engine strategy** — a dynamic grid engine and a trend engine — across multiple trading pairs. Each pair has its own ML regime classifier that determines which engine is active and how capital is allocated.

**Pairs:** BTC-USDT, ETH-USDT, BNB-USDT, DOGE-USDT, XRP-USDT
**Timeframe:** 1-hour candles (fetches every 55 minutes)
**Exchange:** Binance (paper trading or live)

---

## How Grid Trading Works

Grid trading profits from price oscillation within a range. The bot places limit buy orders below the current price and limit sell orders above it. Each time price bounces between levels, a round-trip completes:

```
Sell 5 @ $103,500  ─┐
Sell 4 @ $103,000   │  Sell zone (uniform spacing)
Sell 3 @ $102,500   │
Sell 2 @ $102,000   │
Sell 1 @ $101,500   │
                    │
═══════ $100,000 ═══╪════  Mid price (Bollinger SMA)
                    │
Buy  1 @ $99,000    │
Buy  2 @ $97,800    │
Buy  3 @ $96,380    │  Buy zone (geometric spacing — wider)
Buy  4 @ $94,718    │
Buy  5 @ $92,890   ─┘

RSI > 60 → skip buy levels  |  RSI < 40 → skip sell levels
When price drops to $97,800 → BUY fills
When price bounces to $102,000 → SELL fills
Profit = ($102,000 - $97,800) × quantity - fees
```

Each grid level is spaced by **ATR × 1.5**, which adapts to current volatility. **Buy-side levels use geometric spacing** (`spacing × (1.1)^level`), spreading wider during dips to pull the breakeven price down faster. Sell-side levels use uniform spacing. The 1.5 multiplier ensures each round-trip captures enough spread to comfortably beat exchange fees.

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

The bot operates in four states with deterministic transitions:

```
                     RSI > 70 OR Price < EMA200
       ACTIVE  ──────────────────────────────────────►  PAUSED
          ▲                                               │
          │               RSI < 35 AND Price ≤ BB Lower × 1.02
          │   REACTIVATING ◄──────────────────────────────┘
          │       │
          └───────┘  RSI normalizes AND Price > EMA200

       ACTIVE  ──────────────────────────────────────►  DANGER
                                                         │
                       ML regime no longer DANGER  ◄─────┘
```

### ML Regime Classifier
Each pair has its own Random Forest model that classifies the market regime:
- **RANGING**: Grid engine active at full capital, trend engine idle
- **TRENDING**: Grid capital reduced, trend engine takes directional trades
- **DANGER**: All trading paused — grid enters DANGER state

### Cross-Asset Correlation Gate
BTC-USDT serves as a systemic risk indicator for all altcoin pairs. When BTC enters DANGER regime:
- All altcoin (non-BTC) buy-side grid orders are immediately skipped
- All altcoin trend engine entries are blocked
- Sell orders continue unaffected -- the bot can still exit positions during a selloff
- Gate transitions trigger Telegram alerts

BTC-USDT candle data is always fetched (even when BTC trading is disabled) via a dedicated CandleFeed. If the BTC model fails to load or predict, the gate defaults to safe mode (halt altcoin buys) to prevent unprotected trading.

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
For each level i (1 to 5):
  Buy price  = BB Mid - (ATR × 1.5 × (1 + 0.10)^(i-1))   [clamped to BB Lower]
  Sell price = BB Mid + (ATR × 1.5 × i)                    [clamped to BB Upper]

  Buy level skipped if RSI > 60 (overbought)
  Sell level skipped if RSI < 40 (oversold)

Buy size  = Base Size × (1 + 0.08)^(i-1)   (geometric scaling)
Sell size = Base Size                        (uniform)

Grid capital scaled by ML regime:
  RANGING  → 100% capital allocation
  TRENDING → 60% grid capital, 40% trend capital
  DANGER   → 0% (all paused)
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
| Max base asset exposure | 80% of equity | Don't go all-in on any asset |
| Min USDT reserve | $50 (configurable) | Always keep dry powder |

Each order is checked before placement. If it would violate either limit, the order is blocked and logged.

### Confidence-Weighted Position Sizing (Trend Engine)

Trend positions use ML confidence to scale risk:
```
risk_pct = 0.5% + (confidence × 2.0%), clamped to [0.5%, 3.0%]
position_size = (equity × risk_pct) / entry_price
```
Higher ML confidence → larger position size. Low confidence trades are small and conservative.

---

## Fee Optimization

### BNB Rebalancer
The bot maintains a BNB balance for the 25% trading fee discount on Binance:
- Target: ~$20 worth of BNB (covers ~2 weeks of grid trading fees)
- Buy $20 BNB when balance drops below $10
- Sell excess BNB when balance exceeds $50
- Runs every indicator refresh cycle (55 min), only when grid is ACTIVE
- Configurable via `fee_optimization` in strategy.yaml

### LIMIT_MAKER Orders
All orders use `OrderType.LIMIT_MAKER` (post-only):
- Exchange rejects the order if it would cross the spread (take liquidity)
- Guarantees maker fee rate: 0.075% with BNB discount
- Rejected orders automatically retry on the next tick

---

## Trend Engine

The trend engine runs alongside the grid engine and takes directional trades when ML confirms a trending regime.

### Entry Conditions
- ML regime classifier returns `TRENDING` with confidence > threshold
- RSI not overbought (< 70)
- Price above EMA200 (uptrend confirmed)

### Exit Mechanism
- **Trailing stop**: Locks in gains as price moves favorably
- **Regime change**: Exits if ML switches from TRENDING to RANGING or DANGER
- **Time limit**: Exits if position is open too long without meaningful movement

---

## Configuration

All parameters are in `config/strategy.yaml`:

```yaml
grid:
  levels: 5               # Orders per side (buy + sell)
  capital_usdt: 1000      # USDT allocated
  min_usdt_reserve: 100   # Minimum USDT to keep
  order_refresh_time: 60  # Seconds between order refresh

pairs:
  BTC-USDT:
    enabled: false
    step_size: 0.00001
  ETH-USDT:
    enabled: true
    step_size: 0.001
  BNB-USDT:
    enabled: true
    step_size: 0.01
  DOGE-USDT:
    enabled: true
    step_size: 1
  XRP-USDT:
    enabled: true
    step_size: 0.1

ml:
  enabled: true
  regime_threshold: 0.5    # Confidence threshold for regime-based decisions

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
  max_btc_exposure_pct: 80 # Position guard: max base asset allocation

fee_optimization:
  bnb_target_usdt: 20    # Target BNB balance in USDT
  bnb_min_usdt: 10       # Buy BNB when below this
  bnb_max_usdt: 50       # Sell excess BNB above this
  use_limit_maker: true  # Use post-only orders
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

## Auto-Retraining Pipeline

### Weekly Parameter Sweep
Every Sunday at 00:00 UTC, a GitHub Actions workflow runs VectorBT parameter sweeps for each active pair. If a configuration beats the current baseline Sharpe by >5%, it commits the updated parameters and triggers a deployment.

### Monthly ML Retraining
On the 1st of each month, a GitHub Actions workflow retrains all per-pair ML models using the latest Binance data. New models are compared against deployed models -- only promoted if accuracy improves by >1%.

### Hot-Reload
ML models are monitored for file changes during the indicator refresh cycle. When a model file is updated (e.g., from auto-retraining), the bot:
1. Detects the file modification time change
2. Loads the new model in memory
3. Validates it against last known features
4. Begins using new predictions immediately
5. Sends Telegram notification of the reload

Zero downtime -- the old model continues serving predictions until the new one is fully loaded.

---

## Expected Behavior

**Normal market (ranging):**
- Grid places 5 buy + 5 sell levels within Bollinger Bands per pair
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
