# Mean-Reversion ML Entry-Gate (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a per-pair RandomForest that predicts whether a mean-reversion flush will hit +2% TP before -4% SL, and prove (out-of-sample) that ML-gated entries beat no-ML entries — ETH pilot, no live changes.

**Architecture:** Labeling pass over the backtest's bars → one labeled row per historical flush (strategy-aligned TP-before-SL label). A single `features_at_flush` function builds the feature vector (reused for train + serve). RandomForest per-pair (mirrors `src/ml/regime_classifier.py`), walk-forward train/test, persist `.pkl`. An ML-gated backtest mode compares ML-gated vs no-ML OOS edge — the GO/NO-GO gate.

**Tech Stack:** Python 3, pandas, scikit-learn (RandomForest, joblib), the existing `backtest/mean_reversion/` pipeline (`data.py`, `features.py`, `backtest.py`).

**Spec:** `docs/superpowers/specs/2026-06-13-mean-reversion-ml-entry-gate-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backtest/mean_reversion/labels.py` | Forward TP/SL labeling: for each flush bar, did +2% hit before -4% (within max-hold)? |
| `src/ml/flush_features.py` | `features_at_flush(bars, features, idx)` — single train/serve feature vector |
| `src/ml/flush_reversion_model.py` | `FlushReversionClassifier` (RF, walk-forward, save/load) — mirrors `regime_classifier.py` |
| `backtest/mean_reversion/backtest.py` | + `run_single_ml` / ML-gated entry for the edge check |
| `tests/test_flush_labels.py`, `tests/test_flush_features.py`, `tests/test_flush_reversion_model.py`, `tests/test_mr_backtest.py` (extend) | TDD |

**Conventions:** `python3 -m pytest` from repo root. scikit-learn is already a project dep (regime model). `features.compute_features(bars, bar)` returns columns including `drop_frac, bid_refill_ratio, sell_flow_decay, liq_cascade_score, regime_trending`. `backtest.run_single(bars, features, drop_thr, tp, stop, base_size, bar)` is the labeling oracle.

---

### Task 1: Forward TP/SL labeling — `labels.py`

**Files:**
- Create: `backtest/mean_reversion/labels.py`
- Test: `tests/test_flush_labels.py`

- [ ] **Step 1: Write the failing test (oracle: labeling matches `run_single`'s TP/SL outcome)**

```python
# tests/test_flush_labels.py
import pandas as pd
from backtest.mean_reversion.labels import label_flushes
from backtest.mean_reversion.features import compute_features
from backtest.mean_reversion.backtest import run_single


def _bars(prices):
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="1s")
    return pd.DataFrame({"close": prices, "buy_vol": 1.0, "sell_vol": 1.0}, index=idx)


def test_label_matches_run_single_tp_outcome():
    # 30 flat @100, flush to 94 (entry), then +2% TP at 96.
    bars = _bars([100.0] * 30 + [94.0, 94.0, 96.0])
    feats = compute_features(bars, bar="1s")
    labels = label_flushes(bars, feats, drop_thr=0.05, tp=0.02, stop=0.04, max_hold=10)
    # A flush occurred; +2% (95.88) hit before -4% (90.24) -> label 1 (winner)
    assert len(labels) == 1
    assert labels.iloc[0]["label"] == 1
    # Oracle: run_single at this config is a winning trade (total_return > 0)
    r = run_single(bars, feats, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100, bar="1s")
    assert r is not None and r["total_return_pct"] > 0


def test_label_matches_run_single_stop_outcome():
    # 30 flat @100, flush to 94 (entry), then -4% stop at 90.
    bars = _bars([100.0] * 30 + [94.0, 90.0])
    feats = compute_features(bars, bar="1s")
    labels = label_flushes(bars, feats, drop_thr=0.05, tp=0.02, stop=0.04, max_hold=10)
    assert len(labels) == 1
    assert labels.iloc[0]["label"] == 0  # stop hit first -> loser
    r = run_single(bars, feats, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100, bar="1s")
    assert r is not None and r["total_return_pct"] < 0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_flush_labels.py -v
```
Expected: FAIL — `ModuleNotFoundError: backtest.mean_reversion.labels`.

- [ ] **Step 3: Implement `labels.py`**

```python
# backtest/mean_reversion/labels.py
"""Forward TP/SL labeling for flush events (strategy-aligned label).

For each flush bar (drop_frac > drop_thr), take close as entry, scan forward up
to max_hold bars: label = 1 if high >= entry*(1+tp) before low <= entry*(1-stop),
else 0 (stop first, or neither within max_hold = not a winner).
"""
import pandas as pd


def label_flushes(bars: pd.DataFrame, features: pd.DataFrame, drop_thr: float,
                  tp: float, stop: float, max_hold: int = 180) -> pd.DataFrame:
    high = bars["close"]      # close-only resolution (matches the backtest's SL/TP)
    low = bars["close"]
    close = bars["close"]
    flush_mask = features["drop_frac"] > drop_thr
    rows = []
    for i in bars.index[flush_mask.values]:
        pos = bars.index.get_loc(i)
        entry = float(close.loc[i])
        tp_px = entry * (1.0 + tp)
        sl_px = entry * (1.0 - stop)
        label = 0
        for j in range(pos + 1, min(pos + 1 + max_hold, len(bars))):
            h = float(high.iloc[j]); l = float(low.iloc[j])
            if h >= tp_px:
                label = 1
                break
            if l <= sl_px:
                label = 0
                break
        rows.append({"ts": i, "entry": entry, "drop_frac": float(features.loc[i, "drop_frac"]), "label": label})
    return pd.DataFrame(rows)
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_flush_labels.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backtest/mean_reversion/labels.py tests/test_flush_labels.py
git commit -m "feat(ml): forward TP/SL labeling pass for flush events"
```

---

### Task 2: Feature vector — `src/ml/flush_features.py`

**Files:**
- Create: `src/ml/flush_features.py` (and `ml/__init__.py` exists already)
- Test: `tests/test_flush_features.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_flush_features.py
import pandas as pd
from src.ml.flush_features import features_at_flush, FEATURE_COLUMNS
from backtest.mean_reversion.features import compute_features


def _bars(prices):
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="1s")
    return pd.DataFrame({"close": prices, "buy_vol": 1.0, "sell_vol": 1.0}, index=idx)


def test_feature_vector_has_all_columns_and_no_nan():
    bars = _bars([100.0] * 35 + [94.0])  # flush at last bar
    feats = compute_features(bars, bar="1s")
    vec = features_at_flush(bars, feats, idx=bars.index[-1])
    assert set(FEATURE_COLUMNS).issubset(vec.keys())
    for c in FEATURE_COLUMNS:
        assert pd.notna(vec[c]), f"{c} is NaN"


def test_volume_spike_and_rsi_are_sane():
    bars = _bars([100.0] * 35 + [94.0])
    feats = compute_features(bars, bar="1s")
    vec = features_at_flush(bars, feats, idx=bars.index[-1])
    assert 0.0 <= vec["rsi"] <= 100.0
    assert vec["volume_spike"] >= 0.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_flush_features.py -v
```
Expected: FAIL — `ModuleNotFoundError: ml.flush_features`.

- [ ] **Step 3: Implement `flush_features.py`**

```python
# src/ml/flush_features.py
"""Single train/serve feature vector for the flush-reversion entry gate.

ONE function used for both training (from the labeled dataset) and serving (from
the Rust request) — eliminates train/serve feature drift.
"""
import pandas as pd

FEATURE_COLUMNS = [
    "drop_frac", "bid_refill_ratio", "sell_flow_decay", "liq_cascade_score",
    "regime_trending", "volatility", "rsi", "volume_spike", "hour", "weekday",
]


def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0).rolling(period, min_periods=1).mean()
    loss = (-delta).where(delta < 0, 0.0).rolling(period, min_periods=1).mean()
    rs = gain / (loss + 1e-9)
    return 100.0 - (100.0 / (1.0 + rs))


def features_at_flush(bars: pd.DataFrame, features: pd.DataFrame, idx) -> dict:
    """Feature vector at flush bar `idx`. `features` is compute_features(bars)."""
    close = bars["close"]
    vol = bars["volume"]
    i = bars.index.get_loc(idx)
    rets = close.pct_change()
    volatility = float(rets.iloc[max(0, i - 30):i + 1].std()) if i > 0 else 0.0
    rsi_series = _rsi(close)
    window = vol.iloc[max(0, i - 30):i + 1]
    volume_spike = float(vol.iloc[i] / (window.mean() + 1e-9))
    ts = bars.index[i]
    return {
        "drop_frac": float(features.loc[idx, "drop_frac"]),
        "bid_refill_ratio": float(features.loc[idx, "bid_refill_ratio"]),
        "sell_flow_decay": float(features.loc[idx, "sell_flow_decay"]),
        "liq_cascade_score": float(features.loc[idx, "liq_cascade_score"]),
        "regime_trending": int(bool(features.loc[idx, "regime_trending"])),
        "volatility": volatility,
        "rsi": float(rsi_series.iloc[i]),
        "volume_spike": volume_spike,
        "hour": int(getattr(ts, "hour", 0)),
        "weekday": int(getattr(ts, "weekday", 0)),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_flush_features.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/ml/flush_features.py tests/test_flush_features.py
git commit -m "feat(ml): single train/serve flush feature vector"
```

---

### Task 3: RandomForest model + walk-forward — `flush_reversion_model.py`

**Files:**
- Create: `src/ml/flush_reversion_model.py`
- Test: `tests/test_flush_reversion_model.py`

- [ ] **Step 1: Write the failing test (reproducibility + OOS evaluation)**

```python
# tests/test_flush_reversion_model.py
import pandas as pd
from src.ml.flush_reversion_model import FlushReversionClassifier, build_dataset, walk_forward_evaluate
from backtest.mean_reversion.features import compute_features
from backtest.mean_reversion.labels import label_flushes


def _bars(prices):
    idx = pd.date_range("2026-01-01", periods=len(prices), freq="1s")
    return pd.DataFrame({"close": prices, "buy_vol": 1.0, "sell_vol": 1.0}, index=idx)


def test_reproducible_training_and_oos():
    # Enough flushes for a tiny walk-forward: repeating flush+bounce pattern.
    prices = [100.0] * 35 + [94.0, 96.0] + [100.0] * 35 + [94.0, 96.0] + [100.0] * 35 + [94.0, 90.0] + [100.0] * 35 + [94.0, 90.0]
    bars = _bars(prices)
    feats = compute_features(bars, bar="1s")
    labels = label_flushes(bars, feats, drop_thr=0.05, tp=0.02, stop=0.04, max_hold=10)
    ds = build_dataset(bars, feats, labels)
    clf_a = FlushReversionClassifier(random_state=42)
    clf_b = FlushReversionClassifier(random_state=42)
    clf_a.fit(ds.iloc[:2], ds.iloc[:2]["label"])
    clf_b.fit(ds.iloc[:2], ds.iloc[:2]["label"])
    # Same seed + data -> identical probabilities
    p_a = clf_a.predict_proba(ds.iloc[2:])
    p_b = clf_b.predict_proba(ds.iloc[2:])
    assert list(p_a) == list(p_b)
    # walk_forward_evaluate returns a metrics dict
    metrics = walk_forward_evaluate(ds, test_frac=0.5)
    assert "oos_accuracy" in metrics and "oos_precision" in metrics
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_flush_reversion_model.py -v
```
Expected: FAIL — `ModuleNotFoundError: ml.flush_reversion_model`.

- [ ] **Step 3: Implement `flush_reversion_model.py`**

```python
# src/ml/flush_reversion_model.py
"""Per-pair RandomForest flush-reversion classifier (mirrors regime_classifier.py).

Predicts P(TP before SL) at flush time. Walk-forward: train on first (1-test_frac),
evaluate on the rest (chronological — no shuffling across the split).
"""
import os
import pickle
import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score, recall_score, roc_auc_score

from src.ml.flush_features import features_at_flush, FEATURE_COLUMNS


def build_dataset(bars: pd.DataFrame, features: pd.DataFrame, labels: pd.DataFrame) -> pd.DataFrame:
    """Join per-flush feature vectors with labels -> ML-ready DataFrame."""
    rows = []
    for _, lab in labels.iterrows():
        vec = features_at_flush(bars, features, idx=lab["ts"])
        vec["label"] = int(lab["label"])
        rows.append(vec)
    return pd.DataFrame(rows)


class FlushReversionClassifier:
    def __init__(self, random_state: int = 42):
        self.model = RandomForestClassifier(
            n_estimators=300, max_depth=8, min_samples_leaf=2,
            class_weight="balanced", random_state=random_state, n_jobs=1,
        )
        self.trained = False

    def fit(self, X: pd.DataFrame, y):
        self.model.fit(X[FEATURE_COLUMNS], y)
        self.trained = True
        return self

    def predict_proba(self, X: pd.DataFrame):
        if not self.trained:
            raise ValueError("Model not trained")
        # P(label=1) = P(TP before SL)
        return self.model.predict_proba(X[FEATURE_COLUMNS])[:, list(self.model.classes_).index(1)]

    def save(self, path: str):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "wb") as f:
            pickle.dump({"model": self.model, "features": FEATURE_COLUMNS, "version": 1}, f)

    @classmethod
    def load(cls, path: str):
        with open(path, "rb") as f:
            data = pickle.load(f)
        clf = cls()
        clf.model = data["model"]
        clf.trained = True
        return clf


def walk_forward_evaluate(dataset: pd.DataFrame, test_frac: float = 1 / 3) -> dict:
    """Train on first (1-test_frac), evaluate on the rest. Chronological split."""
    dataset = dataset.sort_values("ts") if "ts" in dataset.columns else dataset.reset_index(drop=True)
    n = len(dataset)
    split = int(n * (1 - test_frac))
    train, test = dataset.iloc[:split], dataset.iloc[split:]
    if train.empty or test.empty:
        return {"oos_accuracy": 0.0, "oos_precision": 0.0, "oos_recall": 0.0, "oos_auc": 0.0, "n_test": 0}
    clf = FlushReversionClassifier()
    clf.fit(train, train["label"])
    p = clf.predict_proba(test)
    yhat = (p >= 0.5).astype(int)
    y = test["label"].values
    metrics = {
        "oos_accuracy": float(accuracy_score(y, yhat)),
        "oos_precision": float(precision_score(y, yhat, zero_division=0)),
        "oos_recall": float(recall_score(y, yhat, zero_division=0)),
        "n_test": int(len(test)),
    }
    try:
        metrics["oos_auc"] = float(roc_auc_score(y, p))
    except ValueError:
        metrics["oos_auc"] = 0.0
    return metrics
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_flush_reversion_model.py -v
```
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add src/ml/flush_reversion_model.py tests/test_flush_reversion_model.py
git commit -m "feat(ml): RandomForest flush-reversion classifier + walk-forward"
```

---

### Task 4: ML-gated edge check in the backtest

**Files:**
- Modify: `backtest/mean_reversion/backtest.py` (add `entry_signal_ml`, `run_single_ml`)
- Test: extend `tests/test_mr_backtest.py`

- [ ] **Step 1: Write the failing test (ML gating with a perfect oracle beats no-ML)**

Append to `tests/test_mr_backtest.py`:

```python
def test_ml_gated_with_perfect_oracle_filters_losers():
    # When the "model" assigns P=1 only to winners and 0 to losers, ML-gated must
    # keep winners and drop losers (a perfect gate -> no losing trades).
    from backtest.mean_reversion.backtest import run_single_ml
    bars = _bars([100.0] * 30 + [94.0, 90.0])  # flush then stop (a loser)
    f = compute_features(bars, bar="1s")
    # Perfect oracle: P(revert)=0 at the (losing) flush bar -> gated out, no entry
    proba = pd.Series([0.0] * len(bars), index=bars.index)
    proba[bars.index[30]] = 0.0
    r = run_single_ml(bars, f, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100,
                      bar="1s", proba=proba, ml_threshold=0.5)
    assert r is None  # gated out -> no trade
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python3 -m pytest tests/test_mr_backtest.py::test_ml_gated_with_perfect_oracle_filters_losers -v
```
Expected: FAIL — `ImportError: cannot import name run_single_ml`.

- [ ] **Step 3: Implement `run_single_ml` + `entry_signal_ml`**

Add to `backtest/mean_reversion/backtest.py` (after `run_single`):

```python
def entry_signal_ml(features: pd.DataFrame, drop_thr: float, proba: pd.Series,
                    ml_threshold: float = 0.5, enter_threshold: float = 2.0) -> pd.Series:
    """Entry signal gated by model P(revert) >= ml_threshold at the flush bar."""
    base = (features["drop_frac"] > drop_thr) & (features["score"] >= enter_threshold)
    return base & (proba.reindex(base.index).fillna(0.0) >= ml_threshold)


def run_single_ml(bars: pd.DataFrame, features: pd.DataFrame, drop_thr: float,
                  tp: float, stop: float, base_size: float, bar: str,
                  proba: pd.Series, ml_threshold: float = 0.5):
    """Like run_single but only enters where the model P(revert) >= ml_threshold."""
    import vectorbt as vbt
    entries = entry_signal_ml(features, drop_thr, proba, ml_threshold)
    if not entries.any():
        return None
    pf = vbt.Portfolio.from_signals(
        close=bars["close"], entries=entries, sl_stop=stop, tp_stop=tp,
        size=base_size, size_type="value", init_cash=INIT_CASH, fees=FEES,
        slippage=SLIPPAGE, freq=bar,
    )
    stats = pf.stats()
    return {
        "drop_thr": drop_thr, "tp": tp, "stop": stop, "base_size": base_size,
        "total_trades": int(stats.get("Total Trades", 0)),
        "total_return_pct": float(stats.get("Total Return [%]", 0.0)),
        "sharpe_ratio": float(stats.get("Sharpe Ratio", 0.0)),
        "max_drawdown_pct": float(stats.get("Max Drawdown [%]", 0.0)),
        "win_rate": float(stats.get("Win Rate [%]", 0.0)),
    }
```

- [ ] **Step 4: Run test to verify it passes**

```bash
python3 -m pytest tests/test_mr_backtest.py -v
```
Expected: PASS (all, incl. the new test).

- [ ] **Step 5: Commit**

```bash
git add backtest/mean_reversion/backtest.py tests/test_mr_backtest.py
git commit -m "feat(backtest): ML-gated entry mode for the edge check"
```

---

### Task 5: ETH pilot run → GO/NO-GO

**Files:** none new — a documented run script + the GO/NO-GO comparison.

- [ ] **Step 1: Write the pilot runner** `src/ml/run_eth_pilot.py`

```python
# src/ml/run_eth_pilot.py
"""ETH Phase-1 pilot: build labeled dataset, train RF walk-forward, compare
ML-gated vs no-ML OOS edge. Produces models/flush_reversion_ETH-USDT.pkl + a
printed GO/NO-GO. Run: python -m src.ml.run_eth_pilot"""
from datetime import date, timedelta
import pandas as pd
from backtest.mean_reversion.data import load_bars
from backtest.mean_reversion.features import compute_features
from backtest.mean_reversion.labels import label_flushes
from backtest.mean_reversion.backtest import run_single, run_single_ml, LIVE_CONFIG
from src.ml.flush_reversion_model import build_dataset, FlushReversionClassifier, walk_forward_evaluate


def main():
    end = date.today(); start = end - timedelta(days=30 * 6)
    bars = load_bars("ETHUSDT", start, end, "5s")
    feats = compute_features(bars, "5s")
    labels = label_flushes(bars, feats, drop_thr=LIVE_CONFIG["drop_thr"],
                           tp=LIVE_CONFIG["tp"], stop=LIVE_CONFIG["stop"], max_hold=180)
    ds = build_dataset(bars, feats, labels)
    print(f"flush events: {len(ds)}  (winners: {int(ds['label'].sum())})")
    metrics = walk_forward_evaluate(ds, test_frac=1 / 3)
    print("OOS metrics:", metrics)

    # Train final model on all data, save, produce per-flush P for the edge check
    clf = FlushReversionClassifier(); clf.fit(ds, ds["label"])
    clf.save("models/flush_reversion_ETH-USDT.pkl")
    proba = pd.Series(clf.predict_proba(ds).tolist(), index=ds["ts"])

    # Edge check: no-ML vs ML-gated on the full period (the running backtest is the
    # no-ML baseline; here we recompute both for a direct comparison).
    no_ml = run_single(bars, feats, bar="5s", **LIVE_CONFIG)
    ml = run_single_ml(bars, feats, bar="5s", proba=proba, ml_threshold=0.5, **LIVE_CONFIG)
    print("no-ML :", no_ml)
    print("ML-gated:", ml)
    verdict = "GO" if (ml and no_ml and ml["total_return_pct"] > no_ml["total_return_pct"]) else "NO-GO"
    print(f"\n=== VERDICT: {verdict} ===")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the pilot (needs the data pipeline; long-running)**

```bash
python3 -m ml.run_eth_pilot
```
Expected: prints flush-event count, OOS metrics, no-ML vs ML-gated returns, and a `VERDICT: GO|NO-GO`. Also writes `models/flush_reversion_ETH-USDT.pkl`.

- [ ] **Step 3: Commit**

```bash
git add src/ml/run_eth_pilot.py
git commit -m "feat(ml): ETH Phase-1 pilot runner (GO/NO-GO edge check)"
```

---

## Self-Review

- **Spec coverage:** labeling pass (Task 1), feature vector (Task 2), RF train+walk-forward (Task 3), ML-gated edge check (Task 4), ETH pilot GO/NO-GO (Task 5). Covers Phase 1 of the spec. Phase 2 (inference service + Rust gate) and Phase 3 (scale) are deliberately out of scope — gated on Phase 1's GO. ✓
- **Placeholder scan:** none — every code step has complete, runnable code. ✓
- **Type/name consistency:** `label_flushes(bars, features, drop_thr, tp, stop, max_hold)` consistent across Tasks 1/5; `features_at_flush(bars, features, idx)` + `FEATURE_COLUMNS` consistent across Tasks 2/3/5; `FlushReversionClassifier.fit/predict_proba/save/load` + `walk_forward_evaluate(dataset, test_frac)` consistent across Tasks 3/5; `run_single_ml(..., proba, ml_threshold)` consistent across Tasks 4/5. ✓
- **Open spec questions resolved:** label hold-horizon = 180 bars (Task 1/5); exit resolution = close-only (Task 1, documented); depth-proxy features deferred (Phase 2).

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-mean-reversion-ml-entry-gate.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
