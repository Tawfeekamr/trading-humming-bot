# Conclusions

## Summary of Findings

Under the corrected evaluation protocol, the answer to the research question is a well-evidenced negative result. Four findings support it.

1. No routing edge. Neither the supervised regime-gated policy nor the PPO agent beat passive buy-and-hold on either asset. On ETH, buy-and-hold returned +38.0% against −21.2% for the gated policy and −12.8% for PPO. On BNB, buy-and-hold also lost, but far less: −4.3% against −20.3% and −25.9%. CH1 is not supported, and CH2 could not be tested as specified. The lower drawdowns of PPO are an exposure effect: on the pooled basis used for exposure matching, random entries at the same capital deployment drew down less than PPO on both assets.

2. The comparison cannot be interpreted at this scale. The minimum detectable effect (81.7 percentage points on ETH and 179.3 on BNB, cumulative, at 80% power) exceeds every observed difference (9.66 and −11.34 points on the same basis; +8.4 and −5.6 points in compounded total return). Detecting literature-anchored effects would need on the order of 2,506 years (ETH) and 7,413 years (BNB) of hourly data at a 1.7%-per-year anchor, or 72 and 214 years at a 10%-per-year crypto anchor. PPO seed variance, up to 28 points within a fold, also dominates the differences between methods. CH3 is upheld only in a power-qualified form: non-significance here documents an underpowered design, not parity.

3. The learned policy is abstention. The agent's 4–5% capital-weighted exposure is learned withdrawal under a drawdown-penalising reward (λ = 0.5). The penalty term is about half the size of the profit-and-loss term on ETH and larger than it on BNB, and a permanently flat policy outscores the trained policy in 19 of 20 seed-fold-pair cells. The single exception, BNB fold 4 with seed 999, wins by only +0.0143, and that seed had the lowest drawdown and highest Sharpe ratio in its fold. The experiment therefore measured a reward specification rather than a routing strategy. Removing the double-counted fee did not change this: with fees counted once, the agent deployed 8.5% (ETH) and 8.4% (BNB) of its capital, and doing nothing still scored higher in all twelve folds, and in all 20 cells of a five-seed check on two folds (Section 4.6).

4. Classification accuracy does not imply gating value. With out-of-sample accuracy of 0.85–0.87 for ETH and BNB (subject to the caveat in Section 3.3), DANGER warnings are false 38–47% of the time. The mean forward return on DANGER bars (−0.79% on ETH, −0.68% on BNB) is too small to pay for the upside given up, and the gate cost 59.1 percentage points (ETH) and 16.0 points (BNB) against buy-and-hold over the same window (Section 4.5).

A supplementary test after the evaluation freeze found the same pattern beyond the two assets. None of five pre-registered rule-based signals, tested on the sixty most-traded Binance perpetual futures, beat random entries using the same exits (Section 4.8).

## Answer to the Research Question

The research question asked whether a PPO agent could learn a routing policy that beats a calibrated supervised baseline on risk-adjusted return and maximum drawdown.

The answer is no. The PPO agent did not beat the supervised baseline in any way that reflects skill, and neither policy beat simply holding the asset. The PPO agent did show smaller drawdowns, but only because it kept most of its capital out of the market. Random entries using the same small amount of capital drew down even less. The study was also too small to detect differences below about 80 percentage points on ETH and 180 on BNB, so it cannot show that the two methods are equal either.

The more useful finding is about evaluation. Under this reward, the agent learned to do very little, and the usual metrics made that look like good risk management. This was not simply the result of a fee-accounting error: when the error was removed, the agent still preferred to stay out of the market. Two simple checks, capital actually deployed and a do-nothing comparison under the training reward, made the problem visible.

## Reflection on the Project

Looking back, the project changed direction. It started as an attempt to build a better trading system with machine learning and reinforcement learning. It ended as a study of why the results looked better than they were. I did not plan that, but I think it is the more valuable outcome.

Several things went well. Building the system end to end, from data to a live server, taught me far more than a notebook experiment would have. Saving every result with its data hash and settings meant that, when doubts arose, I could check the numbers rather than rely on memory. Writing the methodology chapter against the saved evidence, not from memory, exposed errors that I would otherwise have submitted.

Several things I would do differently. I built a lot of system before I evaluated anything carefully, and some early results had to be withdrawn. My own audits missed six defects that only independent checks found, five of them by an outside review, so I would ask for independent review much earlier. I would model slippage from the start, and I would check the reward function on its own, including a do-nothing comparison, before training any agent. I would also start writing earlier; the writing itself turned out to be one of the best ways to find mistakes.

The main lesson I take from the project is that how a model is measured matters as much as the model itself. A good-looking number is a question to investigate, not an answer.

## Future Work

1. Redesign the reward before scaling up. Correcting the fee double-count was not enough (Section 4.6). The drawdown penalty should be bounded relative to achievable profit, or replaced with a drawdown-conditioned shaping term, so that staying flat cannot dominate. The flat-policy comparison should be a standing acceptance test during training.

2. Evaluate with more seeds and longer horizons. Any future method claim needs seed distributions (at least five seeds across all folds) and windows long enough to bring the minimum detectable effect below the effect of interest, with a documented power analysis before the experiment is run. Combinatorial cross-validation (López de Prado, 2018; Gort et al., 2022) is a candidate design.

3. Make gating cost-sensitive. With a 38–47% false-DANGER rate, the gate needs asymmetric costs, or a confidence threshold calibrated against forgone return rather than accuracy, before it can add value in rising markets.

4. Benchmark at equal exposure. Method comparisons should hold capital-weighted exposure constant across arms, or report exposure-normalised returns as a descriptive control, as demonstrated in Section 4.2.
