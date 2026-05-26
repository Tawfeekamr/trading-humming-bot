# 3rd Engine: ML-Powered Momentum Scalping Engine

## Overview

The Momentum Engine is the 3rd trading engine alongside Grid (engine 1) and Trend (engine 2). It scans all USDT pairs on Binance in real-time, detects coins with sudden price/volume surges, uses ML to filter out pump-and-dump traps, enters on confirmed momentum, and exits fast using ML-predicted optimal timing.

**Timeframe:** 5-minute candles for scanning, 1-minute candles for entry/exit timing
**Capital:** Isolated 5% of total equity (max $500 hard cap)
**Max Positions:** 1 concurrent momentum trade
**Goal:** Catch 3–8% moves on breakout coins, exit before momentum dies

---

## How It Works

```
Every 60 seconds (called from on_tick):
│
├── Model 3 (Filter Tuner) adjusts scanner thresholds using BTC regime + market ATR
│
├── Scanner checks all USDT pairs via WebSocket (!ticker@arr stream)
│   └── Filters: price_change_5m > 3%, RVOL > 3.0×, volume > $5M, spread < 0.15%
│
├── Candidates pass 5 rule-based filters?
│   └── NO → skip
│   └── YES ↓
│
├── Model 1 (Pump Quality Classifier) scores each candidate
│   └── Score < 0.65 → SKIP (likely pump-and-dump)
│   └── Score ≥ 0.65 ↓
│
├── Fetch 5m candles for confirmation:
│   ├── RSI(14) between 55–75
│   ├── Price above VWAP
│   └── Last 3 candles have higher lows
│
├── All confirmed + capital available + risk checks pass?
│   └── ENTER via limit order (best ask + 0.05%)
│
└── While in position (checked every 60s):
    ├── Model 2 (Exit Predictor) scores: "Is momentum dying?"
    │   └── Score ≥ 0.70 → SMART EXIT
    │
    ├── Rule-based fallbacks (always active):
    │   ├── Hard stop-loss: -2% from entry
    │   ├── Trailing stop: 1.5% activation, 1.0% trail
    │   ├── Take profit: +4%
    │   ├── Time stop: 30 minutes
    │   └── Volume collapse: volume < 50% of entry volume
    │
    └── First signal wins → EXIT
```

---

## Three ML Models

### Model 1: Pump Quality Classifier (PQC)

Predicts whether a momentum candidate is a sustainable breakout or a pump-and-dump trap. This is the primary ML gate — no entry happens without PQC approval.

**Model type:** XGBoost (same as existing regime classifier infrastructure)
**Model file:** `models/pump_classifier.pkl`
**Output:** Probability 0.0–1.0 that the pump is sustainable
**Entry threshold:** Score ≥ 0.65 (configurable via `strategy.yaml`)

#### PQC Features (15 inputs)

| # | Feature | Type | How to compute |
|---|---------|------|----------------|
| 1 | `taker_buy_ratio` | float | From 24h ticker: `taker_buy_base_volume / total_volume`. Values >0.6 = real buying demand. Values <0.4 = distribution (whales selling). |
| 2 | `volume_concentration` | float | Gini coefficient of individual trade sizes from recent trades API (`GET /api/v3/trades?symbol=X&limit=100`). High gini = few large whale trades (manipulation). Low gini = broad retail+institutional participation. |
| 3 | `price_acceleration` | float | `(price_change_5m - price_change_15m) / price_change_15m`. Positive = momentum accelerating. Negative = decelerating (pump exhausting). Use 0.0 if `price_change_15m` is 0. |
| 4 | `bid_depth_ratio` | float | `sum(bid_qty within 1% of price) / sum(ask_qty within 1% of price)` from order book snapshot (`GET /api/v3/depth?symbol=X&limit=20`). Values <0.5 = no buy support below (will dump). |
| 5 | `historical_pump_count` | int | Count how many times this symbol had `price_change_5m > 5%` in the last 30 days. Computed from stored 5m candle history. Frequent pumps = likely manipulation. |
| 6 | `btc_correlation_1h` | float | Pearson correlation of this symbol's 1h returns vs BTC's 1h returns over last 24 candles. Low correlation + pump = isolated event (higher risk of reversal). Compute from 1h candle closes. |
| 7 | `spread_velocity` | float | Rate of change of bid-ask spread over last 5 minutes. `(spread_now - spread_5m_ago) / spread_5m_ago`. Widening spread = market makers withdrawing (danger). |
| 8 | `rvol_decay_rate` | float | `(rvol_now - rvol_peak) / minutes_since_peak`. Measures how fast relative volume is dropping from its peak. Fast decay = pump ending. |
| 9 | `market_cap_rank` | int | Rank this symbol by 24h quote volume among all USDT pairs. Top 20 = large cap (safer). Bottom 200 = micro cap (pump-and-dump territory). Normalize to 0–1 range. |
| 10 | `time_of_day_sin` | float | `sin(2π × hour_utc / 24)`. Cyclical encoding of UTC hour. Pumps at 3am UTC (low global liquidity) are more likely manipulation than pumps at 15:00 UTC. |
| 11 | `time_of_day_cos` | float | `cos(2π × hour_utc / 24)`. Paired with sin for full cyclical encoding. |
| 12 | `candle_body_ratio` | float | `abs(close - open) / (high - low + 1e-8)` of the last completed 5m candle. Values near 1.0 = strong directional candle. Values near 0.0 = doji/rejection (long wicks, no conviction). |
| 13 | `rsi_5m` | float | RSI computed on 5-minute closes using Wilder's smoothing (same as `src/indicators/rsi.py` but on 5m data). Values >80 = extremely overbought. |
| 14 | `obv_divergence` | float | Compute OBV slope (linear regression over last 10 candles) and price slope separately. `obv_slope - price_slope`. Negative = volume not confirming price (bearish divergence). |
| 15 | `returns_skewness_20` | float | `scipy.stats.skew(returns[-20:])` or manual computation on last 20 five-minute returns. High positive skew = one massive candle created the move (unsustainable). |

#### PQC Training Data Generation

Training labels are generated from historical 5-minute candle data by identifying pump events and labeling their outcomes:

```python
# File: src/momentum/ml/pump_label_generator.py

def generate_pump_labels(df_5m: pd.DataFrame) -> pd.DataFrame:
    """
    Scan 5m candle data for pump events. Label by forward outcome.
    
    Step 1: Identify pump events
      - price_change_5m = (close - close.shift(1)) / close.shift(1)
      - volume_ratio = volume / volume.rolling(60).mean()  # 60 candles = ~5 hours
      - Pump event: price_change_5m > 0.03 AND volume_ratio > 2.0
    
    Step 2: Look forward 6 candles (30 minutes) from each pump event
      - Compute max_drawdown = (entry_price - forward_low) / entry_price
      - Compute max_gain = (forward_high - entry_price) / entry_price
    
    Step 3: Label
      - GOOD PUMP (1): max_drawdown < 0.02 (never dropped >2% below entry within 30min)
      - BAD PUMP (0): max_drawdown >= 0.02 (dropped >2% = trap)
    
    Step 4: Compute all 15 PQC features at each pump event row
    
    Returns DataFrame with features + 'pump_label' column.
    """
```

**Training pipeline:**

```bash
# Fetch 6 months of 5m data for top 50 USDT pairs, generate labels, train XGBoost
python -m src.momentum.ml.train_momentum --model pqc --pairs 50 --candles 5000

# Expected output:
#   Fetched 50 pairs × 5000 candles = 250,000 rows
#   Pump events found: ~3,200
#   Label distribution: 35% good, 65% bad
#   Test set AUC: >0.65, F1: >0.60
```

**Data fetching:** Use the same `load_real_data()` function from `src/ml/train_pipeline.py` but request `5m` interval instead of `1h`. Fetch from Binance public API (`api.binance.com/api/v3/klines`). Apply the same rate limit handling (0.5s sleep, 429 retry).

**Train/test split:** 80/20 chronological (oldest → newest). Use `TimeSeriesSplit(n_splits=3)` for cross-validation during hyperparameter tuning, same as existing `train_pipeline.py`.

**Hyperparameter tuning:** Use `RandomizedSearchCV` with XGBoost, same approach as `RegimeClassifier.tune_hyperparameters()`:

```python
param_distributions = {
    'n_estimators': [200, 300, 500],
    'max_depth': [4, 6, 8],
    'learning_rate': [0.01, 0.05, 0.1],
    'subsample': [0.7, 0.8, 0.9],
    'colsample_bytree': [0.7, 0.8, 0.9],
    'scale_pos_weight': [1.5, 2.0, 3.0],  # Handle class imbalance (more bad pumps than good)
}
```

**Save format:** Same pickle format as `RegimeClassifier.save_model()`:
```python
data = {
    'model': trained_model,
    'model_type': 'xgboost',
    'version': 1,
    'feature_cols': PumpClassifier.FEATURES,
}
```

---

### Model 2: Optimal Exit Predictor (OEP)

Runs every 60 seconds while a momentum position is open. Predicts whether momentum is about to die so the bot can exit before the trailing stop triggers (which means giving back some profit).

**Model type:** LightGBM (fast inference for frequent polling)
**Model file:** `models/exit_predictor.pkl`
**Output:** Probability 0.0–1.0 that now is the right time to exit
**Exit threshold:** Score ≥ 0.70

#### OEP Features (8 inputs)

| # | Feature | How to compute |
|---|---------|----------------|
| 1 | `rvol_since_entry` | `current_rvol / rvol_at_entry`. Stored from PQC evaluation at entry. Values <0.5 = volume dying. |
| 2 | `taker_ratio_delta` | `current_taker_buy_ratio - taker_buy_ratio_at_entry`. Negative = sellers taking over. |
| 3 | `unrealized_pnl_pct` | `(current_price - entry_price) / entry_price * 100`. Positive = in profit. |
| 4 | `time_in_position_min` | `(now - entry_time).total_seconds() / 60`. Minutes since entry. |
| 5 | `price_vs_vwap_delta` | `(current_price - current_vwap) / current_vwap - (entry_price - entry_vwap) / entry_vwap`. How price relationship to VWAP has changed. |
| 6 | `momentum_slope_3m` | Linear regression slope of last 3 price points (1-minute intervals). `np.polyfit(x=[0,1,2], y=last_3_prices, deg=1)[0] / last_3_prices[0]`. Negative = momentum fading. |
| 7 | `consecutive_red_candles` | Count of consecutive bearish 1-minute candles (close < open). Reset on any green candle. |
| 8 | `rsi_1m_delta` | `rsi_1m_now - rsi_1m_3min_ago`. RSI computed on 1-minute closes. Falling RSI = momentum weakening. |

#### OEP Training Data Generation

```python
# File: src/momentum/ml/exit_label_generator.py (inside train_momentum.py)

# Step 1: Use PQC to identify all historical entries that PQC would have approved
# Step 2: For each entry, track 1-minute candles for 30 minutes
# Step 3: Find the optimal exit point = the price peak before any >1% drawdown
# Step 4: At each 60-second checkpoint during the position:
#   - Label 1 (EXIT) if optimal exit is within the next 2 minutes
#   - Label 0 (HOLD) if optimal exit is >2 minutes away
# Step 5: Compute all 8 OEP features at each checkpoint
```

**Dependency:** Requires PQC to be trained first (uses PQC to select which historical events to use for training).

---

### Model 3: Dynamic Filter Tuner (DFT)

Adjusts scanner thresholds based on current market conditions. Uses the existing BTC regime classifier output + market-wide ATR.

**Phase 1 implementation: Rule-based lookup table (no ML model needed initially)**

```python
# File: src/momentum/ml/filter_tuner.py

class FilterTuner:
    """Adjusts momentum scanner thresholds based on market regime."""

    # Rule-based thresholds — upgrade to ML model after 100+ trades
    REGIME_THRESHOLDS = {
        # btc_regime: (min_price_change_5m, min_rvol, pqc_threshold, trailing_stop_pct, max_daily_gain)
        "RANGING":  (3.0, 3.0, 0.65, 1.0, 30.0),
        "TRENDING": (5.0, 4.0, 0.70, 1.5, 20.0),
        "DANGER":   (None, None, None, None, None),  # Engine paused
    }

    def get_adjusted_thresholds(self, btc_regime: str, btc_confidence: float,
                                 market_atr_pct: float) -> dict:
        """
        Args:
            btc_regime: "RANGING", "TRENDING", or "DANGER" from existing RegimeClassifier
            btc_confidence: 0.0-1.0 from existing RegimeClassifier
            market_atr_pct: Average normalized ATR across BTC+ETH+BNB (from existing ATR indicators)
        
        Returns dict with adjusted threshold values, or None if engine should pause.
        """
        if btc_regime == "DANGER":
            return None  # Pause momentum engine

        base = self.REGIME_THRESHOLDS.get(btc_regime, self.REGIME_THRESHOLDS["RANGING"])
        
        # Interpolate based on market volatility
        # Higher ATR → raise thresholds (be pickier)
        vol_factor = max(0.8, min(1.5, market_atr_pct / 0.02))  # Normalize around 2% ATR
        
        return {
            'min_price_change_5m': base[0] * vol_factor,
            'min_rvol': base[1] * vol_factor,
            'pqc_threshold': min(0.85, base[2] + (vol_factor - 1) * 0.1),
            'trailing_stop_pct': base[3] * vol_factor,
            'max_daily_gain_pct': base[4] / vol_factor,
        }
```

**Phase 2 (after 100+ momentum trades):** Train a model that learns which thresholds produced the best risk-adjusted returns. Use momentum journal data as training signal.

---

## Market Scanner

### File: `src/momentum/scanner.py`

Connects to Binance's `!ticker@arr` WebSocket stream — a single connection that receives real-time 24h ticker updates for every trading pair on the exchange (~600 pairs).

#### WebSocket Connection

```python
class MarketScanner:
    """Scans all USDT pairs for momentum candidates."""

    WS_URL = "wss://stream.binance.com:9443/ws/!ticker@arr"
    WS_URL_TESTNET = "wss://testnet.binance.vision/ws/!ticker@arr"

    # Exclusion patterns — never trade these
    EXCLUDED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BEARUSDT", "BULLUSDT")
    EXCLUDED_BASES = ("USDC", "TUSD", "BUSD", "FDUSD", "DAI", "USDP", "EUR", "GBP", "TRY", "BRL")

    def __init__(self, testnet: bool = False, min_volume_24h: float = 5_000_000):
        self._testnet = testnet
        self._min_volume_24h = min_volume_24h
        self._running = False
        
        # Rolling price/volume snapshots for computing 5m and 15m changes
        # Key: symbol, Value: deque of (timestamp, price, volume) tuples
        self._snapshots: Dict[str, deque] = {}
        self._snapshot_max_age = 900  # Keep 15 minutes of snapshots
        
        # Latest ticker data per symbol
        self._tickers: Dict[str, dict] = {}
        
        # Cached symbol list for new listing detection
        self._known_symbols: set = set()
        self._new_listings: Dict[str, float] = {}  # symbol -> detection_timestamp
        
        # Historical volume averages (for RVOL computation)
        self._avg_volumes: Dict[str, float] = {}  # symbol -> 14-day avg hourly volume
```

#### WebSocket Message Handling

Each message from `!ticker@arr` is an array of ticker objects. Parse relevant fields:

```python
# From Binance 24hr ticker:
# s = symbol, c = last price, P = price change percent 24h,
# v = base volume, q = quote volume, Q = taker buy quote volume,
# b = best bid price, a = best ask price
# B = best bid qty, A = best ask qty

def _process_ticker(self, data: list[dict]):
    for ticker in data:
        symbol = ticker.get("s", "")
        
        # Filter: only USDT pairs, exclude leverage/stablecoins
        if not symbol.endswith("USDT"):
            continue
        if any(symbol.endswith(s) for s in self.EXCLUDED_SUFFIXES):
            continue
        base = symbol.replace("USDT", "")
        if base in self.EXCLUDED_BASES:
            continue
            
        quote_volume = float(ticker.get("q", 0))
        if quote_volume < self._min_volume_24h:
            continue
        
        price = float(ticker.get("c", 0))
        if price <= 0:
            continue
        
        # Store ticker
        self._tickers[symbol] = {
            "price": price,
            "price_change_24h": float(ticker.get("P", 0)),
            "volume_24h": quote_volume,
            "taker_buy_volume": float(ticker.get("Q", 0)),
            "best_bid": float(ticker.get("b", 0)),
            "best_ask": float(ticker.get("a", 0)),
            "best_bid_qty": float(ticker.get("B", 0)),
            "best_ask_qty": float(ticker.get("A", 0)),
        }
        
        # Store snapshot for 5m/15m change computation
        now = time.time()
        if symbol not in self._snapshots:
            self._snapshots[symbol] = deque(maxlen=300)  # ~5 min at 1/sec
        self._snapshots[symbol].append((now, price, quote_volume))
        
        # Prune old snapshots
        while self._snapshots[symbol] and (now - self._snapshots[symbol][0][0]) > self._snapshot_max_age:
            self._snapshots[symbol].popleft()
```

#### Scanning Logic

```python
def scan(self) -> list[ScanResult]:
    """Returns list of momentum candidates, sorted by momentum_score descending."""
    results = []
    now = time.time()
    
    for symbol, ticker in self._tickers.items():
        snapshots = self._snapshots.get(symbol, deque())
        if len(snapshots) < 10:  # Need at least ~10 seconds of data
            continue
        
        # Compute 5-minute price change
        price_now = ticker["price"]
        price_5m_ago = self._get_price_at(snapshots, now - 300)
        price_15m_ago = self._get_price_at(snapshots, now - 900)
        
        if price_5m_ago is None or price_5m_ago <= 0:
            continue
            
        price_change_5m = (price_now - price_5m_ago) / price_5m_ago * 100
        price_change_15m = ((price_now - price_15m_ago) / price_15m_ago * 100) if price_15m_ago else 0
        
        # Compute RVOL
        avg_vol = self._avg_volumes.get(symbol, 0)
        rvol = (ticker["volume_24h"] / 24) / avg_vol if avg_vol > 0 else 1.0
        
        # Compute spread
        bid = ticker["best_bid"]
        ask = ticker["best_ask"]
        spread_pct = ((ask - bid) / bid * 100) if bid > 0 else 999
        
        # Taker buy ratio
        total_vol = ticker["volume_24h"]
        taker_buy_ratio = ticker["taker_buy_volume"] / total_vol if total_vol > 0 else 0.5
        
        # Momentum score (composite, 0-10)
        # Weighted: price_acceleration (30%), RVOL (30%), taker_ratio (20%), spread_tightness (20%)
        accel = (price_change_5m - price_change_15m / 3) if price_change_15m != 0 else price_change_5m
        score = (
            min(accel / 2, 3.0) +                          # Max 3 points from acceleration
            min(rvol / 2, 3.0) +                            # Max 3 points from RVOL
            min(max(taker_buy_ratio - 0.4, 0) * 5, 2.0) +  # Max 2 points from taker ratio
            min(max(0.2 - spread_pct, 0) * 10, 2.0)        # Max 2 points from tight spread
        )
        
        # Check if new listing
        is_new = symbol in self._new_listings
        
        results.append(ScanResult(
            symbol=symbol,
            price=price_now,
            price_change_5m=round(price_change_5m, 2),
            price_change_15m=round(price_change_15m, 2),
            rvol=round(rvol, 2),
            spread_pct=round(spread_pct, 4),
            volume_24h_usdt=round(ticker["volume_24h"], 0),
            taker_buy_ratio=round(taker_buy_ratio, 3),
            momentum_score=round(score, 2),
            is_new_listing=is_new,
        ))
    
    # Sort by momentum score descending
    results.sort(key=lambda r: r.momentum_score, reverse=True)
    return results
```

#### RVOL Baseline Computation

On startup (and refreshed daily), fetch 14-day average hourly volume for all active symbols:

```python
def _compute_avg_volumes(self):
    """Fetch 14-day average hourly volume for RVOL computation.
    
    Uses GET /api/v3/klines with interval=1h, limit=336 (14 days × 24 hours).
    Only fetch for symbols already in self._tickers (active USDT pairs).
    Rate limit: 0.5s between requests.
    """
    for symbol in list(self._tickers.keys())[:100]:  # Top 100 by volume
        try:
            klines = fetch_klines(symbol, interval="1h", limit=336)
            volumes = [float(k[5]) for k in klines]  # index 5 = volume
            self._avg_volumes[symbol] = sum(volumes) / len(volumes) if volumes else 0
            time.sleep(0.5)
        except Exception:
            pass
```

#### New Listing Detection

```python
def check_new_listings(self):
    """Compare current exchangeInfo symbols against cached list.
    
    Call every 5 minutes. Uses GET /api/v3/exchangeInfo.
    New symbols = current_set - cached_set.
    """
    try:
        info = requests.get("https://api.binance.com/api/v3/exchangeInfo", timeout=10).json()
        current_symbols = {
            s["symbol"] for s in info["symbols"]
            if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
        }
        
        if self._known_symbols:  # Skip first run (baseline)
            new = current_symbols - self._known_symbols
            for symbol in new:
                self._new_listings[symbol] = time.time()
                logger.info(f"NEW LISTING DETECTED: {symbol}")
        
        self._known_symbols = current_symbols
        
        # Clean up old listings (>2 hours)
        cutoff = time.time() - 7200
        self._new_listings = {s: t for s, t in self._new_listings.items() if t > cutoff}
    except Exception as e:
        logger.warning(f"exchangeInfo check failed: {e}")
```

#### Scanner Lifecycle

The scanner runs in a background thread (same pattern as `WebSocketFeed` in `src/data/ws_feed.py`):

```python
async def start(self):
    """Connect to !ticker@arr and process messages. Runs in background thread."""
    # Same reconnection logic as existing WebSocketFeed:
    # - Exponential backoff on disconnect
    # - Max 50 retries
    # - Reset retry counter on successful connection
    
async def stop(self):
    self._running = False
```

---

## Momentum Filters

### File: `src/momentum/momentum_filters.py`

Five-filter gate with dynamic thresholds from Model 3 (FilterTuner):

```python
@dataclass
class FilterConfig:
    min_price_change_5m: float = 3.0
    min_rvol: float = 3.0
    min_volume_24h: float = 5_000_000
    max_daily_gain_pct: float = 30.0
    max_spread_pct: float = 0.15


class MomentumFilters:
    """Five-filter gate for momentum candidates."""

    def __init__(self, config: FilterConfig = None):
        self._config = config or FilterConfig()

    def update_thresholds(self, adjusted: dict):
        """Called by FilterTuner to dynamically adjust thresholds."""
        if adjusted is None:
            return  # Engine paused by DFT
        self._config.min_price_change_5m = adjusted.get('min_price_change_5m', self._config.min_price_change_5m)
        self._config.min_rvol = adjusted.get('min_rvol', self._config.min_rvol)
        self._config.max_daily_gain_pct = adjusted.get('max_daily_gain_pct', self._config.max_daily_gain_pct)

    def filter(self, candidate: ScanResult) -> tuple[bool, str]:
        """
        Returns (passed: bool, reject_reason: str).
        Reject reason is empty string if passed.
        """
        if candidate.price_change_5m < self._config.min_price_change_5m:
            return False, f"price_change_5m {candidate.price_change_5m}% < {self._config.min_price_change_5m}%"
        
        if candidate.rvol < self._config.min_rvol:
            return False, f"rvol {candidate.rvol}x < {self._config.min_rvol}x"
        
        if candidate.volume_24h_usdt < self._config.min_volume_24h:
            return False, f"volume ${candidate.volume_24h_usdt:,.0f} < ${self._config.min_volume_24h:,.0f}"
        
        # Daily gain cap — skip coins already up too much today
        if candidate.price_change_5m > 0 and candidate.price_change_15m > self._config.max_daily_gain_pct:
            return False, f"daily_gain {candidate.price_change_15m}% > {self._config.max_daily_gain_pct}%"
        
        if candidate.spread_pct > self._config.max_spread_pct:
            return False, f"spread {candidate.spread_pct}% > {self._config.max_spread_pct}%"
        
        return True, ""
```

#### 5-Minute Candle Confirmation

After a candidate passes the 5 filters AND the PQC ML model, fetch 5m candles to confirm the setup:

```python
def confirm_with_candles(self, candles_5m: pd.DataFrame, current_price: float) -> tuple[bool, str]:
    """
    Fetch 50 five-minute candles for the candidate and check:
    1. RSI(14) between 55–75 (momentum present but not overbought)
    2. Price above VWAP (buying pressure)
    3. Last 3 candles have higher lows (uptrend structure)
    
    Uses existing RSI class from src/indicators/rsi.py (instantiate with period=14).
    VWAP: typical_price × volume cumulative sum / cumulative volume (same as feature_engineering.py line 57).
    """
    closes = candles_5m["close"]
    
    # RSI check
    rsi = RSI(period=14)
    rsi_value = rsi.calculate(closes)
    if rsi_value is None or not (55 <= rsi_value <= 75):
        return False, f"RSI {rsi_value:.1f} not in [55, 75]"
    
    # VWAP check
    typical_price = (candles_5m["high"] + candles_5m["low"] + candles_5m["close"]) / 3
    vwap = (typical_price * candles_5m["volume"]).cumsum() / candles_5m["volume"].cumsum()
    if current_price < vwap.iloc[-1]:
        return False, f"Price {current_price} below VWAP {vwap.iloc[-1]:.4f}"
    
    # Higher lows check (last 3 candles)
    lows = candles_5m["low"].tail(3).values
    if len(lows) >= 3 and not (lows[1] > lows[0] and lows[2] > lows[1]):
        return False, f"No higher lows: {lows}"
    
    return True, ""
```

---

## Momentum Engine

### File: `src/momentum/momentum_engine.py`

The main orchestrator. Called from `ta_grid_trend.py`'s `on_tick()`.

```python
class MomentumEngineState(Enum):
    SCANNING = "SCANNING"
    CONFIRMING = "CONFIRMING"
    ML_SCORING = "ML_SCORING"
    ENTERING = "ENTERING"
    IN_POSITION = "IN_POSITION"
    EXITING = "EXITING"
    COOLDOWN = "COOLDOWN"
    PAUSED = "PAUSED"


class MomentumEngine:
    def __init__(self, config: dict, capital_manager: CapitalManager,
                 btc_regime_fn: callable, telegram: TelegramBot):
        """
        Args:
            config: momentum section from strategy.yaml
            capital_manager: existing CapitalManager instance
            btc_regime_fn: callable that returns (regime_str, confidence, danger_prob)
                           from existing ML predictions dict
            telegram: existing TelegramBot instance for alerts
        """
        self._config = config
        self._capital_mgr = capital_manager
        self._get_btc_regime = btc_regime_fn
        self._telegram = telegram
        
        self._state = MomentumEngineState.SCANNING
        self._enabled = config.get("enabled", False)
        self._manual_pause = False
        
        # Sub-components
        self._scanner = MarketScanner(
            testnet=os.environ.get("ENV") == "paper",
            min_volume_24h=config.get("min_volume_24h_usdt", 5_000_000),
        )
        self._filters = MomentumFilters(FilterConfig(
            min_price_change_5m=config.get("min_price_change_5m", 3.0),
            min_rvol=config.get("min_rvol", 3.0),
            min_volume_24h=config.get("min_volume_24h_usdt", 5_000_000),
            max_daily_gain_pct=config.get("max_daily_gain_pct", 30.0),
            max_spread_pct=config.get("max_spread_pct", 0.15),
        ))
        self._risk = MomentumRiskGuard(config)
        self._position_mgr = MomentumPositionManager(config)
        self._journal = MomentumJournal()
        
        # ML models (loaded with hot-reload support)
        self._pqc = None  # PumpClassifier
        self._oep = None  # ExitPredictor
        self._dft = FilterTuner()
        self._load_ml_models(config.get("ml", {}))
        
        # Timing
        self._last_scan_time = 0
        self._scan_interval = config.get("scan_interval_sec", 60)
        self._cooldown_until = 0
        
        # State
        self._current_candidate = None  # ScanResult being evaluated
        self._last_scan_results = []    # For /momentum_status command
        
    def tick(self, connector):
        """Called from on_tick(). Handles the full momentum lifecycle."""
        if not self._enabled or self._manual_pause:
            return
        
        now = time.time()
        
        # Check BTC correlation gate
        btc_regime, btc_conf, _ = self._get_btc_regime()
        if btc_regime == "DANGER" and self._config.get("use_btc_correlation_gate", True):
            if self._state == MomentumEngineState.IN_POSITION:
                # Force exit if in position during BTC DANGER
                self._force_exit(connector, reason="btc_danger")
            self._state = MomentumEngineState.PAUSED
            return
        
        # Update dynamic thresholds from DFT
        market_atr = self._compute_market_atr()
        adjusted = self._dft.get_adjusted_thresholds(btc_regime or "RANGING", btc_conf, market_atr)
        if adjusted is None:
            self._state = MomentumEngineState.PAUSED
            return
        self._filters.update_thresholds(adjusted)
        
        # State machine
        if self._state == MomentumEngineState.COOLDOWN:
            if now >= self._cooldown_until:
                self._state = MomentumEngineState.SCANNING
            return
        
        if self._state == MomentumEngineState.IN_POSITION:
            self._manage_position(connector, now)
            return
        
        if self._state in (MomentumEngineState.SCANNING, MomentumEngineState.PAUSED):
            if now - self._last_scan_time < self._scan_interval:
                return
            self._last_scan_time = now
            self._run_scan(connector)
```

#### Scan → Filter → ML → Enter Flow

```python
    def _run_scan(self, connector):
        """Full scan cycle: scan → filter → ML score → confirm → enter."""
        # Risk pre-checks
        if not self._risk.can_trade():
            return
        if self._position_mgr.has_open_position():
            return
        
        # Scan
        candidates = self._scanner.scan()
        self._last_scan_results = candidates[:10]  # Store for /momentum_status
        
        for candidate in candidates[:5]:  # Evaluate top 5 only
            # Filter gate
            passed, reason = self._filters.filter(candidate)
            if not passed:
                continue
            
            # ML gate (PQC)
            if self._pqc is not None:
                pqc_features = self._compute_pqc_features(candidate)
                pqc_score = self._pqc.predict_quality(pqc_features)
                threshold = self._dft_adjusted.get('pqc_threshold', 0.65) if hasattr(self, '_dft_adjusted') else 0.65
                if pqc_score < threshold:
                    logger.info(f"MOMENTUM: {candidate.symbol} rejected by PQC: {pqc_score:.3f} < {threshold}")
                    continue
            else:
                pqc_score = 0.0  # No ML model, skip ML gate
            
            # Candle confirmation
            try:
                # Convert Binance symbol to our format for CandleFeed
                feed = CandleFeed(symbol=candidate.symbol, interval="5m", testnet=False)
                candles = feed.fetch_candles(limit=50)
                if candles.empty:
                    continue
                    
                confirmed, reason = self._filters.confirm_with_candles(candles, candidate.price)
                if not confirmed:
                    continue
            except Exception as e:
                logger.warning(f"MOMENTUM: Candle confirmation failed for {candidate.symbol}: {e}")
                continue
            
            # Capital allocation
            budget = self._get_momentum_budget()
            if budget <= 0:
                return
            
            allocated = self._capital_mgr.allocate(candidate.symbol, "momentum", budget)
            if not allocated:
                continue
            
            # ENTER
            self._enter_position(connector, candidate, pqc_score, budget, candles)
            return  # Only enter one position per scan
```

#### Position Management with ML Exit

```python
    def _manage_position(self, connector, now: float):
        """Check all exit conditions while in a momentum position."""
        pos = self._position_mgr.get_open_position()
        if pos is None:
            self._state = MomentumEngineState.SCANNING
            return
        
        current_price = self._get_current_price(connector, pos.symbol)
        if current_price <= 0:
            return
        
        # Update trailing stop
        self._position_mgr.update_trailing(pos, current_price)
        
        # Priority 1: Hard stop-loss (non-negotiable)
        if current_price <= pos.stop_loss:
            self._exit_position(connector, pos, current_price, "hard_stop")
            return
        
        # Priority 2: ML exit (OEP)
        if self._oep is not None and (now - pos.last_oep_check) >= 60:
            pos.last_oep_check = now
            oep_features = self._compute_oep_features(pos, current_price)
            oep_score = self._oep.predict_exit_probability(oep_features)
            if oep_score >= self._config.get("ml", {}).get("oep_threshold", 0.70):
                self._exit_position(connector, pos, current_price, "ml_exit", oep_score=oep_score)
                return
        
        # Priority 3: Take profit
        if current_price >= pos.take_profit:
            self._exit_position(connector, pos, current_price, "take_profit")
            return
        
        # Priority 4: Trailing stop
        if pos.trailing_activated and current_price <= pos.trailing_stop:
            self._exit_position(connector, pos, current_price, "trailing_stop")
            return
        
        # Priority 5: Time stop
        elapsed_min = (now - pos.entry_timestamp) / 60
        if elapsed_min >= self._config.get("time_stop_minutes", 30):
            self._exit_position(connector, pos, current_price, "time_stop")
            return
        
        # Priority 6: Volume collapse
        # Compare current volume to entry volume (from scanner snapshot)
        current_rvol = self._scanner.get_current_rvol(pos.symbol)
        if current_rvol and pos.entry_rvol > 0:
            vol_ratio = current_rvol / pos.entry_rvol
            if vol_ratio < self._config.get("volume_collapse_threshold", 0.5):
                self._exit_position(connector, pos, current_price, "volume_collapse")
                return
```

---

## Momentum Position Manager

### File: `src/momentum/momentum_position.py`

Follows the same pattern as `src/trend/position_manager.py` but with momentum-specific features:

```python
@dataclass
class MomentumPosition:
    symbol: str
    entry_order_id: str
    entry_price: float
    amount: float
    stop_loss: float            # Hard stop: entry × (1 - hard_stop_loss_pct)
    take_profit: float          # entry × (1 + take_profit_pct)
    trailing_stop: float        # Starts at stop_loss, ratchets up
    trailing_activated: bool = False
    highest_price: float = 0.0
    entry_timestamp: float = 0.0
    entry_rvol: float = 0.0
    entry_taker_ratio: float = 0.0
    pqc_score: float = 0.0
    last_oep_check: float = 0.0
    exit_order_id: str = ""
    exit_reason: str = ""
    is_closed: bool = False


class MomentumPositionManager:
    def __init__(self, config: dict):
        self._max_positions = config.get("max_positions", 1)
        self._trailing_stop_pct = config.get("trailing_stop_pct", 1.0) / 100
        self._trailing_activation_pct = config.get("trailing_activation_pct", 1.5) / 100
        self._hard_stop_pct = config.get("hard_stop_loss_pct", 2.0) / 100
        self._take_profit_pct = config.get("take_profit_pct", 4.0) / 100
        self._position: Optional[MomentumPosition] = None
    
    def has_open_position(self) -> bool:
        return self._position is not None and not self._position.is_closed
    
    def open_position(self, symbol: str, entry_order_id: str, entry_price: float,
                      amount: float, entry_rvol: float, entry_taker_ratio: float,
                      pqc_score: float) -> MomentumPosition:
        stop_loss = round(entry_price * (1 - self._hard_stop_pct), 8)
        take_profit = round(entry_price * (1 + self._take_profit_pct), 8)
        self._position = MomentumPosition(
            symbol=symbol,
            entry_order_id=entry_order_id,
            entry_price=entry_price,
            amount=amount,
            stop_loss=stop_loss,
            take_profit=take_profit,
            trailing_stop=stop_loss,
            highest_price=entry_price,
            entry_timestamp=time.time(),
            entry_rvol=entry_rvol,
            entry_taker_ratio=entry_taker_ratio,
            pqc_score=pqc_score,
        )
        return self._position
    
    def update_trailing(self, pos: MomentumPosition, current_price: float):
        """Same logic as trend PositionManager.update_trailing()."""
        if current_price > pos.highest_price:
            pos.highest_price = current_price
        if not pos.trailing_activated:
            activation_price = pos.entry_price * (1 + self._trailing_activation_pct)
            if current_price >= activation_price:
                pos.trailing_activated = True
        if pos.trailing_activated:
            new_trail = current_price * (1 - self._trailing_stop_pct)
            if new_trail > pos.trailing_stop:
                pos.trailing_stop = round(new_trail, 8)
```

---

## Momentum Risk Guard

### File: `src/momentum/momentum_risk.py`

Eight-layer risk management, isolated from grid/trend:

```python
class MomentumRiskGuard:
    def __init__(self, config: dict):
        self._max_trades_per_day = config.get("max_trades_per_day", 5)
        self._daily_loss_limit_pct = config.get("daily_loss_limit_pct", 3.0)
        self._cooldown_minutes = config.get("cooldown_minutes", 5)
        self._capital_pct = config.get("capital_pct", 5.0)
        self._max_capital = config.get("max_capital_usdt", 500)
        
        self._trades_today = 0
        self._daily_pnl = 0.0
        self._daily_budget = 0.0
        self._last_trade_time = 0
        self._halted = False
        self._last_reset_date = ""
    
    def can_trade(self) -> bool:
        """Check all risk gates. Returns False if any gate blocks."""
        self._maybe_reset_daily()
        
        if self._halted:
            return False
        
        # Gate 5: Max trades per day
        if self._trades_today >= self._max_trades_per_day:
            return False
        
        # Gate 4: Daily loss limit
        if self._daily_budget > 0 and self._daily_pnl <= -(self._daily_budget * self._daily_loss_limit_pct / 100):
            self._halted = True
            return False
        
        # Gate 6: Cooldown timer
        if time.time() - self._last_trade_time < self._cooldown_minutes * 60:
            return False
        
        return True
    
    def record_trade(self, pnl: float):
        self._trades_today += 1
        self._daily_pnl += pnl
        self._last_trade_time = time.time()
    
    def get_budget(self, total_equity: float) -> float:
        """Calculate momentum budget: min(capital_pct × equity, max_capital)."""
        budget = total_equity * self._capital_pct / 100
        budget = min(budget, self._max_capital)
        self._daily_budget = budget
        return budget
    
    def _maybe_reset_daily(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._last_reset_date:
            self._trades_today = 0
            self._daily_pnl = 0.0
            self._halted = False
            self._last_reset_date = today
```

---

## Momentum Journal

### File: `src/momentum/momentum_journal.py`

SQLite journal in `data/momentum_journal.db`. Follows the exact same pattern as `src/journal/trade_journal.py`:

```python
@dataclass
class MomentumTrade:
    timestamp: str                # ISO format
    symbol: str                   # "PEPEUSDT"
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fee: float
    net_pnl: float
    hold_duration_min: int
    exit_reason: str              # "ml_exit" | "trailing_stop" | "hard_stop" | "take_profit" | "time_stop" | "volume_collapse" | "btc_danger"
    entry_rvol: float
    entry_momentum_score: float
    entry_rsi: float
    entry_price_change_5m: float
    pqc_score: float
    oep_score_at_exit: float      # 0.0 if not ML exit
    dft_thresholds: str           # JSON string of active thresholds at entry
    is_new_listing: bool


class MomentumJournal:
    DB_PATH = Path("data/momentum_journal.db")
    
    # Same init pattern as TradeJournal:
    # - sqlite3 with WAL mode
    # - Thread lock
    # - CREATE TABLE IF NOT EXISTS
    
    # Methods to implement (same signatures as TradeJournal):
    # - log_trade(trade: MomentumTrade) -> int
    # - get_trades(since, until) -> list[dict]
    # - summary_today() -> dict
    # - summary_this_week() -> dict
    # - summary_this_month() -> dict
    # - summary_all_time() -> dict
    
    # Additional methods:
    # - ml_hit_rate() -> dict  # % of trades where PQC score > 0.65 were profitable
    # - exit_reason_breakdown() -> dict  # Count by exit reason
    # - avg_pqc_score_by_outcome() -> dict  # Avg PQC score for winning vs losing trades
```

---

## Integration with Main Strategy

### File: `hummingbot_files/scripts/ta_grid_trend.py`

#### Changes to `__init__()` (after line ~540, after CapitalManager init):

```python
# ── Momentum Engine ──
momentum_cfg = cfg.get("momentum", {})
self._momentum_engine = None
if momentum_cfg.get("enabled", False):
    from src.momentum.momentum_engine import MomentumEngine
    
    def _get_btc_regime():
        """Returns (regime_str, confidence, danger_prob) from existing ML predictions."""
        pred = self._ml_predictions.get("BTC-USDT", (None, 0.0, 0.0))
        regime_map = {0: "RANGING", 1: "TRENDING", 2: "DANGER"}
        regime_str = regime_map.get(pred[0], "RANGING") if pred[0] is not None else "RANGING"
        return (regime_str, pred[1], pred[2])
    
    self._momentum_engine = MomentumEngine(
        config=momentum_cfg,
        capital_manager=self._capital_mgr,
        btc_regime_fn=_get_btc_regime,
        telegram=self.telegram,
    )
    # Start scanner WebSocket in background thread
    threading.Thread(target=self._start_momentum_scanner, daemon=True).start()
    logger.info(f"Momentum Engine initialized: capital_pct={momentum_cfg.get('capital_pct', 5)}%")
```

#### Changes to `on_tick()` (after line ~711, after trend tick loop):

```python
# ── Momentum Engine ──
if self._momentum_engine is not None:
    try:
        connector = self.connectors.get(self.exchange)
        if connector:
            self._momentum_engine.tick(connector)
    except Exception as e:
        logger.error(f"Momentum tick error: {e}")
```

#### Changes to `did_fill_order()`:

```python
# Check if this fill belongs to the momentum engine
if self._momentum_engine is not None:
    if self._momentum_engine.handle_fill(event):
        return  # Momentum engine handled this fill
```

---

## Telegram Commands

### Added to `src/notifications/telegram_commands.py`

Add to `_commands` dict:

```python
"momentum_status": self._cmd_momentum_status,
"momentum_pnl": self._cmd_momentum_pnl,
"momentum_pause": self._cmd_momentum_pause,
"momentum_resume": self._cmd_momentum_resume,
"momentum_scan": self._cmd_momentum_scan,
```

#### /momentum_status

```python
def _cmd_momentum_status(self, update, context):
    engine = getattr(self.strategy, '_momentum_engine', None)
    if engine is None:
        update.message.reply_text("Momentum engine not configured.")
        return
    
    state = engine.state.value
    pos = engine.get_open_position()
    risk = engine.get_risk_status()
    scan = engine.get_last_scan_results()
    
    lines = [
        f"⚡ <b>MOMENTUM ENGINE</b>",
        "•••",
        f"State: <b>{state}</b>",
        f"ML Models: PQC={'✅' if engine.pqc_loaded else '❌'} "
        f"OEP={'✅' if engine.oep_loaded else '❌'} "
        f"DFT={'✅' if engine.dft_loaded else '❌'}",
        f"Trades today: {risk['trades_today']}/{risk['max_trades']}",
        f"Daily P&L: {self._fmt_pnl(risk['daily_pnl'])}",
        f"Budget: ${risk['budget']:,.0f}",
    ]
    
    if pos:
        pnl_pct = (pos.current_price - pos.entry_price) / pos.entry_price * 100
        hold_min = int((time.time() - pos.entry_timestamp) / 60)
        lines.extend([
            "•••",
            f"📈 <b>OPEN: {pos.symbol}</b>",
            f"Entry: ${pos.entry_price:,.4f} ({hold_min}m ago)",
            f"Now: ${pos.current_price:,.4f} ({pnl_pct:+.2f}%)",
            f"PQC: {pos.pqc_score:.2f} | SL: ${pos.stop_loss:,.4f} | TP: ${pos.take_profit:,.4f}",
        ])
    
    if scan:
        lines.extend(["•••", "🔍 <b>Top Scan Results:</b>"])
        for r in scan[:3]:
            lines.append(f"  {r.symbol}: +{r.price_change_5m}% | RVOL:{r.rvol}x | Score:{r.momentum_score}")
    
    update.message.reply_text("\n".join(lines), parse_mode="HTML")
```

---

## Configuration

### Added to `config/strategy.yaml`

```yaml
# ── Momentum Scalping Engine (ML-Enhanced) ────────────────────
momentum:
  enabled: false                  # Start disabled — enable after paper testing
  capital_pct: 5.0                # % of total capital allocated to momentum
  max_capital_usdt: 500           # Hard cap regardless of %

  # Scanner settings
  scan_interval_sec: 60           # How often to scan for candidates
  min_volume_24h_usdt: 5000000    # Minimum 24h volume ($5M)
  min_price_change_5m: 3.0        # Minimum 5m price change (%)
  max_daily_gain_pct: 30.0        # Skip coins already up >30% today
  min_rvol: 3.0                   # Minimum relative volume (vs 14-day avg)
  max_spread_pct: 0.15            # Maximum bid-ask spread (%)

  # Entry confirmation (5m candles)
  rsi_min: 55                     # RSI floor (momentum present)
  rsi_max: 75                     # RSI ceiling (not overbought)
  require_vwap_above: true        # Price must be above VWAP
  require_higher_lows: 3          # Last N candles must have higher lows

  # ML models
  ml:
    pqc_enabled: true             # Pump Quality Classifier
    pqc_model_path: "models/pump_classifier.pkl"
    pqc_threshold: 0.65           # Min score to enter

    oep_enabled: true             # Optimal Exit Predictor
    oep_model_path: "models/exit_predictor.pkl"
    oep_threshold: 0.70           # Exit when score exceeds this
    oep_check_interval_sec: 60    # How often to run exit model

    dft_enabled: true             # Dynamic Filter Tuner
    dft_model_path: "models/filter_tuner.pkl"
    dft_fallback_rules: true      # Use rule-based table if no ML model

  # Position management
  max_positions: 1                # Max concurrent momentum positions
  trailing_stop_pct: 1.0          # Trailing stop distance (%)
  trailing_activation_pct: 1.5    # Trailing activates after this % gain
  hard_stop_loss_pct: 2.0         # Absolute maximum loss per trade (%)
  take_profit_pct: 4.0            # Take profit target (%)
  time_stop_minutes: 30           # Force exit after N minutes
  volume_collapse_threshold: 0.5  # Exit if volume drops to this × entry volume

  # Risk controls
  daily_loss_limit_pct: 3.0       # Halt after losing this % of momentum capital
  max_trades_per_day: 5           # Maximum trades per 24h
  cooldown_minutes: 5             # Wait this long after any trade before next scan
  use_btc_correlation_gate: true  # Pause when BTC regime is DANGER

  # New listings
  new_listings_enabled: true      # Enable new listing detection
  listing_cooldown_minutes: 15    # Wait this long after listing before trading
  listing_max_age_minutes: 120    # Stop watching after this long
  max_listing_trades_per_day: 1   # Maximum new listing trades per day
```

---

## File Structure

```
src/
  momentum/
    __init__.py                   — Package init
    scanner.py                    — MarketScanner class (WebSocket !ticker@arr + REST exchangeInfo)
    momentum_filters.py           — MomentumFilters class (5-filter gate + candle confirmation)
    momentum_engine.py            — MomentumEngine class (orchestrator, state machine)
    momentum_position.py          — MomentumPositionManager (position lifecycle, trailing stops)
    momentum_risk.py              — MomentumRiskGuard (8-layer risk management)
    momentum_journal.py           — MomentumJournal (SQLite logging in data/momentum_journal.db)
    ml/
      __init__.py                 — ML sub-package init
      pump_classifier.py          — PumpClassifier (Model 1: XGBoost pump quality scoring)
      exit_predictor.py           — ExitPredictor (Model 2: LightGBM optimal exit timing)
      filter_tuner.py             — FilterTuner (Model 3: dynamic threshold adjustment)
      momentum_features.py        — calculate_momentum_features() for PQC/OEP
      pump_label_generator.py     — generate_pump_labels() for PQC training data
      train_momentum.py           — Training pipeline CLI (--model pqc|oep|all)

models/
  pump_classifier.pkl             — Trained PQC model
  exit_predictor.pkl              — Trained OEP model
  filter_tuner.pkl                — Trained DFT model (Phase 2, optional)

tests/
  test_scanner.py                 — Scanner unit tests
  test_pump_classifier.py         — PQC model tests
  test_momentum_engine.py         — Full cycle integration tests
  test_momentum_risk.py           — Risk guard tests

config/
  strategy.yaml                   — momentum: section added

hummingbot_files/scripts/
  ta_grid_trend.py                — MomentumEngine wired into __init__() and on_tick()
  capital_manager.py              — EngineType updated to include "momentum"

src/notifications/
  telegram_commands.py            — /momentum_* commands added
```

---

## Dependencies

Add to `requirements.txt`:

```
lightgbm>=4.0.0     # For OEP model (fast inference)
# xgboost already available (used by regime classifier)
# websockets already available (used by ws_feed.py)
# scikit-learn already available
# pandas, numpy already available
```

---

## Build Order

Implement in this order (each step is independently testable):

1. `src/momentum/__init__.py` + `src/momentum/ml/__init__.py` — package setup
2. `src/momentum/scanner.py` — market scanner (test: connect to WebSocket, print top movers)
3. `src/momentum/momentum_filters.py` — filter gate (test: mock ScanResult, verify pass/reject)
4. `src/momentum/momentum_risk.py` — risk guard (test: daily limits, cooldown, capital isolation)
5. `src/momentum/momentum_position.py` — position manager (test: open, trailing, close lifecycle)
6. `src/momentum/momentum_journal.py` — SQLite journal (test: log trade, query summaries)
7. `src/momentum/ml/filter_tuner.py` — DFT rule-based (test: threshold adjustment by regime)
8. `src/momentum/ml/momentum_features.py` — feature computation (test: mock candle data)
9. `src/momentum/ml/pump_classifier.py` — PQC model class (test: load/predict/save)
10. `src/momentum/ml/pump_label_generator.py` — label generation (test: mock 5m data)
11. `src/momentum/ml/train_momentum.py` — training pipeline (test: end-to-end train on small dataset)
12. `src/momentum/ml/exit_predictor.py` — OEP model class
13. `src/momentum/momentum_engine.py` — orchestrator (test: full cycle with mocks)
14. Modify `capital_manager.py` — add "momentum" engine type
15. Modify `strategy.yaml` — add momentum config block
16. Modify `ta_grid_trend.py` — wire momentum into on_tick()
17. Modify `telegram_commands.py` — add /momentum_* commands
18. Write tests: `test_scanner.py`, `test_pump_classifier.py`, `test_momentum_engine.py`, `test_momentum_risk.py`
