# 🚀 Features Plan & Roadmap
### TA-Enhanced BTC/USDT Grid Bot

This document outlines the planned features and system upgrades for the algorithmic trading bot. Each phase builds upon the previous one to increase profitability, safety, and intelligence.

---

## 🎯 Phase 1: Core Trade Mechanics
Establish the foundational logic of the Hummingbot integration.
- [x] **Trade Journaling & Dashboard**: Local SQLite logging with a Streamlit UI.
- [x] **Telegram Reporting Engine**: Alerts for trades, daily, and hourly summaries.
- [ ] **Grid Manager**: Handles placing `BUY`/`SELL` orders and tracking grid levels.
- [ ] **Dynamic Grid Spacing (ATR)**: Automatically widens the gap between orders when volatility spikes.
- [ ] **Bollinger Band Ranges**: Bot automatically adjusts upper and lower bounds of the grid using the 20-period BB.

---

## 🛡️ Phase 2: Algorithmic Protective Layers
Build purely math-driven logic to keep the bot out of bad market conditions.
- [ ] **RSI Trend Filter**: Pauses grid execution (`ACTIVE` to `PAUSED`) if RSI becomes overbought (>70) or oversold (<30).
- [ ] **EMA 200 Filter**: Prevents taking long-biased grids in a macro downtrend.
- [ ] **Circuit Breaker**: An emergency kill switch that halts the bot entirely if the wallet drawdown goes past a user-defined threshold (e.g., -10%).
- [ ] **Position Guard**: Ensures a minimum USDT safety buffer is always left unspent.

---

## 📰 Phase 3: AI Sentiment Intelligence (Zhipu GLM-5.1)
Integrate a Large Language Model to act as a proactive shield against devastating news events and violent unpredicted trends.
- [ ] **News Ingestion Engine**: Periodically grab the top 5–10 crypto headlines (via CryptoPanic API or RSS).
- [ ] **LLM Evaluation**: Send headlines to the **GLM-5.1 API** with a strict prompt: *"Score these headlines as BULLISH, BEARISH, or NEUTRAL for Bitcoin."*
- [ ] **News-Triggered Grid Pause**: If GLM-5.1 detects a `BEARISH` macro environment, send an emergency signal to the Grid Manager to pause buying until volatility settles.

---

## 📊 Phase 4: Multi-Market Expansion
Scaling the infrastructure once Phase 1-3 run profitably in live markets.
- [ ] **Multi-Pair Support**: Expand the single-pair architecture to concurrently trade `ETH-USDT` and `SOL-USDT`.
- [ ] **Exchange Failover**: Route to OKX automatically if Binance FZE API experiences downtime. 
- [ ] **Machine Learning Signals (FreqAI)**: Add a predictive layer alongside the LLM and the TA indicators.

---

*Document created via Gemini Agent*
