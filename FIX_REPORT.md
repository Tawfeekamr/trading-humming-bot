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
