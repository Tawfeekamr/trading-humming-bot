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
│   ├── strategy.yaml                   # Grid params, indicators, risk, pairs
│   └── ta_grid_btcusdt_conf.yml        # Hummingbot v2 script config
├── hummingbot_files/
│   └── scripts/
│       ├── ta_grid_trend.py            # Dual-engine strategy (Grid + Trend)
│       ├── pair_engine.py              # Per-pair state container (PairEngine, PairConfig)
│       └── capital_manager.py          # Multi-pair capital allocation
├── src/
│   ├── indicators/
│   │   ├── bollinger.py                # Bollinger Bands calculator
│   │   ├── rsi.py                      # RSI calculator
│   │   ├── ema.py                      # EMA calculator
│   │   └── atr.py                      # ATR + grid spacing
│   ├── grid/
│   │   ├── grid_manager.py             # Grid level calculation (asymmetric spacing)
│   │   ├── grid_state.py               # State machine (ACTIVE/PAUSED/REACTIVATING/DANGER)
│   │   └── order_tracker.py            # Order status tracking
│   ├── risk/
│   │   ├── circuit_breaker.py          # Drawdown + daily loss halt
│   │   ├── position_guard.py           # Exposure + reserve limits
│   │   └── bnb_rebalancer.py           # BNB balance management for fee optimization
│   ├── trend/
│   │   ├── trend_manager.py            # Trend engine with trailing stops
│   │   └── position_manager.py         # Confidence-weighted position sizing
│   ├── ml/
│   │   ├── regime_classifier.py        # Per-pair ML regime classifier
│   │   └── train_pipeline.py           # ML model training with Binance data
│   ├── data/
│   │   ├── candle_feed.py              # Binance REST candle fetcher (with retry + validation)
│   │   └── ws_feed.py                  # Binance WebSocket feed
│   ├── notifications/
│   │   ├── telegram_bot.py             # Async Telegram alerts
│   │   └── telegram_commands.py        # Interactive Telegram commands
│   ├── journal/
│   │   └── trade_journal.py            # SQLite trade logging + P&L queries
│   ├── logging/
│   │   └── event_logger.py             # JSONL structured event logs (daily rotation + 14-day cleanup)
│   ├── health.py                       # HTTP health check server
│   └── logging_config.py              # Logging setup
├── models/                             # Per-pair ML models (regime_{symbol}.pkl)
├── tests/                              # Unit tests
├── iac/aws-tokyo/                      # Terraform infrastructure
├── .github/workflows/deploy.yml        # CI/CD pipeline
├── .github/workflows/sweep.yml         # Weekly VectorBT parameter sweep
├── .github/workflows/retrain.yml       # Monthly ML model retraining
└── docs/                               # Documentation
```

## Strategy Script (`hummingbot_files/scripts/ta_grid_trend.py`)

The dual-engine strategy extending Hummingbot v2's `StrategyV2Base`. Runs both a grid engine and a trend engine across multiple pairs simultaneously.

### Config Class

```python
class TAGridConfig(StrategyV2ConfigBase):
    script_file_name: str = "ta_grid_trend.py"
    exchange: str = "binance_paper_trade"    # or "binance" for live
    trading_pair: str = "BTC-USDT"
    levels: int = 5
    capital_usdt: float = 200.0
    # ... indicators, risk params
```

### Lifecycle

1. **`__init__`** — Loads config, initializes all modules per pair (indicators, grid, ML, trend, risk, telegram, journal), starts health server and Telegram command handler
2. **`on_tick`** — Called every second by Hummingbot's clock:
   - Fetches candles every 55 minutes from Binance per pair
   - Calculates BB, RSI, EMA200, ATR indicators per pair
   - Runs per-pair ML regime classification
   - Evaluates grid state machine transitions (including DANGER regime)
   - Checks circuit breaker and position guard
   - Places/cancels grid orders and manages trend positions via Hummingbot connector
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
| `_btc_danger_active()` | Checks if BTC regime is DANGER for correlation gate |
| `_run_ml_prediction(pair)` | Runs ML regime classification with hot-reload check |
| BNB rebalancer integration | `_grid_tick` calls rebalancer on first pair's indicator refresh |

## Indicators (`src/indicators/`)

Each indicator is a stateless class with a `calculate()` method taking a pandas Series and returning a float or dataclass. All use standard algorithms:

- **Bollinger Bands**: SMA ± (std_dev x multiplier). Returns `BBResult(upper, mid, lower)`.
- **RSI**: Wilder's smoothing (SMA seed, then EMA). Returns float 0–100.
- **EMA**: `pandas ewm()`. Returns float.
- **ATR**: True Range over N periods. Also provides `grid_spacing()` = ATR x multiplier.

## Grid System (`src/grid/`)

### GridManager
Generates buy/sell levels around Bollinger mid price with asymmetric spacing:
- Buy levels: geometric spacing `base × (1 + α)^i`, clamped to BB lower — wider during dips
- Sell levels: uniform spacing `mid + (spacing × i)`, clamped to BB upper
- Each level has `{level, price, quantity}` where buy-side sizes also scale geometrically

### GridStateMachine
Four states with deterministic transitions:
- ACTIVE → PAUSED: RSI > 70 or price < EMA200
- ACTIVE → DANGER: ML regime classifier returns DANGER
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
- Push base asset exposure above `max_exposure_pct` (80%) of total capital

### BNBRebalancer
Maintains BNB balance for fee payments. Evaluates current BNB balance against configurable thresholds (min $10, target $20, max $50). Returns a `RebalanceResult` indicating buy/sell/hold action with amount. Includes cooldown mechanism (default 3600s) to prevent rapid-fire rebalancing.

## Trend Engine (`src/trend/`)

### TrendManager
Manages directional trend trades with:
- ML-gated entry: only enters when regime is TRENDING with sufficient confidence
- Trailing stop mechanism that locks in gains as price moves favorably
- Independent capital pool from grid engine

### PositionManager
Thread-safe position manager with confidence-weighted sizing:
- Risk percentage scales with ML confidence: `0.5% + (confidence × 2.0%)`, clamped to `[0.5%, 3.0%]`
- Per-pair isolation with separate instances
- State persistence for recovery after restart

## ML Regime Classifier (`src/ml/`)

### RegimeClassifier
Per-pair Random Forest classifier that labels market regime:
- `RANGING` (0): Grid engine active at full capital
- `TRENDING` (1): Grid capital reduced, trend engine enabled
- `DANGER` (2): All trading paused
- Each pair has its own model file: `models/regime_{symbol}.pkl`
- Confidence score (0–1) drives position sizing via PositionManager
- BTC-USDT is always loaded as a systemic signal for the correlation gate, even when BTC trading is disabled
- Hot-reload: tracks file modification time per model, reloads on change with zero downtime
- Training pipeline: `train_pipeline.py` fetches latest Binance data and retrains models with `--pair` and `--output` flags

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

## GitHub Actions Workflows

### deploy.yml
Tests (pytest) on push to main, then deploys to EC2 via AWS SSM with `docker compose up -d --build`.

### sweep.yml
Weekly (Sunday 00:00 UTC) VectorBT parameter sweep. Matrix strategy for 4 active pairs. Compares sweep results against current config. Commits parameter updates if Sharpe improves >5%. Uses `[skip ci]` to prevent deploy loop.

### retrain.yml
Monthly (1st of month) ML model retraining. Fetches latest data from Binance public API. Retrains per-pair Random Forest models. Accuracy-gated: only deploys if new model beats current by >1%. Outputs comparison report.

## Health Check (`src/health.py`)

Lightweight HTTP server on port 8080 using `http.server`. Runs in a daemon thread. Returns JSON with status, grid state, and last tick timestamp.
