# Chapter 2: Literature Review

*(Note: You will need to find and cite academic papers to fill this section out. Google Scholar is your friend here.)*

## 2.1 Algorithmic Trading and Market Microstructure
* Review foundational papers on automated trading.
* Discuss the mechanics of Grid Trading (mean reversion) and its vulnerabilities to trending markets.
* Discuss Trend Following strategies (moving average crossovers) and their vulnerabilities to whipsaw (ranging) markets.

## 2.2 Market Regime Detection in Finance
* How has the industry historically detected market states? 
* Review traditional statistical methods: Hidden Markov Models (HMM), Gaussian Mixture Models (GMM), and rolling volatility metrics.
* Transition into modern Machine Learning approaches: Why tree-based models (like Random Forest) or neural networks are increasingly used for classification in noisy financial data.

## 2.3 Machine Learning in Cryptocurrency Markets
* Review recent literature (2020-present) specifically targeting ML in crypto.
* Discuss the heavy reliance on Bitcoin as a market driver (Cross-Asset Correlation). Studies showing how BTC movements precede or dictate altcoin volatility.

## 2.4 Existing Frameworks and Gaps
* Analyze existing open-source frameworks (Hummingbot, Freqtrade, CCXT).
* **The Gap:** Identify that while tools exist to run algorithms, few provide an integrated pipeline that marries complex, per-pair ML model inference with multi-strategy execution and automated cloud deployment in a single open-source stack. Your project bridges this gap.
