# trading-engine-core: Shared Rust Trading Engine Design

> **Status:** Design phase — not yet approved for implementation.
> **Constraint:** Current Python implementation preserved on `archive/python-engine` branch. No merge to `main` until full migration passes validation.

---

## Goal

Extract all strategy logic (grid, trend, signal) from Python into a shared Rust crate with PyO3 bindings. Both Hummingbot and NautilusTrader consume the same compiled wheel. Strategy code is written once, runs on any engine.

## Architecture

```
trading-engine-core (Rust crate → Python wheel)
│
├── trait ExecutionAdapter    ← Hummingbot, NautilusTrader, Mock implement this
├── trait Strategy            ← Grid, Trend, Signal implement this
├── StrategyHost              ← owns adapter + strategies, routes bars/events
├── Indicators                ← EMA, RSI, ATR, BB, Support/Resistance, Candlestick
└── Risk                      ← Circuit Breaker, Position Guard, BNB Rebalancer
```

Three principles:
1. **Pure Rust core** — `models/`, `strategy/`, `indicators/`, `risk/` have zero PyO3 imports. Tested with `cargo test`.
2. **Thin Python shells** — each engine provides a ~100-line adapter class that translates Rust calls to engine-specific APIs.
3. **Trait-based abstraction** — `ExecutionAdapter` is the seam. Adding a 3rd engine = one new adapter class.

---

## 1. Crate Structure

```
trading-engine-core/
├── Cargo.toml                        # [lib] crate-type = ["rlib", "cdylib"]
├── pyproject.toml                    # maturin build system
├── src/
│   ├── lib.rs                        # PyO3 module entry
│   │
│   ├── models/                       # Shared data types (framework-agnostic)
│   │   ├── mod.rs
│   │   ├── order.rs                  # Order, OrderSide, OrderType, TimeInForce
│   │   ├── position.rs              # Position, PositionSide
│   │   ├── bar.rs                    # Bar (OHLCV), BarType, Timeframe
│   │   ├── instrument.rs            # Instrument (symbol, pip_size, tick_size, step_size)
│   │   ├── currency.rs              # Currency, Money, Price, Quantity
│   │   └── signal.rs                # Signal (from Telegram copy engine)
│   │
│   ├── adapter/                      # Execution adapter trait + bridges
│   │   ├── mod.rs                    # trait ExecutionAdapter
│   │   ├── hummingbot.rs            # PyO3 bridge → Hummingbot connector
│   │   ├── nautilus.rs              # PyO3 bridge → NautilusTrader TradingNode
│   │   └── mock.rs                  # In-memory adapter for backtesting
│   │
│   ├── strategy/                     # Strategy engines (pure Rust)
│   │   ├── mod.rs                    # trait Strategy + StrategyContext
│   │   ├── grid.rs                   # Grid strategy
│   │   ├── trend.rs                  # Trend strategy
│   │   └── signal.rs                # Signal copy strategy
│   │
│   ├── indicators/                   # Technical analysis library
│   │   ├── mod.rs                    # trait Indicator
│   │   ├── ema.rs
│   │   ├── rsi.rs
│   │   ├── atr.rs
│   │   ├── bollinger.rs
│   │   ├── support_resistance.rs
│   │   └── candlestick.rs
│   │
│   ├── risk/                         # Risk management
│   │   ├── mod.rs
│   │   ├── circuit_breaker.rs
│   │   ├── position_guard.rs
│   │   └── bnb_rebalancer.rs
│   │
│   ├── config/                       # Configuration
│   │   ├── mod.rs                    # Serde-based YAML config loading
│   │   └── types.rs                 # Config structs
│   │
│   └── python/                       # PyO3 bindings (feature-gated)
│       ├── mod.rs
│       ├── adapter_bridge.rs        # PyExecutionAdapter
│       ├── strategy_bridge.rs       # PyStrategy
│       └── models_bridge.rs         # Python-visible model classes
│
└── tests/                            # Pure Rust tests
    ├── test_grid.rs
    ├── test_trend.rs
    ├── test_indicators.rs
    ├── test_adapter_mock.rs
    └── test_risk.rs
```

---

## 2. ExecutionAdapter — The Core Abstraction

```rust
/// The execution adapter trait — implemented by each trading engine.
///
/// Strategy code calls these methods. The adapter routes to whichever
/// engine is active (Hummingbot, NautilusTrader, mock for backtests).
pub trait ExecutionAdapter {
    // ── Account & Portfolio ──
    fn get_balance(&self, currency: &Currency) -> Result<Money, AdapterError>;
    fn get_positions(&self, instrument_id: &InstrumentId) -> Result<Vec<Position>, AdapterError>;
    fn get_open_orders(&self, instrument_id: &InstrumentId) -> Result<Vec<Order>, AdapterError>;

    // ── Order Management ──
    fn submit_order(&mut self, order: Order) -> Result<ClientOrderId, AdapterError>;
    fn cancel_order(&mut self, client_order_id: &ClientOrderId) -> Result<(), AdapterError>;
    fn cancel_all_orders(&mut self, instrument_id: &InstrumentId) -> Result<(), AdapterError>;

    // ── Market Data ──
    fn get_mid_price(&self, instrument_id: &InstrumentId) -> Result<Price, AdapterError>;
    fn subscribe_bars(&mut self, instrument_id: &InstrumentId, timeframe: Timeframe) -> Result<(), AdapterError>;

    // ── Instrument Info ──
    fn get_instrument(&self, instrument_id: &InstrumentId) -> Result<Instrument, AdapterError>;
}
```

### PyO3 Bridge Pattern (from NautilusTrader)

```rust
// adapter/hummingbot.rs
pub struct PyHummingbotAdapter {
    py_self: Option<Py<PyAny>>,   // Reference to Python connector instance
}

impl ExecutionAdapter for PyHummingbotAdapter {
    fn submit_order(&mut self, order: Order) -> Result<ClientOrderId, AdapterError> {
        Python::attach(|py| {
            let py_order = order.into_py_any(py)?;
            let py_self = self.py_self.as_ref()
                .ok_or(AdapterError::NotInitialized)?;
            let result = py_self.call_method1(py, "submit_order", (py_order,))?;
            result.extract::<String>(py)
                .map(|s| ClientOrderId::new(&s))
                .map_err(|e| AdapterError::PythonError(e.to_string()))
        })
    }
    // ... same pattern for every method
}
```

### Python Adapter Classes

Each engine provides a thin Python class that the Rust bridge calls into:

**Hummingbot:**
```python
class HummingbotAdapter:
    def __init__(self, connector):
        self._connector = connector

    def submit_order(self, order):
        if order["side"] == "BUY":
            return self._connector.buy(order["symbol"], order["quantity"], OrderType.LIMIT, order["price"])
        else:
            return self._connector.sell(order["symbol"], order["quantity"], OrderType.LIMIT, order["price"])

    def get_balance(self, currency):
        return float(self._connector.get_balance(currency))

    def cancel_order(self, client_order_id):
        self._connector.cancel(client_order_id)

    def cancel_all_orders(self, instrument_id):
        self._connector.cancel_all()

    def get_mid_price(self, instrument_id):
        return float(self._connector.get_mid_price(instrument_id))

    def get_instrument(self, instrument_id):
        rules = self._connector.trading_rules.get(instrument_id)
        return {
            "symbol": instrument_id,
            "pip_size": rules.min_price_increment,
            "tick_size": rules.min_price_increment,
            "step_size": rules.min_base_increment,
        }
```

**NautilusTrader:**
```python
class NautilusAdapter:
    def __init__(self, strategy):
        self._strategy = strategy
        self._cache = strategy.cache
        self._order_factory = strategy.order_factory

    def submit_order(self, order):
        instrument = self._cache.instrument(order["instrument_id"])
        side = OrderSide.BUY if order["side"] == "BUY" else OrderSide.SELL
        nautilus_order = self._order_factory.limit(
            instrument_id=instrument.id,
            order_side=side,
            quantity=instrument.make_qty(order["quantity"]),
            price=instrument.make_price(order["price"]),
            time_in_force=TimeInForce.GTC,
        )
        self._strategy.submit_order(nautilus_order)
        return str(nautilus_order.client_order_id)

    def cancel_order(self, client_order_id):
        order = self._cache.order(ClientOrderId(client_order_id))
        if order:
            self._strategy.cancel_order(order)

    def cancel_all_orders(self, instrument_id):
        self._strategy.cancel_all_orders(instrument_id)

    def get_mid_price(self, instrument_id):
        quote = self._cache.quote_tick(instrument_id)
        return float((quote.bid + quote.ask) / 2) if quote else 0.0

    def get_instrument(self, instrument_id):
        inst = self._cache.instrument(instrument_id)
        return {
            "symbol": str(inst.id),
            "pip_size": float(inst.price_increment),
            "tick_size": float(inst.price_increment),
            "step_size": float(inst.size_increment),
        }
```

---

## 3. Strategy Engine

### Strategy Trait

```rust
pub trait Strategy {
    // ── Identity ──
    fn id(&self) -> &StrategyId;
    fn instrument_id(&self) -> &InstrumentId;

    // ── Lifecycle ──
    fn on_start(&mut self, adapter: &mut dyn ExecutionAdapter) -> Result<(), StrategyError>;
    fn on_stop(&mut self, adapter: &mut dyn ExecutionAdapter) -> Result<(), StrategyError>;

    // ── Data ──
    fn on_bar(&mut self, bar: &Bar, adapter: &mut dyn ExecutionAdapter) -> Result<(), StrategyError>;
    fn on_trade_tick(&mut self, tick: &TradeTick, adapter: &mut dyn ExecutionAdapter) -> Result<(), StrategyError>;

    // ── Events ──
    fn on_order_filled(&mut self, fill: &OrderFilled, adapter: &mut dyn ExecutionAdapter) -> Result<(), StrategyError>;
    fn on_order_rejected(&mut self, rejection: &OrderRejected) -> Result<(), StrategyError>;

    // ── State Persistence ──
    fn save_state(&self) -> Result<Vec<u8>, StrategyError>;
    fn load_state(&mut self, data: &[u8]) -> Result<(), StrategyError>;

    // ── Indicators ──
    fn indicators_initialized(&self) -> bool;
}
```

### Grid Strategy

Ported from `src/grid/grid_manager.py` + `src/grid/grid_state.py` + `src/grid/order_tracker.py`.

```rust
pub struct GridStrategy {
    id: StrategyId,
    instrument_id: InstrumentId,
    config: GridConfig,

    // Indicators
    ema: Ema,
    rsi: Rsi,
    bollinger: BollingerBands,
    atr: Atr,

    // State
    state: GridState,                    // enum: Inactive, Active, Paused, Stopped
    active_orders: HashMap<ClientOrderId, GridLevel>,
    base_price: Option<Price>,
    total_pnl: Money,
    trade_count: u32,

    // Risk
    circuit_breaker: CircuitBreaker,
}

pub struct GridConfig {
    pub levels: u32,                      // Orders per side (5)
    pub capital: Money,                   // Total capital allocated
    pub spacing_atr_multiplier: f64,      // Grid spacing = ATR × this (1.5)
    pub order_refresh_seconds: u64,       // Re-evaluation interval
    pub min_usdt_reserve: Money,          // Always keep this in reserve
    pub ema_period: u32,                  // Trend filter (200)
    pub rsi_period: u32,                  // RSI (14)
    pub bollinger_period: u32,
    pub bollinger_std_dev: f64,
    pub atr_period: u32,
    pub activate_conditions: Vec<Condition>,
    pub pause_conditions: Vec<Condition>,
    pub reactivate_conditions: Vec<Condition>,
}

pub enum GridState {
    Inactive,
    Active,
    Paused,
    Stopped { reason: String },
}

struct GridLevel {
    price: Price,
    side: OrderSide,
    level: u32,
    quantity: Quantity,
    status: LevelStatus,
}
```

**on_bar flow:**
1. Update indicators (EMA, RSI, BB, ATR)
2. Evaluate state transitions (Inactive→Active, Active→Paused, etc.)
3. If entering Active: `place_grid()` — symmetric buy/sell limit orders at ATR-spaced intervals
4. If leaving Active: `cancel_grid()` — cancel all outstanding orders
5. If Active and refresh timer expired: re-center grid on new price

### Trend Strategy

Ported from `src/trend/trend_manager.py` + `position_manager.py` + `support_resistance.py` + `candlestick_patterns.py`.

```rust
pub struct TrendStrategy {
    id: StrategyId,
    instrument_id: InstrumentId,
    config: TrendConfig,

    // Indicators
    ema_fast: Ema,
    ema_slow: Ema,
    ema_trend: Ema,
    rsi: Rsi,
    atr: Atr,

    // Trend-specific
    support_resistance: SupportResistance,
    candlestick: CandlestickPatterns,

    // Position state
    position: Option<TrendPosition>,
    signal_score: u32,
    confirmation_ticks: u32,

    // Risk
    circuit_breaker: CircuitBreaker,
}

pub struct TrendConfig {
    pub ema_fast: u32,                    // 20
    pub ema_slow: u32,                    // 50
    pub ema_trend: u32,                   // 200
    pub rsi_period: u32,                  // 14
    pub rsi_min: f64,                     // 40
    pub rsi_max: f64,                     // 70
    pub atr_period: u32,                  // 14
    pub min_signal_score: u32,            // 3
    pub confirmation_ticks: u32,          // 1
    pub risk_per_trade_pct: f64,          // 2.0
    pub max_position_pct: f64,            // 25.0
    pub max_positions: u32,               // 2 per pair
    pub max_total_positions: u32,         // 3 across all pairs
    pub exit_signal_threshold: u32,       // 2
    pub trailing_stop_pct: f64,           // 1.5
    pub trailing_activation_pct: f64,     // 1.5
    pub rr_ratio: f64,                    // 2.0
    pub sl_buffer_pct: f64,              // 0.2
    pub timeframe: Timeframe,             // 1h
}

pub struct TrendPosition {
    side: PositionSide,
    entry_price: Price,
    stop_loss: Price,
    take_profit: Price,
    quantity: Quantity,
    signal_score_at_entry: u32,
    trailing_stop: Option<TrailingStop>,
    opened_at: i64,
}

pub struct TrailingStop {
    activation_price: Price,
    distance_pct: f64,
    current_stop: Price,
    highest_since_activation: Price,
}
```

**7-point signal scoring system** (same as Python):
1. EMA cross (+2)
2. Support/Resistance bounce (+2)
3. RSI momentum (+1)
4. ATR breakout (+1)
5. Candlestick confirmation (+1)

### Signal Copy Strategy

Ported from `src/signals/signal_engine.py` + `signal_parser.py` + `signal_position.py` + `signal_risk.py`.

```rust
pub struct SignalStrategy {
    id: StrategyId,
    config: SignalConfig,
    positions: HashMap<String, SignalPosition>,
    trade_history: Vec<ClosedPosition>,
    daily_pnl: Money,
    daily_trade_count: u32,
    last_trade_time: Option<i64>,
    circuit_breaker: CircuitBreaker,
    btc_regime: Option<Regime>,
}

pub struct SignalConfig {
    pub max_positions: u32,               // 3
    pub per_trade_risk_pct: f64,          // 3.0
    pub capital_pct: f64,                 // 10.0
    pub max_capital_usdt: Money,          // 1000
    pub min_rr_ratio: f64,                // 1.0
    pub max_sl_distance_pct: f64,         // 10.0
    pub max_entry_zone_pct: f64,          // 3.0
    pub min_quality_score: u32,           // 5
    pub tp1_close_pct: f64,              // 33%
    pub tp2_close_pct: f64,              // 50% of remaining
    pub daily_loss_limit_pct: f64,        // 5.0
    pub max_trades_per_day: u32,          // 10
    pub cooldown_seconds: u64,            // 300
    pub blacklisted_pairs: Vec<String>,
    pub use_btc_correlation_gate: bool,
}

pub struct Signal {
    pub symbol: String,
    pub direction: Direction,
    pub entry_zone: (Price, Price),
    pub stop_loss: Price,
    pub take_profits: Vec<Price>,
    pub quality_score: u32,
    pub source: String,
    pub parsed_at: i64,
}
```

**AI parsing stays Python** — the Rust side receives already-parsed `Signal` structs via `inject_signal()`.

### StrategyHost — The Glue

```rust
pub struct StrategyHost {
    adapter: Box<dyn ExecutionAdapter>,
    strategies: HashMap<StrategyId, Box<dyn Strategy>>,
}

impl StrategyHost {
    pub fn new(adapter: Box<dyn ExecutionAdapter>) -> Self;
    pub fn add_strategy(&mut self, strategy: Box<dyn Strategy>);

    pub fn on_bar(&mut self, bar: &Bar) -> Result<(), HostError> {
        for strategy in self.strategies.values_mut() {
            if strategy.instrument_id() == &bar.instrument_id {
                strategy.on_bar(bar, &mut *self.adapter)?;
            }
        }
        Ok(())
    }

    pub fn on_order_filled(&mut self, fill: &OrderFilled) -> Result<(), HostError>;
}
```

---

## 4. Indicators Library

All pure Rust. Single `Indicator` trait.

```rust
pub trait Indicator {
    type Output;
    fn new(period: u32) -> Self;
    fn update(&mut self, value: f64);
    fn value(&self) -> Self::Output;
    fn is_initialized(&self) -> bool;
    fn reset(&mut self);
    fn count(&self) -> u32;
}
```

### EMA — src/indicators/ema.py → indicators/ema.rs

Standard exponential smoothing: `EMA_t = α × price_t + (1 - α) × EMA_{t-1}` where `α = 2 / (period + 1)`.

### RSI — src/indicators/rsi.py → indicators/rsi.rs

Wilder's smoothed average for gains/losses. SMA seed for first `period` bars, then exponential smoothing.

### ATR — src/indicators/atr.py → indicators/atr.rs

True Range = max(H-L, |H-prev_C|, |L-prev_C|). Wilder's smoothing over `period` bars. Includes breakout detection (current bar range > 1.5× recent average ATR). Requires full OHLC bar input.

### Bollinger Bands — src/indicators/bollinger.py → indicators/bollinger.rs

Middle = SMA(period). Upper/Lower = Middle ± std_dev × σ. Provides `bandwidth` and `percent_b`. Uses exact rolling window for SMA.

### Support/Resistance — src/trend/support_resistance.py → indicators/support_resistance.rs

Pivot-point detection: a high is resistance if it's the highest in a window of N bars on each side. Levels merge when within 0.5% of each other. Tracks strength (bounce count) and last touch timestamp.

### Candlestick Patterns — src/trend/candlestick_patterns.py → indicators/candlestick.rs

Detects 7 patterns: Bullish/Bearish Engulfing, Hammer, Inverted Hammer, Doji, Morning/Evening Star. Returns whether a pattern confirms a given trend direction (used in signal scoring).

### Indicator Summary

| Indicator | Python source | Rust module | Key detail |
|---|---|---|---|
| EMA | `src/indicators/ema.py` | `ema.rs` | Standard α smoothing |
| RSI | `src/indicators/rsi.py` | `rsi.rs` | Wilder's smoothing, SMA seed |
| ATR | `src/indicators/atr.py` | `atr.rs` | Full OHLC, breakout detection |
| Bollinger | `src/indicators/bollinger.py` | `bollinger.rs` | Rolling window, %B |
| Support/Resistance | `src/trend/support_resistance.py` | `support_resistance.rs` | Pivot-point, merge levels |
| Candlestick | `src/trend/candlestick_patterns.py` | `candlestick.rs` | 7 patterns, directional confirmation |

---

## 5. Risk Management

### Circuit Breaker — src/risk/circuit_breaker.py → risk/circuit_breaker.rs

```rust
pub struct CircuitBreaker {
    config: CircuitBreakerConfig,
    equity_peak: f64,
    current_equity: f64,
    daily_starting_equity: f64,
    daily_realized_pnl: f64,
    tripped: bool,
    trip_reason: Option<String>,
}

pub struct CircuitBreakerConfig {
    pub max_drawdown_pct: f64,          // 10.0
    pub daily_loss_limit_pct: f64,      // 5.0
    pub initial_capital: f64,
}

impl CircuitBreaker {
    pub fn check(&self) -> Result<(), CircuitBreakerTripped>;
    pub fn record_pnl(&mut self, amount: f64, timestamp: i64);
    pub fn update_unrealized(&mut self, unrealized_pnl: f64);
    pub fn reset(&mut self);
}
```

Shared across all strategies in a StrategyHost. All strategies report PnL to the same instance — a trend loss counts against the grid's risk budget.

### Position Guard — src/risk/position_guard.py → risk/position_guard.rs

```rust
pub struct PositionGuard {
    config: PositionGuardConfig,
    open_positions: HashMap<String, OpenPosition>,
}

pub struct PositionGuardConfig {
    pub max_positions_per_pair: u32,     // 2
    pub max_total_positions: u32,        // 3
    pub max_exposure_pct: f64,           // 80.0
    pub max_base_exposure_pct: f64,      // 80.0
}

impl PositionGuard {
    pub fn can_open(&self, symbol: &str, strategy_id: &StrategyId, proposed_cost: f64, available_capital: f64) -> Result<(), PositionGuardRejection>;
    pub fn register(&mut self, position: OpenPosition);
    pub fn close(&mut self, symbol: &str);
}
```

### BNB Rebalancer — src/risk/bnb_rebalancer.py → risk/bnb_rebalancer.rs

```rust
pub struct BnbRebalancer {
    target_usdt: f64,        // 20
    min_usdt: f64,           // 10
    max_usdt: f64,           // 50
    use_limit_maker: bool,   // true
}

pub enum RebalanceAction {
    Buy { usdt_amount: f64 },
    Sell { usdt_amount: f64 },
}

impl BnbRebalancer {
    pub fn evaluate(&self, bnb_balance_usdt: f64, bnb_price: f64) -> Option<RebalanceAction>;
}
```

Binance-specific. Only active when running on Binance via Hummingbot.

### Risk Summary

| Component | Python source | Rust module | Shared? |
|---|---|---|---|
| Circuit Breaker | `src/risk/circuit_breaker.py` | `circuit_breaker.rs` | ✅ All strategies |
| Position Guard | `src/risk/position_guard.py` | `position_guard.rs` | ✅ All strategies |
| BNB Rebalancer | `src/risk/bnb_rebalancer.py` | `bnb_rebalancer.rs` | ⚠️ Binance/Hummingbot only |

---

## 6. Integration Diagram

```
┌─────────────────────────────────────────────────────────────────────┐
│                     trading-engine-core (Rust crate)                │
│                                                                     │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────────────────┐ │
│  │   Grid    │  │  Trend   │  │  Signal  │  │ Indicators + Risk  │ │
│  │ Strategy  │  │ Strategy │  │ Strategy │  │ (pure Rust)        │ │
│  └─────┬────┘  └─────┬────┘  └─────┬────┘  └────────────────────┘ │
│        └─────────────┼─────────────┘                                │
│              ┌───────┴───────┐                                      │
│              │ StrategyHost  │                                      │
│              └───────┬───────┘                                      │
│              ┌───────┴───────┐                                      │
│              │  Execution    │                                      │
│              │   Adapter     │                                      │
│              └───┬───────┬───┘                                      │
└──────────────────┼───────┼──────────────────────────────────────────┘
                   │       │
     ┌─────────────┘       └─────────────┐
     ▼                                   ▼
┌────────────────────┐        ┌────────────────────────┐
│  Hummingbot        │        │  NautilusTrader        │
│  Container         │        │  Container             │
│                    │        │                        │
│  HummingbotAdapter │        │  NautilusAdapter       │
│  (Python ~100 loc) │        │  (Python ~100 loc)     │
│       ↓            │        │       ↓                │
│  connector.buy()   │        │  order_factory.limit() │
│  connector.sell()  │        │  strategy.cache        │
│       ↓            │        │       ↓                │
│  Binance (crypto)  │        │  IB Gateway (forex)    │
└────────────────────┘        └────────────────────────┘
         │                              │
         └──────────┬───────────────────┘
                    ▼
              Mosquitto MQTT
              (shared status)
```

---

## 7. What Stays Python vs What Moves to Rust

| Component | Language | Why |
|---|---|---|
| Grid strategy logic | **Rust** | Core trading logic — shared across engines |
| Trend strategy logic | **Rust** | Core trading logic — shared across engines |
| Signal strategy logic | **Rust** | Execution + risk — shared across engines |
| Signal AI parser | **Python** | Calls DeepSeek/GLM HTTP API — I/O bound |
| Telegram bot | **Python** | Heavy library deps (python-telegram-bot, telethon) |
| ML regime classifier | **Python** | sklearn model serving in FastAPI |
| Indicators (EMA, RSI, ATR, BB) | **Rust** | Math-heavy hot paths |
| Risk (circuit breaker, position guard) | **Rust** | Shared across all strategies |
| BNB rebalancer | **Rust** | Binance-specific but used by grid |
| Hummingbot host script | **Python** | Must extend Hummingbot ScriptStrategyBase |
| NautilusTrader host script | **Python** | Must extend NautilusTrader Strategy class |
| Execution adapters | **Python** | Each wraps a specific engine's Python API |
| Config loading | **Rust** (serde) + **Python** (yaml) | Rust reads config, Python overrides with env vars |
| Journal / trade logging | **Python** | SQLite + telegram — not performance-critical |
| Dashboard (Streamlit) | **Python** | Stays as-is |

---

## 8. Docker Integration

### Dockerfile (Hummingbot — install Rust wheel)

```dockerfile
FROM hummingbot/hummingbot:version-2.13.0

# Install the shared Rust engine wheel
COPY dist/trading_engine_core-*.whl /tmp/
RUN /opt/conda/envs/hummingbot/bin/pip install --no-cache-dir /tmp/trading_engine_core-*.whl \
    && rm /tmp/trading_engine_core-*.whl

COPY requirements.txt /tmp/requirements.txt
RUN /opt/conda/envs/hummingbot/bin/pip install --no-cache-dir -r /tmp/requirements.txt

# Thin host script replaces the old 104k-line ta_grid_trend.py
COPY hummingbot_files/scripts/host_script.py /home/hummingbot/scripts/host_script.py

# Signal parser stays Python (AI HTTP calls)
COPY src/signals/signal_parser.py /home/hummingbot/src/signals/signal_parser.py

COPY config/ /home/hummingbot/config/
COPY models/ /home/hummingbot/models/

ENV SCRIPT_CONFIG=host_script_conf.yml
ENV HEADLESS_MODE=true
CMD ["/home/hummingbot/docker-entrypoint.sh"]
```

### Dockerfile.nautilus (NautilusTrader — same wheel)

```dockerfile
FROM python:3.12-slim-bookworm

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential curl git libssl-dev pkg-config \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
RUN pip install --no-cache-dir --upgrade pip setuptools wheel

# Same Rust wheel + NautilusTrader
COPY dist/trading_engine_core-*.whl /tmp/
COPY requirements.nautilus.txt /tmp/requirements.nautilus.txt
RUN pip install --no-cache-dir /tmp/trading_engine_core-*.whl \
    && pip install --no-cache-dir -r /tmp/requirements.nautilus.txt \
    && rm /tmp/trading_engine_core-*.whl /tmp/requirements.nautilus.txt

COPY src/nautilus/host.py /app/nautilus_host.py
COPY config/nautilus.yaml /app/config/nautilus.yaml

ENV PYTHONPATH=/app
CMD ["python", "/app/nautilus_host.py"]
```

---

## 9. Migration Path

### Branch Strategy

```
main                    ← current production (Python-only, UNTOUCHED)
  │
  ├── archive/python-engine   ← permanent safe branch of current state
  │
  └── feat/rust-engine-core   ← all Rust work happens here
        │
        ├── Phase 1: Rust crate skeleton + indicator port
        ├── Phase 2: Grid strategy port + MockAdapter + backtest
        ├── Phase 3: Trend strategy port
        ├── Phase 4: Signal strategy port
        ├── Phase 5: Hummingbot adapter + host script
        ├── Phase 6: NautilusTrader adapter + host script
        ├── Phase 7: Docker integration + CI
        └── Phase 8: Paper trade validation (both engines)
              │
              └── Merge to main ONLY after Phase 8 passes
```

### Phase Gates

| Phase | Deliverable | Validation |
|---|---|---|
| 0 | Branch `archive/python-engine` from `main` | `git checkout -b archive/python-engine` then push |
| 1 | Crate skeleton + 6 indicators | `cargo test` passes for all indicators |
| 2 | Grid strategy + `MockAdapter` | Backtest matches Python grid results |
| 3 | Trend strategy + 7-point scoring | Signal scores match Python for same input bars |
| 4 | Signal strategy + risk modules | Circuit breaker trips at same thresholds |
| 5 | Hummingbot adapter + host script | Paper trade Binance testnet — same PnL as Python |
| 6 | NautilusTrader adapter + host script | Paper trade IB Gateway — orders fill correctly |
| 7 | Docker + CI (build wheels in GitHub Actions) | Both containers build and start cleanly |
| 8 | Parallel paper trading 7+ days | Rust engine matches or beats Python performance |

**Phase 5 is the critical gate** — Rust engine vs Python engine on Binance testnet, side by side. Numbers must match before proceeding.

---

## 10. Strategy Source Mapping

| Strategy | Python source (lines) | Rust module (est.) | Key change |
|---|---|---|---|
| Grid | `grid_manager.py` + `grid_state.py` + `order_tracker.py` (259) | `grid.rs` (~350) | ATR-based spacing, state machine |
| Trend | `trend_manager.py` + `position_manager.py` + `support_resistance.py` + `candlestick_patterns.py` (672) | `trend.rs` (~500) | Same 7-point scoring, trailing stop |
| Signal | `signal_engine.py` + `signal_parser.py` + `signal_position.py` + `signal_risk.py` (1,416) | `signal.rs` (~400) | AI parsing stays Python, execution in Rust |
| Indicators | `ema.py` + `rsi.py` + `atr.py` + `bollinger.py` (138) | 4 modules (~400 total) | Same algorithms, native speed |
| Risk | `circuit_breaker.py` + `position_guard.py` + `bnb_rebalancer.py` (158) | 3 modules (~350 total) | Shared across strategies |

---

## 11. Build System

- **Rust crate**: `cargo build --release` produces `libtrading_engine_core.a` (static lib) and `trading_engine_core.so`/`.dylib` (cdylib)
- **Python wheel**: `maturin build --release` produces `trading_engine_core-X.Y.Z-cp312-cp312-linux_x86_64.whl`
- **CI**: GitHub Actions builds wheels for linux/amd64 (Hummingbot container) and macOS/arm64 (local dev)
- **Feature flag**: `--features python` enables PyO3 bindings; without it, pure Rust only (for `cargo test`)
