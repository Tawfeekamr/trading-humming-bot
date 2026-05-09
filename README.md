# 🤖 TA-Enhanced BTC/USDT Grid Bot
### Intelligent Algorithmic Grid Trading | Hummingbot v2 | Binance FZE | Dubai, UAE

> A fully automated **BTC/USDT Grid Bot** powered by real-time Technical Analysis. Uses Bollinger Bands to set the grid range, RSI to pause/resume activity, EMA 200 for trend bias, and ATR for dynamic spacing — all running inside a Hummingbot v2 custom Python script on Binance FZE (VARA-licensed).

---

## 📌 Project Overview

| Field                   | Details                                                  |
|-------------------------|----------------------------------------------------------|
| **Framework**           | Hummingbot v2 (Script API)                               |
| **Strategy**            | TA-Enhanced Grid Bot                                     |
| **Pair**                | BTC/USDT (Spot)                                          |
| **Exchange**            | Binance FZE — VARA + ADGM licensed (UAE)                 |
| **Indicators**          | Bollinger Bands · RSI · EMA 200 · ATR                    |
| **Timeframe**           | 1h candles for TA signals                                |
| **Language**            | Python 3.11+                                             |
| **Deployment**          | AWS EC2 (Tokyo) via GitHub Actions + Docker               |
| **Notifications**       | Telegram real-time alerts + interactive commands          |
| **Status**              | ✅ Paper Trading                                          |

---

## 🧠 Strategy Logic

The bot enhances a standard grid with 4 TA layers. Every time a 1h candle closes, all indicators are recalculated and the grid is adjusted or paused accordingly.

### How the 4 Indicators Work Together

```
Every 1h candle closes:
│
├─ Calculate: Bollinger Bands (20,2) · RSI (14) · EMA 200 · ATR (14)
│
├─ IF price > EMA200 AND RSI < 65          ← Uptrend, not overbought
│    └─ Grid ACTIVE (long-biased)
│       ├─ Range:   Lower BB  →  Upper BB
│       ├─ Range:   Lower BB  →  Upper BB
│       └─ Spacing: ATR × 1.5
│       ├─ Skip BUY levels when RSI > 60
│       └─ Skip SELL levels when RSI < 40
│
├─ IF RSI > 70 OR price < EMA200           ← Overbought or downtrend
│    └─ Grid PAUSED — cancel all orders, hold USDT
│
└─ IF RSI < 35 AND price near Lower BB     ← Oversold at support
     └─ Grid REACTIVATES at new range
        └─ Place fresh grid orders
```

### Indicator Roles

| Indicator | Role | Setting |
|-----------|------|---------|
| **Bollinger Bands** | Sets grid upper/lower range automatically | Period 20, StdDev 2 |
| **RSI** | Activates grid when oversold, pauses when overbought; filters individual levels | Period 14 |
| **EMA 200** | Trend filter — only run grid in uptrend | Period 200 |
| **ATR** | Sets dynamic grid spacing based on volatility | Period 14, multiplier 1.5 |

---

## 📁 Project Structure

```
ta-grid-bot/
│
├── .env                          # 🔒 Secrets — NEVER COMMIT
├── .env.example                  # ✅ Template — commit this
├── .gitignore
│
├── hummingbot_files/
│   └── scripts/
│       └── ta_grid_btcusdt.py    # Main Hummingbot v2 script (strategy)
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
│   ├── risk/
│   │   ├── circuit_breaker.py    # Halt if drawdown > threshold
│   │   └── position_guard.py     # Max exposure, min USDT reserve
│   │
│   ├── data/
│   │   ├── candle_feed.py        # Fetch 1h OHLCV from Binance REST
│   │   └── ws_feed.py            # Real-time price via WebSocket
│   │
│   ├── journal/
│   │   └── trade_journal.py      # SQLite logger — every trade stored
│   │
│   ├── dashboard/
│   │   └── app.py                # Streamlit P&L dashboard (web UI)
│   │
│   └── notifications/
│       ├── telegram_bot.py       # Grid state alerts
│       ├── telegram_commands.py  # Interactive Telegram commands (/status, /pnl, etc.)
│       └── pnl_reporter.py       # Hourly/daily/monthly P&L summaries
│
├── config/
│   └── strategy.yaml             # All strategy parameters (non-secret)
│
├── backtest/
│   ├── vectorbt_sweep.py         # Phase 1: parameter optimization
│   └── walk_forward.py           # Phase 2: out-of-sample validation
│
├── tests/
│   ├── test_indicators.py
│   ├── test_grid_manager.py
│   └── test_circuit_breaker.py
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
# ── Pair & Exchange ───────────────────────────────────────────────
pair: "BTC-USDT"
exchange: "binance"
timeframe: "1h"

# ── Grid Parameters ───────────────────────────────────────────────
grid:
  levels: 6                   # Orders on each side of mid price
  capital_usdt: 1000          # Total USDT allocated
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

# ── Risk Management ───────────────────────────────────────────────
risk:
  max_drawdown_pct: 10
  daily_loss_limit_pct: 5
  max_btc_exposure_pct: 80
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
| $1K | $75 | $2-3 | $60-90 | ~$1,100 |
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
cp hummingbot_files/scripts/ta_grid_btcusdt.py \
   ~/hummingbot/scripts/ta_grid_btcusdt.py
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
start --script ta_grid_btcusdt.py --conf conf_ta_grid_paper.yml

# Live trading (only after passing all 5 testing stages)
start --script ta_grid_btcusdt.py --conf conf_ta_grid_live.yml
```

---

## 📊 Grid Visualization

```
Price
  │
Upper BB ──── 🔴 SELL order 6   ($103,500)
              🔴 SELL order 5   ($103,000)
              🔴 SELL order 4   ($102,500)
              🔴 SELL order 3   ($102,000)
              🔴 SELL order 2   ($101,500)
              🔴 SELL order 1   ($101,000)
Mid Price ─── ◆  Current BTC   ($100,000)
              🟢 BUY  order 1   ($99,000)
              🟢 BUY  order 2   ($98,000)
              🟢 BUY  order 3   ($97,000)
              🟢 BUY  order 4   ($96,000)
              🟢 BUY  order 5   ($95,000)
Lower BB ──── 🟢 BUY  order 6   ($94,000)
  │
  └─ Spacing = ATR × 1.5  (recalculated every 1h)
     Range   = Lower BB → Upper BB  (recalculated every 1h)

  ⏸️  RSI > 70 or price < EMA200 → ALL ORDERS CANCELLED → HOLD USDT
  🔍  RSI > 60 → skip BUY levels  |  RSI < 40 → skip SELL levels
```

---

## 🛡️ Risk Management

| Parameter | Value | Description |
|-----------|-------|-------------|
| `max_drawdown_pct` | 10% | Bot halts if portfolio drops 10% from peak |
| `daily_loss_limit_pct` | 5% | Circuit breaker triggers if -5% in 24h |
| `max_btc_exposure_pct` | 80% | Never deploy more than 80% capital in BTC |
| `min_usdt_reserve` | $100 | Always keep $100 USDT untouched |

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
| `/price` | Live BTC/USDT price from Binance + RSI, EMA, Bollinger Bands |
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

## 📊 P&L Tracking — 2 Systems

Both systems run simultaneously. They serve different purposes and complement each other.

---

### 1. 📱 Telegram Alerts (Real-Time)

Every trade fires an instant alert to your phone. A daily P&L summary is auto-sent at midnight UTC.

**Per-trade alert (every close):**
```
💚 Trade Closed — BTC/USDT
━━━━━━━━━━━━━━━━━━━━━━
📈 BUY  |  Grid Level 3
⏱ Duration:    45 min
🔵 Entry:      $98,200.00
🔵 Exit:       $99,100.00
📦 Qty:        0.001 BTC
━━━━━━━━━━━━━━━━━━━━━━
💰 Gross PnL:  +$0.90
💸 Fee:        -$0.19
📊 Net PnL:    +$0.71
━━━━━━━━━━━━━━━━━━━━━━
RSI: 42.3  |  Grid: ACTIVE
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

### 2. 🌐 Streamlit Web Dashboard (Live Browser UI)

Open in any browser or on your phone. Shows everything visually.

**What's on the dashboard:**
- 6 summary cards: Today / Week / Month / All-Time PnL + total trades + win rate
- Equity curve chart (7 / 14 / 30 / 60 / 90 day periods)
- Trade history table — every trade color-coded green ✅ or red ❌
- Filter trades by Side (BUY/SELL) and Result (profit/loss)
- Top 5 best trades and top 5 worst trades
- Full period breakdown table (hour / day / week / month / all-time)

**Run locally:**
```bash
streamlit run src/dashboard/app.py
# Opens at http://localhost:8501
```

**Deploy on EC2 (alongside bot):**
```bash
# Dashboard runs inside the same Docker container on EC2
# Accessible at http://<ec2-ip>:8501
```

---

### What Gets Stored Per Trade

Every trade is logged with full context:

```python
{
  "timestamp":    "2026-04-04 14:00:00",
  "pair":         "BTC/USDT",
  "side":         "BUY",
  "entry_price":  98200.00,
  "exit_price":   99100.00,
  "quantity":     0.001,
  "gross_pnl":    +0.90,
  "fee":          -0.19,
  "net_pnl":      +0.71,
  "grid_level":   3,
  "duration_min": 45,
  "rsi":          42.3,
  "bb_upper":     101500,
  "bb_lower":     96800,
  "ema_200":      97200,
  "atr":          850,
  "grid_state":   "ACTIVE"
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
                 └─► Hummingbot container (strategy script + indicators)
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
- [x] Performance reporting (Telegram + dashboard)

**Phase 4 — Production** ✅
- [x] AWS EC2 deployment (Tokyo) via GitHub Actions
- [x] Docker containerization
- [x] Telegram startup/shutdown alerts

**Phase 5 — Optimization** (current)
- [ ] Monitor paper trading results (30+ days)
- [ ] VectorBT parameter sweep
- [ ] Walk-forward backtesting
- [ ] Live micro-stake ($100–200)

**Phase 6 — Expansion**
- [ ] ETH/USDT second grid
- [ ] 4h + 1h multi-timeframe confluence
- [ ] ML signal layer (FreqAI)
- [ ] OKX failover routing

---

## 👤 Author

**Your Name** — Dubai, UAE
[LinkedIn](https://linkedin.com) · [GitHub](https://github.com)

---

> ⚠️ **Disclaimer:** Personal use only on your own VARA-licensed Binance account. Not financial advice. Not licensed for managing third-party funds. Grid bots lose money when price drops sharply below the grid range — always use a circuit breaker and never deploy capital you cannot afford to lose.

*Built with Claude Code CLI · Hummingbot v2 · Binance FZE VARA API · pandas_ta · Python 3.11 · Deployed on AWS EC2 (Tokyo)*
