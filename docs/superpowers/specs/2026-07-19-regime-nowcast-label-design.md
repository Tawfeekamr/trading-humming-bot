# Regime classifier: rework to a now-cast label

**Date:** 2026-07-19
**Status:** Approved (design), pending implementation
**Scope:** `src/data/label_generation.py`, `src/ml/train_regime.py`, `models/regime_*_clean.pkl`

## Context — why

The deployed regime RF (`models/regime_*_clean.pkl`, used by the `regime-pusher`
sidecar to gate grid/MR/trend) emits near-useless predictions: every pair is
labelled `ranging` at confidence 0.40–0.49, and on the May 15–Jul 15 OOS window
median confidence is **0.380** with the model predicting `ranging` 72% of the
time at accuracy **0.528** — *below* the 0.550 majority-class baseline.

Diagnosis (2026-07-19) traced this to the **label**, not the model. The trainer
(`src/ml/train_regime.py`) targets a **forward-looking** regime label
(`src/data/label_generation.py::generate_regime_labels`): "does the next 24 h
move ≥2% / draw down ≥3%?" The 14 features (ADX, RSI, volatility_ratio,
choppiness, …) describe the **current/past** regime, which has near-zero
predictive power for 24 h-ahead regime. This was confirmed by experiment:

- Retraining with a deeper forest (`max_depth=None`, 400 trees) changed almost
  nothing vs the depth-5 default — capacity was not the bottleneck.
- Adding isotonic calibration made the model *honest* about having no signal:
  99% `ranging`, **0.00 trending recall**, accuracy 0.548 (≈ baseline).

So no RF configuration can learn the forward label from these features. The fix
is to change the label to a target the features actually describe: the
**current** regime (a *now-cast*, derived from a trailing window of past bars).

## Goal

A regime classifier whose predictions are (a) accurate and calibrated OOS,
(b) responsive to actual market state, and (c) more useful than the engine's
hard ADX/CHOP thresholds — providing a single probabilistic regime signal the
Rust engine can gate on.

## Non-goals (YAGNI)

- Do **not** change the Rust engine's regime-consumption / gate logic. It
  already reads `data/regime_cache.json` and falls back to TA on low confidence;
  that path stays. We only feed it better labels.
- Do **not** touch RL, the signal engine, or any strategy code.
- Do **not** delete the forward `generate_regime_labels` — keep it for reference
  / comparison (other code may import it).
- Do **not** retrain pairs beyond the 4 already deployed (ETH/BNB/DOGE/XRP).

## Design decisions (approved)

1. **Label basis:** trailing price-action (mirror of the forward label, but
   looking backward). Non-circular: label = raw price behaviour; features =
   indicators that correlate with it.
2. **Window:** 24 bars (1 day) of 1 h bars.
3. **Danger:** drawdown-only (max drawdown within the trailing window ≤ −3%).
   Danger takes precedence over trending, mirroring the forward label.
4. **Thresholds:** `trend_thr = 0.02` (|return| over window ≥ 2%),
   `danger_thr = -0.03` (max drawdown ≤ −3%). Same values as the forward label;
   same window length ⇒ same marginal distribution ⇒ expect ~48/25/26 class
   balance. Actual counts are printed at train time; nudge if a class is starved.

## Component changes

### A. Labeler — `src/data/label_generation.py`

Add `generate_regime_labels_nowcast(df, window=24, trend_thr=0.02,
danger_thr=-0.03) -> pd.DataFrame`:

- Requires a `close` column (input is `calculate_technical_features` output, as
  today).
- For each bar `T ≥ window`, over the past window `close[T-window … T]`:
  - `window_ret = (close[T] - close[T-window]) / close[T-window]`
  - `window_dd` = **max drawdown within the window** (running peak-to-trough:
    `min over i of (close[i] - running_max)/running_max`, ≤ 0). Robust to where
    the window starts — catches a crash regardless of whether `close[T-window]`
    was a local high or low.
  - Classify:
    - `window_dd ≤ danger_thr` → `2` (danger)
    - elif `abs(window_ret) ≥ trend_thr` → `1` (trending)
    - else → `0` (ranging)
- **No lookahead:** every quantity is `≤ T`. Bars `T < window` get the `-1`
  sentinel (`_NO_LABEL`) and are dropped before training (same convention as the
  forward labeler).
- Returns a copy of `df` with an integer `regime_label` column.
- Vectorize with `pd.Series` rolling / cumulative-max for speed on 17 k+ bars.

Unit tests (new, `tests/test_regime_labels.py` or a sibling): synthetic series
asserting (a) a steady ramp → `trending`, (b) a flat series → `ranging`,
(c) a spike-down → `danger` with precedence over a subsequent recovery,
(d) first `window-1` bars are `-1`, (e) no bar uses any future close
(shift-invariance check).

### B. Trainer — `src/ml/train_regime.py`

- Replace `generate_regime_labels` → `generate_regime_labels_nowcast` in `main`.
- Train a **deeper, regularized** forest instead of the depth-5 default:
  `RandomForestClassifier(n_estimators=400, max_depth=None, min_samples_leaf=5,
  max_features="sqrt", class_weight="balanced_subsample", n_jobs=-1,
  random_state=42)`, assigned to `clf.model` before `clf.train(...)`.
- **Temporal** calibration split: sort by time, fit on the first 85%, call
  `clf.calibrate(X_cal, y_cal)` on the held-out 15% (≥500 rows ⇒ isotonic, as
  the existing `RegimeClassifier.calibrate` auto-selects). Calibrating
  out-of-sample is what makes emitted probabilities honest.
- Update the final docstring/print to say "now-cast" labelling, window=24,
  trailing return/drawdown.

### C. Retrain + validate

- Env: `/opt/anaconda3/bin/python3` (sklearn 1.6.1 — matches how models are
  pickled — with working `pandas_ta`).
- Backup of current models already at
  `models/_pre_retrain_backup_20260719/`.
- Retrain ETH, BNB, DOGE, XRP (`--train-end 2026-05-31 --months 24`).

### D. Validation gate (decides deploy / no-deploy)

OOS window 2026-05-15 → 2026-07-17 (load from 2026-04-01 so feature warmup is
absorbed before the OOS slice). Because the label is deterministic-from-past,
compute the **true** OOS label and measure real quality. **Ship only if ALL:**

1. OOS accuracy ≥ ~0.70 (vs 0.55 majority baseline).
2. Calibrated median confidence ≥ ~0.60, and the high-confidence subset has
   markedly higher accuracy than the low-confidence subset (calibration is
   honest — not confidently wrong, the failure mode of the calibrated forward
   model).
3. Predicted class mix is **responsive** across the OOS window (flips between
   ranging/trending/danger as the market does; not stuck ≥~90% on one class).
4. **Sanity vs TA:** model's `trending` calls coincide with high-ADX bars and
   `ranging` calls with high-CHOP bars (qualitative agreement with the engine's
   existing gates). If the model contradicts obvious TA, that's a red flag.
5. **Value-add check (thesis):** compare model regime calls to a simple
   `ADX>25 → trending / CHOP>50 → ranging` rule on the same window. The model
   should at least match it; ideally it's smoother/fewer whipsaws. Document the
   comparison either way.

If the gate fails, **do not deploy** — iterate on thresholds/window or
reconsider.

### E. Deploy (only after gate passes + explicit user OK)

- Commit: labeler + trainer changes + 4 retrained `_clean.pkl`.
- `models/` is volume-mounted into the `regime-pusher` container
  (`./models:/app/models:ro`) and the `_clean.pkl` files are git-tracked, so
  **no image rebuild** is needed.
- EC2: `git pull` → `docker compose -f docker-compose.rust.yml restart
  regime-pusher`.
- Verify: `docker logs trading-regime-pusher` shows per-pair regime + confidence
  that vary by pair and over time; `curl /api/v1/strategies` shows regime
  influencing strategy states.

## Honest framing (for the thesis)

This is **now-casting, not forecasting.** The label and the features both
describe recent price action, so high OOS accuracy partly reflects shared
information. The genuine contribution is a **single calibrated, denoised,
probabilistic regime signal** vs the engine's collection of hard ADX/CHOP
thresholds — and gate criterion #5 makes the "does ML beat the rules?" question
empirical rather than asserted. The earlier claim "ML regime gates my trades"
was illusory under the forward label; under the now-cast label it becomes
measurable.

## Rollback

Production models are untouched until deploy. If the new models regress live,
`cp models/_pre_retrain_backup_20260719/*.pkl models/ && git checkout --
models/` + restart the pusher restores the prior state.
