# 🚀 Implementation Roadmap: Path to Live Trading

> Dubai, UAE · Binance FZE · Hummingbot v2 · May 2026

To reach a state where the bot is consistently profitable and safe to trade live capital, the following development phases must be completed.

---

## 🏗️ Phase 1: Core Strategy Development (High Priority)

The infrastructure exists, but the "brain" of the bot is missing.

### 1.1 Implement Technical Indicators (`src/indicators/`)
- [ ] **`bollinger.py`**: Calculate 20-period BB with 2.0 StdDev.
- [ ] **`rsi.py`**: Calculate 14-period RSI for overbought/oversold signals.
- [ ] **`ema.py`**: Calculate 200-period EMA for trend filtering.
- [ ] **`atr.py`**: Calculate 14-period ATR for dynamic grid spacing.

### 1.2 Build the Strategy Script (`hummingbot_files/scripts/`)
- [ ] **`ta_grid_btcusdt.py`**:
    - Inherit from Hummingbot `ScriptStrategyBase`.
    - Implement `on_tick()` to check indicator confluence.
    - Implement `adjust_grid()` to cancel/place orders based on BB and ATR.
    - Implement `check_circuit_breaker()` to halt on 10% drawdown.

---

## 🧪 Phase 2: Backtesting & Validation

Never deploy capital without knowing how the bot performed in the past.

### 2.1 Parameter Optimization (`backtest/vectorbt_sweep.py`)
- [ ] Run a sweep of BB periods (10-30) and RSI thresholds (30-70).
- [ ] **Target**: Sharpe Ratio > 1.2 and Max Drawdown < 8%.

### 2.2 Walk-Forward Test (`backtest/walk_forward.py`)
- [ ] Test the best parameters on "unseen" data (e.g., 2024 vs 2025).
- [ ] **Target**: Consistency across bull (uptrend) and crab (sideways) markets.

---

## 📡 Phase 3: Monitoring & Observability

Ensure you can see what the bot is doing without logging into a server.

### 3.1 P&L Reporting Enhancement
- [ ] Implement a **Fee-to-Profit ratio alert** in `pnl_reporter.py`.
- [ ] Alert if `fees / gross_pnl > 30%` (signals overtrading).

### 3.2 Google Sheets Refinement
- [ ] Add a "Market Condition" column to the `📋 Trades` tab (Uptrend/Sideways/Pause).

---

## 🏁 Phase 4: The 5-Stage Deployment Gate

| Stage | Duration | Capital | Success Criteria |
|-------|----------|---------|------------------|
| **1. Backtest** | 1 Day | $0 | Sharpe > 1.2, positive expectancy |
| **2. Paper Trade** | 30 Days | $0 | 100+ trades, net profitable |
| **3. Micro-Live** | 14 Days | $100 | Results match paper trading within 15% |
| **4. Scaled-Live** | Ongoing | $500 | Net ROI covers Railway costs ($18/mo) |
| **5. Full Deploy** | Permanent | $1,000+ | Auto-compounding profits |

---

## ⚠️ Safety Checklist

- [ ] **BNB Fee Discount**: MUST be enabled before Phase 3.
- [ ] **Circuit Breaker**: Set to 10% portfolio drawdown.
- [ ] **IP Whitelist**: Binance API keys restricted to Railway's egress IP range.
- [ ] **Secret Management**: Keys NEVER committed to GitHub (use Railway Variables).

---

*Roadmap v1.0 · Generated May 2026*
