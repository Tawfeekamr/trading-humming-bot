# Chapter 3 — Methodology

*Draft for revision. Every figure in this chapter is copied from the committed
record — FIX_REPORT.md (sixteen batches), REPRODUCE.md, the artifacts under
`reports/`, or the frozen source under `src/`. Nothing here is recomputed.
Where a needed figure is not in the record, the gap is marked
`[FIGURE NOT IN RECORD: …]` rather than derived.*

Section 3.1 names the system under study; Sections 3.2 and 3.3 specify the
two policies; Sections 3.4 and 3.5 define the evaluation protocol and its
statistical treatment; Sections 3.6 to 3.8 document the six diagnostics,
the evaluation defects, and the modes by which they were found; Sections
3.9 and 3.10 close with the evidence chain and the ethical position. The
method is presented as it ended, after correction — the correction trail
is part of the object of study, treated in 3.6 to 3.8. The complete
21-row defect register — eighteen evaluation defects plus three later
findings — is in Appendix 3.A, excluded from the word count.

## 3.1 The system under study

The dissertation is evaluated on, and the failure modes were discovered in, a
live-deployed trading system; the system is the apparatus, not the result. It
has three layers. A Python machine-learning layer trains one regime classifier
per asset and a sidecar process pushes each classifier's predictions into a
cache inside the execution engine every 180 seconds over an internal HTTP
endpoint. A Rust execution engine (`trading-engine-core`) runs two production
strategy engines — a grid engine that quotes geometrically spaced buy and sell
levels around an anchor, and a trend engine that enters directionally and
exits on a chandelier trailing stop — on four pairs (ETH, BNB, XRP, DOGE),
in paper-trading mode on a single AWS EC2 instance in Tokyo. The third layer
is the research stack evaluated here: a Python Gymnasium environment that
replays one-hour OHLCV bars and exposes simplified numpy implementations of
the same engine primitives to a reinforcement-learning agent. The primitives
are deliberately simplified for training speed and are not tick-identical to
the Rust engines; one of them (swing) was deleted from the production path in
July 2026 while remaining in the frozen evaluation environment, a
simulation-to-deployment gap documented in Section 5.3. The apparatus matters
two-fold: the evaluation asks whether a learned router adds value over this
production arrangement, and the deployment context supplied several defects
that Section 3.7 catalogues. No real funds were ever at risk: the deployment
trades in paper mode only.

## 3.2 The reinforcement-learning agent

The agent acts once per completed hourly bar. The action space is
`Discrete(10)`, decoded by one mapping shared by training and every
downstream consumer (`src/rl/action_map.py:15-26`): actions 0–2 select the
grid engine at size multiplier 0.5, 1.0 or 1.5; actions 3–5 select trend at
the same sizes; actions 6–8 select swing; action 9 is `GO_FLAT`, which
closes any open position and carries no size. Switching engines closes the
current position at the previous close and activates the new engine;
re-selecting the same engine at a different size changes only future
deployments and costs nothing.

The observation is a 25-dimensional vector (`src/rl/env.py:16-28`): the
first 17 entries are the market-and-time feature block shared verbatim with
the supervised path (the 14 contract features of Section 3.3 plus 3 time
features); then a 4-entry one-hot for the current engine; then four account
variables — unrealised P&L relative to initial equity, drawdown-from-peak,
position notional relative to initial equity, and the normalised count of
consecutive bars in the current engine. An episode replays 4,300 hourly
bars (about six months) from an initial equity of $10,000 and terminates if
equity falls below half its initial value (`env.py:364`). The only modelled
friction is a 0.1%-per-side fee on turnover inside the environment's equity
accounting; the walk-forward harness's additional fee and slippage overlays
default to zero and the corrected run passed neither flag, so no slippage
was modelled.

The reward function (`src/rl/env.py:345-358`) is, precisely:

$$r_t = (R^{eq}_t - R^{bh}_t) - f \cdot \text{Turnover}_t/E_t - \lambda \cdot \Delta DD_t$$

where $R^{eq}_t$ is the bar's equity return, $R^{bh}_t$ the bar's
buy-and-hold return on the underlying, $f = 0.001$ per side (`env.py:76`),
and $\lambda = 0.5$ (`env.py:77`). The first term rewards only excess over
the passive alternative, so the agent cannot win by riding a rising market.

Fees enter in two places, each individually defensible. First, fees are
deducted from equity (`env.py:333-334`): exchanges charge them, and an
equity curve gross of fees would overstate the account the agent would
actually hold. Second, a fee term is subtracted from the reward itself
(`env.py:354-357`), the standard anti-churn device against the classic
reinforce-and-trade-to-death failure. Their interaction is that transaction
cost is counted twice: the equity return in the first term already embeds
the fee drag, and the explicit term subtracts it again — the code's own
comment calls the second subtraction "a deliberate anti-churn knob" that
amplifies "the cost signal a second time" (`env.py:346-350`). This is
described, not judged: each treatment alone is standard, and their
combination makes the reward unusually punitive, which scopes the
withdrawal finding of Chapter 4 to this specification.

One further property matters. On a bar where the agent is flat, its equity
return is zero, so the reward reduces exactly to the negated benchmark
return, $r_t = -R^{bh}_t$: abstention is penalised by the passive gain
foregone. The reward therefore contains an anti-withdrawal incentive, and
Chapter 4.1 documents withdrawal occurring anyway — the diagnosis cannot be
that participation was never rewarded.

## 3.3 The supervised baseline

The supervised policy is a per-asset random-forest regime classifier whose
output gates the same engines. The label is a trailing-window now-cast
(`src/data/label_generation.py:86-138`): at each bar $T$, the regime is
classified over the past 24 bars ending at and including $T$ — **danger** if
the maximum drawdown within the window reaches −3%, else **trending** if the
absolute window return reaches 2%, else **ranging**. Danger takes precedence
over trending. The construction is fully deterministic from past data, unlike
the forward-looking variant retained in the same module, which classifies on
future bars by construction. The feature contract is the same
14-feature market block the RL agent observes — returns, volatility ratio,
normalised ATR, ADX, RSI, volume ratio, close-location value, MACD histogram,
distance to VWAP, OBV rate of change, choppiness, fractal dimension, and the
Aroon oscillator — defined once in `src/data/feature_contract.py` and imported
by both paths, so the two policies observe identical market state.

The classifier is a random forest of 200 depth-full trees
(`min_samples_leaf=5`, `sqrt` feature subsampling, balanced subsample class
weights, seed 42), trained on the first 85% of each training window by time
and calibrated on the remaining 15%: a strictly temporal split, with isotonic
regression fitted on the held-out later segment (Zadrozny & Elkan, 2002). In
the walk-forward evaluation the baseline is retrained per fold — twelve
models in total across the two pairs — each carrying an immutable provenance
manifest recording its training window, data SHA-256, feature-contract hash,
seed and class distribution (for example, ETH fold 0 trains on 2024-07-15 to
2025-01-10, 3,610 rows, classes 1,592 ranging / 877 trending / 1,141 danger).

The classifiers' out-of-sample accuracies are 0.80–0.87 per asset, labelled
for what they are: commit-history claims — the evaluation script prints them
and persists nothing, and no committed artifact contains them (the model
manifests record only sample counts). They are retained with this caveat
rather than withdrawn because no conclusion rests on their precise values;
Chapter 4.5's argument is that accuracy, at whatever value, did not convert
into gating value.[^accuracy]

[^accuracy]: The record carries two ranges, both correct at their own scope:
FIX_REPORT states 0.78–0.87, spanning the four-pair training set (ETH, BNB,
XRP, DOGE); the manuscript and this chapter state 0.80–0.87, the two pairs
the walk-forward evaluation trained and scored (ETH, BNB). This chapter uses
the narrower range because Section 3.3 describes the walk-forward baseline,
whose evaluated universe is ETH and BNB only.

## 3.4 The evaluation protocol

The evaluation is a chronological walk-forward over pinned data. Per pair,
17,304 hourly bars ending at the pinned date 2026-07-05 are split into six
folds: training windows of 4,320 bars (about six months), test windows of
720 bars (about one month), and steps of 2,160 bars (about three months),
giving six non-overlapping test windows per pair separated by 1,440-bar gaps
and 4,314 pooled test bars in total (719 per fold after alignment). Between
each training and test window sits a 70-bar embargo, sized to the maximum
feature lookback: the longest rolling window in the contract is 50 bars
(SMA-50, VWAP-50, OBV-50) and the RSI's exponentially-weighted tail falls
below 1% after roughly 64 bars, so 70 bars bounds direct leakage. The
embargo is a decay tolerance rather than an absolute guarantee — ADX and
MACD are infinite-support recurrences and the warmup prefix can reach about
30 bars past the boundary — a limitation stated in Section 5.3.

Four canonical rules define every comparison. First, comparators are aligned
by timestamp through inner joins on the bar index: each strategy's return
series per fold contains exactly the 719 bars of that fold's test window,
verified by a boundary assertion on all twelve slices that fired on none of
them in the corrected run. This replaced the earlier array-length
truncation, which had paired bars up to 49 hours apart. Second, the
supervised baseline is fold-specific: each test window is scored only by a
model trained on data ending before it, with the embargo respected. Third,
the data are pinned by end date and by content hash (Section 3.9). Fourth,
canonical metric definitions apply throughout: total return is the compounded
chain $\prod(1+r_t)-1$ over the pooled per-bar series, and maximum drawdown is
measured on the multiplicative equity curve within each fold and summarised
as a per-fold distribution with its median — never pooled across the
1,440-bar gaps between folds. Both definitions were canonical only after
correction; the defects they replaced are items 10 and 11 of Section 3.7.
PPO training windows round to calendar months, so the PPO fold-0 window is
4,344 bars against the RF's 4,320 — a disclosed 24-bar asymmetry, with the
out-of-sample boundary verified to hold.

Walk-forward was chosen over combinatorial purged cross-validation (CPCV)
because it preserves temporal contiguity within each test window — serial
dependence is estimated only where it exists in time — and does not require
assuming that non-adjacent market segments are exchangeable, an assumption
this dissertation regards as unsafe and whose consequence this project
measured directly when its own pooling defect (Section 3.7, item 11)
treated non-adjacent windows as one series and inflated headline drawdowns
by factors of 2.3 to 5.5. The cost — fewer test paths and lower effective
sample size — is accepted knowingly; arXiv:2209.05559 documents the
alternative and reports CPCV-trained agents outperforming walk-forward
ones; CPCV is future work (Section 5.4).

## 3.5 Statistical treatment

The primary comparison uses a paired mean-difference test on realised per-bar
returns with Newey–West heteroskedasticity-and-autocorrelation-consistent
standard errors, lag 9 at $n = 4{,}314$ by the standard rule
$\lfloor 4(n/100)^{2/9} \rfloor$, Holm-corrected across the two pairs. It is
stated explicitly that this is **not** a Diebold–Mariano test: that
statistic is defined on forecast-loss differentials, whereas this test
compares realised returns of two trading policies on identical bars. An
earlier version mislabelled the statistic as Diebold–Mariano and reported a
figure (DM = 1.48, p = 0.14) that corresponded to no implemented test —
untraceable to code, and withdrawn (item 4, Section 3.7). Under the
corrected protocol the results are ETH +0.43 (p = 0.665) and BNB −0.33
(p = 0.743), Holm-adjusted to 1.00 on both pairs. All such PPO-versus-RF
statistics are reported as descriptive only: the two policies operated at
materially different capital deployment (4–5% versus 21–61%
capital-weighted exposure), and the per-bar unit understates clustering by
fold — the procedure is evaluated six times per pair, so a fold-level
analysis would have an effective $n$ near six and lower true power.

Drawdown differences use a Politis–Romano stationary bootstrap on paired
series, with fold-clustered resampling: the six per-fold drawdown values are
resampled as units, so no resampled block spans a fold gap. The resulting 95%
intervals for the per-fold maximum-drawdown difference (PPO minus RF) are
−0.070 [−0.121, −0.020] on ETH — excluding zero — and −0.054 [−0.138, +0.022]
on BNB. The block length, 10, comes from a length-only heuristic,
$\lceil 4(n/100)^{2/9} \rceil$: it is stated as such rather than as a
Politis–White automatic selection, which inspects the series'
autocorrelation structure and was never implemented; the intervals are
conditional on that heuristic choice.

Power is handled prospectively only. The minimum detectable effect — the
smallest cumulative return difference the design could detect at 80% power
and α = 0.05, computed from the HAC variance and the sample size alone, and
verified in code to use no observed-effect term — is 81.7 percentage points
on ETH and 179.3 on BNB, against observed differences of 9.66 and −11.34
points. An earlier version also reported *achieved* power (7.2% and 6.2%)
and an observed-anchored sample-size requirement (21 and 36 years); both
were retracted because observed power is a deterministic function of the
p-value and adds no information beyond it (Hoenig & Heisey, 2001). The
underpowering argument instead rests on interval width: the fold-clustered
drawdown intervals span roughly 0.10 and 0.16 against effects of order
0.05–0.13 — a sample whose interval is wider than the effect cannot resolve
that effect.

A design analysis at literature-anchored effect sizes (Gelman & Carlin,
2014; `reports/design_analysis.json`) completes the treatment. Anchored on
the 1.7%-per-year effect of a published positive S&P 500 result
(arXiv:2510.06466), the design has power 0.73 (ETH) and 0.32 (BNB), a
Type S (sign-error) rate of approximately zero to 0.0008, and a Type M
(exaggeration) factor of 1.17× and 1.74× — a significant estimate at this
scale would be inflated by up to three-quarters again on average. Detecting
an effect of that size would require on the order of 2,506 years (ETH) and
7,413 years (BNB) of hourly data; at a generous 10%-per-year crypto anchor
the design is adequate (power ≈ 1, Type M ≈ 1), requiring 72 and 214 years.
The implication strengthens the underpowering finding: this methodology can
resolve very large claimed effects, not the ones typically reported.

## 3.6 Diagnostics

Six diagnostics were applied to this evaluation after correction. None was
invented here; each is standard in an origin discipline, and the
contribution is the documented case in which each one's absence changed a
conclusion. Each is stated with what it measures, how it is computed, its
origin, and what it would have caught here.

**The flat-policy floor** scores a permanently-flat policy under the same
reward, over identical timestamps, before any trained policy is interpreted
— one evaluation rollout with actions pinned to `GO_FLAT`. The origin is
naive-baseline flooring in RL evaluation practice: the score-zero
do-nothing reference of the L2RPN power-grid competitions (Marot et al.,
2021) and trivial-policy benchmarks in market-making and execution RL
(Gašperov & Kostanjčar, 2021; Hafsi & Vittori, 2024). An adversarial
prior-art search found the specific use — scoring abstention *under the
training reward*, as a reward-misspecification test — unclaimed in trading;
a scoped observation, not a priority claim. Here it would have caught the
central result before any performance claim was made: flat outscores
trained in 19 of 20 seed-fold-pair cells.

**Reward-component decomposition** reports the accumulated magnitude of each
reward term, not only the total — instrumentation of a sum already computed
per step. Its origin is the explainable-RL reward-decomposition literature
(Distributional Reward Decomposition, NeurIPS 2019; RD2, NeurIPS 2020) and
RL debugging practice that instructs checking whether an agent favours one
component; an earlier draft's attribution to econometric model diagnostics
was a wrong-discipline attribution, superseded. Here it would have exposed
that the drawdown-penalty term (0.310 and 0.401) rivals the entire PnL term
(−0.579 and −0.328), and would have surfaced the fee double-count of
Section 3.2, which passed unnoticed through four self-audit passes.

**Capital-exposure matching** matches baselines on capital-weighted exposure
— mean |position|/equity, one environment field — rather than time-in-market,
and reports both. Its origins are portfolio attribution (Brinson, Hood &
Beebower, 1986; Cremers & Petajisto, 2009; Frazzini & Pedersen, 2014) and,
structurally, selective prediction, where abstention improving metrics is
the founding observation and coverage reporting the established cure (Chow,
1970; Geifman & El-Yaniv, 2017; SelectiveNet, 2019). Here it reversed a
stated conclusion: PPO's apparent drawdown advantage, which had survived
matching under a conflated definition, sits at the 100th percentile — the
worst — against random entries matched on the same capital exposure, on
both pairs.

**Fold-aware pooling** refuses to concatenate non-adjacent test windows into
one equity path or one dependence structure; fold indices, already present,
carry the grouping. Its origin is purging and embargo practice in
quantitative-finance cross-validation. Here it would have caught a
five-fold inflation of pooled maximum drawdown (factors 2.3–5.5) and the
boundary-crossing joins that corrupted the HAC lags and bootstrap blocks of
Section 3.5.

**Power reporting** states the minimum detectable effect alongside any null
result, and the effective sample size after clustering — closed-form from
the variance estimate already computed. Its origin is power analysis in
clinical trials and applied statistics. Here it would have prevented every
"return parity" statement this report once made: parity is not supported
when the design cannot distinguish it from differences an order of
magnitude larger than those observed.

**Seed-distribution reporting** never reports a single-seed RL result;
training already amortises across seeds, and reporting is a table. Its
origin is the deep-RL reproducibility literature. Here it would have caught
a headline resting on the seed at the unfavourable end of its distribution:
seed choice swings within-fold return by 27 percentage points and drawdown
by a factor of 45, and the paired statistic crosses zero across seeds
(Section 3.7).

## 3.7 Evaluation failure modes and their effect on inference

The protocol described above is the survivor of eighteen documented
evaluation defects, found in sixteen corrective batches between 15 and 23
August 2026; six flipped a conclusion. The audit trail is FIX_REPORT.md.
Table 3.1 summarises the six that changed what could be concluded — the
paragraphs that follow detail each — and the complete register, all
eighteen evaluation defects plus three later findings (rows 19–21, added
and ratified 2026-09-19), is in Appendix 3.A.

Table 3.1 — The six defects that flipped a conclusion

| # | Defect | Effect on the figure | Caught by |
|---|---|---|---|
| 5 | Additive (cumsum) MaxDD equity | overstated drawdown (ETH B&H 0.353→0.511) | SELF |
| 7 | Conflated exposure definitions | ETH exposure-match verdict reversed | SELF |
| 8 | Overlapping-window cost sum | 2,243pp impossible vs a +38% window | SELF |
| 9 | Single-seed reliance | 27pp within-fold seed swing hidden | SELF |
| 10 | Arithmetic (uncompounded) total return | BNB B&H sign flip +3.78%→−4.27% | IND. REVIEW |
| 11 | Pooling across 1,440-bar fold gaps | MaxDD inflated up to 5.5×; joins corrupted | IND. REVIEW |

**Arithmetic versus compounded returns.** The walk-forward harness stored
total return as the arithmetic sum of per-bar returns
(`evaluation_report.py:54`), while the single-window evaluation path
compounded the equity curve (`evaluate.py:301`). Batch 7 verified that all
six stored values matched the sum exactly, and that the compounded chain
differs materially: ETH buy-and-hold +44.83% → +37.97%, PPO −13.10% →
−12.78%, RF −22.32% → −21.17%; BNB buy-and-hold +3.78% → **−4.27%, a sign
reversal** — passive exposure on BNB did not gain, it lost. A sum treats
each bar's return as additive on initial capital and ignores compounding;
over long series with large intermediate swings the two constructions
diverge and can cross zero. Inference was biased toward flattering passive
exposure on BNB, and every dependent quantity (the MDE conversion, the
observed differences) shifted with it.

**Pooling across non-adjacent folds.** The six test windows are separated by
1,440-bar gaps, but "pooled" statistics concatenated the six per-fold series
into one, creating artificial adjacency at five boundaries. Partial
drawdowns at the end of one fold and the start of the next combined into
single "drawdowns" no investor would have experienced — inflating pooled
MaxDD by 2.3× to 5.5× (ETH PPO 0.165 pooled versus a 0.032 per-fold median;
BNB PPO 0.293 versus 0.053) — while HAC lags 1–9 coupled bars months apart
and bootstrap blocks spanned the gaps, corrupting every serial-dependence
estimate. The defect survived four self-directed corrective passes,
including the pass that fixed the drawdown *construction* directly adjacent
to it, because the false premise — that the folds were contiguous — was
inherited, not computed.

**Conflated exposure definitions.** Three distinct quantities were used
interchangeably: time-in-market (engine not flat), the share of bars with
non-zero returns, and capital-weighted exposure (mean |position|/equity).
Grid bars with zero inventory count as "deployed" in the first but hold no
capital, so a policy reported at "58% exposure" was in fact deploying 4.3%
of capital, and the exposure-matched baselines were matched on inconsistent
definitions. Re-matched on capital-weighted exposure, the random-entry
medians are 0.0509 (ETH) and 0.0508 (BNB) against PPO's 0.165 and 0.293 —
the worst, 100th-percentile position on both pairs — reversing the
previously stated ETH verdict that PPO's drawdown advantage "survives
exposure matching." Matching a baseline on a quantity that is not the
risk-bearing quantity compares policies that are not comparable, and the
error propagates silently because each quantity is individually
well-defined.

**The mislabelled test.** The manuscript reported a "Diebold–Mariano"
result (DM = 1.48, p = 0.14) supporting a claim of no difference. The
figure corresponded to no implemented test — it was untraceable to code —
and the Diebold–Mariano statistic is in any case defined on forecast-loss
differentials, not on realised-return differences between trading policies.
The mechanism of bias is borrowed authority: a mislabelled statistic imports
the assumptions and the literature of a different test, and a reader cannot
check either. The claim was withdrawn and the implemented statistic renamed
(Section 3.5); a conclusion stated with a named inferential warrant lost it.

**Single-seed reliance.** Every headline PPO figure came from one seed (42).
The five-seed sweep on folds 0 and 3 shows that within a single fold, seed
alone swings cumulative return by up to 27 percentage points (BNB fold 3:
−22.99% for seed 7 against +3.95% for seed 999) and maximum drawdown by a
factor of 45 (0.007 to 0.312), and that the paired statistic crosses zero
across seeds (BNB range −0.92 to +0.80). Seed variance dominates the method
difference, and the bias is epistemic rather than directional: a
single-seed comparison supports no method-level claim in either direction.
Seed 42 was the pre-registered default, fixed in code before any result was
observed, and sits at the unfavourable end of its distribution on BNB —
foreclosing seed-selection explanations without rescuing
interpretability.

**The overlapping-window opportunity cost.** The cost of the regime gate's
false DANGER warnings was first computed by summing 24-bar forward returns
across hourly signal bars, so each price move was counted in up to 24
overlapping windows: 2,242.9 percentage points (ETH) and 1,183.2 (BNB) of
"foregone return" on windows where buy-and-hold returned +44.8% and +3.8% —
impossible as portfolio costs, since multiply-counted sums inflate without
bound. The retracted figures were replaced by three measured quantities:
per-signal mean forward return on false DANGER signals (+3.34% and +3.88%),
non-overlapping 24-bar block gaps (0.96pp and 0.72pp per block), and the
realised portfolio-level gap between the gated strategy and buy-and-hold
(−67.1pp and −20.8pp). The direction of the argument survived; the
magnitude did not.

## 3.8 Discovery modes

The eighteen evaluation defects distribute across four discovery modes, and
the distribution is itself a methodological finding. Nine were arithmetic or
definitional errors in recorded numbers, caught by self-audit — a check on
the recorded numbers finds errors in the recorded numbers. Six were
inherited premises, caught only by independent review — five by the review
that motivated Batch 7 and one by the 2026-08-23 operational audit; the
contiguous-folds assumption survived five self-directed passes precisely
because it was a premise rather than a computation. Two were unrecorded
data, caught by observing system behaviour rather than artifacts —
self-audit checks what is recorded and cannot check what was never written
down. One was documentation drift, caught by re-running this project's own
reproduction guide: a correct instruction had become wrong when a
definition was corrected downstream. A second instance of that drift class
joined the register on 2026-09-19 (row 21): the guide's stated library
versions had silently diverged from the run manifest the same sentence
cited — found, like the first, by reading the guide against the artifacts.
Drift is the one class that recurs after detection, because every
correction made downstream of written text can invalidate the text again.

Two further defects surfaced in September 2026, after the freeze, and this
dissertation's verdict is that they constitute a **fifth mode:
symptom-driven operational investigation**. From 31 May to 18 September 2026
(110 days) the production system's WebSocket market-data client streamed
from the testnet endpoint, a config-flag wiring error (fixed in commit
`a404634`, 2026-09-19): the paper-trading record was priced partly off
testnet data, and no metric moved — the defect surfaced only because a
performance audit noticed a three-minute connection-reset cadence and
investigated. In the same period the CI pipeline had been red since August
2026 while deploys continued by other means — a quality gate firing
unmonitored, found only when the September session examined pipeline status
(green restored in `e011b3e`, 2026-09-19). These are rows 19 and 20 of the
failure register (Appendix 3.A), ratified by the author on 2026-09-19.

The justification for a fifth mode is the class of escape, distinct from
all four predecessors: in each of the eighteen evaluation defects,
something in the record was wrong — a number, a premise, a persistence
failure, an instruction. In both September findings the record was correct;
the *running system* had diverged from its configuration without any
artifact becoming false. No audit of the evaluation record, however
independent, could have found either, because neither lived in the
evaluation record. Both were caught only because the defect eventually
produced an observable symptom — a reset cadence, a red pipeline — that
crossed a threshold of noticeability during an investigation undertaken
for another purpose. The analogue to this dissertation's central argument
is direct: the WS defect was invisible because nothing reconciled the feed
against reality, just as concealed abstention was invisible because
nothing reported capital-weighted exposure. Two scope notes bound the
addition. Neither finding affected any reported figure (Section 3.9), so
neither flips anything — they extend the register from evaluation defects
to operational defects, defensible here because the system under study is
a deployed system. And the frozen record itself still documents eighteen;
rows 19–21 were added in this chapter and ratified by the author on
2026-09-19, with rows 19–20 marked as affecting no reported figure.

## 3.9 Reproducibility

Every reported evaluation claim reduces to committed artifacts. The raw
audit trail is 36 per-bar return CSVs under `reports/returns/`
(timestamped series for PPO, fold-RF and buy-and-hold per fold per pair)
and 24 per-bar exposure traces under `reports/exposure_traces/` (action,
position value, inventory, turnover, grid-level crossings, trained and
untrained). The twelve fold-RF baselines carry provenance manifests
(training window, data SHA-256, feature-contract hash, seed, class
distribution), the PPO models carry sidecars, and a run manifest records
the evaluation commit (`7e5feaf8`), library versions (scikit-learn 1.6.1,
numpy 2.5.2, pandas 3.0.5, stable-baselines3 2.9.0, torch 2.13.0) and
seeds (42 throughout). REPRODUCE.md maps 16 headline claims to exact
artifact fields and gives pandas-only verification commands (B1–B5a) that
re-derive the headline numbers from the raw CSVs; all were re-run on
2026-08-23 and matched exactly. A Batch-4 traceability table
(MANUSCRIPT_REVISION.md, 2026-08-16) checked 43 numeric claims against
committed artifacts — a dated record on the pre-Batch-7 arithmetic basis,
retained as history.

Data provenance is recorded as follows. All training and evaluation data are
one-hour klines for ETHUSDT and BNBUSDT, 17,304 bars per pair, pinned to an
end date of 2026-07-05 with a recorded SHA-256 per pair in the run manifest
(ETH `f3b80320…`, BNB `5c16c9cb…`). The source is Binance's public archive
(`data.binance.vision` daily files, the same data the REST API serves),
fetched into a local cache by `src/rl/data.py`. The WebSocket endpoint
misconfiguration of Section 3.8 affected only the live paper-trading feed;
no reported result used that feed. That independence is verified rather
than asserted: on 2026-09-19 both recorded hashes were recomputed from the
archive-derived cache over the pinned window and matched the manifest
exactly (17,304 bars per pair), so every evaluation number derives from
data pinned by a hash that reproduces from the public archive,
independently of the misconfigured live feed.

What cannot be reproduced is stated in REPRODUCE.md Section D: single-snapshot
provenance is not demonstrated (the run manifest, PPO sidecars and RF
manifests cite three different commits, diffed as semantically identical for
training and evaluation); the model binaries are untracked (regenerable from
recorded seeds and windows, but not recoverable); library-version sensitivity
beyond the verified 1.6.1/1.8.0 prediction check is unquantified; the kline
source is outside this repository's control; and the original training
environment no longer exists on the author's machine.

## 3.10 Ethical and legal considerations

The evaluation and the deployment that hosted it handled no real funds: the
system trades in paper mode, and the reinforcement-learning results are
historical replays over public market data. No personal data is collected or
processed anywhere in the pipeline. The legal exposure considered is
therefore operational rather than financial, and it was audited: a security
review on 2026-08-23 found two vestigial public ingress rules (ports 80 and
443) on the deployment's security group and revoked them, leaving the
inbound list empty, with external verification that the engine port was
never reachable from the internet. The same review corrected its own
headline — it had reported an exposure at port 3030 that was never open —
by executing the recommendation rather than trusting it.

Deployment-layer risks were documented but deliberately not fixed, because
the evaluation code is frozen and post-hoc repair would sever the provenance
chain Section 3.9 establishes. The documented set comprises a latent
duplicate-sell defect in the live-mode force-flat path (which is why live
routing must not be enabled on this codebase); YAML risk parameters with no
code reader, so no enforced position cap or daily loss limit exists; an
inoperative retraining workflow; a drift monitor that observes but never
reports; a regime-cache TTL equal to the push interval, producing systematic
gate-fallback windows; and routing that is global while regime is per-pair —
each recorded in Section 5.3.1 as a known characteristic of the deployed
system at freeze. The stance taken is that documentation of known defects in
a frozen, verifiable record is more honest than silent repair that would
invalidate the evidence chain.

---

*Note on references: Hoenig & Heisey (2001), Gelman & Carlin (2014), Newey
& West (1987), Holm (1979), Politis & Romano (1994) and Politis & White
(2004) were added to the manuscript's reference list on 2026-09-19,
together with Flyvbjerg (2006) and Tsang (2014), which were also cited in
body text without entries. One body citation remains unverifiable:
"Agarwal et al. 2021" (manuscript §4.6) matches no locatable work —
flagged for the author rather than given an invented entry. arXiv:2209.05559
and arXiv:2510.06466 are cited by identifier in the text as in the existing
manuscript.*

## Appendix 3.A — Evaluation-defect register

All 21 rows. Rows 1–18 are the evaluation defects, copied from FIX_REPORT
(Batches 1–16); rows 19–20 are the operational findings of Section 3.8 and
row 21 the documentation-drift finding, all three added 2026-09-19 and
ratified by the author the same day. Rows 19–21 affect no reported figure.

| # | Defect | Effect on the figure | Flipped? | Caught by |
|---|---|---|---|---|
| 1 | Boundary warmup inside the test-frame prefix | ~49 train bars entered "OOS" series; misaligned comparators | no | SELF |
| 2 | Zero train/test embargo | test features computable from train bars | no | SELF |
| 3 | Non-fold-pure RF baseline | early folds scored by a later-trained model | no | SELF |
| 4 | Mislabelled "Diebold–Mariano" statistic | reported stat untraceable to code | claim withdrawn | SELF |
| 5 | Additive (cumsum) MaxDD equity | overstated drawdown (ETH B&H 0.353→0.511) | **YES** | SELF |
| 6 | Invested-bars Sharpe annualisation | overstated ratio by √(1/f) | no (artifact) | SELF |
| 7 | Conflated exposure definitions | baselines matched on different quantities | **YES** (ETH reversed) | SELF |
| 8 | Overlapping-window cost sum | 2,243pp impossible vs a +38% window | **YES** (magnitude) | SELF |
| 9 | Single-seed reliance | 27pp within-fold seed swing hidden | **YES** | SELF |
| 10 | Arithmetic (uncompounded) total return | BNB B&H sign flip +3.78%→−4.27% | **YES** | IND. REVIEW |
| 11 | Pooling across 1,440-bar fold gaps | MaxDD inflated up to 5.5×; joins corrupted | **YES** | IND. REVIEW |
| 12 | Uncapped grid "ceiling" denominator | capacity diagnostic meaningless | strengthens finding | IND. REVIEW |
| 13 | Fee double-count in reward | reward more punitive than designed | scopes finding | IND. REVIEW |
| 14 | Length-only "Politis–White" block choice | intervals conditional on heuristic | label fixed | IND. REVIEW |
| 15 | Diagnostic data never persisted | central claim unverifiable | traces persisted | AUTHOR OBS. |
| 16 | "Structural amplifier" asserted without measurement | mechanism misattributed | superseded | AUTHOR OBS. |
| 17 | Classifier accuracies only in console output | accuracy claims unverifiable | caveat added | OP. AUDIT |
| 18 | Reproduction guide contradicted by a later fix | verbatim reader sees a false failure | corrected | SELF-VERIFY |
| 19 | WS market-data endpoint wired to testnet for 110 days (31 May–18 Sep 2026) | live paper record priced partly off testnet feed; no evaluation result affected (Section 3.9) | no thesis conclusion | SYMPTOM-DRIVEN INVESTIGATION |
| 20 | CI pipeline red since August 2026 while deploys proceeded | quality gate non-functional ~6 weeks | no thesis conclusion | SYMPTOM-DRIVEN INVESTIGATION |
| 21 | REPRODUCE.md §C library versions contradicted the run manifest | reader instructed to expect numpy 2.4.x / pandas 2.3.x; manifest records 2.5.2 / 3.0.5 (corrected in place 2026-09-19) | no (documentation) | SELF-VERIFY |

Row 21 is the third instance of the documentation-drift class, after row
18 (the guide's B3 expectation, invalidated by the Batch-7 compounding
correction) and the report's "write-only" description of the shadow
journal (invalidated when readers were identified, corrected in Batch 14).
The class recurs because every correction made downstream of written text
can invalidate the text again — which is why the reproduction guide is
re-verified against artifacts rather than trusted (Section 3.8).
