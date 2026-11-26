# Introduction

## Background and Context

Reinforcement learning (RL) has become a common approach in trading research. An RL agent learns a policy by trial and error inside a simulated market, guided by a reward function, and published studies frequently report agents that achieve higher risk-adjusted returns or smaller drawdowns than a baseline (Xue and Ye, 2025; Amirzadeh et al., 2023; Kochliaridis et al., 2023).

Such results are judged on outcome-level metrics: total return, maximum drawdown, Sharpe ratio and time in market. The implicit assumption is that these metrics reflect the agent's decisions, so a smaller drawdown reads as better risk management.

This dissertation tests that assumption in a historical-replay environment derived from a deployed cryptocurrency trading system (Section 3.1). A Proximal Policy Optimisation (PPO) agent (Schulman et al., 2017) learns to route capital between execution primitives and is compared with a calibrated Random-Forest baseline under a corrected walk-forward protocol.

## Problem Statement

The problem is that standard metrics can make an agent that does very little look like an agent that manages risk well.

In this study, the PPO agent recorded a median per-fold maximum drawdown 55–70% lower than its supervised baseline (0.032 against 0.107 on ETH; 0.053 against 0.117 on BNB), while its reported time in market was 58% (ETH) and 54% (BNB). By standard criteria this reads as better risk management. Per-bar traces tell a different story: the agent deployed 4.3% of capital, against 49.2% for an untrained network in the identical environment, and a permanently abstaining policy outscored the trained agent under its own reward function in 19 of 20 seed-fold-pair cells.

Part of this abstention is visible, because the agent often chooses the no-op action (20.5 percentage points of the exposure gap on ETH). The rest is concealed: the agent selects nominally active actions that deploy little capital (24.3 points), mainly a grid primitive that acquires a position on only 10.2% of the bars where it is selected. Neither the action distribution nor time in market reveals this, and capital-weighted exposure is not a standard reported quantity. The failure is therefore invisible to the usual evaluation.

In practice, a team relying on these metrics could deploy such an agent believing it controls risk, when it has learned to stay out of the market.

## Aims and Objectives

The aim of this dissertation is to establish whether a PPO routing policy adds value over a supervised routing baseline in an environment derived from a deployed trading system, and whether standard evaluation can tell the difference.

The objectives are:

1. To build and deploy a multi-engine trading system that serves as the experimental apparatus (Section 3.1).
2. To train a PPO routing agent and a calibrated supervised baseline on the same data and environment (Sections 3.2 and 3.3).
3. To evaluate both under a corrected walk-forward protocol with appropriate statistical treatment and power analysis (Sections 3.4 and 3.5).
4. To diagnose what the agent actually learned, using exposure, action and reward diagnostics (Section 3.6 and Chapter 4).
5. To document how each defect in the evaluation was discovered, and what this implies for evaluation practice (Sections 3.7 and 3.8).

The simulation applies a 0.1%-per-side transaction fee. Slippage and market impact were not modelled; this is discussed as a limitation in Section 5.4.

## Research Question and Hypotheses

The research question is:

> Can reinforcement learning agents (PPO) learn a regime-switched multi-engine routing policy that outperforms a calibrated supervised routing baseline on risk-adjusted return and maximum drawdown within a shared trading environment?

The answer, developed in Chapter 4, is no. Neither policy beat passive buy-and-hold, the comparison is underpowered by an order of magnitude, seed variance dominates the method differences, and the learned policy is abstention under a drawdown-penalising reward.

The hypotheses and their outcomes are:

- SH1: The regime-gated hybrid framework exhibits lower maximum drawdown than standalone grid or trend strategies. Not tested: no standalone grid or trend comparison was run. (The drawdown differences that were measured, between PPO and the baseline, are explained by capital exposure; Section 4.1.)
- SH2: The BTC cross-asset risk gate reduces systemic exposure during DANGER states. Not tested: this hypothesis was scoped out of the final evaluation, which concentrates on the routing comparison.
- SH3: Confidence-weighted sizing improves risk-adjusted return compared with fixed sizing. Not tested: scoped out of the final evaluation for the same reason.
- CH1: At least one RL agent achieves a statistically higher Sharpe ratio than the supervised baseline on walk-forward out-of-sample data. Not supported: the paired return test was not significant (p = 0.67 on ETH, 0.74 on BNB), no Sharpe-specific test was run, and the design is underpowered (Section 4.3).
- CH2: RL agents exhibit lower maximum drawdown than the supervised baseline during regime transitions. Not tested as specified: no analysis conditioned on regime transitions was run. Over the whole test period PPO drew down less than the baseline (significantly on ETH, not on BNB), which is consistent with its much lower exposure rather than with skill (Sections 4.1 and 4.3).
- CH3: A non-significant result across all RL-versus-baseline comparisons is a valid finding that documents where supervised routing suffices. Upheld only in a power-qualified form: the design cannot distinguish parity from differences an order of magnitude larger than those observed.

## Industrial Relevance

Trading firms, crypto exchanges and asset managers increasingly test machine-learning agents before trusting them with money. Most of those tests rely on the same few numbers: return, drawdown, Sharpe ratio and time in market. If an agent can look safe on those numbers while hardly trading at all, a firm could approve a system that does not do what it appears to do. It could also reject a good system for the wrong reason.

Regulators also expect firms to test their trading algorithms before deployment. In the European Union, for example, investment firms must test algorithms and their risk controls before use (European Commission, 2017). Tests are only useful if they measure the right things.

The practical message of this dissertation is simple and cheap to apply. When evaluating a trading agent, also report how much capital it actually used, and check whether doing nothing would have scored just as well under the agent's own reward. Both checks are cheap: the do-nothing score needs only a rerun of the reward, and capital exposure needs per-bar position logs, which should be recorded during evaluation.

## Contributions

This is an MSc project, and its contributions are practical rather than theoretical:

1. **A working system.** A trading system built for this project, with a Rust execution engine, a Python machine-learning layer and 979 automated tests, running in paper-trading mode on AWS (Section 3.12).
2. **An honest comparison.** Under a corrected walk-forward evaluation, the PPO agent did not beat the supervised baseline, and neither beat simply holding the asset. The study also shows how small an effect it could reliably detect (Chapter 4).
3. **A clear explanation of the agent's behaviour.** The agent learned to stay mostly out of the market, partly in ways that the usual metrics hide. Measuring capital actually deployed, and scoring a do-nothing policy under the training reward, revealed this (Sections 4.1 and 5.3). A follow-up experiment, repeated across five seeds, showed that the abstention persisted when a fee-accounting error in the reward was removed, although the error had roughly doubled it (Section 4.6).
4. **A record of the evaluation's own mistakes.** Eighteen defects in the evaluation were found and corrected. Six of them changed a conclusion, and some were only found by an outside reviewer. The record shows how such mistakes happen and how they were caught (Sections 3.7 and 3.8).

None of the individual checks used here is new. The contribution is a fully documented case in which leaving them out would have led to the wrong conclusion.

## Structure of the Dissertation

Chapter 2 reviews the literature on RL for trading, market regimes, evaluation methods, reward design and exposure measurement. Chapter 3 describes the system, the two policies, the evaluation method and the alternatives considered. Chapter 4 presents the results. Chapter 5 discusses what the results mean, their limits, and recommendations for deployment. Chapter 6 summarises the findings, answers the research question and reflects on the project. The appendices hold the full defect register, installation instructions and technical details of the system.
