# Empirical Audit Report

**Repository:** `trading-humming-bot`  
**Audit scope:** committed source, tracked models, reports, logs, configuration, and available backtest artifacts.  
**Audit date:** 2026-08-15  
**Method:** evidence-only review. No source or model files were modified.

## Executive conclusion

The repository contains a credible engineering prototype for **market-regime-aware multi-asset trading**, with a Python ML sidecar, Rust execution engine, Grid/Trend strategies, shared capital and risk controls, and a PPO shadow-routing path. It does **not** currently support the stronger dissertation framing or claims that the system is a validated multi-asset *execution* framework with learned regime *dynamics*.

The strongest reproducible empirical result is narrower:

> Across the recorded ETH/BNB walk-forward experiment, PPO did not significantly outperform the reproducibly trained RF baseline on pooled raw returns; the defensible observed advantage is lower simulated drawdown and lower capital exposure in the single ETH clean-model benchmark.

The original title, **“Machine Learning Regime Dynamics in Multi-Asset Execution,”** overstates three things:

1. no transition/dynamics model or transition analysis was found;
2. the implementation primarily selects and manages trading engines rather than optimizing classical order execution;
3. the clean walk-forward comparison has boundary and baseline-temporality problems, and the tracked report artifacts promised by the documentation are absent.

A technically faithful title is:

> **Market-Regime-Aware Multi-Asset Trading with Machine-Learning Gating: A Hybrid Grid–Trend Framework**

If PPO is central to the dissertation rather than an evaluation ablation:

> **Risk-Aware Multi-Asset Trading Under Market Regimes: Comparing PPO Routing with Supervised Regime Gating**

---

## 1. Diebold–Mariano test: implementation and reported results

### What is implemented

The only committed DM implementation found is `src/rl/evaluate.py:58-93`.

- Differential: `d_t = returns_a[t] - returns_b[t]` (`:69`).
- HAC variance: Newey–West/Bartlett with fixed `max_lag=5` (`:76-84`).
- Statistic: `mean(d) / sqrt(HAC variance / n)` (`:91`).
- P-value: two-sided normal approximation (`:92`).
- No Harvey–Leybourne–Newbold small-sample correction.
- No squared forecast-error loss, absolute-error loss, utility loss, or volatility-scaled loss.
- The docstring explicitly says the test operates on return differences and treats negative return as loss (`:61-65`).

`src/rl/walk_forward.py:84-100` concatenates per-bar PPO and RF return arrays across slices, truncates to the shorter length, and calls the same function. The current test suite confirms this is a paired return-difference test (`tests/test_rl_evaluate.py:30-62`, `tests/test_rl_walk_forward.py:68-77`).

### What the manuscript says

`docs/dissertation_manuscript.md:181-186` defines a squared-error differential:

> `d_t = e_Supervised,t^2 - e_RL,t^2`

and reports `DM = 1.48`, `p = 0.14`.

That is not the implemented statistic. The code tests differences in realized per-bar returns, not predictive errors. The report wording “identical predictive/trading accuracy” (`reports/rl_benchmark.md:14-20`) is therefore broader than the implementation supports.

### Recorded outcomes

| Artifact | Comparison | Statistic | p-value | n | Interpretation |
|---|---:|---:|---:|---:|---|
| `reports/rl_benchmark.md:14-20` | PPO vs RF, single ETH window | 1.6672 | 0.0955 | not recorded | not significant at 5% |
| `models/rl/_walk_forward_full.log:123258-123264` | PPO vs legacy RF, ETH | 3.106 | 0.0019 | 4,620 | significant, but legacy RF is opaque/weak |
| same log | PPO vs legacy RF, BNB | 4.077 | 0.0000 | 4,620 | significant, but legacy RF is opaque/weak |
| `docs/rl_walk_forward_results.md:8-20` | PPO vs clean RF, ETH | 0.30 | 0.77 | not recorded in report | not significant |
| same document | PPO vs clean RF, BNB | 0.71 | 0.48 | not recorded in report | not significant |
| manuscript `docs/dissertation_manuscript.md:181-186` | unspecified loss differential | 1.48 | 0.14 | not recorded | not the committed implementation |

### Audit judgment

**Verified:** a deterministic HAC return-difference test exists.  
**Verified:** the clean-RF result documented in the repository is non-significant.  
**Red flag:** the manuscript's statistical definition and conclusion are not traceable to the current implementation.  
**Red flag:** the significant legacy-RF results are not evidence that PPO beats a strong supervised baseline; the repository itself says the legacy label definition is unrecoverable (`src/data/label_generation.py:4-9`).

---

## 2. Walk-forward protocol, windows, embargo, and temporal boundaries

### Protocol actually encoded

`src/rl/walk_forward.py:28-47` creates chronological splits:

- `train_bars = 4,320` by default (`:408-410`), approximately 180 one-hour days;
- `test_bars = 720`, approximately 30 one-hour days;
- `step_bars = 2,160`, approximately 90 days;
- `embargo_bars = 0` by default (`:411`);
- optional embargo is supported by the splitter (`:33-45`);
- six slices and approximately 4,620 pooled bars per pair are recorded in `docs/rl_walk_forward_results.md:1-4`.

PPO training is launched with a date boundary derived from the exclusive end of the train slice (`src/rl/walk_forward.py:225-230`; `strict_training_end_date` at `:50-58`). The trainer itself supports an explicit `--train-end` (`src/ml/train_regime.py:35-45`, `:60-63`). This is a sound boundary concept for PPO training.

### Critical evaluation-boundary defect

The evaluation path does not start at the declared test boundary.

- `run_walk_forward` prepends `warmup=100` bars to `test_df` (`src/rl/walk_forward.py:203`, `:231`).
- `_evaluate_slice` creates `EnvConfig(window_length=len(test_df))` without overriding `warmup_bars` (`src/rl/walk_forward.py:137-146`).
- `EnvConfig.warmup_bars` defaults to 50 (`src/rl/env.py:88-91`).
- When `window_length == len(test_df)`, `_pick_window_start` falls back to index 50 (`src/rl/env.py:615-625`).
- `_run_model` begins collecting returns on the first `env.step()` (`src/rl/evaluate.py:237-260`).
- The declared test boundary is at index 100 of `test_df`; PPO/RF therefore include roughly 49 bars before the test boundary in their return arrays.
- The TA comparator drops 100 warmup bars (`src/rl/walk_forward.py:237-239`), so it starts at a different timestamp from PPO/RF. The arrays are then truncated by length, not aligned by timestamp.

This is not future leakage in the feature formula; it is **train-period return contamination and cross-comparator timestamp misalignment**. It invalidates the statement that every compared return is strictly OOS at the declared test boundary.

The same defect is inherited by `scripts/wf_reeval_clean.py:35-50`, which reuses `_evaluate_slice`.

### Clean RF temporal problem

`scripts/wf_reeval_clean.py:27-31` selects one clean RF artifact and reuses it for every cached PPO slice (`:44-50`). It does not train an RF model per fold. The documented clean model was trained as one long historical model using the trainer's single `--train-end` mechanism, not as six fold-specific models. Consequently, early test windows are evaluated against an RF that can contain later-period observations.

The repository's phrase “clean RF” is therefore accurate about the label definition, but not sufficient to establish fold-pure OOS comparison. The report itself acknowledges missing RF provenance for the single-window benchmark (`reports/rl_benchmark.md:23-27`).

### Moving-date reproducibility problem

`scripts/wf_reeval_clean.py:38-41` uses `date.today()` to reload history and recreate slices. The cached PPO slice models were produced on 2026-07-05, while the audit date is 2026-08-15. A rerun can shift the history window and slice timestamps relative to the cached models. `src/rl/walk_forward.py:420-421` has the same moving-date behavior. This is incompatible with exact reproduction unless the historical end date is pinned.

### Audit judgment

**Partially verified:** chronological PPO train boundaries and optional embargo machinery exist.  
**Not verified:** strict OOS returns at the reported test boundary.  
**Failed criterion:** the stored/default walk-forward run uses no embargo and includes pre-boundary PPO/RF bars through the environment warmup path.  
**Failed criterion:** clean RF is not fold-specific and has no artifact provenance.

---

## 3. Labels, horizons, and leakage ranges

### Legacy models

The committed legacy regime models were trained by uncommitted code; their labels are explicitly unrecoverable (`src/data/label_generation.py:4-9`). The live Python strategy loads `models/regime_{pair}.pkl` (`hummingbot_files/scripts/ta_grid_trend.py:1161-1175`), not the `_clean.pkl` files used for the later RF comparison.

Therefore the legacy live baseline cannot be independently audited for:

- label horizon;
- threshold values;
- danger precedence;
- train/calibration split;
- training cutoff;
- feature preprocessing.

### Reproducible clean labeler

The clean forward labeler is defined in `src/data/label_generation.py:11-25`, `:41-83`:

- horizon: 24 one-hour bars;
- trending: absolute 24-bar forward return at least 2%;
- danger: minimum forward return within the horizon at most -3%;
- danger has precedence;
- final horizon rows are unlabeled.

The clean trainer now uses a trailing now-cast label (`src/ml/train_regime.py:54-72`), defined in `src/data/label_generation.py:86-138`:

- the label uses the past 24 bars ending at the current bar;
- danger is a trailing-window drawdown of at least 3%;
- trending is an absolute trailing return of at least 2%;
- the first 23 bars are unlabeled.

This is a legitimate causal target for current-state classification. It is not a transition/dynamics model: no state-transition matrix, duration model, hazard model, HMM, or transition-conditioned policy was found.

### Training/calibration split

The clean trainer sorts by time and uses the first 85% for RF fit and the final 15% for isotonic calibration (`src/ml/train_regime.py:77-83`). This is temporally ordered and avoids fitting the calibrator on the RF fit rows.

### Feature causality

The current feature implementation uses rolling/current-bar calculations (`src/data/feature_engineering.py:41-87`) and the RL feature wrapper forward-fills only past warmup values then zero-fills (`src/rl/features.py:44-50`). No obvious future feature operation was found. However:

- the declared evaluation boundary defect above remains;
- the repository does not retain a per-row feature-availability audit;
- model training uses completed historical bars, while live candle acquisition and close completeness are not demonstrated in the empirical artifacts.

### Label/feature contract mismatch in documentation

The manuscript lists “Cross-Asset Volatility Correlation” as feature 14 (`docs/dissertation_manuscript.md:139-155`). The canonical contract's feature 14 is `aroon_oscillator` (`src/data/feature_contract.py:12-27`). No cross-asset volatility-correlation feature is in the current 14-column contract.

### Audit judgment

**Clean label scheme:** defined and causal for now-casting.  
**Legacy label scheme:** unrecoverable.  
**Dynamics claim:** unsupported by the implemented labeler and evaluation.  
**Leakage status:** no obvious forward feature computation, but walk-forward return-boundary contamination and non-fold-specific clean RF remain material.

---

## 4. Features, scaling, calibration, and model artifacts

### Features

The canonical supervised feature contract contains 14 market features plus three time features (`src/data/feature_contract.py:12-30`). The RL feature wrapper intentionally uses the same 14 market features plus hour sine, hour cosine, and day-of-week (`src/rl/features.py:1-12`, `:23-50`).

The production documentation and manuscript are not fully synchronized with this contract. In particular, the manuscript's cross-asset feature claim is not implemented in the canonical contract.

### Scaling

No `StandardScaler`, `MinMaxScaler`, `RobustScaler`, or equivalent preprocessing was found in the clean trainer or classifier path. This is not inherently invalid for a Random Forest, but it must be stated: the model is tree-based and receives heterogeneous indicator scales directly. There is no persisted scaler artifact to reproduce or validate.

### RF configuration

The clean trainer uses:

- 200 trees;
- unlimited depth;
- `min_samples_leaf=5`;
- `max_features="sqrt"`;
- `class_weight="balanced_subsample"`;
- `random_state=42`;
- isotonic calibration on the held-out tail (`src/ml/train_regime.py:101-118`).

The legacy model hyperparameters and class weighting are not recoverable.

### Calibration evidence

Calibration is implemented, but the repository does not contain a retained calibration report for the clean artifacts: no per-pair ECE, reliability diagram data, Brier score, calibration slope/intercept, or held-out calibration sample count was found in `reports/` or `models/`. The manuscript states `ECE=0.03` (`docs/dissertation_manuscript.md:157-164`) without a traceable per-model artifact or calculation output.

### Artifact provenance

The tracked clean model files expose only a small pickle payload in the observed inspection (`model`, `calibrated_model`, `model_type`, and version-like fields); the model metadata sidecars with immutable training windows promised by the documentation are absent for the RF models. The PPO model has a provenance sidecar (`models/rl/ppo_ETHUSDT_2026-07-05_clean-oos-24m.json`) containing dates, data hash, hyperparameters, seed, and training timestamp.

A further reproducibility risk was observed while inspecting persisted models: scikit-learn emitted an `InconsistentVersionWarning` for models serialized under 1.6.1 and loaded under 1.8.0. This is an artifact compatibility risk, not evidence of a numerical failure, but it must be disclosed.

### Audit judgment

**Verified:** feature contract and clean RF calibration code.  
**Missing:** retained calibration metrics, RF provenance, scaler metadata, and model-version lock.  
**Documentation mismatch:** feature list and calibration claims exceed stored evidence.

---

## 5. Classification metrics, class balance, and baselines

### What is available

`scripts/eval_regime_oos.py:1-80` evaluates OOS accuracy, median/mean confidence, top-quartile confidence accuracy, true class mix, and predicted class mix. It also reports the OLD-vs-NEW comparison. The script prints results but does not write a structured result artifact.

The implementation and documents mention criteria for trending/danger recall (`docs/superpowers/plans/2026-07-19-regime-nowcast-label.md:403-410`), but no committed per-pair confusion matrices, precision, recall, F1, balanced accuracy, MCC, or support table was found.

### Reported classification values

A repository commit summary records clean OOS accuracies approximately as ETH 0.84, BNB 0.87, DOGE 0.78, and XRP 0.82, with calibrated median confidence approximately ETH 0.85, BNB 0.86, DOGE 0.85, XRP 0.91. These are commit-history claims, not retained evaluator output. The evaluator source only establishes that such values could have been printed; it does not preserve them.

No audit-ready class counts per pair were found. The trainer prints training counts at runtime (`src/ml/train_regime.py:84-88`), but those logs are not retained as model metadata.

### Baselines

The evaluator labels approximately 0.55 as a baseline in its print path, but no retained per-pair majority-class calculation was found. Because the class distribution is generated by thresholds, a single approximate baseline is insufficient. The audit could not verify that the reported accuracy improvements exceed a separately calculated majority baseline for every pair.

### Audit judgment

**Not audit-ready:** classification headline accuracy is not accompanied by retained confusion matrices, class support, per-class recall, or calibration metrics.  
**Claim status:** the reported accuracies are plausible repository-history claims but not independently reproducible from committed output alone.

---

## 6. Return series and performance metrics

### Return construction

The RL evaluator collects net equity changes after simulated execution fees (`src/rl/evaluate.py:247-268`). It computes annualized Sharpe with `sqrt(8760)` (`:270-281`), includes flat/zero-return bars, and records exposure separately. The single-window report explicitly documents this choice (`reports/rl_benchmark.md:28-33`).

The walk-forward harness pools per-bar arrays across slices (`src/rl/walk_forward.py:61-81`, `:267-269`). The recorded walk-forward n is 4,620 per comparison. The underlying per-bar arrays are not retained in the repository; only summary claims and model logs are available.

### Available single-window metrics

`reports/rl_benchmark.md:3-12` retains:

- total return;
- maximum drawdown;
- Sharpe;
- win rate;
- time in market;
- final equity.

For ETH 2026-06-01 to 2026-07-04:

- Buy & Hold: -4.06% return, 19.22% max drawdown;
- clean RF: -11.50% return, 13.90% max drawdown;
- PPO: -3.42% return, 5.30% max drawdown, 52.3% time in market.

### Missing or incomplete metrics

No retained artifact gives all of the following for each strategy and each fold:

- exact return-array timestamps;
- exact return count for the single window;
- fee total and slippage total tied to the result;
- average trade PnL;
- median trade PnL;
- trade count by engine;
- holding-period distribution;
- turnover;
- Sortino, Calmar, or Omega;
- confidence intervals for the reported single-window Sharpe/return/drawdown.

`src/ml/evaluation_report.py` can calculate a different summary set including profit factor, fees, trade count and bootstrap intervals, but the promised `reports/rl_walk_forward_<PAIR>.json` files are absent. A glob over `reports/rl_walk_forward_*.json` found no files.

### Baseline comparability

Buy & Hold is present in the single-window report. The walk-forward source also constructs passive TA returns (`src/rl/walk_forward.py:150-157`), but its timestamp alignment differs from PPO/RF because of the warmup defect described above. No complete multi-pair portfolio-level benchmark series was found.

### Audit judgment

**Verified:** a net per-bar equity return stream and summary metrics exist.  
**Not verified:** timestamp-aligned, fold-pure, retained return arrays.  
**Result interpretation:** PPO's lower drawdown and lower exposure are supported by the single-window report, but the clean pooled return-parity claim is not independently rerunnable from immutable report artifacts in the current tree.

---

## 7. Implemented strategy engines and hybrid scope

### Python strategy surface

The production Hummingbot script identifies its active engines as “Grid + Trend,” with optional Signal (`hummingbot_files/scripts/ta_grid_trend.py:658-666`). It contains ML prediction, confidence-aware grid sizing, trend entry gates, BTC correlation logging, circuit breakers, capital budgets, and order management.

The Python ML path:

- loads per-pair legacy `regime_{pair}.pkl` (`:1161-1175`);
- predicts on the latest technical feature row (`:1189-1208`);
- applies a local ATR/return danger override (`:1210-1218`);
- scales grid capital for RANGING/TRENDING (`:1130-1139`);
- blocks trend entries for DANGER and confidence conditions (`:1780-1790`).

### Rust strategy surface

The Rust executable constructs a Grid strategy for every enabled pair and a Trend strategy only for configured pairs (`trading-engine-core/src/main.rs:119-149`). The current YAML enables trend gating and excludes ETH from the Rust trend-only list (`config/strategy.yaml:143-154`). The repository contains Rust modules for additional engines/configuration, but the inspected production constructor does not instantiate a Swing or Mean-Reversion strategy in this path.

Rust receives ML regime data through a cache/API path (`trading-engine-core/src/engine.rs:636-668`; `trading-engine-core/src/main.rs:94-107`), not through an automatically trained/in-process classifier. The direct ONNX classifier exists (`trading-engine-core/src/ml/regime.rs:20-76`), but no production construction/use of `RegimeClassifier::new` was found in the executable path. The active architecture is therefore a Python regime/routing sidecar plus Rust execution, not a single end-to-end learned Rust inference pipeline.

### PPO scope

The PPO model is a simplified bar-level router. `src/rl/env.py:1-13` explicitly says its primitives are simplified and do not match production engines tick-for-tick. The evaluation model routes among flat/grid/trend/swing-like primitives, but this is not evidence that the production Rust/Python engines have been faithfully simulated.

### Audit judgment

**Defensible:** “hybrid regime-aware trading framework” or “hybrid Grid–Trend framework.”  
**Too strong:** “multi-asset execution” if execution means implementation-shortfall/order-placement optimization.  
**Unsupported:** “regime dynamics” unless transition statistics or a temporal regime model are added to the research object.

---

## 8. Backtest, paper, live, and data provenance

### Backtest evidence

The RL artifacts are historical OHLCV replay simulations. `src/rl/data.py:1-13` describes one-hour Binance kline loading, and `src/rl/env.py:1-13` describes simplified bar-level engine primitives. The stored single-window and walk-forward results are therefore backtests/simulations, not paper or live performance.

Separate Rust replay artifacts exist under `backtest/results/replay/`; the inspected replay report is a DOGEUSDT run with zero return and no trades. It is not the source of the PPO/RF results and should not be mixed into the dissertation's ML evidence.

### Paper/live configuration

The current configuration uses Binance testnet credentials and `testnet: true` (`config/strategy.yaml:29-34`). PPO routing is explicitly `shadow` (`:75-83`), and the configuration states the sidecar does not update the active routing cache in shadow mode (`:75-81`). No `shadow_routing.jsonl` artifact was found. No retained paper PnL report or live promotion report was found.

The Rust executable logs that testnet mode uses the paper-trade connector (`trading-engine-core/src/main.rs:42-55`), but this is configuration/runtime evidence, not a performance result.

### Data quality and incident history

The repository contains a price-integrity filter with rolling outlier detection and REST verification (`config/strategy.yaml:62-73`). The recorded July 2026 PPO results predate later price-sanity work and no regenerated PPO/RF report tied to the corrected data path was found. Historical audit notes also identify a bad-tick/phantom-loss incident. This creates a provenance gap between result date, data-quality controls, and current runtime behavior.

### Audit judgment

**Backtest:** evidenced.  
**Paper/live performance:** not evidenced.  
**Current routing status:** shadow-only by configuration, not a validated live execution result.  
**Data-quality comparability:** not established across the result and current runtime configurations.

---

## 9. Regime-conditioned/scenario performance

No retained table was found for performance conditioned on RANGING, TRENDING, or DANGER. The available reports do not provide:

- return by predicted regime;
- drawdown by predicted regime;
- trade count/support by regime;
- calibration-conditioned PnL;
- BTC-DANGER gate impact;
- trend-vs-grid attribution by regime;
- scenario groups such as trend, range, crash, and recovery.

The implementation contains regime gates and attribution fields in the Rust trade journal (`trading-engine-core/src/strategy/trade_journal.rs`), but the available current database rows are operational records, not a completed historical experiment report. The dissertation table (`docs/dissertation_manuscript.md:173-179`) is aggregate-only and does not establish scenario robustness.

**Audit judgment:** the claimed regime-aware behavior is implemented in code paths, but regime-conditioned empirical performance is missing.

---

## 10. Robustness and sensitivity analysis

### Present

- PPO uses seed 42 in the tracked provenance sidecars.
- RF uses `random_state=42` (`src/ml/train_regime.py:105-113`).
- A six-window ETH/BNB walk-forward exists.
- A legacy-vs-clean RF comparison exists.
- The repository acknowledges multiple-comparison concerns (`docs/rl_walk_forward_results.md:53-64`).

### Missing

No RL-specific sensitivity analysis was found for:

- PPO seeds;
- PPO timesteps or early stopping;
- observation/reward parameters;
- transaction fees;
- slippage;
- embargo length;
- train/test window length;
- HAC lag length;
- RF depth/leaf size/class weighting;
- label horizon or threshold;
- alternative regime definitions;
- asset universe beyond ETH and BNB in the recorded walk-forward result.

The available `reports/parameter_sweep_results.csv` is an older Grid/TA parameter sweep, not an RL or classifier robustness experiment (`:1-23`).

The clean walk-forward document explicitly states only two pairs were available, hyperparameter/timestep sensitivity was not checked, and comparisons were not Bonferroni-corrected (`docs/rl_walk_forward_results.md:53-64`).

**Audit judgment:** robustness is exploratory, not thesis-grade.

---

## 11. Reproducibility and artifact lineage

### Positive evidence

- Source code for the clean labeler and trainer is committed.
- PPO sidecars retain model path, git SHA, data hash, date range, hyperparameters, seed, and timestamp (`models/rl/ppo_ETHUSDT_2026-07-05_clean-oos-24m.json`).
- Walk-forward source has report metadata hooks including feature hash, fees, slippage, embargo and slice settings (`src/rl/walk_forward.py:378-386`).
- The codebase has a canonical feature contract (`src/data/feature_contract.py:1-40`).

### Missing evidence

- promised `reports/rl_walk_forward_<PAIR>.json` files are absent;
- raw per-bar PPO/RF/TA return arrays are absent;
- RF clean model sidecars with immutable dates/data hashes are absent;
- legacy RF training source and labels are absent;
- classification OOS stdout is not retained as structured data;
- calibration metrics and confusion matrices are absent;
- the exact data snapshot used for each walk-forward run is not stored as a versioned archive;
- walk-forward CLI uses `date.today()` instead of a pinned evaluation end date;
- persisted model software versions are not locked, and a scikit-learn version mismatch warning was observed.

**Audit judgment:** PPO lineage is materially better than RF lineage, but the end-to-end empirical claim cannot be independently reproduced from immutable tracked artifacts alone.

---

## 12. Ranked empirical red flags

### Critical

1. **Walk-forward evaluation returns cross the declared OOS boundary.** The 100-bar external warmup is paired with a 50-bar environment warmup; PPO/RF collect approximately 49 pre-test bars, while TA starts after 100 bars. (`src/rl/walk_forward.py:225-239`; `src/rl/env.py:615-625`; `src/rl/evaluate.py:237-268`.)
2. **The clean RF comparison is not fold-pure.** One clean RF artifact is reused against all cached PPO slices (`scripts/wf_reeval_clean.py:27-50`), and RF provenance is not stored.
3. **The manuscript reports a different DM test than the code runs.** The manuscript uses squared predictive-error loss; the code uses raw return differences with fixed-lag HAC (`docs/dissertation_manuscript.md:181-186`; `src/rl/evaluate.py:58-93`).
4. **Legacy baseline superiority is not interpretable.** Legacy labels/training are unrecoverable (`src/data/label_generation.py:4-9`), so the significant legacy-RF p-values cannot support a method comparison.

### High

5. **The documented walk-forward JSON evidence is absent.** No `reports/rl_walk_forward_*.json` files exist despite `docs/rl_walk_forward_results.md:67-88` claiming they are emitted and retained.
6. **The live model path and evaluated clean model path differ.** Production Python loads `regime_{pair}.pkl` (`ta_grid_trend.py:1161-1175`), while clean evaluation uses `regime_{pair}_clean.pkl`.
7. **No regime-conditioned performance table exists.** Aggregate returns do not establish that the regime gate works in RANGING, TRENDING, DANGER, crash, or recovery conditions.
8. **Reported classifier quality is not retained in audit form.** Accuracy claims lack stored class support, confusion matrices, per-class metrics, and calibration artifacts.
9. **The result date and data-integrity controls are not aligned.** Current price-sanity controls and later runtime changes are not reflected in a regenerated, immutable ML result package.

### Medium

10. **The title says dynamics without a dynamics analysis.** The code classifies current/trailing state; no transitions, durations, or temporal-state model were found.
11. **“Execution” is broader than the experimental object.** The RL environment routes simplified bar-level strategy primitives; it does not model order-book execution, queue priority, implementation shortfall, or market impact (`src/rl/env.py:1-13`).
12. **Only one seed and limited assets are retained for the central walk-forward claim.** ETH and BNB are documented; sensitivity and wider asset validation are absent.
13. **Moving-date evaluation undermines exact replay.** Both walk-forward entry points derive history from `date.today()` (`src/rl/walk_forward.py:420-421`; `scripts/wf_reeval_clean.py:38-41`).
14. **Feature/documentation contract drift exists.** The manuscript's feature 14 is not the canonical feature 14 (`docs/dissertation_manuscript.md:139-155`; `src/data/feature_contract.py:12-27`).

---

## Final academic positioning

The repository supports a research contribution about **risk-aware regime-gated trading orchestration** and a useful negative result: a supervised regime baseline is competitive with PPO on raw returns, while PPO can reduce simulated exposure and drawdown in the reported ETH window.

It does not currently support these stronger claims without a new controlled evidence package:

- PPO statistically outperforms a strong supervised baseline;
- the system learns regime dynamics;
- the system is a validated multi-asset execution optimizer;
- live or paper trading confirms the backtest results;
- classifier calibration and per-regime performance are fully demonstrated.

Recommended title for the current code and evidence:

> **Market-Regime-Aware Multi-Asset Trading with Machine-Learning Gating: A Hybrid Grid–Trend Framework**

Recommended result claim:

> **PPO routing achieved return parity, not return dominance, against a reproducible supervised regime baseline in the recorded ETH/BNB walk-forward experiment; its strongest observed benefit was simulated drawdown and exposure control.**
