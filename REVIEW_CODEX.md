# Independent Methodological Review

## 1. Review basis

**Commit reviewed:** `da645c26fd8552284d30ed9ce0e62b9f9ec230fa`

**Accessed:** the current source tree; `docs/dissertation_manuscript.md`; `FIX_REPORT.md`; `src/rl` evaluation, environment, statistics, and walk-forward code; feature-contract and feature-engineering code; diagnostic and power-analysis scripts; the corrected run manifest; PPO and fold-RF provenance sidecars; the corrected JSON reports; and the per-fold return CSV artifacts under `reports/returns/`.

This review checks the current tree, not the claims made by earlier audit batches. No external literature or venue-review was independently verified; those items are listed under “Could not verify.”

## 2. Findings ranked by severity

### INVALIDATING

#### I-1. Reported “total returns” and cumulative differences are arithmetic sums of simple returns, not compounded portfolio returns

**Finding.** The headline return numbers and the MDE quantities labelled “cumulative” are not the returns of the equity curves used elsewhere in the report.

**Evidence.** `src/rl/evaluate.py:282-285` defines each recorded return as the change in equity divided by the preceding equity. Those are simple per-bar returns. The same module computes episode total return from the equity curve at `src/rl/evaluate.py:299-307`. In contrast, `src/ml/evaluation_report.py:53-60` defines `net_pnl` and `total_return` as `sum(returns) - fees`. `scripts/mde_power_report.py:76-84` likewise labels `sum(d)` and `mde_bar * n` as cumulative quantities and calls the sum a “log-return approx.”

The generated BNB report records buy-and-hold `total_return = 0.03776306163361848` at `reports/rl_walk_forward_BNBUSDT.json:362-367`. Recomputing the same 4,314 buy-and-hold rows from `reports/returns/BNBUSDT_ta_fold*.csv` as an equity curve gives approximately **−4.27%**, not +3.78%. The corresponding ETH artifact gives approximately **+37.97%** compounded versus the reported **+44.83%** arithmetic sum. The reported PPO/RF differences also change: approximately +8.38 percentage points compounded for ETH versus the reported +9.22pp, and approximately −5.65pp compounded for BNB versus the reported −12.04pp.

**Why it matters.** The benchmark table, abstract, MDE, required-sample calculation, and “cumulative return” interpretation do not use one coherent return definition. The BNB statement that buy-and-hold gained +3.8% is false under the equity semantics used by the environment and drawdown code. The directional statement that PPO and RF failed to beat passive exposure still appears to survive this particular recalculation, but the reported magnitudes and the power narrative do not. This is a direct numerical invalidation of the central performance table and its quantitative effect-size claims.

**Confidence:** verified.

#### I-2. Pooled fold-level risk and serial-inference calculations treat non-contiguous test windows as one continuous time series

**Finding.** The six OOS test windows are separated by gaps, but the code concatenates them without representing those gaps. The resulting pooled equity curve, HAC covariance, and stationary bootstrap sequence have artificial adjacency across fold boundaries.

**Evidence.** `src/rl/walk_forward.py:39-58` advances each split by `step_bars`; the corrected run uses `step_bars=2160` and `test_bars=720` (`reports/run_manifest_20260815T030518Z.json:18-20` and `:29-31`). Therefore consecutive 720-bar test windows are separated by 1,440 bars. `src/rl/walk_forward.py:72-92` concatenates all slice arrays directly, and `:95-114` applies the pooled HAC test to that concatenation. `src/ml/evaluation_report.py:23-36` computes MaxDD on the concatenated `cumprod` curve. `src/rl/risk_stats.py:88-133` resamples the same pooled ordering in its stationary bootstrap.

The corrective record explicitly says the opposite: `FIX_REPORT.md:220-224` claims the folds are “contiguous chronological segments” and that concatenation reconstructs one continuous equity path. The report’s recorded slice starts confirm the gaps: `FIX_REPORT.md:88-95` lists starts in January, April, July, October, January, and April.

**Why it matters.** A drawdown cannot cross an uninvested, unrepresented 1,440-hour gap as if the next test bar immediately followed the previous test bar. The pooled MaxDD therefore answers neither a continuous live equity question nor a per-fold-reset question. The HAC estimator also computes lag-1 through lag-9 covariance across fold boundaries that are months apart, while the bootstrap can create circular blocks crossing those artificial joins. Consequently the reported MaxDD point estimates, MaxDD intervals, and pooled p-values are not valid for the stated walk-forward design.

**Confidence:** verified.

#### I-3. The “position ceiling” diagnostic uses a ceiling that the grid engine does not enforce

**Finding.** The manuscript treats 67–100%/75% nominal values as available position ceilings and interprets 73–83% utilisation as evidence that the ceiling is not binding. The grid implementation has no total inventory cap.

**Evidence.** `src/rl/env.py:443-458` creates a five-level ladder once per activation. `src/rl/env.py:460-484` computes a per-level notional and adds units on every bar whose low/high range crosses a buy level. There is no check limiting cumulative inventory to five levels or to any fixed fraction of equity. The diagnostic nevertheless defines grid “maximum notional” as `5 * grid_level_pct * size_mult * equity` at `scripts/diagnose_exposure.py:68-75`, then computes `position / maximum` at `:162-186`. The resulting figures are reported in `reports/exposure_diagnosis.json:3-6` and `:42-46`, and the manuscript calls them non-binding at `docs/dissertation_manuscript.md:330-334`.

**Why it matters.** Repeated level crossings can accumulate inventory beyond the denominator used by the diagnostic. For the dominant grid actions, the denominator is an accounting convention, not an enforceable maximum. The reported binding test cannot establish that configuration capacity was available and unused. Therefore the central causal statement that low capital exposure is learned withdrawal rather than a configuration ceiling is not established by this diagnostic. It also undermines the asserted “structural amplifier” interpretation because the same implementation permits exposure dynamics not represented by the claimed ceiling.

**Confidence:** verified.

#### I-4. The pooled HAC test and power analysis use 4,314 bars as if they were one method-level sample despite six separately fitted policies and fold gaps

**Finding.** The test counts every OOS bar as an observation but does not cluster or otherwise account for the fact that PPO and RF are refit per fold, that the fitted policy changes between folds, and that the test episodes are separated in time.

**Evidence.** `src/rl/walk_forward.py:117-133` trains a fold-specific RF; `:95-114` then applies one pooled paired test to the concatenated per-bar arrays. The reported test has `n=4314` at `reports/rl_walk_forward_ETHUSDT.json:250-253` and `reports/rl_walk_forward_BNBUSDT.json:250-253`. The implemented HAC formula in `src/rl/evaluate.py:87-114` models serial covariance in the concatenated differences only; it contains no fold cluster, model-estimation, or cross-fold dependence term. The power script repeats the same construction at `scripts/mde_power_report.py:60-84`.

**Why it matters.** The inferential unit for a sequence of refitted policies is not automatically the individual bar. The reported p-values and MDEs can be numerically correct for a pooled per-bar mean under the code’s assumptions while failing to support a method-level claim about the learning procedure. With six test episodes and policy retraining between episodes, the stated `n=4314`, “20.7/35.9 years,” and 6–7% power figures should not be presented as method-level evidence without a fold-aware inferential model. This is separate from the already disclosed low power: the dependence structure and estimand are also mis-specified.

**Confidence:** verified for the implemented unit and missing fold adjustment; probable for the resulting inferential invalidity because the appropriate cluster estimand depends on the intended claim.

#### I-5. The 70-bar embargo is not a strict feature-isolation guarantee for the full feature contract

**Finding.** The protocol’s assertion that no test feature can depend on training-period data is false for the exponential indicators used by the contract.

**Evidence.** `src/rl/walk_forward.py:28-36` identifies only RSI as having infinite support and asserts that 70 bars bounds the EWM tail. The feature implementation also computes ADX through `pandas_ta` and MACD through EMA-based `pandas_ta` at `src/data/feature_engineering.py:74-81`; these are smoothing recurrences with non-zero historical support. RSI itself is explicitly an infinite-support EWM at `src/data/feature_engineering.py:65-72`. The OOS evaluator supplies 100 warmup bars at `src/rl/walk_forward.py:384-400`; with a 70-bar embargo, the warmup frame can include 30 bars preceding the embargo boundary. No code proves that the ADX/MACD/RSI dependence on those older bars is exactly zero.

**Why it matters.** Seventy bars may be an approximate decay tolerance, but it cannot support the documented strict no-dependence claim for infinite-support indicators. The corrected protocol is therefore not genuinely boundary-pure as described. This is a leakage-control defect even if the residual influence is small.

**Confidence:** verified for non-zero support and the supplied warmup; unverified for the numerical size of the resulting model influence because the exact `pandas_ta` implementation and feature-state initialisation are not part of the committed repository.

### WEAKENING

#### W-1. The reported MaxDD confidence intervals in the JSON reports are produced by a different, IID/additive bootstrap than the claimed stationary/multiplicative analysis

**Finding.** The report contains two incompatible confidence-interval implementations. The generic strategy intervals are not the stationary-bootstrap MaxDD intervals described in the manuscript.

**Evidence.** `src/ml/evaluation_report.py:64-108` resamples individual rows IID at `:71-74` and computes bootstrap drawdown from `1 + cumsum(sampled_returns)` at `:82-89`. The point MaxDD in the same module is multiplicative at `:23-36`. The report’s generic `confidence_intervals.max_drawdown` fields are visible, for example, at `reports/rl_walk_forward_BNBUSDT.json:214-240` and `:334-360`. Separately, `src/rl/risk_stats.py:88-133` implements the stationary bootstrap used for the `risk_inference` field, which the manuscript cites at `docs/dissertation_manuscript.md:407-408`.

**Why it matters.** A reader consuming the committed JSON cannot tell which interval definition supports a risk claim. The reported point estimate and its generic interval can refer to different equity constructions and different dependence assumptions. This is an internal inferential inconsistency, even though the manuscript’s specifically printed MaxDD-difference intervals appear to come from the separate risk-inference path.

**Confidence:** verified.

#### W-2. `block=10` is not a Politis–White automatic block-length estimate

**Finding.** The function advertised as a Politis–White automatic rule uses only sample length. It never inspects the series’ dependence structure.

**Evidence.** `src/rl/risk_stats.py:77-85` defines `_autoblock_length(x)` but uses only `len(x)`, returning `ceil(4*(n/100)^(2/9))`. `src/rl/risk_stats.py:112` applies that value, and the report labels the result “politis-romano stationary” at `reports/rl_walk_forward_BNBUSDT.json:298-307`. The same value is reported for ETH at `reports/rl_walk_forward_ETHUSDT.json:298-307`. At `n=4314`, the implemented shortcut deterministically returns 10 regardless of the autocorrelation in the return differences.

**Why it matters.** Calling an n-only Newey–West-style shortcut a Politis–White automatic selection is technically incorrect. The MaxDD CI width and the “MDE-equivalent” risk statement depend on an undocumented arbitrary block choice, so the risk uncertainty is not supported as claimed.

**Confidence:** verified.

#### W-3. PPO training does not use the declared 4,320-bar fold window, although the RF baseline does

**Finding.** The RF is exact-fold, but PPO training rounds the requested window to calendar months and includes 4,344 bars in the stored fold-0 artifact.

**Evidence.** `src/rl/walk_forward.py:277-285` explicitly says PPO may see slightly more than the strict slice; `:312-320` rounds `train_bars` to `ceil(train_bars / 720)` months. The RF manifest reports `fold_bars=4320` and `train_start=2024-07-15` at `models/rl/_wf_ETHUSDT_fold_rf_0_4320.pkl.json:10-19`. The corresponding PPO sidecar reports `data_start=2024-07-14`, `data_end=2025-01-10`, and `n_bars=4344` at `models/rl/_wf_ETHUSDT_slice0.json:4-9`. The OOS boundary remains before the test window, but the training sample is not the declared 4,320-bar sample.

**Why it matters.** The protocol description presents a common `train_bars=4320` design while the two arms receive different historical sample lengths. This does not by itself show future leakage, but it weakens the claim that the arms were evaluated under the same fold definition and complicates exact reproducibility of the reported protocol.

**Confidence:** verified.

#### W-4. The provenance artifacts do not identify one reproducible source snapshot

**Finding.** The corrected run manifest, fold-RF artifacts, and PPO artifacts point to different commits.

**Evidence.** `reports/run_manifest_20260815T030518Z.json:1-2` identifies commit `7e5feaf831c01a22a9c2098a0544cb720c7ebd5a`. The ETH fold-0 RF manifest identifies `353cb252fde5b661fe5b9d531917c51059a9a43a` at `models/rl/_wf_ETHUSDT_fold_rf_0_4320.pkl.json:11-15`. The ETH PPO fold-0 sidecar identifies `df1c3bed3ed8616e7d5d6091f133712d25884329` at `models/rl/_wf_ETHUSDT_slice0.json:4-7`; the next PPO fold uses yet another commit at `models/rl/_wf_ETHUSDT_slice1.json:4-7`.

**Why it matters.** The report describes one corrected-protocol run, but the trained artifacts were produced under different source snapshots. Without a recorded diff proving these commits are semantically identical for training and evaluation, the manifest does not provide the claimed single-state provenance. The current reviewed commit is itself `da645c26fd8552284d30ed9ce0e62b9f9ec230fa`, distinct from all of those run commits.

**Confidence:** verified for the commit mismatch; unverified for whether the differences changed any relevant behaviour.

#### W-5. “Randomly-initialised policy” is a deterministic untrained PPO argmax, not a random-action control

**Finding.** The diagnostic contrasts a trained deterministic action sequence with one untrained neural network’s deterministic action sequence and describes the latter as a randomly-initialised policy.

**Evidence.** `scripts/diagnose_exposure.py:41-53` creates an untrained PPO network with seed 42 and wraps it in `PPORouter`. `src/rl/router.py:29-37` calls `model.predict(..., deterministic=True)`. The diagnostic aggregates the resulting action shares at `scripts/diagnose_exposure.py:168-186`, and the manuscript interprets them as learned versus initialised abstention at `docs/dissertation_manuscript.md:327-334`.

**Why it matters.** The comparison does show that training changed this deterministic policy’s decisions, but it is not a random-policy baseline and does not isolate the effect of learning from initialization, stochastic policy sampling, or seed variation. The phrase “randomly-initialised” is therefore easy to overread as a chance-action control.

**Confidence:** verified.

#### W-6. The central withdrawal diagnosis remains confounded by the deliberately double-counted transaction-cost signal

**Finding.** The reward’s equity return already includes fees, and the reward subtracts an additional fee term. The diagnostic correctly reconstructs this implementation, but the manuscript treats the resulting abstention as a general learned-withdrawal finding.

**Evidence.** `src/rl/env.py:330-357` subtracts `fees = turnover * fee_rate` from equity at `:333-334`, then subtracts `cfg.fee_rate * turnover_norm` again at `:354-357`. The code itself calls this an amplification at `:345-350`. The decomposition script reconstructs the terms rather than independently estimating an alternative reward at `scripts/diagnose_exposure.py:86-97`; the manuscript reports the resulting diagnosis at `docs/dissertation_manuscript.md:335-350`.

**Why it matters.** The result establishes behaviour under one unusually punitive reward, not that PPO generally learns abstention in trading. The manuscript acknowledges the single `lambda=0.5` limitation at `docs/dissertation_manuscript.md:576-580`, but it does not equivalently qualify the double-fee choice when presenting “learned withdrawal” as the causal diagnosis. The observed policy may be rational response to the explicit design rather than evidence about RL routing.

**Confidence:** verified for implementation and probable for the scope limitation.

### COSMETIC

No purely cosmetic finding was assigned. The defects above affect estimands, inference, provenance, or causal interpretation.

## 3. Claims checked and found SOUND

1. **Flat-policy comparison mechanics.** `scripts/flat_vs_trained_by_seed.py:35-58` uses the same warmup-prefixed test frame, resets with the same seed, sums the environment’s actual reward, and runs action 9 on a fresh environment. `src/rl/env.py:302-308` maps action 9 to flat and closes any pre-existing position. The flat policy still receives the benchmark term, and with a fresh environment has no opening turnover; it is not a zero-reward shortcut. The comparison is reward-equivalent and timestamp-equivalent for its stated cells.

2. **Reward equation and timing.** `src/rl/env.py:268-357` applies the action through the current bar, uses the previous close as the benchmark origin, updates `prev_close` at `:360-362`, and computes the reward from the same bar’s equity return and close-to-close benchmark return. The sign convention and fee double-count are explicit. The decomposition script’s reconstruction is consistent with that implemented equation.

3. **Timestamp alignment.** `src/rl/walk_forward.py:403-442` converts returns to timestamp-indexed series and inner-joins PPO, RF, and TA. `_run_model` exposes the return timestamps at `src/rl/evaluate.py:258-330`. This is materially different from the superseded position-only truncation and is implemented in the current path.

4. **Strict OOS end guard.** `src/rl/evaluate.py:237-255` rejects a model whose recorded training end is on or after the OOS start. The guard is real and not merely documentary.

5. **Fold-specific RF construction.** `src/rl/walk_forward.py:150-190` slices each RF training frame by fold indices, creates the temporal 85/15 train/calibration split inside that frame, and removes unlabeled rows after label generation. The RF fold-0 manifest records the corresponding window and sample counts at `models/rl/_wf_ETHUSDT_fold_rf_0_4320.pkl.json:10-19`.

6. **HAC lag arithmetic and statistic naming.** `src/rl/evaluate.py:57-114` implements the stated Bartlett-kernel Newey–West calculation and the rule gives lag 9 at `n=4314`. It is correctly described as a paired realised-return mean-difference test, not a Diebold–Mariano forecast-loss test.

7. **Limited Holm scope.** The manuscript says the correction is across assets at `docs/dissertation_manuscript.md:394-405`; it does not falsely describe that particular correction as covering every metric and every exploratory analysis. The code applies Holm to the two return-test p-values at `src/rl/walk_forward.py:957-983`.

8. **Seed-sweep scope and exception disclosure.** The current manuscript explicitly distinguishes the 19-of-20 sweep from the six-fold seed-42 illustration at `docs/dissertation_manuscript.md:304-324` and states the BNB fold-3 seed-999 exception. It also discloses that the sweep covers folds 0 and 3 only at `docs/dissertation_manuscript.md:427-448` and `:552-555`.

9. **No current ECE claim.** The manuscript explicitly says no current calibration figure is reported at `docs/dissertation_manuscript.md:177-189`, so the prior ECE value is not silently reused as current evidence.

10. **Backtest-only limitation.** The manuscript states that no live or paper-trading performance is claimed at `docs/dissertation_manuscript.md:544-551`. The corrected artifacts reviewed here are historical replay outputs.

## 4. Claims not independently verifiable

1. **Literature novelty.** I did not independently search the adjacent literature, so I cannot verify whether the corrected protocol or flat-policy diagnostic is novel. The repository references alone do not establish novelty.

2. **Exact numerical influence of the non-RSI exponential tails.** The repository does not pin or vendor the `pandas_ta` ADX/MACD implementation. The existence of non-zero smoothing support is verified from the feature construction, but the residual boundary influence under the installed library cannot be independently reproduced from the committed tree alone.

3. **Semantic equivalence of the differing artifact commits.** The commit IDs are verified, but this review did not diff every source file across `df1c3bed`, `353cb252`, `7e5feaf`, and the reviewed `da645c26`. Therefore it is not asserted that the commit mismatch changed model outputs; only that the claimed single-snapshot provenance is not demonstrated.

4. **A valid alternative fold-level power calculation.** The review establishes that the committed calculation is pooled per-bar arithmetic and lacks fold clustering. It does not assign a replacement p-value, MDE, or “standard” block length, because doing so would require choosing an estimand and dependence model not specified by the dissertation.
