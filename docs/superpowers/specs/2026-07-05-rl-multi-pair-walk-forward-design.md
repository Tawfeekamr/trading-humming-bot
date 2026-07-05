# RL Multi-Pair + Walk-Forward OOS Harness — Design

**Status: harness IMPLEMENTED (minimal) + smoke-validated.** The pure helpers
(`walk_forward_slices`, `pool_returns`, `aggregate_dm`) and the orchestrator
(`run_walk_forward` / `main`) live in `src/rl/walk_forward.py`, unit-tested in
`tests/test_rl_walk_forward.py` (7 tests). Validated end-to-end on a 2-slice /
3k-step ETH smoke (plumbing proven; numbers meaningless at that step count).

**The full sweep is NOT run** — it's a one-command follow-up awaiting your
sign-off on the open questions below (pair count, window sizes, RF option).

> ⚠️ **RF labeling gap confirmed (blocks option A).** `src/data/label_generation.py`
> does not exist, `generate_regime_labels` is defined nowhere in the repo, and
> `backtest/ml_walk_forward.py` is broken scratch code (imports two missing
> modules + uses dummy data). The committed `regime_*.pkl` files were trained
> by code that was never committed. Closing this needs a regime-labeling
> **methodology decision** (what makes a bar ranging/trending/danger) — not a
> code fix. See "RF baseline parity" below.

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
