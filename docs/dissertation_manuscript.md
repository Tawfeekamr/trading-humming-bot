# Learned Withdrawal and Evaluation Blindness in Reinforcement Learning for Trading: A Corrected-Protocol Case Study

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
- **Amro, T.** and Moustafa, A. (2026) *Regime-Aware Multi-Asset Trading: Adaptive Gating vs. Reinforcement Learning under a Corrected Evaluation Protocol*. (Working title; targeting submission to the *ACM International Conference on AI in Finance*, ICAIF 2026).

---

## Abstract

Cryptocurrency markets exhibit extreme non-stationarity, where static quantitative trading strategies systematically bleed capital when market regimes transition between ranging, trending, and market-wide crisis states. This dissertation addresses the "single-strategy trap" by proposing a **regime-aware multi-asset execution framework** powered by a supervised Random Forest classifier with isotonic probability calibration and cross-asset correlation gating. 

Operating across a 4-asset basket (`ETH`, `BNB`, `XRP`, `DOGE`), the system routes capital between the two production execution engines (Grid, Trend) with additional strategy prototypes (Swing, Mean-Reversion) explored in research backtests. We cast the regime-switched routing problem into a formal Markov Decision Process (MDP) and conduct a controlled empirical benchmark comparing our calibrated supervised routing policy against Proximal Policy Optimization (PPO) agents under simulated market frictions (fees and slippage in the bar-level replay environment).

Empirical results are reported from a corrected walk-forward evaluation protocol (timestamp-aligned comparators, 70-bar train/test embargo, fold-specific supervised baselines, pinned data windows, multiplicative-drawdown definitions). Under this protocol, **neither the supervised gating policy nor the PPO agent outperformed passive buy-and-hold** on either asset over the evaluated window (ETH: B&H +44.8% vs gated −22.3% vs PPO −13.1%; BNB: B&H +3.8% vs gated −17.0% vs PPO −29.0%), and no routing comparison is statistically interpretable: the design's minimum detectable effect (60–103% cumulative return at 80% power) exceeds the observed differences by an order of magnitude, PPO seed variance (up to 27pp return swing within a fold) dominates between-method differences, and the two policies operated at materially different capital deployment (4–5% vs 21–61% capital-weighted exposure). Diagnostic analysis further shows the PPO agent's low exposure is *learned withdrawal under a drawdown-penalising reward* — a permanently-flat policy scores higher than the trained policy — and that the regime classifier's DANGER warnings are false 38–47% of the time, forgoing upside that buy-and-hold captures. The dissertation's contribution is the negative result, the protocol that establishes it, and the causal diagnosis of why learned routing degenerates to abstention; it does not claim a validated trading edge.

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
  - [4.1 Master Performance Benchmark Table (Corrected Protocol)](#41-master-performance-benchmark-table)
  - [4.2 Statistical Hypothesis Testing and Power](#42-statistical-hypothesis-testing)
  - [4.3 Retractions and Protocol Corrections](#43-retractions-and-protocol-corrections)
  - [4.4 Why the Learned Router Abstains: Reward Decomposition](#44-why-the-learned-router-abstains-reward-decomposition)
  - [4.5 Regime-Signal Economics](#45-regime-signal-economics)
- [Chapter 5: Conclusion & Future Research](#chapter-5-conclusion--future-research)
  - [5.1 Summary of Findings](#51-summary-of-findings)
  - [5.2 Engineering & Scientific Contributions](#52-engineering--scientific-contributions)
  - [5.3 Future Work](#53-future-work)
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
- Equation 4.1: Paired Mean-Difference Test with Newey–West HAC Standard Errors

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
This dissertation constructs a production-grade multi-engine trading framework as the experimental apparatus to benchmark supervised regime routing against Deep Reinforcement Learning (PPO) under simulated market frictions (fees and slippage in the bar-level replay environment).

## 1.4 Research Questions & Hypotheses
### Research Question
*Can reinforcement learning agents (PPO) learn a regime-switched multi-engine routing policy that outperforms a calibrated supervised routing baseline on risk-adjusted return and maximum drawdown within a shared execution environment?*

**Answer (Chapter 4):** No — and the question could not have been answered affirmatively at this sample scale. Neither policy beat passive buy-and-hold; the comparison is underpowered by an order of magnitude; seed variance dominates method differences; and the learned policy is abstention under a drawdown-penalising reward.

### Hypotheses
- **`SH1`**: The regime-gated hybrid framework exhibits lower maximum drawdown than standalone Grid or Trend strategies. *(Not supported as a timing effect: observed drawdown differences are explained by capital exposure — exposure-matched random entries drew down less.)*
- **`SH2`**: The BTC cross-asset risk gate reduces systemic exposure during DANGER states.
- **`SH3`**: Confidence-weighted sizing improves risk-adjusted return versus fixed sizing.
- **`CH1`**: At least one RL agent achieves a statistically higher Sharpe ratio than the supervised baseline on walk-forward out-of-sample data. *(Rejected.)*
- **`CH2`**: RL agents exhibit lower maximum drawdown than the supervised baseline during regime transitions. *(Rejected as evidence of skill: worse than exposure-matched random entries on both assets.)*
- **`CH3`**: A non-significant result across all RL-vs-baseline comparisons is a valid finding documenting where supervised routing suffices. *(Upheld only in power-qualified form: the design cannot distinguish parity from differences an order of magnitude larger than those observed.)*

---

# Chapter 3: System Methodology & Mathematical Formalism

## 3.1 MDP Formulation of Regime-Switched Execution
We cast regime-switched multi-asset execution as a finite-horizon Markov Decision Process $\mathcal{M} = (\mathcal{S}, \mathcal{A}, \mathcal{P}, \mathcal{R}, \gamma)$:

$$\text{State Vector: } s_t = \Big( [X_{t,1}, \dots, X_{t,14}], I_t, U_t, \Delta t \Big) \in \mathbb{R}^{18}$$

$$\text{Reward Function (implemented): } r_t = \underbrace{(R^{eq}_t - R^{bh}_t)}_{\text{excess over buy\&hold}} - \underbrace{f \cdot \text{Turnover}_t}_{\text{fee}} - \underbrace{\lambda \cdot \Delta DD_t}_{\text{drawdown step}}, \quad \lambda = 0.5$$

*Note: this reward is central to the findings — Chapter 4.4 shows its
drawdown term rivals the entire PnL term and that a permanently-flat
policy outscores the trained policy, i.e. abstention is optimal under
this specification. The earlier draft's generic form (with a shaping
bonus) was not implemented.*

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
14. **Aroon Oscillator (25)**: Trend-direction persistence measure, $100 \times \frac{\text{bars since high} - \text{bars since low}}{25}$.

## 3.3 Supervised Random Forest Classifier & Isotonic Calibration
The regime classifier estimates class probabilities $\hat{P}(Y=y | X)$ for $y \in \{\text{RANGING}, \text{TRENDING}, \text{DANGER}\}$. Uncalibrated tree probabilities are calibrated via **Isotonic Regression**:

$$m^* = \arg\min_{m} \sum_{i=1}^N \left( y_i - m(\hat{P}_i) \right)^2 \quad \text{subject to } m(a) \le m(b) \ \forall a \le b$$

### Expected Calibration Error (ECE)
$$\text{ECE} = \sum_{b=1}^B \frac{|B_b|}{N} \left| \text{acc}(B_b) - \text{conf}(B_b) \right|$$

*(No calibration figure is reported: the previously reported ECE = 0.03
had no retained, traceable artifact and remains withdrawn. Classifier
quality is instead evidenced by OOS now-cast accuracy — 0.80–0.87 per
asset — with the caveat of §4.5 that accuracy did not translate into
gating value.)*

## 3.5 Asymmetric & Geometric Grid Spacing
$$\text{Spacing}_n = \text{Base Spacing} \cdot (1 + \alpha)^n, \quad \alpha = 0.10$$
$$\text{Size}_n = \text{Base Size} \cdot (1 + \beta)^n, \quad \beta = 0.08$$

---

# Chapter 4: Empirical Results & Hypothesis Testing

All results in this chapter come from the corrected walk-forward protocol:
6 chronological folds per asset (train 4,320 bars, test 720, step 2,160,
70-bar embargo sized to the maximum feature lookback), fold-specific
Random-Forest baselines, timestamp-aligned comparators, pinned data window
ending 2026-07-05, and canonical (multiplicative, pooled-by-concatenation)
drawdowns. The full audit trail - per-bar timestamped return series,
per-fold model provenance manifests, run manifest, and three corrective
batches of protocol fixes - is committed alongside this manuscript.

## 4.1 Master Performance Benchmark Table

**Corrected protocol** (pooled over 6 folds per asset; n = 4,314 hourly bars each):

| ETH-USDT | Total Return | Sharpe (ann.) | Max Drawdown | Profit Factor | Trades | Capital Exposure |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Buy & Hold | **+44.8%** | - | -51.1% | 1.04 | - | 100% |
| Supervised RF-gated | -22.3% | -1.86 | -24.7% | 0.90 | 136 | 21.1% |
| PPO (seed 42) | -13.1% | -1.74 | -16.5% | 0.79 | 46 | 4.3% |

| BNB-USDT | Total Return | Sharpe (ann.) | Max Drawdown | Profit Factor | Trades | Capital Exposure |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Buy & Hold | **+3.8%** | - | -50.3% | 1.00 | - | 100% |
| Supervised RF-gated | -17.0% | -0.72 | -42.6% | 0.95 | 129 | 60.5% |
| PPO (seed 42) | -29.0% | -3.04 | -29.3% | 0.65 | 45 | 5.2% |

*Capital exposure is the capital-weighted definition (mean
|position|/equity), not time-in-market; the two policies operated at
materially different deployment (RF/PPO ratio 4.9x on ETH, 11.5x on BNB),
so all PPO-vs-RF comparisons in this chapter are descriptive.*

**Reading of the table:** no routing policy beat passive exposure on
either asset. The lower drawdowns of the routed policies are an exposure
effect, not a timing effect: capital-matched random-entry baselines at
PPO's own 4-5% deployment produced median MaxDD of 2.9-3.5%, versus PPO's
16.5-29.3% - PPO sits at the 100th percentile (worst) of the
exposure-matched random distribution on both assets.

The previously reported table (Sharpe 1.85, MaxDD -0.4%, from the
single-window June-July 2026 benchmark under the contaminated protocol)
is withdrawn; see 4.3.

## 4.2 Statistical Hypothesis Testing

The implemented statistic is a **paired mean-difference test on realised
per-bar returns with Newey-West HAC standard errors** (lag by the standard
rule floor(4(n/100)^(2/9)) = 9; Holm-corrected across assets). It is not
a Diebold-Mariano test (DM is defined on forecast-loss differentials); an
earlier draft mislabelled it as such.

| PPO vs fold-specific RF | stat | p | Holm-p | n |
| :--- | :---: | :---: | :---: | :---: |
| ETH | +0.43 | 0.665 | 1.00 | 4,314 |
| BNB | -0.33 | 0.743 | 1.00 | 4,314 |

Stationary-bootstrap 95% CIs for the MaxDD difference (PPO - RF): ETH
[-0.342, +0.090]; BNB [-0.381, +0.161] - both straddle zero.

### 4.2.1 Statistical power: the null is uninterpretable, not confirmed

The test is **underpowered by an order of magnitude**. Using the same HAC
variance estimator:

| | ETH | BNB |
| :--- | :---: | :---: |
| Observed cumulative difference | +9.2pp | -12.0pp |
| Minimum detectable effect (80% power, alpha=0.05) | **59.7pp** | **102.7pp** |
| Achieved power at observed difference | 7.2% | 6.2% |
| n required to detect the observed difference | 180,943 bars (20.7y) | 314,103 bars (35.9y) |

**"Parity" is therefore not a supported claim.** The correct statement is:
*at the realised sample size this design cannot distinguish parity from
differences far larger than the one observed.* CH3's "non-significance is
a valid finding" holds only in this weaker, power-qualified form.

### 4.2.2 Seed variance dominates the method difference

Five PPO seeds (folds 0 and 3, both assets) swing cumulative return by up
to **27pp within a single fold** (BNB fold 3: -22.99% for seed 7 vs
+3.95% for seed 999) and MaxDD by 45x (0.007 vs 0.312); the paired
statistic crosses zero across seeds on BNB. The single-seed PPO-vs-RF
comparison above is one draw from this distribution and supports no
method-level claim in either direction. *(Scope: the seed sweep covers
folds 0 and 3 only; its figures are not comparable to the six-fold pooled
table in 4.1.)*

## 4.3 Retractions and protocol corrections

Three corrective batches established that previously reported favourable
numbers were artifacts. Withdrawn or corrected:

1. **Boundary defect** - PPO/RF return series previously included ~49
   pre-boundary bars per fold while the passive comparator started 100
   bars later; arrays were compared position-by-position across misaligned
   timestamps. Fixed: explicit warmup, timestamp inner-join alignment.
2. **Zero embargo** - now 70 bars (max feature lookback incl. EWM tails).
3. **Non-fold-pure baseline** - one long-window RF scored all folds; now
   fold-specific with provenance manifests.
4. **Mislabelled statistic** - "Diebold-Mariano, DM=1.48, p=0.14"
   (squared-error loss) corresponded to no implemented test; removed.
5. **Additive-drawdown MaxDD** - superseded by the multiplicative
   definition throughout.
6. **"Sharpe collapse on invested bars"** - was an annualisation artifact
   (applying sqrt(8760) to a subset of bars); corrected values equal the
   all-bars Sharpe exactly.
7. **False-DANGER opportunity cost** - the summed figures (2,243pp /
   1,183pp) multiply-counted overlapping 24-bar windows and were
   impossible against B&H +44.8%; replaced by per-signal means (+3.3% /
   +3.9%), non-overlapping 24-bar block gaps (0.96pp / 0.72pp per block),
   and the portfolio-level gated-minus-B&H gaps (-67.1pp / -20.8pp).
8. **n=6 correlations involving -B&H** - mechanically inflated; removed.

## 4.4 Why the learned router abstains: reward decomposition

PPO's 4.3%/5.2% capital-weighted exposure (vs 21.1%/60.5% for the
supervised baseline) is **learned withdrawal, not a configuration
ceiling**:

- Action distribution: the trained policy selects FLAT on 41.9%/45.8% of
  steps versus **0.2%/0.0% for a randomly-initialised policy** -
  abstention was learned. When active, the agent uses 73-83% of its
  available size ceiling (41-43% of active steps at >=90% of max), so the
  ceiling is not binding.
- Reward decomposition (lambda = 0.5, `EnvConfig.lambda_dd`): the
  drawdown-penalty term contributes 0.310/0.401 to total reward against a
  PnL term of -0.579/-0.328 - the penalty rivals the entire PnL term.
- **A permanently-flat policy scores -0.448/-0.038 versus the trained
  policy's -0.994/-0.854**: the reward function does not distinguish the
  learned policy from doing nothing. The optimal response to this reward
  is near-abstention; the routing question the experiment intended to
  ask is not the one the reward answers.

## 4.5 Regime-signal economics

The classifier's out-of-sample accuracy (0.80-0.87) does not convert into
gating value. On DANGER-predicted bars the market *rose* 47% (ETH) / 38%
(BNB) of the time (mean forward 24-bar return on DANGER bars: -0.79% /
-0.68% - an edge far too small to pay for skipping the false positives),
and 36.9%/17.9% of non-DANGER bars realised forward drawdowns <= -3%.
Non-overlapping 24-bar blocks entered on DANGER returned -0.57%/-0.49%
versus +0.39%/+0.23% for non-flagged blocks. At portfolio level the
gated strategy underperformed buy-and-hold by 67.1pp (ETH) and 20.8pp
(BNB) over the same window - the honest measure of the gate's cost.

---

# Chapter 5: Conclusion & Future Research

## 5.1 Summary of Findings

Under the corrected evaluation protocol, this dissertation's empirical
answer to its research question is a **well-evidenced negative result**:

1. **No routing edge.** Neither the supervised regime-gated policy nor the
   PPO agent outperformed passive buy-and-hold on either asset
   (ETH: B&H +44.8% vs gated −22.3% vs PPO −13.1%; BNB: B&H +3.8% vs
   gated −17.0% vs PPO −29.0%). **CH1 and CH2 are rejected**; the
   apparent lower drawdowns of routed policies are an exposure effect —
   capital-matched random entries at the same deployment drew down less
   than PPO on both assets.
2. **The comparison itself is uninterpretable at this scale.** The
   minimum detectable effect (60–103% cumulative at 80% power) exceeds
   every observed difference; detecting them would require 21–36 years of
   hourly data; and PPO seed variance (up to 27pp within a fold)
   dominates between-method differences. **CH3 is upheld only in a
   power-qualified form**: non-significance here documents an
   underpowered design, not parity.
3. **The learned policy is abstention.** The PPO agent's 4–5%
   capital-weighted exposure is learned withdrawal under a
   drawdown-penalising reward (λ=0.5): the penalty term rivals the entire
   PnL term, and a permanently-flat policy *outscores* the trained
   policy. The experiment measured a reward specification, not a routing
   strategy.
4. **Regime classification accuracy does not imply gating value.** At
   0.80–0.87 OOS accuracy, DANGER warnings are false 38–47% of the time,
   and the mean forward return when DANGER is correct (−0.7% to −1.0%)
   is too small to pay for the foregone upside — the gate cost 67.1pp
   (ETH) and 20.8pp (BNB) against buy-and-hold over the same window.

## 5.2 Engineering & Scientific Contributions

1. A production multi-engine trading platform deployed on AWS EC2
   (Tokyo), with a live ML regime pipeline (per-pair calibrated
   classifiers, immutable provenance manifests) feeding a Rust execution
   engine.
2. A corrected walk-forward evaluation protocol — timestamp-aligned
   comparators, lookback-sized embargo (70 bars), fold-pure baselines,
   pinned data windows, canonical drawdown definitions, exposure-matched
   random baselines, power analysis, seed sensitivity — with the full
   audit trail (per-bar return series, provenance manifests, run
   manifests) committed as evidence. This protocol is itself a
   contribution: it is what turned plausible-but-wrong positive numbers
   into an honest negative result across three corrective batches.
3. A causal diagnosis of reward misspecification in RL trading research:
   a drawdown penalty of the form implemented here makes abstention
   optimal, and the failure is detectable *before* deployment by
   comparing the trained policy's reward against a permanently-flat
   policy — a cheap, transferable diagnostic.

## 5.3 Future Work

1. **Reward redesign before scale-up.** The drawdown penalty must be
   bounded relative to achievable PnL (or replaced with a
   drawdown-conditioned shaping term) such that flat does not
   dominate; the flat-policy reward comparison should be a standing
   acceptance test during training.
2. **Multi-seed, longer-horizon evaluation.** Any future method claim
   requires seed distributions (≥5 seeds × all folds) and windows long
   enough to shrink the MDE below the effect size of interest — at
   minimum, a documented power analysis before the experiment is run.
3. **Cost-sensitive gating.** With a 38–47% false-DANGER rate, the gate
   needs asymmetric costs (or a higher confidence threshold calibrated
   against foregone return, not accuracy) before it can add value in
   rising regimes.
4. **Exposure-fair benchmarking.** Method comparisons should hold
   capital-weighted exposure constant across arms, or report
   exposure-normalised returns as a descriptive control, as demonstrated
   in §4.1.

---

# References
- Bailey, D.H. and López de Prado, M. (2014) 'The Deflated Sharpe Ratio', *Journal of Portfolio Management*, 40(5), pp. 94–107.
- Diebold, F.X. and Mariano, R.S. (1995) 'Comparing Predictive Accuracy', *Journal of Business & Economic Statistics*, 13(3), pp. 253–263.
- Mnih, V. et al. (2015) 'Human-level control through deep reinforcement learning', *Nature*, 518(7540), pp. 529–533.
- Schulman, J. et al. (2017) 'Proximal Policy Optimization Algorithms', arXiv:1707.06347.
- Zadrozny, B. and Elkan, C. (2002) 'Transforming Classifier Scores into Accurate Multiclass Probability Estimates', *KDD*, pp. 694–699.
