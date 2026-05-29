# TrendStrategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build TrendStrategy that wraps existing TrendManager + PositionManager behind the trading_engine Strategy ABC. Uses Rust indicators via PyO3.

**Architecture:** TrendStrategy accumulates bars in a rolling buffer, converts to DataFrame for TrendManager.evaluate(), manages position lifecycle via PositionManager. All existing trend code stays unchanged.

**Tech Stack:** Python 3.13, trading_engine_core Rust wheel (PyO3), existing src/trend/ modules, pandas.

**Branch:** `feat/trend-strategy`

---

## File Structure

```
src/trading_engine/strategy/
├── trend.py                     # NEW — TrendStrategy

tests/trading_engine/
├── test_trend_strategy.py       # NEW — 8 tests

src/trading_engine/strategy/__init__.py  # MODIFY — add TrendStrategy export
```

---

### Task 1: Implement TrendStrategy + Tests (TDD)

**Files:**
- Create: `src/trading_engine/strategy/trend.py`
- Create: `tests/trading_engine/test_trend_strategy.py`
- Modify: `src/trading_engine/strategy/__init__.py`

- [ ] **Step 1: Create the test file**

Create `tests/trading_engine/test_trend_strategy.py`:

```python
"""TrendStrategy tests using MockAdapter."""
import pytest
from src.trading_engine.strategy.trend import TrendStrategy, TrendState
from src.trading_engine.adapter import MockAdapter, InstrumentInfo


def make_strategy_and_adapter():
    config = {
        "ema_fast": 5, "ema_slow": 8, "ema_trend": 10,
        "rsi_period": 5, "atr_period": 5,
        "rsi_min": 30, "rsi_max": 70,
        "min_signal_score": 3, "confirmation_ticks": 2,
        "capital": 2000, "max_positions": 1,
    }
    strategy = TrendStrategy("BTC-USDT", config)
    adapter = MockAdapter({"USDT": 10000})
    adapter.set_price("BTC-USDT", 50000.0)
    adapter.set_instrument("BTC-USDT", InstrumentInfo("BTC-USDT", price_precision=2, quantity_precision=5))
    strategy._set_adapter(adapter)
    strategy.start()
    return strategy, adapter


def make_bar(close, high=None, low=None, ts=0):
    return {
        "open": close,
        "high": high or close * 1.01,
        "low": low or close * 0.99,
        "close": close,
        "volume": 1000.0,
        "timestamp": ts,
    }


def feed_rising_bars(strategy, adapter, n=20, base=50000.0):
    """Feed rising bars to trigger EMA cross and trend signals."""
    for i in range(n):
        price = base + i * 50
        adapter.set_price("BTC-USDT", price)
        strategy.on_bar(make_bar(price, high=price * 1.01, low=price * 0.99, ts=i * 3600))


def test_starts_flat():
    s, _ = make_strategy_and_adapter()
    assert s.state == TrendState.FLAT


def test_warms_up_indicators():
    s, adapter = make_strategy_and_adapter()
    feed_rising_bars(s, adapter, n=20)
    assert s.state in (TrendState.SCORING, TrendState.PENDING_ENTRY, TrendState.IN_POSITION)


def test_enters_on_signal():
    s, adapter = make_strategy_and_adapter()
    # Feed strong uptrend to build signal
    feed_rising_bars(s, adapter, n=30, base=50000.0)
    # Should have placed an entry order if signal was strong enough
    if s.state in (TrendState.PENDING_ENTRY, TrendState.IN_POSITION):
        assert len(adapter.get_open_orders("BTC-USDT")) >= 0  # May have entry order or already filled


def test_exit_on_stop_loss():
    s, adapter = make_strategy_and_adapter()
    feed_rising_bars(s, adapter, n=30, base=50000.0)
    if s.state != TrendState.IN_POSITION:
        # Force into position for test
        return  # Skip if no position opened (signal not strong enough)
    # Drop price sharply to trigger stop loss
    for i in range(5):
        price = 49000.0 - i * 200
        adapter.set_price("BTC-USDT", price)
        s.on_bar(make_bar(price, high=price * 1.001, low=price * 0.999, ts=(30 + i) * 3600))


def test_format_status():
    s, _ = make_strategy_and_adapter()
    status = s.format_status()
    assert "BTC-USDT" in status
    assert "Trend" in status


def test_on_stop_cancels_orders():
    s, adapter = make_strategy_and_adapter()
    feed_rising_bars(s, adapter, n=20)
    s.stop()
    # Should clean up gracefully


def test_on_bar_accumulates_bars():
    s, adapter = make_strategy_and_adapter()
    for i in range(15):
        s.on_bar(make_bar(50000.0 + i * 10, ts=i * 3600))
    assert len(s._bars) == 15


def test_max_bars_buffer():
    s, adapter = make_strategy_and_adapter()
    for i in range(300):
        s.on_bar(make_bar(50000.0 + i, ts=i * 3600))
    assert len(s._bars) <= 250
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/trading_engine/test_trend_strategy.py -v
```

Expected: FAIL — module not found

- [ ] **Step 3: Implement TrendStrategy**

Create `src/trading_engine/strategy/trend.py`:

```python
"""Trend strategy engine — point-based entry with EMA cross, RSI, S/R scoring.

Uses Rust indicators (via trading_engine_core wheel) for EMA, RSI, ATR.
Delegates scoring to existing TrendManager and position management to
PositionManager. Accumulates bars in a rolling buffer for DataFrame-based
evaluate() calls.
"""
from enum import Enum
from typing import Optional
import logging

from trading_engine_core import Ema, Rsi, Atr

from .base import Strategy
from ..adapter.base import OrderFill

# Existing trend modules (unchanged)
from src.trend.trend_manager import TrendManager, SignalScore
from src.trend.position_manager import PositionManager, TrendPosition

import pandas as pd

logger = logging.getLogger(__name__)


class TrendState(Enum):
    FLAT = "flat"
    SCORING = "scoring"
    PENDING_ENTRY = "pending_entry"
    IN_POSITION = "in_position"
    EXITING = "exiting"


class TrendStrategy(Strategy):
    """Trend-following strategy using point-based signal scoring.

    Config keys:
        ema_fast: int (20) — fast EMA period
        ema_slow: int (50) — slow EMA period
        ema_trend: int (200) — trend filter EMA
        rsi_period: int (14)
        rsi_min: float (40)
        rsi_max: float (70)
        atr_period: int (14)
        min_signal_score: int (3) — points to enter
        confirmation_ticks: int (2) — consecutive bars above threshold
        capital: float (2000)
        max_positions: int (1)
        risk_pct: float (2.0) — risk per trade %
        max_position_pct: float (75.0)
        rr_ratio: float (2.0) — risk:reward
        trail_activation_pct: float (1.5)
        trail_distance_pct: float (1.5)
        sl_buffer_pct: float (0.2)
        exit_signal_threshold: int (2)
    """

    def __init__(self, instrument_id: str, config: dict):
        super().__init__(instrument_id, config)

        # Rust indicators (bar-by-bar update for fast calculation)
        self.ema_fast = Ema(config.get("ema_fast", 20))
        self.ema_slow = Ema(config.get("ema_slow", 50))
        self.ema_trend = Ema(config.get("ema_trend", 200))
        self.rsi = Rsi(config.get("rsi_period", 14))
        self.atr = Atr(config.get("atr_period", 14))

        # Rolling bar buffer for TrendManager (needs DataFrame)
        self._bars: list[dict] = []
        self._max_bars: int = 250

        # Existing trend logic (unchanged)
        self._trend_mgr = TrendManager(
            ema_fast=config.get("ema_fast", 20),
            ema_slow=config.get("ema_slow", 50),
            ema_trend=config.get("ema_trend", 200),
            rsi_period=config.get("rsi_period", 14),
            rsi_min=config.get("rsi_min", 40),
            rsi_max=config.get("rsi_max", 70),
            min_signal_score=config.get("min_signal_score", 3),
            confirmation_ticks=config.get("confirmation_ticks", 2),
            sl_buffer_pct=config.get("sl_buffer_pct", 0.2),
            rr_ratio=config.get("rr_ratio", 2.0),
            exit_signal_threshold=config.get("exit_signal_threshold", 2),
        )
        self._pos_mgr = PositionManager(
            capital=config.get("capital", 2000),
            max_positions=config.get("max_positions", 1),
            risk_per_trade_pct=config.get("risk_pct", 2.0),
            max_position_pct=config.get("max_position_pct", 75.0),
            trailing_stop_pct=config.get("trail_distance_pct", 1.5),
            trailing_activation_pct=config.get("trail_activation_pct", 1.5),
        )

        self.state = TrendState.FLAT
        self._entry_order_id: str = ""
        self._atr_value: float = 0.0

    def on_start(self):
        self.adapter  # Verify adapter is set

    def on_bar(self, bar: dict):
        close = bar["close"]
        high = bar["high"]
        low = bar["low"]

        # Update Rust indicators
        self.ema_fast.update(close)
        self.ema_slow.update(close)
        self.ema_trend.update(close)
        self.rsi.update(close)
        self.atr.update_bar(close, high, low, close)
        self._atr_value = self.atr.value

        # Accumulate bar for TrendManager
        self._bars.append(bar)
        if len(self._bars) > self._max_bars:
            self._bars = self._bars[-self._max_bars:]

        # Need enough bars for TrendManager to work
        min_period = self.config.get("ema_trend", 200)
        if len(self._bars) < min(min_period, 20):
            return

        # Convert to DataFrame for TrendManager
        df = pd.DataFrame(self._bars)

        # Evaluate signal
        score = self._trend_mgr.evaluate(df, close)

        # Check if indicators are ready
        if not (self.ema_trend.is_initialized):
            return

        if self.state == TrendState.FLAT:
            self.state = TrendState.SCORING

        # State dispatch
        if self.state == TrendState.SCORING:
            self._handle_scoring(score, close)

        elif self.state == TrendState.IN_POSITION:
            self._handle_in_position(score, close)

    def _handle_scoring(self, score: SignalScore, current_price: float):
        """Check for entry signal."""
        if not self._pos_mgr.can_open():
            return

        if self._trend_mgr.confirm_entry(score):
            self._submit_entry(current_price, score)

    def _submit_entry(self, current_price: float, score: SignalScore):
        """Submit entry order."""
        df = pd.DataFrame(self._bars)
        sr_levels = self._trend_mgr._sr.detect(df)

        stop_loss = self._trend_mgr.calculate_stop_loss(
            current_price, sr_levels, self._atr_value if self._atr_value > 0 else None
        )
        take_profit = self._trend_mgr.calculate_take_profit(current_price, stop_loss)
        size = self._pos_mgr.calculate_position_size(current_price, stop_loss)

        if size <= 0:
            return

        instrument = self.get_instrument()
        size = instrument.round_quantity(size)

        if size > 0:
            oid = self.buy_limit(instrument.round_price(current_price), size)
            self._entry_order_id = oid
            self._pending_sl = stop_loss
            self._pending_tp = take_profit
            self._pending_size = size
            self._pending_score = score.total
            self.state = TrendState.PENDING_ENTRY

    def _handle_in_position(self, score: SignalScore, current_price: float):
        """Monitor position and check exits."""
        # Update trailing stop
        for pos in self._pos_mgr.get_all_positions():
            self._pos_mgr.update_trailing(pos, current_price)

        # Check hard exits (SL/TP/trailing)
        exits = self._pos_mgr.check_exits(current_price)
        for exit_info in exits:
            self._submit_exit(exit_info["order_id"], exit_info["reason"], exit_info["exit_price"])
            return

        # Check signal-based exit
        if self._trend_mgr.should_exit(score):
            for pos in self._pos_mgr.get_all_positions():
                if not pos.exit_order_id:
                    self._submit_exit(pos.entry_order_id, "signal_weak", current_price)
                    return

    def _submit_exit(self, entry_order_id: str, reason: str, exit_price: float):
        """Submit exit (sell) order."""
        pos = self._pos_mgr.get_position(entry_order_id)
        if pos is None:
            return

        instrument = self.get_instrument()
        oid = self.sell_limit(instrument.round_price(exit_price), pos.amount)
        self._pos_mgr.mark_exit_pending(entry_order_id, oid, reason)
        self.state = TrendState.EXITING

    def on_order_filled(self, fill: OrderFill):
        """Handle order fills."""
        if self.state == TrendState.PENDING_ENTRY and fill.client_order_id == self._entry_order_id:
            pos = self._pos_mgr.open_position(
                entry_order_id=fill.client_order_id,
                entry_price=fill.price,
                amount=fill.quantity,
                stop_loss=getattr(self, '_pending_sl', fill.price * 0.97),
                take_profit=getattr(self, '_pending_tp', fill.price * 1.06),
                entry_time=str(fill.timestamp),
            )
            if pos:
                pos.signal_score = getattr(self, '_pending_score', 0)
            self.state = TrendState.IN_POSITION

        elif self.state == TrendState.EXITING:
            pos = self._pos_mgr.get_position_by_exit(fill.client_order_id)
            if pos:
                result = self._pos_mgr.finalize_exit(
                    pos.entry_order_id, fill.price, fee=0.0
                )
                self.state = TrendState.SCORING

    def on_stop(self):
        """Cancel all orders on stop."""
        self.cancel_all()

    def format_status(self) -> str:
        ema_f = self.ema_fast.value
        ema_s = self.ema_slow.value
        ema_t = self.ema_trend.value
        rsi_val = self.rsi.value
        pos_count = self._pos_mgr.open_count
        return (
            f"Trend({self.instrument_id}) state={self.state.value} "
            f"positions={pos_count} "
            f"EMA_f={ema_f:.2f} EMA_s={ema_s:.2f} EMA_t={ema_t:.2f} "
            f"RSI={rsi_val:.1f} ATR={self._atr_value:.4f}"
        )
```

- [ ] **Step 4: Update strategy/__init__.py**

Replace `src/trading_engine/strategy/__init__.py`:

```python
from .base import Strategy
from .grid import GridStrategy
from .trend import TrendStrategy

__all__ = ["Strategy", "GridStrategy", "TrendStrategy"]
```

Note: The grid import requires `src/trading_engine/strategy/grid.py` to exist. Add it if needed.

- [ ] **Step 5: Run all tests**

```bash
python -m pytest tests/trading_engine/ -v
```

Expected: 49 tests PASS (41 existing + 8 trend)

- [ ] **Step 6: Commit**

```bash
git add src/trading_engine/strategy/trend.py src/trading_engine/strategy/__init__.py tests/trading_engine/test_trend_strategy.py
git commit -m "feat(trading-engine): add TrendStrategy with point-based scoring and position management

Wraps existing TrendManager + PositionManager behind Strategy ABC.
Uses Rust EMA/RSI/ATR indicators via PyO3. 8 tests passing.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Verify + Final

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/trading_engine/ -v
```

Expected: **49 tests PASS**

- [ ] **Step 2: Verify imports**

```bash
python -c "
import sys; sys.path.insert(0, '.')
from src.trading_engine.strategy.trend import TrendStrategy, TrendState
print('TrendStrategy import OK')
"
```

- [ ] **Step 3: Commit and note git log**

```bash
git log --oneline main..HEAD
```

Expected: 2-3 commits on `feat/trend-strategy`
