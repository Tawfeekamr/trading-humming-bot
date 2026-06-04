# Chapter 1: Introduction

## 1.1 Background
* **The Rise of Crypto Algorithmic Trading:** Brief history of automated trading in highly volatile digital asset markets.
* **The Problem of Non-Stationarity:** Financial markets do not exhibit constant statistical properties. A strategy that is highly profitable in a sideways (ranging) market will incur heavy losses during a strong directional breakout.

## 1.2 Problem Statement
Existing algorithmic trading frameworks often force traders to choose between a mean-reversion (grid) strategy or a momentum (trend) strategy. While dynamic parameter tuning exists, fundamentally switching the underlying execution engine based on predictive market state analysis remains computationally complex and under-researched in open-source architectures. Furthermore, independent altcoin trading ignores the macro correlation driven by Bitcoin (BTC), leading to systemic portfolio risk during market-wide crashes.

## 1.3 Research Objectives
The primary objective of this project is to design, implement, and evaluate a Hybrid Algorithmic Trading Framework. Specific goals include:
1. **Regime Classification:** To develop a Machine Learning model (Random Forest) capable of accurately classifying market states (Ranging, Trending, Danger) in real-time.
2. **Dual-Engine Execution:** To engineer a system that dynamically routes capital and execution logic to either a Grid Engine or a Trend Engine based on ML predictions.
3. **Cross-Asset Correlation:** To implement a systemic risk gate that utilizes BTC's market regime as a protective overlay for altcoin execution.
4. **Cloud-Native Deployment:** To build a resilient, automated CI/CD pipeline and deployment architecture that supports zero-downtime hot-reloading of ML models.

## 1.4 Scope and Limitations
* **Scope:** The system focuses on 5 high-liquidity pairs on the Binance FZE exchange. The ML model utilizes a predefined set of technical indicators on a 1-hour timeframe.
* **Limitations:** The study does not cover high-frequency market-making (sub-second latency) or sentiment analysis (NLP on news data).

## 1.5 Thesis Organization
* Outline what chapters 2 through 7 will cover.
