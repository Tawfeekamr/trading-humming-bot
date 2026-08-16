# Reward-Induced Abstention and Evaluation Blindness in a
Reinforcement-Learning Trading System: A Corrected-Protocol Case Study

*(Retitled Batch 10: "Learned Withdrawal ... in Reinforcement Learning
for Trading" read as a general claim about RL trading. The finding is
system- and reward-specific — the comparative literature shows punitive
rewards without abstention — so the title now names the mechanism
(reward-induced) and the object (a system, one instance) rather than
the field.)*

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

Cryptocurrency markets exhibit extreme non-stationarity, where static quantitative trading strategies systematically bleed capital when market regimes transition between ranging, trending, and market-wide crisis states. This dissertation addresses the "single-strategy trap" by building a **regime-aware multi-asset trading system** powered by a supervised Random Forest classifier with isotonic probability calibration and cross-asset correlation gating.

Operating across a 4-asset basket (`ETH`, `BNB`, `XRP`, `DOGE`), the system routes capital between the two production trading engines (Grid, Trend) with additional strategy prototypes (Swing, Mean-Reversion) explored in research backtests. We cast the regime-switched routing problem into a formal Markov Decision Process (MDP) and conduct a walk-forward evaluation comparing our calibrated supervised routing policy against Proximal Policy Optimization (PPO) agents under simulated market frictions (fees and slippage in the bar-level replay environment).

Empirical results are reported from a corrected walk-forward evaluation protocol (timestamp-aligned comparators, 70-bar train/test embargo, fold-specific supervised baselines, pinned data windows, multiplicative-drawdown definitions). Under this protocol, **neither the supervised gating policy nor the PPO agent outperformed passive buy-and-hold** on either asset over the evaluated window (ETH: B&H +38.0% vs gated −21.2% vs PPO −12.8%; BNB: B&H −4.3% vs gated −20.3% vs PPO −25.9%), and no routing comparison is statistically interpretable: the design's minimum detectable effect (82–179% cumulative return at 80% power, compounded basis) exceeds the observed differences by an order of magnitude, PPO seed variance (up to 27pp return swing within a fold) dominates between-method differences, and the two policies operated at materially different capital deployment (4–5% vs 21–61% capital-weighted exposure). Diagnostic analysis further shows the PPO agent's low exposure is *learned withdrawal under a drawdown-penalising reward* — a permanently-flat policy outscores the trained policy in 19 of 20 seed-fold-pair cells. This withdrawal is documented under one reward specification that double-counts transaction costs (fees enter both the equity return and, again, an explicit reward penalty) on top of a drawdown penalty at lambda=0.5 — comparative evidence from published punitive rewards that did NOT produce abstention indicates the fee double-count, not the drawdown penalty, is the likely distinguishing mechanism; no ablation separates them, no threshold in lambda is established, and generalisation to other reward designs is untested. The evaluation further shows that the regime classifier's DANGER warnings are false 38–47% of the time, forgoing upside that buy-and-hold captures. The dissertation's contribution is the negative result, the protocol that establishes it, and the causal diagnosis of why learned routing degenerates to abstention; it does not claim a validated trading edge.

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
  - [3.6 Evaluation Failure Modes and Their Effect on Inference](#36-evaluation-failure-modes-and-their-effect-on-inference)
- [Chapter 4: Empirical Results & Hypothesis Testing](#chapter-4-empirical-results--hypothesis-testing)
  - [4.1 The Learned Policy Is Abstention](#41-the-learned-policy-is-abstention)
  - [4.2 Master Performance Benchmark Table (Corrected Protocol)](#42-master-performance-benchmark-table)
  - [4.3 Statistical Hypothesis Testing and Power](#43-statistical-hypothesis-testing)
  - [4.4 Protocol Corrections](#44-protocol-corrections)
  - [4.5 Regime-Signal Economics](#45-regime-signal-economics)
- [Chapter 5: Conclusion & Future Research](#chapter-5-conclusion--future-research)
  - [5.1 Summary of Findings](#51-summary-of-findings)
  - [5.2 Engineering & Scientific Contributions](#52-engineering--scientific-contributions)
  - [5.3 Limitations](#53-limitations)
  - [5.4 Future Work](#54-future-work)
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
This dissertation constructs a production-grade multi-engine trading framework as the experimental apparatus to evaluate supervised regime routing against Deep Reinforcement Learning (PPO) under simulated market frictions (fees and slippage in the bar-level replay environment).

## 1.4 Research Questions & Hypotheses
### Research Question
*Can reinforcement learning agents (PPO) learn a regime-switched multi-engine routing policy that outperforms a calibrated supervised routing baseline on risk-adjusted return and maximum drawdown within a shared trading environment?*

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

*Verified against the implementation (`src/rl/env.py:349-356`;
`bench_return` computed at `env.py:352`) — the manuscript's formulation
matches the code. The earlier draft's generic form (with a shaping
bonus) was not implemented.*

**Fee double-count (disclosed).** `src/rl/env.py:333-334` subtracts
fees from equity; `:355-357` subtracts a fee term from the reward
again. The code's own comment (`:346-347`) calls this amplification
("a deliberate anti-churn knob"). The reward under study therefore
penalises trading costs twice AND penalises drawdown at λ=0.5 — it is
unusually punitive, and the withdrawal finding is scoped to it.

**The first term is relative to buy-and-hold, and this matters to the
diagnosis.** When the agent is flat, its equity return is zero and the
reward for that bar is exactly $-R^{bh}_t$: in a rising bar, abstaining
is *punished* by precisely the passive gain foregone. The reward
therefore contains an explicit anti-withdrawal incentive. Chapter 4.1
shows the agent withdrew anyway — the drawdown penalty and the
doubly-counted fee drag outweighed an incentive specifically designed
to prevent withdrawal. This is why the withdrawal cannot be attributed
to a missing incentive to participate: the incentive was present, and
the agent learned to accept its loss.

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
asset — with the caveat of Section 4.5 that accuracy did not translate into
gating value.)*

## 3.5 Asymmetric & Geometric Grid Spacing
$$\text{Spacing}_n = \text{Base Spacing} \cdot (1 + \alpha)^n, \quad \alpha = 0.10$$
$$\text{Size}_n = \text{Base Size} \cdot (1 + \beta)^n, \quad \beta = 0.08$$

## 3.6 Evaluation failure modes and their effect on inference

The evaluation protocol used in this dissertation was itself the object of
three corrective audit batches. This section documents the eight failure
modes found, the mechanism by which each biases inference, the before/after
figures, and whether the correction changed a conclusion or only a number.
Four of the eight **flipped a conclusion**; these are marked. The full
audit trail is FIX_REPORT.md (batches 1-3); every figure below is copied
from it.

**Summary table** (detail in items 1-8 below; "flipped" = the correction
reversed a stated conclusion, not merely a number). SELF = found by this
project's own corrective batches; **IND. REVIEW** = found by the
independent review that motivated Batch 7 — the distinction is itself
evidence for this dissertation's argument about the limits of
self-checking.

| # | Defect | Effect on the number | Flipped? | Caught by | Detail |
|---|---|---|---|---|---|
| 1 | Boundary warmup (env default 50 inside 100-bar prefix) | ~49 train-period bars entered "OOS" series; comparators misaligned | no | SELF (B1) | item 1 |
| 2 | Zero embargo | test features computable from train bars | no | SELF (B1) | item 2 |
| 3 | Non-fold-pure RF baseline | early folds scored by later-trained model | no | SELF (B1) | item 3 |
| 4 | Mislabelled "DM" statistic | reported stat untraceable to code | claim withdrawn | SELF (B1) | item 4 |
| 5 | Additive MaxDD equity | overstated drawdown (TA 0.353→0.511) | **YES** | SELF (B2) | item 5 |
| 6 | Invested-Sharpe annualisation | overstated ratio by sqrt(1/f) | no (artifact) | SELF (B2) | item 6 |
| 7 | Conflated exposure definitions | baselines matched on different quantities | **YES** (ETH verdict reversed) | SELF (B2) | item 7 |
| 8 | Overlapping-window cost sum | 2,243pp impossible vs +38% window | **YES** (magnitude) | SELF (B3) | item 8 |
| 9 | Single-seed reliance | 27pp within-fold swing hidden | **YES** | SELF (B2) | §4.3.2 |
| 10 | Arithmetic (uncompounded) total return | BNB B&H sign flip +3.78→−4.27% | **YES** | **IND. REVIEW** (B7) | item 5n / §3.6.1 |
| 11 | Pooling across 1,440-bar fold gaps | MaxDD inflated up to 5.5x; HAC/bootstrap joins | **YES** | **IND. REVIEW** (B7) | §3.6.1 |
| 12 | Uncapped grid "ceiling" denominator | capacity diagnostic meaningless | strengthens finding | **IND. REVIEW** (B7) | §4.1 |
| 13 | Fee double-count in reward | reward more punitive than designed | scopes finding | **IND. REVIEW** (B7) | §3.1 |
| 14 | Length-only "Politis-White" block choice | interval widths conditional on heuristic | label fixed | **IND. REVIEW** (B7) | §5.3-11 |

Six of fourteen were found only by independent review — including the
two that invalidated headline numbers after five self-directed passes.

1. **Evaluation-boundary defect.** *Defect:* the environment's default
   warmup (50 bars) was applied inside a 100-bar warmup-prefixed test
   frame, so PPO/RF return series began 49 bars before the declared
   test boundary while the passive comparator began 100 bars after the
   frame start. *Mechanism:* train-period bars entered the pooled "OOS"
   series, and comparators were aligned by array position, not
   timestamp — pairing bars that occurred up to ~49 hours apart.
   *Figures:* 769 rows/slice from boundary-49 bars -> 719 rows/slice
   from the exact first test bar (all 12 slices, both assets).
   *Effect:* numbers only (the corrected protocol reinforced the null;
   BNB's statistic flipped sign but remained non-significant).

2. **Zero train/test embargo.** *Defect:* default embargo was 0 bars.
   *Mechanism:* test-window features (up to 50-bar rolling windows plus
   exponentially-weighted RSI tails) could be computed from bars inside
   the training window. *Figures:* embargo now 70 bars
   (DEFAULT_EMBARGO_BARS); slice count unchanged (6/asset).
   *Effect:* numbers only.

3. **Non-fold-pure baseline.** *Defect:* one long-window RF artifact
   scored every fold. *Mechanism:* early test windows were evaluated
   against a model trained on data including later periods.
   *Figures:* replaced by 12 fold-specific models with immutable
   provenance manifests (e.g. ETH fold 0: 2024-07-15 to 2025-01-10,
   3,610 rows, classes 1592/877/1141). *Effect:* numbers only.

4. **Mislabelled test statistic.** *Defect:* the manuscript reported a
   squared-error "Diebold-Mariano" result (DM=1.48, p=0.14) that
   corresponded to no implemented test. *Mechanism:* the reported
   statistic was untraceable to code. *Figures:* removed entirely;
   the implemented statistic (paired mean-difference, Newey-West HAC,
   lag 9 by the standard rule) is now named correctly.
   *Effect:* conclusion-form only (the claim it supported was
   withdrawn).

5. **Additive-drawdown MaxDD.** *Defect:* one MaxDD implementation used
   an additive equity curve (1+cumsum), another multiplicative
   (cumprod), producing two different numbers for the same series.
   *Mechanism:* additive cumsum overstates drawdown for large moves.
   *Figures:* ETH PPO 0.172 -> 0.165; BNB PPO 0.326 -> 0.293;
   **ETH TA (buy-and-hold) 0.353 -> 0.511**; BNB RF 0.532 -> 0.426.
   *Effect:* **CONCLUSION FLIP** on the TA/buy-and-hold drawdown
   magnitude (0.353 to 0.511 changes the reading of B&H risk in the
   benchmark table); canonical definition now multiplicative,
   pooled-by-concatenation.

6. **Invested-bars Sharpe annualisation.** *Defect:* the invested-bars
   Sharpe applied the full-year factor sqrt(8760) to a subset of bars.
   *Mechanism:* overstates the ratio by sqrt(1/f) (2.6x at f=0.15).
   *Figures:* ETH PPO -4.47 -> -1.75; BNB PPO -7.81 -> -3.05;
   corrected values equal the all-bars Sharpe exactly, as required for
   zero-flat-bar series. *Effect:* numbers only (the "collapse" was an
   artifact).

7. **Conflated exposure definitions.** *Defect:* three quantities were
   used interchangeably — time-in-market (engine != flat), non-zero
   return bars, and capital-weighted exposure (mean |position|/equity)
   — and the exposure-matched baselines were matched on different
   definitions. *Mechanism:* grid bars with zero inventory count as
   "deployed" in time-in-market but hold no capital, so a "58%
   exposure" policy can be a 4.3%-exposure policy. *Figures:* ETH PPO
   time-in-market 58.1% vs capital-weighted 4.3%; baselines re-matched
   on capital: random-entry median MaxDD 0.0509 (ETH) / 0.0508 (BNB) vs
   PPO's 0.165 / 0.293. *Effect:* **CONCLUSION FLIP** — the ETH
   "drawdown advantage survives exposure matching" verdict reversed to
   PPO at the 100th percentile (worst) on both assets.

8. **Overlapping-window opportunity cost.** *Defect:* the false-DANGER
   cost summed 24-bar forward returns across hourly signal bars, so
   each price move was counted in up to 24 windows. *Mechanism:*
   multiply-counting inflates a sum without bound; 2,243pp of
   "foregone return" on a window where B&H returned +44.8% is
   impossible as a portfolio cost. *Figures:* ~~2,242.9pp (ETH) /
   1,183.2pp (BNB)~~ retracted; replaced by per-signal means (+3.34% /
   +3.88%), non-overlapping 24-bar block gaps (0.96pp / 0.72pp), and
   the portfolio-level gated-minus-B&H gaps (-67.1pp / -20.8pp).
   *Effect:* **CONCLUSION FLIP** on the magnitude (the direction of the
   argument survives; the number did not).

A ninth finding — single-seed reliance — is documented in 4.3.2 rather
than here because it was not present in any pre-audit report; it is a
new result of the corrected protocol, not a correction of one. It also
flipped a conclusion: the paired statistic crosses zero across seeds
(BNB: -0.92 to +0.80), voiding any single-seed method claim.

### 3.6.1 The pooling artifact: a five-fold headline inflation

The single most consequential definitional error in this project, and
the clearest demonstration that one definition can move a headline
figure by a factor of five.

**Defect.** The six walk-forward test windows are separated by
1,440-bar (~60-day) gaps (step_bars 2,160, test_bars 720). Reporting
"pooled" statistics treated the six windows as one continuous series:
drawdowns were measured across joins that do not exist in time, HAC
lags 1-9 coupled bars months apart, and bootstrap blocks spanned the
gaps.

**Mechanism.** Concatenating equity paths creates artificial adjacency
at five fold boundaries. Any partial drawdown at the end of one fold
and any partial drawdown at the start of the next combine into a
single "drawdown" that no investor would have experienced, inflating
MaxDD; the same artificial joins corrupt serial-dependence estimates.

**Before/after (per-fold median replaces pooled-by-concatenation):**

| | pooled (old) | per-fold median (new) | inflation factor |
| :--- | :---: | :---: | :---: |
| ETH PPO | 0.165 | 0.032 | 5.2x |
| ETH RF | 0.247 | 0.107 | 2.3x |
| ETH B&H | 0.511 | 0.195 | 2.6x |
| BNB PPO | 0.293 | 0.053 | 5.5x |
| BNB RF | 0.426 | 0.117 | 3.6x |
| BNB B&H | 0.503 | 0.198 | 2.5x |

**The claim that survived four corrective passes.** The Batch-2
correction record itself asserted the folds were "contiguous
chronological segments" and that concatenation "reconstructs one
continuous equity path" — an incorrect claim that survived four
self-directed corrective batches and was caught only by independent
review (Batch 7). Self-audit corrected the additive-vs-multiplicative
MaxDD construction directly adjacent to this error without noticing
the pooling assumption underneath it.

**This is evidence for the dissertation's own argument about the
limits of self-audit.** Each corrective pass was performed by the same
process that produced the error; the shared false premise (contiguity)
was invisible to every pass because it was a premise, not a
computation. Independent review — a reader without stake in the
correction narrative — caught it in one pass.

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

## 4.1 The learned policy is abstention

**The decisive result: a permanently-flat policy outscores the trained
policy in 19 of 20 seed-fold-pair cells** (5 seeds x folds 0 and 3 x
both assets; reports/flat_vs_trained_by_seed.json). The one exception
is BNB fold 3, seed 999 — trained +0.0944 vs flat +0.0801, a margin of
+0.0143 — and seed 999 is the seed that produced the best BNB return
in the sweep (+3.95%, Section 4.3.2). In other words: the training
exceeds abstention only at its best draw, and then only marginally.
Across the other 19 cells, doing nothing scores higher than the learned
policy — the reward function does not distinguish the trained policy
from abstention, and the optimal response to it is near-abstention. The
routing question the experiment intended to ask is not the one the
reward answers. **Scope: this is a finding about a policy trained
under THIS reward — which double-counts transaction costs (Section 3.1)
and penalises drawdown at λ=0.5 — not a claim that reinforcement
learning generally learns abstention in trading.**

*Illustrative example (seed 42 only, six folds pooled — a different and
broader fold scope than the 19-of-20 seed sweep above, and a single
draw from the seed distribution): flat scores -0.448 (ETH) / -0.038
(BNB) versus the trained agent's -0.994 / -0.854 over identical
timestamps.* PPO's 4.3%/5.2% capital-weighted exposure (vs 21.1%/60.5%
for the supervised baseline) is therefore **learned withdrawal, not a
configuration ceiling**, established by four diagnostics:

1. **Action distribution.** The trained policy selects FLAT on
   41.9%/45.8% of steps versus **0.2%/0.0% for a randomly-initialised
   policy** - abstention was learned, not initialised.
2. **~~Non-binding size ceilings~~ (RETRACTED, Batch 7 task 5).**
   ~~Available maximum is 67-100% of equity (`max_position_pct=0.667`);
   when active, the agent uses 73-83% of that ceiling, with 41-43% of
   active steps at >=90% of max.~~ The diagnostic's denominator
   (5 x grid_level_pct x size_mult x equity) is an ACCOUNTING
   CONVENTION, not an enforceable ceiling: `src/rl/env.py:475-484`
   adds inventory on every bar whose range crosses a buy level, with
   no cumulative cap anywhere in the engine, so grid inventory is
   effectively unbounded (trend/swing remain capped by
   `max_position_pct=0.667` at `env.py:79`). **Direction of the
   correction: the agent had MORE deployable capacity than the
   diagnostic assumed and used LESS of it — this STRENGTHENS the
   withdrawal conclusion.**
3. **Reward decomposition** (lambda = 0.5, `EnvConfig.lambda_dd`): the
   drawdown-penalty term contributes 0.310/0.401 to total reward
   against a PnL term of -0.579/-0.328 - the penalty rivals the entire
   PnL term.
4. **Structural amplifier.** Capital-weighted exposure (4-5%) is far
   below time-in-market (54-58%) because the grid engine is counted
   "deployed" on bars where it holds zero inventory - the learned
   withdrawal is compounded by an accounting asymmetry between the two
   exposure definitions (Section 3.6, item 7).

Note the role of the reward's first term here (Section 3.1): it is
*relative to buy-and-hold*, so a flat bar in a rising market is
penalised by exactly the foregone passive gain — the withdrawal
happened **despite** an incentive specifically designed to prevent it.
The diagnosis is therefore difficult to attribute to a missing
participation incentive; the drawdown penalty simply dominated it.

## 4.2 Master Performance Benchmark Table

**Corrected protocol** (pooled over 6 folds per asset; n = 4,314 hourly bars each):

| ETH-USDT | Total Return | Sharpe (ann.) | Max Drawdown | Profit Factor | Trades | Capital Exposure |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Buy & Hold | **+38.0%** | - | 0.195 (median) | 1.04 | - | 100% |
| Supervised RF-gated | -21.2% | -1.86 | 0.107 (median) | 0.90 | 136 | 21.1% |
| PPO (seed 42) | -12.8% | -1.74 | 0.032 (median) | 0.79 | 46 | 4.3% |

| BNB-USDT | Total Return | Sharpe (ann.) | Max Drawdown | Profit Factor | Trades | Capital Exposure |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Buy & Hold | **-4.3%** | - | 0.198 (median) | 1.00 | - | 100% |
| Supervised RF-gated | -20.3% | -0.72 | 0.117 (median) | 0.95 | 129 | 60.5% |
| PPO (seed 42) | -25.9% | -3.04 | 0.053 (median) | 0.65 | 45 | 5.2% |

*Capital exposure is the capital-weighted definition (mean
|position|/equity), not time-in-market; the two policies operated at
materially different deployment (RF/PPO ratio 4.9x on ETH, 11.5x on
BNB), so all PPO-vs-RF comparisons in this chapter are descriptive.*

*Seed note: both PPO rows are the pre-registered default seed 42 (set
in code before any result was observed — trainer CLI default
`--seed=42`, `src/rl/agents/ppo_trainer.py:116-119`; evaluation reset
`env.reset(seed=42)`, `src/rl/evaluate.py:266`). On BNB, seed 42 sits
at the unfavourable end of the seed distribution (folds-0+3 pooled
MaxDD 0.2625 vs 5-seed mean 0.102, Section 4.3.2): the BNB PPO row is
therefore **conditional on seed 42's position in the distribution** and,
if anything, pessimistic — though not enough to change any conclusion.*

**Reading of the table:** no routing policy beat passive exposure on
either asset. The lower drawdowns of the routed policies are an exposure
effect, not a timing effect: capital-matched random-entry baselines at
PPO's own 4-5% deployment produced median MaxDD of 5.1% on both assets
(ETH 0.0509 / BNB 0.0508, reports/exposure_match_*.json), versus PPO's
16.5-29.3% - PPO sits at the 100th percentile (worst) of the
exposure-matched random distribution on both assets.

The previously reported table (Sharpe 1.85, MaxDD -0.4%, from the
single-window June-July 2026 benchmark under the contaminated protocol)
is withdrawn; see Section 3.6.

## 4.3 Statistical Hypothesis Testing

The implemented statistic is a **paired mean-difference test on realised
per-bar returns with Newey-West HAC standard errors** (lag by the standard
rule floor(4(n/100)^(2/9)) = 9; Holm-corrected across assets). It is not
a Diebold-Mariano test (DM is defined on forecast-loss differentials); an
earlier draft mislabelled it as such.

| PPO vs fold-specific RF | stat | p | Holm-p | n |
| :--- | :---: | :---: | :---: | :---: |
| ETH | +0.43 | 0.665 | 1.00 | 4,314 |
| BNB | -0.33 | 0.743 | 1.00 | 4,314 |

~~Stationary-bootstrap 95% CIs for the MaxDD difference (PPO - RF): ETH
[-0.342, +0.090]; BNB [-0.381, +0.161] - both straddle zero.~~
*(Superseded by fold-clustered bootstrapping, Batch 7 — blocks no
longer span the 1,440-bar fold gaps.)*

**Fold-clustered bootstrap on the per-fold MaxDD difference (PPO − RF)
— the project's first statistically significant result:**

| | ETH | BNB |
| :--- | :---: | :---: |
| estimate (mean per-fold MaxDD diff) | −0.070 | −0.054 |
| 95% CI (fold-cluster resampling) | **[−0.121, −0.020]** | [−0.138, +0.022] |
| excludes zero? | **YES** | no |

PPO's per-fold drawdowns are consistently below the RF baseline's on
ETH, and not distinguishable on BNB. **This result must be read with
its mechanism: it is consistent with PPO's far lower capital
deployment (4.3% vs 21.1% capital-weighted exposure, a 4.9x ratio),
not with timing skill — if it were skill, PPO would not sit at the
100th percentile (worst) against random entries matched on the same
capital exposure (Section 4.2), which it does on both pairs.** The
finding is "a lower-exposure policy drew down less per fold", not "PPO
reduces drawdown".

**The ETH/BNB divergence is itself an illustration of this thesis's
argument about scale.** With two assets and an effective n of six
folds, a result significant on one pair and not the other does not
support a general conclusion — it demonstrates sensitivity to asset
selection, which single-asset studies conceal. Both pairs are
therefore always reported together here.

### 4.3.1 Statistical power: the null is uninterpretable, not confirmed

The test is **underpowered by an order of magnitude**. Using the same HAC
variance estimator:

| | ETH | BNB |
| :--- | :---: | :---: |
| Minimum detectable effect (80% power, alpha=0.05, compounded) | **81.7pp** | **179.3pp** |

~~| Achieved power at observed difference | 7.2% | 6.2% |~~
~~| n required to detect the observed difference | 180,943 bars (20.7y) | 314,103 bars (35.9y) |~~

*Both retracted rows were RETROSPECTIVE quantities computed at the
observed effect size. Observed power is a one-to-one function of the
p-value (Hoenig & Heisey 2001) and adds nothing beyond it; n-required
anchored on the observed difference inherits the same circularity
(the observed difference is itself estimated with an SE this wide).
Retained: the MDE, a prospective design quantity computed from the
HAC variance and sample size only — verified in code
(`scripts/mde_power_report.py:35-41` uses no observed-effect term).*

**The defensible underpowering evidence is the width of the reported
confidence intervals** (Hoenig & Heisey's recommended framing): the
fold-clustered MaxDD-difference CI spans [−0.121, −0.020] on ETH and
[−0.138, +0.022] on BNB — widths of ~0.10 and ~0.16 — where the
effects under discussion are of order 0.05-0.13. A sample whose
interval is wider than the effect cannot resolve that effect; no
retrospective power number is needed to say so, and none could
strengthen it.

**Literature-anchored n-required** (replacing the retracted
observed-anchored figures; effect sizes from published positive
results, not from this study):

| anchor (source) | ETH n-required | BNB n-required |
| :--- | :---: | :---: |
| 1.7pp/yr (arXiv:2510.06466, S&P RL) | 2,506 years | 7,413 years |
| 10pp/yr (crypto-RL order-of-magnitude; stated assumption) | 72 years | 214 years |

Even at effect sizes far larger than anything this study observed, the
design needs decades-to-millennia of hourly data. **"Parity" is
therefore not a supported claim.** The correct statement is: *at the
realised sample size this design cannot distinguish parity from
differences far larger than the one observed.* CH3's
"non-significance is a valid finding" holds only in this weaker,
power-qualified form.

### 4.3.1a Design analysis: Type S and Type M error rates

Following Gelman & Carlin (2014), error rates at the literature-anchored
effect sizes and this study's realised SE
(`reports/design_analysis.json`):

| anchor | pair | power at n=4,314 | Type S (wrong sign \| sig) | Type M (exaggeration \| sig) |
| :--- | :--- | :---: | :---: | :---: |
| 1.7pp/yr | ETH | 0.73 | ~0.000 | 1.17x |
| 1.7pp/yr | BNB | 0.32 | 0.0008 | 1.74x |
| 10pp/yr | ETH | ~1.0 | ~0 | ~1.0x |
| 10pp/yr | BNB | ~1.0 | ~0 | ~1.0x |

Implication stated plainly: at the S&P-anchored effect, a study at
this sample size that DID reach significance on BNB would exaggerate
the effect by ~1.7x on average, and sign errors are rare but nonzero.
**This STRENGTHENS the underpowering argument**: the design is not
merely insensitive — when it does fire, its significant estimates are
inflated. At the (generous) crypto anchor the design is adequate,
which locates the boundary: this methodology can resolve very large
claimed effects, not the ones typically reported.

### 4.3.2 Seed variance dominates the method difference

Five PPO seeds (folds 0 and 3, both assets) swing cumulative return by up
to **27pp within a single fold** (BNB fold 3: -22.99% for seed 7 vs
+3.95% for seed 999) and MaxDD by 45x (0.007 vs 0.312); the paired
statistic crosses zero across seeds on BNB. The single-seed PPO-vs-RF
comparison above is one draw from this distribution and supports no
method-level claim in either direction. *(Scope: the seed sweep covers
folds 0 and 3 only; its figures are not comparable to the six-fold pooled
table in 4.2.)*

**The main run's seed sits at the unfavourable end.** Seed 42 — the
pre-registered default, fixed in code before any result was observed
(trainer CLI default `--seed=42`, `src/rl/agents/ppo_trainer.py:116-119`;
`env.reset(seed=42)`, `src/rl/evaluate.py:266`) — has a BNB folds-0+3
pooled MaxDD of 0.2625 against the 5-seed mean of 0.102 (2.6x worse).
Every headline BNB PPO figure in this dissertation is therefore
conditional on seed 42's unfavourable position in the distribution: the
BNB numbers are, if anything, pessimistic about PPO. No conclusion
changes — the negative result holds across all seeds but one cell (the
BNB fold-3 seed-999 exception in the flat-policy comparison, B5.1) —
but no BNB-specific magnitude should be read as seed-typical.

**Direction of bias.** Across the seed sweep, seed 42 sits at or near
the unfavourable end in three of its four cells and is the
worst-performing draw in BNB fold 0. The headline figures are therefore
pessimistic toward the reinforcement-learning agent rather than
flattering to it — the bias, where it exists, runs against the method
this dissertation might have been tempted to favour.

## 4.4 Protocol corrections

The eight evaluation failure modes underlying this chapter — their
mechanisms, before/after figures, and which of them flipped a
conclusion — are documented in Section 3.6 as part of the
methodological contribution.

## 4.5 Regime-signal economics

The classifier's out-of-sample accuracy (0.80-0.87) does not convert into
gating value. On DANGER-predicted bars the market *rose* 47% (ETH) / 38%
(BNB) of the time (mean forward 24-bar return on DANGER bars: -0.79% /
-0.68% - an edge far too small to pay for skipping the false positives),
and 36.9%/17.9% of non-DANGER bars realised forward drawdowns <= -3%.
These two figures — the negative overall DANGER mean and the strongly
positive mean on the *false* subset (Section 3.6, item 8: +3.34% /
+3.88% mean forward return per false-DANGER signal) — are consistent,
not contradictory: the overall mean is negative because the true
signals pull it down, but it conceals sharp dispersion. That asymmetry
— a small edge when right, a large foregone gain when wrong — is the
mechanism by which high classification accuracy fails to convert into
economic value. Non-overlapping 24-bar blocks entered on DANGER
returned -0.57%/-0.49% versus +0.39%/+0.23% for non-flagged blocks. At
portfolio level the gated strategy underperformed buy-and-hold by
67.1pp (ETH) and 20.8pp (BNB) over the same window - the honest measure
of the gate's cost.

---

## 4.6 Relation to positive-result literature

A substantial published literature reports reinforcement-learning
trading policies outperforming passive exposure; this dissertation's
negative result must be read against it, not instead of it.

- **arXiv:2510.06466** (attention-enhanced RL): PPO terminal wealth
  2.11 vs buy-and-hold 1.94, Sharpe 0.73 vs 0.66 (S&P 500, 2020-2025)
  — a positive result with an effect of roughly 1.7pp CAGR, which this
  dissertation uses as its S&P literature anchor (4.3.1).
- **Felizardo et al.** (Expert Systems with Applications): reports
  outperforming buy-and-hold in all tests on crypto assets.
- **arXiv:2310.09462** (causal RL): CRN-PPO reports positive ROI on
  ETH where buy-and-hold was negative — a direct inversion of this
  study's ETH finding, and a useful reminder that the disagreement is
  between systems, not between datasets alone.
- **arXiv:2209.05559**: PPO trained with combinatorial purged
  cross-validation (CPCV) outperforms walk-forward and K-fold agents
  and the S&P DBM index — notably, from the same evaluation-methodology
  tradition (citing Agarwal et al. 2021) this dissertation relies on,
  reaching a positive conclusion.
- **10.1007/s00521-023-08516-x**: five PPO variants failed to beat
  buy-and-hold — but with NO transaction costs modelled, a different
  failure mechanism from this study's (where costs are, if anything,
  double-counted).

**This dissertation does NOT claim that reinforcement learning
generally fails at trading.** The claim is a documented failure mode in
one system under one reward specification, with a causal diagnosis and
a traceable evidence chain. The papers above differ from this study in
reward design, asset universe, cost modelling, and validation scheme;
any of those differences could be decisive, and which ones are is an
open empirical question this single case cannot settle.

**On CPCV specifically:** arXiv:2209.05559 argues combinatorial purged
cross-validation yields more test paths and a higher effective n than
walk-forward. Walk-forward was used here because it preserves temporal
contiguity within each test window and does not require assuming that
non-adjacent market segments are exchangeable — an assumption this
dissertation regards as unsafe in a non-stationary environment (and
which the fold-gap pooling defect of 3.6.1 shows to be consequential
even implicitly). CPCV is listed as future work (5.4).

*Publication bias is acknowledged as a field-level observation with
its own literature, but it is not invoked here as a defence of this
study's result: it is unfalsifiable in this context and would be
evasive. The disagreement with the positive literature is engaged on
the differences above, not explained away.*

## 4.7 The withdrawal claim, narrowed by the same literature

The positive-result literature also constrains the withdrawal
diagnosis. arXiv:2510.06466 and the Bitcoin-hourly PPO/SAC/TD3/A2C
studies report reward functions combining transaction costs, drawdown
penalties, volatility penalties, and delayed rewards — specifications
at least as punitive as this study's — WITHOUT producing abstention.

**A drawdown penalty alone therefore does not cause learned
withdrawal.** The distinguishing feature of this system is most likely
the DOUBLE-COUNTED TRANSACTION COST: `env.py:333-334` subtracts fees
from equity and `:354-357` subtracts a fee term from the reward again
(the code comment calls this amplification). The revised claim:

> Under a reward specification in which transaction costs are counted
> twice — embedded in the equity return and subtracted again as an
> explicit term — on top of a drawdown penalty at λ=0.5, PPO converged
> to abstention; a permanently-flat policy outscores the trained
> policy in 19 of 20 seed-fold-pair cells.

The λ=0.5 penalty is part of the specification under which the
withdrawal occurred, but the comparative evidence indicates it is not
the sufficient cause; the fee double-count is the candidate
distinguishing mechanism. No ablation separating the two was run
(frozen code), so the attribution is by comparison across published
specifications, not by experiment — stated as such.

---

# Chapter 5: Conclusion & Future Research

## 5.1 Summary of Findings

Under the corrected evaluation protocol, this dissertation's empirical
answer to its research question is a **well-evidenced negative result**:

1. **No routing edge.** Neither the supervised regime-gated policy nor the
   PPO agent outperformed passive buy-and-hold on either asset (on BNB,
   passive exposure itself was negative)
   (ETH: B&H +38.0% vs gated −21.2% vs PPO −12.8%; BNB: B&H −4.3% vs
   gated −20.3% vs PPO −25.9% — on BNB passive exposure also lost, but
   lost far less). **CH1 and CH2 are rejected**; the
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
   PnL term, and a permanently-flat policy outscores the trained policy
   in 19 of 20 seed-fold-pair cells (the exception — BNB fold 3, seed
   999, by +0.0143 — is the sweep's best-performing seed, and then only
   marginally). The experiment measured a reward specification, not a
   routing strategy.
4. **Regime classification accuracy does not imply gating value.** At
   0.80–0.87 OOS accuracy, DANGER warnings are false 38–47% of the time,
   and the mean forward return on DANGER bars (−0.79% ETH / −0.68% BNB,
   per Section 4.5's source) is too small to pay for the foregone
   upside — the gate cost 67.1pp (ETH) and 20.8pp (BNB) against
   buy-and-hold over the same window.

## 5.2 Engineering & Scientific Contributions

**Positioning: a disconfirming case.** The single-system design will be
challenged on generalisability; the defensible position is stated
explicitly rather than left implicit. Flyvbjerg (2006, *Qualitative
Inquiry* 12(2):219-245) documents the misconception that single cases
cannot contribute to scientific development; Tsang (2014, *IJMR*
16(4):369-383) identifies case studies' advantages in theoretical
generalisation and in identifying disconfirming cases. This
dissertation is the latter: the field's prevailing assumption is that
RL routing adds value (4.6); this documents an instance where it does
not, with a causal explanation and a traceable evidence chain.

Against the standard case-study rigour criteria: systematic method
application (the corrected protocol, 3.6); documented data collection
and analysis (per-bar CSVs, provenance manifests, run manifest); a
chain of evidence with traceable inferences (REPRODUCE.md's
claim-to-evidence map; every headline number re-derivable from
committed raw data in five minutes); and consideration of alternative
explanations (the four withdrawal diagnostics of 4.1 — action
distribution, reward decomposition, untrained-policy comparison,
structural dilution — each tests an alternative: initialisation bias,
missing incentive, capacity limits, accounting artifact).

**Limitation stated honestly:** one system, one reward, two assets, no
multi-case design. A multi-case design would require at least: the
same corrected protocol applied across systems differing in reward
specification (the fee double-count ablation), asset class, and cost
model, with seed distributions at each cell — a programme of work,
not a dissertation chapter.

1. A production multi-engine trading platform deployed on AWS EC2
   (Tokyo), with a live ML regime pipeline (per-pair calibrated
   classifiers, immutable provenance manifests) feeding a Rust trading
   engine.

2. A corrected walk-forward evaluation protocol with the full audit
   trail (per-bar return series, provenance manifests, run manifests,
   seven-batch correction record) committed as evidence. Its six
   transferable diagnostics, each with its cost and what it would have
   caught here:

   a. **The flat-policy floor.** Compare any trained policy against
      *Origin: naive-baseline flooring (RL evaluation practice);*
      permanent abstention under the same reward before concluding it
      learned anything. Cost: one line of computation over the
      evaluation rollouts. Would have caught: the abstention result
      before any performance claim was made.
   b. **Reward-component decomposition.** Report the magnitude of each
      *Origin: component reporting (econometric model diagnostics);*
      reward term, not just the total, so a dominating penalty is
      visible. Cost: instrumenting the reward (already summed per
      step). Would have caught: the drawdown penalty rivaling the
      entire PnL term, and the fee double-count.
   c. **Capital-exposure matching.** Match baselines on
      *Origin: exposure matching (portfolio attribution);*
      capital-weighted exposure, not time-in-market, and report both.
      Cost: one env field (per-bar position notional). Would have
      caught: the exposure-artifact "drawdown advantage" verdicts.
   d. **Fold-aware pooling.** Never concatenate non-adjacent test
      *Origin: purging and embargo (quantitative-finance cross-validation);*
      windows into a single equity curve or dependence structure.
      Cost: tracking fold indices (already present). Would have
      caught: the five-fold MaxDD inflation and the boundary-crossing
      HAC/bootstrap dependence.
   e. **Power reporting.** State the minimum detectable effect
      *Origin: power analysis (clinical trials and applied statistics);*
      alongside any null result, and the effective n after clustering.
      Cost: closed-form from the existing variance estimate. Would
      have caught: every "parity" statement this report once made.
   f. **Seed-distribution reporting.** Never report a single-seed RL
      *Origin: seed-distribution reporting (deep-RL reproducibility literature).*
      result. Cost: the training already amortises; reporting is a
      table. Would have caught: a headline resting on the seed at the
      unfavourable end of its own distribution.

3. A causal diagnosis scoped precisely: a reward of this form —
   drawdown penalty at λ=0.5 on top of double-counted transaction
   costs — makes abstention optimal for THIS specification, detectable
   *before* deployment by the flat-policy floor. It is a finding about
   this reward, not a claim that RL generally learns abstention.

**Scope of contribution 2, stated honestly.** None of the diagnostics
was invented here; each is standard practice in an origin discipline
(noted per diagnostic below). The claim is a documented case study in
which each one's absence changed a conclusion, with before/after
evidence. *This dissertation conducted no systematic survey of the
RL-for-trading literature and therefore makes no claim about how
common these omissions are in the field; the observation is restricted
to this project's own pre-correction reports, in which none of the six
was applied. That restriction is itself consistent with the thesis's
argument — but a field-wide prevalence claim would require a survey
that was not performed.*

## 5.3 Limitations

1. **Asset coverage.** The corrected-protocol evaluation covers ETHUSDT
   and BNBUSDT only. The live regime pipeline also serves XRP-USDT and
   DOGE-USDT, which were never evaluated under the corrected protocol;
   nothing here transfers to them.
2. **Backtest only.** All results are historical-replay backtests. No
   live or paper-trading performance is claimed anywhere in this
   dissertation. Shadow routing produced no result within the thesis
   horizon.
3. **Seed scope.** The seed sweep covers 5 seeds on folds 0 and 3 only
   (20 of 60 possible trainings), not the full six folds; its figures
   are not comparable to the six-fold pooled tables (scope note in
   reports/seed_sensitivity.json). *Direction-of-bias note: the main
   run uses seed 42 throughout, fixed as the code default before any
   result was observed (`src/rl/agents/ppo_trainer.py:116-119`,
   `src/rl/evaluate.py:266`). Across the seed sweep, seed 42 sits at or
   near the unfavourable end of its cells and is the worst-performing
   draw in BNB fold 0. The headline figures are therefore pessimistic
   toward the reinforcement-learning agent rather than flattering to
   it.*
4. **Underpowered by construction.** Detecting the observed differences
   at 80% power would require 21-36 years of hourly data
   (Section 4.3.1). The evaluation design cannot support method-level
   claims at any effect size it plausibly encounters.
5. **Baseline ordering.** The supervised baseline was chosen because it
   represents the prevailing approach and because existing
   infrastructure allowed the decision layer to be isolated as the only
   variable. That choice implicitly assumed both approaches outperform
   simpler alternatives — an assumption the corrected evaluation
   falsified (both were beaten by buy-and-hold and, at matched
   exposure, by random entry). Baselines should be ordered from
   abstention upward — flat policy, capital-matched random entry,
   buy-and-hold, then learned policies — and were not.
6. **Lambda sensitivity untested.** The withdrawal is documented at
   lambda = 0.5 only. No sensitivity sweep over the drawdown-penalty
   weight was run, so no threshold separating withdrawal from
   deployment is established; the reward misspecification diagnosis is
   made at a single point of the reward's parameter space.

6a. **Unusually punitive reward.** The reward double-counts
   transaction costs (`env.py:333-334` in equity; `:355-357` in the
   reward; the code comment calls it amplification) in addition to the
   λ=0.5 drawdown penalty. The withdrawal finding licenses the claim
   that THIS reward specification produces abstention, and that the
   flat-policy comparison detects it; it does NOT license the claim
   that RL generally converges to abstention in trading, nor that
   drawdown penalties per se cause withdrawal — a bounded, singly-
   counted cost structure was never tested.

8. **Training-window asymmetry (PPO month rounding).** PPO training
   windows round to calendar months (`walk_forward.py:312-320`), so
   PPO's fold-0 window is 4,344 bars against the RF's 4,320 — a 24-bar
   (0.6%) asymmetry. Disclosed; the OOS boundary still holds (PPO
   trains through 2025-01-10, test starts 2025-01-13).

9. **Provenance is not single-snapshot.** The run manifest
   (7e5feaf8), PPO sidecars (df1c3bed), and RF manifests (353cb252)
   cite different commits. The diff between them touches
   evaluation/reporting code only (`risk_stats.py`, `walk_forward.py`,
   docs) — no training- or environment-logic changes — so they are
   semantically identical for training and evaluation; single-snapshot
   provenance is nonetheless NOT demonstrated.

10. **The "untrained policy" control.** Renamed from
    "randomly-initialised policy": it is an untrained PPO network
    evaluated deterministically, not a random-action control. It
    isolates learned-vs-initial behaviour of the SAME architecture; it
    does not isolate architecture bias or exploration noise.

11. **Two bootstrap CIs exist in the artifacts.**
    `evaluation_report.py:64-108` computes a generic IID/additive
    bootstrap CI (supports the per-fold summary tables); the
    fold-cluster heuristic-block bootstrap in `risk_stats.py` supports
    the MaxDD/Sortino difference claims. Readers should match each
    claim to its CI; the IID CI supports no method-level claim.

12. **The 70-bar embargo is approximate.** It bounds RSI's EWM tail
    but ADX and MACD are also infinite-support recurrences, and the
    100-bar warmup can reach ~30 bars past the embargo boundary. The
    embargo is a decay tolerance, not a strict no-dependence guarantee.

7. **Inferential unit.** The reported pooled paired test treats the
   per-bar return difference as the unit of observation (n = 4,314),
   while the method-level claim concerns a *training procedure
   evaluated six times*. A fold-clustered analysis would have an
   effective n near 6, so the true power is LOWER than reported, not
   higher. No replacement p-value is computed here: the appropriate
   estimand depends on the intended claim and is not specified. This
   limitation makes the underpowering finding STRONGER, not weaker.

## 5.4 Future Work

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
   in Section 4.2.

---

# References
- Bailey, D.H. and López de Prado, M. (2014) 'The Deflated Sharpe Ratio', *Journal of Portfolio Management*, 40(5), pp. 94–107.
- Diebold, F.X. and Mariano, R.S. (1995) 'Comparing Predictive Accuracy', *Journal of Business & Economic Statistics*, 13(3), pp. 253–263.
- Mnih, V. et al. (2015) 'Human-level control through deep reinforcement learning', *Nature*, 518(7540), pp. 529–533.
- Schulman, J. et al. (2017) 'Proximal Policy Optimization Algorithms', arXiv:1707.06347.
- Zadrozny, B. and Elkan, C. (2002) 'Transforming Classifier Scores into Accurate Multiclass Probability Estimates', *KDD*, pp. 694–699.
