# Rust Migration Design: Hummingbot → Pure Rust Trading Engine

**Date:** 2026-05-30
**Status:** Approved
**Approach:** Inside-Out Migration (Approach B)

## Goals

- Eliminate Python runtime crashes (decimal errors, OOM, LIMIT_MAKER mismatches)
- Reduce RAM from ~1GB (bot + Docker) to ~50-100MB single binary
- Improve order execution latency
- Full feature parity with current Hummingbot system
- Deploy on same EC2 instance (t3.small) — no infra changes

## Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Migration approach | Inside-Out | Zero downtime, incremental, testable phases |
| Exchange connector | Build our own | Pure Rust, no Python dependency |
| NautilusTrader | Not used | Keeps Python dependency, contradicts goals |
| Paper trading | Built-in | Same connector trait, config flag to swap |
| Config format | Same YAML | Backward compatible with strategy.yaml |
| Telegram commands | Same commands | /status, /grid, /trend, /system, /capital |
| ML inference | ONNX Runtime | Convert sklearn model to .onnx, inference in Rust |
| Training pipeline | Stays Python | Batch job, not runtime — no need to port |

---

## Section 1: Architecture Overview

The Rust trading engine will be a **single-binary async application** with these top-level modules:

```
trading-engine-core/
├── src/
│   ├── main.rs              # Entry point — spawns runtime, loads config
│   ├── lib.rs               # Re-exports
│   ├── config.rs            # YAML config loader (serde_yaml)
│   │
│   ├── connector/           # Exchange communication
│   │   ├── mod.rs
│   │   ├── binance.rs       # REST + WebSocket client
│   │   ├── paper.rs         # Paper trade engine
│   │   └── types.rs         # Order, Fill, Balance, OrderBook types
│   │
│   ├── strategy/            # Strategy trait + implementations
│   │   ├── mod.rs           # Strategy trait definition
│   │   ├── grid.rs          # Grid strategy ported from Python
│   │   └── trend.rs         # Trend strategy ported from Python
│   │
│   ├── indicators/          # (existing) EMA, RSI, ATR, BB, S/R
│   ├── models/              # (existing) Bar, Currency, Order types
│   │
│   ├── ml/                  # ML inference
│   │   ├── mod.rs
│   │   └── regime.rs        # ONNX Runtime regime classifier
│   │
│   ├── risk/                # Risk management
│   │   ├── mod.rs
│   │   ├── position_guard.rs    # Position limits
│   │   └── circuit_breaker.rs   # Loss-based halt
│   │
│   ├── notifications/       # Telegram
│   │   ├── mod.rs
│   │   └── telegram.rs      # Telegram Bot API client
│   │
│   └── engine.rs            # Orchestrator — ticks strategies, manages state
│
├── Cargo.toml
├── config/
│   └── strategy.yaml        # Same YAML format as current
└── models/
    └── regime.onnx          # Converted ML model
```

**Key design decisions:**
- **tokio** for async runtime (WebSocket streams, REST calls, Telegram polling)
- **No Python dependency** in the final binary — PyO3 removed, Maturin removed
- **Same YAML config format** so existing `strategy.yaml` works unchanged
- **Same Telegram commands** (`/status`, `/grid`, `/trend`, `/system`, `/capital`)

---

## Section 2: Strategy Trait & Engine Orchestrator

### Strategy Trait

```rust
// src/strategy/mod.rs

#[async_trait]
pub trait Strategy {
    /// Unique name for logging/telegram (e.g. "grid", "trend")
    fn name(&self) -> &str;

    /// Trading pair (e.g. "BTCUSDT")
    fn trading_pair(&self) -> &str;

    /// Called on every tick (1-second default, configurable)
    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>>;

    /// Called when an order is filled (partial or full)
    async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>>;

    /// Called on startup — initial setup, place initial orders
    async fn on_start(&mut self, ctx: &StartContext) -> Result<Vec<OrderRequest>>;

    /// Graceful shutdown — cancel open orders, report final state
    async fn on_stop(&mut self) -> Result<()>;

    /// Current state snapshot for Telegram /status
    fn status(&self) -> StrategyStatus;
}
```

### TickContext (what strategies see)

```rust
pub struct TickContext {
    pub order_book: OrderBook,           // Top-of-book bids/asks
    pub recent_bars: Vec<Bar>,           // Last N candles for indicators
    pub balances: HashMap<String, f64>,  // Available balances
    pub open_orders: Vec<Order>,         // Our current open orders
    pub regime: Option<MarketRegime>,    // ML prediction (if enabled)
    pub timestamp: i64,                  // Unix millis
}
```

### Engine Orchestrator

```
┌─────────────────────────────────┐
│         Engine (main loop)       │
│                                  │
│  1. Binance WS ──→ order book    │
│  2. Binance WS ──→ kline stream  │
│  3. Every tick (1s):             │
│     ├─ Update indicators         │
│     ├─ Run ML regime classifier  │
│     ├─ Call strategy.on_tick()   │
│     ├─ Submit orders to exchange │
│     ├─ Process fills             │
│     └─ Check risk rules          │
│  4. Telegram poll (every 5s)     │
│  5. Status broadcast (hourly)    │
└─────────────────────────────────┘
```

**Key behaviors:**
- Strategies never talk to the exchange directly — they return `OrderRequest`, the engine submits
- Risk layer sits between engine and exchange — can veto any order
- Multiple strategies can run on different pairs simultaneously (same as now)
- Engine manages WebSocket reconnection, heartbeat, order book synchronization

---

## Section 3: Binance Connector

### REST Client

```rust
// src/connector/binance.rs

pub struct BinanceRest {
    client: reqwest::Client,
    api_key: String,
    api_secret: String,
    base_url: String,  // https://api.binance.com
}

impl BinanceRest {
    // Account
    async fn get_balances(&self) -> Result<Vec<Balance>>;
    async fn get_open_orders(&self, symbol: &str) -> Result<Vec<Order>>;
    async fn get_account_info(&self) -> Result<AccountInfo>;

    // Orders
    async fn place_order(&self, req: &OrderRequest) -> Result<OrderResponse>;
    async fn cancel_order(&self, symbol: &str, order_id: u64) -> Result<()>;
    async fn cancel_all_orders(&self, symbol: &str) -> Result<Vec<CancelResult>>;

    // Market data
    async fn get_order_book(&self, symbol: &str, limit: u16) -> Result<OrderBook>;
    async fn get_klines(&self, symbol: &str, interval: &str, limit: u16) -> Result<Vec<Bar>>;
    async fn get_exchange_info(&self) -> Result<ExchangeInfo>;
}
```

### WebSocket Client

```rust
pub struct BinanceWebSocket {
    streams: Vec<String>,
    // Uses tokio-tungstenite
}
```

**Streams subscribed per pair:**

| Stream | Purpose |
|--------|---------|
| `{symbol}@depth20@100ms` | Real-time order book (20 levels) |
| `{symbol}@kline_{interval}` | Candlestick updates for indicators |
| `{symbol}@trade` | Last trade price (paper trade fill simulation) |
| `userDataStream` | Order fills, cancellations, account changes |

### WebSocket Event Flow

```
Binance WebSocket
    │
    ├─ depth20@100ms ──→ OrderBook updated every 100ms
    │                     Engine caches latest snapshot
    │
    ├─ kline_1m ───────→ New bar completed
    │                     Indicators updated, strategy ticked
    │
    ├─ trade ───────────→ Price update
    │                     Used for paper trade fills
    │
    └─ userDataStream ──→ Order fill / cancel / reject
                          Triggers strategy.on_fill()
```

### Paper Trade Engine

```rust
// src/connector/paper.rs

pub struct PaperTradeEngine {
    balances: HashMap<String, f64>,
    open_orders: Vec<Order>,
    trade_history: Vec<Fill>,
}

impl PaperTradeEngine {
    /// LIMIT order fills when market price crosses the limit price
    /// MARKET order fills immediately at best bid/ask
    fn try_fill_orders(&mut self, trade_price: f64, trade_side: Side) -> Vec<Fill>;

    fn place_order(&mut self, req: &OrderRequest) -> Result<Order>;
    fn cancel_order(&mut self, order_id: &str) -> Result<()>;
    fn get_balances(&self) -> &HashMap<String, f64>;
    fn get_open_orders(&self) -> &[Order];
}
```

### Connector Trait

Both real and paper implement the same interface:

```rust
#[async_trait]
pub trait Connector {
    async fn place_order(&self, req: &OrderRequest) -> Result<OrderResponse>;
    async fn cancel_order(&self, symbol: &str, order_id: &str) -> Result<()>;
    async fn get_balances(&self) -> Result<HashMap<String, f64>>;
    async fn get_open_orders(&self, symbol: &str) -> Result<Vec<Order>>;
}
```

The engine holds `Box<dyn Connector>` — swap `BinanceConnector` for `PaperTradeEngine` via config flag.

### Cargo Dependencies

```toml
[dependencies]
tokio = { version = "1", features = ["full"] }
tokio-tungstenite = "0.21"
reqwest = { version = "0.12", features = ["json"] }
serde = { version = "1", features = ["derive"] }
serde_json = "1"
serde_yaml = "0.9"
hmac = "0.12"
sha2 = "0.10"
hex = "0.4"
chrono = "0.4"
tracing = "0.1"
tracing-subscriber = "0.3"
anyhow = "1"
async-trait = "0.1"
ort = { version = "2", features = ["loading-dynamic"] }
sysinfo = "0.30"
```

---

## Section 4: Grid Strategy Port

### Python → Rust Mapping

```
Python                          Rust
──────────────────────────────  ──────────────────────────────
GridManager::calc_levels()      GridStrategy::calculate_levels()
  ├─ Bollinger Bands center       ├─ Uses existing Rust BB indicator
  ├─ ATR-based spacing            ├─ Uses existing Rust ATR indicator
  ├─ Geometric buy spacing        ├─ Same formula, Rust f64
  └─ Uniform sell spacing         └─ Same formula, Rust f64

GridStateMachine                 GridStrategy::state field
  ├─ ACTIVE/PAUSED/DISABLED       ├─ Enum: GridState { Active, Paused, Disabled }
  ├─ Activation rules             ├─ on_tick() checks indicator conditions
  └─ Pause rules                  └─ Same conditions, Rust logic

OrderTracker                     GridStrategy::orders field
  ├─ Track placed orders          ├─ HashMap<OrderId, GridOrder>
  ├─ Refresh cycles               ├─ on_tick() checks age, refreshes
  └─ Fill detection               └─ on_fill() updates, places opposite order

Auto-compound                    GridStrategy::compound() method
  ├─ Reinvest profits             ├─ Called from on_fill() when sell fills
  └─ Adjust grid spacing          └─ Recalculates levels with new capital
```

### Grid Strategy State

```rust
pub struct GridStrategy {
    // Config
    pair: String,
    levels: u8,
    capital_pct: f64,
    reserve_pct: f64,

    // Indicators (existing Rust)
    bb: BollingerBands,
    atr: Atr,

    // State
    state: GridState,         // Active | Paused | Disabled
    grid_levels: Vec<GridLevel>,
    orders: HashMap<String, GridOrder>,
    total_pnl: f64,
    peak_equity: f64,

    // Risk
    max_drawdown_pct: f64,
}
```

### Auto-Compound Formula (preserved exactly)

```
When a SELL order fills:
  profit = (sell_price - buy_price) * quantity - fees
  new_capital = current_capital + profit
  growth_ratio = new_capital / initial_capital
  adjusted_levels = base_levels * growth_ratio
  Recalculate grid with new levels and capital
```

---

## Section 5: Trend Strategy Port

### Python → Rust Mapping

```
Python                              Rust
──────────────────────────────────  ──────────────────────────────
TrendManager::evaluate_signals()    TrendStrategy::on_tick()
  ├─ EMA cross (fast/slow/trend)      ├─ 3x existing Rust EMA indicators
  ├─ RSI overbought/oversold          ├─ Existing Rust RSI indicator
  ├─ Support/Resistance proximity     ├─ Existing Rust S/R indicator
  ├─ Candlestick pattern detection    ├─ Existing Rust candlestick detector
  └─ Signal scoring (need ≥ 3)        └─ Score accumulator, same weights

Confirmation ticks                   TrendStrategy::confirm_ticks field
  ├─ Require N consecutive signals     ├─ Counter, resets on reversal
  └─ Prevents false entries            └─ Same logic, Rust

Stop-loss / Take-profit              TrendStrategy position tracking
  ├─ S/L from support or ATR          ├─ Calculated at entry
  ├─ T/P from risk:reward (2:1)       ├─ Fixed ratio from entry
  └─ Trailing stop option             └─ ATR-based trail
```

### Trend Strategy State

```rust
pub struct TrendStrategy {
    // Config
    pair: String,
    min_signal_score: u8,       // Default: 3
    confirmation_ticks: u8,     // Consecutive signals required
    risk_reward_ratio: f64,     // Default: 2.0

    // Indicators (existing Rust)
    ema_fast: Ema,
    ema_slow: Ema,
    ema_trend: Ema,
    rsi: Rsi,
    atr: Atr,
    sr: SupportResistance,
    candlestick: Candlestick,

    // Signal state
    current_score: u8,
    confirm_count: u8,
    last_signal: Option<SignalDirection>,

    // Position tracking
    position: Option<TrendPosition>,
}

pub struct TrendPosition {
    side: Side,
    entry_price: f64,
    stop_loss: f64,
    take_profit: f64,
    quantity: f64,
    trailing_stop: Option<f64>,
}

pub enum SignalDirection {
    Bullish,
    Bearish,
}
```

### Signal Scoring (same weights as Python)

| Signal | Weight | Condition |
|--------|--------|-----------|
| EMA cross | +1 | Fast EMA crosses above/below slow EMA |
| Trend alignment | +1 | Price above/below trend EMA (50) |
| RSI confirmation | +1 | RSI < 30 (bullish) or > 70 (bearish) |
| S/R proximity | +1 | Price near support (buy) or resistance (sell) |
| Candlestick pattern | +1 | Bullish/bearish engulfing, hammer, etc. |
| **Minimum to enter** | **≥ 3** | Must meet threshold |

### Position Lifecycle

```
No position → Signal score ≥ 3 for N consecutive ticks → ENTER
  ├─ Calculate stop-loss: max(support_level, entry - 1.5*ATR)
  ├─ Calculate take-profit: entry + risk_reward * (entry - stop_loss)
  └─ Place LIMIT entry order

In position → Market moves → EXIT conditions checked every tick
  ├─ Price hits take-profit → CLOSE (profit)
  ├─ Price hits stop-loss → CLOSE (loss)
  ├─ Trailing stop triggered → CLOSE (profit)
  ├─ Opposite signal (score ≥ 3, opposite direction) → CLOSE + REVERSE
  └─ Circuit breaker triggered → CLOSE (emergency)
```

---

## Section 6: ML Regime Classifier

### Model Conversion Path

```
Python scikit-learn RandomForest → trained model (.pkl)
                                   ↓
  Convert using skl2onnx           → regime.onnx
                                   ↓
  Rust loads                       → ONNX Runtime inference
```

### Feature Engineering (same features as Python)

```rust
pub struct RegimeClassifier {
    session: ort::Session,
    feature_buffer: VecDeque<Bar>,
}

impl RegimeClassifier {
    /// Build feature vector from recent bars — must match Python training exactly
    fn extract_features(&self, bars: &[Bar]) -> Vec<f64> {
        // Same features:
        // - Returns (1m, 5m, 15m)
        // - Volatility (ATR-based)
        // - Volume ratio
        // - Price position relative to BB
        // - RSI value
        // - EMA slope
    }

    /// Run inference — returns regime + confidence
    fn predict(&self, bars: &[Bar]) -> Result<RegimePrediction>;
}

pub struct RegimePrediction {
    pub regime: MarketRegime,    // Ranging | Trending | Danger
    pub confidence: f64,         // 0.0 - 1.0
    pub probabilities: [f64; 3], // [ranging, trending, danger]
}

pub enum MarketRegime {
    Ranging,    // → Favor grid strategy
    Trending,   // → Favor trend strategy
    Danger,     // → Pause both strategies
}
```

**Training pipeline stays Python** — it's a batch job, not runtime. Only inference is ported.

---

## Section 7: Risk Management

### Risk Manager (unified interface)

```rust
pub struct RiskManager {
    position_guard: PositionGuard,
    circuit_breaker: CircuitBreaker,
}

impl RiskManager {
    /// Called BEFORE every order — approve or veto
    fn check_order(&self, req: &OrderRequest, ctx: &RiskContext) -> Result<()>;

    /// Called AFTER every fill to update risk state
    fn on_fill(&mut self, fill: &Fill);

    fn status(&self) -> RiskStatus;
}
```

### Position Guard

```rust
pub struct PositionGuard {
    max_position_pct: f64,        // e.g. 0.3 = 30% of balance
    max_open_orders: u32,         // per pair
    min_balance_reserve: f64,     // minimum USDT to keep
    current_exposure: HashMap<String, f64>,
}
```

### Circuit Breaker

```rust
pub struct CircuitBreaker {
    peak_equity: f64,
    current_equity: f64,
    max_drawdown_pct: f64,        // e.g. 0.05 = 5% halt
    cooldown_secs: u64,           // e.g. 1800 = 30 min cooldown
    halted_at: Option<Instant>,
    loss_window: Vec<(Instant, f64)>,  // sliding window loss tracking
    max_loss_per_window: f64,     // e.g. max 2% loss in 30 min
}
```

### Risk Enforcement Flow

```
Strategy returns OrderRequest
        │
        ▼
  RiskManager::check_order()
        │
        ├─ CircuitBreaker: Are we halted? ──YES──→ VETO
        │                    │
        │                   NO
        ├─ PositionGuard: Exceeds max exposure? ──YES──→ VETO
        │                    │
        │                   NO
        ├─ PositionGuard: Too many open orders? ──YES──→ VETO
        │                    │
        │                   NO
        └─ APPROVED ──→ Engine submits to Connector
```

---

## Section 8: Telegram Integration

### Telegram Client

```rust
pub struct TelegramBot {
    token: String,
    chat_id: String,
    client: reqwest::Client,
    last_update_id: i64,
    command_tx: mpsc::Sender<Command>,
    strategy_status_fn: Box<dyn Fn() -> String>,
}
```

### Commands (same as current)

| Command | What it returns |
|---------|----------------|
| `/status` | All strategies summary: state, PnL, open orders |
| `/grid` | Grid detail: levels, fills, auto-compound state |
| `/trend` | Trend detail: signal score, position, S/L, T/P |
| `/system` | RAM usage, CPU %, uptime, log size |
| `/capital` | USDT balance, total equity, available |

### Alert Types

| Alert | Trigger |
|-------|---------|
| Startup | Bot starts, config loaded |
| Shutdown | Graceful stop or crash |
| Order filled | Any order completes |
| Grid level filled | Buy or sell grid level triggered |
| Trend entry/exit | Position opened or closed |
| Circuit breaker | Trading halted due to drawdown |
| Error | Any unrecoverable error |

### Architecture Integration

```
Engine
  ├─ spawns TelegramBot::poll_loop() as tokio task
  ├─ receives Commands via mpsc channel
  ├─ on Command::Status → calls strategy.status() → formats → sends reply
  └─ alerts sent via TelegramBot::send_alert() from engine event handlers
```

---

## Section 9: Migration Phases

### Phase 0: Foundation (Week 1)

**Goal:** Set up the Rust binary structure, remove PyO3, add async runtime.

```
Tasks:
├─ Remove PyO3 + Maturin from Cargo.toml
├─ Add tokio, reqwest, tracing, serde_yaml dependencies
├─ Create main.rs with tokio::main, config loading
├─ Implement config.rs — parse existing strategy.yaml via serde
├─ Define Connector trait + strategy::Strategy trait
├─ Define all shared types (OrderRequest, Fill, OrderBook, etc.)
└─ Verify: binary compiles, loads config, prints parsed values
```

### Phase 1: Binance Connector (Week 1-2)

**Goal:** Read market data and place/cancel orders on Binance.

```
Tasks:
├─ Binance REST client
│   ├─ HMAC-SHA256 signature signing
│   ├─ Place/cancel orders
│   ├─ Get balances, open orders
│   └─ Get order book, klines
├─ Binance WebSocket client
│   ├─ Connect + subscribe to streams per pair
│   ├─ Parse depth, kline, trade, userData messages
│   ├─ Auto-reconnect with exponential backoff
│   └─ Maintain local order book snapshot
├─ Integration test against Binance testnet
└─ Verify: can read order book, place/cancel test order on testnet
```

### Phase 2: Paper Trade Engine (Week 2)

**Goal:** Simulate fills without real money.

```
Tasks:
├─ PaperTradeEngine implementing Connector trait
├─ LIMIT order fills when market price crosses limit price
├─ MARKET order fills at best bid/ask
├─ Balance tracking (configurable paper balance)
├─ Trade history / fill logging
└─ Verify: place paper orders, see simulated fills from real market data
```

### Phase 3: Grid Strategy (Week 2-3)

**Goal:** Port grid strategy from Python to Rust, test with paper trading.

```
Tasks:
├─ Port GridManager::calc_levels() → GridStrategy::calculate_levels()
├─ Port GridStateMachine → GridState enum + transition logic
├─ Port OrderTracker → HashMap-based order tracking
├─ Port auto-compound logic
├─ Wire to existing Rust indicators (BB, ATR)
├─ Integration test: feed historical bars, verify levels match Python
└─ Verify: grid strategy runs on paper trade engine with real market data
```

### Phase 4: Trend Strategy (Week 3)

**Goal:** Port trend strategy from Python to Rust.

```
Tasks:
├─ Port TrendManager::evaluate_signals() → TrendStrategy::on_tick()
├─ Port signal scoring system (5 signals, same weights)
├─ Port confirmation tick logic
├─ Port position management (entry, S/L, T/P, trailing stop)
├─ Wire to existing Rust indicators (EMA x3, RSI, ATR, S/R, Candlestick)
├─ Integration test: feed historical bars, verify signals match Python
└─ Verify: trend strategy runs on paper trade engine
```

### Phase 5: Risk Management (Week 3-4)

**Goal:** Port risk layer to protect live trading.

```
Tasks:
├─ Implement PositionGuard (max exposure, max orders, reserve)
├─ Implement CircuitBreaker (drawdown halt, cooldown, loss window)
├─ RiskManager unified check → approve/veto before every order
├─ Wire into engine loop
└─ Verify: circuit breaker triggers on simulated drawdown
```

### Phase 6: ML Inference (Week 4)

**Goal:** Port regime classifier inference to Rust via ONNX.

```
Tasks:
├─ Convert existing Python model to .onnx (one-time Python script)
├─ Implement RegimeClassifier using ort crate
├─ Port feature engineering (must match Python training exactly)
├─ Integration test: compare Rust vs Python predictions on same data
├─ Wire regime output into TickContext for strategies
└─ Verify: Rust predictions match Python predictions within tolerance
```

### Phase 7: Telegram Integration (Week 4-5)

**Goal:** Full monitoring parity with current Telegram bot.

```
Tasks:
├─ Telegram Bot API client (send messages, poll commands)
├─ Implement all 5 commands (/status, /grid, /trend, /system, /capital)
├─ Implement all alert types (startup, fills, errors, circuit breaker)
├─ Wire into engine as tokio task
└─ Verify: send /status from Telegram, get formatted response
```

### Phase 8: Engine Integration & Testing (Week 5)

**Goal:** Wire everything together, stress test.

```
Tasks:
├─ Engine orchestrator — tick loop, fill processing, risk checks
├─ Multi-pair support (same engine, multiple strategy instances)
├─ Graceful shutdown (cancel orders, save state, notify Telegram)
├─ State persistence (save/load grid state, positions to disk)
├─ End-to-end test: full trading session on paper trade
├─ Run parallel with Hummingbot for 48h — compare results
└─ Verify: behavior matches Hummingbot on same market conditions
```

### Phase 9: Cutover (Week 5-6)

**Goal:** Switch from Hummingbot to Rust binary on production EC2.

```
Tasks:
├─ Build release binary (cross-compile or build on EC2)
├─ Stop Hummingbot Docker container
├─ Start Rust binary as systemd service (no Docker needed)
├─ Set up logrotate for Rust binary logs
├─ Monitor for 24h via Telegram
├─ Remove Docker, Python, Hummingbot from EC2
└─ Verify: stable, < 100MB RAM, zero crashes
```

### Timeline Summary

```
Week 1     ████░░░░░░░░░░░░  Foundation + Binance Connector
Week 2     ████████░░░░░░░░  Paper Engine + Grid Strategy
Week 3     ████████████░░░░  Trend Strategy + Risk Management
Week 4     ██████████████░░  ML Inference + Telegram
Week 5     ████████████████  Integration Testing
Week 6     ████████████████  Cutover + Cleanup
```

**Each phase ends with a working, testable system.** The Hummingbot bot stays running until Phase 9.
