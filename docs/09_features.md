# Features

TA-Enhanced Multi-Pair Grid + Trend Bot — feature overview.

## Trading Pairs

- BTC-USDT, ETH-USDT, BNB-USDT, DOGE-USDT, XRP-USDT
- Each pair runs in its own `PairEngine` with independent grid and trend state
- Capital allocated per pair with configurable limits (25% max per pair default)

## Grid Strategy

- Dynamic grid levels calculated from Bollinger Bands and ATR
- Buy levels below mid price, sell levels above, spaced by ATR x 1.5
- **5 levels per side**, configurable capital per pair
- **Asymmetric geometric spacing** on buy side — wider levels during dips for faster breakeven
- Orders refresh every 60 seconds

## Technical Indicators

| Indicator | Period | Purpose |
|-----------|--------|---------|
| Bollinger Bands | 20, 2.0 std dev | Defines price boundaries and grid range |
| RSI | 14 | Momentum filter — pauses grid above 70, reactivates below 35 |
| EMA | 200 | Trend filter — grid only runs above EMA200 |
| ATR | 14 | Sets grid spacing (ATR x 0.8 multiplier) |

## Grid State Machine

```
                 RSI > 70 or Price < EMA200
    ACTIVE  ──────────────────────────────────►  PAUSED
       ▲                                            │
       │              RSI < 35 + near BB lower      │
       │  REACTIVATING ◄────────────────────────────┘
       │       │
       └───────┘  RSI normalizes, price > EMA200
```

- **ACTIVE**: Placing buy/sell grid orders
- **PAUSED**: All orders cancelled, holding USDT
- **REACTIVATING**: Re-entering market after oversold bounce

## ML Regime Classifier

- Per-pair Random Forest models (`models/regime_{symbol}.pkl`)
- Classifies market into 3 regimes: `RANGING` (0), `TRENDING` (1), `DANGER` (2)
- Regime gates grid capital scaling and trend entry decisions
- Confidence-weighted position sizing: risk scales from 0.5% to 3% based on ML confidence

## Cross-Asset ML Correlation Gate

- BTC-USDT always loaded as systemic risk signal (even when BTC trading is disabled)
- When BTC regime = DANGER (2), all altcoin buy-side operations halt immediately
- Sell orders are unaffected — bot can still exit positions during selloffs
- BTC candle data fetched via dedicated CandleFeed (`candle_feeds["BTC-USDT"]`)
- Gate transitions (active/inactive) trigger Telegram alerts
- Safe default: if BTC model fails to load, altcoin buys halt (fail-safe)

## Dual-Engine Strategy

- **Grid Engine**: Asymmetric grid with ATR-adaptive spacing, profits from oscillation
- **Trend Engine**: Directional trades with trailing stops, entered only on ML-confirmed trends
- Independent capital pools per engine (e.g., 70% grid / 30% trend)
- Both engines filtered by ML regime and technical indicators

## Confidence-Weighted Position Sizing

- Trend engine risk scales with ML confidence score
- Formula: `risk_pct = 0.5% + (confidence x 2.0%)`, clamped to [0.5%, 3.0%]
- Higher confidence means larger positions, lower confidence means conservative sizing
- Per-pair isolation with separate PositionManager instances

## Risk Management

### Circuit Breaker
- Halts trading if portfolio drops 10% from peak equity
- Halts trading if daily loss exceeds 5%
- Requires manual reset via Telegram `/reset` command

### Position Guard
- Caps BTC exposure at 80% of total capital
- Enforces minimum $50 USDT reserve
- Blocks individual orders that would violate limits

## Dynamic Fee Optimization

### BNB Rebalancer (`src/risk/bnb_rebalancer.py`)
- Maintains BNB balance within configurable range for 25% fee discount
- Target: $15-25 worth of BNB (covers ~2 weeks of grid trading fees)
- Buy $20 of BNB when balance < $10, sell excess when > $50
- Runs every indicator refresh cycle (55 min), only when grid is ACTIVE
- Cooldown mechanism prevents rapid-fire rebalancing

### LIMIT_MAKER Orders
- All orders use `OrderType.LIMIT_MAKER` (post-only) instead of `OrderType.LIMIT`
- If order would cross spread (take liquidity), exchange rejects it
- Guarantees maker fee rate: 0.075% with BNB discount vs 0.1% taker
- Rejected orders retry on next tick with updated price

## Telegram Integration

### Alerts (automatic)
- Bot startup / shutdown
- Grid state changes (ACTIVE, PAUSED, REACTIVATING) with indicator values
- Trade fills with full P&L breakdown and round-trip details

### Interactive Commands
| Command | Description |
|---------|-------------|
| `/status` | Current grid state, mode, uptime, pending orders |
| `/pnl` | P&L summary — today, week, month, all-time with win rates |
| `/pause` | Manually pause grid trading |
| `/resume` | Resume grid from manual pause |
| `/reset` | Reset circuit breaker after halt |
| `/trades` | Last 5 closed trades |
| `/help` | Command reference |

All commands respond only to the configured `TELEGRAM_CHAT_ID`.

## Streamlit Dashboard

- Password-protected (bcrypt, streamlit-authenticator)
- Live equity curve with daily P&L bars
- Summary cards: today, week, month, all-time P&L
- Filterable trade history table with CSV export
- Best and worst trades ranking
- Period breakdown table (hour, day, week, month, all-time)
- Dark theme, auto-refresh button

## Trade Journal

- SQLite database with WAL mode (safe concurrent access)
- Every trade logged with full indicator snapshot: RSI, BB, EMA200, ATR, grid state
- Round-trip tracking: pairs BUY with matching SELL to compute net P&L
- Indexed queries for time-range analytics

## Health Check

- HTTP endpoint on port 8080
- Returns `200 OK` with grid state when running
- Returns `503` when halted (circuit breaker triggered)
- Suitable for container orchestration probes

## Event Logging

- Structured JSONL files with daily rotation
- Events: bot_started, indicators_updated, state_changed, order_placed, order_cancelled, order_blocked, trade_filled, round_trip_closed, circuit_breaker, daily_reset, bot_stopped
- Each event includes full context (prices, balances, indicator values)

## Auto-Retraining Pipeline

### Weekly Parameter Sweep (`.github/workflows/sweep.yml`)
- Runs every Sunday at 00:00 UTC
- VectorBT sweep for each active pair (ETH, BNB, DOGE, XRP)
- Compares against current config as baseline
- If Sharpe improves >5%, updates strategy.yaml and commits
- Uploads results as GitHub Actions artifacts

### Monthly ML Retraining (`.github/workflows/retrain.yml`)
- Runs on 1st of each month
- Retrains per-pair Random Forest models with latest Binance data
- New models saved as `.new`, compared against deployed accuracy
- Only replaces if accuracy improves >1%
- Commits with `[skip ci]` to prevent deploy loop

### Hot-Reload Detection
- Tracks `last_loaded_mtime` per ML model file
- On indicator refresh cycle (55 min), checks file modification time
- If changed, loads new model, validates with last known features
- Logs reload + sends Telegram notification
- Zero downtime — old model serves predictions until new one loads
