# Appendix C: System Architecture and Test Summary

This appendix gives the details behind Section 3.12. All figures were measured from the code and the live server on 26 November 2026.

**C.1 Rust execution engine (`trading-engine-core`)**

| Module | Lines | What it does |
|---|---:|---|
| strategy | 5,183 | Grid and trend engines; regime and routing caches; trade journal |
| connector | 3,243 | Binance and Gate.io connections (REST and WebSocket); paper-trading exchange; price checks |
| signal | 2,026 | Signal-copy engine: parsing, validation, positions, risk |
| indicators | 1,534 | ADX, ATR, Bollinger bands, candlestick patterns, choppiness, Donchian, EMA, MACD, RSI, support and resistance, volume, VWAP |
| backtest | 1,481 | Bar replay, fill simulation, portfolio, reports, parameter sweeps |
| risk | 687 | Circuit breaker and position guard |
| api | 650 | Internal HTTP interface, including the endpoint that receives regime labels |
| bin | 429 | Tools: replay backtest, configuration check, test-order check |
| capital | 349 | Per-strategy capital budgets |
| ml | 282 | Regime features and regime model interface |
| models, notifications, other | 4,069 | Data types, Telegram messages, engine start-up and main loop |
| **Total source** | **19,933** | |
| Integration tests | 3,271 | 34 test files |

**C.2 Python components (`src`)**

| Package | Lines | What it does |
|---|---:|---|
| rl | 3,906 | Gymnasium environment, PPO training, walk-forward evaluation, exposure matching, risk statistics, routing |
| signals | 2,615 | Signal listener, parser, validator, risk and paper futures connector |
| notifications | 2,092 | Telegram alerts and commands |
| ml | 1,753 | Regime classifier, labels, training, calibration, drift check, promotion gate, regime pusher |
| data, monitoring, other | 937 | Data loading, health and logging |
| **Total source** | **11,303** | |
| Scripts | 3,357 | Diagnostics and one-off analysis |
| Tests | 8,804 | Test suite |

**C.3 Containers on the server**

| Container | Image | Role |
|---|---|---|
| trading-bot-rust | trading-bot (103 MB) | Execution engine; health check; restarts on failure |
| trading-signal-listener | trading-signal-listener (678 MB) | Signal engine; restarts on failure |
| trading-regime-pusher | trading-signal-listener (same image) | Sends regime labels to the engine every 180 seconds; restarts on failure |

Server: AWS EC2 t3.small (2 vCPU, 2 GB memory, 20 GB disk), Tokyo region, Amazon Linux 2023.

**C.4 Automated tests and pipelines**

| Item | Count or result |
|---|---|
| Rust tests | 423 |
| Python tests | 556 |
| Total tests | 979 |
| Deployment pipeline (last 200 runs) | 168 passed, 23 failed (deployment blocked), 9 cancelled |
| Other pipelines | Replay backtest (Python and Rust), test coverage, monthly model retraining with a 30-day hold-out check |
| Commits | 1,069 (May 459, June 324, July 144, August 113, September 7, October 13, November 9) |
