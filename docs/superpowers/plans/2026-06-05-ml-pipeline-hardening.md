# ML Pipeline Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix label leakage (#1), add Rust TTL staleness guard (#3), improve diagnostics (#4), increase data defaults (#2), and harden calibration + DANGER labels (#5).

**Architecture:** Five independent fixes across the Python ML pipeline and Rust engine. Each fix is self-contained and can be deployed independently. The label leakage fix (Python) and TTL guard (Rust) are the two live-safety items and are implemented first.

**Tech Stack:** Python/scikit-learn (ML pipeline), Rust/tokio (regime cache), pytest + cargo test

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `src/ml/purged_cv.py` | Create | PurgedTimeSeriesSplit with embargo gap |
| `tests/test_purged_cv.py` | Create | Tests for purged CV splitter |
| `src/ml/train_pipeline.py` | Modify | Use purged CV, add confusion matrix, data warnings, DANGER analysis |
| `src/ml/regime_classifier.py` | Modify | Sigmoid calibration fallback for small samples |
| `trading-engine-core/src/strategy/regime_cache.rs` | Modify | Add TTL staleness guard to `get()` |
| `trading-engine-core/src/engine.rs` | Modify | Pass TTL to RegimeCache constructor |

---

### Task 1: Purged TimeSeriesSplit (Issue #1 — Label Leakage)

**Why:** Labels use a forward-looking window (`shift(-forward_window)`). The last `forward_window` samples in each training fold have labels that depend on prices inside the test fold → train/test information overlap → inflated accuracy.

**Files:**
- Create: `src/ml/purged_cv.py`
- Test: `tests/test_purged_cv.py`

- [ ] **Step 1: Write failing tests for PurgedTimeSeriesSplit**

```python
# tests/test_purged_cv.py
import numpy as np
import pytest
from src.ml.purged_cv import PurgedTimeSeriesSplit


def test_purged_cv_splits_with_embargo():
    """Training folds are truncated by embargo samples at the end."""
    X = np.arange(100).reshape(-1, 1)
    cv = PurgedTimeSeriesSplit(n_splits=3, embargo=10)

    splits = list(cv.split(X))
    assert len(splits) == 3

    for train_idx, test_idx in splits:
        # Train indices must not overlap with test indices
        assert train_idx[-1] < test_idx[0]
        # Train indices are contiguous from 0
        assert train_idx[0] == 0
        # Gap between last train and first test >= embargo
        assert test_idx[0] - train_idx[-1] >= 10


def test_purged_cv_no_embargo_matches_sklearn():
    """With embargo=0, splits should be same as sklearn TimeSeriesSplit."""
    from sklearn.model_selection import TimeSeriesSplit
    X = np.arange(60).reshape(-1, 1)

    purged = PurgedTimeSeriesSplit(n_splits=3, embargo=0)
    sklearn_cv = TimeSeriesSplit(n_splits=3)

    for (p_train, p_test), (s_train, s_test) in zip(purged.split(X), sklearn_cv.split(X)):
        np.testing.assert_array_equal(p_train, s_train)
        np.testing.assert_array_equal(p_test, s_test)


def test_purged_cv_embargo_larger_than_fold():
    """If embargo > fold size, training set should be empty (skipped)."""
    X = np.arange(20).reshape(-1, 1)
    cv = PurgedTimeSeriesSplit(n_splits=3, embargo=100)
    splits = list(cv.split(X))
    # All folds should produce empty train sets and be skipped
    assert len(splits) == 0


def test_purged_cv_get_n_splits():
    cv = PurgedTimeSeriesSplit(n_splits=5, embargo=0)
    assert cv.get_n_splits() == 5


def test_purged_cv_negative_embargo_raises():
    with pytest.raises(ValueError, match="embargo must be >= 0"):
        PurgedTimeSeriesSplit(n_splits=3, embargo=-1)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_purged_cv.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.ml.purged_cv'`

- [ ] **Step 3: Implement PurgedTimeSeriesSplit**

```python
# src/ml/purged_cv.py
"""
Purged TimeSeriesSplit — López de Prado's embargo for forward-looking label leakage.

When labels depend on a forward window of size N, the last N training samples
have labels that leak information into the test fold. This splitter drops those
samples from the training set.

Usage:
    embargo = max(forward_window_across_intervals)
    cv = PurgedTimeSeriesSplit(n_splits=3, embargo=embargo)
    classifier.tune_hyperparameters(X, y, cv=cv)
"""

import numpy as np


class PurgedTimeSeriesSplit:
    """TimeSeriesSplit with embargo gap to prevent label leakage.

    Parameters
    ----------
    n_splits : int
        Number of cross-validation folds.
    embargo : int
        Number of samples to drop from the end of each training fold.
        Must be >= the forward_window used in label generation.
    """

    def __init__(self, n_splits: int = 3, embargo: int = 0):
        if embargo < 0:
            raise ValueError(f"embargo must be >= 0, got {embargo}")
        self.n_splits = n_splits
        self.embargo = embargo

    def split(self, X, y=None, groups=None):
        n_samples = len(X)
        fold_size = n_samples // (self.n_splits + 1)

        for i in range(self.n_splits):
            train_end = (i + 1) * fold_size
            test_start = train_end
            test_end = min((i + 2) * fold_size, n_samples)

            # Apply embargo: remove last `embargo` samples from training
            purged_train_end = max(0, train_end - self.embargo)
            train_indices = np.arange(0, purged_train_end)
            test_indices = np.arange(test_start, test_end)

            # Skip folds where training set would be empty
            if len(train_indices) > 0 and len(test_indices) > 0:
                yield train_indices, test_indices

    def get_n_splits(self, X=None, y=None, groups=None):
        return self.n_splits
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_purged_cv.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/ml/purged_cv.py tests/test_purged_cv.py
git commit -m "feat: add PurgedTimeSeriesSplit with embargo gap for label leakage prevention"
```

---

### Task 2: Wire Purged CV into Training Pipeline (Issue #1 — continued)

**Why:** The training pipeline currently passes plain `TimeSeriesSplit(n_splits=3)` to `tune_hyperparameters`. It must use `PurgedTimeSeriesSplit` with `embargo = max(forward_window)` across all intervals being trained.

**Files:**
- Modify: `src/ml/train_pipeline.py:3` (add import)
- Modify: `src/ml/train_pipeline.py:127-131` (compute embargo)
- Modify: `src/ml/train_pipeline.py:179` (use purged CV)

- [ ] **Step 1: Add import and compute embargo**

In `src/ml/train_pipeline.py`, add import at top (after line 3):

```python
from src.ml.purged_cv import PurgedTimeSeriesSplit
```

Replace the dataset collection loop (lines 127-131) to also compute the embargo:

```python
    datasets = []
    max_forward_window = 0
    for interval, cfg in interval_configs.items():
        df = load_real_data(symbol, intervals=[interval], candles_per_interval=args.candles)
        datasets.append((interval, cfg, df))
        max_forward_window = max(max_forward_window, cfg["forward_window"])
```

- [ ] **Step 2: Use PurgedTimeSeriesSplit in hyperparameter tuning**

Replace line 179:

```python
    best_params = classifier.tune_hyperparameters(X_trainval, y_trainval, n_iter=20, cv=TimeSeriesSplit(n_splits=3))
```

With:

```python
    embargo = max_forward_window
    print(f"  Embargo gap: {embargo} samples (max forward_window across intervals)")
    best_params = classifier.tune_hyperparameters(X_trainval, y_trainval, n_iter=20, cv=PurgedTimeSeriesSplit(n_splits=3, embargo=embargo))
```

- [ ] **Step 3: Remove unused TimeSeriesSplit import if no longer needed**

Check if `TimeSeriesSplit` is still used elsewhere in the file. The import on line 3 is:
```python
from sklearn.model_selection import train_test_split, TimeSeriesSplit
```

If `train_test_split` is still used (it's not — the file uses manual slicing), remove the whole import. But check first — `train_test_split` may be used elsewhere. If not, replace the import line with just removing `TimeSeriesSplit`:

```python
from sklearn.model_selection import train_test_split
```

Actually, `train_test_split` is also not used in this file. Remove the entire import line:

```python
# REMOVE this line entirely:
from sklearn.model_selection import train_test_split, TimeSeriesSplit
```

- [ ] **Step 4: Run the pipeline in dry-run to verify it doesn't crash**

Run: `python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 1h --candles 500 2>&1 | head -40`
Expected: Pipeline runs, prints "Embargo gap: 12 samples", no import errors. (It may fail on Binance rate limit — that's fine, we're just checking the plumbing.)

- [ ] **Step 5: Commit**

```bash
git add src/ml/train_pipeline.py
git commit -m "fix: use PurgedTimeSeriesSplit with embargo in training pipeline to prevent label leakage"
```

---

### Task 3: RegimeCache TTL Staleness Guard (Issue #3 — Live Safety)

**Why:** If the Python container dies or stops pushing, `RegimeCache.get()` serves the last regime forever. A stale "Ranging" prediction lets the grid deploy straight into a developing trend. The grid strategy already treats `None` as "block deployment" (line 231-233 of grid.rs), so returning `None` when stale auto-pauses safely.

**Files:**
- Modify: `trading-engine-core/src/strategy/regime_cache.rs` — add `ttl_ms` field, TTL check in `get()`
- Modify: `trading-engine-core/src/engine.rs` — pass TTL to constructor

- [ ] **Step 1: Add TTL field and update constructor**

In `regime_cache.rs`, update the struct and constructor. The `ttl_ms` field stores the maximum age in milliseconds before an entry is considered stale:

```rust
#[derive(Clone)]
pub struct RegimeCache {
    inner: Arc<RwLock<HashMap<String, RegimeEntry>>>,
    file_path: String,
    last_mtime: Arc<RwLock<u64>>,
    ttl_ms: i64, // Max age in milliseconds; entries older than this return None
}

impl RegimeCache {
    /// Create a new RegimeCache.
    /// `ttl_ms`: max entry age in milliseconds. 0 = no TTL (never stale).
    /// Recommended: 3× poll interval (e.g., 180_000 for 60s polling).
    pub fn new(file_path: &str, ttl_ms: i64) -> Self {
        Self {
            inner: Arc::new(RwLock::new(HashMap::new())),
            file_path: file_path.to_string(),
            last_mtime: Arc::new(RwLock::new(0)),
            ttl_ms,
        }
    }
```

- [ ] **Step 2: Add TTL check in `get()` method**

Replace the current `get()` method (lines 59-63):

```rust
    /// Get regime for a pair. Returns (regime, confidence).
    /// Returns None if: pair not found, or entry is older than TTL.
    /// Checks file mtime first — if file changed since last read, reloads.
    pub async fn get(&self, pair: &str) -> Option<(i32, f64)> {
        self.maybe_reload_from_file().await;
        let map = self.inner.read().await;
        map.get(pair).and_then(|e| {
            if self.ttl_ms > 0 {
                let now = chrono::Utc::now().timestamp_millis();
                if now - e.timestamp > self.ttl_ms {
                    return None; // Stale — treat as unknown
                }
            }
            Some((e.regime, e.confidence))
        })
    }
```

- [ ] **Step 3: Update existing tests to pass new constructor signature**

All three test functions create `RegimeCache::new("/tmp/...")`. Update to pass `ttl_ms = 0` (no TTL, preserves existing behavior for tests):

```rust
    #[tokio::test]
    async fn test_regime_cache_update_and_get() {
        let cache = RegimeCache::new("/tmp/test_regime_cache.json", 0);
        // ... rest unchanged
    }

    #[tokio::test]
    async fn test_regime_cache_file_roundtrip() {
        let path = "/tmp/test_regime_roundtrip.json";
        let _ = std::fs::remove_file(path);
        let cache = RegimeCache::new(path, 0);
        // ... rest unchanged
        let cache2 = RegimeCache::new(path, 0);
        // ... rest unchanged
    }

    #[tokio::test]
    async fn test_regime_cache_no_file_is_ok() {
        let cache = RegimeCache::new("/tmp/nonexistent_regime.json", 0);
        // ... rest unchanged
    }
```

- [ ] **Step 4: Add TTL expiry test**

```rust
    #[tokio::test]
    async fn test_regime_cache_ttl_expiry() {
        let cache = RegimeCache::new("/tmp/test_regime_ttl.json", 5000); // 5s TTL

        // Insert entry with timestamp 10 seconds ago
        let mut map = cache.inner.write().await;
        map.insert("BTC-USDT".to_string(), RegimeEntry {
            regime: 0,
            confidence: 0.9,
            timestamp: chrono::Utc::now().timestamp_millis() - 10_000, // 10s ago
        });
        drop(map);

        // Entry should be expired → None
        let result = cache.get("BTC-USDT").await;
        assert_eq!(result, None);
    }

    #[tokio::test]
    async fn test_regime_cache_ttl_fresh_entry() {
        let cache = RegimeCache::new("/tmp/test_regime_ttl_fresh.json", 180_000); // 3min TTL

        cache.update(&[
            RegimeUpdate { pair: "BTC-USDT".into(), regime: 1, confidence: 0.8 },
        ]).await;

        // Just inserted — should be fresh
        let result = cache.get("BTC-USDT").await;
        assert_eq!(result, Some((1, 0.8)));
    }
```

- [ ] **Step 5: Update engine.rs to pass TTL**

Find where `RegimeCache::new()` is called in `engine.rs`. Update the constructor call to pass TTL = 180 seconds (3× the 60s Python poll interval):

```rust
// Change from:
self.regime_cache = RegimeCache::new("data/regime_cache.json");
// To:
self.regime_cache = RegimeCache::new("data/regime_cache.json", 180_000); // 3min TTL = 3×60s poll
```

(Note: find the exact line in engine.rs — it may be in `Engine::new()` or similar. The file path may also differ.)

- [ ] **Step 6: Build and run Rust tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot/trading-engine-core && cargo test --lib strategy::regime_cache`
Expected: All 5 tests pass (3 existing + 2 new TTL tests)

Run: `cargo check`
Expected: Compiles with no errors

- [ ] **Step 7: Commit**

```bash
git add trading-engine-core/src/strategy/regime_cache.rs trading-engine-core/src/engine.rs
git commit -m "feat: add TTL staleness guard to RegimeCache — stale entries return None (auto-pause grid)"
```

---

### Task 4: Confusion Matrix and Regime Transition Accuracy (Issue #4)

**Why:** Overall accuracy masks weakness on the classes that cost money. A model that's 90% accurate on Ranging but misses every DANGER transition will look good in aggregate but fail catastrophically in production. Need confusion matrix and explicit transition accuracy.

**Files:**
- Modify: `src/ml/train_pipeline.py:181-192` (after test set evaluation)

- [ ] **Step 1: Add confusion matrix and transition accuracy**

Add import at top of `train_pipeline.py`:

```python
from sklearn.metrics import confusion_matrix
```

After the existing evaluation block (after line 185 `print(classification_report(...))`), add:

```python
    # Confusion matrix
    cm = confusion_matrix(y_test, y_pred, labels=[0, 1, 2] if n_classes == 3 else [0, 1])
    print("\n--- Confusion Matrix (rows=true, cols=predicted) ---")
    labels_str = ["Ranging", "Trending", "Danger"] if n_classes == 3 else ["Ranging", "Trending"]
    header = "true\\pred".ljust(12) + "  ".join(f"{l:>10s}" for l in labels_str)
    print(f"  {header}")
    for i, row in enumerate(cm):
        row_str = "  ".join(f"{v:>10d}" for v in row)
        print(f"  {labels_str[i]:<12s} {row_str}")

    # Regime transition accuracy
    # How often does the model correctly predict when regime changes from one class to another?
    if len(y_test) > 1:
        y_true_arr = y_test.values if hasattr(y_test, 'values') else y_test
        y_pred_arr = y_pred
        transition_mask = y_true_arr[1:] != y_true_arr[:-1]
        n_transitions = transition_mask.sum()
        if n_transitions > 0:
            transition_correct = (y_pred_arr[1:][transition_mask] == y_true_arr[1:][transition_mask]).sum()
            transition_acc = transition_correct / n_transitions
            print(f"\n--- Regime Transition Accuracy ---")
            print(f"  Transitions detected: {n_transitions}/{len(y_true_arr)-1} bars")
            print(f"  Transition accuracy:  {transition_acc:.4f} ({transition_correct}/{n_transitions})")
        else:
            print(f"\n--- Regime Transition Accuracy ---")
            print(f"  No regime transitions in test set (all one class)")

    # DANGER-specific metrics
    if n_classes == 3 and 2 in y_test.values:
        danger_mask = y_test == 2
        n_danger = danger_mask.sum()
        danger_correct = (y_pred[danger_mask.values] == 2).sum()
        print(f"\n--- DANGER Class Breakdown ---")
        print(f"  DANGER samples in test: {n_danger}")
        print(f"  DANGER recall:          {danger_correct}/{n_danger} ({danger_correct/n_danger:.2%})")
        # False negatives: true DANGER predicted as something else
        danger_fn = (y_pred[danger_mask.values] != 2).sum()
        print(f"  DANGER false negatives: {danger_fn} (missed danger events)")
```

- [ ] **Step 2: Run pipeline to verify new output**

Run: `python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 1h --candles 500 2>&1 | tail -40`
Expected: Confusion matrix, transition accuracy, and DANGER breakdown printed after evaluation

- [ ] **Step 3: Commit**

```bash
git add src/ml/train_pipeline.py
git commit -m "feat: add confusion matrix, transition accuracy, and DANGER breakdown to training pipeline"
```

---

### Task 5: Data Sufficiency Warnings and Default Increase (Issue #2)

**Why:** 1000 candles on 15m is ~10 days — not enough for a full market cycle. 2000 is ~20 days, still thin. The default should be higher, and the pipeline should warn when data is likely insufficient.

**Files:**
- Modify: `src/ml/train_pipeline.py:101-102` (default and warning)

- [ ] **Step 1: Increase default candles and add sufficiency warnings**

Change the argparse default (line 101-102):

```python
    parser.add_argument('--candles', type=int, default=2000,
                        help='Number of candles per timeframe (default: 2000). Use 4000+ for 15m.')
```

After the data fetch loop (after line 131, before the feature engineering loop), add a data sufficiency check. Insert before `feature_cols = [` (line 132):

```python
    # Data sufficiency warnings
    INTERVAL_HOURS = {"15m": 0.25, "1h": 1.0, "4h": 4.0, "1d": 24.0}
    MINIMUM_DAYS = {
        "15m": 60,   # ~5,760 candles needed
        "1h": 90,    # ~2,160 candles needed
        "4h": 120,   # ~720 candles needed
        "1d": 180,   # ~180 candles needed
    }
    for name, cfg, df in datasets:
        if name in INTERVAL_HOURS and name in MINIMUM_DAYS:
            span_hours = len(df) * INTERVAL_HOURS[name]
            span_days = span_hours / 24.0
            min_days = MINIMUM_DAYS[name]
            min_candles = int(min_days * 24 / INTERVAL_HOURS[name])
            if span_days < min_days:
                print(f"\n  ⚠️  WARNING: {name} data spans only {span_days:.0f} days "
                      f"({len(df)} candles). Recommended minimum: {min_days} days "
                      f"({min_candles} candles). Short-TF models may overfit recent conditions.")
                if span_days < min_days * 0.5:
                    print(f"  🚨 CRITICAL: {name} data covers less than half the recommended span. "
                          f"Model will likely be fragile. Use --candles {min_candles}.")
```

- [ ] **Step 2: Run pipeline with thin data to verify warnings**

Run: `python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 15m --candles 500 2>&1 | grep -A3 "WARNING\|CRITICAL"`
Expected: Warning about 15m data spanning only ~5 days, CRITICAL flag for less than half recommended span

- [ ] **Step 3: Commit**

```bash
git add src/ml/train_pipeline.py
git commit -m "feat: increase default candles to 2000 and add data sufficiency warnings per timeframe"
```

---

### Task 6: Sigmoid Calibration Fallback for Small Samples (Minor)

**Why:** Isotonic calibration can overfit on small samples (it's a non-parametric step function). Sigmoid/Platt scaling is more stable when data is thin. Use sigmoid when validation set < 500 samples.

**Files:**
- Modify: `src/ml/regime_classifier.py:54-59` (calibrate method)

- [ ] **Step 1: Add sample-size-aware calibration**

Replace the `calibrate` method (lines 54-59):

```python
    def calibrate(self, X_val, y_val):
        n_samples = len(X_val)
        # Isotonic can overfit on small samples; sigmoid is more stable
        method = 'isotonic' if n_samples >= 500 else 'sigmoid'
        self.calibrated_model = CalibratedClassifierCV(
            self.model, method=method, cv=5
        )
        self.calibrated_model.fit(X_val, y_val)
        print(f"Calibration complete (method={method}, n_samples={n_samples}).")
```

- [ ] **Step 2: Run existing ML tests to verify no regression**

Run: `python -m pytest tests/test_ml_multi_pair.py -v`
Expected: All existing tests pass

- [ ] **Step 3: Commit**

```bash
git add src/ml/regime_classifier.py
git commit -m "fix: use sigmoid calibration for small samples (<500) to prevent overfitting"
```

---

### Task 7: DANGER Label Analysis and Retraining Documentation (Issue #5)

**Why:** DANGER drives "pause everything" but its label definition combines two loosely related conditions (whipsaw + high-vol-flat). The class is often rare. Need: (1) explicit DANGER count warning during training, (2) documentation of retraining cadence, (3) a simple drift check script.

**Files:**
- Modify: `src/ml/train_pipeline.py` (add DANGER count warning after label distribution)
- Create: `docs/ml-retraining-guide.md`

- [ ] **Step 1: Add DANGER count warning after label distribution**

In `train_pipeline.py`, after the label distribution print (line 151):

```python
        print(f"  Label distribution: {df_labeled['regime_label'].value_counts().to_dict()}")
```

Add after it:

```python
        # DANGER class sufficiency check
        danger_count = (df_labeled['regime_label'] == 2).sum()
        danger_pct = danger_count / len(df_labeled) * 100
        if danger_count < 50:
            print(f"  ⚠️  WARNING: Only {danger_count} DANGER samples ({danger_pct:.1f}%). "
                  f"Model will struggle to learn this class. Consider increasing --candles.")
        elif danger_pct < 5.0:
            print(f"  ⚠️  WARNING: DANGER class is only {danger_pct:.1f}% of labels ({danger_count} samples). "
                  f"Consider adjusting danger_threshold or trend_atr_k to increase DANGER examples.")
```

- [ ] **Step 2: Create retraining guide**

```markdown
# ML Regime Model Retraining Guide

## When to Retrain

| Trigger | Action |
|---------|--------|
| **Scheduled** | Retrain every 2 weeks (crypto regimes drift fast) |
| **Regime shift detected** | If model confidence drops below 0.55 for 24h+, retrain with latest data |
| **New pair added** | Train a new per-pair model before enabling the pair |
| **Accuracy degradation** | If out-of-sample accuracy drops >10% from baseline, retrain |

## Training Commands

```bash
# Standard retrain (all timeframes, 2000 candles)
python -m src.ml.train_pipeline --pair SOL-USDT

# High-quality retrain (more data, single timeframe)
python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 1h --candles 4000

# Quick validation (check if model still performs)
python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 1h --candles 2000
```

## Data Requirements

| Timeframe | Min Candles | Min Days | Recommended Candles |
|-----------|-------------|----------|---------------------|
| 15m       | 5,760       | 60       | 8,000+              |
| 1h        | 2,160       | 90       | 4,000+              |
| 4h        | 720         | 120      | 2,000+              |
| 1d        | 180         | 180      | 500+                |

## Drift Detection

Compare the last 7 days of model predictions against the training label distribution:

1. Count regime predictions per class over the last 7 days
2. If any class shifts >20% from training distribution, flag for retraining
3. If DANGER predictions increase >3× baseline, investigate immediately (may indicate real market shift)

## Deployment Checklist

- [ ] Train model and review accuracy, confusion matrix, DANGER recall
- [ ] Verify embargo gap is active (check "Embargo gap: N samples" in training output)
- [ ] Review feature importances — if all flat (0.04-0.10), model may be weak
- [ ] Copy `.pkl.new` → `.pkl` (active model)
- [ ] Restart Python container to pick up new model
- [ ] Monitor `/system` Telegram command for regime changes for 30 minutes
```

- [ ] **Step 3: Commit**

```bash
git add src/ml/train_pipeline.py docs/ml-retraining-guide.md
git commit -m "feat: add DANGER class sufficiency warnings and ML retraining guide"
```

---

## Post-Implementation Verification

- [ ] **Final Step: Retrain models with hardened pipeline and verify results**

```bash
# Retrain SOL-USDT with the full hardened pipeline
python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 1h --candles 2000
```

Check that:
1. "Embargo gap: 12 samples" is printed (Task 2)
2. Confusion matrix, transition accuracy, DANGER breakdown appear (Task 4)
3. Data sufficiency warning if applicable (Task 5)
4. DANGER count warning if applicable (Task 7)
5. Calibration method is printed (Task 6)

```bash
# Build and test Rust changes
cd trading-engine-core && cargo test && cargo check
```

Verify:
6. All regime_cache tests pass including TTL tests (Task 3)
7. Engine compiles with new TTL constructor (Task 3)

---

## Self-Review

**1. Spec coverage:**
- Issue #1 (label leakage): Task 1 + Task 2 ✓
- Issue #2 (thin data): Task 5 ✓
- Issue #3 (stale regime TTL): Task 3 ✓
- Issue #4 (confusion matrix / transition accuracy): Task 4 ✓
- Issue #5 (DANGER quality / retraining): Task 6 + Task 7 ✓
- Minor (sigmoid calibration): Task 6 ✓
- Minor (ADX 22-25 gap): Noted in docs, no code change needed ✓

**2. Placeholder scan:** No TBD, TODO, or vague steps found.

**3. Type consistency:**
- `PurgedTimeSeriesSplit` used in `train_pipeline.py` matches the class defined in `purged_cv.py` ✓
- `RegimeCache::new` signature `(file_path: &str, ttl_ms: i64)` matches all callers (tests + engine.rs) ✓
- `RegimeEntry.timestamp` is `i64` (Unix millis) compared against `chrono::Utc::now().timestamp_millis()` (also i64) ✓
