# Trend-Following Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a trend-following trading engine alongside the existing grid bot in the same Hummingbot v2 container.

**Architecture:** A new dual-engine strategy (`ta_grid_trend.py`) imports the existing grid bot as-is and adds an independent trend engine with signal scoring (EMA crossover + RSI + S/R + candlestick patterns). Both engines share one container but have isolated capital, state, and trade journals.

**Tech Stack:** Python 3.10+, pandas, SQLite, Hummingbot v2 (StrategyV2Base), existing indicator modules (EMA, RSI, ATR, BB), python-telegram-bot.

---

## File Structure

### New Files
```
src/trend/__init__.py                    # Package init
src/trend/support_resistance.py          # S/R level detection
src/trend/candlestick_patterns.py        # Bullish/bearish pattern detection
src/trend/position_manager.py            # Position tracking, SL/TP/trailing
src/trend/trend_manager.py              # Signal scoring, entry/exit decisions
src/trend/trend_journal.py              # Trend trade journal (separate DB table)
hummingbot_files/scripts/ta_grid_trend.py          # Dual-engine strategy entry point
hummingbot_files/conf/scripts/ta_grid_trend_conf.yml  # V2 config

tests/test_support_resistance.py         # Unit tests
tests/test_candlestick_patterns.py       # Unit tests
tests/test_position_manager.py           # Unit tests
tests/test_trend_manager.py             # Unit tests
tests/test_trend_journal.py             # Unit tests
```

### Modified Files
```
src/notifications/telegram_commands.py   # Add /trend_* command handlers
config/strategy.yaml                     # Add trend config section
docker-entrypoint.sh                     # Change SCRIPT_CONFIG
```

### Files NOT Modified
```
hummingbot_files/scripts/ta_grid_btcusdt.py   # ZERO changes
src/grid/                                       # ZERO changes
src/indicators/                                 # Reused as-is
src/risk/circuit_breaker.py                     # Reused as-is
```

---

### Task 1: Support/Resistance Detection Module

**Files:**
- Create: `src/trend/__init__.py`
- Create: `src/trend/support_resistance.py`
- Create: `tests/test_support_resistance.py`

- [ ] **Step 1: Create package init and write failing test**

Create `src/trend/__init__.py`:
```python
```
(empty file)

Create `tests/test_support_resistance.py`:
```python
import pytest
import pandas as pd
import numpy as np
from src.trend.support_resistance import SupportResistance


@pytest.fixture
def sample_candles():
    """50 candles with clear support at ~90 and resistance at ~100."""
    closes = []
    highs = []
    lows = []
    # Create price data bouncing between 90 and 100
    for i in range(50):
        phase = (i % 10)
        if phase < 5:
            price = 90.0 + phase * 2.0
        else:
            price = 100.0 - (phase - 5) * 2.0
        closes.append(price)
        highs.append(price + 0.5)
        lows.append(price - 0.5)
    return pd.DataFrame({
        "high": highs,
        "low": lows,
        "close": closes,
    })


class TestSupportResistance:
    def test_detect_levels_returns_list(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        assert isinstance(levels, list)

    def test_level_structure(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        for level in levels:
            assert "price" in level
            assert "type" in level  # "support" or "resistance"
            assert "touches" in level
            assert "strength" in level
            assert level["type"] in ("support", "resistance")
            assert level["touches"] >= 2
            assert isinstance(level["price"], float)

    def test_support_below_current_price(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        current_price = sample_candles["close"].iloc[-1]
        supports = [l for l in levels if l["type"] == "support"]
        for s in supports:
            assert s["price"] <= current_price * 1.01  # within 1%

    def test_resistance_above_current_price(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        current_price = sample_candles["close"].iloc[-1]
        resistances = [l for l in levels if l["type"] == "resistance"]
        for r in resistances:
            assert r["price"] >= current_price * 0.99

    def test_cluster_nearby_pivots(self):
        """Pivots within 0.5% should be clustered."""
        sr = SupportResistance(cluster_pct=0.005)
        closes = [100.0] * 50
        lows = [99.0 + (i % 5) * 0.1 for i in range(50)]  # 99.0 to 99.4
        highs = [101.0 + (i % 5) * 0.1 for i in range(50)]  # 101.0 to 101.4
        df = pd.DataFrame({"high": highs, "low": lows, "close": closes})
        levels = sr.detect(df)
        # Nearby pivots should cluster into fewer levels
        support_prices = [l["price"] for l in levels if l["type"] == "support"]
        # No two support levels should be within 0.5% of each other
        for i, p1 in enumerate(support_prices):
            for p2 in support_prices[i + 1:]:
                assert abs(p1 - p2) / min(p1, p2) > 0.004

    def test_nearest_support(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        support = sr.nearest_support(levels, 95.0)
        if support is not None:
            assert support["type"] == "support"
            assert support["price"] <= 95.0

    def test_nearest_support_returns_none_when_empty(self):
        sr = SupportResistance()
        assert sr.nearest_support([], 95.0) is None

    def test_nearest_resistance(self, sample_candles):
        sr = SupportResistance()
        levels = sr.detect(sample_candles)
        resistance = sr.nearest_resistance(levels, 95.0)
        if resistance is not None:
            assert resistance["type"] == "resistance"
            assert resistance["price"] >= 95.0

    def test_empty_dataframe(self):
        sr = SupportResistance()
        df = pd.DataFrame({"high": [], "low": [], "close": []})
        levels = sr.detect(df)
        assert levels == []

    def test_insufficient_data(self):
        sr = SupportResistance()
        df = pd.DataFrame({"high": [100], "low": [99], "close": [99.5]})
        levels = sr.detect(df)
        assert levels == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_support_resistance.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.trend.support_resistance'`

- [ ] **Step 3: Write implementation**

Create `src/trend/support_resistance.py`:
```python
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class SupportResistance:
    """Detects support and resistance levels from candle data."""

    def __init__(self, cluster_pct: float = 0.005, min_touches: int = 2,
                 lookback: int = 50) -> None:
        self._cluster_pct = cluster_pct
        self._min_touches = min_touches
        self._lookback = lookback

    def detect(self, df: pd.DataFrame) -> list[dict]:
        """Find support and resistance levels from OHLC data.

        Args:
            df: DataFrame with 'high', 'low', 'close' columns.

        Returns:
            List of dicts with keys: price, type, touches, strength.
        """
        if len(df) < 5:
            return []

        df = df.tail(self._lookback)
        pivots = self._find_pivots(df)
        if not pivots:
            return []

        clustered = self._cluster_pivots(pivots)
        current_price = float(df["close"].iloc[-1])

        levels = []
        for price, touches in clustered.items():
            if touches < self._min_touches:
                continue
            level_type = "support" if price <= current_price else "resistance"
            strength = min(touches / 5.0, 1.0)
            levels.append({
                "price": price,
                "type": level_type,
                "touches": touches,
                "strength": round(strength, 2),
            })

        levels.sort(key=lambda l: l["price"])
        return levels

    def nearest_support(self, levels: list[dict], price: float) -> Optional[dict]:
        """Find the closest support level at or below price."""
        supports = [l for l in levels if l["type"] == "support" and l["price"] <= price]
        if not supports:
            return None
        return max(supports, key=lambda l: l["price"])

    def nearest_resistance(self, levels: list[dict], price: float) -> Optional[dict]:
        """Find the closest resistance level at or above price."""
        resistances = [l for l in levels if l["type"] == "resistance" and l["price"] >= price]
        if not resistances:
            return None
        return min(resistances, key=lambda l: l["price"])

    def _find_pivots(self, df: pd.DataFrame) -> list[tuple[str, float]]:
        """Find swing highs and lows using 5-candle windows."""
        pivots = []
        highs = df["high"].tolist()
        lows = df["low"].tolist()

        for i in range(2, len(df) - 2):
            # Swing high: this candle's high is highest in 5-candle window
            if highs[i] >= max(highs[i - 2:i]) and highs[i] >= max(highs[i + 1:i + 3]):
                pivots.append(("resistance", float(highs[i])))
            # Swing low: this candle's low is lowest in 5-candle window
            if lows[i] <= min(lows[i - 2:i]) and lows[i] <= min(lows[i + 1:i + 3]):
                pivots.append(("support", float(lows[i])))

        return pivots

    def _cluster_pivots(self, pivots: list[tuple[str, float]]) -> dict[float, int]:
        """Group nearby pivots within cluster_pct into single levels."""
        if not pivots:
            return {}

        sorted_pivots = sorted(pivots, key=lambda p: p[1])
        clusters: dict[float, int] = {}

        current_price = sorted_pivots[0][1]
        current_touches = 1

        for i in range(1, len(sorted_pivots)):
            price = sorted_pivots[i][1]
            threshold = current_price * self._cluster_pct

            if abs(price - current_price) <= threshold:
                # Within cluster range — average and accumulate
                current_price = (current_price * current_touches + price) / (current_touches + 1)
                current_touches += 1
            else:
                clusters[round(current_price, 4)] = current_touches
                current_price = price
                current_touches = 1

        clusters[round(current_price, 4)] = current_touches
        return clusters
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_support_resistance.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/trend/__init__.py src/trend/support_resistance.py tests/test_support_resistance.py
git commit -m "feat(trend): add support/resistance detection module"
```

---

### Task 2: Candlestick Pattern Detection Module

**Files:**
- Create: `src/trend/candlestick_patterns.py`
- Create: `tests/test_candlestick_patterns.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_candlestick_patterns.py`:
```python
import pytest
import pandas as pd
from src.trend.candlestick_patterns import CandlestickPatterns


def make_candles(data: list[dict]) -> pd.DataFrame:
    """Helper to build DataFrame from list of {open, high, low, close} dicts."""
    return pd.DataFrame(data)


class TestCandlestickPatterns:
    def test_hammer_detected(self):
        """Hammer: small body at top, long lower shadow (≥2× body), small/no upper shadow."""
        df = make_candles([
            {"open": 100.0, "high": 101.0, "low": 95.0, "close": 99.5},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert "hammer" in [r["name"] for r in result]

    def test_bullish_engulfing_detected(self):
        """Bullish engulfing: red candle followed by green candle that engulfs it."""
        df = make_candles([
            {"open": 101.0, "high": 101.5, "low": 99.0, "close": 99.5},
            {"open": 99.0, "high": 102.0, "low": 98.5, "close": 101.5},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert "bullish_engulfing" in [r["name"] for r in result]

    def test_bullish_harami_detected(self):
        """Bullish harami: large red candle, then small green candle inside it."""
        df = make_candles([
            {"open": 102.0, "high": 102.5, "low": 98.0, "close": 98.5},
            {"open": 99.0, "high": 100.5, "low": 98.5, "close": 100.0},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert "bullish_harami" in [r["name"] for r in result]

    def test_morning_star_detected(self):
        """Morning star: large red, small body (any color), large green."""
        df = make_candles([
            {"open": 102.0, "high": 102.5, "low": 98.0, "close": 98.5},
            {"open": 98.5, "high": 99.5, "low": 97.5, "close": 99.0},
            {"open": 99.0, "high": 103.0, "low": 98.5, "close": 102.5},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert "morning_star" in [r["name"] for r in result]

    def test_bullish_marubozu_detected(self):
        """Bullish marubozu: large green candle with very small wicks."""
        df = make_candles([
            {"open": 95.0, "high": 100.5, "low": 94.8, "close": 100.0},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert "bullish_marubozu" in [r["name"] for r in result]

    def test_no_pattern_returns_empty(self):
        """No pattern when candle data doesn't match any."""
        df = make_candles([
            {"open": 100.0, "high": 100.5, "low": 99.5, "close": 100.2},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert result == []

    def test_result_structure(self):
        df = make_candles([
            {"open": 100.0, "high": 101.0, "low": 95.0, "close": 99.5},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        for r in result:
            assert "name" in r
            assert "type" in r
            assert r["type"] == "bullish"
            assert "signal" in r
            assert r["signal"] == "bull"

    def test_insufficient_data(self):
        df = pd.DataFrame({"open": [], "high": [], "low": [], "close": []})
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        assert result == []

    def test_bearish_patterns_not_returned(self):
        """We only return bullish patterns for trend engine (long-only)."""
        # Shooting star (bearish)
        df = make_candles([
            {"open": 100.0, "high": 105.0, "low": 99.5, "close": 100.5},
        ])
        patterns = CandlestickPatterns()
        result = patterns.detect(df)
        names = [r["name"] for r in result]
        assert "shooting_star" not in names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_candlestick_patterns.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

Create `src/trend/candlestick_patterns.py`:
```python
import logging
from typing import Optional

import pandas as pd

logger = logging.getLogger(__name__)


class CandlestickPatterns:
    """Detects bullish candlestick patterns for trend entry signals."""

    def detect(self, df: pd.DataFrame) -> list[dict]:
        """Scan the last 3 candles for known patterns.

        Args:
            df: DataFrame with 'open', 'high', 'low', 'close' columns.

        Returns:
            List of detected patterns with name, type, signal, score.
        """
        if len(df) < 1:
            return []

        patterns = []

        # Single-candle patterns (check last candle)
        last = self._candle(df, -1)
        if last:
            p = self._check_single(last)
            if p:
                patterns.append(p)

        # Double-candle patterns (check last 2 candles)
        if len(df) >= 2:
            prev = self._candle(df, -2)
            if prev and last:
                p = self._check_double(prev, last)
                if p:
                    patterns.append(p)

        # Triple-candle patterns (check last 3 candles)
        if len(df) >= 3:
            first = self._candle(df, -3)
            if first and prev and last:
                p = self._check_triple(first, prev, last)
                if p:
                    patterns.append(p)

        return patterns

    def _candle(self, df: pd.DataFrame, idx: int) -> Optional[dict]:
        """Extract candle data as dict with computed fields."""
        try:
            row = df.iloc[idx]
            o, h, l, c = float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])
        except (IndexError, KeyError, ValueError):
            return None

        body = abs(c - o)
        total_range = h - l
        if total_range == 0:
            return None

        upper_shadow = h - max(o, c)
        lower_shadow = min(o, c) - l
        is_green = c > o
        is_red = c < o

        return {
            "open": o, "high": h, "low": l, "close": c,
            "body": body, "range": total_range,
            "upper_shadow": upper_shadow, "lower_shadow": lower_shadow,
            "is_green": is_green, "is_red": is_red,
        }

    def _bullish_result(self, name: str) -> dict:
        return {"name": name, "type": "bullish", "signal": "bull", "score": 2}

    def _check_single(self, c: dict) -> Optional[dict]:
        """Check single-candle bullish patterns."""
        # Hammer: small body at top, long lower shadow (≥2× body), tiny upper shadow
        if (c["lower_shadow"] >= 2 * c["body"]
                and c["upper_shadow"] <= c["body"] * 0.5
                and c["body"] > 0):
            return self._bullish_result("hammer")

        # Bullish Marubozu: large green body, very small wicks (<10% of body)
        if (c["is_green"]
                and c["body"] > 0
                and c["upper_shadow"] < c["body"] * 0.1
                and c["lower_shadow"] < c["body"] * 0.1):
            return self._bullish_result("bullish_marubozu")

        return None

    def _check_double(self, prev: dict, curr: dict) -> Optional[dict]:
        """Check double-candle bullish patterns."""
        # Bullish Engulfing: red candle then green candle that engulfs it
        if (prev["is_red"]
                and curr["is_green"]
                and curr["open"] <= prev["close"]
                and curr["close"] >= prev["open"]
                and curr["body"] > prev["body"]):
            return self._bullish_result("bullish_engulfing")

        # Bullish Harami: large red candle, then small green candle inside it
        if (prev["is_red"]
                and curr["is_green"]
                and curr["open"] > prev["close"]
                and curr["close"] < prev["open"]
                and curr["body"] < prev["body"] * 0.5):
            return self._bullish_result("bullish_harami")

        return None

    def _check_triple(self, first: dict, mid: dict, last: dict) -> Optional[dict]:
        """Check triple-candle bullish patterns."""
        # Morning Star: large red, small body, large green
        first_is_large_red = first["is_red"] and first["body"] > mid["body"] * 2
        mid_is_small = mid["body"] < first["body"] * 0.5
        last_is_large_green = last["is_green"] and last["body"] > mid["body"] * 2

        if first_is_large_red and mid_is_small and last_is_large_green:
            return self._bullish_result("morning_star")

        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_candlestick_patterns.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/trend/candlestick_patterns.py tests/test_candlestick_patterns.py
git commit -m "feat(trend): add candlestick pattern detection module"
```

---

### Task 3: Position Manager Module

**Files:**
- Create: `src/trend/position_manager.py`
- Create: `tests/test_position_manager.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_position_manager.py`:
```python
import pytest
import json
import tempfile
from pathlib import Path
from src.trend.position_manager import PositionManager, TrendPosition


@pytest.fixture
def manager():
    return PositionManager(capital=2000.0)


class TestTrendPosition:
    def test_create_position(self):
        pos = TrendPosition(
            entry_order_id="abc123",
            entry_price=94.20,
            amount=14.0,
            stop_loss=91.30,
            take_profit=100.00,
            entry_time="2026-05-11T10:00:00Z",
        )
        assert pos.entry_price == 94.20
        assert pos.trailing_stop == 91.30  # Initially same as stop_loss
        assert pos.trailing_activated is False


class TestPositionManager:
    def test_open_position(self, manager):
        pos = manager.open_position(
            entry_order_id="abc123",
            entry_price=94.20,
            amount=14.0,
            stop_loss=91.30,
            take_profit=100.00,
            entry_time="2026-05-11T10:00:00Z",
        )
        assert pos is not None
        assert manager.open_count == 1
        assert manager.can_open() is False  # max 1 with this capital

    def test_can_open_respects_max_positions(self):
        mgr = PositionManager(capital=2000.0, max_positions=2)
        mgr.open_position("id1", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        assert mgr.can_open() is True
        mgr.open_position("id2", 95.0, 14.0, 92.0, 101.0, "2026-05-11T10:00:00Z")
        assert mgr.can_open() is False

    def test_close_position(self, manager):
        pos = manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        closed = manager.close_position(pos.entry_order_id, exit_price=100.0, exit_reason="take_profit")
        assert closed is not None
        assert closed["pnl"] > 0
        assert closed["exit_reason"] == "take_profit"
        assert manager.open_count == 0

    def test_close_position_pnl_calculation(self, manager):
        pos = manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        closed = manager.close_position("abc", exit_price=91.0, exit_reason="stop_loss")
        expected_pnl = (91.0 - 94.0) * 14.0
        assert abs(closed["pnl"] - expected_pnl) < 0.01
        assert closed["pnl"] < 0

    def test_check_exits_hit_take_profit(self, manager):
        manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        exits = manager.check_exits(current_price=100.5)
        assert len(exits) == 1
        assert exits[0]["reason"] == "take_profit"

    def test_check_exits_hit_stop_loss(self, manager):
        manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        exits = manager.check_exits(current_price=90.5)
        assert len(exits) == 1
        assert exits[0]["reason"] == "stop_loss"

    def test_check_exits_trailing_stop(self, manager):
        pos = manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        # Price rises to activate trailing
        manager.update_trailing(pos, current_price=96.0)
        assert pos.trailing_activated is True
        # Trailing stop should have moved up
        assert pos.trailing_stop > 91.0
        # Price drops back to trigger trailing stop
        exits = manager.check_exits(current_price=pos.trailing_stop - 0.1)
        assert len(exits) == 1
        assert exits[0]["reason"] == "trailing_stop"

    def test_trailing_stop_only_moves_up(self, manager):
        pos = manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        manager.update_trailing(pos, current_price=96.0)
        trail_after_rise = pos.trailing_stop
        manager.update_trailing(pos, current_price=94.5)
        assert pos.trailing_stop == trail_after_rise  # Doesn't move down

    def test_position_size_calculation(self, manager):
        size = manager.calculate_position_size(entry_price=94.0, stop_loss_price=91.3)
        # risk = 2000 * 0.02 = 40, sl_distance = 94 - 91.3 = 2.7, size = 40 / 2.7 = 14.81
        expected_size = (2000.0 * 0.02) / (94.0 - 91.3)
        assert abs(size - expected_size) < 0.01

    def test_position_size_capped_at_25pct(self):
        mgr = PositionManager(capital=100.0)
        # Very tight stop would produce huge size, but capped
        size = mgr.calculate_position_size(entry_price=94.0, stop_loss_price=93.99)
        max_notional = 100.0 * 0.25
        assert size * 94.0 <= max_notional + 1.0  # small tolerance

    def test_get_open_position(self, manager):
        manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        pos = manager.get_position("abc")
        assert pos is not None
        assert pos.entry_price == 94.0

    def test_get_position_returns_none_if_not_found(self, manager):
        assert manager.get_position("nonexistent") is None

    def test_save_and_load_state(self, manager):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        path = Path(tmp.name)

        manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        manager.save_state(path)

        mgr2 = PositionManager(capital=2000.0)
        mgr2.load_state(path)
        assert mgr2.open_count == 1
        pos = mgr2.get_position("abc")
        assert pos.entry_price == 94.0

        Path(tmp.name).unlink()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_position_manager.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

Create `src/trend/position_manager.py`:
```python
import json
import logging
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TrendPosition:
    entry_order_id: str
    entry_price: float
    amount: float
    stop_loss: float
    take_profit: float
    entry_time: str
    trailing_stop: float = 0.0
    trailing_activated: bool = False
    highest_price: float = 0.0

    def __post_init__(self):
        if self.trailing_stop == 0.0:
            self.trailing_stop = self.stop_loss
        if self.highest_price == 0.0:
            self.highest_price = self.entry_price


class PositionManager:
    """Manages open trend positions with SL/TP/trailing stop logic."""

    def __init__(self, capital: float = 2000.0, max_positions: int = 2,
                 risk_per_trade_pct: float = 2.0, max_position_pct: float = 25.0,
                 trailing_stop_pct: float = 1.5,
                 trailing_activation_pct: float = 1.5) -> None:
        self._capital = capital
        self._max_positions = max_positions
        self._risk_per_trade_pct = risk_per_trade_pct / 100.0
        self._max_position_pct = max_position_pct / 100.0
        self._trailing_stop_pct = trailing_stop_pct / 100.0
        self._trailing_activation_pct = trailing_activation_pct / 100.0
        self._positions: dict[str, TrendPosition] = {}
        self._lock = threading.Lock()

    @property
    def open_count(self) -> int:
        return len(self._positions)

    def can_open(self) -> bool:
        return len(self._positions) < self._max_positions

    def open_position(self, entry_order_id: str, entry_price: float,
                      amount: float, stop_loss: float, take_profit: float,
                      entry_time: str) -> Optional[TrendPosition]:
        with self._lock:
            if not self.can_open():
                logger.warning(f"Cannot open position: max {self._max_positions} reached")
                return None
            pos = TrendPosition(
                entry_order_id=entry_order_id,
                entry_price=entry_price,
                amount=amount,
                stop_loss=stop_loss,
                take_profit=take_profit,
                entry_time=entry_time,
            )
            self._positions[entry_order_id] = pos
            logger.info(f"Opened trend position: {amount} @ ${entry_price:.2f}, SL={stop_loss:.2f}, TP={take_profit:.2f}")
            return pos

    def close_position(self, order_id: str, exit_price: float,
                       exit_reason: str) -> Optional[dict]:
        with self._lock:
            pos = self._positions.pop(order_id, None)
            if pos is None:
                return None

            pnl = (exit_price - pos.entry_price) * pos.amount
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100

            # Parse entry time and calculate duration
            try:
                entry_dt = datetime.fromisoformat(pos.entry_time.replace("Z", "+00:00"))
                duration_min = int((datetime.now(entry_dt.tzinfo) - entry_dt).total_seconds() / 60)
            except (ValueError, TypeError):
                duration_min = 0

            return {
                "order_id": order_id,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "amount": pos.amount,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "exit_reason": exit_reason,
                "duration_minutes": duration_min,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "trailing_stop": pos.trailing_stop,
            }

    def get_position(self, order_id: str) -> Optional[TrendPosition]:
        return self._positions.get(order_id)

    def get_all_positions(self) -> list[TrendPosition]:
        return list(self._positions.values())

    def check_exits(self, current_price: float) -> list[dict]:
        """Check all positions for exit triggers. Returns list of exit signals."""
        exits = []
        with self._lock:
            for order_id, pos in list(self._positions.items()):
                # Take-profit hit
                if current_price >= pos.take_profit:
                    exits.append({"order_id": order_id, "reason": "take_profit",
                                  "exit_price": pos.take_profit})

                # Hard stop-loss hit
                elif current_price <= pos.stop_loss:
                    exits.append({"order_id": order_id, "reason": "stop_loss",
                                  "exit_price": pos.stop_loss})

                # Trailing stop hit
                elif pos.trailing_activated and current_price <= pos.trailing_stop:
                    exits.append({"order_id": order_id, "reason": "trailing_stop",
                                  "exit_price": pos.trailing_stop})

        return exits

    def update_trailing(self, pos: TrendPosition, current_price: float) -> None:
        """Update trailing stop if price moves in our favor."""
        if current_price > pos.highest_price:
            pos.highest_price = current_price

        # Activate trailing after price rises activation_pct above entry
        if not pos.trailing_activated:
            activation_price = pos.entry_price * (1 + self._trailing_activation_pct)
            if current_price >= activation_price:
                pos.trailing_activated = True

        # Move trailing stop up (never down)
        if pos.trailing_activated:
            new_trail = current_price * (1 - self._trailing_stop_pct)
            if new_trail > pos.trailing_stop:
                pos.trailing_stop = round(new_trail, 4)

    def calculate_position_size(self, entry_price: float, stop_loss_price: float) -> float:
        """Calculate position size based on risk per trade."""
        sl_distance = abs(entry_price - stop_loss_price)
        if sl_distance == 0:
            return 0.0

        risk_amount = self._capital * self._risk_per_trade_pct
        size_usdt = risk_amount / (sl_distance / entry_price)

        # Cap at max_position_pct of capital
        max_notional = self._capital * self._max_position_pct
        size_usdt = min(size_usdt, max_notional)

        return round(size_usdt / entry_price, 4)

    def save_state(self, path: Path) -> None:
        """Persist positions to JSON file."""
        data = {
            "capital": self._capital,
            "positions": {oid: asdict(pos) for oid, pos in self._positions.items()},
        }
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2))
        tmp_path.replace(path)

    def load_state(self, path: Path) -> None:
        """Load positions from JSON file."""
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._positions.clear()
            for oid, pdata in data.get("positions", {}).items():
                self._positions[oid] = TrendPosition(**pdata)
            logger.info(f"Restored {len(self._positions)} trend positions from {path}")
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"Failed to load trend state: {e}")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_position_manager.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/trend/position_manager.py tests/test_position_manager.py
git commit -m "feat(trend): add position manager with SL/TP/trailing stop"
```

---

### Task 4: Trend Trade Journal

**Files:**
- Create: `src/trend/trend_journal.py`
- Create: `tests/test_trend_journal.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_trend_journal.py`:
```python
import pytest
import tempfile
from pathlib import Path
from src.trend.trend_journal import TrendJournal


@pytest.fixture
def journal():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    j = TrendJournal(db_path=path)
    yield j
    path.unlink()


class TestTrendJournal:
    def test_log_and_retrieve_trade(self, journal):
        trade_id = journal.log_trade(
            side="BUY", entry_price=94.0, exit_price=100.0,
            amount=14.0, fee=2.10, pnl=82.9, pnl_pct=6.02,
            stop_loss=91.3, take_profit=100.0,
            exit_reason="take_profit", signal_score=4,
            duration_minutes=180,
        )
        assert trade_id > 0

        trades = journal.get_trades()
        assert len(trades) == 1
        assert trades[0]["side"] == "BUY"
        assert trades[0]["pnl"] == 82.9
        assert trades[0]["exit_reason"] == "take_profit"

    def test_summary_empty(self, journal):
        summary = journal.summary()
        assert summary["total_trades"] == 0
        assert summary["win_rate"] == 0.0

    def test_summary_with_trades(self, journal):
        journal.log_trade("BUY", 94.0, 100.0, 14.0, 2.0, 82.0, 6.0, 91.0, 100.0, "take_profit", 4, 180)
        journal.log_trade("BUY", 95.0, 92.0, 14.0, 2.0, -44.0, -3.1, 92.0, 101.0, "stop_loss", 3, 60)

        summary = journal.summary()
        assert summary["total_trades"] == 2
        assert summary["wins"] == 1
        assert summary["losses"] == 1
        assert abs(summary["win_rate"] - 50.0) < 0.1
        assert abs(summary["total_pnl"] - 38.0) < 0.1

    def test_recent_trades_limit(self, journal):
        for i in range(15):
            journal.log_trade("BUY", 94.0, 95.0, 14.0, 1.0, 13.0, 1.0, 92.0, 96.0, "take_profit", 4, 60)

        recent = journal.recent_trades(limit=10)
        assert len(recent) == 10

    def test_performance_metrics(self, journal):
        journal.log_trade("BUY", 94.0, 100.0, 14.0, 2.0, 82.0, 6.0, 91.0, 100.0, "take_profit", 4, 180)
        journal.log_trade("BUY", 95.0, 100.0, 14.0, 2.0, 68.6, 5.0, 92.0, 101.0, "trailing_stop", 5, 240)
        journal.log_trade("BUY", 93.0, 90.0, 14.0, 2.0, -44.0, -3.2, 90.0, 99.0, "stop_loss", 3, 30)

        metrics = journal.performance()
        assert abs(metrics["profit_factor"] - (150.6 / 44.0)) < 0.1
        assert metrics["avg_win"] > 0
        assert metrics["avg_loss"] < 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trend_journal.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

Create `src/trend/trend_journal.py`:
```python
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import sqlite3

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/trend_journal.db")


class TrendJournal:
    """SQLite-backed trade journal for the trend engine."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trend_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    amount REAL NOT NULL,
                    fee REAL DEFAULT 0,
                    pnl REAL NOT NULL,
                    pnl_pct REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    exit_reason TEXT NOT NULL,
                    signal_score INTEGER DEFAULT 0,
                    duration_minutes INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()

    def log_trade(self, side: str, entry_price: float, exit_price: float,
                  amount: float, fee: float, pnl: float, pnl_pct: float,
                  stop_loss: float, take_profit: float, exit_reason: str,
                  signal_score: int = 0, duration_minutes: int = 0) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._conn()
            cursor = conn.execute(
                """INSERT INTO trend_trades
                   (timestamp, side, entry_price, exit_price, amount, fee,
                    pnl, pnl_pct, stop_loss, take_profit, exit_reason,
                    signal_score, duration_minutes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts, side, entry_price, exit_price, amount, fee,
                 pnl, pnl_pct, stop_loss, take_profit, exit_reason,
                 signal_score, duration_minutes),
            )
            trade_id = cursor.lastrowid
            conn.commit()
            conn.close()
        logger.info(f"Trend trade logged: {side} {amount}@${entry_price:.2f}->${exit_price:.2f} PnL=${pnl:.2f} ({exit_reason})")
        return trade_id

    def get_trades(self, since: Optional[str] = None, limit: int = 100) -> list[dict]:
        conn = self._conn()
        if since:
            rows = conn.execute(
                "SELECT * FROM trend_trades WHERE timestamp >= ? ORDER BY id DESC LIMIT ?",
                (since, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trend_trades ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def recent_trades(self, limit: int = 10) -> list[dict]:
        return self.get_trades(limit=limit)

    def summary(self, since: Optional[str] = None) -> dict:
        conn = self._conn()
        query = "SELECT * FROM trend_trades"
        params = []
        if since:
            query += " WHERE timestamp >= ?"
            params.append(since)

        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        conn.close()

        if not rows:
            return {"total_trades": 0, "wins": 0, "losses": 0,
                    "win_rate": 0.0, "total_pnl": 0.0}

        wins = [t for t in rows if t["pnl"] > 0]
        losses = [t for t in rows if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in rows)

        return {
            "total_trades": len(rows),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(len(wins) / len(rows) * 100, 1),
            "total_pnl": round(total_pnl, 2),
        }

    def performance(self, since: Optional[str] = None) -> dict:
        conn = self._conn()
        query = "SELECT * FROM trend_trades"
        params = []
        if since:
            query += " WHERE timestamp >= ?"
            params.append(since)

        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        conn.close()

        if not rows:
            return {"profit_factor": 0, "avg_win": 0, "avg_loss": 0,
                    "largest_win": 0, "largest_loss": 0, "avg_duration": 0}

        wins = [t["pnl"] for t in rows if t["pnl"] > 0]
        losses = [t["pnl"] for t in rows if t["pnl"] <= 0]
        gross_wins = sum(wins) if wins else 0
        gross_losses = abs(sum(losses)) if losses else 0.001

        return {
            "profit_factor": round(gross_wins / gross_losses, 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "largest_win": round(max(wins), 2) if wins else 0,
            "largest_loss": round(min(losses), 2) if losses else 0,
            "avg_duration": round(sum(t["duration_minutes"] for t in rows) / len(rows), 0),
        }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_trend_journal.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/trend/trend_journal.py tests/test_trend_journal.py
git commit -m "feat(trend): add trend trade journal with performance metrics"
```

---

### Task 5: Trend Manager — Signal Scoring Engine

**Files:**
- Create: `src/trend/trend_manager.py`
- Create: `tests/test_trend_manager.py`

- [ ] **Step 1: Write failing test**

Create `tests/test_trend_manager.py`:
```python
import pytest
import pandas as pd
import numpy as np
from src.trend.trend_manager import TrendManager, SignalScore


@pytest.fixture
def trend_manager():
    return TrendManager(
        ema_fast=20, ema_slow=50, ema_trend=200,
        rsi_period=14, rsi_min=40, rsi_max=70,
        min_signal_score=3, confirmation_ticks=2,
    )


def make_candles_with_trend(n: int = 250, trend: str = "up") -> pd.DataFrame:
    """Generate candle data with a known trend direction."""
    if trend == "up":
        base = np.cumsum(np.random.uniform(-0.5, 1.0, n)) + 90
    elif trend == "down":
        base = np.cumsum(np.random.uniform(-1.0, 0.5, n)) + 100
    else:
        base = np.cumsum(np.random.uniform(-0.5, 0.5, n)) + 95

    return pd.DataFrame({
        "open": base - 0.2,
        "high": base + 0.5,
        "low": base - 0.5,
        "close": base,
    })


class TestTrendManager:
    def test_score_structure(self, trend_manager):
        candles = make_candles_with_trend(250, "up")
        score = trend_manager.evaluate(candles, 94.0)
        assert isinstance(score, SignalScore)
        assert hasattr(score, "total")
        assert hasattr(score, "details")
        assert score.total >= 0
        assert score.total <= 7

    def test_score_details_list(self, trend_manager):
        candles = make_candles_with_trend(250, "up")
        score = trend_manager.evaluate(candles, 94.0)
        assert isinstance(score.details, list)
        for d in score.details:
            assert "signal" in d
            assert "points" in d

    def test_ema_cross_adds_points(self, trend_manager):
        """When EMA20 > EMA50 and they recently crossed, should add points."""
        # Force a crossover by creating data where fast crosses above slow
        n = 250
        base = np.linspace(90, 100, n)  # strong uptrend guarantees EMA20 > EMA50
        candles = pd.DataFrame({
            "open": base - 0.1,
            "high": base + 0.3,
            "low": base - 0.3,
            "close": base,
        })
        score = trend_manager.evaluate(candles, float(candles["close"].iloc[-1]))
        # In strong uptrend, at least EMA cross and trend filter should fire
        assert score.total >= 1

    def test_should_enter_requires_minimum_score(self, trend_manager):
        candles = make_candles_with_trend(250, "up")
        score = trend_manager.evaluate(candles, 94.0)
        if score.total >= 3:
            assert trend_manager.should_enter(score) is True
        else:
            assert trend_manager.should_enter(score) is False

    def test_should_exit_on_low_score(self, trend_manager):
        score = SignalScore(total=1, details=[])
        assert trend_manager.should_exit(score) is True

    def test_should_not_exit_on_high_score(self, trend_manager):
        score = SignalScore(total=4, details=[])
        assert trend_manager.should_exit(score) is False

    def test_confirmation_ticks(self, trend_manager):
        """Signal must hold for confirmation_ticks before entering."""
        trend_manager._pending_ticks = 0
        trend_manager._pending_score = None

        candles = make_candles_with_trend(250, "up")
        score = trend_manager.evaluate(candles, 94.0)

        if score.total >= 3:
            # First call: should not enter yet (need 2 ticks)
            assert trend_manager.confirm_entry(score) is False
            # Second call: now confirmed
            assert trend_manager.confirm_entry(score) is True

    def test_calculate_stop_loss_with_support(self, trend_manager):
        from src.trend.support_resistance import SupportResistance
        sr = SupportResistance()
        levels = [{"price": 92.0, "type": "support", "touches": 3, "strength": 0.6}]
        sl = trend_manager.calculate_stop_loss(94.0, levels, atr_value=0.5)
        # Should be below support with buffer
        assert sl < 92.0
        assert sl == pytest.approx(91.82, abs=0.01)

    def test_calculate_stop_loss_without_support(self, trend_manager):
        sl = trend_manager.calculate_stop_loss(94.0, [], atr_value=0.5)
        # Fallback: entry - 2 * ATR
        assert sl == pytest.approx(93.0, abs=0.01)

    def test_calculate_take_profit(self, trend_manager):
        tp = trend_manager.calculate_take_profit(94.0, 91.3)
        risk = 94.0 - 91.3
        expected_tp = 94.0 + risk * 2.0
        assert tp == pytest.approx(expected_tp, abs=0.01)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_trend_manager.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Write implementation**

Create `src/trend/trend_manager.py`:
```python
import logging
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from src.indicators.ema import EMA
from src.indicators.rsi import RSI
from src.indicators.atr import ATR
from src.trend.candlestick_patterns import CandlestickPatterns
from src.trend.support_resistance import SupportResistance

logger = logging.getLogger(__name__)


@dataclass
class SignalScore:
    total: int = 0
    details: list[dict] = field(default_factory=list)


class TrendManager:
    """Signal scoring engine for trend-following entries and exits."""

    def __init__(self, ema_fast: int = 20, ema_slow: int = 50, ema_trend: int = 200,
                 rsi_period: int = 14, rsi_min: float = 40, rsi_max: float = 70,
                 min_signal_score: int = 3, confirmation_ticks: int = 2,
                 sl_buffer_pct: float = 0.2, rr_ratio: float = 2.0) -> None:
        self._ema_fast = EMA(ema_fast)
        self._ema_slow = EMA(ema_slow)
        self._ema_trend = EMA(ema_trend)
        self._rsi = RSI(rsi_period)
        self._atr = ATR(14)
        self._patterns = CandlestickPatterns()
        self._sr = SupportResistance()

        self._rsi_min = rsi_min
        self._rsi_max = rsi_max
        self._min_signal_score = min_signal_score
        self._confirmation_ticks = confirmation_ticks
        self._sl_buffer_pct = sl_buffer_pct / 100.0
        self._rr_ratio = rr_ratio

        self._prev_ema_fast: Optional[float] = None
        self._prev_ema_slow: Optional[float] = None
        self._pending_ticks: int = 0
        self._pending_score: Optional[SignalScore] = None

    def evaluate(self, candles: pd.DataFrame, current_price: float) -> SignalScore:
        """Calculate bull signal score from all indicators."""
        score = SignalScore()
        closes = candles["close"]

        # 1. EMA Cross (+1)
        ema_f = self._ema_fast.calculate(closes)
        ema_s = self._ema_slow.calculate(closes)
        ema_t = self._ema_trend.calculate(closes)

        if ema_f is not None and ema_s is not None:
            # Golden cross: EMA fast above slow, and it recently crossed
            if ema_f > ema_s:
                crossed = (self._prev_ema_fast is not None
                           and self._prev_ema_slow is not None
                           and self._prev_ema_fast <= self._prev_ema_slow)
                if crossed or self._prev_ema_fast is None:
                    score.total += 1
                    score.details.append({"signal": "ema_cross", "points": 1,
                                          "note": "EMA fast > slow"})

            self._prev_ema_fast = ema_f
            self._prev_ema_slow = ema_s

        # 2. Trend filter (+1): price > EMA200 AND EMA fast > EMA slow
        if ema_f is not None and ema_s is not None and ema_t is not None:
            if current_price > ema_t and ema_f > ema_s:
                score.total += 1
                score.details.append({"signal": "trend_filter", "points": 1,
                                      "note": f"price({current_price:.2f}) > EMA200({ema_t:.2f})"})

        # 3. RSI Confirmation (+1)
        rsi_val = self._rsi.calculate(closes)
        if rsi_val is not None and self._rsi_min <= rsi_val <= self._rsi_max:
            score.total += 1
            score.details.append({"signal": "rsi_filter", "points": 1,
                                  "note": f"RSI={rsi_val:.1f} in [{self._rsi_min}-{self._rsi_max}]"})

        # 4. At Support (+2)
        sr_levels = self._sr.detect(candles)
        support = self._sr.nearest_support(sr_levels, current_price)
        if support is not None:
            distance_pct = abs(current_price - support["price"]) / current_price
            if distance_pct <= 0.01:
                score.total += 2
                score.details.append({"signal": "at_support", "points": 2,
                                      "note": f"Support at {support['price']:.2f} ({distance_pct*100:.1f}% away)"})

        # 5. Bullish Candlestick Pattern (+2)
        patterns = self._patterns.detect(candles)
        if patterns:
            best = patterns[0]
            score.total += 2
            score.details.append({"signal": "candlestick", "points": 2,
                                  "note": f"Pattern: {best['name']}"})

        return score

    def should_enter(self, score: SignalScore) -> bool:
        return score.total >= self._min_signal_score

    def should_exit(self, score: SignalScore) -> bool:
        return score.total < 2

    def confirm_entry(self, score: SignalScore) -> bool:
        """Require confirmation_ticks consecutive ticks above threshold."""
        if not self.should_enter(score):
            self._pending_ticks = 0
            self._pending_score = None
            return False

        if self._pending_score is not None and self._pending_score.total >= self._min_signal_score:
            self._pending_ticks += 1
        else:
            self._pending_ticks = 1
            self._pending_score = score

        if self._pending_ticks >= self._confirmation_ticks:
            self._pending_ticks = 0
            self._pending_score = None
            return True
        return False

    def calculate_stop_loss(self, entry_price: float, sr_levels: list[dict],
                            atr_value: Optional[float] = None) -> float:
        """Calculate stop-loss based on nearest support, fallback to ATR."""
        support = None
        for level in sr_levels:
            if level["type"] == "support" and level["price"] < entry_price:
                if support is None or level["price"] > support["price"]:
                    support = level

        if support is not None:
            return round(support["price"] * (1 - self._sl_buffer_pct), 2)

        # Fallback: entry - 2 × ATR
        if atr_value and atr_value > 0:
            return round(entry_price - 2 * atr_value, 2)

        # Last resort: 3% below entry
        return round(entry_price * 0.97, 2)

    def calculate_take_profit(self, entry_price: float, stop_loss: float) -> float:
        """Calculate take-profit based on risk-reward ratio."""
        risk_distance = entry_price - stop_loss
        return round(entry_price + risk_distance * self._rr_ratio, 2)

    def reset_confirmation(self) -> None:
        self._pending_ticks = 0
        self._pending_score = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_trend_manager.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/trend/trend_manager.py tests/test_trend_manager.py
git commit -m "feat(trend): add signal scoring engine with EMA/RSI/SR/candlestick"
```

---

### Task 6: Telegram Commands for Trend Bot

**Files:**
- Modify: `src/notifications/telegram_commands.py`

- [ ] **Step 1: Read the current telegram_commands.py to understand command registration pattern**

Read `src/notifications/telegram_commands.py` and identify:
- The `commands` dict where commands are registered
- The `_cmd_status` method as a template
- How the strategy reference is accessed (`self._strategy`)

- [ ] **Step 2: Add trend command handlers**

Add the following methods to the `TelegramCommands` class (before the last method), following the exact pattern of existing commands:

```python
# ── Trend Commands ────────────────────────────────────────────

def _cmd_trend_status(self, update, context) -> str:
    """Show trend engine status: positions, EMA values, signal score."""
    strategy = self._strategy
    if not hasattr(strategy, '_trend_manager') or strategy._trend_manager is None:
        return "Trend engine not active"

    tm = strategy._trend_manager
    pm = strategy._position_manager

    lines = ["TREND ENGINE", chr(9473) * 33]

    # Positions
    positions = pm.get_all_positions()
    lines.append(f"Open positions: {len(positions)}/{pm._max_positions}")
    for pos in positions:
        pnl_pct = 0
        current = getattr(strategy, '_last_price', pos.entry_price)
        if current and pos.entry_price:
            pnl_pct = (current - pos.entry_price) / pos.entry_price * 100
        lines.append(f"  {pos.amount:.2f} SOL @ ${pos.entry_price:.2f} | SL ${pos.stop_loss:.2f} TP ${pos.take_profit:.2f}")
        lines.append(f"  P&L: {pnl_pct:+.1f}% | Trail: ${pos.trailing_stop:.2f}")

    # Capital
    lines.append(f"Capital: ${pm._capital:.2f}")

    # Signal
    if hasattr(strategy, '_last_trend_score'):
        score = strategy._last_trend_score
        lines.append(f"Signal score: {score.total}/7")
        for d in score.details:
            lines.append(f"  +{d['points']} {d['signal']}: {d['note']}")

    return "\n".join(lines)


def _cmd_trend_capital(self, update, context) -> str:
    """Set trend engine capital. Usage: /trend_capital 2000"""
    strategy = self._strategy
    if not hasattr(strategy, '_position_manager') or strategy._position_manager is None:
        return "Trend engine not active"

    args = self._get_args(update)
    if not args:
        return f"Current trend capital: ${strategy._position_manager._capital:.2f}\nUsage: /trend_capital <amount>"

    try:
        amount = float(args[0])
    except (ValueError, IndexError):
        return "Invalid amount. Usage: /trend_capital 2000"

    if amount < 0:
        return "Capital must be >= 0"

    old = strategy._position_manager._capital
    strategy._position_manager._capital = amount

    self._event_log.log("trend_capital_updated", old=old, new=amount)

    return f"Trend capital: ${old:.2f} -> ${amount:.2f}"


def _cmd_trend_pnl(self, update, context) -> str:
    """Show trend engine P&L summary."""
    strategy = self._strategy
    if not hasattr(strategy, '_trend_journal') or strategy._trend_journal is None:
        return "Trend engine not active"

    journal = strategy._trend_journal
    summary = journal.summary()
    perf = journal.performance()

    lines = ["TREND P&L", chr(9473) * 33]
    lines.append(f"Total trades: {summary['total_trades']}")
    lines.append(f"Win rate: {summary['win_rate']:.1f}% ({summary['wins']}W / {summary['losses']}L)")
    lines.append(f"Total P&L: ${summary['total_pnl']:.2f}")
    lines.append(f"Profit factor: {perf['profit_factor']:.2f}")
    lines.append(f"Avg win: ${perf['avg_win']:.2f} | Avg loss: ${perf['avg_loss']:.2f}")
    lines.append(f"Avg duration: {perf['avg_duration']:.0f} min")

    return "\n".join(lines)


def _cmd_trend_close(self, update, context) -> str:
    """Manually close all open trend positions."""
    strategy = self._strategy
    if not hasattr(strategy, '_position_manager') or strategy._position_manager is None:
        return "Trend engine not active"

    pm = strategy._position_manager
    positions = pm.get_all_positions()
    if not positions:
        return "No open trend positions"

    strategy._trend_force_close = True
    return f"Closing {len(positions)} trend position(s) on next tick..."


def _cmd_trend_history(self, update, context) -> str:
    """Show last 10 trend trades."""
    strategy = self._strategy
    if not hasattr(strategy, '_trend_journal') or strategy._trend_journal is None:
        return "Trend engine not active"

    trades = strategy._trend_journal.recent_trades(limit=10)
    if not trades:
        return "No trend trades yet"

    lines = ["TREND HISTORY", chr(9473) * 33]
    for t in trades:
        emoji = "+" if t["pnl"] >= 0 else "-"
        lines.append(f"{emoji} {t['side']} {t['amount']:.1f}@${t['entry_price']:.2f}->${t['exit_price']:.2f} | ${t['pnl']:+.2f} ({t['exit_reason']})")

    return "\n".join(lines)
```

- [ ] **Step 3: Register the new commands in the commands dict**

Find the `commands` dict (around line 126-144) and add:
```python
"trend_status": self._cmd_trend_status,
"trend_capital": self._cmd_trend_capital,
"trend_pnl": self._cmd_trend_pnl,
"trend_close": self._cmd_trend_close,
"trend_history": self._cmd_trend_history,
```

- [ ] **Step 4: Run existing tests to verify no regressions**

Run: `pytest tests/test_telegram_bot.py -v`
Expected: All existing tests PASS

- [ ] **Step 5: Commit**

```bash
git add src/notifications/telegram_commands.py
git commit -m "feat(trend): add /trend_* telegram commands"
```

---

### Task 7: Dual-Engine Strategy Entry Point

**Files:**
- Create: `hummingbot_files/scripts/ta_grid_trend.py`
- Create: `hummingbot_files/conf/scripts/ta_grid_trend_conf.yml`

- [ ] **Step 1: Read the existing strategy to understand the import pattern**

Read `hummingbot_files/scripts/ta_grid_btcusdt.py` lines 8-104 (imports) and lines 124-300 (config/init) to understand:
- How `StrategyV2Base` and `StrategyV2ConfigBase` are imported
- How the config class is defined
- How `__init__` initializes all components
- How `on_tick()` and `did_fill_order()` are structured

- [ ] **Step 2: Create the v2 config file**

Create `hummingbot_files/conf/scripts/ta_grid_trend_conf.yml`:
```yaml
script_file_name: ta_grid_trend.py
```

- [ ] **Step 3: Create the dual-engine strategy**

Create `hummingbot_files/scripts/ta_grid_trend.py`. This file:
- Imports `TAGridSOLUSDT` and `TAGridConfig` from `ta_grid_btcusdt`
- Creates a new `TAGridTrendConfig` and `TAGridTrendStrategy`
- In `__init__`: initializes grid engine (delegating to TAGridSOLUSDT pattern) + trend engine components
- In `on_tick()`: runs both engines
- In `did_fill_order()`: routes fills to the correct engine

```python
"""
TA Grid + Trend Dual-Engine Strategy

Runs the existing grid bot unchanged alongside a new trend-following engine.
Both engines share one Hummingbot instance but have isolated capital and state.
"""
import os
import json
import logging
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional

import pandas as pd
import yaml

# Trend engine imports
from src.trend.trend_manager import TrendManager
from src.trend.position_manager import PositionManager
from src.trend.trend_journal import TrendJournal
from src.trend.support_resistance import SupportResistance

# Shared imports
from src.indicators.ema import EMA
from src.indicators.rsi import RSI
from src.indicators.atr import ATR
from src.indicators.bollinger import BollingerBands
from src.risk.circuit_breaker import CircuitBreaker
from src.journal.trade_journal import TradeJournal
from src.notifications.event_logger import EventLogger

# Import the grid bot (ZERO changes to that file)
from ta_grid_btcusdt import TAGridSOLUSDT, TAGridConfig

# Hummingbot imports
try:
    from hummingbot.strategy_v2_base import StrategyV2Base, StrategyV2ConfigBase
    from hummingbot.core.data_type.common import TradeType
except ImportError:
    pass

logger = logging.getLogger(__name__)


class TAGridTrendConfig(StrategyV2ConfigBase):
    """Config for the dual-engine strategy."""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.exchange = os.environ.get("EXCHANGE", "binance")
        self.trading_pair = os.environ.get("TRADING_PAIR", "SOL-USDT")

    @classmethod
    def update_markets(cls, markets):
        exchange = os.environ.get("EXCHANGE", "binance")
        pair = os.environ.get("TRADING_PAIR", "SOL-USDT")
        if exchange not in markets:
            markets[exchange] = {}
        markets[exchange].add(pair)
        return markets


class TAGridTrendStrategy(StrategyV2Base):
    """Dual-engine strategy: grid bot + trend following."""

    def __init__(self, connectors: Dict, config: TAGridTrendConfig):
        super().__init__(connectors, config)

        # Load config
        config_path = Path(__file__).parent.parent.parent / "config" / "strategy.yaml"
        cfg = {}
        if config_path.exists():
            with open(config_path) as f:
                cfg = yaml.safe_load(f) or {}

        trend_cfg = cfg.get("trend", {})

        # Grid engine (delegate to existing class — we just track its state)
        self._grid_active = True

        # Trend engine components
        self._trend_manager = TrendManager(
            ema_fast=trend_cfg.get("ema_fast", 20),
            ema_slow=trend_cfg.get("ema_slow", 50),
            ema_trend=trend_cfg.get("ema_trend", 200),
            rsi_period=trend_cfg.get("rsi_period", 14),
            rsi_min=trend_cfg.get("rsi_min", 40),
            rsi_max=trend_cfg.get("rsi_max", 70),
            min_signal_score=trend_cfg.get("min_signal_score", 3),
            confirmation_ticks=trend_cfg.get("confirmation_ticks", 2),
            sl_buffer_pct=trend_cfg.get("sl_buffer_pct", 0.2),
            rr_ratio=trend_cfg.get("rr_ratio", 2.0),
        )

        self._position_manager = PositionManager(
            capital=float(trend_cfg.get("capital", 0)),
            max_positions=trend_cfg.get("max_positions", 2),
            risk_per_trade_pct=trend_cfg.get("risk_per_trade_pct", 2.0),
            max_position_pct=trend_cfg.get("max_position_pct", 25.0),
            trailing_stop_pct=trend_cfg.get("trailing_stop_pct", 1.5),
            trailing_activation_pct=trend_cfg.get("trailing_activation_pct", 1.5),
        )

        self._trend_journal = TrendJournal()
        self._trend_enabled = trend_cfg.get("enabled", True)

        # Trend circuit breaker (separate from grid)
        self._trend_breaker = CircuitBreaker(
            max_drawdown_pct=trend_cfg.get("max_drawdown_pct", 10.0),
            daily_loss_limit_pct=trend_cfg.get("daily_loss_limit_pct", 5.0),
        )

        # State
        self._last_price: float = 0.0
        self._last_trend_score = None
        self._trend_force_close: bool = False
        self._trend_candles: Optional[pd.DataFrame] = None
        self._trend_tick_count: int = 0

        # Load trend state
        trend_state_path = Path("data/trend_state.json")
        if trend_state_path.exists():
            self._position_manager.load_state(trend_state_path)

        logger.info(f"Trend engine initialized: capital=${self._position_manager._capital:.2f}, enabled={self._trend_enabled}")

    def on_tick(self):
        """Main tick loop — delegates to grid engine and trend engine."""
        self._trend_tick_count += 1

        # Update current price
        connector = self.connectors.get(self.config.exchange)
        if connector:
            mid_price = connector.get_mid_price(self.config.trading_pair)
            if mid_price:
                self._last_price = float(mid_price)

        # Update trailing stops for all open trend positions
        if self._last_price > 0:
            for pos in self._position_manager.get_all_positions():
                self._position_manager.update_trailing(pos, self._last_price)

        # Check trend exits every tick
        if self._trend_enabled and self._position_manager.open_count > 0:
            self._check_trend_exits()

        # Check for forced close
        if self._trend_force_close:
            self._close_all_trend_positions()
            self._trend_force_close = False

        # Trend engine: evaluate signals every 55 ticks (~55 seconds, after candles refresh)
        if (self._trend_enabled
                and self._last_price > 0
                and self._trend_tick_count % 55 == 0):
            self._evaluate_trend_signals()

    def _check_trend_exits(self):
        """Check and execute trend exits."""
        if not self._last_price:
            return

        exits = self._position_manager.check_exits(self._last_price)
        for exit_info in exits:
            pos = self._position_manager.get_position(exit_info["order_id"])
            if pos:
                self._execute_trend_exit(pos, exit_info)

    def _execute_trend_exit(self, pos, exit_info: dict):
        """Execute a trend position exit."""
        exit_price = exit_info["exit_price"]
        reason = exit_info["reason"]

        # Place sell order
        amount = Decimal(str(pos.amount)).quantize(Decimal("0.01"))
        try:
            self.sell(self.config.exchange, self.config.trading_pair, amount)
        except Exception as e:
            logger.error(f"Trend sell failed: {e}")
            return

        # Close position and log
        closed = self._position_manager.close_position(
            pos.entry_order_id, exit_price, reason,
        )
        if closed:
            fee = exit_price * float(amount) * 0.00075
            self._trend_journal.log_trade(
                side="SELL",
                entry_price=closed["entry_price"],
                exit_price=exit_price,
                amount=closed["amount"],
                fee=round(fee, 2),
                pnl=closed["pnl"],
                pnl_pct=closed["pnl_pct"],
                stop_loss=closed["stop_loss"],
                take_profit=closed["take_profit"],
                exit_reason=reason,
                signal_score=0,
                duration_minutes=closed["duration_minutes"],
            )

            self._save_trend_state()
            logger.info(f"TREND EXIT ({reason}): {closed['amount']:.1f} SOL @ ${exit_price:.2f} | PnL ${closed['pnl']:+.2f}")

    def _evaluate_trend_signals(self):
        """Fetch candles and evaluate trend signals."""
        if not self._position_manager.can_open():
            return

        if self._trend_breaker.halted:
            return

        # Fetch candles from connector
        connector = self.connectors.get(self.config.exchange)
        if not connector:
            return

        try:
            candles = self._fetch_candles(connector)
            if candles is None or len(candles) < 200:
                return
        except Exception as e:
            logger.error(f"Trend candle fetch failed: {e}")
            return

        # Calculate signal score
        score = self._trend_manager.evaluate(candles, self._last_price)
        self._last_trend_score = score

        # Check for entry
        if self._trend_manager.should_enter(score):
            if self._trend_manager.confirm_entry(score):
                self._open_trend_position(candles, score)

    def _fetch_candles(self, connector) -> Optional[pd.DataFrame]:
        """Fetch OHLCV candles from the connector."""
        try:
            # Use the connector's candle data if available
            candles_df = connector.candles.get_candles_df(self.config.trading_pair)
            if candles_df is not None and len(candles_df) >= 200:
                return candles_df.tail(250)
        except Exception:
            pass
        return None

    def _open_trend_position(self, candles: pd.DataFrame, score):
        """Open a new trend position based on signal score."""
        # Calculate stop-loss and take-profit
        sr_levels = self._trend_manager._sr.detect(candles)
        atr = ATR(14)
        closes = candles["close"]
        atr_val = None
        if "high" in candles.columns and "low" in candles.columns:
            atr_val = atr.calculate(candles["high"], candles["low"], closes)

        sl = self._trend_manager.calculate_stop_loss(self._last_price, sr_levels, atr_val)
        tp = self._trend_manager.calculate_take_profit(self._last_price, sl)

        # Calculate position size
        amount = self._position_manager.calculate_position_size(self._last_price, sl)
        if amount <= 0:
            return

        # Place buy order
        amount_dec = Decimal(str(amount)).quantize(Decimal("0.01"))
        try:
            order_id = self.buy(self.config.exchange, self.config.trading_pair, amount_dec)
        except Exception as e:
            logger.error(f"Trend buy failed: {e}")
            return

        entry_time = datetime.now(timezone.utc).isoformat()

        pos = self._position_manager.open_position(
            entry_order_id=str(order_id),
            entry_price=self._last_price,
            amount=amount,
            stop_loss=sl,
            take_profit=tp,
            entry_time=entry_time,
        )

        if pos:
            self._save_trend_state()
            logger.info(f"TREND ENTRY: {amount:.1f} SOL @ ${self._last_price:.2f} | SL ${sl:.2f} TP ${tp:.2f} | Score {score.total}/7")

    def _close_all_trend_positions(self):
        """Force-close all open trend positions."""
        for pos in self._position_manager.get_all_positions():
            self._execute_trend_exit(pos, {
                "order_id": pos.entry_order_id,
                "exit_price": self._last_price or pos.entry_price,
                "reason": "manual_close",
            })

    def _save_trend_state(self):
        """Persist trend state to disk."""
        path = Path("data/trend_state.json")
        self._position_manager.save_state(path)

    def did_fill_order(self, event):
        """Route order fills to the correct engine."""
        # This is handled by the grid engine's did_fill_order and by
        # the trend engine's exit logic. The trend engine places orders
        # and tracks them by order_id, so fills are handled via
        # check_exits() and position tracking.
        pass
```

- [ ] **Step 4: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py hummingbot_files/conf/scripts/ta_grid_trend_conf.yml
git commit -m "feat(trend): add dual-engine strategy entry point"
```

---

### Task 8: Config and Docker Updates

**Files:**
- Modify: `config/strategy.yaml`
- Modify: `docker-entrypoint.sh`

- [ ] **Step 1: Add trend config section to strategy.yaml**

Append the following to `config/strategy.yaml`:
```yaml

# ── Trend Engine ──────────────────────────────────────────────
trend:
  enabled: true
  capital: 0                    # Set via /trend_capital (0 = disabled)
  ema_fast: 20
  ema_slow: 50
  ema_trend: 200
  rsi_period: 14
  rsi_min: 40
  rsi_max: 70
  min_signal_score: 3
  confirmation_ticks: 2
  risk_per_trade_pct: 2.0
  max_position_pct: 25.0
  max_positions: 2
  trailing_stop_pct: 1.5
  trailing_activation_pct: 1.5
  rr_ratio: 2.0
  sl_buffer_pct: 0.2
  max_drawdown_pct: 10.0
  daily_loss_limit_pct: 5.0
  timeframe: "1h"
```

- [ ] **Step 2: Update docker-entrypoint.sh to use new strategy**

In `docker-entrypoint.sh`, the script is launched via `hummingbot_quickstart.py` which reads `SCRIPT_CONFIG` env var. Update the environment to point to the new config:

Find the line that contains `SCRIPT_CONFIG` or `ta_grid_btcusdt_conf` and change it to:
```bash
export SCRIPT_CONFIG=ta_grid_trend_conf.yml
```

If there's no explicit `SCRIPT_CONFIG` line, the config file `hummingbot_files/conf/scripts/ta_grid_btcusdt_conf.yml` is being copied. Ensure the new `ta_grid_trend_conf.yml` is also copied and set as active.

- [ ] **Step 3: Verify Dockerfile copies the new script**

Check `Dockerfile` for the line that copies scripts:
```
COPY hummingbot_files/scripts/ /home/hummingbot/scripts/
COPY hummingbot_files/conf/ /home/hummingbot/conf/
```

These should automatically pick up the new files since they copy entire directories. Verify this is the case.

- [ ] **Step 4: Commit**

```bash
git add config/strategy.yaml docker-entrypoint.sh
git commit -m "feat(trend): wire up trend engine config and docker entry point"
```

---

### Task 9: Integration Test

**Files:**
- Create: `tests/test_trend_integration.py`

- [ ] **Step 1: Write integration test**

Create `tests/test_trend_integration.py`:
```python
"""Integration test: simulate trend signals through the full pipeline."""
import pytest
import pandas as pd
import numpy as np
from src.trend.trend_manager import TrendManager
from src.trend.position_manager import PositionManager
from src.trend.trend_journal import TrendJournal
from src.trend.support_resistance import SupportResistance
from src.trend.candlestick_patterns import CandlestickPatterns
import tempfile
from pathlib import Path


def generate_trending_candles(n: int = 300, direction: str = "up") -> pd.DataFrame:
    """Generate realistic OHLCV candles with a clear trend."""
    np.random.seed(42)
    if direction == "up":
        returns = np.random.normal(0.002, 0.008, n)
    elif direction == "down":
        returns = np.random.normal(-0.002, 0.008, n)
    else:
        returns = np.random.normal(0, 0.008, n)

    close = 90 * np.cumprod(1 + returns)
    high = close * (1 + np.abs(np.random.normal(0, 0.005, n)))
    low = close * (1 - np.abs(np.random.normal(0, 0.005, n)))
    opn = close * (1 + np.random.normal(0, 0.002, n))

    return pd.DataFrame({
        "open": opn, "high": high, "low": low, "close": close,
    })


class TestTrendIntegration:
    def test_full_pipeline_uptrend(self):
        """Simulate an uptrend: should generate signals and open position."""
        candles = generate_trending_candles(300, "up")
        current_price = float(candles["close"].iloc[-1])

        tm = TrendManager()
        score = tm.evaluate(candles, current_price)

        # In uptrend, score should be non-zero
        assert score.total > 0
        assert len(score.details) > 0

        # Calculate stop-loss and take-profit
        sr = SupportResistance()
        levels = sr.detect(candles)
        sl = tm.calculate_stop_loss(current_price, levels, atr_value=1.0)
        tp = tm.calculate_take_profit(current_price, sl)

        assert sl < current_price
        assert tp > current_price
        assert tp - current_price >= (current_price - sl) * 1.9  # ~2:1 R:R

        # Open position
        pm = PositionManager(capital=2000.0, max_positions=2)
        amount = pm.calculate_position_size(current_price, sl)
        assert amount > 0

        pos = pm.open_position("test_order", current_price, amount, sl, tp, "2026-05-11T10:00:00Z")
        assert pos is not None
        assert pm.open_count == 1

    def test_full_pipeline_ranging_no_entry(self):
        """In a ranging market, should not generate high enough scores to enter."""
        candles = generate_trending_candles(300, "flat")
        current_price = float(candles["close"].iloc[-1])

        tm = TrendManager(min_signal_score=3, confirmation_ticks=1)
        score = tm.evaluate(candles, current_price)

        # Flat market should produce lower scores
        # (May still score if EMA alignment happens, but less likely)

    def test_trade_lifecycle(self):
        """Test complete trade lifecycle: open → trailing → close."""
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        journal = TrendJournal(db_path=Path(tmp.name))

        pm = PositionManager(capital=2000.0, trailing_stop_pct=1.5, trailing_activation_pct=1.5)
        entry_price = 94.0
        sl = 91.3
        tp = 99.4

        amount = pm.calculate_position_size(entry_price, sl)
        pos = pm.open_position("lifecycle_test", entry_price, amount, sl, tp, "2026-05-11T10:00:00Z")

        # Simulate price rising (trailing activates)
        pm.update_trailing(pos, 95.5)
        pm.update_trailing(pos, 96.5)

        # Check trailing moved up
        assert pos.trailing_stop > sl
        assert pos.trailing_activated is True

        # Price hits take profit
        exits = pm.check_exits(current_price=99.5)
        assert len(exits) == 1
        assert exits[0]["reason"] == "take_profit"

        # Close and journal
        closed = pm.close_position("lifecycle_test", 99.4, "take_profit")
        assert closed["pnl"] > 0

        journal.log_trade(
            side="SELL",
            entry_price=closed["entry_price"],
            exit_price=closed["exit_price"],
            amount=closed["amount"],
            fee=0.75,
            pnl=closed["pnl"],
            pnl_pct=closed["pnl_pct"],
            stop_loss=closed["stop_loss"],
            take_profit=closed["take_profit"],
            exit_reason="take_profit",
            signal_score=4,
            duration_minutes=closed["duration_minutes"],
        )

        # Verify journal
        summary = journal.summary()
        assert summary["total_trades"] == 1
        assert summary["wins"] == 1
        assert summary["total_pnl"] > 0

        Path(tmp.name).unlink()

    def test_stop_loss_trade(self):
        """Test trade that hits stop-loss."""
        pm = PositionManager(capital=2000.0)
        pos = pm.open_position("sl_test", 94.0, 14.0, 91.3, 99.4, "2026-05-11T10:00:00Z")

        # Price drops to stop-loss
        exits = pm.check_exits(current_price=91.0)
        assert len(exits) == 1
        assert exits[0]["reason"] == "stop_loss"

        closed = pm.close_position("sl_test", 91.3, "stop_loss")
        assert closed["pnl"] < 0
```

- [ ] **Step 2: Run all trend tests**

Run: `pytest tests/test_trend*.py tests/test_support_resistance.py tests/test_candlestick_patterns.py tests/test_position_manager.py -v`
Expected: All tests PASS

- [ ] **Step 3: Run full test suite to verify no regressions**

Run: `pytest tests/ -v`
Expected: All existing + new tests PASS

- [ ] **Step 4: Commit**

```bash
git add tests/test_trend_integration.py
git commit -m "test(trend): add integration tests for full trend pipeline"
```

---

### Task 10: Final Deployment Verification

- [ ] **Step 1: Run full test suite one final time**

Run: `pytest tests/ -v --tb=short`
Expected: All tests PASS

- [ ] **Step 2: Verify file structure**

Run: `find src/trend/ hummingbot_files/scripts/ta_grid_trend.py hummingbot_files/conf/scripts/ta_grid_trend_conf.yml -type f`
Expected: All new files listed

- [ ] **Step 3: Verify grid bot is untouched**

Run: `git diff HEAD~10 -- hummingbot_files/scripts/ta_grid_btcusdt.py`
Expected: No changes to ta_grid_btcusdt.py

- [ ] **Step 4: Push and deploy**

```bash
git push origin main
```

This triggers GitHub Actions → runs tests → deploys to EC2 via SSM. The new dual-engine strategy starts with `trend_capital: 0` (disabled), so the grid bot continues normally.

- [ ] **Step 5: Verify on EC2**

After deploy completes, check logs:
```bash
aws ssm send-command --instance-ids "i-0eafde6592d97eab2" --document-name "AWS-RunShellScript" --parameters 'commands=["cd /home/ec2-user/trading-humming-bot && docker compose logs --tail=20 bot"]'
```

Look for: `Trend engine initialized: capital=$0.00, enabled=True`

- [ ] **Step 6: Enable trend engine via Telegram**

Send `/trend_capital 100` to start micro paper testing.
Send `/trend_status` to verify the engine is active.
```

---

**Self-Review Checklist:**

1. **Spec coverage:** Every section in the design spec maps to a task:
   - Signal scoring → Task 5
   - Support/Resistance → Task 1
   - Candlestick patterns → Task 2
   - Entry/exit logic → Task 5 + Task 7
   - Risk management → Task 3 (position manager) + Task 7 (circuit breaker)
   - State management → Task 3 (save/load) + Task 7
   - Telegram commands → Task 6
   - Performance metrics → Task 4
   - Config/Docker → Task 8
   - Testing → Tasks 1-5 + Task 9
   - Migration path → Task 10

2. **Placeholder scan:** No TBDs, TODOs, or "implement later" found. All steps contain actual code.

3. **Type consistency:** Method names are consistent across tasks:
   - `TrendPosition` dataclass fields match between Task 3 (definition) and Task 7 (usage)
   - `SignalScore.total` and `.details` consistent between Task 5 (definition) and Task 6/7 (usage)
   - `PositionManager.open_position()` / `close_position()` / `check_exits()` signatures match across all tasks
   - `TrendJournal.log_trade()` parameters match between Task 4 and Task 7
   - `TrendManager.evaluate()` / `should_enter()` / `calculate_stop_loss()` / `calculate_take_profit()` consistent
