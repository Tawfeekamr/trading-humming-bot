# IBKR Paper Trading Integration Plan

## Goal
Add Interactive Brokers (IBKR) as a second execution venue alongside Binance, giving the bot access to US stocks, ETFs, options, and futures. Start with paper trading to validate, then go live.

## Current Architecture (crypto-only)
```
Binance WS (testnet) → Rust Engine (Grid/Trend/Swing/MR) → Paper Fills
Gate.io REST → Python Signal Engine (Telegram → DeepSeek → entries)
Capital Manager (request_capital across all strategies)
```

## Target Architecture (multi-asset)
```
Binance WS ──→ Rust Engine ──→ Grid/Trend/Swing/MR (crypto)
IBKR TWS/GW ──→ Rust Engine ──→ Stock/ETF strategies (equities)
                    ↑
            Capital Manager (shared pool across both venues)
                    ↑
            RL Agent (regime routing across asset classes)
```

## Why Custom Rust Connector (NOT NautilusTrader)
- The Rust engine already has a `Connector` trait abstraction (`place_order`, `get_balances`, `get_order_book`, `get_klines`). Adding IBKR = one new implementation alongside `binance_ws.rs` and `paper.rs`.
- NautilusTrader was explored (May 2026) and abandoned — dead files removed in this cleanup. The custom Rust engine is faster (no GIL), already works, and gives full RL instrumentation control.
- No framework dependency, no rewrite of existing strategies.

## IBKR Connection Options

### Option A: Rust `tws-api` crate (recommended)
- Native Rust IBKR TWS API client
- Direct WS/TCP connection to TWS or IB Gateway
- No Python bridge needed — stays in the Rust engine
- Risk: ecosystem maturity (fewer users than Python ib_insync)

### Option B: Python `ib_async` (formerly ib_insync) bridge
- Mature, well-documented Python IBKR library
- Run as a separate container alongside the signal listener
- Bridge to Rust engine via HTTP API (same pattern as the signal engine)
- Proven pattern: the signal listener already bridges Python ↔ Rust via HTTP

### Option C: IB Gateway in Docker + REST/WebSocket API
- IB Gateway runs in a container (docker-ib-gateway project)
- Expose REST API (Client Portal API) — no TWS needed
- Rust engine calls REST endpoints (like it calls Binance/Gate.io)
- Cleanest deployment (no desktop TWS dependency)

## Phased Plan

### Phase 0: Setup & Paper Connection (1-2 days)
1. Open IBKR Paper Trading account (free, instant at interactivebrokers.com)
2. Install IB Gateway or TWS Paper Trading environment
3. Verify connectivity: `ib_async` Python script that connects, queries account balance, places a paper order
4. Decide: Rust crate vs Python bridge vs REST API

### Phase 1: Market Data (3-5 days)
1. Implement `IbkrConnector` (implements the `Connector` trait)
2. Stream real-time quotes for a watchlist (e.g., SPY, QQQ, AAPL, TSLA)
3. Feed into `BarCache` alongside Binance klines
4. Backfill historical bars from IBKR for backtesting

### Phase 2: Paper Execution (3-5 days)
1. `place_order()` → IBKR paper order
2. `get_balances()` → IBKR account (multi-currency: USD, EUR)
3. `cancel_order()` → IBKR cancel
4. Paper fills from IBKR (they simulate fills in paper mode)
5. Wire into Capital Manager (request_capital works across venues)

### Phase 3: Equity Strategies (1-2 weeks)
1. Port Grid strategy to equities (mean-reversion on range-bound stocks)
2. Port Trend strategy to equities (momentum on breakout stocks)
3. Add a Scanner: daily scan of S&P 500 / Nasdaq 100 for setups
4. Signal sources: earnings calendar, technical screeners, options flow

### Phase 4: RL Multi-Asset (thesis extension)
1. Expand the RL state space: add equity regime features (VIX, sector rotation, breadth)
2. Expand action space: route capital between crypto and equities
3. Train on combined crypto + equity historical data
4. The Capital Manager already supports cross-strategy allocation — extend to cross-venue

## Technical Requirements

### IBKR Account
- Paper trading: free, immediate
- Live: $0 minimum (Pro account $10/mo if < $10k balance)
- Market data subscriptions needed for real-time (delayed data is free)

### Infrastructure
- IB Gateway or TWS running on EC2 (or a separate VPS — IBKR needs a stable IP)
- Docker container for IB Gateway (use `gwcas/ib-gateway-docker` or similar)
- Open ports: 4001/4002 (API), 5900 (VNC for Gateway UI)

### Rust Dependencies
- Option A: `tws-api` crate (or `ibkr-parser` for raw protocol)
- Option B: HTTP client (already have `reqwest`) if using REST/Client Portal API
- No new Python deps if using Rust native

## Risks & Considerations
- **PDT rule**: IBKR enforces Pattern Day Trader rule ($25k min for >3 day trades/week in margin accounts). Solution: use cash account, or swing/position holds (not intraday).
- **Settlement**: T+1 for US equities (2024 rule change). Capital isn't instantly reusable after a sell.
- **Multi-currency**: IBKR supports base currency selection. Capital Manager needs USD + USDT tracking.
- **Market hours**: US equities trade 9:30-16:00 ET (plus extended hours). Crypto is 24/7. The engine needs session-aware logic.
- **Regulatory**: IBKR is a regulated US broker. KYC/AML, reporting, and tax documents are handled by IBKR — but strategy compliance (no wash sales, PDT) is your responsibility.

## What This Unlocks for the Thesis
- **Multi-asset RL**: the RL agent routes between crypto and equities based on regime — a much richer research problem than crypto-only
- **Deep scanning**: scan thousands of US equities daily for setups (earnings, breakouts, mean-reversion) — not limited to 4 crypto pairs
- **Portfolio optimization**: allocate across asset classes (stocks + crypto + options), not just within crypto
- **Hedging**: short ES futures to hedge long crypto exposure (IBKR gives futures access)
