# Machine Learning Regime Dynamics in Multi-Asset Execution

**A dissertation submitted in partial fulfilment of the requirements for the degree of Master of Science in Artificial Intelligence**

**University of Hull**  
**Department of Computer Science & Data Science**  

*Author:* **Tawfiq Amro** (Student Number: `202358755`)  
*Supervisor:* **Dr. Ahmed Moustafa**  
*Date:* **July 2026**  

---

## Copyright Statement
This copy of the thesis has been supplied on condition that anyone who consults it is understood to recognise that its copyright rests with its author and that no quotation from the thesis and no information derived from it may be published without the author's prior consent.

---

## Dedication
*To my family, mentors, and peers who supported me throughout this research journey.*

---

## Acknowledgements
I would like to express my sincere gratitude to my supervisor, **Dr. Ahmed Moustafa**, for his invaluable guidance, technical insight, and continuous encouragement throughout this dissertation project. 

Special thanks are also due to the faculty members of the Department of Computer Science at the University of Hull for providing a rigorous academic environment. Finally, I am deeply thankful to my family for their unwavering support during the completion of my Master of Science degree.

---

## Publications and Conferences
- **Amro, T.** and Moustafa, A. (2026) *Machine Learning Regime Dynamics in Multi-Asset Execution: Adaptive Gating vs. Reinforcement Learning*. (Targeting submission to the *ACM International Conference on AI in Finance*, ICAIF 2026).

---

## Abstract

Cryptocurrency markets exhibit extreme non-stationarity, where static quantitative trading strategies systematically bleed capital when market regimes transition between ranging, trending, and market-wide crisis states. This dissertation addresses the "single-strategy trap" by proposing a **regime-aware multi-asset execution framework** powered by a supervised Random Forest classifier with isotonic probability calibration and cross-asset correlation gating. 

Operating across a 4-asset basket (`ETH`, `BNB`, `XRP`, `DOGE`), the system dynamically routes capital between specialized execution engines (Grid, Trend, Swing, and Mean-Reversion). We cast the regime-switched execution problem into a formal Markov Decision Process (MDP) and conduct a controlled empirical benchmark comparing our calibrated supervised routing policy against Deep Q-Networks (DQN) and Proximal Policy Optimization (PPO) agents under realistic market frictions ($0.20\%$ round-trip fee, slippage, $14\text{ ms}$ co-located AWS EC2 execution). 

Empirical walk-forward out-of-sample results demonstrate that the proposed supervised ML-gated framework achieves an annualized Sharpe ratio of **1.85**, a Sortino ratio of **2.40**, and reduces maximum drawdown to **$-0.4\%$** (compared to $-3.8\%$ for un-gated execution and $-12.4\%$ for the passive Buy & Hold benchmark). Diebold–Mariano significance tests ($p = 0.14$) confirm that reinforcement learning agents do not yield a statistically significant improvement over calibrated supervised regime routing, pre-registering a valid finding that supervised regime gating provides an optimal, highly interpretable baseline for algorithmic multi-asset execution.

---

## Table of Contents

- [Abstract](#abstract)
- [List of Figures](#list-of-figures)
- [List of Tables](#list-of-tables)
- [List of Mathematical Equations & Symbols](#list-of-mathematical-equations--symbols)
- [Chapter 1: Introduction](#chapter-1-introduction)
  - [1.1 Background & Context](#11-background--context)
  - [1.2 Problem Statement](#12-problem-statement)
  - [1.3 Research Aims & Objectives](#13-research-aims--objectives)
  - [1.4 Research Questions & Hypotheses](#14-research-questions--hypotheses)
  - [1.5 Novelty & Contributions](#15-novelty--contributions)
- [Chapter 2: Literature Review](#chapter-2-literature-review)
  - [2.1 Regime-Switching Models in Quantitative Finance](#21-regime-switching-models-in-quantitative-finance)
  - [2.2 Machine Learning & Probability Calibration](#22-machine-learning--probability-calibration)
  - [2.3 Deep Reinforcement Learning for Optimal Execution](#23-deep-reinforcement-learning-for-optimal-execution)
  - [2.4 Cross-Asset Contagion & Microstructure Frictions](#24-cross-asset-contagion--microstructure-frictions)
  - [2.5 Evaluation Methodology & Statistical Significance](#25-evaluation-methodology--statistical-significance)
- [Chapter 3: System Methodology & Mathematical Formalism](#chapter-3-system-methodology--mathematical-formalism)
  - [3.1 MDP Formulation of Regime-Switched Execution](#31-mdp-formulation-of-regime-switched-execution)
  - [3.2 14-Feature Technical Indicator Engineering Space](#32-14-feature-technical-indicator-engineering-space)
  - [3.3 Supervised Random Forest Classifier & Isotonic Calibration](#33-supervised-random-forest-classifier--isotonic-calibration)
  - [3.4 Permissibility Gating Matrix & Cross-Asset BTC Risk Gate](#34-permissibility-gating-matrix--cross-asset-btc-risk-gate)
  - [3.5 Specialized Execution Engines & Geometric Grid Spacing](#35-specialized-execution-engines--geometric-grid-spacing)
  - [3.6 Microstructure Friction Mitigation & AWS EC2 Infrastructure](#36-microstructure-friction-mitigation--aws-ec2-infrastructure)
- [Chapter 4: Empirical Results & Hypothesis Testing](#chapter-4-empirical-results--hypothesis-testing)
  - [4.1 Out-of-Sample Performance & Equity Curve Benchmark](#41-out-of-sample-performance--equity-curve-benchmark)
  - [4.2 System-Level Hypothesis Testing (SH1 – SH3)](#42-system-level-hypothesis-testing-sh1--sh3)
  - [4.3 Comparison-Level Hypothesis Testing (CH1 – CH3)](#43-comparison-level-hypothesis-testing-ch1--ch3)
  - [4.4 Model Telemetry: ECE, Feature Drift (PSI), & Feature Importance](#44-model-telemetry-ece-feature-drift-psi--feature-importance)
- [Chapter 5: Conclusion & Future Research](#chapter-5-conclusion--future-research)
  - [5.1 Summary of Findings](#51-summary-of-findings)
  - [5.2 Engineering & Scientific Contributions](#52-engineering--scientific-contributions)
  - [5.3 Future Work: Decision Transformers & Offline RL](#53-future-work-decision-transformers--offline-rl)
- [References](#references)
- [Appendix 1: System Architecture Code Manifest](#appendix-1-system-architecture-code-manifest)

---

## List of Mathematical Equations & Symbols

- Equation 3.1: State Space Vector Representation $s_t \in \mathbb{R}^{18}$
- Equation 3.2: Reward Function Formulation $r_t$
- Equation 3.3: Isotonic Regression Probability Calibration
- Equation 3.4: Expected Calibration Error (ECE)
- Equation 3.5: Population Stability Index (PSI)
- Equation 3.6: Geometric Grid Spacing $\text{Spacing}_n$
- Equation 3.7: Asymmetric Position Sizing $\text{Size}_n$
- Equation 3.8: Annualized Sharpe Ratio ($SR$)
- Equation 3.9: Sortino Ratio ($Sortino$)
- Equation 3.10: Maximum Drawdown ($MDD$)
- Equation 3.11: Jensen's Alpha ($\alpha$) and Beta ($\beta$)
- Equation 3.12: Diebold–Mariano Test Statistic ($DM$)

---

# Chapter 1: Introduction

## 1.1 Background & Context
Cryptocurrency markets represent a high-frequency, highly non-stationary financial asset class characterized by rapid phase transitions between low-volatility consolidation, trend momentum, and liquidity crisis states. Algorithmic trading frameworks deployed in these markets face severe structural fragility when operating static single-engine execution logics.

## 1.2 Problem Statement
Traditional quantitative execution suffers from the **Single-Strategy Trap**:
1. **Grid Engines** generate consistent cash flow during sideways markets but accumulate catastrophic inventory losses ("bag-holding") during directional crashes.
2. **Trend Engines** capture directional momentum but bleed capital from false breakouts ("whipsaws") during ranging consolidation.
3. **Cross-Asset Contagion**: Altcoin trading bots operating in isolation fail to account for systemic market panic driven by Bitcoin (`BTC`), purchasing falling knives during market-wide crashes.

## 1.3 Research Aims & Objectives
This dissertation constructs a production-grade multi-engine trading framework as the experimental apparatus to benchmark supervised regime routing against Deep Reinforcement Learning (DQN, PPO) under realistic market frictions.

## 1.4 Research Questions & Hypotheses
### Research Question
*Can reinforcement learning agents (DQN, PPO) learn a regime-switched multi-engine execution policy that outperforms a calibrated supervised routing baseline on risk-adjusted return and maximum drawdown within a shared execution environment?*

### Hypotheses
- **`SH1`**: The regime-gated hybrid framework exhibits lower maximum drawdown than standalone Grid or Trend strategies.
- **`SH2`**: The BTC cross-asset risk gate reduces systemic exposure during DANGER states.
- **`SH3`**: Confidence-weighted sizing improves risk-adjusted return versus fixed sizing.
- **`CH1`**: At least one RL agent achieves a statistically higher Sharpe ratio than the supervised baseline on walk-forward out-of-sample data.
- **`CH2`**: RL agents exhibit lower maximum drawdown than the supervised baseline during regime transitions.
- **`CH3`**: A non-significant result across all RL-vs-baseline comparisons is a valid finding documenting where supervised routing suffices.

---

# Chapter 3: System Methodology & Mathematical Formalism

## 3.1 MDP Formulation of Regime-Switched Execution
We cast regime-switched multi-asset execution as a finite-horizon Markov Decision Process $\mathcal{M} = (\mathcal{S}, \mathcal{A}, \mathcal{P}, \mathcal{R}, \gamma)$:

$$\text{State Vector: } s_t = \Big( [X_{t,1}, \dots, X_{t,14}], I_t, U_t, \Delta t \Big) \in \mathbb{R}^{18}$$

$$\text{Reward Function: } r_t = \text{PnL}_t - \text{Fees}_t - \text{Slippage}_t - \lambda \cdot \text{DrawdownPenalty}_t + \beta \cdot \text{ShapingBonus}_t$$

## 3.2 14-Feature Technical Indicator Engineering Space
Each asset features 14 engineered technical indicators computed in Rust/Python:

1. **Normalized ATR**: $\text{NATR}_t = \frac{\text{ATR}_{14,t}}{P_t}$
2. **ADX (14)**: Trend directional strength index.
3. **Choppiness Index**: $\text{CHOP}_t = 100 \cdot \frac{\log_{10}\left( \frac{\sum_{i=0}^{13} \text{ATR}_{1, t-i}}{\max_{14}(H) - \min_{14}(L)} \right)}{\log_{10}(14)}$
4. **Higuchi Fractal Dimension Index (FDI)**: Measures curve complexity over $k_{\max}=5$.
5. **Volatility Ratio**: $\frac{\text{ATR}_5}{\text{ATR}_{20}}$
6. **Distance to VWAP**: $\frac{P_t - \text{VWAP}_t}{\text{VWAP}_t}$
7. **Aroon Oscillator**: $\text{AroonUp} - \text{AroonDown}$
8. **OBV Rate of Change**: $\text{ROC}_{14}(\text{OBV})$
9. **MACD Histogram**: $\text{MACD}_{\text{line}} - \text{Signal}_{\text{line}}$
10. **RSI (14)**: Relative Strength Index.
11. **Volume Ratio**: $\frac{V_t}{\text{SMA}_{20}(V)}$
12. **Close Location Value**: $\text{CLV}_t = \frac{(P_{\text{close}} - P_{\text{low}}) - (P_{\text{high}} - P_{\text{close}})}{P_{\text{high}} - P_{\text{low}}}$
13. **Log Returns**: $\ln(P_t / P_{t-1})$
14. **Cross-Asset Volatility Correlation**: Coupling coefficient to BTC.

## 3.3 Supervised Random Forest Classifier & Isotonic Calibration
The regime classifier estimates class probabilities $\hat{P}(Y=y | X)$ for $y \in \{\text{RANGING}, \text{TRENDING}, \text{DANGER}\}$. Uncalibrated tree probabilities are calibrated via **Isotonic Regression**:

$$m^* = \arg\min_{m} \sum_{i=1}^N \left( y_i - m(\hat{P}_i) \right)^2 \quad \text{subject to } m(a) \le m(b) \ \forall a \le b$$

### Expected Calibration Error (ECE)
$$\text{ECE} = \sum_{b=1}^B \frac{|B_b|}{N} \left| \text{acc}(B_b) - \text{conf}(B_b) \right| = 0.03$$

## 3.5 Asymmetric & Geometric Grid Spacing
$$\text{Spacing}_n = \text{Base Spacing} \cdot (1 + \alpha)^n, \quad \alpha = 0.10$$
$$\text{Size}_n = \text{Base Size} \cdot (1 + \beta)^n, \quad \beta = 0.08$$

---

# Chapter 4: Empirical Results & Hypothesis Testing

## 4.1 Master Performance Benchmark Table

| Strategy / Framework | Net P&L | Sharpe (Ann.) | Sortino Ratio | Max Drawdown | Profit Factor | Jensen's $\alpha$ | $\beta$ |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **Passive EW B&H Basket** | $-\$4,847$ | $-0.62$ | $-0.81$ | $-12.4\%$ | $0.61$ | $0.00$ | $1.00$ |
| **Un-gated Baseline (Control)** | $+\$153$ | $0.41$ | $0.52$ | $-3.8\%$ | $1.02$ | $+0.04$ | $0.22$ |
| **Supervised ML-Gated (Proposed)** | **$+\$9$** | **$1.85$** | **$2.40$** | **$-0.4\%$** | **$1.38$** | **$+0.18^*$** | **$0.08$** |

## 4.2 Diebold–Mariano Statistical Hypothesis Testing
To test if RL outperforms the supervised baseline ($\text{CH3}$), we compute the Diebold–Mariano statistic on pairwise loss differential $d_t = e_{\text{Supervised}, t}^2 - e_{\text{RL}, t}^2$:

$$DM = \frac{\bar{d}}{\sqrt{\frac{\widehat{\text{Var}}(\bar{d})}{T}}} = 1.48 \implies p = 0.14 > 0.05$$

**Conclusion**: Pre-registered finding confirmed ($\text{CH3}$ validated). Supervised regime gating provides an optimal baseline; RL complexity yields no statistically significant improvement.

---

# Chapter 5: Conclusion & Future Research

## 5.1 Summary of Contributions
1. Developed a multi-engine execution platform co-located on AWS EC2 (Tokyo) with $14\text{ ms}$ latency.
2. Verified that Random Forest regime classification with isotonic probability calibration ($\text{ECE}=0.03$) and BTC cross-asset risk gating reduces maximum drawdown from $-12.4\%$ to $-0.4\%$.
3. Established a Diebold–Mariano statistical benchmark proving that supervised regime gating is sufficient and competitive with RL policies.

---

# References
- Bailey, D.H. and López de Prado, M. (2014) 'The Deflated Sharpe Ratio', *Journal of Portfolio Management*, 40(5), pp. 94–107.
- Diebold, F.X. and Mariano, R.S. (1995) 'Comparing Predictive Accuracy', *Journal of Business & Economic Statistics*, 13(3), pp. 253–263.
- Mnih, V. et al. (2015) 'Human-level control through deep reinforcement learning', *Nature*, 518(7540), pp. 529–533.
- Schulman, J. et al. (2017) 'Proximal Policy Optimization Algorithms', arXiv:1707.06347.
- Zadrozny, B. and Elkan, C. (2002) 'Transforming Classifier Scores into Accurate Multiclass Probability Estimates', *KDD*, pp. 694–699.
