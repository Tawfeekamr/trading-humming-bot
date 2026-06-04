# Chapter 5: Results and Evaluation

This chapter provides the empirical evidence proving the system works.

## 5.1 Evaluation Metrics
Define how success is measured:
* **Sharpe Ratio / Sortino Ratio:** Risk-adjusted returns.
* **Maximum Drawdown (MDD):** Capital preservation metric.
* **Win Rate & Profit Factor:** Trade efficiency.

## 5.2 Backtesting Results (VectorBT)
* Present the results of the out-of-sample walk-forward backtests.
* **Comparative Analysis:** Compare the Hybrid Strategy against:
  1. A pure Buy-and-Hold strategy.
  2. A static Grid-only strategy.
  3. A static Trend-only strategy.
* Include charts showing the equity curve and drawdown periods.

## 5.3 ML Model Performance
* Present the confusion matrix for the Random Forest classifier on the test set.
* Discuss Precision, Recall, and F1-Score for predicting the "DANGER" regime (as false negatives here are costly).
* Feature Importance analysis: What features did the Random Forest find most predictive?

## 5.4 Live / Paper Trading Validation
* Present the results from the 30+ day forward-test (paper trading) on Binance.
* Discuss slippage, latency, and real-world execution metrics vs. the backtest.
* Analyze a specific market event during the paper trading period where the Cross-Asset Correlation Gate successfully protected the portfolio.
