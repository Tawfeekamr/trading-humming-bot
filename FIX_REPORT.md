# Evaluation-Protocol Fix Report

**Date:** 2026-08-15
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
random-entry MaxDD medians are 0.029 (ETH) / 0.035 (BNB) vs PPO's
0.165 / 0.293 — **PPO sits at the 100th percentile (worst) on both pairs.**
The batch-1 ETH verdict "survives exposure matching" was an artifact of the
mismatched definitions. PPO's drawdown is worse than random entries at the
same capital exposure, on both pairs.

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

When the agent acts, it uses most of its ceiling — 41–43% of active steps
sit at ≥90% of the maximum. **The ceiling is not binding; the frequency of
acting is the constraint.**

**4. Reward decomposition** (totals over all folds; λ = 0.5, set at
`src/rl/env.py:77`, default in trainer CLI):

| term | ETH | BNB |
|---|---|---|
| PnL-vs-benchmark term | −0.579 | −0.328 |
| fee term | +0.105 | +0.125 |
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
  initialised; ceiling used at 73–83% of max when active; flat policy
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
