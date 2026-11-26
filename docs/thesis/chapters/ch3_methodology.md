# Methodology

This chapter describes the method in its final, corrected form: the system (3.1), the two policies compared (3.2–3.3), the evaluation protocol and statistics (3.4–3.5), the diagnostics, defects and discovery modes found by the audit (3.6–3.8), and reproducibility and ethics (3.9–3.10). How the protocol came to be correct is treated as one of the dissertation's findings. The full register of twenty-four defects is in Appendix A.

## The System Under Study

The evaluation was carried out on, and its failure modes were discovered in, a trading system that was deployed and running. The system is the apparatus of the study, not its result. It has three layers, shown in Figure 3.1.

The first layer is a Python machine-learning layer. It trains one regime classifier per asset, and a sidecar process pushes each classifier's predictions into a cache inside the execution engine every 180 seconds over an internal HTTP endpoint.

The second layer is a Rust execution engine. It runs two production strategy engines, alongside a separate signal-copy engine that is outside this study: a grid engine on ETH, BNB, XRP and DOGE, which quotes geometrically spaced buy and sell levels around an anchor price, and a trend engine on BNB, XRP and DOGE (ETH excluded since July 2026), which enters directionally and exits on a chandelier trailing stop. The engine trades in paper mode on a single AWS EC2 instance in Tokyo.

The third layer is the research stack evaluated in this dissertation. It is a Python Gymnasium environment that replays one-hour OHLCV bars and exposes simplified implementations of the same engine primitives to a reinforcement-learning agent. The primitives are simplified for training speed and are not tick-identical to the Rust engines. One of them, swing, was removed from the production engine in July 2026 but remains in the frozen evaluation environment; this gap between simulation and deployment is discussed in Section 5.4.

<!-- FIGURE fig_3_1_architecture -->

The evaluation asks whether a learned router adds value over this production arrangement, and the deployment supplied several defects catalogued in Section 3.7. No real funds were ever at risk.

## The Reinforcement-Learning Agent

The agent is trained with Proximal Policy Optimization (PPO; Schulman et al., 2017) and acts once per completed hourly bar.

### Actions and observations

The action space has ten discrete actions, decoded by a single mapping shared by training and every later use of the policy. Actions 0–2 select the grid engine at size multipliers of 0.5, 1.0 or 1.5. Actions 3–5 select the trend engine at the same sizes, and actions 6–8 select swing. Action 9, `GO_FLAT`, closes any open position and carries no size. Switching engines closes the current position at the previous close and activates the new engine. Choosing the same engine at a different size changes only future deployments and costs nothing.

The observation is a 25-dimensional vector. The first 17 entries are the market and time features shared with the supervised path: the 14 contract features described in Section 3.3 plus three time features. These are followed by a four-entry one-hot code for the current engine and by four account variables: the change in equity (realised plus unrealised) relative to initial equity, drawdown from peak, position notional relative to initial equity, and the normalised number of consecutive bars spent in the current engine.

An episode replays 4,300 hourly bars (about six months) from an initial equity of $10,000, and ends early if equity falls below half of its initial value. The only modelled friction is a fee of 0.1% per side on turnover, applied inside the environment's equity accounting. Slippage was not modelled: the evaluation harness has optional fee and slippage overlays, but both default to zero and neither was enabled.

### The reward function

The reward at bar $t$ is:

$$r_t = (R^{eq}_t - R^{bh}_t) - f \cdot \text{Turnover}_t/E_t - \lambda \cdot \Delta DD_t$$

where $R^{eq}_t$ is the bar's equity return, $R^{bh}_t$ is the buy-and-hold return on the underlying asset over the same bar, $f = 0.001$ per side, and $\lambda = 0.5$. The first term rewards only the excess over the passive alternative, so the agent cannot score well simply by riding a rising market.

Fees enter the reward in two places, and each is defensible on its own. First, fees are deducted from equity, because exchanges charge them and an equity curve gross of fees would overstate the account the agent would actually hold. Second, a fee term is subtracted from the reward itself, which is the standard device for discouraging excessive trading. Together, however, they count transaction cost twice: the equity return in the first term already includes the fee, and the explicit term subtracts it again. The code describes the second subtraction as a deliberate way to amplify the cost signal. Each treatment is standard, but together they make the reward unusually punitive, so the withdrawal finding of Chapter 4 is scoped to this specification.

One further property of the reward matters. On a bar where the agent is flat, its equity return is zero, so the reward reduces to the negated benchmark return, $r_t = -R^{bh}_t$. Abstaining is therefore penalised by the passive gain it gives up. The reward contains an incentive against withdrawal, and Section 4.1 shows that withdrawal occurred anyway. The explanation cannot be that participation was never rewarded.

## The Supervised Baseline

The supervised policy is a random-forest regime classifier, one per asset, whose output gates the same engines.

The training label is a trailing-window now-cast. At each bar $T$, the regime is classified over the 24 bars ending at and including $T$. The regime is danger if the maximum drawdown within the window reaches −3%; otherwise it is trending if the absolute return over the window reaches 2%; otherwise it is ranging. Danger takes precedence over trending. The label uses only past data, unlike a forward-looking variant in the same codebase, which by construction classifies on future bars.

Both policies observe identical market state. The feature contract is the same 14-feature market block that the reinforcement-learning agent observes: returns, volatility ratio, normalised ATR, trend strength (the gap between the 20- and 50-bar moving averages), ADX, RSI, volume ratio, close-location value, MACD histogram, distance to VWAP, OBV rate of change, choppiness, fractal dimension and the Aroon oscillator. The features are defined once and imported by both paths.

The classifier is a random forest of 200 fully grown trees, with a minimum of five samples per leaf, square-root feature subsampling, balanced subsample class weights and seed 42. It is trained on the first 85% of each training window and calibrated on the remaining 15%. The split is strictly temporal, and isotonic regression is fitted on the held-out later segment (Zadrozny & Elkan, 2002).

In the walk-forward evaluation the baseline is retrained for each fold, giving twelve models across the two pairs. Each model carries a fixed provenance manifest that records its training window, data hash, feature-contract hash, seed and class distribution. For example, the ETH model for the first fold is trained on data from 15 July 2024 to 10 January 2025: 3,610 rows, of which 1,592 are ranging, 877 trending and 1,141 danger.

The deployed classifiers' out-of-sample accuracies are 0.849 (ETH) and 0.870 (BNB), and 0.80–0.87 across the four live pairs; the accuracies of the twelve fold models were not recorded. These figures come from the development history: the evaluation script prints them but does not save them, and the saved model manifests record only sample counts. They are kept, with this caveat, because no conclusion depends on their exact values. The argument of Section 4.5 is that accuracy, whatever its value, did not translate into value from gating.

## The Evaluation Protocol

The evaluation is a chronological walk-forward over pinned data, illustrated in Figure 3.2.

For each pair, 17,304 hourly bars ending on 5 July 2026 are loaded, and the six folds use the first 15,910 of them; the final 1,394 bars (9 May to 5 July 2026) are not used. Each fold has a training window of 4,320 bars (about six months) and a test window of 720 bars (about one month), and successive folds are stepped by 2,160 bars (about three months). This gives six non-overlapping test windows per pair, separated by gaps of 1,440 bars, and 4,314 test bars in total (719 per fold after alignment).

Between each training window and its test window sits an embargo of 70 bars, sized to the longest feature lookback. The longest direct lookback in the feature contract is 63 bars (the OBV feature: a 14-bar difference standardised over 50 bars), and the exponentially weighted tail of the RSI falls below 1% after about 64 bars, so 70 bars bounds direct leakage. The embargo is a tolerance rather than an absolute guarantee: ADX and MACD are recursive indicators with infinite memory, and their warm-up can extend about 30 bars past the boundary. This limitation is discussed in Section 5.4.

<!-- FIGURE fig_3_2_walk_forward -->

Four rules govern every comparison.

1. Comparators are aligned by timestamp through inner joins on the bar index. Each strategy's return series for a fold contains exactly the 719 bars of that fold's test window. A boundary check confirms this on all twelve slices.
2. The supervised baseline is fold-specific. Each test window is scored only by a model trained on data that ends before it, with the embargo respected.
3. The data are pinned by end date and by content hash (Section 3.9).
4. Metrics have fixed definitions. Total return is the compounded product $\prod(1+r_t)-1$ over the per-bar series. Maximum drawdown is measured on the multiplicative equity curve within each fold and summarised by its median across folds. It is never pooled across the 1,440-bar gaps between folds.

Rules 1 and 4 came from the audit: they replaced alignment by array length, which had paired bars up to 49 hours apart, and the definitions recorded as defects 10 and 11 (Section 3.7). PPO training windows are rounded to calendar months, so the first PPO window is 4,344 bars long against the random forest's 4,320. This 24-bar difference is disclosed: the first PPO window starts 24 bars before the pinned data frame, and the out-of-sample boundary was checked to hold.

Walk-forward validation was chosen over combinatorial purged cross-validation (CPCV) for two reasons. It keeps each test window continuous in time, so serial dependence is estimated only where it really exists. It also avoids assuming that non-adjacent market periods are interchangeable. This dissertation regards that assumption as unsafe, and the project measured its consequences directly: when non-adjacent windows were wrongly pooled into one series (defect 11), headline drawdowns were inflated by factors of 2.3 to 5.5. The cost of walk-forward, fewer test paths and a smaller effective sample, is accepted knowingly. Gort et al. (2022) use a combinatorial alternative and report agents selected that way outperforming walk-forward and K-fold ones; applying CPCV is left to future work (Section 6.4).

## Statistical Treatment

### The primary test

The primary comparison is a paired mean-difference test on realised per-bar returns. It uses Newey–West heteroskedasticity- and autocorrelation-consistent (HAC) standard errors with lag 9, given by the standard rule $\lfloor 4(n/100)^{2/9} \rfloor$ at $n = 4{,}314$, and a Holm correction across the two pairs. Because the statistic is computed on the six folds joined end to end, its lags cross the gaps between folds; it is therefore reported as descriptive only, and no within-fold replacement was computed.

This is not a Diebold–Mariano test. That statistic is defined on differences in forecast loss, whereas this test compares the realised returns of two trading policies on identical bars. The audit found that an earlier analysis had reported a "Diebold–Mariano" figure that matched no implemented test; that figure was withdrawn (Appendix A, row 4).

The results are +0.43 for ETH (p = 0.665) and −0.33 for BNB (p = 0.743), both Holm-adjusted to 1.00. All comparisons between PPO and the random forest are treated as descriptive only, for two reasons. The two policies deployed very different amounts of capital (4–5% against 21–61% capital-weighted exposure). In addition, using individual bars as the unit understates clustering by fold: the procedure is evaluated only six times per pair, so a fold-level analysis would have an effective sample size close to six, and lower true power.

### Drawdown intervals

Drawdown differences are assessed with a fold-cluster bootstrap: the six per-fold drawdown differences are resampled with replacement as whole units, so nothing crosses a gap between folds. The resulting 95% intervals for the per-fold maximum-drawdown difference (PPO minus random forest) are −0.070 [−0.121, −0.020] on ETH, which excludes zero, and −0.054 [−0.138, +0.022] on BNB.

No block length is involved in this interval. A length-based block rule ($\lceil 4(n/100)^{2/9} \rceil = 10$) is used only for the secondary Sortino-ratio interval; it is not the automatic selection of Politis and White (2004), which examines the series' autocorrelation and was not implemented (register row 14).

### Power

Power is treated prospectively only. The minimum detectable effect is the smallest cumulative return difference the design could detect at 80% power and α = 0.05, computed from the HAC variance and the sample size alone. It is 81.7 percentage points on ETH and 179.3 on BNB, against observed differences of 9.66 and −11.34 points.

Power calculated at the observed effect is not reported, because it is a direct function of the p-value and adds no information beyond it (Hoenig & Heisey, 2001). The case that the study is underpowered rests instead on interval width. The fold-clustered drawdown intervals span roughly 0.10 and 0.16, against effects of about 0.05–0.07; with intervals this wide, the drawdown difference is resolved on only one of the two assets.

A design analysis at effect sizes taken from the literature (Gelman & Carlin, 2014) completes the picture. Anchored on the 1.7% per year effect of a published positive result on the S&P 500 (Xue & Ye, 2025), the design has power of about 0.05 on both pairs, a Type S (wrong-sign) error rate of 0.45–0.47, and a Type M (exaggeration) factor of roughly 60 to 100 (Table 4.6). In other words, a significant estimate at this scale would on average overstate the true effect by up to three-quarters. Detecting an effect of that size would need roughly 2,506 years (ETH) and 7,413 years (BNB) of hourly data; even at a generous 10% per year anchor (power ≈ 1, Type M ≈ 1) it needs 72 and 214 years. The method can resolve only very large claimed effects, not those typically reported.

## Diagnostics

Six diagnostics were applied to the corrected evaluation. None was invented here; each is standard in its home discipline. The contribution is a documented case in which the absence of each changed a conclusion.

### The flat-policy floor

This diagnostic scores a policy that is always flat, under the same reward and over identical timestamps, before any trained policy is interpreted. It costs one evaluation run with the action fixed to `GO_FLAT`. It comes from the practice of flooring results against a naive baseline in reinforcement-learning evaluation: the score-zero "do nothing" reference of the L2RPN power-grid competitions (Marot et al., 2021), and trivial-policy benchmarks in market-making and execution (Gašperov & Kostanjčar, 2021; Hafsi & Vittori, 2024). A targeted search of prior work did not find this specific use in trading, scoring abstention under the training reward as a test for a mis-specified reward. That is a scoped observation, not a claim of priority. Here it would have exposed the central result before any performance claim was made: the flat policy outscores the trained policy in 19 of 20 seed–fold–pair cells.

### Reward-component decomposition

This diagnostic reports the accumulated size of each term in the reward, not only the total, which requires only instrumenting a sum that is already computed at every step. It comes from the reward-decomposition literature in explainable reinforcement learning (Lin et al., 2019, 2020) and from debugging practice, which advises checking whether an agent favours one component. Here it would have shown that the drawdown-penalty term (0.310 and 0.401) was about half the size of the profit-and-loss term on ETH (−0.579) and larger than it on BNB (−0.328). It would also have exposed the fee double-count described in Section 3.2, which went unnoticed through four rounds of self-audit.

### Capital-exposure matching

This diagnostic matches baselines on capital-weighted exposure, the mean of |position|/equity, rather than on time in market, and reports both. On the trained runs, three quantities separate clearly:

- time in market, which counts bars on which an engine is active, is 58% on ETH and 54.2% on BNB;
- bars actually holding inventory are far fewer, 12.2% on ETH and 12.4% on BNB;
- capital-weighted exposure averages 4.3% on ETH and 5.2% on BNB.

The diagnostic comes from portfolio attribution (Brinson, Hood & Beebower, 1986; Cremers & Petajisto, 2009; Frazzini & Pedersen, 2014). Its closest structural relative is selective prediction, where the observation that abstaining improves metrics is foundational, and reporting coverage is the established remedy (Chow, 1970; Geifman & El-Yaniv, 2017; Geifman & El-Yaniv, 2019). Here it reversed a stated conclusion. PPO's apparent drawdown advantage, which had survived matching under a mixed-up definition of exposure, ranks at the 100th percentile, the worst position, against random entries matched on the same capital exposure, on both pairs, on the pooled drawdown basis on which the matching was run.

### Fold-aware pooling

This diagnostic never joins non-adjacent test windows into one equity curve or dependence structure. It comes from purging and embargo practice in financial cross-validation. Here it would have caught the 2.3–5.5-fold inflation of pooled drawdown and the cross-boundary joins that corrupted the HAC lags and bootstrap blocks (Section 3.5).

### Power reporting

This diagnostic states the minimum detectable effect alongside any null result, together with the effective sample size after clustering. Both follow in closed form from the variance estimate that is already computed. It comes from power analysis in clinical trials and applied statistics. Here it would have prevented any claim that the returns were "at parity": parity is not supported when the design cannot tell it apart from differences an order of magnitude larger than those observed.

### Seed-distribution reporting

This diagnostic never reports a reinforcement-learning result from a single seed, following the reproducibility literature in deep reinforcement learning. Here it would have caught a headline resting on a seed at the unfavourable end of its distribution: within a single fold, the choice of seed changes return by 28 percentage points and drawdown by a factor of 45, and the paired statistic changes sign across seeds (Section 3.7).

## Evaluation Failure Modes and Their Effect on Inference

The protocol described above is what remained after an audit carried out between 15 and 23 August 2026. The audit found eighteen evaluation defects, six of which changed a conclusion. Table 3.1 lists these six, and the paragraphs that follow explain each. The complete register is in Appendix A. It also records four later findings (rows 19–22), none of which changed any reported figure.

**Table 3.1 — The six defects that changed a conclusion**

| # | Defect | Effect on the result | How it was caught |
|---|---|---|---|
| 5 | Additive (cumulative-sum) drawdown equity | Misstated drawdown (ETH buy-and-hold 0.353 additive against 0.511 multiplicative) | Self-audit |
| 7 | Conflated exposure definitions | ETH exposure-match verdict reversed | Self-audit |
| 8 | Overlapping-window cost sum | 2,243 pp: impossible against a +38% window | Self-audit |
| 9 | Reliance on a single seed | 28 pp within-fold swing between seeds hidden | Self-audit |
| 10 | Arithmetic (uncompounded) total return | BNB buy-and-hold sign flip, +3.78% → −4.27% | Independent review |
| 11 | Pooling across the 1,440-bar fold gaps | Drawdown inflated up to 5.5×; dependence estimates corrupted | Independent review |

### Arithmetic versus compounded returns

The walk-forward harness stored total return as the arithmetic sum of per-bar returns, while the single-window evaluation compounded the equity curve. The audit confirmed that all six stored values matched the sum exactly, and that the compounded figures differ materially:

- ETH buy-and-hold +44.83% → +37.97%; PPO −13.10% → −12.78%; random forest −22.32% → −21.17%;
- BNB buy-and-hold +3.78% → −4.27%, a change of sign: passive exposure to BNB did not gain, it lost.

A sum ignores compounding, and over long series with large swings the two methods diverge and can fall on opposite sides of zero. Inference was biased in favour of passive exposure on BNB, and every dependent quantity shifted with it.

### Pooling across non-adjacent folds

The six test windows are separated by gaps of 1,440 bars, but the "pooled" statistics joined the six per-fold series into one, creating false adjacency at five boundaries. Partial drawdowns at the end of one fold and the start of the next merged into single drawdowns that no investor would have experienced. This inflated pooled maximum drawdown by 2.3 to 5.5 times: PPO's pooled drawdown was 0.165 on ETH against a per-fold median of 0.032, and 0.293 on BNB against 0.053. At the same time, HAC lags 1–9 linked bars that were months apart, and bootstrap blocks spanned the gaps, corrupting every estimate of serial dependence. The defect survived five rounds of self-audit, including the round that fixed the drawdown calculation right next to it, because the false premise that the folds were contiguous was inherited rather than computed.

### Conflated exposure definitions

Three different quantities had been used interchangeably: time in market (any engine active), the share of bars with non-zero returns, and capital-weighted exposure (mean |position|/equity). Grid bars with no inventory count as deployed under the first definition but hold no capital. A policy reported at "58% exposure" was in fact deploying 4.3% of its capital, and the exposure-matched baselines had been matched on inconsistent definitions. When the baselines were re-matched on capital-weighted exposure, the random-entry median drawdowns were 0.0509 (ETH) and 0.0508 (BNB). On the same pooled basis, PPO's were 0.165 and 0.293, the worst position (100th percentile) on both pairs; the corresponding per-fold medians are 0.032 and 0.053. This reversed the earlier ETH verdict that PPO's drawdown advantage survived exposure matching. The error spreads silently because each quantity is well defined on its own.

### The mislabelled test

An earlier analysis reported a "Diebold–Mariano" result (DM = 1.48, p = 0.14) in support of a claim of no difference. The figure matched no implemented test, and in any case the Diebold–Mariano statistic is defined on differences in forecast loss, not on differences in realised returns between trading policies. The bias works through borrowed authority: a mislabelled statistic imports the assumptions and literature of a different test, and a reader cannot check either. The claim was withdrawn and the statistic renamed (Section 3.5), so a conclusion that had been stated with a named statistical test lost its support.

### Reliance on a single seed

Every headline PPO figure originally came from one seed (42). A five-seed sweep on two folds shows that within a single fold, the seed alone changes cumulative return by up to 28 percentage points (BNB, fourth fold: −22.99% for seed 7 against +4.69% for seed 123) and maximum drawdown by a factor of 45 (0.007 to 0.312). The paired statistic also changes sign across seeds (BNB range −0.92 to +0.80). Seed variance dominates the difference between methods. The bias is epistemic rather than directional: a comparison based on one seed supports no claim about methods in either direction. Seed 42 was fixed in code before any result was observed and sits at the unfavourable end of its distribution on BNB, which rules out seed selection but does not make the single-seed result interpretable.

### The overlapping-window opportunity cost

The cost of the regime gate's false danger warnings was first calculated by summing 24-bar forward returns across hourly signal bars, so each price move was counted in up to 24 overlapping windows. The result was 2,242.9 percentage points (ETH) and 1,183.2 (BNB) of foregone return, over periods in which buy-and-hold returned +38.0% and −4.3% (compounded). These are impossible as portfolio costs, because sums over overlapping windows grow without bound. They were replaced by three measured quantities:

- the mean forward return per false danger signal: +3.34% (ETH) and +3.88% (BNB);
- the gap over non-overlapping 24-bar blocks: 0.96 and 0.72 percentage points per block;
- the realised portfolio-level gap between the gated strategy and buy-and-hold: −59.1 and −16.0 percentage points, computed from the compounded totals in Tables 4.1 and 4.2 (earlier versions used uncompounded totals, −67.1 and −20.8; register row 24).

The direction of the argument survived the correction; its magnitude did not.

## Discovery Modes

The way the eighteen evaluation defects were found is itself a methodological finding. They fall into four discovery modes.

1. Self-audit (nine defects). These were arithmetic or definitional errors in recorded numbers. A check of the recorded numbers finds errors in the recorded numbers.
2. Independent review (six defects). These were inherited premises. Five were found by an external review of the evaluation, and one by an operational audit on 23 August 2026. The assumption that the folds were contiguous survived five rounds of self-audit precisely because it was a premise rather than a calculation.
3. Observing system behaviour (two defects). These were cases of data that had never been recorded. Self-audit checks what is recorded; it cannot check what was never written down.
4. Re-running the reproduction guide (one defect). This was documentation drift: a correct instruction had become wrong after a definition was corrected downstream. A further instance of the same kind was found later (Appendix A, row 21), when the guide's stated library versions turned out to differ from the run manifest that the same sentence cited. Drift is the only class that recurs after detection, because every correction made after a piece of text was written can make that text wrong again.

Two further defects came to light in September 2026, after the evaluation was frozen. This dissertation treats them as a fifth mode: investigation driven by a symptom.

- From 31 May to 18 September 2026 (110 days), the live system's WebSocket market-data client streamed from Binance's test network instead of the production feed, because of a configuration wiring error. The paper-trading record was therefore priced partly from test-network data, but no evaluation metric changed. The defect came to light only because a performance review noticed that connections were being reset about every three minutes, and investigated.
- In the same period, the continuous-integration pipeline had been failing since August 2026 while deployments continued by other means. This quality gate was firing with nobody watching, and was found only when the pipeline's status was examined in September.

These are rows 19 and 20 of the register (Appendix A).

A fifth mode is justified by the kind of escape involved, which differs from all four earlier modes. In each of the eighteen evaluation defects, something in the record was wrong: a number, a premise, a missing record or an instruction. In both September findings the evaluation record was correct: the feed defect was a wiring error present since deployment, and the pipeline failure combined a dependency upgrade with a test left out of date by an earlier correction. No audit of the evaluation record, however independent, could have found either one, because neither was in the evaluation record. Both were caught only because the defect eventually produced a visible symptom, a reset pattern or a failing pipeline, which was noticed during an investigation undertaken for another purpose.

The parallel with the central argument is direct: the feed defect was invisible because nothing compared the feed with reality, just as concealed abstention was invisible because nothing reported capital-weighted exposure. Neither finding affected any reported figure (Section 3.9). They extend the register from evaluation defects to operational defects, which is reasonable because the system under study is deployed, and they are reported separately from the eighteen evaluation defects.

## Reproducibility

Every reported evaluation result can be traced to artefacts saved in the project repository.

The raw evidence consists of 36 per-bar return series, one each for PPO, the fold-specific random forest and buy-and-hold, per fold and per pair, together with 24 per-bar exposure traces for the trained and untrained policies. The traces record the action, position value, inventory, turnover and grid-level crossings at every bar. The twelve fold-specific random-forest models carry provenance manifests (training window, data hash, feature-contract hash, seed and class distribution), and the PPO models carry equivalent records. A run manifest records the evaluated code version, the library versions (scikit-learn 1.6.1, numpy 2.5.2, pandas 3.0.5, stable-baselines3 2.9.0, torch 2.13.0) and the seeds (42 throughout).

A reproduction guide (REPRODUCE.md in the code archive, summarised in Appendix B) maps 16 headline claims to exact fields in these artefacts and gives short commands that recompute them from the raw series; all were re-run on 23 August 2026 and matched exactly.

All training and evaluation data are one-hour candles for ETHUSDT and BNBUSDT, 17,304 bars per pair, ending on 5 July 2026, with a SHA-256 hash recorded for each pair in the run manifest. The source is Binance's public data archive, the same data the exchange's REST interface serves. The test-network feed problem described in Section 3.8 affected only the live paper-trading feed, and no reported result used that feed. This independence was verified rather than assumed: on 19 September 2026 both recorded hashes were recomputed from the archive-derived cache over the pinned window and matched the manifest exactly (17,304 bars per pair). One qualification applies: the recomputation used the local cache derived from the archive, not a fresh download.

Some things cannot be reproduced, and they are stated openly:

- Provenance is not from a single snapshot. The run manifest, the PPO records and the random-forest manifests cite three different code versions; a comparison showed them to be identical in substance for training and evaluation.
- The trained model files are not in the repository or the code archive. They exist only on the author's machine, with their hashes recorded, and can otherwise only be regenerated from the recorded seeds and windows.
- Sensitivity to library versions has not been measured beyond one prediction check between scikit-learn 1.6.1 and 1.8.0.
- The market-data source is outside the project's control.
- The original training environment no longer exists.

## Ethical and Legal Considerations

The evaluation, and the deployment that hosted it, involved no real money. The system trades in paper mode, and the reinforcement-learning results are historical replays of public market data. No personal data is collected or processed anywhere in the pipeline. The legal exposure considered was therefore operational rather than financial.

That exposure was audited. A security review on 23 August 2026 removed two unused public access rules (ports 80 and 443) from the server's firewall, leaving no inbound access, and confirmed from outside that the engine's port had never been reachable from the internet.

Risks in the deployment layer were documented, and deliberately left unchanged while the evaluation ran, so that the evidence chain described in Section 3.9 stayed intact. They were:

- a latent duplicate-sell defect in the live-mode force-flat path;
- trend-engine risk settings in the configuration that no code read, so the trend engine had no position cap of its own;
- a retraining workflow that did not run;
- a drift monitor that observed but never reported;
- a regime-cache expiry time equal to the push interval, which produced regular windows in which the gate fell back to its default;
- routing that is global while regimes are per pair.

After the evaluation was complete, all of these except the global routing were fixed in the live system in November 2026, together with a newly found defect in the paper account (Section 5.4.1). The fixes changed only the live trading code and its deployment. The evaluation code and its results are unchanged; the one later experiment (Section 4.6) used the frozen code with a single switch that is off by default.
