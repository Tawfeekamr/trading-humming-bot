# Mean-Reversion ML Entry-Gate — Design

**Date:** 2026-06-13
**Status:** Approved (pending implementation plan)
**Owner:** tawfeekamr

## Context

The mean-reversion strategy enters on sharp price flushes (>X% in 30s) and exits at +2% TP / -4% SL. A full tick-replay backtest (6 months, 4 pairs) is being run to establish whether the *rule-based* version has an edge. Regardless of that result, the strategy's core risk is "catching a falling knife" — many flushes keep falling and hit the stop. This spec adds the project's **first outcome-prediction ML model**: a per-pair classifier that, at flush time, predicts whether the trade will be a winner (TP before SL), so the bot only enters when confidence is high.

The project already has one ML model — a per-pair **RandomForest regime classifier** (`src/ml/regime_classifier.py`, sklearn, joblib `.pkl`, consumed by the Rust engine via `runner.py`). This design reuses that pattern end-to-end.

## Goal

Improve mean-reversion's edge by gating entries with an ML model that predicts flush reversion. Validate the edge out-of-sample **before** any live wiring.

## Non-Goals

- Replacing the rule-based flush detection (the model *gates* entries; it does not detect flushes).
- Predicting optimal exits (fixed +2%/-4% stays for v1).
- A regime-model replacement (the existing ML regime stays; this is additive).
- Reinforcement learning / full learned policies.
- Modifying grid, trend, or signal strategies.

## Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| ML role | **Entry gate** — predict flush reversion; enter only when `P(revert)` is high |
| Label | **Strategy-aligned binary** — did +2% TP hit before -4% SL (within a max hold) |
| Training data | **Backtest pipeline + labeling pass** — each historical flush → one labeled row |
| Features | **Backtest trade-flow + curated context** (drop, refill, decay, cascade, regime, volatility, RSI, volume spike, hour, weekday) |
| Model | **RandomForest, per-pair** (mirrors `regime_classifier.py`) |
| Inference | **Python inference service** — Rust requests `P(revert)` at flush time |

## Approach: Phased, pilot-first (ETH)

**Phase 1 — prove the edge (ETH only, no live changes):** build label → features → train → validate, and produce an OOS edge number. **GO/NO-GO gate:** does ML-gated entry beat no-ML entry out-of-sample?
**Phase 2 — wire up (only if Phase 1 shows edge):** inference service + Rust ML gate, ETH, paper mode.
**Phase 3 — scale:** all 4 pairs, threshold tuning, then live.

Rejected alternatives: all-pairs-at-once (builds 4× the wrong thing if the approach leaks); porting the model to Rust (train/serve parity burden for v1); moving entry logic to Python (duplicates flush detection, big architecture change).

## Components

### Phase 1

**1. Labeling pass — `backtest/mean_reversion/labels.py`**
- Input: bars for a symbol/range (via `data.load_bars`).
- For each flush event (bar where `drop_frac > drop_thr`, using `features.compute_features`): take the close as entry, scan forward up to `max_hold_bars` and set `label = 1` if `high >= entry*1.02` before `low <= entry*0.96`, else `0`. (Exits resolve on bar high/low; matches the backtest's close-only SL/TP as an approximation — documented.)
- Output: a parquet dataset, one row per flush event: `{symbol, ts, label, <feature columns>}`.
- Oracle test: the labeling outcome must match `backtest.run_single`'s TP/SL result on identical synthetic flush series (the backtest is the labeling oracle).

**2. ML feature vector — `ml/flush_features.py`**
- `features_at_flush(bars, idx, bar) -> dict`: at flush bar `idx`, returns `drop_frac, bid_refill_ratio, sell_flow_decay, liq_cascade_score, regime_trending` (reused from `features.compute_features`) **plus** `volatility` (rolling std of returns), `rsi` (14), `volume_spike` (current bar volume / rolling mean), `hour`, `weekday`.
- **One function used for both training and serving** — eliminates train/serve feature drift. Inputs are the recent bars window (train: from the dataset; serve: from the Rust request).

**3. Train + validate — `ml/flush_reversion_model.py`** (mirrors `regime_classifier.py`)
- Per-pair `sklearn.ensemble.RandomForestClassifier`; `GridSearchCV` over `n_estimators/max_depth/min_samples_leaf`; `class_weight='balanced'` (flush winners may be the minority).
- Walk-forward split: train on first 2/3 of flush events, test on last 1/3 (chronological — no shuffling across the split).
- Persist `models/flush_reversion_{PAIR}.pkl` (joblib), seeded for reproducibility.
- Metrics: OOS **precision/recall of the "enter" (label=1) decision**, ROC-AUC, and a calibration check.

**4. Edge check — extend `backtest/mean_reversion/backtest.py`**
- Add an ML-gated entry mode: `entry_signal` additionally requires `P(revert) > ml_threshold` (model loaded per pair).
- Run the **same** walk-forward comparison: ML-gated vs no-ML, OOS return / Sharpe / win-rate / profit-factor.
- **The GO/NO-GO:** ML-gated must beat no-ML on OOS (not just accuracy — actual P&L). The currently-running backtest provides the no-ML baseline.

### Phase 2 (only after Phase 1 GO)

**5. Inference service — `ml/flush_inference_service.py`**
- Small HTTP endpoint (axum-independent; plain Python, e.g. Flask/FastAPI or the existing API style) that loads the per-pair `.pkl` and `features_at_flush`, accepts a request with recent bars (+ optional orderbook proxy), returns `{"p_revert": float}`.
- Deployed alongside the signal-listener Python component (same host/container family).

**6. Rust ML gate — `trading-engine-core/src/strategy/mean_reversion.rs`**
- New config: `MeanReversionConfig.ml_gate` (bool, default false) + `ml_threshold` (float) + `ml_endpoint` (URL).
- In `on_tick`, at a qualifying flush: POST the recent bar window to `ml_endpoint`; enter only if returned `p_revert >= ml_threshold`. Fail-soft: if the endpoint is unreachable, fall back to the no-ML behavior (configurable: skip or allow).

### Phase 3
- Train per-pair models for BNB/DOGE/XRP; threshold sweep; promote to live (still behind `ml_gate` config).

## Data Flow

```
aggTrades (data.py) → bars → flush events (features.py)
        ├── labels.py: forward TP/SL outcome  →  labeled dataset (parquet)
        └── flush_features.py: feature vector ─┘
                      ↓
        flush_reversion_model.py: RF train (walk-forward) → .pkl + OOS metrics
                      ↓
        backtest.py ML-gated mode: OOS edge vs no-ML  →  GO/NO-GO
                      ↓ (Phase 2)
        flush_inference_service.py  ←  mean_reversion.rs (on_tick flush) → gate entry
```

## Validation & Overfit Guards
- Walk-forward, chronological split (no cross-split leakage).
- Per-pair models (each pair's microstructure differs).
- **Profit-based evaluation**, not just accuracy — the model must improve OOS P&L vs no-ML.
- `class_weight='balanced'` + a tunable `ml_threshold` (treat as a precision/recall knob, swept on IS only).
- Feature-parity enforced by a single `features_at_flush` function (train == serve).
- Reproducibility: fixed random seed; model + feature list versioned in the `.pkl` metadata.

## Testing
- **Labeling oracle**: `labels.py` outcome matches `run_single` TP/SL on synthetic flush series.
- **Feature parity**: `features_at_flush` produces identical vectors from (a) the dataset's bars and (b) an equivalent served request.
- **Model reproducibility**: same seed + data → same `.pkl` + same OOS metrics.
- **Edge-check sanity**: on a synthetic series with an obvious reversion pattern, ML-gated ≥ no-ML.

## File Layout (Phase 1)
```
backtest/mean_reversion/labels.py        # forward TP/SL labeling pass
ml/flush_features.py                     # single train/serve feature function
ml/flush_reversion_model.py              # RF train + walk-forward validate (mirrors regime_classifier)
backtest/mean_reversion/backtest.py      # + ML-gated entry mode for the edge check
models/flush_reversion_ETH-USDT.pkl      # trained per-pair model (Phase 1 output)
tests/test_flush_labels.py
tests/test_flush_features.py
tests/test_flush_reversion_model.py
```
Phase 2 adds `ml/flush_inference_service.py` + the Rust gate in `mean_reversion.rs`.

## Open Questions for Implementation
- `max_hold_bars` for the label (how long to wait for TP/SL) — propose 180 bars at the backtest bar size (≈30 min), configurable.
- Label exit resolution: bar high/low (stricter, matches "could have exited") vs close-only (matches the current backtest). Propose high/low; document.
- Whether to include orderbook-depth proxies as features (only if a live source exists at serve time) — defer to Phase 2.
