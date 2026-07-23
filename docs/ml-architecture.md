# ML Architecture — How It Works & What's Configurable

## 1. Live ML Pipeline (Python → Rust)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        PYTHON CONTAINER                                  │
│                                                                          │
│  ┌──────────────┐    ┌─────────────────────┐    ┌──────────────────┐   │
│  │  Binance API  │───▶│  Feature Engineering │───▶│  RegimeClassifier│   │
│  │  /klines      │    │  (14 features)       │    │  (Random Forest)  │   │
│  │  OHLCV bars   │    │                      │    │  predict_class()  │   │
│  └──────────────┘    │  returns             │    │  predict_proba()  │   │
│       ▲              │  volatility_ratio    │    └────────┬─────────┘   │
│       │              │  normalized_atr      │             │              │
│       │              │  trend_strength      │             ▼              │
│       │              │  rsi_14              │    ┌──────────────────┐   │
│       │              │  volume_ratio        │    │  RegimeManager   │   │
│       │              │  adx_14              │    │  poll_loop(60s)  │   │
│       │              │  macd_histogram      │    │  classify + push │   │
│       │              │  choppiness_index    │    └────────┬─────────┘   │
│       │              │  fractal_dim (Higuchi)│             │              │
│       │              │  aroon_oscillator    │             │              │
│       │              │  obv_roc_14          │             │              │
│       │              │  close_location_val  │             │              │
│       │              └─────────────────────┘             │              │
│       │                                                   │              │
│       │              EVERY 60 SECONDS                     │              │
│       │              ┌──────────────┐                     │              │
│       │              │ Per-pair:    │                     │              │
│       │              │ BTC-USDT.pkl │                     │              │
│       │              │ ETH-USDT.pkl │                     │              │
│       │              │ BNB-USDT.pkl │                     │              │
│       │              │ DOGE-USDT.pkl│                     │              │
│       │              │ XRP-USDT.pkl │                     │              │
│       │              └──────────────┘                     │              │
└───────────────────────────────────────────────────────────┼──────────────┘
                                                            │
                          POST /api/v1/regime               │
                          [{"pair":"BTC-USDT",              │
                            "regime":1,                      │
                            "confidence":0.85}]              │
                                                            ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         RUST CONTAINER                                   │
│                                                                          │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────────────────┐  │
│  │  API Handler  │───▶│ RegimeCache   │───▶│  Engine.tick_strategies() │  │
│  │  POST /regime │    │              │    │                          │  │
│  │               │    │ HashMap:     │    │  for each strategy:      │  │
│  └──────────────┘    │  BTC → (1,   │    │    regime = cache.get()  │  │
│                       │         0.85)│    │         │                │  │
│  ┌──────────────┐    │  ETH → (0,   │    │         ▼                │  │
│  │  File Fallback│    │         0.72)│    │  ┌──────────────────┐   │  │
│  │  data/        │───▶│  ...         │    │  │  TickContext      │   │  │
│  │  regime_cache │    └──────────────┘    │  │  .regime = Some()│   │  │
│  │  .json       │                        │  └────────┬─────────┘   │  │
│  └──────────────┘                         │           │             │  │
│                                           │           ▼             │  │
│                                           │  ┌──────────────────┐  │  │
│                                           │  │  Grid Gate       │  │  │
│                                           │  │  trend=Trending? │  │  │
│                                           │  │  → PAUSED        │  │  │
│                                           │  └──────────────────┘  │  │
│                                           │                        │  │
│                                           │  ┌──────────────────┐  │  │
│                                           │  │  Signal Engine   │  │  │
│                                           │  │  btc_regime_fn() │  │  │
│                                           │  │  → block/gate    │  │  │
│                                           │  └──────────────────┘  │  │
│                                           └──────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

## 2. Model Training Pipeline (Offline)

```
┌─────────────┐     ┌─────────────────────┐     ┌─────────────────┐
│ Binance API  │────▶│ Feature Engineering  │────▶│ Label Generation│
│ 4 timeframes │     │ calculate_technical_  │     │ generate_regime_│
│ 15m/1h/4h/1d│     │ features()           │     │ labels()        │
│ 2000 candles │     │                      │     │                 │
│              │     │ 14 features ────────▶│     │ 0 = RANGING     │
└─────────────┘     │ (same as live path)  │     │ 1 = TRENDING    │
                    └──────────────────────┘     │ 2 = DANGER      │
                                                  └────────┬────────┘
                                                           │
                                                           ▼
                    ┌──────────────────────────────────────────────┐
                    │         RegimeClassifier (Random Forest)      │
                    │                                              │
                    │  Hyperparameter Tuning (RandomizedSearchCV)  │
                    │  TimeSeriesSplit (3 folds)                   │
                    │  20 iterations                               │
                    │                                              │
                    │  Output: models/regime_{PAIR}.pkl            │
                    └──────────────────────────────────────────────┘
```

## 3. How Each Bot Uses ML Regime

```
                    ML OUTPUT (3 classes)
                    ┌───────────────────────┐
                    │ 0 = RANGING (0.72)    │──── ┐
                    │ 1 = TRENDING (0.85)   │     │
                    │ 2 = DANGER (0.60)     │     │
                    └───────────────────────┘     │
                                                  │
            ┌─────────────────────────────────────┘
            │
            ▼
   ┌────────────────────┐          ┌─────────────────────────┐
   │   GRID STRATEGY     │          │    TREND STRATEGY        │
   │                     │          │                          │
   │ ML regime feeds     │          │ ML regime NOT used yet   │
   │ into deploy gate:   │          │ (uses rule-based ADX/    │
   │                     │          │ Choppiness gate instead) │
   │ RANGING → deploy ✅ │          │                          │
   │ TRENDING → pause ⛔ │          │ Gate: ADX>25 AND CHOP<38 │
   │ DANGER → pause  ⛔ │          │ Direction: EMA fast/slow  │
   │                     │          │ Score: MACD+Vol+RSI       │
   │ Also checks:        │          │ Exit: ADX<20 / dir flip  │
   │ ADX < 22            │          │       / ATR trailing      │
   │ CHOP > 55           │          └──────────────────────────┘
   │ NATR 0.005-0.04     │
   └────────────────────┘
```

## 4. Configurable Parameters

### ML Model Parameters (Python)

| Parameter | Location | Default | What It Does |
|-----------|----------|---------|-------------|
| **Model type** | `regime_classifier.py` | `random_forest` | RF or XGBoost |
| **N_estimators** | tuning (200-500) | tuned | Trees in the forest |
| **Max_depth** | tuning (8-15) | tuned | Tree depth limit |
| **Class_weight** | tuning | `balanced` | Handles class imbalance |
| **Calibration** | `calibrate()` | isotonic | Probability calibration |
| **Poll interval** | `runner.py RegimeManager` | `60` seconds | How often to classify |
| **Volume threshold** | feature_engineering | `20` (SMA period) | Volume smoothing |
| **FDI window** | feature_engineering | `30` bars | Higuchi fractal dim |
| **FDI kmax** | feature_engineering | `5` | Higuchi k-max parameter |

### Training Parameters

| Parameter | Location | Default | What It Does |
|-----------|----------|---------|-------------|
| **Forward window** | `train_pipeline.py` | varies by TF | Look-ahead for labeling |
| **Trend threshold** | `train_pipeline.py` | `0.02` (1h) | Min move to count as trend |
| **Trend ATR k** | `train_pipeline.py` | `1.5` (1h) | ATR multiplier for threshold |
| **Timeframes** | `train_pipeline.py` | `15m/1h/4h/1d` | Multi-TF training |
| **Candles** | `train_pipeline.py` | `2000` | Data size per TF |
| **Test split** | `train_pipeline.py` | `80/20` | Train/test split |

### Grid Gate Parameters (Rust)

| Parameter | Location | Default | What It Does |
|-----------|----------|---------|-------------|
| **ADX_RANGE_MAX** | `grid.rs` const | `22.0` | ADX below = no strong trend |
| **CHOP_RANGE_MIN** | `grid.rs` const | `55.0` | Choppiness above = ranging |
| **NATR_FLOOR** | `grid.rs` const | `0.005` | Min volatility for grid |
| **NATR_CEIL** | `grid.rs` const | `0.04` | Max volatility for grid |

### Trend Strategy Parameters (Rust config → YAML)

| Parameter | YAML key | Default | What It Does |
|-----------|----------|---------|-------------|
| **adx_gate_threshold** | `trend.adx_gate_threshold` | `25.0` | ADX > this = trend exists |
| **adx_exit_threshold** | `trend.adx_exit_threshold` | `20.0` | ADX < this = exit |
| **choppiness_threshold** | `trend.choppiness_threshold` | `38.0` | CHOP < this = trending |
| **volume_ratio_threshold** | `trend.volume_ratio_threshold` | `1.2` | Volume must exceed 1.2× avg |
| **rsi_long_max** | `trend.rsi_long_max` | `65.0` | Don't buy above RSI 65 |
| **rsi_short_min** | `trend.rsi_short_min` | `35.0` | Don't sell below RSI 35 |
| **trailing_stop_atr_mult** | `trend.trailing_stop_atr_mult` | `2.0` | Chandelier exit multiplier |
| **ema_fast** | `trend.ema_fast` | `12` | Fast EMA period |
| **ema_slow** | `trend.ema_slow` | `40` | Slow EMA period |

## 5. Feature Importance (Trained Models)

```
All 14 features ranked by importance (averaged across pairs):

  normalized_atr        ████████████  0.10   ← #1 across most pairs
  adx_14                ███████████   0.09   ← Trend strength
  trend_strength        █████████     0.08   ← EMA-based
  volatility_ratio      █████████     0.08   ← Short/long vol ratio
  choppiness_index      █████████     0.08   ← Range vs trend
  distance_to_vwap      ████████      0.07   ← Price vs VWAP
  fractal_dimension_idx ███████       0.07   ← Higuchi FDI (FIXED ✅)
  aroon_oscillator      ███████       0.07   ← Trend direction
  obv_roc_14            ███████       0.07   ← Volume flow
  macd_histogram        ███████       0.07   ← Momentum
  rsi_14                ██████        0.07   ← Overbought/oversold
  volume_ratio          ██████        0.06   ← Participation
  close_location_value  ████          0.04   ← Bar position
  returns               ████          0.04   ← Raw returns
```

## 6. What To Tune & When

```
                TUNING GUIDE
                
  "Grid deploying in trends"     → ADX_RANGE_MAX, CHOP_RANGE_MIN
  "Trend bot entering fakes"     → volume_ratio_threshold (↑ = stricter)
  "Trend bot missing moves"      → adx_gate_threshold (↓ = enter sooner)
  "Trend bot holding too long"   → trailing_stop_atr_mult (↓ = tighter trail)
  "ML confidence too low"        → Retrain with more data / candles
  "ML wrong about regime"        → Check feature engineering, retrain
  "Bot trades in danger zone"    → ML regime feeds grid gate, tune ADX/CHOP
```
