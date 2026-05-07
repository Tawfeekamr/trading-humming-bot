# Features

TA-Enhanced BTC/USDT Grid Bot — feature overview.

## Grid Strategy

- Dynamic grid levels calculated from Bollinger Bands and ATR
- Buy levels below mid price, sell levels above, spaced by ATR x 0.8
- 8 levels per side, $200 starting capital (configurable)
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

## Risk Management

### Circuit Breaker
- Halts trading if portfolio drops 10% from peak equity
- Halts trading if daily loss exceeds 5%
- Requires manual reset via Telegram `/reset` command

### Position Guard
- Caps BTC exposure at 80% of total capital
- Enforces minimum $50 USDT reserve
- Blocks individual orders that would violate limits

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
