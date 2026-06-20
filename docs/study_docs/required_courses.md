# Required Knowledge Domains & Courses for the Thesis

Given your 12 years of software engineering experience, the implementation, system architecture, Rust/Python integrations, and MLOps aspects of this project are already fully covered. To successfully execute and defend this Master's thesis, your focus should be entirely on the mathematical, financial, and advanced AI concepts.

Here are the required domains of study, specific courses/resources, and estimated hours of study:

## 1. Quantitative Finance & Market Microstructure
You need to deeply understand how markets operate, how strategies are evaluated, and how costs eat into theoretical edge.
*   **Algorithmic Trading Strategies:** Mathematics of Mean-Reversion and Momentum models.
*   **Performance Metrics:** Risk-adjusted returns (Sharpe, Sortino) and Maximum Drawdown.
*   **Transaction Cost Analysis & Risk Management:** Execution slippage, Kelly Criterion.

**Recommended Courses & Resources (~40 Hours):**
*   **Course:** [Machine Learning for Trading by Georgia Tech (Udacity)](https://www.udacity.com/course/machine-learning-for-trading--ud501)
    *   *Focus:* Portfolio risk, Sharpe ratios, and market mechanics.
    *   *Time:* ~20 hours
*   **Course:** [Introduction to Portfolio Construction and Analysis with Python (Coursera / EDHEC)](https://www.coursera.org/learn/introduction-portfolio-construction-python)
    *   *Focus:* Deep mathematical calculation of drawdowns and risk metrics.
    *   *Time:* ~20 hours
*   **Optional Course:** [AI for Trading Nanodegree (Udacity)](https://www.udacity.com/course/ai-for-trading--nd880)
    *   *Focus:* Broad overview of quantitative trading, signals, and portfolio optimization.
    *   *Time:* ~40 hours (Optional)

## 2. Financial Statistics & Time Series Analysis
Financial data heavily violates standard machine learning assumptions (like IID). You must handle non-stationary time series robustly to defend your thesis.
*   **Time Series Cross-Validation:** Purged and Embargoed CV.
*   **Statistical Significance Testing:** Diebold-Mariano Test.
*   **Bootstrapping Financial Data:** Block-Bootstrapping for confidence intervals.

**Recommended Courses & Resources (~35 Hours):**
*   **Book/Self-Study:** *Advances in Financial Machine Learning* by Marcos López de Prado
    *   *Focus:* Chapters on Purged Cross-Validation, Bootstrapping, and fractional differentiation. This is the academic "bible" for this exact topic.
    *   *Time:* ~25 hours reading and implementing formulas.
*   **Course:** [Practical Time Series Analysis (Coursera / SUNY)](https://www.coursera.org/learn/practical-time-series-analysis)
    *   *Focus:* Stationarity, auto-correlation, ARIMA, and Hidden Markov Models (HMM).
    *   *Time:* ~10 hours (skip the basic ML, focus solely on time series stats).
*   **Optional Course:** [Time Series Forecasting (Udacity)](https://www.udacity.com/course/time-series-forecasting--ud980)
    *   *Focus:* Autoregressive models and handling sequential data.
    *   *Time:* ~15 hours (Optional)

## 3. Machine Learning for Finance (Supervised)
Focusing on how standard ML algorithms are adapted to survive noisy financial data.
*   **Tree-Based Models:** Random Forests, preventing overfitting, robust feature importance.
*   **Probability Calibration:** Isotonic Regression and Platt Scaling.

**Recommended Courses & Resources (~25 Hours):**
*   **Course:** [How to Win a Data Science Competition (Coursera / HSE University)](https://www.coursera.org/learn/competitive-data-science)
    *   *Focus:* Phenomenal modules on tree-based models (Random Forest/XGBoost) and advanced hyperparameter tuning.
    *   *Time:* ~15 hours
*   **Documentation / Hands-on:** [Scikit-Learn Probability Calibration Guide](https://scikit-learn.org/stable/modules/calibration.html)
    *   *Focus:* Isotonic regression implementation and statistical theory.
    *   *Time:* ~10 hours
*   **Optional Course:** [Intro to Machine Learning (Udacity)](https://www.udacity.com/course/intro-to-machine-learning--ud120)
    *   *Focus:* Great foundations if you need a refresher on decision trees and random forests.
    *   *Time:* ~20 hours (Optional)

## 4. Advanced Reinforcement Learning (RL)
This is the core of your new thesis direction. You must go beyond basic online RL and understand offline, sequence-based modeling.
*   **Markov Decision Processes (MDP):** Formulating the financial trading problem.
*   **Reward Shaping:** Designing cost-aware, delayed-reward functions.
*   **Offline Reinforcement Learning & Decision Transformers:** Treating RL as a sequence modeling problem with Return-to-Go (RTG).

**Recommended Courses & Resources (~50 Hours):**
*   **Course:** [Reinforcement Learning Specialization by Univ. of Alberta (Coursera)](https://www.coursera.org/specializations/reinforcement-learning)
    *   *Focus:* Courses 1 and 2 for deep grounding in MDPs, value functions, and policy gradients.
    *   *Time:* ~30 hours
*   **Course/Tutorial:** [Offline Reinforcement Learning Tutorial (UC Berkeley / Sergey Levine)](https://offline-rl.github.io/)
    *   *Focus:* Why online RL fails in strict environments and how offline datasets are used.
    *   *Time:* ~10 hours
*   **Research Paper & Code:** [Decision Transformer: Reinforcement Learning via Sequence Modeling (arXiv)](https://arxiv.org/abs/2106.01345)
    *   *Focus:* Read the original paper and the Hugging Face [Decision Transformer implementation guide](https://huggingface.co/blog/decision-transformers).
    *   *Time:* ~10 hours
*   **Optional Course:** [Deep Reinforcement Learning Nanodegree (Udacity)](https://www.udacity.com/course/deep-reinforcement-learning-nanodegree--nd893)
    *   *Focus:* Extensive hands-on coding for DQN, PPO, and Actor-Critic models if you want to implement the baselines from scratch.
    *   *Time:* ~40 hours (Optional)

---

### **Total Estimated Study Time: ~150 Hours**
*(Roughly 3-4 weeks of full-time intensive focus, or 2 months part-time).*
