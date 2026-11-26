# Results

All results in this chapter come from the corrected walk-forward protocol described in Section 3.4. Each asset is evaluated on six chronological folds, with 4,320 training bars, 720 test bars, a step of 2,160 bars and a 70-bar embargo between training and testing. Each fold is scored against its own Random-Forest baseline, comparators are aligned by timestamp, and the data window is fixed to end on 5 July 2026. Maximum drawdown is measured on the compounded equity curve within each fold and summarised by the median across folds; it is never pooled across the gaps between folds.

## The Learned Policy Is Abstention

The central result of this dissertation is that the trained agent learned to stay out of the market. It did so in two ways: openly, by choosing the action that does nothing, and in a concealed way, by choosing actions that are recorded as activity but deploy almost no capital. This section sets out the evidence.

### Capital deployed against reported activity

The trained agent's capital-weighted exposure (mean absolute position divided by equity) was 4.27% on ETH and 5.25% on BNB. An untrained network of the same architecture, run in the identical environment with the same primitives and the same fill rules, deployed 49.15% and 49.35%. Because everything except the learning is held fixed, the low deployment is learned behaviour, not a property of the configuration or the fill mechanics.

Standard reporting does not show this gap. The agent's reported time in market, which counts bars on which a trading engine is active, was 58.1% on ETH and 54.2% on BNB. Figure 4.1 places the two measures side by side for every policy. By time in market the agent looks moderately active; by capital deployed it is almost idle.

<!-- FIGURE fig_4_1_time_vs_capital -->

Three different quantities are involved, and all three are true at once. An engine was active on 54–58% of bars; the agent actually held inventory on only 12.2% (ETH) and 12.4% (BNB) of bars; and average capital deployed was 4.3% and 5.2%. Only the last one predicts the risk profile, and it is not a quantity that reinforcement-learning trading studies normally report.

One limitation of the control must be stated. The untrained ETH run stopped in fold 5, when its equity fell below the environment's threshold of 50% of initial capital. Its trace therefore holds 4,220 bars rather than 4,320, and its 49.15% is the mean before termination. The termination is itself informative: deploying about half of capital without judgement led to liquidation. The untrained network is a measure of how much capital the primitives can deploy, not a performance target.

### Two components of abstention

The gap between the untrained and trained exposure is 44.89 percentage points on ETH and 44.10 on BNB. Taking the untrained network's exposure on active bars as the counterfactual, the gap splits into two parts of similar size (Figure 4.2):

- explicit abstention, from selecting the FLAT (do-nothing) action: 20.5 points on ETH and 22.6 on BNB;
- concealed abstention, from lower deployment within nominally active actions: 24.3 points on ETH and 21.5 on BNB.

<!-- FIGURE fig_4_2_exposure_split -->

The explicit component is visible in the action distribution. The trained agent selected FLAT on 41.9% of ETH bars and 45.8% of BNB bars, against 0.2% and 0.0% for the untrained network, so this behaviour was learned rather than present at initialisation.

The concealed component works through a change in which active actions the agent prefers (Figure 4.3). Immediate-entry primitives (trend and swing) fell from 48.32% of the untrained network's selections to 2.87% of the trained agent's, a seventeen-fold drop. The share of the grid primitive stayed roughly the same (55.19% trained against 51.45% untrained on ETH). This matters because the primitives fill very differently. Trend and swing acquire a position on 93.3–97.2% of the bars where they are selected. Grid acquires one on only 10.23% (ETH) and 9.76% (BNB), because a price level must be crossed before it buys. In effect, the agent chose actions that count as activity but hold no capital on about four bars in five (78.9% of active bars on ETH, 77.2% on BNB).

<!-- FIGURE fig_4_3_action_mix -->

The bar-level counts confirm both components. Of the trained agent's 4,320 bars per asset, explicit FLAT accounts for 1,812 (41.9%) on ETH and 1,980 (45.8%) on BNB; active actions holding no position account for 1,979 (45.8%) and 1,806 (41.8%); and active actions holding a position account for only 529 (12.2%) and 534 (12.4%).

This dissertation calls the second pattern concealed abstention: selecting nominally active actions that deploy little or no capital in practice. The term is descriptive, not a claim of priority; learned abstention has been reported before in market making (Wang, Ventre and Polukarov, 2025). What this study adds is the measured split between the explicit and concealed parts. Concealed abstention is what makes the failure invisible: neither the action distribution nor time in market reveals it.

### Why the agent abstained

The reward decomposition shows why abstention paid. With the drawdown penalty weighted at λ = 0.5, the penalty term subtracted 0.310 (ETH) and 0.401 (BNB) from total reward, against profit-and-loss terms of −0.579 and −0.328. The penalty was about half the size of the profit-and-loss term on ETH and larger than it on BNB.

This happened despite an incentive designed to prevent it. The reward's first term is measured relative to buy-and-hold (Section 3.2), so a flat bar in a rising market is penalised by exactly the passive gain it gives up. The withdrawal therefore cannot be blamed on a missing incentive to participate; the drawdown penalty and the double-counted fees (Section 3.2) outweighed it.

### The do-nothing policy scores higher under the agent's own reward

The decisive test is to score a permanently flat policy with the same reward function the agent was trained on. Across five seeds, folds 1 and 4, and both assets, the flat policy scored higher than the trained agent in 19 of 20 cells (Figure 4.4). The single exception is BNB fold 4 with seed 999, where the trained agent scored 0.0944 against 0.0801 for the flat policy, a margin of 0.0143. Seed 999 also had the lowest drawdown and the highest Sharpe ratio in that fold (Section 4.3). Training beat doing nothing only at one of its best draws, and then only narrowly.

<!-- FIGURE fig_4_5_flat_vs_trained -->

For seed 42 over all six folds, the same comparison gives −0.448 (ETH) and −0.038 (BNB) for the flat policy against −0.994 and −0.854 for the trained agent. This is a single draw on a broader fold scope, and is shown only as an illustration.

The conclusion is that the reward did not reward routing. Under it, near-abstention is close to optimal, so the routing question the experiment set out to ask is not the question the reward answered. This is a finding about this reward, which counts transaction costs twice and penalises drawdown at λ = 0.5. It is not a claim that reinforcement learning generally learns abstention in trading.

### No good point on the exposure spectrum

The observed policies span the range of capital deployment, and none of them succeeds. The untrained network deployed about 49% of capital without judgement and was liquidated on ETH. The trained agent deployed 4–5% and still underperformed passive exposure on both assets. The supervised baseline, at 21.1% (ETH) and 60.5% (BNB), also underperformed passive exposure. Each point fails for a different reason. The question that matters is whether any learned policy earns its deployment against passive exposure at matched risk, and on this evidence none does.

## Performance Under the Corrected Protocol

Tables 4.1 and 4.2 report performance pooled over the six folds of each asset (4,314 hourly bars). Maximum drawdown is the median of the per-fold values; capital exposure is the capital-weighted measure defined above.

Table 4.1: ETH-USDT, corrected protocol

| Policy | Total return | Sharpe (ann.) | Max drawdown (median) | Profit factor | Trades | Capital exposure |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Buy and hold | +38.0% | – | 0.195 | 1.04 | – | 100% |
| Supervised RF-gated | −21.2% | −1.86 | 0.107 | 0.90 | 136 | 21.1% |
| PPO (seed 42) | −12.8% | −1.74 | 0.032 | 0.79 | 46 | 4.3% |

Table 4.2: BNB-USDT, corrected protocol

| Policy | Total return | Sharpe (ann.) | Max drawdown (median) | Profit factor | Trades | Capital exposure |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| Buy and hold | −4.3% | – | 0.198 | 1.00 | – | 100% |
| Supervised RF-gated | −20.3% | −0.72 | 0.117 | 0.95 | 129 | 60.5% |
| PPO (seed 42) | −25.9% | −3.04 | 0.053 | 0.65 | 45 | 5.2% |

No routing policy beat passive exposure on either asset. The two routed policies also operated at very different levels of deployment: the Random Forest deployed 4.9 times as much capital as PPO on ETH and 11.5 times as much on BNB. All comparisons between PPO and the Random Forest in this chapter are therefore descriptive.

Both PPO rows use seed 42, which was fixed in the code before any result was seen. On BNB this seed lies at the unfavourable end of the seed distribution (Section 4.3), so the BNB figures are, if anything, pessimistic about PPO. This does not change any conclusion.

The lower drawdowns of the routed policies come from lower exposure, not from better timing. Figure 4.5 shows the per-fold drawdowns behind the medians: PPO's median is 3.2% on ETH and 5.3% on BNB, against 10.7% and 11.7% for the Random Forest and 19.5% and 19.8% for buy-and-hold. To test whether this reflects skill, PPO was compared with random-entry strategies matched to its own 4–5% capital exposure. On the pooled drawdown basis that this diagnostic uses, the matched random strategies had a median maximum drawdown of 5.1% on both assets (0.0509 ETH, 0.0508 BNB), against 16.5% and 29.3% for PPO. PPO sat at the 100th percentile, the worst position, of the matched random distribution on both assets. A policy that drew down less because it held less is not a policy that managed risk.

<!-- FIGURE fig_4_4_fold_drawdowns -->

A single-window benchmark reported earlier in the project (Sharpe 1.85, maximum drawdown −0.4%) was produced under a contaminated protocol and is withdrawn; Section 3.7 explains why.

## Statistical Testing and Power

### The paired test

The primary comparison is a paired mean-difference test on realised per-bar returns, with Newey–West heteroskedasticity- and autocorrelation-consistent standard errors (Newey and West, 1987), lag 9 by the rule ⌊4(n/100)^(2/9)⌋, and Holm correction across the two assets (Holm, 1979). It compares the realised returns of two trading policies on identical bars; it is not a Diebold–Mariano test, which is defined on forecast-loss differentials.

Table 4.3: Paired test, PPO against the fold-specific Random Forest

| Pair | Statistic | p | Holm-adjusted p | n |
| :--- | :---: | :---: | :---: | :---: |
| ETH | +0.43 | 0.665 | 1.00 | 4,314 |
| BNB | −0.33 | 0.743 | 1.00 | 4,314 |

Neither difference is significant.

### Drawdown differences

Drawdown differences were tested with a fold-cluster bootstrap: the six per-fold drawdown differences are resampled with replacement as whole units, so nothing crosses a gap between folds.

Table 4.4: Per-fold maximum-drawdown difference (PPO − RF), fold-clustered bootstrap

| | ETH | BNB |
| :--- | :---: | :---: |
| Mean per-fold difference | −0.070 | −0.054 |
| 95% confidence interval | [−0.121, −0.020] | [−0.138, +0.022] |
| Excludes zero | Yes | No |

On ETH, PPO's per-fold drawdowns are below the baseline's in five of six folds, and the mean difference excludes zero; on BNB they cannot be distinguished. The ETH result must be read with its mechanism. It is consistent with PPO's much lower deployment (4.3% against 21.1%), not with timing skill: if it were skill, PPO would not rank worst against random entries matched on the same exposure (Section 4.2), which it does on both assets. The finding is that a lower-exposure policy drew down less per fold, not that PPO reduces drawdown.

The split between the two assets also illustrates a point about scale. With two assets and an effective sample of six folds each, a result that is significant on one asset but not the other supports no general conclusion. It shows sensitivity to the choice of asset, which single-asset studies cannot reveal, and is why both assets are always reported together.

### Statistical power

The design is underpowered by an order of magnitude, so the null result cannot be read as evidence of parity. Using the same variance estimator, the minimum detectable effect at 80% power and α = 0.05 is 81.7 percentage points of cumulative return on ETH and 179.3 on BNB. The observed differences, on the same constant-per-bar basis, were +9.7 and −11.3 points (in compounded total return, Tables 4.1 and 4.2, they are +8.4 and −5.6 points). Figure 4.6 shows how far inside the undetectable range they lie.

<!-- FIGURE fig_4_6_detectable_effect -->

The minimum detectable effect is a prospective design quantity, computed from the variance and the sample size alone. Observed (retrospective) power is not reported, because it is a direct function of the p-value and adds no information (Hoenig and Heisey, 2001). The clearest evidence of underpowering is instead the width of the confidence intervals in Table 4.4: about 0.10 on ETH and 0.16 on BNB, against effects of 0.05–0.07. With intervals this wide relative to the effects, the drawdown difference is resolved on only one of the two assets, and the return difference (Table 4.3) on neither.

To put the required sample in context, Table 4.5 anchors the effect size on published positive results rather than on this study's own estimate.

Table 4.5: Sample required at literature-anchored effect sizes

| Anchor effect | ETH | BNB |
| :--- | :---: | :---: |
| 1.7 pp per year (Xue and Ye, 2025, S&P 500 RL) | 2,506 years | 7,413 years |
| 10 pp per year (crypto-RL order of magnitude; an assumption) | 72 years | 214 years |

Even at effects far larger than anything observed here, the design would need decades to millennia of hourly data. "Parity" is therefore not a supported claim. The correct statement is that, at this sample size, the design cannot distinguish parity from differences far larger than the one observed. Hypothesis CH3 holds only in this weaker, power-qualified form.

A design analysis (Gelman and Carlin, 2014) at the same anchors makes the point sharper (Table 4.6).

Table 4.6: Type S and Type M error rates at n = 4,314

| Anchor | Pair | Power | Type S (wrong sign, if significant) | Type M (exaggeration, if significant) |
| :--- | :--- | :---: | :---: | :---: |
| 1.7 pp/yr | ETH | 0.050 | 0.45 | about 60× |
| 1.7 pp/yr | BNB | 0.050 | 0.47 | about 100× |
| 10 pp/yr | ETH | 0.056 | 0.25 | about 10× |
| 10 pp/yr | BNB | 0.052 | 0.35 | about 17× |

At both anchors the power is barely above the 5% false-positive rate, so a significant result would be close to a coin toss. If one did occur, it would have a 25–47% chance of pointing the wrong way and would exaggerate the true effect roughly 10 to 100 times. The design is therefore not only insensitive; even at the larger crypto anchor, any significant result it produced would be unreliable. (These values are computed from the same per-bar standard error that gives the minimum detectable effect above, and are consistent with Table 4.5.)

### Seed variance

Variation between random seeds is larger than the difference between methods. Across five PPO seeds on folds 1 and 4 of both assets, cumulative return varied by up to 28 percentage points within a single fold (BNB fold 4: −22.99% for seed 7 against +4.69% for seed 123), and maximum drawdown by a factor of 45 (0.007 against 0.312). The paired statistic changes sign across seeds on both assets (BNB −0.92 to +0.80; ETH −0.03 to +1.98). The single-seed comparison in Section 4.2 is one draw from this distribution and supports no method-level claim in either direction. The seed sweep covers folds 1 and 4 only, so its figures are not directly comparable with the six-fold tables.

Seed 42, used for all headline figures, sits at the unfavourable end of this distribution. Its BNB per-fold drawdowns are 0.190 in fold 1, the worst of the five seeds (five-seed mean 0.098), and 0.105 in fold 4, close to that fold's mean of 0.106. It is at or near the unfavourable end in both BNB cells and in the middle of the distribution on ETH. The headline figures are therefore pessimistic towards the reinforcement-learning agent rather than flattering to it, and no BNB-specific magnitude should be read as typical of the method.

## Protocol Corrections

The results in this chapter are the product of a corrected protocol. A series of audits found defects in the original evaluation, including leakage at fold boundaries, pooled drawdowns, uncompounded returns and single-seed reliance. Six of these defects reversed a conclusion. Each is described in Section 3.7 (Table 3.1), and the full register is in Appendix A. Only corrected figures are reported here.

## Regime-Signal Economics

The supervised classifier's accuracy did not translate into economic value. The deployed classifiers' out-of-sample accuracy is 0.849 (ETH) and 0.870 (BNB), subject to the provenance caveat in Section 3.3. Yet on bars predicted as DANGER, the market rose 47% (ETH) and 38% (BNB) of the time. The mean forward 24-bar return on DANGER bars was −0.79% and −0.68%, an edge far too small to pay for the missed gains on false positives. Meanwhile, 36.9% (ETH) and 17.9% (BNB) of bars not flagged as DANGER went on to suffer forward drawdowns of 3% or more.

These figures fit together. The overall mean on DANGER bars is negative because the correct warnings pull it down, but false warnings gave up a large gain on average: +3.34% (ETH) and +3.88% (BNB) mean forward return (Section 3.7, register row 8). That asymmetry, a small edge when right and a large missed gain when wrong, is how high classification accuracy fails to produce value.

The same pattern holds at coarser scales. Non-overlapping 24-bar blocks entered on a DANGER signal returned −0.57% (ETH) and −0.49% (BNB), against +0.39% and +0.23% for blocks without the flag. At portfolio level, the gated strategy underperformed buy-and-hold by 59.1 percentage points on ETH and 16.0 on BNB over the same window (compounded returns, Tables 4.1 and 4.2). That is the real cost of the gate.

## Removing the Fee Double-Count

The reward counts transaction costs twice: once in the net equity return and once as an explicit fee term (Section 3.2). A reasonable objection is that the agent abstained only because of this error. To test this, the twelve PPO models were retrained with the explicit fee term removed, so that fees were counted once. Everything else was unchanged: the same folds, data, seed (42), training length (one million steps), hyper-parameters and drawdown penalty (λ = 0.5). This experiment was run in November 2026, after the evaluation was frozen, using the frozen code with a single configuration switch.

Table 4.7: Effect of counting fees once instead of twice (seed 42, six folds per asset)

| Measure | ETH, fees twice | ETH, fees once | BNB, fees twice | BNB, fees once |
|---|---:|---:|---:|---:|
| Capital-weighted exposure | 4.3% | 8.5% | 5.2% | 8.4% |
| Share of bars on FLAT | 41.9% | 40.4% | 45.8% | 41.7% |
| Share of bars on trend or swing | 2.9% | 10.3% | 2.9% | 7.9% |
| Total return | −12.8% | −43.2% | −25.9% | −38.5% |
| Median per-fold maximum drawdown | 0.032 | 0.062 | 0.053 | 0.052 |
| Folds where doing nothing scored higher | – | 6 of 6 | – | 6 of 6 |

Counting fees once doubled the capital the agent deployed on ETH (4.3% to 8.5%) and raised it by about 60% on BNB (5.2% to 8.4%), and the agent chose trend and swing actions more often. But it still deployed less than a tenth of its capital, far below the untrained network (49%) and the Random Forest baseline (21% on ETH, 61% on BNB). It still chose to do nothing on about 40% of bars. Under the corrected reward, the do-nothing policy scored higher than the trained agent in all six folds on both assets. The extra trading also lost money: total return fell to −43.2% on ETH and −38.5% on BNB.

The fee double-count is therefore not the main cause of the abstention. Removing it changed the amount of abstention, but not the result. The rest of the reward, a return measured against buy-and-hold together with the drawdown penalty, still makes staying out of the market the best choice in this environment.

To check that this is not a property of seed 42, the fees-once models were also trained with seeds 7, 123, 2024 and 999 on folds 1 and 4, the same seed-fold design as Section 4.4. Table 4.8 gives the result.

Table 4.8: Fees counted once, five seeds on folds 1 and 4 (20 seed-fold-asset cells)

| Measure | ETH | BNB |
|---|---:|---:|
| Capital-weighted exposure, mean (range) | 12.5% (3.9–22.3%) | 7.4% (3.8–14.6%) |
| Share of bars on FLAT | 25.1% | 44.9% |
| Cells where doing nothing scored higher | 10 of 10 | 10 of 10 |

Doing nothing scored higher than the trained agent in all 20 cells, compared with 19 of 20 when fees were counted twice (Section 4.4). Exposure varied with the seed, reaching 22% in two ETH cells, but the agent never deployed more than about a fifth of its capital, and on average far less than the untrained network (49%). The seeds therefore confirm the direction of the seed-42 result. The exact amount of exposure depends on the seed, so the six-fold figures in Table 4.7 should be read as one draw rather than a precise estimate.

## A Survey of Open-Source RL-for-Trading Repositories

This study's defects raise a wider question: are such diagnostics routinely absent from published work? To examine it, a ten-check audit tool was built from the failure modes documented in this dissertation. It was validated on this project's own artefacts from before and after correction, where it caught six of eight known defects with no false positives. It was then applied to public repositories accompanying reinforcement-learning trading research.

Of twelve repositories identified, seven could be cloned and examined; the other five had been renamed, removed or were unreachable. None of the seven committed the artefacts that any audit needs: per-bar return series, fold boundaries or seed-level metrics. Results are computed when the code runs; the evidence does not travel with the publication. The tool therefore returned "not applicable" on eight or nine of its ten checks for every repository. Of the two checks that could run, seed reporting was flagged on all seven (no seed-level results were published, and several trainers default to no fixed seed), and retrospective power passed on all seven only because none reported power of any kind.

The finding is scoped to what was examined. Among seven open-source repositories, none published artefacts sufficient to verify a single headline number, and none published seed-level results. This does not show that the underlying analyses omit the diagnostics. It shows something more actionable: independent verification of the published claims is impossible from what is published. This dissertation's own evidence chain (Section 3.9 and Appendix B) is designed to answer that problem.

The survey has clear limits. The sample is small, at seven repositories. It covers only open-source work, which is the field's more transparent end, so the results probably understate the problem. Only ten checks were implemented. Four of the seven codebases could not be executed at all, because of unpinned dependencies, missing data or undocumented entry points, which is itself a reproducibility finding. No claim is made about any individual repository's conclusions; only aggregate counts are reported.
