# Chapter 6: Conclusion and Future Work

## 6.1 Summary of Contributions
* Reiterate the core problem: static strategies fail in dynamic markets.
* Summarize how the Hybrid Framework solved this using the Random Forest regime classifier and Dual-Engine execution.
* Highlight the engineering achievements: CI/CD, hot-reloading, cloud deployment.

## 6.2 Key Findings
* The ML model successfully mitigated severe drawdowns by identifying DANGER regimes.
* The Cross-Asset gate using BTC significantly reduced altcoin exposure during macro downturns.
* The system proved technically resilient during continuous cloud operation.

## 6.3 Limitations
* **Model Decay:** The model still relies on historical data; unprecedented black-swan events cannot be predicted.
* **Execution Latency:** Operating on a 1-hour timeframe is resilient to micro-volatility but misses intra-hour flash crashes until the candle closes (unless hard stops are hit).
* **Feature Constraints:** The model currently relies purely on price/volume technical analysis, lacking sentiment or on-chain data.

## 6.4 Future Work
* **Alternative ML Models:** Upgrading from Random Forest to deep learning approaches like LSTMs or Transformers for sequence modeling.
* **Alternative Data:** Integrating NLP-based sentiment analysis from Twitter/News APIs as additional features for the regime classifier.
* **Reinforcement Learning:** Replacing the static dual-engine logic with an RL agent that learns continuous, optimal policy execution.
* **Multi-Exchange Arbitrage:** Expanding the framework to route orders across OKX, Bybit, and Binance simultaneously based on orderbook depth.
