# Evaluation-Protocol Fix Report

**Date:** 2026-08-15

> **CORRECTION TRAIL NOTE (Batch 5).** Batch 1 sections below are
> SUPERSEDED where marked by banners — Batch 2 and Batch 3 corrected
> several Batch-1 figures and reversed two of its verdicts. The
> superseded text is retained deliberately: the correction trail is part
> of this report's contribution. Read Batch 1 only through the banners.

**Scope:** Audit-fix tasks 1–8 on the RL walk-forward evaluation protocol.
**Rule followed:** protocol fixes only — no hyperparameters, rewards,
thresholds, or strategy logic were touched. All results below come from a
single corrected-protocol run over pinned data. Where the corrected results
are weaker or less flattering than the previous ones, both are shown.

---

## 1. Headline numbers: before / after

### Pooled paired mean-difference test (PPO vs clean RF)

| | Old protocol | Corrected protocol |
|---|---|---|
| ETH stat / p / n | 0.30 / 0.77 / 4,620 | **+0.43 / 0.665 / 4,314** |
| BNB stat / p / n | 0.71 / 0.48 / 4,620 | **−0.33 / 0.743 / 4,314** |
| Holm-adjusted p (ETH, BNB) | not computed | **1.00, 1.00** |
| Baseline RF | one long-window artifact (not fold-pure) | fold-specific, 70-bar embargo |
| Comparator alignment | array-length truncation (misaligned ~49 bars) | timestamp inner-join |
| HAC lag | fixed 5 | rule-based: **9** at n=4,314 |
| Embargo | 0 bars | 70 bars |
| Test name | "Diebold–Mariano" (misnomer) | paired mean-difference, Newey–West HAC |

**Direction of change:** the corrected protocol does not rescue the
previous conclusion — it *reinforces the null*. ETH's statistic moves
slightly positive, BNB's flips sign negative; neither is remotely
significant (Holm-adjusted p = 1.00 both pairs). The old BNB p=0.48 was
computed on misaligned, partially train-contaminated bars and was not
meaningful.

### Risk inference (corrected protocol, new)

> **SUPERSEDED (B2.1):** the MaxDD row of this table used the additive
> equity construction. Canonical multiplicative values: ETH PPO/RF/TA
> 0.172/0.273/0.353 -> **0.165/0.247/0.511**; BNB 0.326/0.532/0.428 ->
> **0.293/0.426/0.503**.


| | ETH | BNB |
|---|---|---|
| MaxDD PPO − RF (point) | −0.082 | −0.134 |
| MaxDD diff 95% CI (stationary bootstrap, block=10) | **[−0.342, +0.090]** | **[−0.381, +0.161]** |
| Sortino diff (point, CI) | +0.57 (−1.64, +2.57) | −0.53 (−2.89, +1.24) |
| PPO MaxDD / RF MaxDD / TA MaxDD | 0.172 / 0.273 / 0.353 | 0.326 / 0.532 / 0.428 |
| PPO total return / RF / TA | −13.1% / −22.3% / +44.8% | −29.0% / −17.0% / +3.8% |
| PPO time-in-market / RF | 58.1% / 61.4% | 54.1% / 76.0% |
| Sharpe (all bars) PPO / RF | −1.74 / −1.86 | −3.04 / −0.72 |
| Sharpe (invested bars only) PPO / RF | −4.47 / −3.01 | −7.81 / −1.09 |

> **SUPERSEDED (B2.6):** the invested-bars Sharpe row below was an
> annualisation artifact (sqrt(8760) applied to a subset of bars);
> corrected values equal the all-bars Sharpe exactly (ETH PPO -4.47 ->
> -1.75; BNB PPO -7.81 -> -3.05).

| Promotion gate | **ineligible** (inconclusive sample; no risk-adjusted improvement) | **ineligible** (same) |

**Stated plainly:** under the corrected protocol PPO shows no return edge
(and is worse than buy-and-hold on both pairs in this window), and the
drawdown advantage vs the RF baseline is **not statistically significant**
— both bootstrap CIs straddle zero.

## 2. Task-1 boundary assertions

- OLD: PPO/RF collected 769 rows/slice starting **49 bars before** the
  declared test boundary; TA started at the first test bar with 719 rows;
  pooling truncated by length, comparing misaligned bars.
- NEW: all comparators return 719 rows/slice starting **exactly at the
  first test bar**, identical timestamp index. Verified on all 12 slices.
- The loud assertion fired during development on a deliberately
  mis-indexed fixture (proving it works); **no assertion fired on any
  real slice** in the corrected run — every slice's first timestamp
  equals the declared first test bar.

Per-slice first/last/rows (ETH and BNB identical structure; with the
70-bar embargo the test windows are shifted 70 bars later than the
historical no-embargo windows):

| slice | first ts (all comparators) | rows |
|---|---|---|
| 0 | 2025-01-13 23:00 | 719 |
| 1 | 2025-04-12 17:00 | 719 |
| 2 | 2025-07-11 16:00 | 719 |
| 3 | 2025-10-09 16:00 | 719 |
| 4 | 2026-01-07 16:00 | 719 |
| 5 | 2026-04-07 17:00 | 719 |

## 3. Embargo

- Max feature lookback: 50 bars (sma_50/vwap-50/OBV-50); RSI Wilder EWM
  tail <1% after ~64 bars → **embargo = 70 bars** (`DEFAULT_EMBARGO_BARS`).
- Slice count unchanged: 6/pair (embargo consumed step-budget headroom,
  not test bars). Verified in the splitter and reflected in slice
  boundaries.

## 4. Exposure-matched result (Task 6)

> **SUPERSEDED (B2.2):** this section conflated exposure definitions
> (its '15%' matching basis was neither time-in-market nor
> capital-weighted). Re-matched on capital-weighted exposure, the ETH
> verdict REVERSES: PPO sits at the 100th percentile (worst) of the
> exposure-matched random distribution on BOTH pairs (medians 0.0509/
> 0.0508 vs PPO 0.165/0.293).


**One sentence each:**

- **ETH:** PPO's drawdown advantage over random entry **survives exposure
  matching** — MaxDD 0.165 sits at the 31st percentile of the 200-seed
  exposure-matched random distribution (median 0.208), though a constant
  15%-exposure buy-and-hold still beats it (MaxDD 0.099).
- **BNB:** PPO's drawdown advantage **does NOT survive exposure matching**
  — MaxDD 0.293 sits at the **95th percentile** (i.e., worse than ~95% of
  random entries at the same exposure; median 0.171).

Combined with §1: the exposure-matched evidence for PPO's risk-control
benefit is **pair-dependent and not established overall**.

## 5. Corrected-protocol run details

- Command: `python -m src.rl.walk_forward --pairs ETHUSDT BNBUSDT
  --data-end 2026-07-05 --timesteps 1000000 --report-dir reports`
- 12/12 slices evaluated, 0 failed. Commit, data hashes, seeds, library
  versions in `reports/run_manifest_20260815T030518Z.json`.
- Raw audit trail: 36 CSVs under `reports/returns/` (timestamped per-bar
  returns for PPO / fold-RF / TA per fold).
- Fold-RF baselines: 12 models with immutable provenance manifests
  (train window, data SHA-256, feature-contract hash, seed, class
  distributions; e.g. ETH fold 0: 2024-07-15→2025-01-10, 3,610 train
  rows, classes 1592/877/1141 ranging/trending/danger).

## 6. Claims: supported vs unsupported

> **PARTIALLY SUPERSEDED (B2.3, B2.8, B3.6):** the 'PPO ~= fold-RF on
> pooled returns: no edge either direction' claim below was withdrawn
> and replaced by the underpowered-design statement (MDE 60-103%
> cumulative; achieved power 6-7%); 'parity' phrasing is unsupported.


**Now supported by committed evidence:**
- Timestamp-aligned, embargoed, boundary-strict, fold-pure walk-forward
  results (this run + 36 return CSVs + 2 JSON reports + run manifest).
- The statistical test is what it says it is (paired mean-difference,
  Newey–West HAC lag 9, Holm-corrected across pairs).
- PPO ≈ fold-RF on pooled returns: **no edge either direction** (p=0.67 /
  0.74; Holm 1.00 / 1.00).
- MaxDD difference point estimates favor PPO on both pairs but the 95%
  bootstrap CIs include zero — not significant.
- sklearn 1.6.1 pin verified: no version warnings, predictions bit-identical
  to 1.8.0 on a fixed sample.
- Exposure-matched baselines exist and give a mixed verdict (ETH survives,
  BNB does not).

**Still unsupported:**
- "PPO reduces drawdown" as a general claim — CI includes zero, and the
  exposure-matched test fails on BNB.
- Any live/paper confirmation of backtest results.
- Regime-conditioned performance tables (not yet built).
- Calibration metrics (ECE) for current models.
- The old manuscript headline numbers (Sharpe 1.85, MaxDD −0.4%) — they
  came from the contaminated protocol and the single-window benchmark;
  both were removed from the manuscript in Task 8.

## 7. Deviations & incidents

- Two earlier full-run attempts died: (a) an immutability-guard failure
  loop on already-trained fold models, (b) a reproducible parent-process
  segfault (EXIT=139) when loading PPO models after training-subprocess
  churn. Fixes: provenance-matched artifact reuse, and per-slice
  evaluation in isolated subprocesses. Neither change touches the
  protocol semantics.
- One PPO sidecar-path bug (`x.zip.json` vs `x.json`) briefly caused
  needless retraining; fixed; deterministic seeds make the retrain
  immaterial.
- `reports/` is gitignored by default; evidence files were force-added
  (they are the thesis audit trail).

---

## Bottom line

> **SUPERSEDED (B2.8, B3.6):** 'parity is confirmed' below was
> withdrawn — the design cannot distinguish parity from differences an
> order of magnitude larger than those observed. The exposure-matched
> verdict cited here also reversed in B2.2.


Under the corrected protocol, the previous headline claim — "PPO achieves
return parity with the supervised baseline, with lower drawdown" — holds
only in its weakest form: parity is confirmed (p≈0.7 both pairs), but the
drawdown advantage is statistically indistinguishable from zero, and once
exposure-matched it disappears entirely on BNB. PPO underperformed
buy-and-hold on both pairs over the evaluated window. The supervised RF
baseline also underperformed buy-and-hold on ETH. Nothing in the corrected
results supports deploying either router over passive exposure on this
window — consistent with the promotion gate's verdict: ineligible.

---

# Batch 2 — Second corrective pass (2026-08-15)

## B2.1 Canonical MaxDD (task 1)

> **SUPERSEDED (B7 task 4) — THE CONTIGUITY ASSERTION BELOW IS FALSE.**
> The six test windows are separated by **1,440-bar (~60-day) gaps**
> (step_bars=2160, test_bars=720), verified directly. The folds are NOT
> contiguous; concatenation creates artificial adjacency at five fold
> boundaries. What is true: the *additive-vs-multiplicative* diagnosis
> was correct and stands; the *pooled-by-concatenation* aggregation
> does not. Replaced by per-fold MaxDD distributions and a
> fold-cluster bootstrap (see Batch 7 §4).

**Cause of the contradiction:** two different equity-curve constructions on
identical input series (same 4,314 bars, same span). §1's table came from
`evaluation_report._drawdown` — **additive** equity `1 + cumsum(r)`; §4's
from `risk_stats.max_drawdown` — **multiplicative** equity `cumprod(1+r)`.
Reproduced exactly: ETH 0.1716 (additive) vs 0.1654 (multiplicative);
BNB 0.3259 vs 0.2926.

**Canonical definition (now everywhere, documented in code):** MaxDD on the
multiplicative equity curve, **pooled across folds by concatenation** — folds
are contiguous chronological segments, so concatenation reconstructs one
continuous equity path; per-fold-averaged MaxDD would hide cross-fold
drawdowns and answer a different question.

| Strategy | Old (additive) | **Canonical** |
|---|---|---|
| ETH PPO / RF / TA | 0.172 / 0.273 / 0.353 | **0.165 / 0.247 / 0.511** |
| BNB PPO / RF / TA | 0.326 / 0.532 / 0.428 | **0.293 / 0.426 / 0.503** |

MaxDD bootstrap CIs recomputed (conclusions unchanged — still straddle zero):
ETH [−0.342, +0.090]; BNB [−0.381, +0.161].

## B2.2 Exposure definitions (task 2)

Three quantities were being conflated. Measured per strategy/pair:

| | time_in_market | capital-weighted exposure | old §4 matching basis |
|---|---|---|---|
| ETH PPO | 58.1% | **4.3%** | 15.2% (nonzero-return bars) |
| BNB PPO | 54.2% | **5.2%** | — |
| ETH RF | 61.4% | **21.1%** | — |
| BNB RF | 76.0% | **60.5%** | — |

**The batch-1 baselines were not on the same footing**: the scaled B&H used
a "15%" constant fraction while random entries matched a time-in-market-like
quantity. All baselines are now matched on **capital-weighted exposure**
(the economically meaningful definition — mean |position|/equity). Report
fields renamed (`time_in_market` vs `capital_weighted_exposure`); the env
now exposes per-bar `position_value`.

**Verdict change (ETH flips):** matched on true capital exposure,
random-entry MaxDD medians are 0.0509 (ETH) / 0.0508 (BNB) per the final
committed artifacts (reports/exposure_match_*.json; the 0.029/0.035 quoted
in an earlier draft of this section was from a pre-final regeneration) vs
PPO's 0.165 / 0.293 — **PPO sits at the 100th percentile (worst) on both
pairs.** The batch-1 ETH verdict "survives exposure matching" was an
artifact of the mismatched definitions. PPO's drawdown is worse than random
entries at the same capital exposure, on both pairs.

## B2.3 MDE and power (task 3)

Using the actual per-bar series and the same Newey-West HAC variance
(lag 9, n=4,314):

| | ETH | BNB |
|---|---|---|
| observed cumulative diff (PPO−RF) | +9.2% | −12.0% |
| **MDE at 80% power** | **59.7% cumulative** | **102.7% cumulative** |
| achieved power at observed diff | 7.2% | 6.2% |
| n required for observed diff | 180,943 bars = **20.7 years** | 314,103 bars = **35.9 years** |
| MaxDD CI half-width (MDE-equiv) | 0.216 | 0.271 |

Plain language:
- **ETH:** at n=4,314 this design can only detect cumulative return
  differences larger than 59.7%; the observed difference was 9.2%.
- **BNB:** at n=4,314 this design can only detect cumulative return
  differences larger than 102.7%; the observed difference was −12.0%.

**"Parity" is withdrawn as a claim.** The test cannot distinguish parity
from a 12-point gap; the correct statement is "underpowered to detect
economically large differences."

## B2.4 Seed sensitivity (task 4)

Restriction documented: 5 seeds × 2 folds (0, 3) × 2 pairs = 20 trainings
(full 60-training sweep infeasible in-session). Folds span the
market-direction split.

| across seeds (folds pooled) | ETH | BNB |
|---|---|---|
| total return mean / SD / range | −2.9% / 4.4pp / −9.1..+5.4 | −5.5% / 8.0pp / −23.0..+4.7 |
| MaxDD mean / SD / range | 0.075 / 0.038 / 0.032..0.151 | 0.102 / 0.092 / 0.007..0.312 |
| paired stat vs RF mean / SD / range | +0.94 / 0.63 / −0.03..+1.98 | −0.04 / 0.57 / −0.92..+0.80 |

**Seed variance DOMINATES the method difference.** Within the same fold,
seed alone swings cumulative return by up to 27pp (BNB fold3: seed 7
−22.99% vs seed 999 +3.95%) and MaxDD by 45× (0.007 vs 0.312). The paired
statistic crosses zero within seeds on BNB. **The batch-1 single-seed
(seed 42) comparison is one draw from this distribution and cannot support
a method-level claim in either direction.** This finding outranks the
PPO-vs-RF comparison.

## B2.5 False-DANGER opportunity cost (task 5)

> **SUPERSEDED (B3.2):** the summed totals below (2,242.9 pp / 1,183.2
> pp) were RETRACTED — overlapping 24-bar windows multiply-count each
> move. Replacements: per-signal means +3.34%/+3.88%, 24-bar block gaps
> 0.96pp/0.72pp, portfolio-level gaps -67.1pp/-20.8pp. The DANGER-bar
> mean forward return is superseded by -0.79%/-0.68% (corrected
> mask; see false_danger_corrected.json). Retained as the trail.


Fold-specific RF predictions vs realised forward 24-bar outcomes:

| | ETH | BNB |
|---|---|---|
| DANGER share of bars | 39.3% | 25.0% |
| DANGER calls that were false (fwd return > 0) | **47%** | **38%** |
| total foregone return from false DANGER | **2,242.9 pp** | **1,183.2 pp** |
| mean fwd return on DANGER bars | −0.38% | −0.98% |
| missed danger (non-DANGER bars with fwd DD ≤ −3%) | 36.9% | 17.9% |
| mean fwd return on TRENDING bars | +1.27% | +0.42% |

**The classifier's 0.78–0.87 accuracy does not convert into usable gating.**
When it says DANGER the market still rises 47%/38% of the time, and the mean
forward return on DANGER bars (−0.4%/−1.0%) is far too small an edge to pay
for skipping the 47%/38% of DANGER bars that rise. This is the leading
explanation for why every gated strategy underperforms buy-and-hold in
rising folds.

## B2.6 Invested-bars Sharpe (task 6)

"Invested bar" = non-zero-return bar. Batch 1 annualised the invested-bars
Sharpe with the full-year factor √8760 on a subset of bars — overstating by
√(1/f) (2.6× at f=0.15). Corrected: factor = √(8760 × invested_fraction).

| | all bars (√8760 ≈ 93.6) | invested bars — old (√8760) | **invested bars — corrected (√(8760·f))** |
|---|---|---|---|
| ETH PPO | −1.74 | −4.47 | **−1.75** (f=36.5) |
| ETH RF | −1.86 | −3.01 | **−1.86** (f=57.7) |
| BNB PPO | −3.05 | −7.81 | **−3.05** (f=36.5) |
| BNB RF | −0.72 | −1.09 | **−0.72** (f=61.5) |

Corrected invested-bars Sharpe **equals** the all-bars Sharpe to rounding —
as it must for zero-flat-bar series (flat bars add zero mean and cut
variance by exactly √f; the effects cancel). **Batch 1's "Sharpe collapse
on invested bars" was entirely an annualisation artifact**, not evidence of
worse per-position performance. (Note the seed-sweep rows show the same
equality, confirming the fix.)

## B2.7 Market-direction conditionality (task 7)

Per-fold buy-and-hold vs PPO over the corrected protocol:

| fold | ETH B&H | ETH PPO−B&H | BNB B&H | BNB PPO−B&H |
|---|---|---|---|---|
| 0 | −14.4% | +12.5pp | +1.6% | −16.2pp |
| 1 | +69.6% | **−70.0pp** | +14.2% | −13.8pp |
| 2 | +43.6% | −41.7pp | +17.2% | −15.9pp |
| 3 | −7.1% | +3.9pp | −10.2% | +1.1pp |
| 4 | −32.7% | +21.7pp | −27.1% | +21.9pp |
| 5 | +5.7% | −4.2pp | +7.6% | −8.6pp |

**corr(B&H return, PPO−B&H): ETH −0.995, BNB −0.928 (n=6 each).**
Nearly perfectly negative: PPO beats buy-and-hold only in falling folds and
loses catastrophically in rising folds (ETH fold 1: B&H +69.6% vs PPO
−0.3%). **The result is conditional on market direction, not on model
quality** — PPO behaves as a perpetually-defensive posture (consistent with
its 4–5% capital exposure), not a timing model.

## B2.8 Updated supported / unsupported claims

**Supported by committed evidence:**
- The evaluation protocol itself: timestamp-aligned, 70-bar-embargoed,
  fold-pure, pinned-data, canonical-MaxDD, correctly-named statistics.
- The design is **underpowered**: MDE 60–103% cumulative; detecting the
  observed gaps would need 21–36 years of data. Any "parity" statement is
  unsupported — replaced by "underpowered to distinguish".
- Seed variance dominates method differences (27pp return swing within a
  fold; paired stat crosses zero across seeds).
- Direction conditionality: corr −0.93/−1.00 between B&H and excess return.
- PPO's drawdown is **worse than capital-exposure-matched random entries on
  both pairs** (100th percentile).
- False-DANGER cost: 47%/38% of DANGER calls false; 2,243/1,183 pp foregone.
- Canonical MaxDD figures (multiplicative, pooled-by-concatenation).
- Sharpe variant equivalence after annualisation fix.

**Unsupported (now withdrawn):**
- "Return parity" (batch 1 phrasing) — replaced by underpowered-design
  statement.
- "PPO reduces drawdown" in any form — worse than exposure-matched random.
- Any single-seed method comparison.
- Any claim that the regime gate adds economic value as configured — its
  false-DANGER rate forecloses the upside that buy-and-hold captures.

**Bottom line (batch 2):** the honest summary of this system is now: *an
underpowered single-seed evaluation of a low-exposure defensive router,
whose apparent risk control disappears under exposure matching, whose
gating signal has a 38–47% false-alarm rate on the cost-bearing side, and
whose performance is a function of market direction rather than model
quality.* The negative result is robust; every previously flattering
reading traced to a protocol or definition artifact.

---

# Batch 3 — Third corrective pass (2026-08-15)

## B3.1 Why is PPO's capital-weighted exposure 4.3%? (task 1)

**VERDICT: (B) LEARNED WITHDRAWAL** — the agent had access to positions up
to 67–100% of equity, used its ceiling when active, and learned to abstain;
the reward makes abstention rational (the permanently-flat policy scores
HIGHER than the trained policy). A structural amplifier explains the gap
between time-in-market (54–58%) and capital exposure (4–5%): the grid
engine is "deployed" on bars with zero inventory. Driving evidence:
diagnostics 1, 4 and 5 below.

**1. Action distribution** (share of steps; trained vs randomly-initialised):

| action | ETH trained | ETH untrained | BNB trained | BNB untrained |
|---|---|---|---|---|
| FLAT | **41.9%** | 0.2% | **45.8%** | 0.0% |
| grid 0.5 / 1.0 / 1.5 | 16.6/26.6/12.0% | 4.6/13.9/32.9% | 21.2/14.5/15.6% | 5.6/12.7/35.1% |
| trend 0.5 / 1.0 / 1.5 | 0.9/0.0/0.3% | —/—/39.6% | 0.2/1.0/1.3% | —/—/37.7% |
| swing (any) | 1.7% | 8.7% | 0.4% | 9.0% |

An untrained policy deploys ~100% of steps, preferring max-size actions
(trend 1.5 + grid 1.5 ≈ 72%). The trained policy spends ~42–46% FLAT and,
when active, prefers smaller grid sizes. **Abstention and size reduction
are learned, not initialised.**

**2. Position-size ceilings** (nothing near binding at 4–5% realised):

| parameter | location | value |
|---|---|---|
| `max_position_pct` | `src/rl/env.py:79` | 0.6666 (trend/swing notional ≤ 67% × equity × size_mult) |
| `grid_level_pct` | `src/rl/env.py:95-97` | 0.10 per level × 5 levels |
| size multipliers | `src/rl/action_map.py:15-26` | 0.5 / 1.0 / 1.5 max |

Available maximum ≈ 67–100% of equity (trend 1.5), 75% (grid 1.5).

**3. Binding test** (realised size ÷ available max, active steps only):

| | ETH | BNB |
|---|---|---|
| mean | 0.734 | 0.830 |
| median | 0.606 | 0.605 |
| active steps ≥ 90% of max | 41.0% | 42.7% |

> **RETRACTED (B7 task 5).** The "ceiling" above is an accounting
> convention, not an enforceable limit: `src/rl/env.py:475-484` adds
> inventory on every bar whose range crosses a buy level with NO
> cumulative cap (no max_inventory/inventory_cap exists in the engine),
> so grid inventory is effectively unbounded. The 73–83% and 41–43%
> figures are retained struck-through as the trail. **Direction: the
> agent had MORE deployable capacity than assumed and used LESS — the
> correction STRENGTHENS the withdrawal conclusion.**

~~When the agent acts, it uses most of its ceiling — 41–43% of active steps
sit at ≥90% of the maximum.~~ **The ceiling is not binding; the frequency of
acting is the constraint.**

**4. Reward decomposition** (totals over all folds; λ = 0.5, set at
`src/rl/env.py:77`, default in trainer CLI):

| term | ETH | BNB |
|---|---|---|
| PnL-vs-benchmark term | −0.579 | −0.328 |
| fee term | +0.105 | +0.125 |

*Sign convention: the fee term is reported as a magnitude — the reward
subtracts it (`- cfg.fee_rate * turnover_norm`, src/rl/env.py:356); the
agent is penalised, not rewarded, for trading. Verified: pnl − fee − dd
reproduces the reported trained total exactly (ETH −0.9938, BNB
−0.8538).*
| λ·drawdown-step term | **0.310** | **0.401** |

The drawdown penalty (λ=0.5) is **comparable to or larger than the entire
PnL term** in magnitude on both pairs. The reward punishes deployment.

**5. Abstention value** (identical timestamps):

| total reward | ETH | BNB |
|---|---|---|
| trained policy | −0.994 | −0.854 |
| permanently flat | **−0.448** | **−0.038** |

**Flat scores HIGHER than the trained policy on both pairs.** The reward
function does not distinguish the learned policy from doing nothing —
direct evidence of reward misspecification under (B).

## B3.2 False-DANGER opportunity cost — RETRACTED and replaced (task 2)

**Cause confirmed:** the summed figures counted each price move in up to 24
overlapping forward windows (748 hourly signal bars × ~3pp mean forward
return). ~~2,242.9 pp (ETH)~~ and ~~1,183.2 pp (BNB)~~ are impossible as
portfolio costs on windows where buy-and-hold returned +44.8% / +3.8%.
**Removed from the record.**

Replacements (non-overlapping):

| measure | ETH | BNB |
|---|---|---|
| mean fwd 24-bar return per false-DANGER signal | +3.34% | +3.88% |
| de-overlapped 24-bar blocks: DANGER-entry mean return (n blocks) | −0.57% (72) | −0.49% (45) |
| non-flagged blocks mean return (n) | +0.39% (95) | +0.23% (122) |
| per-block gap | 0.96pp | 0.72pp |
| **realised portfolio-level cost (gated − B&H)** | **−67.1pp** | **−20.8pp** |

Kept (sound): false-alarm rates **47% (ETH) / 38% (BNB)**; mean forward
return on DANGER bars −0.79% / −0.68%. The argument survives the
correction in weakened but clear form: DANGER calls are wrong often
enough and carry little downside edge when right, so gating forfeits
upside — the portfolio-level gap is the honest measure of that cost.

## B3.3 Seed-sweep scope annotation (task 3)

**Verified: fold coverage is the whole explanation.** Seed-42 per-fold
MaxDD in the sweep (ETH 0.0769 / 0.0490) matches the main run to four
decimals. The sweep's folds (0, 3) omit the folds that compound the
six-fold pooled drawdown (ETH fold 4: 0.120; BNB folds 1–2). Scope note
embedded in `reports/seed_sensitivity.json` with the correct reference
points:

| | sweep mean (folds 0+3) | main-run seed-42 folds 0+3 pooled | six-fold pooled (§1) |
|---|---|---|---|
| ETH MaxDD | 0.075 | 0.0776 | 0.165 |
| BNB MaxDD | 0.102 | 0.2625 | 0.293 |

**Seed-42 positioning note (Batch 5 task 4).** The main run — the
source of every headline figure in this report — uses seed 42
throughout, and on BNB that seed sits at the **unfavourable end of the
seed distribution**: its folds-0+3 pooled MaxDD is 0.2625 against a
5-seed mean of 0.102 (2.6x worse). The B5.1 flat-vs-trained sweep
corroborates this: all four seed-42 cells are negative-diff and at or
near the bad end of their cells. **Seed 42 is the pre-registered
default, fixed in code before any result was observed**: the trainer
CLI default (`--seed`, default=42, src/rl/agents/ppo_trainer.py:116-119)
and the evaluation reset (`env.reset(seed=42)`, src/rl/evaluate.py:266).
No seed was selected post hoc. The implication runs against the
flattering direction: the main run's BNB numbers are, if anything,
*pessimistic* about PPO — though not enough to change any conclusion
(no cell reverses the negative result except the already-reported BNB
fold-3 seed-999 exception).

The seed-variance-dominates verdict is unaffected.

## B3.4 Correlation retraction (task 4)

~~corr(B&H, PPO−B&H) = −0.995 (ETH) / −0.928 (BNB)~~ **removed**: the
dependent variable contains −B&H (mechanical correlation) and n=6 cannot
support a correlation estimate. The per-fold table (B2.7) stands on its
own. Replaced by the direct descriptive correlation, clearly labelled:

- corr(fold B&H return, fold PPO return): **ETH +0.677, BNB +0.475**
  (descriptive at n=6, not a test result)

Reading: PPO's returns co-move positively with the market but massively
damped — consistent with 4–5% capital deployment, not with timing skill.

## B3.5 Exposure-comparability statement (task 5)

PPO and the RF baseline operate at materially different capital
deployment: **RF/PPO exposure ratio 4.9× (ETH) and 11.5× (BNB)**. Every
PPO-vs-RF statistical comparison in this report is between policies at
different deployment levels and must be read as **descriptive only**. This
caveat applies to: the pooled paired HAC test (§1), the MaxDD/Sortino
bootstrap (§1), the seed-sweep paired statistics (B2.4).

Descriptive normalisation — return per unit of capital-weighted exposure
(**not** a risk-adjusted performance measure):

| | ETH | BNB |
|---|---|---|
| PPO | −306.9% per unit | −553.1% per unit |
| RF | −105.8% per unit | −28.1% per unit |
| Buy & hold | +44.8% | +3.8% |

Per unit of capital actually deployed, PPO loses more than RF on both
pairs, and both lose while buy-and-hold gains.

**Survival assessment: none of the statistical comparisons survive as a
meaningful method comparison.** The only exposure-fair comparisons in this
report are the capital-matched random-entry baselines (B2.2) — on those,
PPO sits at the 100th percentile (worst) on both pairs.

## B3.6 Updated supported / unsupported claims

**Supported:**
- PPO's low exposure is **learned withdrawal plus grid-structure dilution,
  not a configuration ceiling** (B3.1: flat 42–46% learned vs ~0%
  initialised; ~~ceiling used at 73–83% of max when active~~ (RETRACTED
  B7 task 5 — grid inventory uncapped, denominator was an accounting
  convention); flat policy
  out-scores trained policy).
- The reward (λ=0.5 drawdown penalty) is misspecified for deployment: its
  drawdown term rivals the whole PnL term, and abstention outscores the
  learned policy.
- False-DANGER false-alarm rates (47%/38%) and per-signal forward returns.
- De-overlapped block gaps (0.96pp / 0.72pp per 24-bar block) and the
  portfolio-level gated-minus-B&H gaps (−67.1pp / −20.8pp).
- Exposure ratios (4.9×/11.5×) and the per-unit-exposure normalisation as
  descriptive facts.
- Descriptive B&H–PPO correlations (+0.677/+0.475 at n=6, labelled).

**Unsupported / retracted:**
- ~~2,242.9 pp and 1,183.2 pp false-DANGER opportunity cost~~ (multiply
  counted; replaced above).
- ~~corr(B&H, PPO−B&H) = −0.995/−0.928~~ (mechanical, n=6).
- Any PPO-vs-RF statistical comparison read as a method comparison
  (exposure incomparability + underpowering + seed dominance).
- Seed-sweep MaxDD figures compared against six-fold pooled numbers
  (scope note added).

**Bottom line (batch 3):** the system's central anomaly — a router that
holds 4–5% of capital — is now explained, mechanically and causally: the
agent learned to abstain because the reward penalises deployment more than
it rewards PnL, and doing nothing literally outscores the learned policy.
Every method-level statistical claim in this report is now either
retracted, scope-annotated, or downgraded to descriptive. What remains
true and evidenced: the negative result (no routing edge), the exposure
explanation (learned withdrawal under a punishing reward), and the gate's
false-alarm economics at portfolio level.

---

# Batch 5 — Final corrective pass (2026-08-16)

Task 1 was the last permitted computation; Tasks 2–6 are documentation
only. Nothing was deleted — superseded text is banner-marked, never
erased.

## B5.1 Flat-policy baseline across seeds (task 1 — final computation)

The decisive flat-outscores-trained figure previously rested on seed 42
alone, while Batch 2 voids single-seed claims and B3.3 flags seed 42 as
atypical on BNB. Using ONLY the already-trained B2.4 seed models (folds
0+3, both pairs — all 20 verified present; nothing retrained):

| pair | fold | seed | trained | flat | diff | flat wins |
|---|---|---|---|---|---|---|
| ETH | 0 | 42 | +0.0066 | +0.1184 | −0.1118 | ✓ |
| ETH | 0 | 7 | −0.0397 | +0.1184 | −0.1580 | ✓ |
| ETH | 0 | 123 | +0.0549 | +0.1184 | −0.0635 | ✓ |
| ETH | 0 | 2024 | −0.0722 | +0.1184 | −0.1905 | ✓ |
| ETH | 0 | 999 | −0.0889 | +0.1184 | −0.2073 | ✓ |
| ETH | 3 | 42 | −0.0427 | +0.0529 | −0.0956 | ✓ |
| ETH | 3 | 7 | +0.0167 | +0.0529 | −0.0362 | ✓ |
| ETH | 3 | 123 | −0.1944 | +0.0529 | −0.2473 | ✓ |
| ETH | 3 | 2024 | −0.1451 | +0.0529 | −0.1980 | ✓ |
| ETH | 3 | 999 | −0.0342 | +0.0529 | −0.0870 | ✓ |
| BNB | 0 | 42 | −0.3267 | −0.0364 | −0.2903 | ✓ |
| BNB | 0 | 7 | −0.0604 | −0.0364 | −0.0240 | ✓ |
| BNB | 0 | 123 | −0.2601 | −0.0364 | −0.2237 | ✓ |
| BNB | 0 | 2024 | −0.1391 | −0.0364 | −0.1027 | ✓ |
| BNB | 0 | 999 | −0.1481 | −0.0364 | −0.1118 | ✓ |
| BNB | 3 | 42 | −0.1460 | +0.0801 | −0.2261 | ✓ |
| BNB | 3 | 7 | −0.5070 | +0.0801 | −0.5870 | ✓ |
| BNB | 3 | 123 | +0.0415 | +0.0801 | −0.0385 | ✓ |
| BNB | 3 | 2024 | −0.0660 | +0.0801 | −0.1461 | ✓ |
| BNB | 3 | 999 | **+0.0944** | +0.0801 | **+0.0143** | **✗** |

**Statement in the exact form the evidence supports: flat outscored the
trained policy in 19 of 20 seed-fold-pair cells.**

**Exception (not softened, not dropped): BNB fold 3, seed 999** — the
trained policy scored +0.0944 vs flat's +0.0801, winning by +0.0143.
Seed 999 is the same seed that produced BNB's best return in the B2.4
sweep (+3.95%). The flat-policy reward was verified identical across
seeds within each cell (deterministic; asserted in-script).

**Seed-42 cells located within the distribution:** all four are
negative-diff and at or near the bad end of their cells — ETH f0 diff
−0.1118 (cell range −0.0635..−0.2073); ETH f3 −0.0956 (−0.0362..−0.2473);
BNB f0 −0.2903 (−0.0240..−0.2903, worst in cell); BNB f3 −0.2261
(−0.0385..−0.5870). The batch-1/B3.1 headline (−0.994/−0.854 vs
−0.448/−0.038) is a six-fold pooled aggregation and is not directly one
of these cells, but the per-cell evidence strengthens rather than
weakens the claim: 19/20 with the exception named.

Evidence: `reports/flat_vs_trained_by_seed.json` + script.

## B5.2 Supersession banners (task 2)

All six banners added, original text fully retained beneath each:

1. §1 risk table MaxDD row → SUPERSEDED by B2.1 (additive →
   multiplicative; ETH 0.172/0.273/0.353 → 0.165/0.247/0.511, BNB
   0.326/0.532/0.428 → 0.293/0.426/0.503)
2. §1 invested-Sharpe row → SUPERSEDED by B2.6 (annualisation artifact;
   corrected = all-bars Sharpe)
3. §4 exposure-matched (entire) → SUPERSEDED by B2.2 (definitions
   conflated; ETH verdict REVERSED to 100th percentile, both pairs)
4. §6 supported-claims list → PARTIALLY SUPERSEDED by B2.3/B2.8/B3.6
   (parity withdrawn → underpowered-design statement)
5. Batch-1 Bottom line → SUPERSEDED by B2.8/B3.6 ("parity is confirmed"
   withdrawn)
6. B2.5 (entire) → SUPERSEDED by B3.2 (2,242.9/1,183.2 pp retracted;
   DANGER-bar mean superseded by −0.79%/−0.68%)

Plus the document-top note: Batch 1 superseded where marked; the
correction trail is retained deliberately.

## B5.3 Fee-term sign convention (task 3)

Verified against code: the fee enters the reward **negatively** —
`- cfg.fee_rate * turnover_norm`, `src/rl/env.py:356`. The B3.1 table's
+0.105/+0.125 are magnitudes (accumulated positive, subtracted
downstream); the agent is penalised for trading, not rewarded.

Decomposition-sum check (no adjustment): pnl − fee − dd reproduces the
reported trained totals exactly — ETH −0.9938, BNB −0.8538 (match to 4
decimals, both pairs). One-line convention note added under the B3.1
table.

## B5.4 Seed-42 positioning (task 4)

Documented in B3.3 and in the manuscript (§4.2 seed note + §4.3.2
paragraph): the main run uses seed 42 throughout; on BNB it sits at the
unfavourable end of the seed distribution (folds-0+3 pooled MaxDD 0.2625
vs 5-seed mean 0.102 — 2.6× worse). **Seed 42 is the pre-registered
default, fixed in code before any result was observed**: trainer CLI
default (`--seed`, default=42, `src/rl/agents/ppo_trainer.py:116-119`)
and evaluation reset (`env.reset(seed=42)`, `src/rl/evaluate.py:266`).
This forecloses any seed-selection suggestion. The implication runs
against the flattering direction (the main run's BNB numbers are
pessimistic about PPO), and every BNB-specific PPO magnitude in the
manuscript is now qualified as conditional on seed 42's position.

## B5.5 Abstract wording (task 5)

| Before | After |
|---|---|
| "proposing a **regime-aware multi-asset execution framework**" | "building a **regime-aware multi-asset trading system**" |
| "conduct a **controlled empirical benchmark**" | "conduct a **walk-forward evaluation**" |
| "two production execution engines" | "two production trading engines" |
| (RQ) "within a shared execution environment" | "within a shared trading environment" |
| (1.3) "to benchmark supervised regime routing" | "to evaluate supervised regime routing" |

Re-read findings beyond the two required changes: "execution engines"
(RQ-adjacent), "shared execution environment" (RQ), and "to benchmark"
(1.3) — all fixed. Remaining "execution" uses are the literature-review
section title (Optimal Execution — a domain name) and the methodology
MDP phrasing, retained as formal-problem description per the batch-4
report's flag.

## B5.6 DANGER figures linked (task 6)

Manuscript §4.5 now carries the linking sentence: the overall DANGER
mean (−0.79%/−0.68%) and the false-signal mean (+3.34%/+3.88%,
§3.6 item 8) are consistent — the overall mean is
negative because true signals pull it down while concealing sharp
dispersion; the asymmetry (small edge when right, large foregone gain
when wrong) is the mechanism by which accuracy fails to convert into
economic value.

---

## CODE FROZEN

**The code is now frozen. Batch 5 Task 1 was the final computation; no
further training, evaluation, or re-computation will be performed.**
All subsequent work on this project is documentation-only.

---

# Batch 6 (documentation) — Post-freeze cleanup (2026-08-16)

Documentation only. Nothing computed, nothing deleted.

## B6.1 Stray figure in B5.6 — fixed

The incomplete-edit artifact "+3.34%/−0.88% → +3.34%/+3.88%" cleaned to
the artifact-correct **+3.34%/+3.88%**
(reports/false_danger_corrected.json: ETH +3.34, BNB +3.88). Confirmed
fixed; the stray −0.88 no longer appears anywhere in this report.

## B6.2 Primary framing of the withdrawal claim — resolved

The manuscript's Chapter 4 previously opened with the single-seed
six-fold figure while its own §4.3.2 voids single-seed claims. Resolved
in `docs/dissertation_manuscript.md` §4.1:

- **PRIMARY claim:** flat outscores trained in **19 of 20 seed-fold-pair
  cells** (reports/flat_vs_trained_by_seed.json), with the exception
  stated in the claim itself, not a footnote: **BNB fold 3, seed 999 —
  trained +0.0944 vs flat +0.0801, margin +0.0143** — and seed 999 is
  the best BNB seed in the B2.4 sweep (+3.95%): the training exceeds
  abstention only at its best draw, and then only marginally.
- The six-fold pooled figures (−0.994/−0.854 vs −0.448/−0.038) DEMOTED
  to an explicitly labelled illustrative example (seed 42 only;
  broader fold scope than the sweep; one draw from the seed
  distribution).
- Cross-references aligned: the Abstract and Chapter 5.1 finding 3 now
  lead with the 19-of-20 framing and carry the exception.

## B6.3 Consistency sweep (task 4) — result

1. **Withdrawn-figure grep (manuscript):** zero unmarked instances of
   Sharpe 1.85, MaxDD −0.4%, DM p=0.14, legacy-RF p-values, 2,243/1,183
   pp, the n=6 correlations, or "return parity" as an active claim. Two
   grep hits (p=0.14; 2,243pp) verified by reading to sit inside the
   §3.6 failure-mode/retraction documentation — correctly marked.
2. **Banners:** all six present (targets B2.1, B2.2, B2.3/B2.8/B3.6,
   B2.6, B2.8/B3.6, B3.2) plus the document-top note; every target
   section exists.
3. **Traceability after Batch 5:** the multi-seed withdrawal claim
   cites `reports/flat_vs_trained_by_seed.json` (manuscript §4.1).
   **Inconsistency found and reported, not fixed:**
   MANUSCRIPT_REVISION.md (the Batch-4 report) still cites the six-fold
   figures for the withdrawal claim in its traceability table. That
   file is a dated historical record of the Batch-4 state and remains
   accurate as such; the live manuscript is correct. Left as-is
   deliberately — rewriting a dated report would falsify it.
4. **Other tracked docs:** rl_walk_forward_results.md body sits under
   its document-level SUPERSEDED banner; the study planning docs carry
   superseded-title notes. Clean.

## B6.4 Seed-42 direction-of-bias note — added

Stated explicitly in the manuscript results chapter (§4.3.2 "Direction
of bias") and in limitations (§5.3 item 3): the main run uses seed 42
throughout, fixed as the code default before any result was observed
(`src/rl/agents/ppo_trainer.py:116-119`; `src/rl/evaluate.py:266`);
across the seed sweep it sits at or near the unfavourable end of its
cells and is the worst draw in BNB fold 0; the headline figures are
therefore pessimistic toward the RL agent rather than flattering to it.

## B6.5 Supervisor report — exists

`docs/progress/2026-09_report.md` created: standalone two-page summary
(research-question move, title change rationale, four audit findings,
claimed vs withdrawn, what remains). Written for a supervisor who last
saw the pre-audit framing; the negative result is presented as the
finding it is.

---

## CODE FROZEN (restated)

**The code is frozen. No training, evaluation, or recomputation will be
performed. All remaining work is documentation.**

---

# Batch 7 — Recomputation under corrected definitions (2026-08-16)

Freeze lifted for RECOMPUTATION ONLY (no retraining, no policy
re-evaluation; every number from the committed per-bar CSVs), in
response to an independent review. Task 1 was a verification gate.

## B7.1 Task 1 — verification: DEFECT CONFIRMED

| pair | strat | stored | arithmetic | compounded | stored matches |
|---|---|---|---|---|---|
| ETH | ppo | −0.1310 | −0.1310 | −0.1278 | ARITHMETIC |
| ETH | rf | −0.2232 | −0.2232 | −0.2117 | ARITHMETIC |
| ETH | ta | +0.4483 | +0.4483 | +0.3797 | ARITHMETIC |
| BNB | ppo | −0.2902 | −0.2902 | −0.2590 | ARITHMETIC |
| BNB | rf | −0.1698 | −0.1698 | −0.2025 | ARITHMETIC |
| BNB | ta | +0.0378 | +0.0378 | **−0.0427** | ARITHMETIC |

All six stored values are arithmetic sums. The review's prediction
reproduced exactly: BNB buy-and-hold +3.78% stored vs **−4.27%**
compounded — a sign reversal. Code confirmed at the cited lines:
`evaluation_report.py:54` (`np.sum(returns)`, the walk-forward source)
vs `evaluate.py:301` (equity-curve compounded, single-window path
only).

## B7.2 Every changed number (before → after)

**Total returns (now compounded, canonical):**
- ETH: PPO −13.10% → **−12.78%** | RF −22.32% → **−21.17%** | B&H +44.83% → **+37.97%**
- BNB: PPO −29.02% → **−25.90%** | RF −16.98% → **−20.25%** | B&H +3.78% → **−4.27% (SIGN FLIP)**

**MDE (compounded conversion (1+δ)^n−1):**
- ETH: observed +9.22 → +9.66pp; MDE 59.7 → **81.7pp**
- BNB: observed −12.04 → −11.34pp; MDE 102.7 → **179.3pp**
- Power (7.2%/6.2%) and required n (20.7y/35.9y) unchanged — per-bar
  quantities. Underpowering STRENGTHENED.

**MaxDD (pooled-concatenation → per-fold distributions, median headline):**
- ETH: PPO 0.165 → **0.032** (folds .077/.008/.014/.049/.120/.007); RF 0.247 → 0.107; TA 0.511 → 0.195
- BNB: PPO 0.293 → **0.053** (folds .190/.018/.033/.105/.073/.013); RF 0.426 → 0.117; TA 0.503 → 0.198

**MaxDD-diff bootstrap (fold-cluster resampling replaces block-spanning pooled):**
- ETH: est −0.0816 [−0.342, +0.090] → est −0.0702 **[−0.121, −0.020] — now excludes zero**
- BNB: est −0.1338 [−0.381, +0.161] → est −0.0539 [−0.138, +0.022] — still straddles

**Retracted (marked in place):** grid ceiling diagnostic (73–83% of
ceiling; 41–43% ≥90% of max) — denominator was an accounting
convention, grid inventory is uncapped (`env.py:475-484`).

## B7.3 Directional conclusion, per pair

- **ETH:** unchanged — no policy beats B&H (+37.97% vs −21.17%/−12.78%).
- **BNB:** unchanged in direction, sharpened in substance — B&H is now
  NEGATIVE (−4.27%) and both policies still lose to it; additionally
  the fold-clustered MaxDD CI on ETH now excludes zero (PPO's per-fold
  drawdown advantage is consistently signed on ETH only), while the
  B2.2 exposure-matching result (worse than capital-matched random
  entry on both pairs) still governs whether any of that is skill.

## B7.4 Withdrawal diagnosis — restated on four diagnostics

The position-ceiling diagnostic is RETRACTED (B7 task 5): the
diagnostic's denominator was an accounting convention; grid inventory
is effectively unbounded. **Direction: the agent had MORE deployable
capacity than assumed and used LESS — the correction STRENGTHENS the
withdrawal conclusion.** The diagnosis now rests on: (1) flat
outscores trained in 19/20 cells; (2) action distribution 41.9%/45.8%
flat vs 0.2%/0.0% untrained; (3) reward decomposition; (4) grid
structural dilution — re-examined and retained with a corrected
mechanism (inventory stays small via sell-level realization and anchor
re-deployment, not any cap).

## B7.5 Disclosures added

1. Inferential-unit limitation (5.3 item 7): per-bar unit vs a
   procedure evaluated 6 times; fold-clustered effective n≈6; true
   power LOWER than reported; underpowering finding STRONGER.
2. Block-length claim corrected: `_heuristic_block_length` (length-only
   rule), Politis-White NOT implemented; intervals conditional on the
   heuristic choice. Report labels updated, old labels retained
   superseded.
3. Reward scoped unusually punitive: fee double-count verified and
   quoted (`env.py:333-334` equity, `:355-357` reward, code comment
   calls it amplification). Withdrawal claim qualified in 3.1, 4.1,
   5.3-6a: licenses "this specification produces abstention", NOT "RL
   generally learns abstention".
4. Training-window asymmetry: PPO fold-0 4,344 vs RF 4,320 bars
   (month rounding); OOS boundary verified to hold.
5. Provenance commits diverge; diffed (evaluation/reporting code only)
   — semantically identical for training/evaluation; single-snapshot
   provenance not demonstrated.
6. "Untrained policy" renamed from "randomly-initialised"; isolates
   learned-vs-initial behaviour, not architecture bias.
7. Two bootstrap CIs labelled (IID per-fold vs fold-cluster risk CIs).
8. Embargo restated as approximate decay tolerance (ADX/MACD
   infinite-support; warmup can reach ~30 bars past the boundary).

## B7.6 The B2.1 contiguity claim — corrected

Marked SUPERSEDED at source: the six test windows are separated by
**1,440-bar (~60-day) gaps** (verified); "contiguous chronological
segments" was FALSE. The additive-vs-multiplicative diagnosis stands;
pooled-by-concatenation aggregation does not. Superseded by per-fold
distributions + fold-cluster bootstrap (B7.2). Pooled HAC statistic
RETAINED with an explicit descriptive-only caveat (lags 1–9 cross
60-day gaps); no within-fold replacement computed — that estimand
decision is deferred per Task 6.

---

## CODE RE-FROZEN

**The code is re-frozen. Batch 7 recomputation is complete; no further
training, evaluation, or recomputation will be performed.**

---

# Batch 8 (documentation) — Post-recomputation integration (2026-08-16)

Documentation only; frozen code untouched.

## B8.1 Compounded-basis updates — every location

`docs/dissertation_manuscript.md` (11 locations):
- 4.2 ETH table: B&H +44.8→**+38.0** | RF −22.3→**−21.2** | PPO −13.1→**−12.8** (MaxDD column → per-fold medians 0.195/0.107/0.032)
- 4.2 BNB table: B&H +3.8→**−4.3** | RF −17.0→**−20.3** | PPO −29.0→**−25.9** (medians 0.198/0.117/0.053)
- 4.3.1 MDE: 59.7/102.7pp → **81.7/179.3pp** (compounded)
- Abstract: returns and MDE updated
- 5.1 findings + finding-1 narrative (BNB passive-loss qualification)
`docs/progress/2026-09_report.md`: fully rewritten (B8.4).
MANUSCRIPT_REVISION.md retains Batch-4 arithmetic figures by policy
(dated record, not rewritten).

## B8.2 The ETH significant result — as it now appears (4.3)

"Fold-clustered bootstrap on the per-fold MaxDD difference (PPO − RF)
— the project's first statistically significant result: ETH −0.070
[−0.121, −0.020] (excludes zero); BNB −0.054 [−0.138, +0.022] (does
not)." Both pairs always shown together. Mechanism sentence carried in
every statement: *"consistent with PPO's far lower capital deployment
(4.3% vs 21.1%, a 4.9x ratio), not with timing skill — if it were
skill, PPO would not sit at the 100th percentile against random
entries matched on the same capital exposure, which it does on both
pairs."* No bare "PPO reduces drawdown" anywhere. The ETH/BNB
divergence is presented as asset-selection sensitivity at effective
n=6 — the thesis's own scale argument.

## B8.3 September report — updated, three costs separated

Section 4 now states distinctly: (1) the retracted ~~2,243pp~~
(overlap-counting artifact, impossible against the +38% window);
(2) the per-block de-overlapped gap 0.96pp/0.72pp — the actual
replacement; (3) the portfolio-level gated-vs-B&H gap −67.1pp/−20.8pp.
Related quantities, not versions of one another. Seed-42
direction-of-bias and the independent-review item added; section 1
reframed from defensive to "the new question emerged from inside the
original one."

## B8.4 Contributions — as restated (5.2)

Six named diagnostics (flat-policy floor; reward-component
decomposition; capital-exposure matching; fold-aware pooling; power
reporting; seed-distribution reporting), each with cost and
what-it-would-have-caught. Scoping paragraph: each is standard in an
adjacent literature; the claim is a documented case study where each
one's absence changed a conclusion — not invention.

## B8.5 Novelty-claim sweep — none found

Only occurrences of novel/invented language are the disclaimers
themselves; the abstract claims the protocol "establishes" the result
(no invention claim); §1.5 Novelty & Contributions exists only as a
ToC entry with no body. Nothing rewritten because nothing overclaimed.

## B8.6 Pooling artifact foregrounded (3.6.1)

Dedicated subsection with the full before/after table (inflation
factors 2.3x–5.5x across all strategies/pairs) and the self-audit
argument: the false contiguity claim survived four corrective passes —
including the batch that fixed the adjacent MaxDD construction — and
was caught only by independent review, because it was a premise, not a
computation.
