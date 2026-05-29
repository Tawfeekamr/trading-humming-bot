# TrendStrategy Design Spec

**Date:** 2026-05-29
**Branch:** `feat/trend-strategy`

## Goal

Build `TrendStrategy` — a Strategy subclass that wraps the existing `TrendManager` (point-based scoring) and `PositionManager` (SL/TP/trailing) behind the trading_engine Strategy ABC. Uses Rust indicators via PyO3 for EMA/RSI/ATR, but delegates scoring logic to the existing Python code.

## Architecture

```
TrendStrategy("BTC-USDT", config)
  ├── Rust indicators (PyO3): Ema(20), Ema(50), Ema(200), Rsi(14), Atr(14)
  ├── _bar_buffer: rolling list of last 250 bars (for TrendManager.evaluate())
  ├── TrendManager: point scoring + confirmation (existing, unchanged)
  ├── PositionManager: SL/TP/trailing/sizing (existing, unchanged)
  └── Strategy ABC: on_start, on_bar, on_stop, on_order_filled
```

## State Machine

```
FLAT → SCORING → PENDING_ENTRY → IN_POSITION → EXITING → FLAT
```

- **FLAT**: No position, indicators warming up
- **SCORING**: Indicators ready, evaluating signals each bar
- **PENDING_ENTRY**: Signal confirmed, entry order submitted
- **IN_POSITION**: Monitoring trailing stop, checking exits each bar
- **EXITING**: Exit order submitted

## TrendStrategy Implementation

### Constructor

```python
class TrendState(Enum):
    FLAT = "flat"
    SCORING = "scoring"
    PENDING_ENTRY = "pending_entry"
    IN_POSITION = "in_position"
    EXITING = "exiting"

class TrendStrategy(Strategy):
    def __init__(self, instrument_id: str, config: dict):
        super().__init__(instrument_id, config)

        # Rust indicators (bar-by-bar update)
        self.ema_fast = Ema(config.get("ema_fast", 20))
        self.ema_slow = Ema(config.get("ema_slow", 50))
        self.ema_trend = Ema(config.get("ema_trend", 200))
        self.rsi = Rsi(config.get("rsi_period", 14))
        self.atr = Atr(config.get("atr_period", 14))

        # Rolling bar buffer for TrendManager (needs DataFrame)
        self._bars: list[dict] = []
        self._max_bars: int = 250

        # Existing trend logic (unchanged)
        self._trend_mgr = TrendManager(...)
        self._pos_mgr = PositionManager(...)

        self.state = TrendState.FLAT
        self._entry_order_id: str = ""
```

### on_bar flow

```
on_bar(bar):
    1. Update Rust indicators (EMA fast/slow/trend, RSI, ATR)
    2. Append bar to _bars buffer (keep last 250)
    3. Convert _bars to pandas DataFrame
    4. Call trend_mgr.evaluate(df, bar["close"]) → SignalScore
    5. State dispatch:
       - FLAT/SCORING: if confirm_entry(score) → submit entry order
       - IN_POSITION:
         a. update_trailing(current_price)
         b. check_exits(current_price) → submit exit if triggered
         c. if should_exit(score) → submit exit
```

### on_order_filled flow

```
on_order_filled(fill):
    - PENDING_ENTRY + fill matches entry order → IN_POSITION, open_position()
    - EXITING + fill matches exit order → FLAT, finalize_exit()
```

## Files

```
src/trading_engine/strategy/
├── trend.py                    # NEW — TrendStrategy

tests/trading_engine/
├── test_trend_strategy.py      # NEW — tests with MockAdapter
```

## Config Keys

| Key | Default | Description |
|-----|---------|-------------|
| ema_fast | 20 | Fast EMA period |
| ema_slow | 50 | Slow EMA period |
| ema_trend | 200 | Trend filter EMA |
| rsi_period | 14 | RSI period |
| atr_period | 14 | ATR period |
| rsi_min | 40 | RSI lower bound for entry |
| rsi_max | 70 | RSI upper bound for entry |
| min_signal_score | 3 | Points needed to enter |
| confirmation_ticks | 2 | Consecutive bars above threshold |
| risk_pct | 2.0 | Risk per trade (% of capital) |
| max_position_pct | 75.0 | Max position size (% of capital) |
| rr_ratio | 2.0 | Risk:reward ratio |
| trail_activation_pct | 1.5 | % gain to activate trailing |
| trail_distance_pct | 1.5 | Trailing stop distance (%) |
| capital | 2000 | Allocated capital |
| max_positions | 1 | Max concurrent positions |
| sl_buffer_pct | 0.2 | Stop loss buffer below support (%) |
| exit_signal_threshold | 2 | Score below this → exit signal |

## Tests (8 tests)

1. test_starts_flat — initial state is FLAT
2. test_warms_up_indicators — feeds bars, state moves to SCORING
3. test_enters_on_signal — feeds bars with strong trend, verify entry order placed
4. test_exit_on_stop_loss — enter position, price drops to SL, verify exit
5. test_exit_on_take_profit — enter position, price rises to TP, verify exit
6. test_trailing_stop — enter, price rises activating trail, then drops
7. test_exit_on_signal_weak — enter, then weak signal triggers exit
8. test_format_status — verify status string

## Scope

### In scope
- TrendStrategy class
- Tests with MockAdapter
- No changes to TrendManager or PositionManager

### Out of scope
- Wiring into live bot (separate PR)
- Trend journaling (can add later)
- State persistence (can add later)
- NautilusTrader adapter
