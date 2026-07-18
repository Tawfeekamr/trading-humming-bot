# Research Roadmap: Master's Degree Thesis

**Thesis Title:** Machine Learning Regime Dynamics in Multi-Asset Execution: Benchmarking Supervised and Reinforcement Learning Policies

This document outlines the step-by-step process to transition your existing `trading-humming-bot` project into a formal Master's degree research thesis. 

---

### Phase 1: Literature Review (The "Context")
Before writing, you must understand the academic landscape. Gather 15-20 peer-reviewed papers on the following topics:
1.  **Market Regime Switching Models:** Hidden Markov Models (HMM) and how machine learning is replacing them to detect market states.
2.  **Random Forest in Quantitative Finance:** Why Random Forest is effective for classification tasks in noisy financial data.
3.  **Reinforcement Learning for Execution:** Formulating trading as a Markov Decision Process (MDP), and using Offline RL (like Decision Transformers) to learn from logged trading trajectories.
4.  **Portfolio Risk Management:** Dynamic asset allocation and cross-asset correlation during market crashes.

## Phase 2: Formulate Research Questions & Hypotheses
Align your thesis around the supervised baseline versus the Decision Transformer (DT).
*   **RQ1 (Primary):** Do active regime-switching policies (supervised and/or RL) outperform Buy-and-Hold on a risk-adjusted basis, net of costs?
*   **RQ2:** Where active policies clear Buy-and-Hold, does the Decision Transformer (DT) beat the supervised baseline router?
*   **H1:** Both Supervised Router and DT will achieve higher OOS Sharpe Ratio and lower Max Drawdown than Buy-and-Hold.
*   **H2:** The DT will outperform the Supervised Router in predicting regime shifts, but will suffer from higher turnover and fee sensitivity.

## Phase 3: Formalize the Methodology (The "How")
Document your system as a rigorous scientific framework.
1.  **The Supervised Baseline:** Document the Random Forest classifier (14 features) and how it gates the Grid, Trend, and Swing engines.
2.  **MDP Formulation:** Define your State (regime features + portfolio), Action space (engine routing + sizing), and Reward function (excess over passive - fees - drawdown penalty).
3.  **Gymnasium Environment:** Detail the custom `TradingEnv` built to simulate realistic fees, slippage, and engine primitives.
4.  **Decision Transformer:** Explain offline sequence-based learning, Return-to-Go (RTG) conditioning, and the training pipeline on logged trajectories.

## Phase 4: Data Gathering & Experimentation
Run formal experiments using the walk-forward evaluation method:
1.  **Phase 0 (Regression):** Ensure the `TradingEnv` perfectly replicates the known Supervised baseline equity curve to prove environment faithfulness.
2.  **Data Generation:** Log offline trajectories using the 5-engine supervised system.
3.  **RL Training:** Train the Decision Transformer offline on the logged sequences (ETH-USDT, BNB-USDT primarily).
4.  **Walk-Forward OOS Testing:** Run the 3-way comparison (Buy-and-Hold vs Supervised vs DT) on purged and embargoed out-of-sample windows.

## Phase 5: Analysis & Statistical Validation (PC10 & PC11)
Analyze the data gathered in Phase 4 rigorously.
*   **Statistical Tests:** Use Diebold-Mariano tests on per-bar returns to prove if the DT's outperformance is statistically significant.
*   **Risk Metrics:** Compare Block-bootstrapped confidence intervals for Sharpe Ratio and Maximum Drawdown.
*   **Critical Reflection:** Identify weaknesses in the RL agent (e.g., turnover, fee traps, reaction latency on 1h bars) and evaluate if the complexity of a Decision Transformer is justified over a simpler supervised model.

## Phase 6: Structuring the Thesis Document
A standard structure for your final document:
1.  **Abstract:** Quick summary of the problem, method, and results.
2.  **Introduction:** Motivation, problem statement, and research objectives.
3.  **Literature Review:** What others have done (Regimes, ML, RL).
4.  **Methodology & System Architecture:** Supervised baseline, MDP formulation, and Decision Transformer.
5.  **Experimental Setup:** Gymnasium environment, reward shaping, and walk-forward parameters.
6.  **Results & Discussion:** 3-way comparison metrics, Diebold-Mariano tests, ablation studies (fee impact).
7.  **Critical Evaluation:** Reflection on strengths, limitations, and practical implications.
8.  **Conclusion:** Final thoughts and future work.

## Proposed Timeline (18 Weeks)

| Weeks | Tasks |
| :--- | :--- |
| **Weeks 1–3** | Literature review (regime-switching, ML for finance, RL for execution); research design; feature-engineering pipeline (14 technical indicators in Rust). |
| **Weeks 4–6** | Regime classifier development: Random Forest training per pair, isotonic probability calibration, forward-looking dynamic labelling, TimeSeriesSplit cross-validation; HMM/GMM baseline comparison to validate supervised design choice. |
| **Weeks 7–9** | Multi-engine strategy implementation (Grid, Trend, Swing, Mean-Reversion); Rust Strategy/Connector trait architecture; Gymnasium-compatible Python simulation environment. |
| **Weeks 10–12** | System integration and deployment: hybrid Rust + Python Docker stack, AWS EC2 (Tokyo), GitHub Actions CI/CD, MLOps hot-reload pipeline; simulation-environment validation against Rust engine on off-baseline action sequences; paper-trading launch. Phase 0–1 (faithful env + validated supervised baseline) must be complete by end of Week 12. |
| **Weeks 13–15** | Walk-forward out-of-sample validation (central test, highest priority). Decision Transformer (DT) offline sequence training on ETH-USDT and BNB-USDT (must-do); DOGE-USDT and XRP-USDT stress tests if time allows. Paper-trading data collection; go/no-go for live micro-stake. |
| **Weeks 16–18** | RL results analysis (Sharpe, drawdown, Diebold–Mariano tests, Bonferroni correction) comparing the Decision Transformer to the supervised baseline and Buy-and-Hold. Dissertation writing and final submission. |

---
> [!TIP]
> **Immediate Next Step:** Start with **Phase 1** / **Weeks 1-3**. Open Google Scholar and search for *"Machine learning market regime detection"* and *"Random Forest algorithmic trading"*. Create a spreadsheet to log the papers you read.
