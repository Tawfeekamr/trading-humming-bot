# Discussion and Recommendations

## Relation to Positive-Result Literature

A substantial published literature reports RL trading policies that outperform passive exposure. The negative result of this dissertation has to be read against that literature, not instead of it.

Several studies report clear gains. Xue and Ye (2025) report PPO terminal wealth of 2.11 against 1.94 for buy-and-hold, and a Sharpe ratio of 0.73 against 0.66, on the S&P 500 between 2020 and 2025. This corresponds to roughly 1.7 percentage points of CAGR, which this dissertation uses as its S&P literature anchor in the power analysis (Section 4.3). Kochliaridis et al. (2023) report promising results on five cryptocurrencies when PPO is combined with rule-based safety mechanisms, with commission fees modelled. Amirzadeh et al. (2023) report a causal-RL PPO agent with positive return on ETH where buy-and-hold was negative, the direct inverse of this study's ETH finding. This is a reminder that the disagreement lies between systems, not only between datasets. Gort et al. (2022) find that PPO trained with combinatorial purged cross-validation (CPCV) outperforms walk-forward and K-fold agents. They work in the same evaluation-methodology tradition this dissertation relies on (Agarwal et al., 2021), yet reach a positive conclusion.

Other studies are closer to this one. Felizardo et al. (2022) report that a supervised model outperformed several RL trading systems on cryptocurrency assets, which agrees with this study's finding that the supervised baseline was not beaten. Ma (2025) adds a recent negative result from portfolio management: myopic optimisation outperformed RL, which produced lower or negative returns, higher variance and larger costs.

Abstention itself has published precedents. Wang, Ventre and Polukarov (2025) show that occasionally refusing to quote improves returns and Sharpe ratios in market making. The difference is design. There, abstention is designed, beneficial and bounded, with quoting ratios above 95%. Here it is undesigned, emerges from a misspecified reward and leaves roughly 95% of deployable capital idle. Abstention is therefore not a failure in itself; undesigned abstention under a misspecified reward is the case documented here. Liu et al. (2023) name the related phenomenon of "lazy agents" that do nothing while reward accrues. In their setting it is a multi-agent free-riding problem, because teammates carry the task. The case here is single-agent: there are no teammates, and the reward itself makes abstention optimal. Zhang (2025) uses an identically-zero "zero-hedge" baseline, a do-nothing precedent in trading evaluation, but compares it on profit and penalty metrics rather than on the training reward.

The closest known analogue is selective prediction. In selective classification, abstention is declared and measured, and coverage is reported as a matter of course (Chow, 1970; Geifman and El-Yaniv, 2017; Geifman and El-Yaniv, 2019). In RL trading evaluation, neither the abstention rate nor capital-weighted exposure is a standard reported quantity. The contribution here is therefore not the observation that abstention flatters metrics, which is decades old. It is a documented case in a domain where the corresponding coverage measure is missing from standard practice.

This dissertation does not claim that reinforcement learning generally fails at trading. The claim is a documented failure mode in one system under one reward specification, with a causal diagnosis and a traceable evidence chain. The papers above differ from this study in reward design, asset universe, cost modelling and validation scheme. Any of those differences could be decisive, and which ones are is an open empirical question that a single case cannot settle.

On validation scheme specifically, Gort et al. (2022) argue that combinatorial cross-validation tests many more market situations than walk-forward, which tests only one. Walk-forward was used here because it preserves temporal contiguity within each test window and does not assume that non-adjacent market segments are exchangeable. That assumption is unsafe in a non-stationary market, and the fold-gap pooling defect (Section 3.7, register row 11) shows that it has real consequences even when made implicitly. CPCV is listed as future work (Section 6.4).

Publication bias is a recognised field-level issue, but it is not used here to defend this study's result. In this context it cannot be tested, and invoking it would be evasive. The disagreement with the positive literature is addressed through the concrete differences above.

## The Withdrawal Claim, Narrowed

The same literature also limits how far the withdrawal diagnosis can be pushed. Xue and Ye (2025) use a reward that includes transaction costs and a portfolio-variance penalty, yet their agent does not abstain. Penalising costs and risk does not, on its own, produce withdrawal.

The closest published relative is the JaxMARL-HFT study (Mohl et al., 2025), in which market makers "learn to trade very infrequently". The authors remark that under that reward family "an optimal policy seems to be to never trade". This is an abstention-optimal reward observed by the authors and stated analytically, but no flat-policy baseline is run and it is not framed as a test for reward misspecification. The flat-policy floor described in Section 5.3 is exactly the test that remark implies.

The obvious candidate for what made this system different was its double-counted transaction cost: the environment subtracts fees from equity, and the reward then subtracts a fee term again (Section 3.2). The ablation in Section 4.6 tested this directly. With fees counted once, the agent deployed more capital (8.5% instead of 4.3% on ETH), but it still abstained on about 40% of bars, and doing nothing still scored higher in all twelve folds.

The fee double-count therefore made the abstention stronger but was not its main cause. What remains is the combination of a return measured against buy-and-hold and a drawdown penalty at λ = 0.5, in an environment where the grid primitive rarely fills. Which of these matters most was not separated by experiment. The claim is accordingly narrowed to the following:

> Under a reward that measures return against buy-and-hold and penalises drawdown at λ = 0.5, PPO converged to abstention in this system, with or without the double-counted fee; a permanently flat policy outscores the trained policy in 19 of 20 seed-fold-pair cells, and in 12 of 12 folds when fees are counted once.

## Contributions

This dissertation is a disconfirming case study. A single-system design will be challenged on generalisability, so the position is stated directly. Flyvbjerg (2006) shows that the belief that single cases cannot contribute to scientific development is a misconception. Tsang (2014) identifies the strengths of case studies in theoretical generalisation and in identifying disconfirming cases. This study is of the second kind: the field's prevailing assumption is that RL routing adds value (Section 5.1), and this study documents an instance where it does not, with a causal explanation and a traceable evidence chain.

The study meets the standard rigour criteria for case research. The method is applied systematically through the corrected protocol (Section 3.4). Data collection and analysis are documented through per-bar return series, provenance manifests and a run manifest. The headline returns, drawdowns, exposure figures and the flat-policy count can be re-derived from the committed per-bar data in about five minutes; the claim-to-evidence map is in the reproduction guide referenced in Appendix B. Alternative explanations are tested: the six withdrawal diagnostics in Section 4.1 (action distribution, reward decomposition, untrained-policy comparison, the two-component gap decomposition, the per-bar split of active actions, and the untrained exposure control) each address one alternative. These are initialisation bias, a missing incentive, capacity limits, an accounting artefact, structural non-filling only, and bar-level composition.

The scope is limited: one system, one reward and two assets, with no multi-case design. A multi-case design would need the same corrected protocol applied across systems that differ in reward specification (including a fee double-count ablation), asset class and cost model, with seed distributions in each cell. That is a programme of work rather than a dissertation chapter.

Within that scope, the contributions are three.

1. A production multi-engine trading platform deployed on AWS EC2 (Tokyo), with a live machine-learning regime pipeline (per-pair calibrated classifiers with provenance manifests) feeding a Rust trading engine.

2. A corrected walk-forward evaluation protocol, committed with its full audit trail (per-bar return series, provenance manifests, run manifests and the defect register in Appendix A), and six transferable diagnostics. None of these diagnostics was invented here; each is established practice in its origin discipline. What this study adds is a documented case in which the absence of each one changed a conclusion, with before-and-after evidence.

   a. The flat-policy floor. Before concluding that a trained policy has learned anything, compare it with permanent abstention under the same reward. Here it would have exposed the abstention result before any performance claim was made. Do-nothing baselines are institutionalised outside trading: the do-nothing agent is the zero-score reference in the L2RPN power-grid competitions (Marot et al., 2021), and a trivial fixed-quote policy (quoting at fixed offsets, A = [0, 0]) is "a relatively strong benchmark" in market-making RL (Gašperov and Kostanjčar, 2021). Execution RL also uses a passive baseline that is inactive 60% of the time (Hafsi and Vittori, 2024). However, every trading use found in the prior-art search scores abstention on performance metrics rather than under the training reward. Its use as a test for reward misspecification appears unclaimed in the work examined; this is a scoped observation from one search, not a priority claim.

   b. Reward-component decomposition. Report the size of each reward term, not only the total, so that a dominating penalty becomes visible. Here it would have shown the drawdown penalty at about half the size of the profit-and-loss term on ETH and larger than it on BNB, and the fee double-count, both of which survived four self-audit passes. The technique is established in explainable RL (Lin et al., 2019; Lin et al., 2020) and in RL debugging practice; only the case documentation is new.

   c. Capital-exposure matching. Match baselines on capital-weighted exposure rather than time in market, and report both. Here it would have prevented the "drawdown advantage" verdicts, which were exposure artefacts. Its origins are portfolio attribution (Brinson, Hood and Beebower, 1986), Active Share (Cremers and Petajisto, 2009) and Betting Against Beta (Frazzini and Pedersen, 2014). The closest structural analogy is selective prediction (Chow, 1970; Geifman and El-Yaniv, 2017; Geifman and El-Yaniv, 2019), where coverage reporting is the established remedy.

   d. Fold-aware pooling. Never join non-adjacent test windows into a single equity curve or dependence structure. Here it would have prevented the 2.3- to 5.5-fold drawdown inflation and the HAC and bootstrap dependence that crossed fold boundaries. It comes from purging and embargo practice in quantitative-finance cross-validation.

   e. Power reporting. State the minimum detectable effect alongside any null result, together with the effective sample size after clustering. Here it would have prevented every "parity" statement made before correction. It comes from power analysis in applied statistics (Hoenig and Heisey, 2001; Gelman and Carlin, 2014).

   f. Seed-distribution reporting. Never report a single-seed RL result. Here it would have shown that a headline rested on a seed at the unfavourable end of its own distribution. It comes from the deep-RL reproducibility literature (Agarwal et al., 2021).

   A survey of seven open-source RL-for-trading repositories (Section 4.7) found that none committed the per-bar, fold-level or seed-level artefacts the diagnostics require, and none published seed-level results. This is a finding about what is published, not about the analyses those authors may have run.

3. A precisely scoped causal diagnosis. A reward of this form, a return measured against buy-and-hold with a drawdown penalty at λ = 0.5, makes abstention optimal in this system, and the flat-policy floor detects this before deployment. Removing the double-counted fee did not change this (Section 4.6). It is a finding about this reward, not a claim that RL generally learns to abstain.

## Limitations

1. Asset coverage. The corrected evaluation covers ETHUSDT and BNBUSDT only. The live regime pipeline also serves XRP-USDT and DOGE-USDT, which were never evaluated under the corrected protocol, and nothing here transfers to them. Section 4.8 widens coverage to sixty coins, but only for simple rule-based signals, not for the RL agent.

2. Backtest only. All results come from historical-replay backtests, and no live or paper-trading performance is claimed. A paper-trading gate for shadow routing was merged into the codebase but never run: no routing sidecar was deployed, no shipped container could execute the router, and no shadow routing log was ever produced.

3. Seed scope. The seed sweep covers five seeds on folds 1 and 4 only (20 of 60 possible trainings), so its figures are not comparable with the six-fold pooled tables. The main run uses seed 42 throughout, fixed as the code default before any result was observed. Seed 42 is at the unfavourable end on BNB (the worst draw in fold 1 and the second worst in fold 4) and in the middle of the distribution on ETH, so the BNB headline figures are, if anything, pessimistic towards the RL agent.

4. Underpowered by construction. Detecting literature-anchored effect sizes at 80% power would require on the order of 2,506 to 7,413 years of hourly data at the 1.7%-per-year anchor, or 72 to 214 years at a 10%-per-year crypto anchor (Section 4.3). The design cannot support method-level claims at any effect size it is likely to meet.

5. Baseline ordering. The supervised baseline was chosen because it represents the prevailing approach and because the existing infrastructure isolated the decision layer as the only variable. This implicitly assumed that both approaches beat simpler alternatives, which the corrected evaluation disproved: both were beaten by buy-and-hold and, at matched exposure, by random entry. Baselines should be ordered upwards from abstention (flat policy, capital-matched random entry, buy-and-hold, then learned policies), and were not.

6. Single penalty weight. Withdrawal is documented at λ = 0.5 only. No sensitivity sweep over the drawdown-penalty weight was run, so no threshold separating withdrawal from deployment has been established.

7. Unusually punitive reward. The frozen reward counts transaction costs twice, in addition to the λ = 0.5 drawdown penalty (Section 3.2). Counting fees once did not remove the abstention across five seeds (Section 4.6), but the drawdown penalty and the buy-and-hold benchmark were never varied. The finding therefore supports the claim that this reward produces abstention and that the flat-policy comparison detects it. It does not support the claim that RL generally converges to abstention in trading, or that drawdown penalties as such cause it.

8. No slippage. The only simulated friction is the 0.1%-per-side fee; spread and market impact were not modelled, so every fill executes at the bar price. Together with limitation 7, this means the agent learned under a cost signal that was too punitive on fees and too optimistic on execution, and neither direction is individually representative of live trading.

9. Inferential unit. The pooled paired test treats each per-bar return difference as an observation (n = 4,314), while the method-level claim concerns a training procedure evaluated six times. A fold-clustered analysis would have an effective n near six, so true power is lower than reported. This strengthens the underpowering finding rather than weakening it.

10. Training-window asymmetry. PPO training windows round to calendar months, so the PPO fold-1 window is 4,344 bars against the Random Forest's 4,320, a 24-bar (0.6%) difference. The out-of-sample boundary still holds: PPO trains through 10 January 2025 and testing starts on 13 January 2025.

11. Provenance across commits. The run manifest, the PPO model files and the Random-Forest manifests cite different commits of the codebase. The differences touch evaluation and reporting code only, with no change to training or environment logic, but single-snapshot provenance is not demonstrated.

12. The untrained-policy control. This is an untrained PPO network evaluated deterministically, not a random-action control. It separates learned from initial behaviour of the same architecture, but not architecture bias or exploration noise.

13. Untrained-control horizon. The untrained ETH control terminates in fold 5, when equity falls below the environment's 50%-of-initial-equity threshold, leaving 4,220 observed rows instead of 4,320. Its 49.15% exposure is therefore an observed pre-termination mean, not a full-horizon estimate.

14. Two bootstrap intervals. The artefacts contain a generic independent bootstrap interval, which supports the per-fold summary tables, and a fold-clustered bootstrap, which supports the drawdown and Sortino difference claims. Each claim should be read against its own interval; the independent interval supports no method-level claim.

15. Approximate embargo. The 70-bar embargo bounds the RSI's exponential tail, but ADX and MACD are also infinite-support recurrences, and the 100-bar warm-up can reach about 30 bars past the boundary. The embargo is a decay tolerance, not a strict guarantee of independence.

16. Simulation-to-deployment gap. The evaluation environment includes three execution primitives, grid, trend and swing, and the trained agent did select swing (on ETH, 39, 0 and 36 selections at sizes 0.5, 1.0 and 1.5 of 4,320 steps; on BNB, 10, 8 and 1). The production Rust engine removed its swing strategy in July 2026, so 3 of the 10 simulated actions cannot be executed by the deployed system. No reported result is affected, but the action space was validated against a primitive the production path no longer contains.

### Deployment-Layer Limitations

An operational audit in August 2026, and server checks in October and November 2026, compared the live system with this dissertation. None of the points below affects any reported result: the evaluation uses frozen historical backtests, not the live system.

Seven problems were fixed on 26 November 2026, after the evaluation was frozen: five documented in the August audit and two found in the later server checks. Each code fix has automated tests; the retraining fix changes a scheduled workflow and will first run on 1 December 2026.

1. **Paper buys were not checked against available cash.** Grid buys kept filling into a falling market until the simulated USDT balance reached −566,141 and total equity was negative (−94,925). Buys are now refused or reduced when cash is short, in the same way sells were already limited to the coins held.
2. **The circuit breaker could not see negative equity.** Its daily-loss check switched itself off when the start-of-day equity was zero or negative. It now halts trading whenever equity is zero or negative.
3. **The regime label expired exactly when the next one arrived.** The cache kept labels for 180 seconds, the same as the push interval, so every cycle ended with a short gap in which the grid fell back to technical gating. Labels now last three push intervals.
4. **The trend engine's position limit was never read.** The configured maximum of two concurrent trend positions is now enforced across all pairs. The trend-level drawdown and daily-loss settings, which were also never read, were removed, because the portfolio-wide circuit breaker (10% drawdown from peak, 5% daily loss) already provides these limits.
5. **Monthly retraining had never succeeded.** Every scheduled run from June 2026 failed; from July onwards the workflow called a module that had been removed. It now retrains each model, scores the new and the current model on the most recent 30 days that the new model has not seen, and replaces the live model only if the new one is at least as accurate.
6. **Drift alerts could never fire**, because the drift report was never evaluated. The pusher now checks drift every hour and sends an alert when a pair's drift status changes. A latent defect that could send a second full-inventory sell during a forced exit was also fixed.

A further defect appeared on 27 November. While one trend entry waited for a delayed paper fill, the engine placed 143 more; 27 filled and moved the whole paper account into XRP. It was fixed on 28 November and the $3,033 loss recorded (Appendix C.5).

Two points remain. Routing is global while the regime signal is per pair, so if routing were made live, one asset's policy would gate all pairs in the same way. And because every push to the main branch is deployed automatically, a configuration change is an automated path by which routing could reach live mode. Routing is therefore kept in shadow mode and must not be enabled on this codebase.

## Recommendations for Deployment

The recommendations fall into two groups: what to do with this system, and what anyone evaluating a trading agent should do.

**For this system**

1. **Do not run the PPO router with real money.** It did not beat the supervised baseline or buy-and-hold, and its apparent safety came from not trading. The router should stay in shadow mode, as it is now.
2. **Keep the live system in paper mode for now.** The defects found in the live system were fixed in November 2026 (Section 5.4.1), but the fixes need several weeks of clean paper trading before any real money is used. Routing must also be made per pair before it is ever enabled.
3. **Add realistic trading costs to the simulator.** Slippage and spread were not modelled (Section 5.4). Any future result should include them, because they grow with position size.
4. **Tune the regime gate against missed gains, not accuracy.** Its danger warnings were false 38–47% of the time, and it cost more in missed gains than it saved (Section 4.5). Its threshold should be set against the return it gives up, not against classification accuracy.

**For anyone evaluating a trading agent**

1. **Report capital actually deployed** (mean absolute position divided by equity) next to time in market. Time in market alone can hide an agent that does very little.
2. **Score a do-nothing policy under the training reward.** If doing nothing scores as well as the trained agent, the reward is rewarding inactivity.
3. **Report each reward term separately**, so that a penalty that dominates the reward is easy to see.
4. **Use several random seeds and state the smallest effect the test can detect.** A non-significant result from a small test says little about whether two methods are equal.
5. **Fix the rules before testing, and compare with random entries.** Writing down the rules and pass criteria in advance, and comparing results with random entries that use the same exits, stopped five plausible signals from reaching paper trading (Section 4.8).

Each of these checks is cheap. Together they would have caught the main problem in this study before any performance claim was made.
