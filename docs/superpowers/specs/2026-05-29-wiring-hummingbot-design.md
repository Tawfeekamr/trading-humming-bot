# Wire trading_engine into Live Bot — Design Spec

**Date:** 2026-05-29
**Branch:** `feat/wire-trading-engine`
**Depends on:** Phase 2 + Phase 5 (merged to main)

## Goal

Replace the 4 PairEngine grid/indicator logic in `ta_grid_btcusdt.py` with the trading_engine package (StrategyHost + GridStrategy + HummingbotAdapter). All production features (Telegram, journaling, PnL matching, state persistence) remain intact.

## Approach

**Replace indicator + grid logic, keep everything else.** The existing `_grid_tick()` method handles: circuit breaker checks, daily resets, overtrading checks, candle fetching, indicator calculation, state evaluation, and grid placement. We replace **indicator calculation + state evaluation + grid placement** with trading_engine. The safety checks and candle fetching stay in the existing flow.

## What Changes in ta_grid_btcusdt.py

### 1. New imports (top of file)
```python
from src.trading_engine.adapter.hummingbot_integration import (
    init_trading_engine, tick_trading_engine, route_fill, build_grid_config,
)
```

### 2. In `__init__`: Initialize trading engine (after PairEngine creation)
```python
# After existing PairEngine initialization (~line 387)
# Build trading engine config from YAML values
te_config = {
    "grid_levels": self.grid_levels,
    "capital": self.capital_usdt,
    "spacing_atr_multiplier": self.atr_multiplier,
    "ema_period": self.ema_period,
    "rsi_period": self.rsi_period,
    "atr_period": self.atr_period,
    "bollinger_period": self.bb_period,
    "bollinger_std_dev": self.bb_std,
    "order_refresh_seconds": self.order_refresh_time,
    "rsi_oversold": self.rsi_oversold,
    "rsi_overbought": self.rsi_overbought,
}
self._te_host = init_trading_engine(
    connector=self.connectors.get(self.exchange),
    strategy_ref=self,
    pairs=list(self._pair_engines.keys()),
    config=te_config,
)
```

### 3. In `_grid_tick()`: Replace indicator + grid logic with trading_engine
Instead of: fetch candles → calculate indicators → evaluate state → place grid
Replace with: fetch candles → feed bar to `tick_trading_engine()`

The circuit breaker, daily reset, and overtrading checks stay in `_grid_tick()` BEFORE the trading engine call. The trading engine handles its own indicator calculation and grid placement internally.

```python
# In _grid_tick(), after safety checks and candle fetching:
# Replace lines ~759-827 (indicator calc, state eval, grid placement) with:
if latest_candle is not None:
    bar = {
        "open": float(latest_candle["open"]),
        "high": float(latest_candle["high"]),
        "low": float(latest_candle["low"]),
        "close": float(latest_candle["close"]),
        "volume": float(latest_candle.get("volume", 0)),
        "timestamp": int(latest_candle.get("timestamp", 0)),
    }
    tick_trading_engine(self._te_host, symbol, bar)
```

### 4. In `did_fill_order()`: Route fills to trading engine
Add one line at the start of the fill handler, before existing PnL matching:
```python
route_fill(self._te_host, event)
```

### 5. In `on_stop()`: Stop the trading engine
Add before existing cleanup:
```python
self._te_host.stop()
```

## What Stays Unchanged
- YAML config loading
- Telegram notifications and command handler
- Trade journaling (SQLite)
- Buy/sell PnL matching in did_fill_order()
- Grid state persistence
- Health monitoring
- Circuit breaker (existing, separate from trading_engine's)
- Daily reset logic
- Overtrading protection
- Candle feed fetching

## Risk Mitigation
- **Feature flag**: `self._use_trading_engine = True` config toggle. If False, uses old PairEngine path.
- **Gradual migration**: Can enable per-pair by checking if pair is in a `_te_enabled_pairs` list.
- **Rollback**: If trading_engine breaks, set flag to False and redeploy.

## Testing
- Deploy to paper trading (already on binance_paper_trade)
- Compare DOGE-USDT grid behavior before and after
- Monitor for 24-48 hours
- Verify fills still match, journal still records, Telegram still notifies
