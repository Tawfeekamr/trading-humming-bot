# Codebase Reference

Project structure and module walkthrough.

## Directory Layout

```
trading-humming-bot/
├── app.py                              # Streamlit dashboard
├── docker-compose.yml                  # Bot + Dashboard containers
├── docker-entrypoint.sh                # Bot container init script
├── Dockerfile                          # Bot (Hummingbot base + conda)
├── Dockerfile.dashboard                # Dashboard (Python 3.12)
├── requirements.txt                    # Python dependencies
├── config/
│   ├── strategy.yaml                   # Grid params, indicators, risk
│   └── ta_grid_btcusdt_conf.yml        # Hummingbot v2 script config
├── hummingbot_files/
│   └── scripts/
│       └── ta_grid_btcusdt.py          # Main strategy (StrategyV2Base)
├── src/
│   ├── indicators/
│   │   ├── bollinger.py                # Bollinger Bands calculator
│   │   ├── rsi.py                      # RSI calculator
│   │   ├── ema.py                      # EMA calculator
│   │   └── atr.py                      # ATR + grid spacing
│   ├── grid/
│   │   ├── grid_manager.py             # Grid level calculation
│   │   ├── grid_state.py               # State machine (ACTIVE/PAUSED/REACTIVATING)
│   │   └── order_tracker.py            # Order status tracking
│   ├── risk/
│   │   ├── circuit_breaker.py          # Drawdown + daily loss halt
│   │   └── position_guard.py           # Exposure + reserve limits
│   ├── data/
│   │   ├── candle_feed.py              # Binance REST candle fetcher
│   │   └── ws_feed.py                  # Binance WebSocket feed
│   ├── notifications/
│   │   ├── telegram_bot.py             # Async Telegram alerts
│   │   └── telegram_commands.py        # Interactive Telegram commands
│   ├── journal/
│   │   └── trade_journal.py            # SQLite trade logging + P&L queries
│   ├── logging/
│   │   └── event_logger.py             # JSONL structured event logs
│   ├── health.py                       # HTTP health check server
│   └── logging_config.py              # Logging setup
├── tests/                              # Unit tests
├── iac/aws-tokyo/                      # Terraform infrastructure
├── .github/workflows/deploy.yml        # CI/CD pipeline
└── docs/                               # Documentation
```

## Strategy Script (`hummingbot_files/scripts/ta_grid_btcusdt.py`)

The core strategy extending Hummingbot v2's `StrategyV2Base`.

### Config Class

```python
class TAGridConfig(StrategyV2ConfigBase):
    script_file_name: str = "ta_grid_btcusdt.py"
    exchange: str = "binance_paper_trade"    # or "binance" for live
    trading_pair: str = "BTC-USDT"
    levels: int = 8
    capital_usdt: float = 200.0
    # ... indicators, risk params
```

### Lifecycle

1. **`__init__`** — Loads config, initializes all modules (indicators, grid, risk, telegram, journal), starts health server and Telegram command handler
2. **`on_tick`** — Called every second by Hummingbot's clock:
   - Fetches candles every 55 minutes from Binance
   - Calculates BB, RSI, EMA200, ATR indicators
   - Evaluates grid state machine transitions
   - Checks circuit breaker and position guard
   - Places/cancels grid orders via Hummingbot connector
3. **`did_fill_order`** — Called on each fill:
   - Logs trade to journal with indicator snapshot
   - Matches BUY/SELL pairs for round-trip P&L
   - Sends rich Telegram alert on trade close
4. **`on_stop`** — Cancels all orders, stops Telegram handler, logs shutdown

### Key Methods

| Method | Purpose |
|--------|---------|
| `_load_config()` | Reads `config/strategy.yaml` for overrides |
| `_place_grid_orders(grid, price)` | Cancels old orders, places new grid levels |
| `_cancel_all_orders(reason)` | Cancels all in-flight orders with logging |
| `_get_usdt_balance()` / `_get_btc_balance()` | Reads connector balances (handles v1/v2 API) |
| `_estimate_equity(price)` | USDT + (BTC x price) |
| `_notify_state_change(...)` | Sends Telegram alert with cooldown |

## Indicators (`src/indicators/`)

Each indicator is a stateless class with a `calculate()` method taking a pandas Series and returning a float or dataclass. All use standard algorithms:

- **Bollinger Bands**: SMA ± (std_dev x multiplier). Returns `BBResult(upper, mid, lower)`.
- **RSI**: Wilder's smoothing (SMA seed, then EMA). Returns float 0–100.
- **EMA**: `pandas ewm()`. Returns float.
- **ATR**: True Range over N periods. Also provides `grid_spacing()` = ATR x multiplier.

## Grid System (`src/grid/`)

### GridManager
Generates symmetric buy/sell levels around Bollinger mid price:
- Buy levels: `mid - (spacing x i)`, clamped to BB lower
- Sell levels: `mid + (spacing x i)`, clamped to BB upper
- Each level has `{level, price, quantity}` where quantity is split evenly from capital

### GridStateMachine
Three states with deterministic transitions:
- ACTIVE → PAUSED: RSI > 70 or price < EMA200
- PAUSED → REACTIVATING: RSI < 35 AND price near BB lower (within 2%)
- REACTIVATING → ACTIVE: Conditions normalize

### OrderTracker
In-memory order status tracking with PENDING/FILLED/CANCELLED states.

## Risk Management (`src/risk/`)

### CircuitBreaker
Tracks peak equity and start-of-day equity. Returns `True` (halt) when:
- Current equity drops `max_drawdown_pct` (10%) below peak, OR
- Current equity drops `daily_loss_limit_pct` (5%) below start-of-day

Once halted, the `halted` flag must be manually reset via Telegram `/reset`.

### PositionGuard
Blocks orders that would:
- Leave USDT balance below `min_reserve` ($50)
- Push BTC exposure above `max_btc_exposure_pct` (80%) of total capital

## Data Feeds (`src/data/`)

### CandleFeed
Fetches OHLCV candles from Binance REST API using `python-binance`. Uses public endpoint (no API keys). Returns DataFrame with open, high, low, close, volume columns.

## Notifications (`src/notifications/`)

### TelegramBot
Async message sender using `python-telegram-bot`. Sends HTML-formatted alerts. Uses `asyncio.get_event_loop().create_task()` to avoid blocking the tick loop.

### TelegramCommandHandler
Runs a separate `python-telegram-bot` Application in a daemon thread. Filters all updates by `TELEGRAM_CHAT_ID`. Commands query journal/state/circuit_breaker and return formatted text.

## Trade Journal (`src/journal/`)

SQLite database in `data/trade_journal.db` with WAL mode. Each trade stores 19 fields including prices, quantities, fees, P&L, and indicator snapshots. Provides time-windowed summary queries and equity curve generation.

## Dashboard (`app.py`)

Streamlit app with `streamlit-authenticator` for login. Reads from the same SQLite database. Dark-themed with custom CSS. No write operations — purely a read-only view of trading data.

## Event Logger (`src/logging/`)

JSONL files in `logs/` directory with daily rotation (`events_YYYY-MM-DD.jsonl`). Each line is a JSON object with timestamp, event type, and contextual data.

## Health Check (`src/health.py`)

Lightweight HTTP server on port 8080 using `http.server`. Runs in a daemon thread. Returns JSON with status, grid state, and last tick timestamp.
