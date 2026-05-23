# 🤖 TA-Enhanced Multi-Pair Grid + Trend Bot
### Dual-Engine Strategy | ML Regime Classifier | 5 Trading Pairs | Hummingbot v2 | Binance FZE | Dubai, UAE

> A fully automated **Multi-Pair Grid + Trend Bot** powered by real-time Technical Analysis and a per-pair ML regime classifier. Runs two engines in parallel — a grid engine for ranging markets and a trend engine for directional moves — across BTC-USDT, ETH-USDT, BNB-USDT, DOGE-USDT, and XRP-USDT. Uses Bollinger Bands, RSI, EMA 200, ATR, and a Random Forest regime classifier (RANGING/TRENDING/DANGER) to dynamically switch strategies, with a cross-asset correlation gate that halts altcoin buys when BTC enters DANGER — all on Binance FZE (VARA-licensed).

---

## 📌 Project Overview

| Field                   | Details                                                  |
|-------------------------|----------------------------------------------------------|
| **Framework**           | Hummingbot v2 (Script API)                               |
| **Strategy**            | TA-Enhanced Multi-Pair Grid + Trend Bot                  |
| **Pair**                | BTC-USDT, ETH-USDT, BNB-USDT, DOGE-USDT, XRP-USDT (multi-pair) |
| **Exchange**            | Binance FZE — VARA + ADGM licensed (UAE)                 |
| **Indicators**          | Bollinger Bands · RSI · EMA 200 · ATR · ML Regime Classifier |
| **Timeframe**           | 1h candles for TA signals                                |
| **Language**            | Python 3.11+                                             |
| **Deployment**          | AWS EC2 (Tokyo) via GitHub Actions + Docker               |
| **Notifications**       | Telegram real-time alerts + interactive commands          |
| **Status**              | Paper Trading (Multi-Pair)                               |

---

## 🧠 Strategy Logic

The bot runs a **dual-engine architecture** — a grid engine for ranging markets and a trend engine for directional moves. Each of the 5 trading pairs is evaluated independently every time a 1h candle closes. A per-pair ML regime classifier determines which engine to activate, while a cross-asset correlation gate protects altcoin positions when BTC signals danger.

### Multi-Pair Dual-Engine Flow

```
Every 1h candle closes (per pair):
│
├─ Calculate: Bollinger Bands (20,2) · RSI (14) · EMA 200 · ATR (14)
│
├─ ML Regime Classification (Random Forest)
│    └─ RANGING  → Grid Engine active
│    └─ TRENDING → Trend Engine active
│    └─ DANGER   → Both engines pause, cancel all orders
│
├─ Cross-Asset Correlation Gate
│    └─ IF BTC regime == DANGER → halt all altcoin BUY orders
│    └─ IF BTC regime == RANGING/TRENDING → altcoins resume normally
│
├─ Grid Engine (RANGING regime)
│    ├─ IF price > EMA200 AND RSI < 65          ← Uptrend, not overbought
│    │    └─ Grid ACTIVE (long-biased)
│    │       ├─ Range:   Lower BB  →  Upper BB
│    │       └─ Spacing: ATR × 1.5
│    │       ├─ Skip BUY levels when RSI > 60
│    │       └─ Skip SELL levels when RSI < 40
│    │       └─ Position sizing: confidence-weighted (ML regime probability)
│    │
│    ├─ IF RSI > 70 OR price < EMA200           ← Overbought or downtrend
│    │    └─ Grid PAUSED — cancel all orders, hold USDT
│    │
│    └─ IF RSI < 35 AND price near Lower BB     ← Oversold at support
│         └─ Grid REACTIVATES at new range
│
└─ Trend Engine (TRENDING regime)
     ├─ EMA 20/50 crossover for entry signals
     ├─ RSI confirmation (40–70 band)
     ├─ Signal score (min 3/5 confirmations required)
     ├─ Trailing stop: 1.5% activation, 1.5% trail
     └─ Risk: 2% per trade, max 2 concurrent positions
```

### Indicator Roles

| Indicator | Role | Setting |
|-----------|------|---------|
| **Bollinger Bands** | Sets grid upper/lower range automatically | Period 20, StdDev 2 |
| **RSI** | Activates grid when oversold, pauses when overbought; filters individual levels | Period 14 |
| **EMA 200** | Trend filter — only run grid in uptrend | Period 200 |
| **ATR** | Sets dynamic grid spacing based on volatility | Period 14, multiplier 1.5 |
| **ML Regime Classifier** | Per-pair Random Forest — classifies market as RANGING, TRENDING, or DANGER | Per-pair `.pkl` model |
| **Correlation Gate** | Cross-asset safety — halts altcoin buys when BTC enters DANGER | BTC regime monitor |

---

## 📁 Project Structure

```
ta-grid-bot/
│
├── .env                          # Secrets — NEVER COMMIT
├── .env.example                  # Template — commit this
├── .gitignore
│
├── hummingbot_files/
│   └── scripts/
│       ├── ta_grid_trend.py      # Main Hummingbot v2 script (dual-engine strategy)
│       ├── capital_manager.py    # Multi-pair capital allocation and auto-compound
│       └── pair_engine.py        # Per-pair engine orchestration (grid + trend)
│
├── src/
│   ├── indicators/
│   │   ├── bollinger.py          # Bollinger Bands calculator
│   │   ├── rsi.py                # RSI calculator
│   │   ├── ema.py                # EMA calculator
│   │   └── atr.py                # ATR + grid spacing calculator
│   │
│   ├── grid/
│   │   ├── grid_manager.py       # Grid creation, placement, rebalancing
│   │   ├── grid_state.py         # Grid state: ACTIVE / PAUSED / REACTIVATING
│   │   └── order_tracker.py      # Track open/filled/cancelled grid orders
│   │
│   ├── trend/
│   │   ├── trend_manager.py      # Trend engine — EMA crossovers, signal scoring
│   │   ├── position_manager.py   # Trend position lifecycle (entry, trailing stop, exit)
│   │   ├── support_resistance.py # Support/resistance level detection
│   │   ├── candlestick_patterns.py # Candlestick pattern recognition
│   │   └── trend_journal.py      # SQLite logger for trend trades
│   │
│   ├── ml/
│   │   ├── regime_classifier.py  # Per-pair Random Forest regime classifier (RANGING/TRENDING/DANGER)
│   │   └── train_pipeline.py     # ML training pipeline with feature engineering + labeling
│   │
│   ├── risk/
│   │   ├── circuit_breaker.py    # Halt if drawdown > threshold
│   │   ├── position_guard.py     # Max exposure, min USDT reserve
│   │   └── bnb_rebalancer.py     # Auto-maintain BNB balance for 25% fee discount
│   │
│   ├── data/
│   │   ├── candle_feed.py        # Fetch 1h OHLCV from Binance REST
│   │   ├── feature_engineering.py # ML feature computation from OHLCV data
│   │   ├── label_generation.py   # Regime label generation for training
│   │   └── ws_feed.py            # Real-time price via WebSocket
│   │
│   ├── journal/
│   │   └── trade_journal.py      # SQLite logger — every trade stored
│   │
│   └── notifications/
│       ├── telegram_bot.py       # Grid state alerts
│       ├── telegram_commands.py  # Interactive Telegram commands (/status, /pnl, etc.)
│       └── pnl_reporter.py       # Hourly/daily/monthly P&L summaries
│
├── models/
│   ├── regime_ETH-USDT.pkl       # Per-pair trained Random Forest models
│   ├── regime_DOGE-USDT.pkl
│   ├── regime_BNB-USDT.pkl
│   └── regime_XRP-USDT.pkl
│
├── config/
│   └── strategy.yaml             # All strategy parameters (non-secret)
│
├── backtest/
│   ├── vectorbt_sweep.py         # Parameter optimization sweep
│   ├── walk_forward.py           # Out-of-sample validation
│   ├── ml_walk_forward.py        # ML model walk-forward validation
│   └── reporting.py              # Backtest report generation
│
├── .github/workflows/
│   ├── deploy.yml                # CI/CD — build and deploy to EC2
│   ├── sweep.yml                 # Weekly VectorBT parameter sweep
│   └── retrain.yml               # Monthly ML model retraining
│
├── tests/
│   ├── test_indicators.py
│   ├── test_grid_manager.py
│   ├── test_circuit_breaker.py
│   ├── test_trend_manager.py
│   ├── test_position_manager.py
│   ├── test_pair_engine.py
│   ├── test_capital_manager.py
│   ├── test_correlation_gate.py
│   ├── test_ml_multi_pair.py
│   ├── test_ml_hot_reload.py
│   ├── test_bnb_rebalancer.py
│   └── ... (20+ test files)
│
├── logs/                         # Auto-generated (gitignored)
├── reports/                      # Performance reports (gitignored)
│
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
└── README.md
```

---

## 🔑 Environment Variables

Copy `.env.example` to `.env` and fill in your values. **Never commit `.env`.**

```bash
cp .env.example .env
```

### Full Variable Reference

| Variable | Required | Description |
|----------|----------|-------------|
| `BINANCE_API_KEY` | ✅ | Binance FZE API key (trade permission only) |
| `BINANCE_API_SECRET` | ✅ | Binance FZE API secret |
| `BINANCE_ED25519_KEY_PATH` | ⚪ | Path to Ed25519 private key for faster signing |
| `BINANCE_TESTNET_API_KEY` | ✅ | Testnet key from testnet.binance.vision |
| `BINANCE_TESTNET_API_SECRET` | ✅ | Testnet secret |
| `OKX_API_KEY` | ⚪ | OKX failover API key |
| `OKX_API_SECRET` | ⚪ | OKX failover API secret |
| `OKX_PASSPHRASE` | ⚪ | OKX API passphrase |
| `OKX_DEMO_MODE` | ⚪ | Set to `1` to enable OKX paper trading |
| `TELEGRAM_BOT_TOKEN` | ✅ | Create bot via @BotFather on Telegram |
| `TELEGRAM_CHAT_ID` | ✅ | Your chat ID via @userinfobot on Telegram |
| `ENV` | ✅ | `paper` or `live` |
| `LOG_LEVEL` | ✅ | `DEBUG`, `INFO`, `WARNING`, or `ERROR` |
| `GRID_CAPITAL_USDT` | ✅ | Total USDT allocated to grid |
| `GRID_LEVELS` | ✅ | Number of orders above and below mid price |
| `MAX_DRAWDOWN_PCT` | ✅ | Circuit breaker threshold (%) |
| `MIN_USDT_RESERVE` | ✅ | USDT to always keep out of the grid |

---

## ⚙️ Strategy Configuration (`config/strategy.yaml`)

```yaml
# ── Pairs & Exchange ─────────────────────────────────────────────
pairs:
  - symbol: "DOGE-USDT"
    step_size: 1
    enabled: true
  - symbol: "ETH-USDT"
    step_size: 0.001
    enabled: true
  - symbol: "BTC-USDT"
    step_size: 0.00001
    enabled: false               # BTC disabled — ML regime classifier active on altcoins
  - symbol: "BNB-USDT"
    step_size: 0.01
    enabled: true
  - symbol: "XRP-USDT"
    step_size: 0.1
    enabled: true

exchange: "binance"
timeframe: "1h"

# ── Grid Parameters ───────────────────────────────────────────────
grid:
  levels: 5                   # Orders on each side of mid price
  capital_usdt: 5000          # Total USDT allocated across pairs
  min_usdt_reserve: 100       # Always keep this in reserve
  order_refresh_time: 60      # Seconds between order refresh

# ── Indicator Settings ────────────────────────────────────────────
indicators:
  bollinger:
    period: 20
    std_dev: 2.0

  rsi:
    period: 14
    oversold: 35              # Grid activates below this
    overbought: 70            # Grid pauses above this

  ema:
    period: 200               # Trend filter: only run grid above EMA200

  atr:
    period: 14
    spacing_multiplier: 1.5   # Grid spacing = ATR × this value

# ── Grid State Rules ──────────────────────────────────────────────
rules:
  activate_conditions:
    - "price > ema_200"
    - "rsi < 65"
  pause_conditions:
    - "price < ema_200"
    - "rsi > 70"
  reactivate_conditions:
    - "rsi < 35"
    - "price near lower_bb"

# ── Trend Engine ──────────────────────────────────────────────
trend:
  enabled: true
  capital: 5000                 # Trend engine capital pool
  ema_fast: 20
  ema_slow: 50
  ema_trend: 200
  rsi_period: 14
  rsi_min: 40
  rsi_max: 70
  min_signal_score: 3
  risk_per_trade_pct: 2.0
  max_position_pct: 25.0
  max_positions: 2
  trailing_stop_pct: 1.5
  trailing_activation_pct: 1.5
  rr_ratio: 2.0

# ── Fee Optimization ───────────────────────────────────────────
fee_optimization:
  bnb_target_usdt: 20         # Target BNB balance in USDT
  bnb_min_usdt: 10            # Buy BNB when balance drops below this
  bnb_max_usdt: 50            # Sell excess BNB when above this
  use_limit_maker: true       # Use LIMIT_MAKER (post-only) for all orders

# ── Risk Management ───────────────────────────────────────────────
risk:
  max_drawdown_pct: 10
  daily_loss_limit_pct: 5
  max_base_exposure_pct: 80
```

---

## 📈 Auto-Compound & Capital Management

### Auto-Compound

The bot automatically reinvests profits by scaling order sizes based on equity growth:

```
compound_capital = base_capital × (current_equity / initial_equity)
```

- **At startup:** base capital = $1,000, initial equity captured
- **As profits grow:** compound capital increases, order sizes get bigger
- **Floor protected:** never goes below your original base capital
- **Works in paper and live** — growth ratio normalizes regardless of starting balance

### Updating Capital

No redeploy needed. Change grid capital directly from Telegram:

```
/capital 5000       → sets grid to $5,000
/capital 25000       → sets grid to $25,000
```

Order sizes recalculate on the next grid refresh (within 1 hour).

### Expected Returns

| Capital | Order size | Daily (normal) | Monthly | Yearly (compounded) |
|---------|-----------|----------------|---------|---------------------|
| $5K | $375 | $10-15 | $300-450 | ~$5,500 |
| $10K | $750 | $20-30 | $600-900 | ~$11,000 |
| $25K | $1,875 | $50-75 | $1,500-2,250 | ~$27,000 |

---

## 🚀 Installation & Setup

### 1. Install Hummingbot

```bash
# Via Docker (recommended)
curl -o docker-compose.yml https://raw.githubusercontent.com/hummingbot/hummingbot/master/docker-compose.yml
docker compose up -d
```

### 2. Clone This Project

```bash
git clone https://github.com/your-username/ta-grid-bot.git
cd ta-grid-bot
```

### 3. Set Up Environment

```bash
cp .env.example .env
nano .env                         # Fill in your keys
pip install -r requirements.txt
```

### 4. Copy Strategy to Hummingbot

```bash
cp hummingbot_files/scripts/ta_grid_trend.py \
   ~/hummingbot/scripts/ta_grid_trend.py
```

### 5. Connect Binance in Hummingbot

```bash
# Inside Hummingbot terminal
connect binance
# → Enter BINANCE_API_KEY
# → Enter BINANCE_API_SECRET
```

---

## ▶️ Running the Bot

```bash
# Paper trading (30 days minimum before going live)
start --script ta_grid_trend.py --conf conf_ta_grid_trend_conf.yml

# Live trading (only after passing all 5 testing stages)
start --script ta_grid_trend.py --conf conf_ta_grid_live.yml
```

---

## 📊 Grid Visualization

```
Per-Pair Grid (e.g. ETH-USDT in RANGING regime):
Price
  │
Upper BB ──── 🔴 SELL order 5   ($2,800)
              🔴 SELL order 4   ($2,770)
              🔴 SELL order 3   ($2,740)
              🔴 SELL order 2   ($2,710)
              🔴 SELL order 1   ($2,680)
Mid Price ─── ◆  Current ETH   ($2,650)
              🟢 BUY  order 1   ($2,620)
              🟢 BUY  order 2   ($2,590)
              🟢 BUY  order 3   ($2,560)
              🟢 BUY  order 4   ($2,530)
Lower BB ──── 🟢 BUY  order 5   ($2,500)
  │
  └─ Spacing = ATR × 1.5  (recalculated every 1h)
     Range   = Lower BB → Upper BB  (recalculated every 1h)

  ⏸️  ML DANGER or RSI > 70 or price < EMA200 → ALL ORDERS CANCELLED
  🔍  BTC DANGER → all altcoin BUY orders halted (correlation gate)
  🔍  RSI > 60 → skip BUY levels  |  RSI < 40 → skip SELL levels
  📐  Position sizes scaled by ML regime confidence (0.5x–1.5x)
```

---

## 🛡️ Risk Management

| Parameter | Value | Description |
|-----------|-------|-------------|
| `max_drawdown_pct` | 10% | Bot halts if portfolio drops 10% from peak |
| `daily_loss_limit_pct` | 5% | Circuit breaker triggers if -5% in 24h |
| `max_base_exposure_pct` | 80% | Never deploy more than 80% capital in any single asset |
| `min_usdt_reserve` | $100 | Always keep $100 USDT untouched |
| Correlation Gate | BTC DANGER | Halts all altcoin buys when BTC regime is DANGER |
| ML Confidence Floor | 0.5x | Positions scaled down when regime confidence is low |

---

## 📬 Telegram Alerts & Commands

### Alerts (automatic)

| Event | Alert | Content |
|-------|-------|---------|
| Grid activated | 🟢 | Range, spacing, levels, capital |
| Grid paused | ⏸️ | Reason (RSI/EMA), current price |
| Grid reactivated | 🔄 | New range, RSI value, EMA status |
| Buy order filled | 💚 | Price, qty, grid level |
| Sell order filled | 🔴 | Price, qty, PnL for that level |
| Circuit breaker | 🚨 | Drawdown %, bot halted |
| Daily P&L report | 📅 | Auto-sent at midnight UTC — trades, win rate, P&L, fees, equity |

### Commands (interactive)

| Command | Description |
|---------|-------------|
| `/status` | Grid state, mode, uptime, levels, spacing, capital |
| `/pnl` | P&L summary — today, week, month, all-time with win rates |
| `/balance` | USDT, BTC, equity, and grid capital with growth % |
| `/capital <amount>` | Update grid capital on the fly (no redeploy needed) |
| `/price` | Live prices from Binance + RSI, EMA, Bollinger Bands, ML regime per pair |
| `/trades` | Last 5 closed trades |
| `/pending` | Open orders with prices and amounts |
| `/system` | CPU, memory, disk usage |
| `/fees` | Total fees paid today / this week / this month |
| `/errors` | Recent errors from crash log |
| `/logs` | Last 30 lines from today's bot log |
| `/pause` | Manually pause grid trading |
| `/resume` | Resume grid from manual pause |
| `/reset` | Reset circuit breaker after halt |
| `/clear` | Clear logs and grid state (preserves trade history) |
| `/help` | Command reference |

---

## 📊 P&L Tracking

Both systems run simultaneously. They serve different purposes and complement each other.

---

### 1. 📱 Telegram Alerts (Real-Time)

Every trade fires an instant alert to your phone. A daily P&L summary is auto-sent at midnight UTC.

**Per-trade alert (every close):**
```
💚 Trade Closed — ETH/USDT
━━━━━━━━━━━━━━━━━━━━━━
📈 BUY  |  Grid Level 3
⏱ Duration:    45 min
🔵 Entry:      $2,560.00
🔵 Exit:       $2,620.00
📦 Qty:        0.05 ETH
━━━━━━━━━━━━━━━━━━━━━━
💰 Gross PnL:  +$3.00
💸 Fee:        -$0.13
📊 Net PnL:    +$2.87
━━━━━━━━━━━━━━━━━━━━━━
RSI: 42.3  |  Grid: ACTIVE  |  ML Regime: RANGING (0.87)
```

**Daily summary (sent at midnight UTC):**
```
📅 Daily Report — Apr 04, 2026
━━━━━━━━━━━━━━━━━━━━━━━━
📊 Trades:      24  (✅18 / ❌6)
🎯 Win Rate:    75%
━━━━━━━━━━━━━━━━━━━━━━━━
💰 Gross PnL:   +$47.20
💸 Fees paid:   -$8.30
📈 Net Today:   +$38.90
━━━━━━━━━━━━━━━━━━━━━━━━
📆 This Week:   +$142.50
🗓 This Month:  +$380.00
```

**Schedule:**
| Report | When |
|--------|------|
| Per-trade alert | Instantly after every close |
| Daily summary | Auto-sent at midnight UTC (trades, P&L, fees, equity growth) |

---

### What Gets Stored Per Trade

Every trade is logged with full context:

```python
{
  "timestamp":    "2026-05-23 14:00:00",
  "pair":         "ETH-USDT",
  "side":         "BUY",
  "entry_price":  2560.00,
  "exit_price":   2620.00,
  "quantity":     0.05,
  "gross_pnl":    +3.00,
  "fee":          -0.13,
  "net_pnl":      +2.87,
  "grid_level":   3,
  "duration_min": 45,
  "rsi":          42.3,
  "bb_upper":     2800,
  "bb_lower":     2500,
  "ema_200":      2520,
  "atr":          30,
  "grid_state":   "ACTIVE",
  "ml_regime":    "RANGING",
  "ml_confidence": 0.87,
  "btc_regime":   "RANGING"
}
```

---

All 5 stages must pass before scaling capital.

```
Stage 1 — VectorBT Parameter Sweep        (Hours)
  └─ Optimize BB period, RSI thresholds, ATR multiplier
  └─ Pass: Sharpe > 1.0, 200+ trades, positive expectancy

Stage 2 — Walk-Forward Backtest           (1–2 Days)
  └─ Out-of-sample test across bull/bear/sideways markets
  └─ Pass: Consistent results across all conditions

Stage 3 — Paper Trading on Testnet        (30–60 Days)
  └─ Real market data, zero risk
  └─ Pass: 100+ trades, profitable, no crashes or bugs

Stage 4 — Live Micro-Stake ($100–200)     (2 Weeks)
  └─ Real money, minimal exposure
  └─ Pass: Results match paper trading within 10%

Stage 5 — Full Capital Deployment
  └─ Scale: 25% → 50% → 100% of target allocation
  └─ Never skip straight to full capital
```

---

## 🌍 UAE Regulatory Compliance (2026)

| Item | Status |
|------|--------|
| **Personal income tax** | ✅ 0% |
| **Capital gains tax** | ✅ 0% (Cabinet Decision 100, retroactive 2018) |
| **VAT on crypto** | ✅ 0% exempt |
| **License for personal algo trading** | ✅ Not required |
| **Binance FZE** | ✅ VARA (Apr 2024) + ADGM (Jan 2026) |
| **Privacy tokens** | ❌ Banned under VARA — do not trade |
| **KYC** | Emirates ID + Passport + Proof of Address |
| **CARF reporting** | ⚠️ Starts Jan 2027 — keep trade records now |

---

## 🚀 Deployment (AWS EC2 + GitHub Actions)

The bot runs on an AWS EC2 instance in Tokyo, deployed via GitHub Actions CI/CD. Every push to `main` triggers an automatic rebuild and redeploy.

---

### Architecture

```
GitHub (push to main)
  └─► GitHub Actions workflow
       └─► SSH into EC2 via AWS SSM
            └─► docker compose build + up
                 └─► Hummingbot container (dual-engine strategy + ML models + indicators)
```

---

### Infrastructure

| Component | Details |
|-----------|---------|
| **Cloud** | AWS EC2 (ap-northeast-1 — Tokyo) |
| **Container** | Docker + docker-compose |
| **CI/CD** | GitHub Actions → AWS SSM Session Manager |
| **Base image** | `hummingbot/hummingbot:latest` |
| **Logs** | Docker volume mount at `./logs/` |

---

### Deploy Process

Pushing to `main` triggers the GitHub Actions workflow:

1. Checkout code
2. Connect to EC2 via AWS SSM
3. Pull latest code
4. Rebuild Docker container
5. Restart with `docker compose up -d`
6. Bot sends startup alert to Telegram

---

### Monitoring

| What | How |
|------|-----|
| Live logs | `docker compose logs -f` on EC2 |
| Telegram commands | `/status`, `/pnl`, `/trades`, `/errors` |
| Trade alerts | Automatic on every fill |
| Daily summary | Sent at midnight UTC |

---

### Paper → Live Transition

Update the `ENV` variable from `paper` to `live` in your `.env` file on the EC2 instance, then restart:

```bash
docker compose restart
```

No code changes needed — same strategy, real money.

---

## 🧬 ML & Automation

### Per-Pair Regime Classifier

Each enabled pair runs its own trained Random Forest model that classifies the current market into one of three regimes:

| Regime | Action |
|--------|--------|
| **RANGING** | Grid engine activates — Bollinger Bands range, ATR spacing |
| **TRENDING** | Trend engine activates — EMA crossovers, trailing stops |
| **DANGER** | Both engines pause — all orders cancelled, hold USDT |

Models are trained using the pipeline in `src/ml/train_pipeline.py` with features engineered from OHLCV data (returns, volatility, RSI, Bollinger %b, ATR ratio). Labels are generated by `src/data/label_generation.py` using a rule-based approach over historical data.

### Cross-Asset Correlation Gate

BTC drives the broader crypto market. When the BTC regime classifier outputs DANGER, the correlation gate automatically halts all altcoin (ETH, BNB, DOGE, XRP) buy orders regardless of their own regime. Altcoin sells are allowed to exit positions safely. When BTC returns to RANGING or TRENDING, altcoin operations resume normally.

### Confidence-Weighted Position Sizing

Position sizes are scaled by the ML regime confidence score rather than using fixed amounts:

```
actual_size = base_size × confidence_multiplier
```

High-confidence RANGING classifications (0.85+) get 1.5x position sizing. Lower confidence scores scale down to 0.5x minimum. This reduces exposure during uncertain market conditions without fully pausing.

### Auto-Retraining Pipeline

Two GitHub Actions workflows keep the models current:

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| `sweep.yml` | Weekly | Runs VectorBT parameter sweep to validate current settings against latest market data |
| `retrain.yml` | Monthly | Retrains all per-pair Random Forest models on fresh data and deploys new `.pkl` files |

### Hot-Reload for Zero-Downtime Updates

The regime classifier supports hot-reloading of model files. When a new `.pkl` model is deployed (via retrain workflow or manual upload), the classifier picks it up on the next inference cycle without restarting the bot. This ensures continuous trading during model updates.

### BNB Rebalancer

Binance offers a 25% fee discount when paying trading fees in BNB. The `bnb_rebalancer.py` module automatically maintains a target BNB balance (default: $20 USDT equivalent). When BNB drops below the minimum threshold, it buys a small top-up. When it exceeds the maximum, excess is sold back to USDT. Combined with `LIMIT_MAKER` (post-only) orders, this minimizes total fee drag.

---

## 🗺️ Roadmap

**Phase 1 — Foundation** ✅
- [x] Hummingbot v2 script structure
- [x] Bollinger Bands auto range-setting
- [x] ATR dynamic grid spacing
- [x] Paper trading on Binance testnet

**Phase 2 — Intelligence** ✅
- [x] RSI pause/resume filter + per-level RSI filtering
- [x] EMA 200 trend bias
- [x] Telegram alerts + interactive commands
- [x] Circuit breaker + position guard

**Phase 3 — Validation** ✅
- [x] Unit tests (indicators, grid, circuit breaker)
- [x] Paper trading on Binance
- [x] Performance reporting (Telegram alerts + commands)

**Phase 4 — Production** ✅
- [x] AWS EC2 deployment (Tokyo) via GitHub Actions
- [x] Docker containerization
- [x] Telegram startup/shutdown alerts

**Phase 5 — Optimization** (current)
- [x] Monitor paper trading results (30+ days)
- [x] VectorBT parameter sweep
- [x] Walk-forward backtesting
- [x] Multi-pair deployment (ETH, BNB, DOGE, XRP)
- [x] ML regime classifier (Random Forest per pair)
- [ ] Live micro-stake ($100–200)

**Phase 6 — Expansion**
- [x] Multi-pair support (5 pairs, BTC currently disabled)
- [x] ML signal layer (per-pair regime classifier)
- [x] Confidence-weighted position sizing
- [x] Cross-asset correlation gate (BTC DANGER halts altcoins)
- [x] BNB rebalancer for 25% fee discount
- [x] Auto-retraining pipeline (weekly sweep + monthly retrain)
- [ ] 4h + 1h multi-timeframe confluence
- [ ] OKX failover routing
- [ ] Live capital deployment (gradual scale-up)

---

## 👤 Author

**Your Name** — Dubai, UAE
[LinkedIn](https://linkedin.com) · [GitHub](https://github.com)

---

> ⚠️ **Disclaimer:** Personal use only on your own VARA-licensed Binance account. Not financial advice. Not licensed for managing third-party funds. Grid bots lose money when price drops sharply below the grid range — always use a circuit breaker and never deploy capital you cannot afford to lose.

*Built with Claude Code CLI · Hummingbot v2 · Binance FZE VARA API · pandas_ta · scikit-learn · Python 3.11 · Deployed on AWS EC2 (Tokyo)*
