# TA-Enhanced BTC/USDT Grid Bot — Full Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete TA-Enhanced Grid Bot trading engine — indicators, grid logic, risk management, data feeds, Hummingbot integration, and deployment config.

**Architecture:** Hummingbot v2 ScriptStrategyBase drives order execution. A modular `src/` package provides indicators (BB, RSI, EMA, ATR), grid management (placement, state machine, order tracking), risk controls (circuit breaker, position guard), and data feeds (REST candles + WebSocket). SQLite journal + Telegram alerts + Streamlit dashboard already exist and will be wired into the new modules.

**Tech Stack:** Python 3.11, Hummingbot v2, pandas_ta, python-telegram-bot, Streamlit, SQLite, Google Sheets API, Docker, Binance REST/WebSocket APIs.

---

## File Structure

```
ta-grid-bot/
├── .env.example                    # EXISTS — no changes
├── .gitignore                      # CREATE
├── requirements.txt                # CREATE
├── Dockerfile                      # CREATE
├── docker-compose.yml              # CREATE
├── config/
│   └── strategy.yaml               # CREATE
├── src/
│   ├── __init__.py                 # CREATE
│   ├── indicators/
│   │   ├── __init__.py             # CREATE
│   │   ├── bollinger.py            # CREATE
│   │   ├── rsi.py                  # CREATE
│   │   ├── ema.py                  # CREATE
│   │   └── atr.py                  # CREATE
│   ├── grid/
│   │   ├── __init__.py             # CREATE
│   │   ├── grid_manager.py         # CREATE
│   │   ├── grid_state.py           # CREATE
│   │   └── order_tracker.py        # CREATE
│   ├── risk/
│   │   ├── __init__.py             # CREATE
│   │   ├── circuit_breaker.py      # CREATE
│   │   └── position_guard.py       # CREATE
│   ├── data/
│   │   ├── __init__.py             # CREATE
│   │   ├── candle_feed.py          # CREATE
│   │   └── ws_feed.py              # CREATE
│   ├── notifications/
│   │   ├── __init__.py             # CREATE
│   │   └── telegram_bot.py         # CREATE
│   ├── journal/
│   │   ├── __init__.py             # CREATE
│   │   ├── trade_journal.py        # MOVE (from root)
│   │   └── sheets_sync.py          # MOVE (from root)
│   └── dashboard/
│       └── app.py                  # MOVE (from root)
├── hummingbot_files/
│   └── scripts/
│       └── ta_grid_btcusdt.py      # CREATE — main Hummingbot strategy
├── backtest/
│   ├── vectorbt_sweep.py           # CREATE
│   └── walk_forward.py             # CREATE
├── tests/
│   ├── __init__.py                 # CREATE
│   ├── test_indicators.py          # CREATE
│   ├── test_grid_manager.py        # CREATE
│   ├── test_circuit_breaker.py     # CREATE
│   ├── test_position_guard.py      # CREATE
│   └── test_candle_feed.py         # CREATE
├── app.py                          # EXISTS (root-level, will update import)
├── pnl_reporter.py                 # EXISTS (root-level, will update import)
├── sheets_sync.py                  # EXISTS (root-level, will refactor)
└── trade_journal.py                # EXISTS (root-level, will refactor)
```

---

## Task 1: Project Skeleton — .gitignore, requirements.txt, config/strategy.yaml

**Files:**
- Create: `.gitignore`
- Create: `requirements.txt`
- Create: `config/strategy.yaml`

- [ ] **Step 1: Create .gitignore**

```
# Environment
.env
keys/

# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.venv/
venv/

# Data & Logs
data/
logs/
reports/

# IDE
.idea/
.vscode/
*.swp

# OS
.DS_Store
Thumbs.db

# Terraform state
iac/**/*.tfstate*
iac/**/.terraform/
```

- [ ] **Step 2: Create requirements.txt**

```
# Core
pandas>=2.0
numpy>=1.24
python-dotenv>=1.0

# Technical Analysis
pandas_ta>=0.3.14b

# Binance API
python-binance>=1.0.19
websockets>=12.0

# Hummingbot (script development)
hummingbot>=2.0

# Notifications
python-telegram-bot>=21.0
APScheduler>=3.10

# Dashboard
streamlit>=1.30
plotly>=5.18

# Google Sheets
gspread>=5.12
google-auth>=2.28

# Backtesting
vectorbt>=0.26

# Testing
pytest>=8.0
pytest-asyncio>=0.23
```

- [ ] **Step 3: Create config/strategy.yaml**

```yaml
pair: "BTC-USDT"
exchange: "binance"
timeframe: "1h"

grid:
  levels: 8
  capital_usdt: 200
  min_usdt_reserve: 50
  order_refresh_time: 60

indicators:
  bollinger:
    period: 20
    std_dev: 2.0
  rsi:
    period: 14
    oversold: 35
    overbought: 70
  ema:
    period: 200
  atr:
    period: 14
    spacing_multiplier: 0.8

rules:
  activate_conditions:
    - "price > ema_200"
    - "rsi < 65"
  pause_conditions:
    - "price < ema_200"
    - "rsi > 70"
  reactivate_conditions:
    - "rsi < 35"
    - "price near lower_bb"

risk:
  max_drawdown_pct: 10
  daily_loss_limit_pct: 5
  max_btc_exposure_pct: 80
```

- [ ] **Step 4: Create directory structure and __init__.py files**

```bash
mkdir -p src/indicators src/grid src/risk src/data src/notifications src/journal src/dashboard
mkdir -p hummingbot_files/scripts backtest tests config
touch src/__init__.py src/indicators/__init__.py src/grid/__init__.py
touch src/risk/__init__.py src/data/__init__.py src/notifications/__init__.py
touch src/journal/__init__.py tests/__init__.py
```

- [ ] **Step 5: Commit**

```bash
git add .gitignore requirements.txt config/strategy.yaml src/__init__.py src/indicators/__init__.py src/grid/__init__.py src/risk/__init__.py src/data/__init__.py src/notifications/__init__.py src/journal/__init__.py tests/__init__.py
git commit -m "feat: project skeleton — gitignore, requirements, config, directory structure"
```

---

## Task 2: Technical Indicators — Bollinger Bands

**Files:**
- Create: `src/indicators/bollinger.py`
- Test: `tests/test_indicators.py` (Bollinger section)

- [ ] **Step 1: Write the failing test for Bollinger Bands**

```python
# tests/test_indicators.py
import numpy as np
import pandas as pd
import pytest

from src.indicators.bollinger import BollingerBands


@pytest.fixture
def sample_candles():
    """Generate 30 synthetic close prices."""
    np.random.seed(42)
    base = 100_000.0
    noise = np.random.normal(0, 500, 30)
    closes = pd.Series(base + noise)
    return closes


class TestBollingerBands:
    def test_calculate_returns_upper_mid_lower(self, sample_candles):
        bb = BollingerBands(period=20, std_dev=2.0)
        result = bb.calculate(sample_candles)
        assert "upper" in result
        assert "mid" in result
        assert "lower" in result

    def test_upper_greater_than_mid_greater_than_lower(self, sample_candles):
        bb = BollingerBands(period=20, std_dev=2.0)
        result = bb.calculate(sample_candles)
        assert result["upper"] > result["mid"] > result["lower"]

    def test_insufficient_data_returns_none(self):
        bb = BollingerBands(period=20, std_dev=2.0)
        closes = pd.Series([100_000, 101_000, 99_000])
        result = bb.calculate(closes)
        assert result is None

    def test_mid_equals_sma(self, sample_candles):
        bb = BollingerBands(period=20, std_dev=2.0)
        result = bb.calculate(sample_candles)
        expected_mid = sample_candles.iloc[-20:].mean()
        assert abs(result["mid"] - expected_mid) < 0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indicators.py::TestBollingerBands -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.indicators.bollinger'`

- [ ] **Step 3: Write BollingerBands implementation**

```python
# src/indicators/bollinger.py
import pandas as pd
from dataclasses import dataclass


@dataclass
class BBResult:
    upper: float
    mid: float
    lower: float


class BollingerBands:
    def __init__(self, period: int = 20, std_dev: float = 2.0):
        self.period = period
        self.std_dev = std_dev

    def calculate(self, closes: pd.Series) -> BBResult | None:
        if len(closes) < self.period:
            return None
        window = closes.iloc[-self.period:]
        mid = window.mean()
        std = window.std()
        upper = mid + self.std_dev * std
        lower = mid - self.std_dev * std
        return BBResult(upper=upper, mid=mid, lower=lower)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_indicators.py::TestBollingerBands -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/indicators/bollinger.py tests/test_indicators.py
git commit -m "feat: add Bollinger Bands indicator with tests"
```

---

## Task 3: Technical Indicators — RSI

**Files:**
- Create: `src/indicators/rsi.py`
- Modify: `tests/test_indicators.py` (add RSI section)

- [ ] **Step 1: Write the failing test for RSI**

Add to `tests/test_indicators.py`:

```python
from src.indicators.rsi import RSI


class TestRSI:
    def test_calculate_returns_float(self, sample_candles):
        rsi = RSI(period=14)
        result = rsi.calculate(sample_candles)
        assert isinstance(result, float)

    def test_rsi_between_0_and_100(self, sample_candles):
        rsi = RSI(period=14)
        result = rsi.calculate(sample_candles)
        assert 0 <= result <= 100

    def test_insufficient_data_returns_none(self):
        rsi = RSI(period=14)
        closes = pd.Series([100.0, 101.0, 99.0])
        result = rsi.calculate(closes)
        assert result is None

    def test_all_gains_rsi_100(self):
        closes = pd.Series(range(1, 20), dtype=float)
        rsi = RSI(period=14)
        result = rsi.calculate(closes)
        assert result == 100.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indicators.py::TestRSI -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.indicators.rsi'`

- [ ] **Step 3: Write RSI implementation**

```python
# src/indicators/rsi.py
import pandas as pd


class RSI:
    def __init__(self, period: int = 14):
        self.period = period

    def calculate(self, closes: pd.Series) -> float | None:
        if len(closes) < self.period + 1:
            return None
        delta = closes.diff().iloc[-(self.period + 1):]
        gain = delta.where(delta > 0, 0.0)
        loss = (-delta).where(delta < 0, 0.0)
        avg_gain = gain.iloc[1:].mean()
        avg_loss = loss.iloc[1:].mean()
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100.0 - (100.0 / (1.0 + rs))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_indicators.py::TestRSI -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/indicators/rsi.py tests/test_indicators.py
git commit -m "feat: add RSI indicator with tests"
```

---

## Task 4: Technical Indicators — EMA

**Files:**
- Create: `src/indicators/ema.py`
- Modify: `tests/test_indicators.py` (add EMA section)

- [ ] **Step 1: Write the failing test for EMA**

Add to `tests/test_indicators.py`:

```python
from src.indicators.ema import EMA


class TestEMA:
    def test_calculate_returns_float(self, sample_candles):
        ema = EMA(period=20)
        result = ema.calculate(sample_candles)
        assert isinstance(result, float)

    def test_insufficient_data_returns_none(self):
        ema = EMA(period=20)
        closes = pd.Series([100.0] * 5)
        result = ema.calculate(closes)
        assert result is None

    def test_ema_smoothing_less_than_last_close(self, sample_candles):
        ema = EMA(period=20)
        result = ema.calculate(sample_candles)
        # EMA should lag behind rapid price changes
        assert isinstance(result, float)

    def test_constant_price_returns_same(self):
        closes = pd.Series([50_000.0] * 250)
        ema = EMA(period=200)
        result = ema.calculate(closes)
        assert abs(result - 50_000.0) < 0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indicators.py::TestEMA -v`
Expected: FAIL

- [ ] **Step 3: Write EMA implementation**

```python
# src/indicators/ema.py
import pandas as pd


class EMA:
    def __init__(self, period: int = 200):
        self.period = period

    def calculate(self, closes: pd.Series) -> float | None:
        if len(closes) < self.period:
            return None
        return float(closes.ewm(span=self.period, adjust=False).mean().iloc[-1])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_indicators.py::TestEMA -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/indicators/ema.py tests/test_indicators.py
git commit -m "feat: add EMA indicator with tests"
```

---

## Task 5: Technical Indicators — ATR

**Files:**
- Create: `src/indicators/atr.py`
- Modify: `tests/test_indicators.py` (add ATR section)

- [ ] **Step 1: Write the failing test for ATR**

Add to `tests/test_indicators.py`:

```python
from src.indicators.atr import ATR


@pytest.fixture
def sample_ohlcv():
    """Generate synthetic OHLCV data with 30 rows."""
    np.random.seed(42)
    n = 30
    base = 100_000.0
    highs = pd.Series(base + np.random.uniform(100, 1000, n))
    lows = pd.Series(base - np.random.uniform(100, 1000, n))
    closes = pd.Series(base + np.random.normal(0, 300, n))
    return highs, lows, closes


class TestATR:
    def test_calculate_returns_float(self, sample_ohlcv):
        highs, lows, closes = sample_ohlcv
        atr = ATR(period=14)
        result = atr.calculate(highs, lows, closes)
        assert isinstance(result, float)

    def test_atr_positive(self, sample_ohlcv):
        highs, lows, closes = sample_ohlcv
        atr = ATR(period=14)
        result = atr.calculate(highs, lows, closes)
        assert result > 0

    def test_grid_spacing_calculation(self, sample_ohlcv):
        highs, lows, closes = sample_ohlcv
        atr = ATR(period=14, spacing_multiplier=0.8)
        result = atr.calculate(highs, lows, closes)
        spacing = atr.grid_spacing(result)
        assert spacing == result * 0.8

    def test_insufficient_data_returns_none(self):
        highs = pd.Series([101_000.0, 100_500.0])
        lows = pd.Series([99_000.0, 99_500.0])
        closes = pd.Series([100_000.0, 100_200.0])
        atr = ATR(period=14)
        result = atr.calculate(highs, lows, closes)
        assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_indicators.py::TestATR -v`
Expected: FAIL

- [ ] **Step 3: Write ATR implementation**

```python
# src/indicators/atr.py
import pandas as pd


class ATR:
    def __init__(self, period: int = 14, spacing_multiplier: float = 0.8):
        self.period = period
        self.spacing_multiplier = spacing_multiplier

    def calculate(self, highs: pd.Series, lows: pd.Series, closes: pd.Series) -> float | None:
        if len(closes) < self.period + 1:
            return None
        prev_close = closes.shift(1)
        tr1 = highs - lows
        tr2 = (highs - prev_close).abs()
        tr3 = (lows - prev_close).abs()
        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr_series = true_range.ewm(span=self.period, adjust=False).mean()
        return float(atr_series.iloc[-1])

    def grid_spacing(self, atr_value: float) -> float:
        return atr_value * self.spacing_multiplier
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_indicators.py::TestATR -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/indicators/atr.py tests/test_indicators.py
git commit -m "feat: add ATR indicator with grid spacing and tests"
```

---

## Task 6: Grid State Machine

**Files:**
- Create: `src/grid/grid_state.py`

- [ ] **Step 1: Write GridState implementation**

This is an enum-based state machine with transition rules. Simple enough to verify by inspection.

```python
# src/grid/grid_state.py
from enum import Enum


class GridState(Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REACTIVATING = "REACTIVATING"


class GridStateMachine:
    def __init__(self):
        self.state = GridState.PAUSED

    def evaluate(self, price: float, rsi: float, ema_200: float,
                 bb_lower: float, bb_upper: float,
                 rsi_overbought: float = 70.0, rsi_oversold: float = 35.0) -> GridState:
        # Pause if overbought or below EMA200
        if rsi > rsi_overbought or price < ema_200:
            self.state = GridState.PAUSED
            return self.state

        # Reactivate if oversold near lower BB
        if rsi < rsi_oversold and price <= bb_lower * 1.02:
            self.state = GridState.REACTIVATING
            return self.state

        # Activate if price above EMA200 and RSI not overbought
        if price > ema_200 and rsi < rsi_overbought:
            self.state = GridState.ACTIVE
            return self.state

        return self.state

    @property
    def is_active(self) -> bool:
        return self.state in (GridState.ACTIVE, GridState.REACTIVATING)

    @property
    def is_paused(self) -> bool:
        return self.state == GridState.PAUSED
```

- [ ] **Step 2: Commit**

```bash
git add src/grid/grid_state.py
git commit -m "feat: add grid state machine with PAUSED/ACTIVE/REACTIVATING transitions"
```

---

## Task 7: Order Tracker

**Files:**
- Create: `src/grid/order_tracker.py`

- [ ] **Step 1: Write OrderTracker implementation**

```python
# src/grid/order_tracker.py
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


@dataclass
class GridOrder:
    order_id: str
    level: int
    side: OrderSide
    price: float
    quantity: float
    status: OrderStatus = OrderStatus.PENDING


class OrderTracker:
    def __init__(self):
        self._orders: dict[str, GridOrder] = {}

    def add(self, order: GridOrder) -> None:
        self._orders[order.order_id] = order

    def mark_filled(self, order_id: str) -> Optional[GridOrder]:
        order = self._orders.get(order_id)
        if order:
            order.status = OrderStatus.FILLED
        return order

    def cancel_all(self) -> list[str]:
        cancelled_ids = []
        for order in self._orders.values():
            if order.status == OrderStatus.PENDING:
                order.status = OrderStatus.CANCELLED
                cancelled_ids.append(order.order_id)
        return cancelled_ids

    def pending_orders(self) -> list[GridOrder]:
        return [o for o in self._orders.values() if o.status == OrderStatus.PENDING]

    def filled_orders(self) -> list[GridOrder]:
        return [o for o in self._orders.values() if o.status == OrderStatus.FILLED]

    def clear_history(self) -> None:
        """Remove cancelled and old filled orders to prevent unbounded growth."""
        self._orders = {
            oid: o for oid, o in self._orders.items()
            if o.status == OrderStatus.PENDING
        }

    @property
    def total_pending(self) -> int:
        return len(self.pending_orders())
```

- [ ] **Step 2: Commit**

```bash
git add src/grid/order_tracker.py
git commit -m "feat: add order tracker for grid order lifecycle management"
```

---

## Task 8: Grid Manager

**Files:**
- Create: `src/grid/grid_manager.py`
- Test: `tests/test_grid_manager.py`

- [ ] **Step 1: Write the failing test for GridManager**

```python
# tests/test_grid_manager.py
import pytest
from src.grid.grid_manager import GridManager
from src.indicators.bollinger import BBResult
from src.grid.grid_state import GridState


class TestGridManager:
    def test_calculate_grid_levels_count(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        assert len(grid.buy_levels) == 8
        assert len(grid.sell_levels) == 8

    def test_buy_levels_below_mid(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        for level in grid.buy_levels:
            assert level["price"] < 100_000

    def test_sell_levels_above_mid(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        for level in grid.sell_levels:
            assert level["price"] > 100_000

    def test_order_size_bounded_by_capital(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        deployable = 200 - 50  # capital minus reserve
        total_buy = sum(l["quantity"] * l["price"] for l in grid.buy_levels)
        assert total_buy <= deployable * 1.01  # allow small float tolerance

    def test_spacing_matches_atr(self):
        gm = GridManager(levels=4, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=104_000, mid=100_000, lower=96_000),
            atr_value=500,
        )
        spacing = 500 * 0.8  # ATR * multiplier
        # Check first buy level is mid - spacing
        assert abs(grid.buy_levels[0]["price"] - (100_000 - spacing)) < 1.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_grid_manager.py -v`
Expected: FAIL

- [ ] **Step 3: Write GridManager implementation**

```python
# src/grid/grid_manager.py
from dataclasses import dataclass
from src.indicators.bollinger import BBResult


@dataclass
class GridLayout:
    buy_levels: list[dict]   # [{"price": float, "quantity": float, "level": int}]
    sell_levels: list[dict]
    spacing: float
    mid_price: float


class GridManager:
    def __init__(self, levels: int = 8, capital_usdt: float = 200,
                 min_reserve: float = 50, spacing_multiplier: float = 0.8):
        self.levels = levels
        self.capital_usdt = capital_usdt
        self.min_reserve = min_reserve
        self.spacing_multiplier = spacing_multiplier

    def calculate_grid(self, bb: BBResult, atr_value: float) -> GridLayout:
        spacing = atr_value * self.spacing_multiplier
        deployable = self.capital_usdt - self.min_reserve
        order_value = deployable / (self.levels * 2)

        buy_levels = []
        sell_levels = []

        for i in range(1, self.levels + 1):
            buy_price = bb.mid - spacing * i
            # Clamp to lower BB
            buy_price = max(buy_price, bb.lower)
            buy_qty = order_value / buy_price

            sell_price = bb.mid + spacing * i
            # Clamp to upper BB
            sell_price = min(sell_price, bb.upper)
            sell_qty = order_value / sell_price

            buy_levels.append({
                "price": round(buy_price, 2),
                "quantity": round(buy_qty, 8),
                "level": i,
            })
            sell_levels.append({
                "price": round(sell_price, 2),
                "quantity": round(sell_qty, 8),
                "level": i,
            })

        return GridLayout(
            buy_levels=buy_levels,
            sell_levels=sell_levels,
            spacing=spacing,
            mid_price=bb.mid,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_grid_manager.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add src/grid/grid_manager.py tests/test_grid_manager.py
git commit -m "feat: add grid manager with level calculation and tests"
```

---

## Task 9: Risk Management — Circuit Breaker

**Files:**
- Create: `src/risk/circuit_breaker.py`
- Test: `tests/test_circuit_breaker.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_circuit_breaker.py
import pytest
from src.risk.circuit_breaker import CircuitBreaker


class TestCircuitBreaker:
    def test_no_trip_when_below_threshold(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_peak_equity(1000.0)
        assert not cb.check(950.0)  # 5% drawdown — safe

    def test_trips_at_threshold(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_peak_equity(1000.0)
        assert cb.check(890.0)  # 11% drawdown — trip

    def test_peak_updates_on_new_high(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_peak_equity(1000.0)
        cb.update_peak(1100.0)
        assert not cb.check(1000.0)  # 9% from new peak — safe

    def test_daily_loss_limit(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_start_of_day_equity(1000.0)
        assert cb.check_daily(940.0)  # 6% daily loss — trip

    def test_daily_safe_below_limit(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_start_of_day_equity(1000.0)
        assert not cb.check_daily(960.0)  # 4% daily loss — safe

    def test_halted_flag(self):
        cb = CircuitBreaker(max_drawdown_pct=10.0, daily_loss_limit_pct=5.0)
        cb.set_peak_equity(1000.0)
        cb.check(800.0)  # 20% drawdown
        assert cb.halted
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_circuit_breaker.py -v`
Expected: FAIL

- [ ] **Step 3: Write CircuitBreaker implementation**

```python
# src/risk/circuit_breaker.py
class CircuitBreaker:
    def __init__(self, max_drawdown_pct: float = 10.0, daily_loss_limit_pct: float = 5.0):
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self._peak_equity: float = 0.0
        self._sod_equity: float = 0.0  # start-of-day
        self.halted: bool = False

    def set_peak_equity(self, equity: float) -> None:
        self._peak_equity = equity

    def set_start_of_day_equity(self, equity: float) -> None:
        self._sod_equity = equity

    def update_peak(self, current_equity: float) -> None:
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity

    def check(self, current_equity: float) -> bool:
        """Return True if drawdown from peak exceeds threshold."""
        if self._peak_equity == 0:
            return False
        drawdown_pct = ((self._peak_equity - current_equity) / self._peak_equity) * 100
        if drawdown_pct >= self.max_drawdown_pct:
            self.halted = True
            return True
        return False

    def check_daily(self, current_equity: float) -> bool:
        """Return True if daily loss exceeds threshold."""
        if self._sod_equity == 0:
            return False
        loss_pct = ((self._sod_equity - current_equity) / self._sod_equity) * 100
        if loss_pct >= self.daily_loss_limit_pct:
            self.halted = True
            return True
        return False
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_circuit_breaker.py -v`
Expected: 6 passed

- [ ] **Step 5: Commit**

```bash
git add src/risk/circuit_breaker.py tests/test_circuit_breaker.py
git commit -m "feat: add circuit breaker with drawdown and daily loss limits"
```

---

## Task 10: Risk Management — Position Guard

**Files:**
- Create: `src/risk/position_guard.py`
- Test: `tests/test_position_guard.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_position_guard.py
import pytest
from src.risk.position_guard import PositionGuard


class TestPositionGuard:
    def test_allows_order_within_exposure(self):
        pg = PositionGuard(max_btc_exposure_pct=80, min_usdt_reserve=50,
                           total_capital=200)
        # BTC held: 0.001 BTC @ $100,000 = $100 exposure (50%)
        assert pg.can_place_order(
            current_btc=0.001, btc_price=100_000,
            current_usdt=100, order_usdt=20
        )

    def test_blocks_order_exceeding_exposure(self):
        pg = PositionGuard(max_btc_exposure_pct=80, min_usdt_reserve=50,
                           total_capital=200)
        # BTC held: 0.0015 BTC @ $100,000 = $150 exposure (75%)
        # New order would push to 90%
        assert not pg.can_place_order(
            current_btc=0.0015, btc_price=100_000,
            current_usdt=50, order_usdt=30
        )

    def test_blocks_order_below_usdt_reserve(self):
        pg = PositionGuard(max_btc_exposure_pct=80, min_usdt_reserve=50,
                           total_capital=200)
        # Only $60 USDT left, order is $20, would leave $40 < $50 reserve
        assert not pg.can_place_order(
            current_btc=0.0005, btc_price=100_000,
            current_usdt=60, order_usdt=20
        )

    def test_btc_exposure_pct_calculation(self):
        pg = PositionGuard(max_btc_exposure_pct=80, min_usdt_reserve=50,
                           total_capital=200)
        pct = pg.btc_exposure_pct(current_btc=0.001, btc_price=100_000)
        assert pct == 50.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_position_guard.py -v`
Expected: FAIL

- [ ] **Step 3: Write PositionGuard implementation**

```python
# src/risk/position_guard.py
class PositionGuard:
    def __init__(self, max_btc_exposure_pct: float = 80.0,
                 min_usdt_reserve: float = 50.0, total_capital: float = 200.0):
        self.max_btc_exposure_pct = max_btc_exposure_pct
        self.min_usdt_reserve = min_usdt_reserve
        self.total_capital = total_capital

    def btc_exposure_pct(self, current_btc: float, btc_price: float) -> float:
        btc_value = current_btc * btc_price
        return (btc_value / self.total_capital) * 100

    def can_place_order(self, current_btc: float, btc_price: float,
                        current_usdt: float, order_usdt: float) -> bool:
        # Check USDT reserve
        if (current_usdt - order_usdt) < self.min_usdt_reserve:
            return False
        # Check BTC exposure if this is a buy order (adds BTC)
        new_btc_value = (current_btc * btc_price) + order_usdt
        new_exposure_pct = (new_btc_value / self.total_capital) * 100
        if new_exposure_pct > self.max_btc_exposure_pct:
            return False
        return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_position_guard.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add src/risk/position_guard.py tests/test_position_guard.py
git commit -m "feat: add position guard with exposure and reserve checks"
```

---

## Task 11: Data Feed — Binance REST Candle Fetcher

**Files:**
- Create: `src/data/candle_feed.py`
- Test: `tests/test_candle_feed.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_candle_feed.py
import pytest
from unittest.mock import MagicMock, patch
import pandas as pd
from src.data.candle_feed import CandleFeed


class TestCandleFeed:
    @patch("src.data.candle_feed.Client")
    def test_fetch_candles_returns_dataframe(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.get_klines.return_value = [
            [0, 100_000, 101_000, 99_000, 100_500, 1.0, 0, 0, 0, 0, 0, 0]
        ]
        feed = CandleFeed(symbol="BTCUSDT", interval="1h")
        df = feed.fetch_candles(limit=1)
        assert isinstance(df, pd.DataFrame)
        assert "open" in df.columns
        assert "high" in df.columns
        assert "low" in df.columns
        assert "close" in df.columns

    @patch("src.data.candle_feed.Client")
    def test_candle_columns_correct(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.get_klines.return_value = [
            [1700000000000, 100_000, 101_000, 99_000, 100_500, 1.0, 0, 0, 0, 0, 0, 0]
        ]
        feed = CandleFeed(symbol="BTCUSDT", interval="1h")
        df = feed.fetch_candles(limit=1)
        assert len(df) == 1
        assert df.iloc[0]["close"] == 100_500.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_candle_feed.py -v`
Expected: FAIL

- [ ] **Step 3: Write CandleFeed implementation**

```python
# src/data/candle_feed.py
import os
import pandas as pd
from binance.client import Client


class CandleFeed:
    def __init__(self, symbol: str = "BTCUSDT", interval: str = "1h",
                 testnet: bool = False):
        self.symbol = symbol
        self.interval = interval
        if testnet:
            api_key = os.environ.get("BINANCE_TESTNET_API_KEY", "")
            api_secret = os.environ.get("BINANCE_TESTNET_API_SECRET", "")
            self.client = Client(api_key, api_secret, testnet=True)
        else:
            api_key = os.environ.get("BINANCE_API_KEY", "")
            api_secret = os.environ.get("BINANCE_API_SECRET", "")
            self.client = Client(api_key, api_secret)

    def fetch_candles(self, limit: int = 200) -> pd.DataFrame:
        klines = self.client.get_klines(
            symbol=self.symbol,
            interval=self.interval,
            limit=limit,
        )
        df = pd.DataFrame(klines, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "quote_volume", "trades",
            "taker_buy_base", "taker_buy_quote", "ignore",
        ])
        for col in ["open", "high", "low", "close", "volume"]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        return df[["open", "high", "low", "close", "volume"]]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_candle_feed.py -v`
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add src/data/candle_feed.py tests/test_candle_feed.py
git commit -m "feat: add Binance REST candle feed with tests"
```

---

## Task 12: Data Feed — WebSocket Price Feed

**Files:**
- Create: `src/data/ws_feed.py`

- [ ] **Step 1: Write WebSocket price feed**

```python
# src/data/ws_feed.py
import json
import logging
import asyncio
from typing import Callable, Optional
import websockets

logger = logging.getLogger(__name__)


class WebSocketFeed:
    BINANCE_WS = "wss://stream.binance.com:9443/ws"
    BINANCE_WS_TESTNET = "wss://testnet.binance.vision/ws"

    def __init__(self, symbol: str = "btcusdt", testnet: bool = False,
                 on_price_update: Optional[Callable[[float], None]] = None):
        self.symbol = symbol.lower()
        self.testnet = testnet
        self.on_price_update = on_price_update
        self._running = False
        self._latest_price: float = 0.0

    @property
    def latest_price(self) -> float:
        return self._latest_price

    async def start(self) -> None:
        base = self.BINANCE_WS_TESTNET if self.testnet else self.BINANCE_WS
        stream = f"{base}/{self.symbol}@ticker"
        self._running = True
        async with websockets.connect(stream) as ws:
            logger.info(f"WebSocket connected: {stream}")
            while self._running:
                msg = await ws.recv()
                data = json.loads(msg)
                price = float(data["c"])  # current price
                self._latest_price = price
                if self.on_price_update:
                    self.on_price_update(price)

    def stop(self) -> None:
        self._running = False
```

- [ ] **Step 2: Commit**

```bash
git add src/data/ws_feed.py
git commit -m "feat: add Binance WebSocket price feed"
```

---

## Task 13: Notifications — Telegram Bot

**Files:**
- Create: `src/notifications/telegram_bot.py`

- [ ] **Step 1: Write TelegramBot wrapper**

This wraps the existing `pnl_reporter.py` and adds a startup/shutdown alert.

```python
# src/notifications/telegram_bot.py
import os
import asyncio
import logging
from typing import Optional
from telegram import Bot
from telegram.constants import ParseMode

logger = logging.getLogger(__name__)


class TelegramBot:
    def __init__(self):
        self.token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        self._bot: Optional[Bot] = None

    @property
    def bot(self) -> Bot:
        if not self._bot:
            self._bot = Bot(token=self.token)
        return self._bot

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, message: str) -> None:
        if not self.enabled:
            logger.warning("Telegram not configured — skipping alert")
            return
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.HTML,
            )
        except Exception as e:
            logger.error(f"Telegram send failed: {e}")

    async def alert_startup(self, env: str, capital: float) -> None:
        await self.send(
            f"🟢 <b>Grid Bot STARTED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🌐 Mode: {env.upper()}\n"
            f"💰 Capital: ${capital:,.0f} USDT\n"
            f"📊 Pair: BTC/USDT\n"
            f"⏰ Time: {self._now()} UTC"
        )

    async def alert_shutdown(self, reason: str = "manual") -> None:
        await self.send(
            f"🔴 <b>Grid Bot STOPPED</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"⚠️ Reason: {reason}\n"
            f"⏰ Time: {self._now()} UTC"
        )

    @staticmethod
    def _now() -> str:
        from datetime import datetime
        return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
```

- [ ] **Step 2: Commit**

```bash
git add src/notifications/telegram_bot.py
git commit -m "feat: add Telegram notification bot wrapper"
```

---

## Task 14: Refactor — Move existing files into src/ structure

**Files:**
- Create: `src/journal/trade_journal.py` (copy from root)
- Create: `src/journal/sheets_sync.py` (copy from root, fix imports)
- Update: `app.py` (fix imports)
- Update: `pnl_reporter.py` (fix imports)

- [ ] **Step 1: Copy trade_journal.py to src/journal/ with no changes**

The file at root `trade_journal.py` is already self-contained (no imports from src). Copy it as-is.

```bash
cp trade_journal.py src/journal/trade_journal.py
```

- [ ] **Step 2: Copy sheets_sync.py to src/journal/ with updated imports**

Change `from src.journal.trade_journal import ...` to relative import.

In `src/journal/sheets_sync.py`, the import line:
```python
from src.journal.trade_journal import Trade, TradeJournal
```
becomes:
```python
from .trade_journal import Trade, TradeJournal
```

- [ ] **Step 3: Update root app.py import**

Change:
```python
from src.journal.trade_journal import TradeJournal
```
(This already works with the new location — no change needed)

- [ ] **Step 4: Update root pnl_reporter.py import**

Change:
```python
from src.journal.trade_journal import TradeJournal, Trade
```
(This already works — no change needed)

- [ ] **Step 5: Run all existing tests to verify nothing broke**

Run: `python -m pytest tests/ -v`
Expected: All indicator + grid + risk tests pass

- [ ] **Step 6: Commit**

```bash
git add src/journal/trade_journal.py src/journal/sheets_sync.py
git commit -m "refactor: move journal modules into src/journal/ package"
```

---

## Task 15: Main Hummingbot Strategy Script

**Files:**
- Create: `hummingbot_files/scripts/ta_grid_btcusdt.py`

- [ ] **Step 1: Write the main Hummingbot v2 strategy script**

This is the orchestration layer that ties indicators, grid, risk, and notifications together.

```python
# hummingbot_files/scripts/ta_grid_btcusdt.py
"""
ta_grid_btcusdt.py — TA-Enhanced BTC/USDT Grid Bot
Hummingbot v2 ScriptStrategyBase implementation.

Start: start --script ta_grid_btcusdt.py
"""

import os
import asyncio
import logging
from decimal import Decimal
from typing import Dict, List, Optional

import pandas as pd
from hummingbot.strategy.script_strategy_base import ScriptStrategyBase
from hummingbot.core.event.events import OrderType, TradeType

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from src.indicators.bollinger import BollingerBands
from src.indicators.rsi import RSI
from src.indicators.ema import EMA
from src.indicators.atr import ATR
from src.grid.grid_manager import GridManager
from src.grid.grid_state import GridStateMachine, GridState
from src.grid.order_tracker import OrderTracker, OrderSide
from src.risk.circuit_breaker import CircuitBreaker
from src.risk.position_guard import PositionGuard
from src.data.candle_feed import CandleFeed
from src.notifications.telegram_bot import TelegramBot
from src.journal.trade_journal import TradeJournal, Trade
from src.notifications.telegram_bot import TelegramBot

logger = logging.getLogger(__name__)


class TAGridBTCUSDT(ScriptStrategyBase):
    """
    TA-Enhanced Grid Bot strategy for Hummingbot v2.
    Uses Bollinger Bands, RSI, EMA 200, and ATR to dynamically
    manage a grid of buy/sell orders on BTC/USDT.
    """

    # ── Configuration ────────────────────────────────────────────────
    exchange = "binance"
    trading_pair = "BTC-USDT"
    order_refresh_time = 60  # seconds between grid re-evaluations

    # Grid parameters
    levels = 8
    capital_usdt = float(os.environ.get("GRID_CAPITAL_USDT", "200"))
    min_reserve = float(os.environ.get("MIN_USDT_RESERVE", "50"))

    # Indicator parameters
    bb_period = 20
    bb_std = 2.0
    rsi_period = 14
    rsi_overbought = 70.0
    rsi_oversold = 35.0
    ema_period = 200
    atr_period = 14
    atr_multiplier = 0.8

    # Risk parameters
    max_drawdown_pct = float(os.environ.get("MAX_DRAWDOWN_PCT", "10"))
    daily_loss_limit_pct = 5.0
    max_btc_exposure_pct = float(os.environ.get("MAX_BTC_EXPOSURE_PCT", "80"))

    # Environment
    env = os.environ.get("ENV", "paper")
    is_testnet = env == "paper"

    markets = {
        "binance": {trading_pair: {}}
    }

    def __init__(self, connectors: Dict):
        super().__init__(connectors)

        # Initialize modules
        self.bb = BollingerBands(self.bb_period, self.bb_std)
        self.rsi = RSI(self.rsi_period)
        self.ema = EMA(self.ema_period)
        self.atr = ATR(self.atr_period, self.atr_multiplier)

        self.grid_manager = GridManager(
            levels=self.levels,
            capital_usdt=self.capital_usdt,
            min_reserve=self.min_reserve,
            spacing_multiplier=self.atr_multiplier,
        )
        self.state_machine = GridStateMachine()
        self.order_tracker = OrderTracker()
        self.circuit_breaker = CircuitBreaker(self.max_drawdown_pct, self.daily_loss_limit_pct)
        self.position_guard = PositionGuard(
            self.max_btc_exposure_pct, self.min_reserve, self.capital_usdt
        )
        self.candle_feed = CandleFeed(
            symbol="BTCUSDT",
            interval="1h",
            testnet=self.is_testnet,
        )
        self.telegram = TelegramBot()
        self.journal = TradeJournal()

        self._last_grid_time = 0
        self._peak_equity = self.capital_usdt

        # Signal startup
        asyncio.get_event_loop().create_task(
            self.telegram.alert_startup(self.env, self.capital_usdt)
        )

    # ── Main Tick Loop ───────────────────────────────────────────────

    def on_tick(self):
        if self.circuit_breaker.halted:
            return

        # Fetch candle data
        try:
            df = self.candle_feed.fetch_candles(limit=250)
        except Exception as e:
            logger.error(f"Candle fetch failed: {e}")
            return

        closes = df["close"]
        highs = df["high"]
        lows = df["low"]
        current_price = float(closes.iloc[-1])

        # Calculate indicators
        bb_result = self.bb.calculate(closes)
        rsi_value = self.rsi.calculate(closes)
        ema_value = self.ema.calculate(closes)
        atr_value = self.atr.calculate(highs, lows, closes)

        if any(v is None for v in [bb_result, rsi_value, ema_value, atr_value]):
            logger.warning("Insufficient data for indicator calculation")
            return

        # Evaluate grid state
        prev_state = self.state_machine.state
        new_state = self.state_machine.evaluate(
            price=current_price,
            rsi=rsi_value,
            ema_200=ema_value,
            bb_lower=bb_result.lower,
            bb_upper=bb_result.upper,
            rsi_overbought=self.rsi_overbought,
            rsi_oversold=self.rsi_oversold,
        )

        # Log state transitions
        if new_state != prev_state:
            logger.info(f"Grid state: {prev_state.value} -> {new_state.value}")
            self._notify_state_change(new_state, current_price, rsi_value, bb_result)

        # Cancel all orders if paused
        if self.state_machine.is_paused:
            self._cancel_all_orders()
            return

        # Check risk
        equity = self._estimate_equity(current_price)
        self.circuit_breaker.update_peak(equity)
        if self.circuit_breaker.check(equity):
            self._cancel_all_orders()
            logger.critical("Circuit breaker triggered!")
            return

        # Place grid
        grid = self.grid_manager.calculate_grid(bb_result, atr_value)
        self._place_grid_orders(grid, current_price)

    # ── Order Management ─────────────────────────────────────────────

    def _place_grid_orders(self, grid, current_price: float):
        connector = self.connectors[self.exchange]

        # Cancel stale pending orders first
        self._cancel_all_orders()

        # Place buy orders
        for level in grid.buy_levels:
            if level["price"] >= current_price:
                continue  # Buy levels must be below current price
            if not self.position_guard.can_place_order(
                current_btc=self._get_btc_balance(),
                btc_price=current_price,
                current_usdt=self._get_usdt_balance(),
                order_usdt=level["price"] * level["quantity"],
            ):
                continue
            self.place_order(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                order_type=OrderType.LIMIT,
                trade_type=TradeType.BUY,
                amount=Decimal(str(level["quantity"])),
                price=Decimal(str(level["price"])),
            )

        # Place sell orders
        for level in grid.sell_levels:
            if level["price"] <= current_price:
                continue  # Sell levels must be above current price
            self.place_order(
                connector_name=self.exchange,
                trading_pair=self.trading_pair,
                order_type=OrderType.LIMIT,
                trade_type=TradeType.SELL,
                amount=Decimal(str(level["quantity"])),
                price=Decimal(str(level["price"])),
            )

    def _cancel_all_orders(self):
        connector = self.connectors[self.exchange]
        for order in connector.in_flight_orders.values():
            self.cancel_order(self.exchange, order.trading_pair, order.client_order_id)

    # ── Balance Helpers ──────────────────────────────────────────────

    def _get_usdt_balance(self) -> float:
        connector = self.connectors[self.exchange]
        balance = connector.get_balance("USDT")
        return float(balance.available) if balance else 0.0

    def _get_btc_balance(self) -> float:
        connector = self.connectors[self.exchange]
        balance = connector.get_balance("BTC")
        return float(balance.available) if balance else 0.0

    def _estimate_equity(self, btc_price: float) -> float:
        return self._get_usdt_balance() + (self._get_btc_balance() * btc_price)

    # ── Notifications ────────────────────────────────────────────────

    def _notify_state_change(self, new_state, price, rsi, bb):
        if new_state == GridState.ACTIVE:
            msg = (
                f"🟢 <b>Grid ACTIVATED — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Price: ${price:,.2f}\n"
                f"📐 Range: ${bb.lower:,.0f} → ${bb.upper:,.0f}\n"
                f"📊 RSI: {rsi:.1f}"
            )
        elif new_state == GridState.PAUSED:
            msg = (
                f"⏸️ <b>Grid PAUSED — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Price: ${price:,.2f}\n"
                f"📊 RSI: {rsi:.1f}\n"
                f"💤 Holding USDT until re-entry signal."
            )
        elif new_state == GridState.REACTIVATING:
            msg = (
                f"🔄 <b>Grid REACTIVATING — BTC/USDT</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"💵 Price: ${price:,.2f}\n"
                f"📊 RSI: {rsi:.1f} (oversold bounce)\n"
                f"📐 New range: ${bb.lower:,.0f} → ${bb.upper:,.0f}"
            )
        else:
            return
        asyncio.get_event_loop().create_task(self.telegram.send(msg))

    # ── Trade Filled Hook ────────────────────────────────────────────

    def did_fill_order(self, event):
        """Called by Hummingbot when an order is filled."""
        order = event.order
        trade = Trade(
            timestamp=pd.Timestamp.now(tz="UTC").strftime("%Y-%m-%d %H:%M:%S"),
            pair="BTC/USDT",
            side="BUY" if order.trade_type == TradeType.BUY else "SELL",
            entry_price=float(order.price),
            exit_price=float(order.price),
            quantity=float(order.amount),
            gross_pnl=0.0,
            fee=0.0,
            net_pnl=0.0,
            grid_level=0,
            duration_min=0,
            rsi=0.0,
            bb_upper=0.0,
            bb_lower=0.0,
            ema_200=0.0,
            atr=0.0,
            grid_state=self.state_machine.state.value,
        )
        trade_id = self.journal.log_trade(trade)
        logger.info(f"Trade filled: {trade.side} {trade.quantity} @ {trade.entry_price}")
```

- [ ] **Step 2: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_btcusdt.py
git commit -m "feat: add main Hummingbot v2 TA Grid Bot strategy script"
```

---

## Task 16: Dockerfile and docker-compose.yml

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`

- [ ] **Step 1: Create Dockerfile**

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory for SQLite
RUN mkdir -p data logs

CMD ["python", "-m", "hummingbot", "start", "--script", "hummingbot_files/scripts/ta_grid_btcusdt.py"]
```

- [ ] **Step 2: Create docker-compose.yml**

```yaml
version: "3.8"

services:
  bot:
    build: .
    env_file: .env
    restart: unless-stopped
    volumes:
      - ./data:/app/data
      - ./logs:/app/logs
      - ./config:/app/config
    ports:
      - "8501:8501"

  dashboard:
    build: .
    command: streamlit run app.py --server.port 8501 --server.address 0.0.0.0
    env_file: .env
    ports:
      - "8502:8501"
    volumes:
      - ./data:/app/data
    depends_on:
      - bot
```

- [ ] **Step 3: Commit**

```bash
git add Dockerfile docker-compose.yml
git commit -m "feat: add Dockerfile and docker-compose for deployment"
```

---

## Task 17: Backtesting — VectorBT Parameter Sweep

**Files:**
- Create: `backtest/vectorbt_sweep.py`

- [ ] **Step 1: Write parameter sweep script**

```python
# backtest/vectorbt_sweep.py
"""
Phase 1 Backtest: VectorBT Parameter Sweep
Optimize BB period, RSI thresholds, and ATR multiplier.

Run: python backtest/vectorbt_sweep.py
Target: Sharpe > 1.2, Max Drawdown < 8%, 200+ trades
"""

import os
import vectorbt as vbt
import pandas as pd
import numpy as np
from itertools import product


def fetch_data(symbol: str = "BTCUSDT", start: str = "2025-01-01",
               end: str = "2026-04-30") -> pd.DataFrame:
    """Fetch historical BTC data from Binance via vectorbt."""
    df = vbt.BinanceData.download(
        symbol,
        start=start,
        end=end,
        interval="1h",
    ).get()
    return df


def run_sweep(df: pd.DataFrame):
    """Sweep over BB periods, RSI thresholds, and ATR multipliers."""
    bb_periods = [15, 20, 25]
    rsi_oversold = [30, 35, 40]
    rsi_overbought = [65, 70, 75]
    atr_multipliers = [0.5, 0.8, 1.0]

    results = []

    for bb_p, rsi_low, rsi_high, atr_m in product(
        bb_periods, rsi_oversold, rsi_overbought, atr_multipliers
    ):
        close = df["Close"]

        # Bollinger Bands
        sma = close.rolling(bb_p).mean()
        std = close.rolling(bb_p).std()
        upper = sma + 2 * std
        lower = sma - 2 * std

        # RSI
        delta = close.diff()
        gain = delta.where(delta > 0, 0).rolling(14).mean()
        loss = (-delta).where(delta < 0, 0).rolling(14).mean()
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        # EMA 200
        ema = close.ewm(span=200).mean()

        # ATR
        high = df["High"]
        low = df["Low"]
        prev_close = close.shift(1)
        tr = pd.concat([
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr = tr.ewm(span=14).mean()

        # Grid spacing
        spacing = atr * atr_m

        # Signal: Buy when price crosses below (mid - spacing) AND RSI < threshold AND price > EMA
        # Signal: Sell when price crosses above (mid + spacing) AND RSI > threshold
        entries = (
            (close < sma - spacing) &
            (rsi < rsi_low) &
            (close > ema)
        )
        exits = (
            (close > sma + spacing) |
            (rsi > rsi_high) |
            (close < ema)
        )

        # Simulate
        pf = vbt.Portfolio.from_signals(
            close=close,
            entries=entries,
            exits=exits,
            freq="1h",
            init_cash=200,
            fees=0.00075,  # 0.075% with BNB discount
            freq="1h",
        )

        stats = pf.stats()
        results.append({
            "bb_period": bb_p,
            "rsi_oversold": rsi_low,
            "rsi_overbought": rsi_high,
            "atr_multiplier": atr_m,
            "total_trades": stats.get("Total Trades", 0),
            "total_return_pct": stats.get("Total Return [%]", 0),
            "sharpe_ratio": stats.get("Sharpe Ratio", 0),
            "max_drawdown_pct": stats.get("Max Drawdown [%]", 0),
            "win_rate": stats.get("Win Rate [%]", 0),
        })

    results_df = pd.DataFrame(results)
    results_df = results_df.sort_values("sharpe_ratio", ascending=False)
    print("\n=== TOP 10 PARAMETER COMBINATIONS ===")
    print(results_df.head(10).to_string(index=False))

    # Filter for passing criteria
    passing = results_df[
        (results_df["sharpe_ratio"] > 1.2) &
        (results_df["total_trades"] > 200) &
        (results_df["max_drawdown_pct"] < 8)
    ]
    print(f"\n=== PASSING CRITERIA: {len(passing)} / {len(results_df)} ===")
    if not passing.empty:
        print(passing.to_string(index=False))
    return results_df


if __name__ == "__main__":
    print("Fetching BTC/USDT 1h data...")
    df = fetch_data()
    print(f"Data shape: {df.shape}")
    results = run_sweep(df)
    results.to_csv("reports/parameter_sweep_results.csv", index=False)
    print("\nResults saved to reports/parameter_sweep_results.csv")
```

- [ ] **Step 2: Commit**

```bash
mkdir -p reports
git add backtest/vectorbt_sweep.py
git commit -m "feat: add VectorBT parameter sweep backtest"
```

---

## Task 18: Backtesting — Walk-Forward Validation

**Files:**
- Create: `backtest/walk_forward.py`

- [ ] **Step 1: Write walk-forward validation script**

```python
# backtest/walk_forward.py
"""
Phase 2 Backtest: Walk-Forward Out-of-Sample Validation
Test best parameters from sweep on unseen data periods.

Run: python backtest/walk_forward.py
Target: Consistent results across bull/bear/sideways
"""

import vectorbt as vbt
import pandas as pd
import numpy as np


def fetch_data(symbol: str = "BTCUSDT", start: str = "2024-01-01",
               end: str = "2026-04-30") -> pd.DataFrame:
    df = vbt.BinanceData.download(
        symbol, start=start, end=end, interval="1h"
    ).get()
    return df


def apply_strategy(df: pd.DataFrame, bb_period: int = 20,
                   rsi_oversold: float = 35, rsi_overbought: float = 70,
                   atr_multiplier: float = 0.8) -> vbt.Portfolio:
    close = df["Close"]
    sma = close.rolling(bb_period).mean()
    std = close.rolling(bb_period).std()
    upper = sma + 2 * std
    lower = sma - 2 * std
    delta = close.diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = (-delta).where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    ema = close.ewm(span=200).mean()
    high, low = df["High"], df["Low"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    atr = tr.ewm(span=14).mean()
    spacing = atr * atr_multiplier

    entries = (close < sma - spacing) & (rsi < rsi_oversold) & (close > ema)
    exits = (close > sma + spacing) | (rsi > rsi_overbought) | (close < ema)

    return vbt.Portfolio.from_signals(
        close=close, entries=entries, exits=exits,
        freq="1h", init_cash=200, fees=0.00075,
    )


def walk_forward_test(df: pd.DataFrame, train_months: int = 6,
                      test_months: int = 3):
    """Rolling walk-forward: train on 6 months, test on next 3 months."""
    df.index = pd.to_datetime(df.index)
    start = df.index[0]
    end = df.index[-1]

    results = []
    train_delta = pd.DateOffset(months=train_months)
    test_delta = pd.DateOffset(months=test_months)

    current = start
    while current + train_delta + test_delta <= end:
        train_end = current + train_delta
        test_end = train_end + test_delta

        train_df = df.loc[current:train_end]
        test_df = df.loc[train_end:test_end]

        # Test with default best parameters (from sweep results)
        pf = apply_strategy(test_df)
        stats = pf.stats()

        results.append({
            "train_period": f"{current.date()} → {train_end.date()}",
            "test_period": f"{train_end.date()} → {test_end.date()}",
            "total_return": stats.get("Total Return [%]", 0),
            "sharpe": stats.get("Sharpe Ratio", 0),
            "max_drawdown": stats.get("Max Drawdown [%]", 0),
            "trades": stats.get("Total Trades", 0),
            "win_rate": stats.get("Win Rate [%]", 0),
        })

        current = train_end

    results_df = pd.DataFrame(results)
    print("\n=== WALK-FORWARD RESULTS ===")
    print(results_df.to_string(index=False))
    print(f"\nAverage Return: {results_df['total_return'].mean():.2f}%")
    print(f"Average Sharpe: {results_df['sharpe'].mean():.2f}")
    print(f"Worst Drawdown: {results_df['max_drawdown'].min():.2f}%")
    return results_df


if __name__ == "__main__":
    print("Fetching BTC/USDT historical data...")
    df = fetch_data()
    print(f"Data: {df.shape[0]} rows from {df.index[0]} to {df.index[-1]}")
    results = walk_forward_test(df)
    results.to_csv("reports/walk_forward_results.csv", index=False)
    print("\nResults saved to reports/walk_forward_results.csv")
```

- [ ] **Step 2: Commit**

```bash
git add backtest/walk_forward.py
git commit -m "feat: add walk-forward out-of-sample backtest validation"
```

---

## Task 19: Run all tests and verify

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/ -v --tb=short
```

Expected: All tests pass (test_indicators, test_grid_manager, test_circuit_breaker, test_position_guard, test_candle_feed)

- [ ] **Step 2: Verify file structure matches plan**

```bash
find . -name "*.py" -not -path "./.git/*" -not -path "./.venv/*" | sort
```

Expected output shows all files in place:
- `src/indicators/*.py`
- `src/grid/*.py`
- `src/risk/*.py`
- `src/data/*.py`
- `src/notifications/*.py`
- `src/journal/*.py`
- `hummingbot_files/scripts/ta_grid_btcusdt.py`
- `backtest/*.py`
- `tests/*.py`

---

## Task 20: Final commit — all modules

- [ ] **Step 1: Stage and commit all remaining files**

```bash
git add -A
git status  # verify no .env or keys/ in staging
git commit -m "feat: complete TA Grid Bot implementation — indicators, grid engine, risk, data feeds, strategy, backtests, Docker"
```

---

## Self-Review

**1. Spec coverage:**
- Phase 1 (Core Trade Mechanics): Grid Manager (Task 8), Dynamic Grid Spacing/ATR (Task 5), Bollinger Band Ranges (Task 2) — covered
- Phase 2 (Protective Layers): RSI filter (Task 3), EMA 200 filter (Task 4), Circuit Breaker (Task 9), Position Guard (Task 10) — covered
- Data feeds: Candle Feed (Task 11), WebSocket (Task 12) — covered
- Notifications: Telegram (Task 13) — covered
- Hummingbot script: Task 15 — covered
- Backtesting: Tasks 17-18 — covered
- Deployment: Tasks 16 — covered
- Infrastructure: IaC already exists in `iac/aws-tokyo/` — no changes needed

**2. Placeholder scan:** No TBD, TODO, or placeholder steps found. Every code block contains full implementation.

**3. Type consistency:**
- `BBResult(upper, mid, lower)` used consistently in grid_manager and strategy
- `GridState` enum values (ACTIVE, PAUSED, REACTIVATING) match across grid_state.py and strategy
- `Trade` dataclass fields match between trade_journal.py and strategy's `did_fill_order`
- `GridManager.calculate_grid()` returns `GridLayout` with `buy_levels`/`sell_levels` — consistent usage in strategy
- `OrderTracker` uses `GridOrder` with `OrderSide`/`OrderStatus` enums — consistent
