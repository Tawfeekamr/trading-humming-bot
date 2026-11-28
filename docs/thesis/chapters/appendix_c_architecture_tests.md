# Appendix C: System Architecture and Test Summary

This appendix gives the details behind Section 3.12. All figures were measured from the code and the live server on 26 November 2026.

**C.1 Rust execution engine (`trading-engine-core`)**

| Module | Lines | What it does |
|---|---:|---|
| strategy | 5,336 | Grid and trend engines; regime and routing caches; trade journal |
| connector | 3,325 | Binance and Gate.io connections (REST and WebSocket); paper-trading exchange; price checks |
| signal | 2,026 | Signal-copy engine: parsing, validation, positions, risk |
| indicators | 1,534 | ADX, ATR, Bollinger bands, candlestick patterns, choppiness, Donchian, EMA, MACD, RSI, support and resistance, volume, VWAP |
| backtest | 1,481 | Bar replay, fill simulation, portfolio, reports, parameter sweeps |
| risk | 726 | Circuit breaker and position guard |
| api | 650 | Internal HTTP interface, including the endpoint that receives regime labels |
| bin | 429 | Tools: replay backtest, configuration check, test-order check |
| capital | 349 | Per-strategy capital budgets |
| ml | 282 | Regime features and regime model interface |
| models, notifications, other | 4,083 | Data types, Telegram messages, engine start-up and main loop |
| **Total source** | **20,221** | |
| Integration tests | 3,279 | 34 test files |

**C.2 Python components (`src`)**

| Package | Lines | What it does |
|---|---:|---|
| rl | 3,920 | Gymnasium environment, PPO training, walk-forward evaluation, exposure matching, risk statistics, routing |
| signals | 2,615 | Signal listener, parser, validator, risk and paper futures connector |
| notifications | 2,092 | Telegram alerts and commands |
| ml | 1,822 | Regime classifier, labels, training, calibration, drift check, promotion gate, regime pusher |
| data, monitoring, other | 937 | Data loading, health and logging |
| **Total source** | **11,386** | |
| Scripts | 3,629 | Diagnostics and one-off analysis |
| Tests | 8,925 | Test suite |

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
| Other pipelines | Replay backtest (Python and Rust), test coverage, monthly model retraining with a 30-day hold-out check (repaired 26 November 2026; first run 1 December 2026) |
| Commits | 1,069 (May 459, June 324, July 144, August 113, September 7, October 13, November 9) |

**C.5 Duplicate trend entries on 27 November 2026**

This defect occurred in the live paper-trading system after the evaluation was frozen. It does not affect any reported result.

*What happened.* At 14:02 UTC on 27 November 2026, the trend engine closed an XRP position at its trailing stop. In the next five seconds it placed 144 identical buy orders for XRP. The first 27 filled and spent the whole simulated cash balance of about $100,000, buying 103,455 XRP at $0.96855. The remaining 117 were refused by the cash check added on 26 November (Section 5.4.1, item 1).

*Why.* Three things combined:

1. The trend engine checked only whether it held a position before entering. It did not check whether an entry order was already waiting to fill.
2. The paper exchange deliberately delays fills on a coin for five seconds after any fill on that coin, to stop instant entry-and-exit loops. The entry placed just after the exit therefore waited, and every price update in those five seconds placed another one.
3. The capital check counted cash already spent and open positions, but not orders still waiting to fill, so each new order was granted in full.

When the fills arrived, the first opened the position and the next 26 were read as exits. The engine therefore recorded itself as flat while the account held the XRP. No engine would have sold it, every strategy was left without cash, and the loss did not appear in the trade log.

*How it was found.* A routine check on 28 November showed a cash balance of zero and an account held almost entirely in one coin. The trade log alone did not show the problem. Capital actually deployed did, which is the measure Section 5.5 recommends reporting.

*Fix.* The trend engine now allows one waiting entry at a time and cancels it if it is still unfilled after 60 seconds. Each entry order carries its own identifier, so fills are matched to entries by that identifier and an exit fill can no longer open or close the wrong position. A waiting entry now counts towards the trend engine's capital budget. Five new tests replay the incident: without the fix the main test places 144 entries, and with it, one. The fix was deployed on 28 November 2026 through the normal tested pipeline.

*Recording the loss.* Restarting the engine resets the simulated account, so the position was valued at the market price before the fix was deployed: a mid price of $0.94065, less 8 basis points of slippage and the 5 basis-point fee. The loss of $3,032.92, including $68.66 in fees, was recorded as one trade. A live sell was not used, because on the unfixed engine it would have freed the cash and repeated the same burst. A separate $76.01 trend loss from 26 November was removed from the log, because it recorded the sale of XRP that the account no longer held after the 26 November reset.

