# Wire trading_engine into Live Bot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the trading_engine package (StrategyHost + GridStrategy + HummingbotAdapter) into the existing Hummingbot strategy script, replacing indicator+grid logic for all 4 pairs while keeping production features intact.

**Architecture:** 5 surgical insertions into `ta_grid_btcusdt.py`. Feature flag `use_trading_engine` in YAML config enables the new path. Old PairEngine path is kept as fallback.

**Tech Stack:** Python 3.13, Hummingbot StrategyV2Base, trading_engine package (Phase 2+5)

**Branch:** `feat/wire-trading-engine`

---

## File Structure

```
hummingbot_files/scripts/ta_grid_btcusdt.py   # MODIFY — 5 insertion points
```

---

### Task 1: Add imports and feature flag

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_btcusdt.py`

- [ ] **Step 1: Add trading_engine imports after line 94**

Insert after line 94 (the last import block):

```python

# Trading engine integration (Phase 5)
try:
    from src.trading_engine.adapter.hummingbot_integration import (
        init_trading_engine, tick_trading_engine, route_fill, build_grid_config,
    )
    _TRADING_ENGINE_AVAILABLE = True
except ImportError:
    _TRADING_ENGINE_AVAILABLE = False
```

- [ ] **Step 2: Add feature flag read in __init__ config loading**

After line 260 (where `self.atr_multiplier` is set), add:

```python

        # Trading engine feature flag
        self.use_trading_engine = (
            _TRADING_ENGINE_AVAILABLE
            and os.environ.get("USE_TRADING_ENGINE", "false").lower() == "true"
        )
```

- [ ] **Step 3: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_btcusdt.py
git commit -m "feat(bot): add trading_engine imports and feature flag to ta_grid script"
```

---

### Task 2: Initialize StrategyHost in __init__

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_btcusdt.py`

- [ ] **Step 1: Add trading engine initialization at end of __init__**

Before line 496 (the force-ready watchdog thread start), insert:

```python

        # Initialize trading engine (Phase 5) — after connector is available
        self._te_host = None
        if self.use_trading_engine:
            try:
                te_config = {
                    "grid_levels": self.levels,
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
                pairs = list(self._pair_engines.keys())
                self._te_host = init_trading_engine(
                    connector=self.connectors.get(self.exchange),
                    strategy_ref=self,
                    pairs=pairs,
                    config=te_config,
                )
                self.logger().info(f"Trading engine initialized for {len(pairs)} pairs: {pairs}")
            except Exception as e:
                self.logger().error(f"Failed to initialize trading engine: {e}")
                self._te_host = None
                self.use_trading_engine = False

```

- [ ] **Step 2: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_btcusdt.py
git commit -m "feat(bot): initialize StrategyHost with HummingbotAdapter in __init__"
```

---

### Task 3: Wire tick_trading_engine into _grid_tick()

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_btcusdt.py`

- [ ] **Step 1: Add trading engine path in _grid_tick()**

Find the block where candle data is fetched and indicators are calculated (around line 714-827). Insert a trading engine branch that short-circuits the old indicator+grid logic. Add this block right BEFORE the candle fetch block (before line ~714):

```python
            # ── Trading Engine Path (Phase 5) ──
            if self.use_trading_engine and self._te_host is not None:
                try:
                    if engine.symbol in self.candle_feeds:
                        df = self.candle_feeds[engine.symbol].fetch_candles(limit=250)
                        if df is not None and len(df) > 0:
                            last = df.iloc[-1]
                            bar = {
                                "open": float(last["open"]),
                                "high": float(last["high"]),
                                "low": float(last["low"]),
                                "close": float(last["close"]),
                                "volume": float(last.get("volume", 0)),
                                "timestamp": int(last.get("timestamp", 0)),
                            }
                            tick_trading_engine(self._te_host, engine.symbol, bar)
                except Exception as e:
                    self.logger().error(f"Trading engine tick error for {engine.symbol}: {e}")
                return  # Skip old PairEngine grid logic for this pair

```

This inserts before the existing candle fetch, and `return` skips the rest of the old indicator+grid logic for pairs using trading_engine. The circuit breaker, daily reset, and overtrading checks that happen BEFORE this block still run.

- [ ] **Step 2: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_btcusdt.py
git commit -m "feat(bot): wire tick_trading_engine into _grid_tick with feature flag"
```

---

### Task 4: Wire fills and stop

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_btcusdt.py`

- [ ] **Step 1: Route fills in did_fill_order()**

At the start of `did_fill_order()` method body (after line 1106, inside the `try:` block), add:

```python
            # Route fill to trading engine (Phase 5)
            if self.use_trading_engine and self._te_host is not None:
                try:
                    route_fill(self._te_host, event)
                except Exception as e:
                    self.logger().error(f"Trading engine fill routing error: {e}")

```

This goes BEFORE the existing fill processing code, so both the trading engine AND the existing PnL matching/journaling run for each fill.

- [ ] **Step 2: Stop host in on_stop()**

At the start of `on_stop()` method body (after line 1552, before the state saving), add:

```python
        # Stop trading engine (Phase 5)
        if self._te_host is not None:
            try:
                self._te_host.stop()
            except Exception:
                pass

```

- [ ] **Step 3: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_btcusdt.py
git commit -m "feat(bot): wire route_fill and host.stop into did_fill_order and on_stop"
```

---

### Task 5: Verify and commit final

- [ ] **Step 1: Verify Python tests still pass**

```bash
python -m pytest tests/trading_engine/ -v
```

Expected: 41 tests PASS (unchanged)

- [ ] **Step 2: Verify the script has no syntax errors**

```bash
python -c "
import ast
with open('hummingbot_files/scripts/ta_grid_btcusdt.py') as f:
    ast.parse(f.read())
print('Syntax OK')
"
```

Expected: `Syntax OK`

- [ ] **Step 3: Verify git log**

```bash
git log --oneline main..HEAD
```

Expected: 5 commits on `feat/wire-trading-engine`

---

## Self-Review

### 1. Spec Coverage

| Spec Requirement | Task | Status |
|---|---|---|
| Import integration helpers | Task 1 | ✅ |
| Feature flag (USE_TRADING_ENGINE env var) | Task 1 | ✅ |
| Initialize StrategyHost in __init__ | Task 2 | ✅ |
| Replace indicator+grid in _grid_tick() | Task 3 | ✅ |
| Route fills in did_fill_order() | Task 4 | ✅ |
| Stop host in on_stop() | Task 4 | ✅ |
| Old path kept as fallback | Task 3 | ✅ |
| Syntax verification | Task 5 | ✅ |

### 2. Placeholder Scan

No TBD/TODO/placeholders. All steps contain complete code.

### 3. Type Consistency

- `self._te_host` initialized as `None` in Task 2, checked for `is not None` in Tasks 3-4
- Config keys match `build_grid_config()` expected keys from `hummingbot_integration.py`
- `tick_trading_engine()` takes `(host, pair, bar)` — matches integration layer signature
- `route_fill()` takes `(host, event)` — matches integration layer signature
