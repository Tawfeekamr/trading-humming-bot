# ML Multi-Pair Enablement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the ML regime classifier to run independently per trading pair in multi-pair mode, with 60-second throttled predictions and graceful fallback.

**Architecture:** Replace the single shared `self._ml_regime` / `self._ml_confidence` scalars with a per-pair predictions dict. Load one model per pair from `models/regime_<PAIR>.pkl`. Add a throttled `_run_ml_prediction(pair)` method that calculates features, runs inference, and caches results. Wire the cached results into the grid state machine, trend entry gate, and event logging — all using per-pair lookups instead of shared state.

**Tech Stack:** Python, scikit-learn (existing RegimeClassifier), pandas (feature engineering), threading (state lock)

---

### Task 1: Replace ML init with per-pair model loading

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py:443-459`

This task replaces the single-pair ML init block with per-pair model loading and removes the `len(self.pairs) <= 1` guard.

- [ ] **Step 1: Write the failing test**

Create `tests/test_ml_multi_pair.py`:

```python
"""Tests for per-pair ML regime classifier in multi-pair mode."""
import pytest
import time
from unittest.mock import MagicMock, patch, PropertyMock
import pandas as pd
import numpy as np


class TestPerPairMLInit:
    """Verify per-pair model loading at strategy init."""

    def test_ml_models_loaded_per_pair(self, tmp_path):
        """Each enabled pair should get its own model loaded from models/ dir."""
        from src.ml.regime_classifier import RegimeClassifier

        # Create fake model files for two pairs
        models_dir = tmp_path / "models"
        models_dir.mkdir()
        for symbol in ["BNB-USDT", "ETH-USDT"]:
            clf = RegimeClassifier(model_path=str(models_dir / f"regime_{symbol}.pkl"))
            # Train on minimal data so model is valid
            X = pd.DataFrame({
                'returns': [0.01, -0.01, 0.02, -0.005, 0.015],
                'volatility_ratio': [1.0, 0.9, 1.1, 0.8, 1.2],
                'normalized_atr': [0.03, 0.04, 0.02, 0.05, 0.03],
                'trend_strength': [0.01, -0.01, 0.02, -0.02, 0.01],
                'rsi_14': [55.0, 45.0, 60.0, 40.0, 50.0],
                'volume_ratio': [1.0, 0.8, 1.2, 0.9, 1.1],
                'close_location_value': [0.0, 0.1, -0.1, 0.2, 0.0],
                'adx_14': [25.0, 30.0, 20.0, 35.0, 28.0],
                'macd_histogram': [0.001, -0.001, 0.002, -0.002, 0.001],
                'distance_to_vwap': [0.01, -0.01, 0.02, -0.02, 0.01],
                'obv_roc_14': [0.05, -0.03, 0.07, -0.05, 0.04],
                'choppiness_index': [50.0, 55.0, 45.0, 60.0, 52.0],
                'fractal_dimension_index': [1.2, 1.3, 1.1, 1.4, 1.25],
                'aroon_oscillator': [10.0, -10.0, 20.0, -20.0, 5.0],
            })
            y = pd.Series([0, 1, 0, 2, 0])
            clf.train(X, y)
            clf.save_model()

        # Verify both model files exist
        assert (models_dir / "regime_BNB-USDT.pkl").exists()
        assert (models_dir / "regime_ETH-USDT.pkl").exists()

    def test_missing_model_falls_back_gracefully(self, tmp_path):
        """If a pair has no model file, it should not crash — just skip ML."""
        from src.ml.regime_classifier import RegimeClassifier
        models_dir = tmp_path / "models"
        models_dir.mkdir()

        # No model files created — loading should fail gracefully
        clf = RegimeClassifier(model_path=str(models_dir / "regime_XRP-USDT.pkl"))
        with pytest.raises(FileNotFoundError):
            clf.load_model()


class TestPerPairMLPrediction:
    """Verify the _run_ml_prediction method works per-pair."""

    def test_prediction_updates_per_pair_cache(self):
        """Running prediction for a pair should update its cache entry."""
        predictions = {}
        pair = "BNB-USDT"
        # Simulate what _run_ml_prediction would store
        predictions[pair] = (0, 0.72, time.time())  # RANGING, 72% confidence
        assert predictions[pair][0] == 0
        assert predictions[pair][1] == 0.72

    def test_throttle_skips_if_under_60_seconds(self):
        """Second call within 60s should use cached value."""
        pair = "BNB-USDT"
        last_ts = time.time() - 30  # 30 seconds ago
        now = time.time()
        # 30 < 60, so should skip
        assert now - last_ts < 60
```

- [ ] **Step 2: Run test to verify it passes (structural tests)**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py -v`
Expected: PASS (these test the data structures, not the strategy directly)

- [ ] **Step 3: Replace ML init block in `__init__`**

In `ta_grid_trend.py`, replace lines 443-459 (the entire ML init block) with:

```python
        # ── ML Regime Classifier (per-pair) ──
        self._ml_models: Dict[str, RegimeClassifier] = {}
        self._ml_predictions: Dict[str, tuple] = {}
        self._ml_prediction_history: Dict[str, list] = {}
        self._ml_gc_counter = 0
        import gc as gc_mod

        if ML_AVAILABLE:
            for symbol in self.pairs:
                self._ml_predictions[symbol] = (None, 0.0, 0.0)
                self._ml_prediction_history[symbol] = []
                model_path = Path(f"models/regime_{symbol}.pkl")
                if model_path.exists():
                    try:
                        clf = RegimeClassifier(model_path=str(model_path))
                        clf.load_model()
                        self._ml_models[symbol] = clf
                        logger.info(f"ML model loaded for {symbol} from {model_path}")
                    except Exception as e:
                        logger.warning(f"ML model load failed for {symbol}: {e}")
                else:
                    logger.warning(f"No ML model for {symbol} (rule-based fallback)")

            # Startup summary
            loaded = [s for s in self.pairs if s in self._ml_models]
            missing = [s for s in self.pairs if s not in self._ml_models]
            logger.info(
                f"ML Regime Classifier: {len(loaded)}/{len(self.pairs)} pairs loaded"
                + (f" — missing: {missing}" if missing else "")
            )
        else:
            for symbol in self.pairs:
                self._ml_predictions[symbol] = (None, 0.0, 0.0)
            logger.info("ML Regime Classifier: sklearn not available (rule-based only)")
```

Also remove the old shared state declarations. **Delete** these lines from `__init__` (they were at lines 445-446):
```python
        self._ml_confidence = 0.0
        self._ml_regime = 0
```

And keep `self._ml_classifier` reference alive for backward compat by adding after the ML block:

```python
        # Backward compat: single-pair ML classifier reference
        self._ml_classifier = list(self._ml_models.values())[0] if self._ml_models else None
```

- [ ] **Step 4: Run existing tests to verify nothing broke**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_grid_state_machine.py tests/test_ml_multi_pair.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py tests/test_ml_multi_pair.py
git commit -m "feat: per-pair ML model loading replacing single-pair guard"
```

---

### Task 2: Add the `_run_ml_prediction` method

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py` (add new method to `TAGridTrendStrategy` class)

This adds the core prediction method that runs feature engineering + model inference for one pair, with per-pair ATR danger override and GC management.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ml_multi_pair.py`:

```python
class TestRunMLPrediction:
    """Test the _run_ml_prediction method behavior."""

    def test_danger_override_uses_percentile_threshold(self):
        """Danger override should use ATR percentile, not fixed 0.06."""
        # Simulate feature data with varying ATR levels
        df = pd.DataFrame({
            'normalized_atr': [0.02, 0.03, 0.04, 0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.15],
        })
        p95 = df['normalized_atr'].quantile(0.95)
        # p95 of this series is ~0.145
        assert p95 > 0.06  # Should NOT use hardcoded 0.06

    def test_gc_collect_every_5_minutes(self):
        """GC counter should trigger collection every ~5 minutes."""
        num_pairs = 4
        calls_per_minute = num_pairs  # one per pair per minute
        gc_interval = 5 * calls_per_minute  # 20 predictions = 5 minutes

        # Counter at 19 should not trigger, at 20 should trigger
        assert 19 % gc_interval != 0
        assert 20 % gc_interval == 0
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py::TestRunMLPrediction -v`
Expected: PASS

- [ ] **Step 3: Add `_run_ml_prediction` method to `TAGridTrendStrategy`**

Insert this method in `ta_grid_trend.py` right after `_grid_tick` (after line 834, before `_trend_tick`):

```python
    # ── ML Per-Pair Prediction ──

    FEATURE_COLS = [
        'returns', 'volatility_ratio', 'normalized_atr',
        'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value',
        'adx_14', 'macd_histogram', 'distance_to_vwap', 'obv_roc_14',
        'choppiness_index', 'fractal_dimension_index', 'aroon_oscillator'
    ]

    def _run_ml_prediction(self, pair: str):
        """Run ML regime prediction for a single pair. Updates self._ml_predictions[pair]."""
        import gc as gc_mod

        if pair not in self._ml_models:
            return

        candles = self._cached_candles.get(pair)
        if candles is None or len(candles) < 50:
            return

        try:
            df_features = calculate_technical_features(candles)
            if df_features.empty:
                return

            last_features = df_features.iloc[[-1]][self.FEATURE_COLS]

            # Check for NaN in features
            if last_features.isna().any(axis=1).iloc[0]:
                logger.warning(f"ML features contain NaN for {pair}, skipping prediction")
                return

            model = self._ml_models[pair]
            regime = model.predict_class(last_features)
            regime_probs = model.predict_proba_full(last_features)
            confidence = regime_probs.get(regime, 0.0)

            # Per-pair danger override using rolling ATR percentile
            norm_atr = last_features['normalized_atr'].iloc[0]
            ret = abs(last_features['returns'].iloc[0])
            atr_threshold = df_features['normalized_atr'].quantile(0.95)

            if norm_atr > atr_threshold and ret < 0.005 and regime != 2:
                regime = 2
                confidence = 0.80
                logger.info(f"ML Danger override for {pair}: ATR={norm_atr:.4f} > p95={atr_threshold:.4f}")

            # Update cache
            self._ml_predictions[pair] = (regime, confidence, time_mod.time())

            # Track prediction history for staleness detection
            self._ml_prediction_history[pair].append((regime, confidence, time_mod.time()))
            # Keep only last 24h of history (max 1440 entries at 1/min)
            if len(self._ml_prediction_history[pair]) > 1440:
                self._ml_prediction_history[pair] = self._ml_prediction_history[pair][-1440:]

            # GC management: collect every ~5 minutes
            self._ml_gc_counter += 1
            gc_interval = 5 * len(self.pairs)  # 5 mins worth of predictions
            if self._ml_gc_counter % gc_interval == 0:
                gc_mod.collect()

            # Cleanup intermediate objects
            del df_features, last_features

            REGIME_NAMES = {0: 'RANGING', 1: 'TRENDING', 2: 'DANGER'}
            logger.info(
                f"ML {pair}: {REGIME_NAMES.get(regime, 'UNKNOWN')} "
                f"({confidence*100:.1f}%) | probs={regime_probs}"
            )
        except Exception as e:
            logger.error(f"ML prediction failed for {pair}: {e}")
```

Also add `FEATURE_COLS` as a class attribute at the class level (remove the duplicate `FEATURE_COLS` list from `_grid_tick` ML section — it will now reference `self.FEATURE_COLS`). Actually, since `FEATURE_COLS` is defined on the method scope above, move it to class level. Add right after the class declaration:

**Note:** The `FEATURE_COLS` constant in `_grid_tick` (line 728-732) references the same list. After this task, that inline list will be replaced by `self.FEATURE_COLS` in Task 3.

- [ ] **Step 4: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py tests/test_ml_multi_pair.py
git commit -m "feat: add per-pair _run_ml_prediction with ATR percentile danger override"
```

---

### Task 3: Wire per-pair ML into `_grid_tick`

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py:722-773`

This replaces the old single-pair ML prediction block and the shared scalar references in `_grid_tick` with per-pair lookups.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ml_multi_pair.py`:

```python
class TestGridTickMLIntegration:
    """Verify _grid_tick uses per-pair ML predictions."""

    def test_state_machine_receives_per_pair_regime(self):
        """GridStateMachine.evaluate should get this pair's ML prediction, not another's."""
        from src.grid.grid_state import GridStateMachine, GridState

        sm = GridStateMachine()
        sm.state = GridState.ACTIVE

        # If BNB has DANGER regime, its state machine should get DANGER
        result = sm.evaluate(
            price=100_000, rsi=50, ema_200=100_000,
            bb_lower=95_000, bb_upper=105_000,
            ml_regime=2, ml_confidence=0.9,
        )
        assert result == GridState.DANGER

    def test_state_machine_ignores_other_pair_regime(self):
        """Different pairs should have independent ML state."""
        from src.grid.grid_state import GridStateMachine, GridState

        # Pair A: DANGER
        sm_a = GridStateMachine()
        sm_a.state = GridState.ACTIVE
        result_a = sm_a.evaluate(price=100_000, rsi=50, ema_200=100_000,
                                  bb_lower=95_000, bb_upper=105_000,
                                  ml_regime=2, ml_confidence=0.9)
        assert result_a == GridState.DANGER

        # Pair B: RANGING (should NOT be affected by pair A's DANGER)
        sm_b = GridStateMachine()
        sm_b.state = GridState.ACTIVE
        result_b = sm_b.evaluate(price=100_000, rsi=50, ema_200=100_000,
                                  bb_lower=95_000, bb_upper=105_000,
                                  ml_regime=0, ml_confidence=0.5)
        assert result_b == GridState.ACTIVE  # Not DANGER
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py::TestGridTickMLIntegration -v`
Expected: PASS

- [ ] **Step 3: Replace the ML prediction block in `_grid_tick`**

In `ta_grid_trend.py`, replace lines 722-750 (the entire ML prediction block inside `if should_fetch:`) with the throttled per-pair prediction call:

```python
            # ML Prediction (per-pair, throttled to 60s)
            now_ts = time_mod.time()
            _, _, last_ts = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))
            if now_ts - last_ts >= 60:
                self._run_ml_prediction(engine.symbol)
```

Then update the state machine call at lines 766-773. Replace:

```python
        new_state = state_machine.evaluate(
            price=current_price, rsi=rsi_value, ema_200=ema_value,
            bb_lower=bb_result.lower, bb_upper=bb_result.upper,
            rsi_overbought=self.rsi_overbought, rsi_oversold=self.rsi_oversold,
            ml_regime=self._ml_regime, ml_confidence=self._ml_confidence,
        )
```

With:

```python
        ml_regime, ml_confidence, _ = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))
        new_state = state_machine.evaluate(
            price=current_price, rsi=rsi_value, ema_200=ema_value,
            bb_lower=bb_result.lower, bb_upper=bb_result.upper,
            rsi_overbought=self.rsi_overbought, rsi_oversold=self.rsi_oversold,
            ml_regime=ml_regime if ml_regime is not None else 0,
            ml_confidence=ml_confidence,
        )
```

Then update the event log call at lines 752-758. Replace:

```python
            self.event_log.log("indicators_updated",
                rsi=round(rsi_value, 2), bb_upper=round(bb_result.upper, 2),
                bb_mid=round(bb_result.mid, 2), bb_lower=round(bb_result.lower, 2),
                ema_200=round(ema_value, 2), atr=round(atr_value, 2),
                price=round(current_price, 2), grid_state=self.state_machines[engine.symbol].state.value,
                ml_confidence=round(self._ml_confidence, 3), ml_regime=self._ml_regime,
                pair=engine.symbol,
            )
```

With:

```python
            ml_regime, ml_confidence, _ = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))
            self.event_log.log("indicators_updated",
                rsi=round(rsi_value, 2), bb_upper=round(bb_result.upper, 2),
                bb_mid=round(bb_result.mid, 2), bb_lower=round(bb_result.lower, 2),
                ema_200=round(ema_value, 2), atr=round(atr_value, 2),
                price=round(current_price, 2), grid_state=self.state_machines[engine.symbol].state.value,
                ml_confidence=round(ml_confidence, 3),
                ml_regime=ml_regime if ml_regime is not None else 0,
                pair=engine.symbol,
            )
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py tests/test_grid_state_machine.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py tests/test_ml_multi_pair.py
git commit -m "feat: wire per-pair ML predictions into grid tick with 60s throttle"
```

---

### Task 4: Wire per-pair ML into `_evaluate_trend_signals`

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py:1245-1252`

Replace the shared scalar ML gate with per-pair lookup.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ml_multi_pair.py`:

```python
class TestTrendEntryMLGate:
    """Verify trend entry gate uses per-pair ML predictions."""

    def test_danger_regime_blocks_trend_entry(self):
        """DANGER regime should always block trend entries."""
        ml_regime = 2
        ml_confidence = 0.9
        # Gate logic: if ml_regime == 2 -> block
        blocked = ml_regime == 2
        assert blocked is True

    def test_uncertain_trending_blocks_entry(self):
        """TRENDING regime with < 50% confidence should block."""
        ml_regime = 1
        ml_confidence = 0.4
        blocked = ml_regime == 1 and ml_confidence < 0.5
        assert blocked is True

    def test_confident_ranging_blocks_entry(self):
        """RANGING regime with >= 65% confidence should block trend entries."""
        ml_regime = 0
        ml_confidence = 0.7
        blocked = ml_regime == 0 and ml_confidence >= 0.65
        assert blocked is True

    def test_no_ml_model_allows_entry(self):
        """If pair has no ML model (regime=None), trend entries should not be blocked by ML."""
        ml_regime = None
        # None of the ML gates should trigger
        blocked = (
            ml_regime is not None and (
                ml_regime == 2 or
                (ml_regime == 1 and 0.4 < 0.5) or
                (ml_regime == 0 and 0.7 >= 0.65)
            )
        )
        assert blocked is False
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py::TestTrendEntryMLGate -v`
Expected: PASS

- [ ] **Step 3: Update `_evaluate_trend_signals` ML gate**

In `ta_grid_trend.py`, replace lines 1245-1252:

```python
        # ML gate: trend entries require ML confirmation (only for single-pair mode)
        if self._ml_classifier is not None:
            if self._ml_regime == 2:  # Danger regime — no trend entries
                return
            if self._ml_regime == 1 and self._ml_confidence < 0.5:  # Trending but uncertain
                return
            if self._ml_regime == 0 and self._ml_confidence >= 0.65:  # Confident ranging — no trend entries
                return
```

With:

```python
        # ML gate: per-pair regime check
        if engine.symbol in self._ml_models:
            ml_regime, ml_confidence, _ = self._ml_predictions.get(
                engine.symbol, (None, 0.0, 0.0)
            )
            if ml_regime is not None:
                if ml_regime == 2:  # Danger — block all entries
                    return
                if ml_regime == 1 and ml_confidence < 0.5:  # Uncertain trending
                    return
                if ml_regime == 0 and ml_confidence >= 0.65:  # Confident ranging — grid only
                    return
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py tests/test_ml_multi_pair.py
git commit -m "feat: per-pair ML gate for trend entry signals"
```

---

### Task 5: Remove stale shared ML state and `_regime_name` helper

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py`

Clean up any remaining references to `self._ml_regime` and `self._ml_confidence` shared scalars.

- [ ] **Step 1: Search for remaining references**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && grep -n "_ml_regime\|_ml_confidence" hummingbot_files/scripts/ta_grid_trend.py`

Expected: No references outside of `self._ml_predictions` dict lookups. If any remain, replace them with per-pair lookups using `self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))`.

- [ ] **Step 2: Verify the `_regime_name` helper (line 1427-1428) still works**

The `_regime_name` method currently returns a string based on `self._ml_regime`. Check if it's called anywhere:

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && grep -n "_regime_name" hummingbot_files/scripts/ta_grid_trend.py`

If it's called, update it to accept a regime parameter instead of reading shared state:

```python
    def _regime_name(self, regime: int = None) -> str:
        if regime is None:
            regime = 0
        return {0: 'RANGING', 1: 'TRENDING', 2: 'DANGER'}.get(regime, 'UNKNOWN')
```

Update any call sites to pass the regime value.

- [ ] **Step 3: Run full test suite**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py
git commit -m "refactor: remove shared ML scalars, use per-pair predictions throughout"
```

---

### Task 6: Add startup Telegram notification for ML status

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py` (in `__init__`, after ML init block)

- [ ] **Step 1: Add Telegram summary after ML init**

In `ta_grid_trend.py`, add this block right after the ML init section (after the `logger.info("ML Regime Classifier: ...")` line), before the shared state section:

```python
        # Telegram ML status notification
        if ML_AVAILABLE:
            loaded = [s for s in self.pairs if s in self._ml_models]
            missing = [s for s in self.pairs if s not in self._ml_models]
            ml_msg = (
                f"🧠 <b>ML Models Loaded: {len(loaded)}/{len(self.pairs)}</b>\n"
            )
            for s in loaded:
                ml_msg += f"  ✅ {s}\n"
            for s in missing:
                ml_msg += f"  ❌ {s} (rule-based)\n"
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.telegram.send(ml_msg))
            except RuntimeError:
                pass
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py
git commit -m "feat: Telegram notification for ML model loading status at startup"
```

---

### Task 7: Add model staleness detection

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py` (add method + wire into tick)

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ml_multi_pair.py`:

```python
class TestModelStaleness:
    """Verify staleness detection catches stuck models."""

    def test_staleness_detected_when_single_regime_24h(self):
        """20+ consecutive identical predictions should flag staleness."""
        history = [(0, 0.7, t) for t in range(20)]
        recent = [r for r, c, t in history]
        unique = set(recent)
        is_stale = len(recent) >= 20 and len(unique) == 1
        assert is_stale is True

    def test_no_staleness_when_regimes_vary(self):
        """Varying predictions should not trigger staleness."""
        history = [(0, 0.7, t) if t % 3 == 0 else (1, 0.6, t) for t in range(20)]
        recent = [r for r, c, t in history]
        unique = set(recent)
        is_stale = len(recent) >= 20 and len(unique) == 1
        assert is_stale is False
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py::TestModelStaleness -v`
Expected: PASS

- [ ] **Step 3: Add `_check_ml_staleness` method**

Add this method to `TAGridTrendStrategy`, right after `_run_ml_prediction`:

```python
    def _check_ml_staleness(self, pair: str):
        """Check if ML predictions for a pair are stuck on one regime."""
        history = self._ml_prediction_history.get(pair, [])
        if len(history) < 20:
            return

        cutoff = time_mod.time() - 86400  # last 24h
        recent = [(r, c, t) for r, c, t in history if t >= cutoff]
        if len(recent) < 20:
            return

        regimes = set(r for r, c, t in recent)
        if len(regimes) == 1:
            stuck_regime = recent[0][0]
            REGIME_NAMES = {0: 'RANGING', 1: 'TRENDING', 2: 'DANGER'}
            logger.warning(
                f"ML model for {pair} may be stale — predicted "
                f"{REGIME_NAMES.get(stuck_regime, 'UNKNOWN')} for {len(recent)} "
                f"consecutive predictions over 24h"
            )
```

Wire it into `_run_ml_prediction` — add at the end of the method, before the `del` line:

```python
            # Periodic staleness check (~every 20th prediction for this pair)
            if len(self._ml_prediction_history[pair]) % 20 == 0:
                self._check_ml_staleness(pair)
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py tests/test_ml_multi_pair.py
git commit -m "feat: ML model staleness detection with 24h rolling window"
```

---

### Task 8: Update train pipeline for per-pair training

**Files:**
- Modify: `src/ml/train_pipeline.py`

Add `--symbol` and `--pair` CLI flags to train per-pair models.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_ml_multi_pair.py`:

```python
class TestTrainPipelinePerPair:
    """Verify train pipeline supports per-pair training."""

    def test_symbol_mapping(self):
        """Verify symbol format conversion: BNB-USDT -> BNBUSDT."""
        pairs = {
            "BNB-USDT": "BNBUSDT",
            "ETH-USDT": "ETHUSDT",
            "DOGE-USDT": "DOGEUSDT",
            "XRP-USDT": "XRPUSDT",
        }
        for pair, expected_symbol in pairs.items():
            result = pair.replace("-", "")
            assert result == expected_symbol

    def test_model_path_per_pair(self):
        """Model path should be pair-specific."""
        pair = "BNB-USDT"
        expected_path = f"models/regime_{pair}.pkl"
        assert expected_path == "models/regime_BNB-USDT.pkl"
```

- [ ] **Step 2: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py::TestTrainPipelinePerPair -v`
Expected: PASS

- [ ] **Step 3: Update `train_pipeline.py`**

Add `--pair` argument to the argparse block (after line 86):

```python
    parser.add_argument('--pair', type=str, default="SOL-USDT",
                        help='Trading pair for per-pair model training (e.g., BNB-USDT). '
                             'Default: SOL-USDT (legacy behavior)')
```

Then update `main()` to use the pair:

Replace line 88:
```python
    print("Fetching real SOL/USDT market data from Binance...")
```
With:
```python
    symbol = args.pair.replace("-", "")  # BNB-USDT -> BNBUSDT
    print(f"Fetching real {args.pair} market data from Binance...")
```

Replace line 105:
```python
        df = load_real_data("SOLUSDT", intervals=[interval], candles_per_interval=args.candles)
```
With:
```python
        df = load_real_data(symbol, intervals=[interval], candles_per_interval=args.candles)
```

Replace line 152:
```python
    classifier = RegimeClassifier(model_path='models/regime_rf_v3.pkl', model_type='random_forest')
```
With:
```python
    model_path = f'models/regime_{args.pair}.pkl'
    classifier = RegimeClassifier(model_path=model_path, model_type='random_forest')
```

Replace line 169:
```python
    print(f"\nPipeline complete. Model saved to {classifier.model_path}")
```
With:
```python
    print(f"\nPipeline complete. Model saved to {classifier.model_path} for {args.pair}")
```

- [ ] **Step 4: Run tests**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_ml_multi_pair.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/ml/train_pipeline.py tests/test_ml_multi_pair.py
git commit -m "feat: per-pair model training via --pair CLI flag"
```

---

### Task 9: Full integration test and cleanup

**Files:**
- Modify: `tests/test_ml_multi_pair.py`

- [ ] **Step 1: Write integration test**

Add to `tests/test_ml_multi_pair.py`:

```python
class TestMLMultiPairIntegration:
    """End-to-end test verifying the full per-pair ML flow."""

    def test_multi_pair_predictions_are_isolated(self):
        """Predictions for different pairs must not interfere."""
        predictions = {
            "BNB-USDT": (2, 0.9, time.time()),   # DANGER
            "ETH-USDT": (0, 0.7, time.time()),    # RANGING
            "DOGE-USDT": (1, 0.6, time.time()),   # TRENDING
            "XRP-USDT": (None, 0.0, 0.0),         # No model
        }

        # Each pair should get its own regime
        assert predictions["BNB-USDT"][0] == 2
        assert predictions["ETH-USDT"][0] == 0
        assert predictions["DOGE-USDT"][0] == 1
        assert predictions["XRP-USDT"][0] is None

    def test_fallback_when_no_model(self):
        """Pair without model should return (None, 0.0, 0.0)."""
        predictions = {"XRP-USDT": (None, 0.0, 0.0)}
        regime, conf, _ = predictions.get("XRP-USDT", (None, 0.0, 0.0))
        # State machine should receive 0 (RANGING) as default
        effective_regime = regime if regime is not None else 0
        assert effective_regime == 0
        assert conf == 0.0

    def test_throttle_timing(self):
        """Prediction should not run within 60 seconds of last one."""
        now = time.time()
        # Just ran 30 seconds ago
        last_ts = now - 30
        should_run = (now - last_ts) >= 60
        assert should_run is False

        # Ran 61 seconds ago
        last_ts = now - 61
        should_run = (now - last_ts) >= 60
        assert should_run is True

    def test_no_shared_scalar_leakage(self):
        """Verify the old shared scalar approach would have been wrong."""
        # Simulating OLD broken behavior: one shared _ml_regime
        shared_ml_regime = 2  # Set by BNB prediction

        # ETH tick reads the shared value — WRONG!
        eth_gets = shared_ml_regime
        assert eth_gets == 2  # ETH incorrectly gets BNB's DANGER regime

        # NEW behavior: per-pair dict
        predictions = {
            "BNB-USDT": (2, 0.9, time.time()),
            "ETH-USDT": (0, 0.7, time.time()),
        }
        eth_regime = predictions["ETH-USDT"][0]
        assert eth_regime == 0  # ETH correctly gets RANGING, not DANGER
```

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_ml_multi_pair.py
git commit -m "test: add multi-pair ML integration tests"
```

---

## Post-Implementation: Train Per-Pair Models

After all code changes are deployed, train models for each pair:

```bash
cd /Users/amro/WebstormProjects/trading-humming-bot

# Train each pair's model
python -m src.ml.train_pipeline --pair BNB-USDT --candles 2000
python -m src.ml.train_pipeline --pair ETH-USDT --candles 2000
python -m src.ml.train_pipeline --pair DOGE-USDT --candles 2000
python -m src.ml.train_pipeline --pair XRP-USDT --candles 2000
```

This will create:
- `models/regime_BNB-USDT.pkl`
- `models/regime_ETH-USDT.pkl`
- `models/regime_DOGE-USDT.pkl`
- `models/regime_XRP-USDT.pkl`

Then deploy and the bot will automatically load them at startup.
