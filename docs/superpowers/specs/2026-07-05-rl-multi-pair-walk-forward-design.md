# RL Multi-Pair + Walk-Forward OOS Harness — Design

**Status: harness + RF trainer IMPLEMENTED.** Walk-forward pure helpers +
orchestrator in `src/rl/walk_forward.py` (7 tests). RF reproducible trainer in
`src/ml/train_regime.py` over a documented labeling scheme in
`src/data/label_generation.py` (5 tests). **Full 2-pair sweep is RUNNING.**

> ✅ **RF labeling gap RESOLVED.** `src/data/label_generation.py` now defines a
> documented, lookahead-free-per-bar 3-class forward-looking labeling
> (1=trending if |24-bar fwd ret|≥2%, 2=danger if min 24-bar fwd ret≤−3%,
> 0=ranging otherwise; danger takes precedence). `src/ml/train_regime.py`
> trains a reproducible RF per pair → `models/regime_{PAIR}_clean.pkl`
> (does NOT overwrite the legacy opaque `.pkl`). ETH clean RF trained
> (17,231 rows; ranging 48% / danger 26% / trending 25%).
>
> ⚠️ **Honest consequence:** the clean RF is a *stronger* baseline than the
> legacy opaque `.pkl`. ETH single-window OOS, PPO −3.42% vs clean RF −11.50%
> (DM p=0.096, **not** significant), versus vs legacy RF −19.37% (p=0.0147,
> significant). PPO still wins on raw return and drawdown either way; whether
> the edge is *statistically* significant depends on baseline quality. The
> walk-forward pooled test (more power) is the right arbiter — the running
> sweep uses the legacy RF; a clean-RF re-eval of the cached per-slice PPO
> models is the definitive follow-up.

---

*Original draft (kept for context): written 2026-07-05 while the user was AFK.
The single-pair/single-window benchmark (`reports/rl_benchmark.md`) is valid
but statistically thin. This spec covers the dissertation-grade harness.*

## Problem

`src/rl/evaluate.py` evaluates one PPO model on one pair over one OOS window.
The clean result (PPO −6.30%, RF −19.37%, DM p=0.061) is real but underpowered:
a single 1-month ETH window cannot support a thesis claim that *RL execution
routing systematically beats the supervised gate*. p=0.061 is one bar of data
away from either side of 0.05.

## Goals

1. **Multi-pair**: train + evaluate PPO per pair, aggregate cross-pair.
2. **Walk-forward**: rolling train/test splits so the OOS evidence spans the
   full available history, not one month.
3. **Statistical power**: pool per-bar excess returns across all (pair, window)
   OOS slices into a single HAC-robust DM test with enough n to be conclusive.
4. **Honest baseline parity**: the RF regime baseline evaluated on identical
   windows.

## Non-goals

- Not wiring any model into live trading (research only).
- Not changing the env reward, engine primitives, or observation space.
- Not per-pair hyperparameter tuning (shared PPO config across pairs).

## Architecture

New module `src/rl/walk_forward.py` orchestrating; reuses `ppo_trainer`,
`evaluate`, `data.load_klines`, `TradingEnv` unchanged.

```
for pair in PAIRS:                       # ETH, BNB, (BTC, DOGE, XRP after download)
    bars = load_klines(pair, full_history)
    for window in walk_forward_slices(bars, train_months=6, test_months=1, step_months=1):
        ppo = train_ppo(pair, train=window.train)      # --train-end = window.test_start
        rf  = ensure_rf_model(pair, train_end=window.test_start)  # see RF gap below
        slice_result = evaluate_slice(ppo, rf, bars=window.test)
        results.append((pair, window, slice_result))
aggregate(results) -> pooled DM, per-pair table, equity curves
```

- `walk_forward_slices`: emits `(train_start, train_end, test_start, test_end)`
  tuples. Train strictly precedes test (the boundary guard already enforces
  this per-model).
- `evaluate_slice`: a single-pair/window run = today's `evaluate._run_model`
  on the OOS slice, returning per-bar returns + time_in_market + round-trips.
- `aggregate`: pools `ppo_ret - rf_ret` across all slices for one HAC DM test;
  reports per-pair mean return/Sharpe/MaxDD/time_in_market + a cross-pair
   average + Bonferroni-adjusted p for per-pair DM (multiple comparisons).

## Data scope

1h kline cache currently holds **ETHUSDT, BNBUSDT** (~2 years after the 24-month
backfill in progress). RF regime `.pkl`s exist for ETH/BNB/BTC/DOGE/XRP. To run
5 pairs, download 1h klines for BTCUSDT/DOGEUSDT/XRPUSDT (the loader already
backfills on demand). Recommend starting with the 2 cached pairs to validate
the harness, then expanding.

## RF baseline parity — known reproducibility gap

`RegimeClassifier` has `train()`/`save_model()` but **no script in the repo
calls them**; the committed `regime_*.pkl` files were trained externally
(notebook/removed script) and the 3-class regime *label* definition (ranging /
trending / danger) is not in a reproducible trainer. Two options:

- **(A)** Build `src/ml/train_regime.py` — reconstruct the label logic from
  `src/data/feature_engineering.py` + the classifier's expected columns, train
  per pair on `[history_start, window.test_start)`, save. This is the right
  fix but is its own sub-project (label design must avoid lookahead).
- **(B)** Use the existing `.pkl`s as-is and accept the few-day train/OOS
  overlap per pair (as ETH has now). Faster, less rigorous.

**Recommend (A)** for the thesis; (B) as a stopgap to validate the harness.

## Compute

PPO at ~4000 fps (MPS) ⇒ 1M steps ≈ 4 min/run. 5 pairs × ~12 walk-forward
windows × 4 min ≈ **4 hours** of training. Parallelizable across pairs
(separate processes) but each is single-env; MPS serializes. Acceptable for a
one-time dissertation run; cache every trained model + slice result.

## Risks

- **RF label lookahead**: regime labels often use forward-looking windows
  (e.g., "was the next N bars trending?"). If labels leak future info into
  training features, the RF baseline is artificially strong — biases against
  the thesis. Must audit the label definition.
- **Multiple comparisons**: 5 pairs × per-pair DM ⇒ Bonferroni or report all
  with confidence intervals, else p-hacking risk.
- **Survivorship/pair selection**: choosing ETH+BNB (major, liquid) biases
  toward regimes the engines handle well. Document the selection.

## Open questions for the user

1. How many pairs — 2 (cached) to validate, or go straight to 5?
2. Walk-forward window sizes — propose train 6mo / test 1mo / step 1mo.
3. RF: option (A) rebuild a reproducible trainer, or (B) use existing `.pkl`s?
4. Is the 4-hour one-time compute acceptable, or cap at fewer windows?
