# Chapter 3: Methodology

This chapter details the theoretical and mathematical models underpinning the Hybrid Framework.

## 3.1 Data Acquisition and Feature Engineering
* **Data Source:** Binance REST API and WebSockets (1h timeframe).
* **Features Used:** 
  * Volatility: Average True Range (ATR), Bollinger Band Width (%B).
  * Momentum: Relative Strength Index (RSI), EMA Distance.
  * Price Action: Returns, logarithmic returns.
* **Labeling Strategy:** How did you define historical "Ranging", "Trending", and "Danger" periods to train the model? (e.g., rule-based labeling using historical ATR and trendlines).

## 3.2 The Machine Learning Regime Classifier
* **Model Selection:** Why Random Forest? (Discuss its robustness to overfitting, feature importance outputs, and non-linear classification capabilities compared to Logistic Regression or SVMs).
* **Training Pipeline:** Discuss cross-validation, train/test splits tailored for time-series data (avoiding data leakage).
* **Confidence Scoring:** How the probability output of the Random Forest is used to dynamically scale position sizing (Confidence-Weighted Position Sizing).

## 3.3 The Dual-Engine Strategy
### 3.3.1 Grid Engine (Mean Reversion)
* Mathematics of the Grid: Calculating upper/lower bounds using Bollinger Bands.
* Dynamic Spacing: Using `ATR * multiplier` to adjust grid density.
* RSI constraints for order placement.

### 3.3.2 Trend Engine (Momentum)
* Entry conditions (EMA Crossovers).
* Risk management: Trailing stops and hard stop-losses.

## 3.4 Cross-Asset Correlation Gate
* The logic behind using BTC as the macro-indicator.
* State machine logic: IF `BTC_Regime == DANGER` THEN `Halt Altcoin Buy Orders`.
