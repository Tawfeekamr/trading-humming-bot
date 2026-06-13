# Research Roadmap: Master's Degree Thesis

**Thesis Title:** Machine Learning Regime Dynamics in Multi-Asset Execution: A Hybrid Algorithmic Trading Framework

This document outlines the step-by-step process to transition your existing `trading-humming-bot` project into a formal Master's degree research thesis. 

---

## Phase 1: Literature Review (The "Context")
Before writing about your bot, you must understand the academic landscape. Gather 15-20 peer-reviewed papers on the following topics:
1.  **Market Regime Switching Models:** Look into Hidden Markov Models (HMM) and how machine learning is replacing them to detect bullish/bearish/ranging states.
2.  **Random Forest in Quantitative Finance:** Research why Random Forest is effective for classification tasks in noisy financial data compared to Deep Learning (e.g., lower overfitting).
3.  **Algorithmic Trading Strategies:** Find academic definitions and past research on **Grid Trading** (mean reversion) and **Trend Following** (momentum). 
4.  **Portfolio Risk Management:** Read about dynamic asset allocation and cross-asset correlation during market crashes.

## Phase 2: Formulate Research Questions & Hypotheses
Your thesis needs a core question it is trying to answer. 
*   **Primary Research Question:** How does integrating a Random Forest regime classifier into a dual-engine (Grid + Trend) trading framework affect risk-adjusted returns compared to static, single-strategy algorithms?
*   **Hypothesis 1:** A hybrid framework guided by ML regime detection will exhibit a significantly lower maximum drawdown than a standalone Grid or Trend strategy.
*   **Hypothesis 2:** Cross-asset risk gating (monitoring BTC to halt altcoins) reduces systemic portfolio risk during flash crashes.

## Phase 3: Formalize the Methodology (The "How")
Document your system not just as code, but as a scientific methodology.
1.  **Data Sourcing & Feature Engineering:** Explain how you fetch OHLCV data. Define your features mathematically (e.g., RSI formula, ATR, Bollinger Bands).
2.  **Model Training & Labeling:** How do you label your data? (e.g., What defines a "Ranging" vs. "Trending" regime historically?). Detail the Random Forest parameters and training pipeline.
3.  **The Hybrid Execution Engine:** Draw architecture diagrams of how the ML output feeds into the Grid and Trend engines.
4.  **Evaluation Metrics:** Define the metrics you will use to prove success (Sharpe Ratio, Sortino Ratio, Maximum Drawdown, Win Rate, Profit Factor).

## Phase 4: Data Gathering & Experimentation
Run formal experiments using your existing infrastructure:
1.  **Baseline Testing (The Control Group):** Run a VectorBT backtest of a *pure* Grid bot and a *pure* Trend bot over the last 2-3 years. Record the results.
2.  **Hybrid Testing (Your AI Model):** Run a walk-forward backtest of your ML-driven hybrid framework over the exact same period.
3.  **Out-of-Sample/Paper Trading:** Export the logs from your live paper-trading environment to show how the model performs in real-time, unseen market conditions.

## Phase 5: Analysis & Critical Reflection (PC10 & PC11)
Analyze the data you gathered in Phase 4.
*   Did the hybrid model outperform the baselines? Why or why not?
*   In which market conditions did the ML classifier struggle? (e.g., False breakouts).
*   **Critical Reflection:** Identify the weaknesses of your model (e.g., feature lag, execution slippage) and suggest opportunities for future research.

## Phase 6: Structuring the Thesis Document
A standard structure for your final document:
1.  **Abstract:** Quick summary of the problem, method, and results.
2.  **Introduction:** Motivation, problem statement, and research objectives.
3.  **Literature Review:** What others have done.
4.  **Methodology & System Architecture:** How you built your ML model and trading engines.
5.  **Experimental Setup & Backtesting:** The data, timeframe, and testing parameters.
6.  **Results & Discussion:** Charts, tables, and analysis of performance.
7.  **Critical Evaluation:** Reflection on strengths, limitations, and practical business implications.
8.  **Conclusion:** Final thoughts and future work.

---
> [!TIP]
> **Immediate Next Step:** Start with **Phase 1**. Open Google Scholar and search for *"Machine learning market regime detection"* and *"Random Forest algorithmic trading"*. Create a spreadsheet to log the papers you read.
