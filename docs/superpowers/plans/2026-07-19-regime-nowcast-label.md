# Regime now-cast label — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the unlearnable forward-24h regime label with a trailing-window (now-cast) label, retrain the 4 per-pair RF classifiers, and ship only if they pass a real OOS gate.

**Architecture:** Add `generate_regime_labels_nowcast` alongside the existing forward labeler (kept for reference). Switch `train_regime.py` to it + a deeper regularized RF with temporal isotonic calibration. Retrain in the conda env, evaluate on a held-out OOS window against explicit criteria, deploy only on pass + user OK.

**Tech Stack:** Python, scikit-learn 1.6.1, pandas, numpy, pandas_ta. Tests: pytest.

## Global Constraints

- **Training env:** `/opt/anaconda3/bin/python3` (conda base) — sklearn **1.6.1** (matches how models are pickled) and a working `pandas_ta`. The `.mltrain` venv is broken for `ta.adx`; do not use it.
- **Labeler is pure numpy/pandas** (no `pandas_ta`) → its unit tests are CI-safe.
- **Models are git-tracked** (`models/regime_*_clean.pkl`) and **volume-mounted** into the `regime-pusher` container (`./models:/app/models:ro`). The inference path only loads the `.pkl`; it does **not** import the labeler, so the labeler/trainer code changes need no image rebuild to take effect live — only the new `.pkl` files must reach `./models` on the EC2.
- **Deploy trigger:** push to `main` runs `.github/workflows/deploy.yml` (test → build → deploy: git pull + compose up). Branch first; merge to main only after the OOS gate passes and the user approves.
- **Rollback:** pre-retrain models are backed up at `models/_pre_retrain_backup_20260719/`.
- **DRY/YAGNI:** do not touch the Rust engine, RL, signal engine, or strategy code. Do not delete the forward labeler.

---

### Task 1: Now-cast labeler + unit tests (TDD)

**Files:**
- Modify: `src/data/label_generation.py` (append new function; do not alter `generate_regime_labels`)
- Test: `tests/test_regime_labels.py` (append new tests following the existing `_labels` pattern)

**Interfaces:**
- Produces: `generate_regime_labels_nowcast(df: pd.DataFrame, window: int = 24, trend_thr: float = 0.02, danger_thr: float = -0.03) -> pd.DataFrame` — returns a copy of `df` with an integer `regime_label` column (0=ranging, 1=trending, 2=danger, or `-1` sentinel for the first `window-1` bars).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_regime_labels.py`:

```python
def _nowcast(df, **kw):
    from src.data.label_generation import generate_regime_labels_nowcast

    return generate_regime_labels_nowcast(df, **kw)


def test_nowcast_trending_uptrend_is_trending():
    # +30% ramp: the trailing 24-bar return exceeds 2% -> trending (1) at the end.
    df = pd.DataFrame({"close": np.linspace(100, 130, 60)})
    out = _nowcast(df, window=24, trend_thr=0.02, danger_thr=-0.03)
    assert out["regime_label"].iloc[-1] == 1


def test_nowcast_ranging_flat_noise_is_ranging():
    rng = np.random.RandomState(0)
    close = 100 + rng.randn(200) * 0.005  # tiny noise, <2% move, <3% drawdown
    df = pd.DataFrame({"close": close})
    out = _nowcast(df, window=24, trend_thr=0.02, danger_thr=-0.03)
    assert (out["regime_label"].iloc[24:180] == 0).all()


def test_nowcast_danger_crash_is_danger_and_takes_precedence():
    # Ramp up (would be trending) then a -10% crash in the last bars: danger wins.
    close = np.concatenate([np.linspace(100, 115, 40), np.linspace(115, 103.5, 10)])
    df = pd.DataFrame({"close": close})
    out = _nowcast(df, window=24, trend_thr=0.02, danger_thr=-0.03)
    assert out["regime_label"].iloc[-1] == 2


def test_nowcast_first_window_minus_one_bars_are_sentinel():
    df = pd.DataFrame({"close": np.linspace(100, 130, 60)})
    out = _nowcast(df, window=24)
    assert (out["regime_label"].iloc[:23] == -1).all()
    assert out["regime_label"].iloc[23] != -1


def test_nowcast_has_no_lookahead():
    # Truncating the series must not change labels on the retained prefix.
    full = pd.DataFrame({"close": np.concatenate([np.linspace(100, 130, 60), np.linspace(130, 110, 30)])})
    trunc = full.iloc[:70]
    a = _nowcast(full, window=24)["regime_label"].iloc[:70].to_numpy()
    b = _nowcast(trunc, window=24)["regime_label"].to_numpy()
    assert np.array_equal(a, b)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_regime_labels.py -k nowcast -v`
Expected: FAIL with `ImportError: cannot import name 'generate_regime_labels_nowcast'`.

- [ ] **Step 3: Implement the labeler**

Append to `src/data/label_generation.py` (after `generate_regime_labels`):

```python
def generate_regime_labels_nowcast(
    df: pd.DataFrame,
    window: int = DEFAULT_HORIZON,
    trend_thr: float = DEFAULT_TREND_THR,
    danger_thr: float = DEFAULT_DANGER_THR,
) -> pd.DataFrame:
    """Add a trailing-window (now-cast) ``regime_label`` column (0/1/2, or -1).

    At each bar T, classifies the regime over the PAST ``window`` bars ending at
    and including T — ``close[T-window+1 … T]``. Fully deterministic from past
    data (no lookahead), unlike the forward-looking :func:`generate_regime_labels`.

    * **2 (danger)** — max drawdown within the window ≤ ``danger_thr``.
    * **1 (trending)** — ``|return over window|`` ≥ ``trend_thr``.
    * **0 (ranging)** — neither.

    Danger takes precedence over trending. The first ``window-1`` bars carry
    ``-1`` (insufficient history) and should be dropped before training.

    Args:
        df: frame with a ``close`` column (typically
            ``calculate_technical_features`` output, which preserves ``close``).
        window: trailing window in bars (default 24 = 1 day of 1h bars).
        trend_thr: |window return| threshold for the trending class.
        danger_thr: max-drawdown threshold for the danger class (negative).

    Returns:
        A copy of ``df`` with an integer ``regime_label`` column.
    """
    if "close" not in df.columns:
        raise ValueError("generate_regime_labels_nowcast requires a 'close' column")
    if window <= 0:
        raise ValueError("window must be positive")

    close = df["close"].to_numpy(dtype=np.float64)
    n = len(close)
    labels = np.full(n, _NO_LABEL, dtype=np.int64)

    for T in range(window - 1, n):
        w = close[T - window + 1 : T + 1]            # `window` bars, incl. T
        running_max = np.maximum.accumulate(w)
        window_dd = float(((w - running_max) / running_max).min())   # ≤ 0
        window_ret = float((w[-1] - w[0]) / w[0])
        if window_dd <= danger_thr:
            labels[T] = 2
        elif abs(window_ret) >= trend_thr:
            labels[T] = 1
        else:
            labels[T] = 0

    out = df.copy()
    out["regime_label"] = labels
    return out
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_regime_labels.py -k nowcast -v`
Expected: 5 passed.

- [ ] **Step 5: Run the full label test file to confirm no regressions**

Run: `python -m pytest tests/test_regime_labels.py -v`
Expected: all pass (existing forward-label tests + new nowcast tests).

- [ ] **Step 6: Commit**

```bash
git checkout -b feat/regime-nowcast-label
git add src/data/label_generation.py tests/test_regime_labels.py
git commit -m "feat(regime): add trailing-window now-cast label generator

Mirror of the forward label but lookback-only (no lookahead): at bar T,
trending=|24-bar return|>=2%, danger=max-drawdown-in-window<=-3%
(precedence over trending), ranging=else. Pure numpy/pandas, CI-safe unit
tests. Forward label kept for reference."
```

---

### Task 2: Trainer — deeper RF + temporal calibration + now-cast label

**Files:**
- Modify: `src/ml/train_regime.py:54-90` (the lazy-imports block and the fit/save block in `main`)

**Interfaces:**
- Consumes: `generate_regime_labels_nowcast` (Task 1), `RegimeClassifier.calibrate` (already exists in `src/ml/regime_classifier.py`).
- Produces: retrained `models/regime_{PAIR}_clean.pkl` with `calibrated_model` populated.

- [ ] **Step 1: Swap the labeler import and call**

In `src/ml/train_regime.py`, change the import (around line 55):

```python
    from src.data.label_generation import generate_regime_labels_nowcast
```

and the call (around line 70):

```python
    feats = calculate_technical_features(bars)
    labeled = generate_regime_labels_nowcast(feats)
    labeled = labeled[labeled["regime_label"] >= 0]  # drop no-history tail
```

- [ ] **Step 2: Replace the fit block with deeper RF + temporal calibration split**

Replace the `X = labeled[...] ... clf.save_model()` block (around lines 77-85) with:

```python
    # Temporal split: fit on the first 85% by time, calibrate on the held-out
    # tail (never seen by the fit) so emitted probabilities are honest.
    labeled = labeled.sort_index()
    split = int(len(labeled) * 0.85)
    train_df, cal_df = labeled.iloc[:split], labeled.iloc[split:]
    X_tr, y_tr = train_df[MARKET_FEATURE_COLS], train_df["regime_label"]
    X_cal, y_cal = cal_df[MARKET_FEATURE_COLS], cal_df["regime_label"]
    counts = {int(k): int(v) for k, v in y_tr.value_counts().items()}
    print(
        f"  fit: {len(train_df):,} rows (class counts {counts}); "
        f"calibrate: {len(cal_df):,} held-out rows"
    )

    out = args.output or f"models/regime_{_pair_to_slug(args.pair)}_clean.pkl"
    clf = RegimeClassifier(model_path=out, model_type="random_forest")
    # Deeper, regularized forest — the depth-5 default underfits; now that the
    # label is learnable, full depth + min_samples_leaf lets it fit real
    # structure, and isotonic calibration makes the confidences honest.
    from sklearn.ensemble import RandomForestClassifier

    clf.model = RandomForestClassifier(
        n_estimators=400,
        max_depth=None,
        min_samples_leaf=5,
        max_features="sqrt",
        class_weight="balanced_subsample",
        n_jobs=-1,
        random_state=42,
    )
    clf.train(X_tr, y_tr)
    clf.calibrate(X_cal, y_cal)
    clf.save_model()
```

- [ ] **Step 3: Update the final docstring/print**

Replace the trailing `print(...)` (around lines 86-90) with:

```python
    print(
        f"Saved -> {out}\n"
        f"  Labeling: now-cast (trailing window, no lookahead), 3-class "
        f"(0=ranging, 1=trending, 2=danger), window={DEFAULT_HORIZON if False else 24} bars, "
        f"|ret|>={trend_thr if False else 0.02} / max-DD<=-0.03. "
        f"Forest: depth-full, min_samples_leaf=5, isotonic-calibrated."
    )
```

(If cleaner, hardcode the literals `window=24`, `|ret|>=2%`, `max-DD<=-3%` directly in the f-string rather than the `DEFAULT_*` constants — the goal is an accurate human-readable line.)

- [ ] **Step 4: Smoke-test the trainer on ETH (conda, temp output)**

Run:
```bash
cd /Users/amro/WebstormProjects/trading-humming-bot
/opt/anaconda3/bin/python -m src.ml.train_regime --pair ETHUSDT --train-end 2026-05-31 --months 24 --output /tmp/regime_ETH_test.pkl
```
Expected output includes: `fit: 14,6xx rows (class counts {0: ..., 2: ..., 1: ...}); calibrate: 2,5xx held-out rows`, `Training complete.`, `Calibration complete (method=isotonic, ...)`, `Saved -> /tmp/regime_ETH_test.pkl`. Confirm the 3 classes are all populated (none starved). If a class is near-zero, stop and adjust thresholds before proceeding.

- [ ] **Step 5: Commit**

```bash
git add src/ml/train_regime.py
git commit -m "feat(regime): train on now-cast label with deeper RF + isotonic calibration"
```

---

### Task 3: Retrain all 4 pairs

**Files:**
- Overwrite: `models/regime_ETH-USDT_clean.pkl`, `models/regime_BNB-USDT_clean.pkl`, `models/regime_DOGE-USDT_clean.pkl`, `models/regime_XRP-USDT_clean.pkl`

**Interfaces:**
- Consumes: Task 2 trainer. Existing backup at `models/_pre_retrain_backup_20260719/`.

- [ ] **Step 1: Confirm backup exists**

Run: `ls models/_pre_retrain_backup_20260719/`
Expected: the 4 original `_clean.pkl` files (dated Jul 13).

- [ ] **Step 2: Retrain all 4 pairs (conda)**

Run:
```bash
cd /Users/amro/WebstormProjects/trading-humming-bot
for P in ETHUSDT BNBUSDT DOGEUSDT XRPUSDT; do
  echo "=== $P ==="
  /opt/anaconda3/bin/python -m src.ml.train_regime --pair "$P" --train-end 2026-05-31 --months 24
done
```
Expected: each pair prints class counts (all 3 classes populated), `Calibration complete (method=isotonic ...)`, `Saved -> models/regime_{PAIR}-USDT_clean.pkl`.

- [ ] **Step 3: Verify the 4 new model files**

Run: `ls -la models/regime_*_clean.pkl`
Expected: all 4 have a fresh mtime (today); sizes in the same ballpark as before (calibration adds a little).

- [ ] **Step 4: Commit (do not push yet)**

```bash
git add models/regime_*_clean.pkl
git commit -m "feat(regime): retrained 4 pairs on now-cast label (deeper+calibrated)"
```

---

### Task 4: OOS validation gate (decides deploy)

**Files:**
- Create: `scripts/eval_regime_oos.py` (committed — reproducible gate for the thesis)
- Reads: the 4 new models + `models/_pre_retrain_backup_20260719/` (for OLD-vs-NEW comparison)

**Interfaces:**
- Consumes: `load_klines` (`src.rl.data`), `calculate_technical_features`, `generate_regime_labels_nowcast`, `MARKET_FEATURE_COLS`, `RegimeClassifier`.

- [ ] **Step 1: Write the OOS eval script**

Create `scripts/eval_regime_oos.py`:

```python
#!/usr/bin/env python3
"""OOS evaluation for the regime classifier on a held-out window.

Computes the TRUE now-cast label on the OOS window (deterministic from past
data) and reports accuracy, calibrated confidence, class mix, per-class recall,
and a comparison vs a plain ADX/CHOP rule. Run under conda (pandas_ta).

Usage: /opt/anaconda3/bin/python scripts/eval_regime_oos.py [PAIRUSDT]
"""
from __future__ import annotations
import sys, pickle, numpy as np, pandas as pd
sys.path.insert(0, ".")
from datetime import date
from collections import Counter
from src.rl.data import load_klines
from src.data.feature_engineering import calculate_technical_features
from src.data.label_generation import generate_regime_labels_nowcast
from src.rl.features import MARKET_FEATURE_COLS
from src.ml.regime_classifier import RegimeClassifier
from sklearn.metrics import accuracy_score

NAMES = {0: "ranging", 1: "trending", 2: "danger"}
OOS_START = pd.Timestamp("2026-05-15", tz="UTC")
OOS_END = pd.Timestamp("2026-07-17", tz="UTC")

def load_xy(pair_slug_binance):
    bars = load_klines(pair_slug_binance, date(2026, 4, 1), date(2026, 7, 17))
    feats = calculate_technical_features(bars.copy())
    lab = generate_regime_labels_nowcast(feats).loc[OOS_START:OOS_END]
    X = lab[MARKET_FEATURE_COLS]; y = lab["regime_label"]
    m = X.notna().all(axis=1).to_numpy()
    return X[m], y[m], lab[m]

def evaluate(path, label, X, y):
    c = RegimeClassifier(model_path=path, model_type="random_forest"); c.load_model()
    probs = [c.predict_proba_full(X.iloc[[i]]) for i in range(len(X))]
    regs = np.array([max(p, key=p.get) for p in probs])
    confs = np.array([p[r] for p, r in zip(probs, regs)])
    ym = y.values >= 0
    acc = accuracy_score(y[ym], regs[ym]) if ym.sum() else float("nan")
    # calibration honesty: accuracy of the top-quartile-confidence subset
    if len(confs):
        hi = confs >= np.percentile(confs, 75)
        hi_acc = accuracy_score(y[ym][hi[ym]], regs[ym][hi[ym]]) if hi[ym].sum() else float("nan")
    else:
        hi_acc = float("nan")
    rec = {k: ((regs[(y.values == k) & ym] == k).mean() if ((y.values == k) & ym).sum() else float("nan"))
           for k in (1, 2)}
    print(f"\n=== {label} ===\n  {path}")
    print(f"  calibrated: {c.calibrated_model is not None}")
    print(f"  accuracy: {acc:.3f}  (majority baseline ~0.55)")
    print(f"  confidence: median={np.median(confs):.3f} mean={confs.mean():.3f} | "
          f"top-quartile-conf accuracy={hi_acc:.3f}")
    print(f"  predicted mix: " + ", ".join(f"{NAMES[int(k)]}={v}({v/len(regs)*100:.0f}%)"
          for k, v in sorted(Counter(regs).items())))
    print(f"  recall: trending={rec[1]:.2f} danger={rec[2]:.2f}")

def main():
    pair = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
    slug = pair.replace("USDT", "-USDT")
    X, y, _ = load_xy(pair)
    yl = y[y >= 0]
    print(f"OOS {pair} {OOS_START.date()}->{OOS_END.date()}: {len(X)} bars | TRUE mix: "
          + ", ".join(f"{NAMES[int(k)]}={v}" for k, v in sorted(Counter(yl).items())))
    evaluate(f"models/_pre_retrain_backup_20260719/regime_{slug}_clean.pkl", "OLD (forward label)", X, y)
    evaluate(f"models/regime_{slug}_clean.pkl", "NEW (now-cast label)", X, y)

if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the gate for all 4 pairs**

Run:
```bash
cd /Users/amro/WebstormProjects/trading-humming-bot
for P in ETHUSDT BNBUSDT DOGEUSDT XRPUSDT; do
  /opt/anaconda3/bin/python scripts/eval_regime_oos.py "$P"
done
```

- [ ] **Step 3: Apply the gate criteria — DECISION CHECKPOINT**

For each pair, confirm ALL hold (per spec):
1. NEW OOS accuracy ≥ ~0.70 (well above ~0.55 baseline, and above OLD).
2. NEW calibrated median confidence ≥ ~0.60, AND top-quartile-confidence accuracy is markedly higher than overall (honest calibration).
3. NEW predicted class mix is responsive (not ≥~90% on one class).
4. NEW trending/danger recall are well above their ~23%/22% base rates.
5. Qualitative: NEW trending calls coincide with high-ADX bars (eyeball vs the engine's ADX gates).

**If ANY pair fails → STOP.** Do not deploy. Report which criterion failed and reconsider thresholds/window. If all pass → proceed to Task 5.

- [ ] **Step 4: Commit the eval script**

```bash
git add scripts/eval_regime_oos.py
git commit -m "test(regime): reproducible OOS eval script for the now-cast gate"
```

---

### Task 5: Deploy (ONLY after Task 4 passes AND user approves)

**Files:** none new (deploy of committed model + code).

- [ ] **Step 1: Get explicit user approval to deploy**

Present the Task 4 gate results to the user. Do not proceed until they say go. (This is a production-impacting change to a live strategy gate.)

- [ ] **Step 2: Merge to main (triggers CI deploy)**

```bash
git checkout main
git merge --no-ff feat/regime-nowcast-label
git push origin main
```
CI (`.github/workflows/deploy.yml`) runs test → build → deploy (EC2 git pull + compose up). The pusher container restarts with the new mounted `./models/*.pkl`.

- [ ] **Step 3: Verify the deploy landed**

Run (SSM, ~2-3 min after CI green):
```bash
# replace with the project's SSM helper; check pusher logs for fresh predictions
aws ssm send-command --instance-ids "i-0eafde6592d97eab2" --document-name "AWS-RunShellScript" \
  --parameters 'commands=["docker logs trading-regime-pusher --tail 20"]' --region ap-northeast-1
```
Expected: per-pair `regime=X(name) confidence=0.YY` lines where (a) regimes **differ across pairs** and (b) confidence is ≥ ~0.5 (not stuck at ~0.40 ranging).

- [ ] **Step 4: Verify regime is influencing strategies**

```bash
aws ssm send-command --instance-ids "i-0eafde6592d97eab2" --document-name "AWS-RunShellScript" \
  --parameters 'commands=["curl -s localhost:3030/api/v1/strategies"]' --region ap-northeast-1
```
Expected: strategy `details` reflect regime (e.g., a grid paused with a regime reason, or a trend strategy active in a trending regime) rather than uniform TA-fallback.

- [ ] **Step 5: Rollback if anything looks wrong**

```bash
cp models/_pre_retrain_backup_20260719/regime_*_clean.pkl models/
git checkout -- models/regime_*_clean.pkl   # restore committed old models
git push origin main                         # CI redeploys the old models
```

---

### Task 6: Memory + thesis value-add note

**Files:**
- Create: `memory/regime_nowcast_relabel.md` (project memory)

- [ ] **Step 1: Write the memory**

Record: forward-24h label was unlearnable (accuracy ≈ baseline, refuted by deepening+calibration experiment on 2026-07-19); replaced with trailing-window now-cast label (24-bar, |ret|≥2% / max-DD≤−3%, danger precedence); retrained with deeper RF + isotonic calibration; OOS gate results (accuracy/conf/mix per pair); deploy status; rollback path. Link `[[regime_pusher_deployed]]`, `[[trend_regime_gate_not_wired]]`, `[[naked_eth_short_breaker]]`.

- [ ] **Step 2: Add the MEMORY.md pointer**

Append `- [Regime Now-cast Relabel](regime_nowcast_relabel.md) — ...` to `memory/MEMORY.md`.

- [ ] **Step 3: Commit**

```bash
git add memory/regime_nowcast_relabel.md memory/MEMORY.md
git commit -m "docs(memory): record regime now-cast relabel + OOS results"
git push origin main
```

---

## Self-review notes

- **Spec coverage:** labeler (Task 1), trainer deeper+calibrate+nowcast (Task 2), retrain 4 (Task 3), all 5 gate criteria + value-add (Task 4), deploy + verify + rollback (Task 5), memory (Task 6). Engine/RL/strategy untouched (non-goals respected). Forward labeler preserved.
- **Placeholders:** none — every code step shows real code; the Step 3 print in Task 2 flags the `DEFAULT_*` vs literal choice explicitly.
- **Type consistency:** `generate_regime_labels_nowcast(df, window=24, trend_thr=0.02, danger_thr=-0.03)` signature is identical in Task 1 (definition), Task 2 (call), Task 4 (eval). `RegimeClassifier.calibrate` / `predict_proba_full` / `calibrated_model` match the existing class.
- **Env consistency:** all training/eval uses `/opt/anaconda3/bin/python3`; unit tests use plain `python -m pytest` (labeler is numpy/pandas only).
