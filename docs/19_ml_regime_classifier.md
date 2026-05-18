# ML Regime Classifier

## Overview

The ML Regime Classifier is a Random Forest model that reads live market data every ~55 seconds and classifies the current market into one of three regimes. These predictions gate both the grid and trend trading engines.

## Three Regimes

| Regime | ID | Meaning | Grid Effect | Trend Effect |
|--------|----|---------|-------------|--------------|
| RANGING | 0 | Price staying flat, low directional movement | Relaxes Bollinger Band re-entry threshold when confident | Blocked if confidence >= 65% |
| TRENDING | 1 | Price moving directionally (up or down) | Relaxes RSI overbought threshold when confident | Allowed if confidence >= 50% |
| DANGER | 2 | Whipsaw / high volatility with no clear direction | Hard pause — both engines stop | Hard block — no trend entries |

## How It Decides

### Input Features (14 indicators)

Every prediction cycle, the model reads 14 technical features from the latest candle:

| Feature | What it measures |
|---------|-----------------|
| `returns` | Price change % |
| `volatility_ratio` | Short-term vs long-term volatility (14/30 period ratio) |
| `normalized_atr` | Average True Range normalized to price |
| `trend_strength` | Directional movement strength |
| `rsi_14` | Relative Strength Index |
| `volume_ratio` | Current volume vs average |
| `close_location_value` | Where close sits within the candle range |
| `adx_14` | Trend strength (any direction) |
| `macd_histogram` | Momentum |
| `distance_to_vwap` | Price distance from VWAP |
| `obv_roc_14` | On-balance volume rate of change |
| `choppiness_index` | Ranging vs trending detection (>61.8 = ranging, <38.2 = trending) |
| `fractal_dimension_index` | Price complexity (~1.0 = trend, ~1.5 = random, ~2.0 = reversal) |
| `aroon_oscillator` | Time since high/low (positive = uptrend, negative = downtrend) |

### Model Architecture

```
RandomForestClassifier
  - n_estimators: 300 (varies by tuning)
  - max_depth: 8-10
  - class_weight: {0:1, 1:1, 2:3} (upweights Danger)
  - max_features: sqrt
```

The model is an ensemble of 300+ decision trees. Each tree votes on the regime. The majority vote determines the predicted regime, and the vote distribution gives the confidence score. A volatility-based Danger override activates when normalized ATR exceeds 6% and price return is near zero.

### Prediction Output

Example for current market conditions:

```
Ranging:  65.2%  ████████████████████░░░░░░░░░░
Trending: 30.1%  █████████░░░░░░░░░░░░░░░░░░░░░
Danger:    4.7%  █░░░░░░░░░░░░░░░░░░░░░░░░░░░░░

Winner: RANGING (regime=0), confidence=0.652
```

- `ml_regime` = the regime with the highest probability
- `ml_confidence` = that regime's probability

## Training Pipeline

### Data Collection

Real OHLCV data is fetched from Binance public API across 4 timeframes:

| Timeframe | Candles | Lookahead | Trend Threshold | ATR Multiplier |
|-----------|---------|-----------|-----------------|----------------|
| 15m | 1000 | 48 periods (12h) | 1.5% | 1.2x |
| 1h | 1000 | 12 periods (12h) | 2.0% | 1.5x |
| 4h | 1000 | 6 periods (24h) | 2.5% | 1.5x |
| 1d | 1000 | 5 periods (5d) | 3.0% | 2.0x |

### Label Generation

Each candle is labeled by looking at **future** price action:

- **RANGING (0):** Future return stays below threshold, no big moves either direction
- **TRENDING (1):** Future return exceeds threshold in either direction
- **DANGER (2):** Both up and down excursions exceed 0.8x threshold, OR high volatility (top 10% ATR) with near-zero net return

The threshold is dynamic — calculated as `k * ATR / close` — so it adapts to current volatility. Danger labels are prioritized over Trending.

### Training Process

1. Fetch 1000 candles per timeframe from Binance
2. Calculate 12 technical features per candle
3. Generate forward-looking regime labels
4. Split: 80% train+val, 20% test (oldest → newest)
5. Hyperparameter tuning: 20 iterations of randomized search with 3-fold CV
6. Evaluate on held-out test set
7. Save model to `models/regime_rf_v3.pkl`

### Current Model Performance

| Metric | Value |
|--------|-------|
| Accuracy | 62% |
| Ranging F1 | 73% |
| Trending F1 | 56% |
| Danger F1 | 23% |

**Top 5 feature importances:**

1. normalized_atr (17.3%)
2. fractal_dimension_index (14.0%)
3. distance_to_vwap (9.2%)
4. trend_strength (9.0%)
5. macd_histogram (7.3%)

## Integration Points

### Grid Engine (`src/grid/grid_state.py`)

```
evaluate(price, rsi, ema_200, bb_lower, bb_upper, ml_regime, ml_confidence)
```

| ML Output | Grid Behavior |
|-----------|---------------|
| Regime 2 (DANGER) | Hard pause — grid and trend both stop |
| Regime 1 (TRENDING, confidence > 85%) | Relaxes RSI overbought from 70 → 75 |
| Regime 0 (RANGING, confidence > 85%) | Widens BB re-entry threshold from 1.02 → 1.05 |
| Any regime, low confidence | No effect — let other rules decide |

**Important:** ML does NOT override the EMA 200 rule. Price must be above EMA 200 for the grid to activate regardless of ML.

### Trend Engine (`ta_grid_trend.py`)

```
_evaluate_trend_signals() → ML gate → score → confirm → buy/sell
```

| ML Output | Trend Entry |
|-----------|-------------|
| Regime 2 (DANGER) | **BLOCKED** — always |
| Regime 1 (TRENDING, confidence < 50%) | **BLOCKED** — uncertain |
| Regime 1 (TRENDING, confidence >= 50%) | **ALLOWED** — ML confirms trend |
| Regime 0 (RANGING, confidence >= 65%) | **BLOCKED** — confident ranging |
| Regime 0 (RANGING, confidence < 65%) | **ALLOWED** — ML uncertain, let signals decide |
| No ML model loaded | **ALLOWED** — fall through to signal score |

### Telegram Status (`/status`)

The `/status` command shows the current ML regime and confidence:

```
🤖 ML: RANGING (65%)
```

## File Structure

```
src/
  ml/
    regime_classifier.py     — RegimeClassifier class (train, predict, save, load)
    train_pipeline.py         — Full training pipeline (fetch, label, tune, evaluate)
  data/
    feature_engineering.py    — calculate_technical_features() — 12 features
    label_generation.py       — generate_regime_labels() — 3-class labeling
  grid/
    grid_state.py             — GridStateMachine.evaluate() — ML + technical rules
  trend/
    trend_manager.py          — TrendManager — signal scoring and confirmation
    position_manager.py       — PositionManager — sizing, SL/TP, trailing stops

hummingbot_files/
  scripts/
    ta_grid_trend.py          — Main strategy — wires ML to both engines

models/
  regime_rf_v3.pkl            — Trained Random Forest model (~18MB)
```

## Retraining

To retrain the model:

```bash
# Using Python 3.13 (required for sklearn/numba compatibility)
.mltrain/bin/python3.13 -m src.ml.train_pipeline

# Single-timeframe training (1h only, use more candles):
.mltrain/bin/python3.13 -m src.ml.train_pipeline --timeframe 1h --candles 2000
```

Or inside the Docker container:

```bash
docker compose exec bot pip install pandas scikit-learn pandas_ta
docker compose exec bot python3 -m src.ml.train_pipeline
```

After retraining, commit and push to trigger CI/CD deployment:

```bash
git add models/regime_rf_v3.pkl
git commit -m "retrain: update ML regime classifier"
git push
```
