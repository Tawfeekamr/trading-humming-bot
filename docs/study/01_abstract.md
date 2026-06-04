# Abstract

*(Note: The Abstract is typically written last, after all results and conclusions are finalized. Below is a template based on your project.)*

## Draft Abstract

Algorithmic trading strategies in cryptocurrency markets frequently suffer from performance degradation due to non-stationary market dynamics. Static strategies, such as grid trading or trend following, perform optimally only under specific market regimes (ranging or trending, respectively). This project proposes a **Hybrid Algorithmic Trading Framework** that dynamically switches between execution strategies based on real-time machine learning regime classification.

The framework continuously evaluates five major cryptocurrency pairs (BTC, ETH, BNB, DOGE, XRP). A per-pair Random Forest classifier, trained on historical OHLCV data and technical indicators (Bollinger Bands, RSI, ATR), predicts the current market regime as RANGING, TRENDING, or DANGER. Based on this classification, the system activates a Grid Engine for mean-reversion capture, a Trend Engine for directional momentum, or halts trading to preserve capital. Furthermore, the system incorporates a cross-asset correlation gate where the Bitcoin (BTC) regime acts as a leading indicator, overriding altcoin signals during high-risk 'DANGER' conditions.

Deployed on a high-availability cloud infrastructure using Hummingbot v2, the framework demonstrates robust out-of-sample performance. Through extensive VectorBT backtesting and live paper trading, the hybrid approach shows a statistically significant improvement in risk-adjusted returns (Sharpe ratio) and a reduction in maximum drawdown compared to single-strategy benchmarks. The integration of automated model retraining pipelines ensures the classifier adapts to evolving market conditions, presenting a scalable and resilient solution for multi-asset automated execution.

**Keywords:** Algorithmic Trading, Machine Learning, Random Forest, Market Regime Classification, Cryptocurrency, Grid Trading, System Architecture.
