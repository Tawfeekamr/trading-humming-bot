# ML Regime Classifier for Multi-Pair Mode

## Problem

The ML regime classifier is disabled when running multi-pair mode (`len(self.pairs) <= 1` guard in `ta_grid_trend.py:447`). The bot currently trades 4 pairs (BNB-USDT, ETH-USDT, DOGE-USDT, XRP-USDT) without ML-enhanced regime detection. All 4 pairs run purely rule-based, missing the danger-regime protection and confidence-adjusted thresholds that the single-pair mode benefits from.

## Design

### Per-pair model management

- One trained model per pair: `models/regime_BNB-USDT.pkl`, `models/regime_ETH-USDT.pkl`, etc.
- All available models loaded at strategy init into a dict: `self._ml_models: Dict[str, RegimeClassifier]`
- Missing model for a pair → rule-based fallback, logged as warning at startup
- Model loading wrapped in try/except; one failure doesn't prevent other models from loading

### Per-pair ML state

- Cached predictions stored in: `self._ml_predictions: Dict[str, Tuple[int, float, float]]`
  - Key: pair symbol (e.g. `"BNB-USDT"`)
  - Value: `(regime, confidence, timestamp)`
- Initialized with `(None, 0.0, 0.0)` for each pair
- Updated only by the throttled prediction call

**IMPORTANT: Remove shared scalar state.** The current `self._ml_regime` and `self._ml_confidence` class-level attributes must be **deleted**. They are read in 3 places that would leak the last-predicted pair's regime to all other pairs:
  - Grid state evaluation (`ta_grid_trend.py:772`)
  - Trend entry ML gate (`ta_grid_trend.py:1246-1252`)
  - Event logging (`ta_grid_trend.py:757`)

All 3 sites must be rewritten to read from the per-pair `self._ml_predictions` dict.

### 60-second throttle

- Per-pair throttle: only run prediction if `time.time() - last_prediction_time >= 60`
- No queue, no accumulation — if a tick lands before 60s elapsed, use cached value
- This means 4 predictions per minute total (one per pair), minimal CPU impact

### Prediction execution

New method `_run_ml_prediction(pair: str)`:
1. Get candles for pair from `self._cached_candles[pair]` (already fetched by grid tick)
2. Call `calculate_technical_features(df)`
3. Select the 14-feature subset (same as current single-pair):
   ```python
   FEATURE_COLS = [
       'returns', 'volatility_ratio', 'normalized_atr',
       'trend_strength', 'rsi_14', 'volume_ratio', 'close_location_value',
       'adx_14', 'macd_histogram', 'distance_to_vwap', 'obv_roc_14',
       'choppiness_index', 'fractal_dimension_index', 'aroon_oscillator'
   ]
   ```
4. Call `model.predict_class(features)` and `model.predict_proba_full(features)`
5. Apply per-pair danger override (see section below)
6. Update `self._ml_predictions[pair]` with `(regime, confidence, time.time())`
7. `del df_features, last_features` to free memory
8. Log prediction result

### Per-pair volatility danger override

The current single-pair code has a hardcoded danger override (`ta_grid_trend.py:738-744`):
```python
# CURRENT (broken for multi-pair):
if norm_atr > 0.06 and ret < 0.005 and self._ml_regime != 2:
    self._ml_regime = 2       # writes shared scalar!
    self._ml_confidence = 0.80
```

**Problem:** Different pairs have different "normal" ATR levels — DOGE's normalized ATR is naturally higher than ETH's. A fixed `0.06` threshold false-triggers DANGER for volatile pairs.

**Fix:** Use per-pair ATR percentile thresholds:
```python
# Per-pair danger override using rolling ATR percentile
DANGER_ATR_PERCENTILE = 0.95  # top 5% of historical ATR
norm_atr = last_features['normalized_atr'].iloc[0]
ret = abs(last_features['returns'].iloc[0])
atr_threshold = df_features['normalized_atr'].quantile(DANGER_ATR_PERCENTILE)

if norm_atr > atr_threshold and ret < 0.005 and regime != 2:
    regime = 2
    confidence = 0.80
    logger.info(f"ML Danger override for {pair}: ATR={norm_atr:.4f} > p95={atr_threshold:.4f}")
```

This is self-calibrating — each pair's danger threshold adapts to its own volatility profile.

### Garbage collection

- Feature DataFrames and numpy arrays are local to `_run_ml_prediction`
- Explicit `del df_features, last_features` after prediction
- `gc.collect()` every 5 minutes (not every call — too expensive)
- `gc.collect()` counter: increment per prediction, trigger when `counter % (5 * len(self.pairs)) == 0` (roughly every 5 minutes across all pairs)
- No pre-allocated buffers — the overhead of 4 small DataFrames per minute is negligible

### Integration points

**Grid tick** (`_grid_tick`):
```python
# Look up per-pair prediction (replaces shared self._ml_regime / self._ml_confidence)
ml_regime, ml_confidence, _ = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))
new_state = state_machine.evaluate(
    ...,
    ml_regime=ml_regime if ml_regime is not None else 0,
    ml_confidence=ml_confidence,
)
```

**Trend entry gate** (`_evaluate_trend_signals`):
```python
# Replaces: if self._ml_classifier is not None:
if engine.symbol in self._ml_models:
    ml_regime, ml_confidence, _ = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))
    if ml_regime == REGIME_DANGER:
        return  # block all entries
    if ml_regime == REGIME_TRENDING and ml_confidence < 0.50:
        return  # uncertain trend — don't enter
    if ml_regime == REGIME_RANGING and ml_confidence >= 0.65:
        return  # confident ranging — grid only, no trend
```

**Throttle check** — called at the top of each pair's grid tick, after candle fetch:
```python
now = time.time()
_, _, last_ts = self._ml_predictions.get(pair, (None, 0.0, 0.0))
if now - last_ts >= 60 and pair in self._ml_models:
    self._run_ml_prediction(pair)
```

**Event logging** — use per-pair values:
```python
ml_regime, ml_confidence, _ = self._ml_predictions.get(engine.symbol, (None, 0.0, 0.0))
self.event_log.log("indicators_updated",
    ...,
    ml_regime=ml_regime if ml_regime is not None else 0,
    ml_confidence=round(ml_confidence, 3),
    pair=engine.symbol,
)
```

### Startup health check

At end of `__init__`, log:
```
ML Regime Classifier: BNB-USDT ✓, ETH-USDT ✓, DOGE-USDT ✓, XRP-USDT ✗ (no model, using rule-based)
```

Also send a Telegram summary:
```
🧠 ML Models Loaded: 3/4
  ✓ BNB-USDT  ✓ ETH-USDT  ✓ DOGE-USDT
  ✗ XRP-USDT (rule-based fallback)
```

### Fallback behavior

- No model for pair → `ml_regime=None, ml_confidence=0.0` → state machine uses rule-based only (same as current multi-pair behavior)
- Prediction throws exception → log warning, keep cached value, don't crash
- Feature calculation returns NaN → skip prediction, keep cached value
- Model file corrupt / wrong version → log error at startup, exclude from `_ml_models` dict

### Model staleness detection

Track prediction distributions per pair with a rolling 24-hour window:

```python
# In _run_ml_prediction, after updating prediction:
self._ml_prediction_history[pair].append((regime, confidence, timestamp))

# Periodic check (every hour):
def _check_ml_staleness(self, pair: str):
    recent = [r for r, c, t in self._ml_prediction_history[pair]
              if time.time() - t < 86400]  # last 24h
    if len(recent) >= 20:
        unique_regimes = set(recent)
        if len(unique_regimes) == 1:
            logger.warning(
                f"ML model for {pair} may be stale — predicted {REGIME_NAMES[recent[0]]} "
                f"for {len(recent)} consecutive predictions over 24h"
            )
            # Optionally notify via Telegram
```

This catches models that are stuck predicting the same regime indefinitely.

### Training

#### Symbol mapping

| Pair (config) | Binance API symbol | Model path |
|---|---|---|
| `BNB-USDT` | `BNBUSDT` | `models/regime_BNB-USDT.pkl` |
| `ETH-USDT` | `ETHUSDT` | `models/regime_ETH-USDT.pkl` |
| `DOGE-USDT` | `DOGEUSDT` | `models/regime_DOGE-USDT.pkl` |
| `XRP-USDT` | `XRPUSDT` | `models/regime_XRP-USDT.pkl` |

#### Training pipeline changes

`train_pipeline.py` updated with:
- `--pair` parameter: accepts config-style pair name (e.g. `BNB-USDT`)
- Auto-derives Binance symbol: `pair.replace("-", "")` → `BNBUSDT`
- Auto-derives model path: `models/regime_{pair}.pkl`
- Per-pair interval configs with pair-specific thresholds:

```python
PAIR_CONFIGS = {
    "BNB-USDT": {
        "1h": {"forward_window": 12, "trend_threshold": 0.02, "trend_atr_k": 1.5},
        "4h": {"forward_window": 6,  "trend_threshold": 0.025, "trend_atr_k": 1.5},
    },
    "DOGE-USDT": {
        "1h": {"forward_window": 12, "trend_threshold": 0.03, "trend_atr_k": 1.8},  # higher vol
        "4h": {"forward_window": 6,  "trend_threshold": 0.04, "trend_atr_k": 2.0},
    },
    "ETH-USDT": {
        "1h": {"forward_window": 12, "trend_threshold": 0.018, "trend_atr_k": 1.4},
        "4h": {"forward_window": 6,  "trend_threshold": 0.022, "trend_atr_k": 1.5},
    },
    "XRP-USDT": {
        "1h": {"forward_window": 12, "trend_threshold": 0.025, "trend_atr_k": 1.6},
        "4h": {"forward_window": 6,  "trend_threshold": 0.03,  "trend_atr_k": 1.8},
    },
}
```

#### Batch training

New `scripts/train_all_models.sh`:
```bash
#!/bin/bash
set -e
for PAIR in BNB-USDT ETH-USDT DOGE-USDT XRP-USDT; do
    echo "=== Training $PAIR ==="
    python -m src.ml.train_pipeline --pair "$PAIR" --candles 2000
done
echo "All models trained."
```

#### Legacy compatibility

- Existing `regime_rf_v3.pkl` remains untouched (used by single-pair mode if `len(pairs) <= 1`)
- New per-pair models stored alongside it in `models/`

## Files to modify

### 1. `ta_grid_trend.py` — Main strategy (largest change)

| Section | Lines | Change |
|---|---|---|
| ML init block | 443-459 | Replace single-model loading with per-pair model loop; remove `self._ml_classifier`, `self._ml_regime`, `self._ml_confidence` |
| Per-pair state init | 476-480 | Add `self._ml_predictions[symbol] = (None, 0.0, 0.0)` |
| Grid tick — candle fetch | 722-750 | Replace single-model prediction block with throttled `self._run_ml_prediction(engine.symbol)` call |
| Grid tick — state evaluation | 768-773 | Replace `self._ml_regime` / `self._ml_confidence` with per-pair dict lookup |
| Grid tick — event log | 752-758 | Read ml_regime/confidence from per-pair dict |
| Trend entry gate | 1245-1252 | Replace `self._ml_classifier is not None` with `engine.symbol in self._ml_models`; read from per-pair dict |
| New method | — | Add `_run_ml_prediction(self, pair: str)` |
| New method | — | Add `_check_ml_staleness(self, pair: str)` (called hourly) |

### 2. `src/ml/train_pipeline.py` — Training script

| Change | Detail |
|---|---|
| Add `--pair` arg | `argparse` argument, maps to Binance symbol and model output path |
| Per-pair configs | `PAIR_CONFIGS` dict with pair-specific thresholds |
| Model output path | `models/regime_{pair}.pkl` |
| Default behavior | No `--pair` → original SOL behavior (backward compat) |

### 3. `scripts/train_all_models.sh` — [NEW] Batch training

Shell script that trains all 4 pair models sequentially.

### 4. `src/ml/regime_classifier.py` — No changes

### 5. `src/data/feature_engineering.py` — No changes

## Memory budget

- 4 models x ~5MB = ~20MB resident memory (loaded once at startup)
- 4 cached predictions = ~100 bytes (trivial)
- 4 prediction history buffers (24h rolling) = ~50KB total
- Feature DataFrames = transient, ~50KB each, GC'd after prediction
- Total impact: ~20MB static + negligible per-tick overhead

## Risks

- **Model quality**: Per-pair models need sufficient training data. Pairs with less history may have weaker models. Mitigated by fallback to rule-based.
- **Tick latency**: Feature calculation adds ~1-5ms per pair per minute. Negligible.
- **False regime signals**: Same risk as single-pair mode. Mitigated by confidence thresholds already in place.
- **Model staleness**: Models trained on old data may not reflect current market dynamics. Mitigated by staleness detection (new) and planned auto-retraining (future).
- **Per-pair ATR divergence**: Volatile pairs (DOGE) have different "normal" ranges than stable pairs (ETH). Mitigated by percentile-based danger override (new) instead of fixed threshold.

## Future: AI Roadmap This Unblocks

This spec is the foundation for deeper AI integration. Once per-pair ML is live:

| Next Step | Description | Dependency on This Spec |
|---|---|---|
| **Confidence-based position sizing** | Scale trend position size by ML confidence (high confidence → larger) | Needs per-pair confidence values |
| **Dynamic grid spacing** | ML model predicts optimal ATR multiplier from market features | Uses same per-pair feature pipeline |
| **AI trend entry scoring** | Replace hardcoded +1/+2 point system with learned entry quality | Per-pair regime gates trend entries |
| **Online model retraining** | Weekly cron retrains per-pair models with fresh data | Needs per-pair model path convention |
| **Model performance tracking** | Log `ml_regime` + `ml_confidence` to trade journals for accuracy analysis | Needs per-pair prediction values |
