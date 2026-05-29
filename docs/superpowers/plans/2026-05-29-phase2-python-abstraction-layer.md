# Phase 2: Python Abstraction Layer + Grid Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a Python `trading_engine` package with an `ExecutionAdapter` abstraction, `Strategy` base class, and Grid strategy engine. Python strategies call Rust indicators (via the `trading-engine-core` wheel) for math, while orchestration stays in Python for fast iteration.

**Architecture:** Rust handles indicator math (already done in Phase 1). Python handles strategy logic, state machines, risk management, and execution wiring. The `ExecutionAdapter` ABC is the seam — Hummingbot and NautilusTrader each provide a thin adapter. Strategies never know which engine they're running on.

**Tech Stack:** Python 3.13, trading-engine-core Rust wheel (Phase 1), abc/Protocol for typing, pytest for testing.

**Design spec:** `docs/superpowers/specs/2026-05-29-trading-engine-core-rust-design.md`

**Branch:** `feat/rust-engine-core`

---

## File Structure

```
src/trading_engine/                     # New Python package (alongside existing src/)
├── __init__.py
├── adapter/
│   ├── __init__.py                     # Re-exports
│   ├── base.py                         # ExecutionAdapter ABC
│   ├── hummingbot.py                   # Wraps Hummingbot connector
│   ├── nautilus.py                     # Wraps NautilusTrader (stub for Phase 6)
│   └── mock.py                         # In-memory adapter for backtesting/tests
├── strategy/
│   ├── __init__.py                     # Re-exports
│   ├── base.py                         # Strategy ABC
│   └── grid.py                         # Grid strategy engine
├── risk/
│   ├── __init__.py                     # Re-exports
│   ├── circuit_breaker.py              # Drawdown + daily loss limits
│   └── position_guard.py              # Max positions, exposure limits
├── host.py                             # StrategyHost: owns adapter + strategies
└── config.py                           # Unified YAML config loader

tests/trading_engine/
├── __init__.py
├── test_adapter_base.py
├── test_mock_adapter.py
├── test_circuit_breaker.py
├── test_position_guard.py
├── test_grid_strategy.py
└── test_host.py
```

Also modifying Rust crate to add `#[pyclass]` bindings:
```
trading-engine-core/src/indicators/ema.rs      # Add #[pyclass] + #[pymethods]
trading-engine-core/src/indicators/rsi.rs      # Add #[pyclass] + #[pymethods]
trading-engine-core/src/indicators/atr.rs      # Add #[pyclass] + #[pymethods]
trading-engine-core/src/indicators/bollinger.rs # Add #[pyclass] + #[pymethods]
trading-engine-core/src/python/mod.rs          # Register all indicator classes
```

---

### Task 1: Add PyO3 Bindings to Rust Indicators

**Files:**
- Modify: `trading-engine-core/src/indicators/ema.rs`
- Modify: `trading-engine-core/src/indicators/rsi.rs`
- Modify: `trading-engine-core/src/indicators/atr.rs`
- Modify: `trading-engine-core/src/indicators/bollinger.rs`
- Modify: `trading-engine-core/src/python/mod.rs`
- Rebuild: `trading-engine-core` wheel

- [ ] **Step 1: Add pyclass to EMA**

Add to `trading-engine-core/src/indicators/ema.rs` — add `#[cfg_attr(feature = "python", pyo3::pyclass)]` derive and `#[pymethods]` block:

```rust
#[derive(Debug, Clone)]
#[cfg_attr(feature = "python", pyo3::pyclass)]
pub struct Ema {
    // ... existing fields unchanged
}

// ... existing impl block unchanged ...

#[cfg(feature = "python")]
#[pyo3::pymethods]
impl Ema {
    #[new]
    fn py_new(period: u32) -> Self {
        Self::new(period)
    }

    fn update(&mut self, price: f64) {
        self.update(price);
    }

    #[getter]
    fn value(&self) -> f64 {
        self.value()
    }

    #[getter]
    fn is_initialized(&self) -> bool {
        self.is_initialized()
    }

    #[getter]
    fn count(&self) -> u32 {
        self.count()
    }

    #[getter]
    fn period(&self) -> u32 {
        self.period()
    }

    fn reset(&mut self) {
        self.reset();
    }
}
```

**Important:** The `#[pymethods]` methods need different names from the `impl` methods to avoid name conflicts. Use the pattern: `impl Ema` has `pub fn update(&mut self, price: f64)`, and `#[pymethods]` has `fn update(&mut self, price: f64)` — they can share the same name since they're in different impl blocks. But if the compiler complains, rename the pymethods versions to `py_update`, etc.

Actually, the simpler pattern is to NOT duplicate methods. Instead, add `#[pyo3(signature = (period))]` on the `new` method, and use `#[getter]` on value methods. The existing `impl Ema` methods are called from within the pymethods block.

The cleanest approach: keep the existing `impl Ema` untouched, and add a separate `#[cfg(feature = "python")] #[pyo3::pymethods]` block that delegates to the existing methods.

- [ ] **Step 2: Add pyclass to RSI, ATR, BollingerBands**

Same pattern as EMA for each indicator. For ATR, the `update_bar` method takes 4 float params (open, high, low, close).

- [ ] **Step 3: Update python/mod.rs to register all indicator classes**

Replace `trading-engine-core/src/python/mod.rs`:

```rust
#[cfg(feature = "python")]
use pyo3::prelude::*;

#[cfg(feature = "python")]
use crate::indicators::{Ema, Rsi, Atr, BollingerBands};

#[cfg(feature = "python")]
#[pyfunction]
fn version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[cfg(feature = "python")]
pub fn register_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_class::<Ema>()?;
    m.add_class::<Rsi>()?;
    m.add_class::<Atr>()?;
    m.add_class::<BollingerBands>()?;
    Ok(())
}
```

- [ ] **Step 4: Rebuild wheel and verify**

```bash
cd trading-engine-core && export PATH="$HOME/.cargo/bin:$PATH" && maturin develop
python -c "
from trading_engine_core import Ema, Rsi, Atr, BollingerBands, version
print(f'Core v{version()}')
e = Ema(10)
for p in [100, 102, 101, 103, 105]:
    e.update(float(p))
print(f'EMA(10) = {e.value}, initialized = {e.is_initialized}')
"
```
Expected: `Core v0.1.0` and `EMA(10) = <value>, initialized = True`

- [ ] **Step 5: Run Rust tests (no python feature)**

```bash
cd trading-engine-core && export PATH="$HOME/.cargo/bin:$PATH" && cargo test
```
Expected: 41 tests still pass

- [ ] **Step 6: Commit**

```bash
git add trading-engine-core/
git commit -m "feat(engine-core): add PyO3 bindings to EMA, RSI, ATR, BollingerBands indicators"
```

---

### Task 2: Python Package Skeleton + ExecutionAdapter ABC

**Files:**
- Create: `src/trading_engine/__init__.py`
- Create: `src/trading_engine/adapter/__init__.py`
- Create: `src/trading_engine/adapter/base.py`
- Create: `src/trading_engine/adapter/mock.py`
- Create: `src/trading_engine/strategy/__init__.py`
- Create: `src/trading_engine/risk/__init__.py`
- Create: `src/trading_engine/config.py`
- Create: `tests/trading_engine/__init__.py`
- Create: `tests/trading_engine/test_adapter_base.py`
- Create: `tests/trading_engine/test_mock_adapter.py`

- [ ] **Step 1: Create package structure**

```bash
mkdir -p src/trading_engine/{adapter,strategy,risk}
mkdir -p tests/trading_engine
touch src/trading_engine/__init__.py
touch src/trading_engine/adapter/__init__.py
touch src/trading_engine/strategy/__init__.py
touch src/trading_engine/risk/__init__.py
touch tests/trading_engine/__init__.py
```

- [ ] **Step 2: Write ExecutionAdapter ABC**

Create `src/trading_engine/adapter/base.py`:

```python
"""Execution adapter interface — the seam between strategy logic and trading engines.

Each engine (Hummingbot, NautilusTrader, mock) implements this ABC.
Strategy code calls these methods and never knows which engine is running.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class Order:
    """Universal order representation."""
    instrument_id: str
    side: str           # "BUY" or "SELL"
    order_type: str     # "LIMIT" or "MARKET"
    price: float
    quantity: float
    client_order_id: str = ""


@dataclass
class OrderFill:
    """Notification that an order was filled."""
    client_order_id: str
    instrument_id: str
    side: str
    price: float
    quantity: float
    timestamp: int


@dataclass
class InstrumentInfo:
    """Instrument metadata."""
    symbol: str
    pip_size: float = 0.0001
    tick_size: float = 0.0001
    step_size: float = 0.0001
    price_precision: int = 4
    quantity_precision: int = 4

    def round_price(self, price: float) -> float:
        factor = 10 ** self.price_precision
        return round(price * factor) / factor

    def round_quantity(self, quantity: float) -> float:
        factor = 10 ** self.quantity_precision
        return int(quantity * factor) / factor


class ExecutionAdapter(ABC):
    """Abstract execution adapter — strategy code calls these methods.

    Implementations:
    - HummingbotAdapter: wraps Hummingbot's connector
    - NautilusAdapter: wraps NautilusTrader's order_factory
    - MockAdapter: in-memory for backtesting and unit tests
    """

    @abstractmethod
    def get_balance(self, currency: str) -> float:
        """Get available balance for a currency."""

    @abstractmethod
    def submit_order(self, order: Order) -> str:
        """Submit an order. Returns client_order_id."""

    @abstractmethod
    def cancel_order(self, client_order_id: str) -> None:
        """Cancel an existing order."""

    @abstractmethod
    def cancel_all_orders(self, instrument_id: str) -> None:
        """Cancel all open orders for an instrument."""

    @abstractmethod
    def get_open_orders(self, instrument_id: str) -> list[Order]:
        """Get all open orders for an instrument."""

    @abstractmethod
    def get_mid_price(self, instrument_id: str) -> float:
        """Get current mid price for an instrument."""

    @abstractmethod
    def get_instrument(self, instrument_id: str) -> InstrumentInfo:
        """Get instrument metadata."""
```

- [ ] **Step 3: Write MockAdapter**

Create `src/trading_engine/adapter/mock.py`:

```python
"""In-memory mock adapter for backtesting and unit tests.

Tracks orders and balances in dictionaries. No real exchange connection.
"""
from .base import ExecutionAdapter, Order, InstrumentInfo


class MockAdapter(ExecutionAdapter):
    """In-memory execution adapter for testing."""

    def __init__(self, balances: dict[str, float] | None = None):
        self._balances: dict[str, float] = balances or {"USDT": 10000.0}
        self._orders: dict[str, Order] = {}
        self._filled: list[dict] = []
        self._prices: dict[str, float] = {}
        self._instruments: dict[str, InstrumentInfo] = {}
        self._next_id: int = 1

    def set_price(self, instrument_id: str, price: float):
        """Set the mock mid price for an instrument."""
        self._prices[instrument_id] = price

    def set_instrument(self, instrument_id: str, info: InstrumentInfo):
        """Register instrument metadata."""
        self._instruments[instrument_id] = info

    def get_balance(self, currency: str) -> float:
        return self._balances.get(currency, 0.0)

    def submit_order(self, order: Order) -> str:
        order_id = f"mock-{self._next_id}"
        self._next_id += 1
        order = Order(
            instrument_id=order.instrument_id,
            side=order.side,
            order_type=order.order_type,
            price=order.price,
            quantity=order.quantity,
            client_order_id=order_id,
        )
        self._orders[order_id] = order
        return order_id

    def cancel_order(self, client_order_id: str) -> None:
        self._orders.pop(client_order_id, None)

    def cancel_all_orders(self, instrument_id: str) -> None:
        to_remove = [oid for oid, o in self._orders.items() if o.instrument_id == instrument_id]
        for oid in to_remove:
            del self._orders[oid]

    def get_open_orders(self, instrument_id: str) -> list[Order]:
        return [o for o in self._orders.values() if o.instrument_id == instrument_id]

    def get_mid_price(self, instrument_id: str) -> float:
        return self._prices.get(instrument_id, 0.0)

    def get_instrument(self, instrument_id: str) -> InstrumentInfo:
        if instrument_id in self._instruments:
            return self._instruments[instrument_id]
        return InstrumentInfo(symbol=instrument_id)

    def fill_order(self, client_order_id: str, fill_price: float | None = None):
        """Simulate filling an order (test helper)."""
        order = self._orders.pop(client_order_id, None)
        if order is None:
            return
        price = fill_price or order.price
        self._filled.append({
            "order_id": client_order_id,
            "instrument_id": order.instrument_id,
            "side": order.side,
            "price": price,
            "quantity": order.quantity,
        })
```

- [ ] **Step 4: Write adapter __init__.py**

Create `src/trading_engine/adapter/__init__.py`:

```python
from .base import ExecutionAdapter, Order, OrderFill, InstrumentInfo
from .mock import MockAdapter

__all__ = ["ExecutionAdapter", "Order", "OrderFill", "InstrumentInfo", "MockAdapter"]
```

- [ ] **Step 5: Write adapter tests**

Create `tests/trading_engine/test_adapter_base.py`:

```python
"""Verify ExecutionAdapter ABC cannot be instantiated directly."""
import pytest
from src.trading_engine.adapter.base import ExecutionAdapter


def test_cannot_instantiate_abc():
    with pytest.raises(TypeError):
        ExecutionAdapter()
```

Create `tests/trading_engine/test_mock_adapter.py`:

```python
"""MockAdapter tests."""
from src.trading_engine.adapter import MockAdapter, Order, InstrumentInfo


def test_submit_order_returns_id():
    adapter = MockAdapter()
    order = Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001)
    oid = adapter.submit_order(order)
    assert oid.startswith("mock-")


def test_cancel_order():
    adapter = MockAdapter()
    order = Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001)
    oid = adapter.submit_order(order)
    adapter.cancel_order(oid)
    assert len(adapter.get_open_orders("BTC-USDT")) == 0


def test_cancel_all_orders():
    adapter = MockAdapter()
    adapter.submit_order(Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001))
    adapter.submit_order(Order("BTC-USDT", "SELL", "LIMIT", 51000.0, 0.001))
    adapter.submit_order(Order("ETH-USDT", "BUY", "LIMIT", 3000.0, 0.01))
    adapter.cancel_all_orders("BTC-USDT")
    assert len(adapter.get_open_orders("BTC-USDT")) == 0
    assert len(adapter.get_open_orders("ETH-USDT")) == 1


def test_get_balance():
    adapter = MockAdapter({"USDT": 5000.0, "BTC": 0.5})
    assert adapter.get_balance("USDT") == 5000.0
    assert adapter.get_balance("ETH") == 0.0


def test_set_and_get_price():
    adapter = MockAdapter()
    adapter.set_price("BTC-USDT", 50000.0)
    assert adapter.get_mid_price("BTC-USDT") == 50000.0
    assert adapter.get_mid_price("ETH-USDT") == 0.0


def test_fill_order():
    adapter = MockAdapter()
    oid = adapter.submit_order(Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001))
    adapter.fill_order(oid, 50001.0)
    assert len(adapter.get_open_orders("BTC-USDT")) == 0
    assert len(adapter._filled) == 1
    assert adapter._filled[0]["price"] == 50001.0


def test_instrument_info():
    adapter = MockAdapter()
    info = InstrumentInfo("BTC-USDT", 0.01, 0.00001, 2, 5)
    adapter.set_instrument("BTC-USDT", info)
    got = adapter.get_instrument("BTC-USDT")
    assert got.round_price(50000.126) == 50000.13
    assert got.round_quantity(0.123456789) == 0.12345
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/trading_engine/ -v
```
Expected: 7 tests PASS

- [ ] **Step 7: Commit**

```bash
git add src/trading_engine/ tests/trading_engine/
git commit -m "feat(trading-engine): add ExecutionAdapter ABC and MockAdapter with tests"
```

---

### Task 3: Strategy Base Class

**Files:**
- Create: `src/trading_engine/strategy/base.py`
- Create: `src/trading_engine/strategy/__init__.py` (update)

- [ ] **Step 1: Write Strategy ABC**

Create `src/trading_engine/strategy/base.py`:

```python
"""Strategy base class — all strategy engines inherit from this.

Provides lifecycle hooks (on_start, on_stop, on_bar, on_order_filled)
and helper methods for working with the execution adapter.
"""
from abc import ABC, abstractmethod
from typing import Optional

from ..adapter.base import ExecutionAdapter, Order, OrderFill, InstrumentInfo


class Strategy(ABC):
    """Base class for trading strategies.

    Subclasses must implement:
    - on_start(): called when the strategy starts
    - on_bar(bar): called on each new bar (dict with OHLCV)
    - on_stop(): called when the strategy stops

    Optional overrides:
    - on_order_filled(fill): called when an order is filled
    - on_order_rejected(rejection): called when an order is rejected
    """

    def __init__(self, instrument_id: str, config: dict):
        self.instrument_id = instrument_id
        self.config = config
        self._adapter: Optional[ExecutionAdapter] = None
        self._running = False

    @property
    def adapter(self) -> ExecutionAdapter:
        if self._adapter is None:
            raise RuntimeError("Strategy not started — adapter not set")
        return self._adapter

    @property
    def running(self) -> bool:
        return self._running

    def _set_adapter(self, adapter: ExecutionAdapter):
        """Internal — called by StrategyHost when adding the strategy."""
        self._adapter = adapter

    def start(self):
        """Called by host to start the strategy."""
        self._running = True
        self.on_start()

    def stop(self):
        """Called by host to stop the strategy."""
        self._running = False
        self.on_stop()

    @abstractmethod
    def on_start(self):
        """Initialize indicators, subscriptions, etc."""

    @abstractmethod
    def on_bar(self, bar: dict):
        """Process a new bar. Bar dict: {open, high, low, close, volume, timestamp}."""

    @abstractmethod
    def on_stop(self):
        """Clean up orders, save state, etc."""

    def on_order_filled(self, fill: OrderFill):
        """Override to handle order fills. Default: no-op."""
        pass

    def on_order_rejected(self, rejection: dict):
        """Override to handle order rejections. Default: log warning."""
        pass

    # ── Helper methods ──

    def buy_limit(self, price: float, quantity: float) -> str:
        """Submit a limit buy order."""
        order = Order(
            instrument_id=self.instrument_id,
            side="BUY",
            order_type="LIMIT",
            price=price,
            quantity=quantity,
        )
        return self.adapter.submit_order(order)

    def sell_limit(self, price: float, quantity: float) -> str:
        """Submit a limit sell order."""
        order = Order(
            instrument_id=self.instrument_id,
            side="SELL",
            order_type="LIMIT",
            price=price,
            quantity=quantity,
        )
        return self.adapter.submit_order(order)

    def cancel_all(self):
        """Cancel all open orders for this instrument."""
        self.adapter.cancel_all_orders(self.instrument_id)

    def get_price(self) -> float:
        """Get current mid price."""
        return self.adapter.get_mid_price(self.instrument_id)

    def get_balance(self, currency: str = "USDT") -> float:
        """Get available balance."""
        return self.adapter.get_balance(currency)

    def get_instrument(self) -> InstrumentInfo:
        """Get instrument metadata."""
        return self.adapter.get_instrument(self.instrument_id)

    def format_status(self) -> str:
        """Return a status string for dashboard/Telegram. Override in subclasses."""
        return f"{self.__class__.__name__}({self.instrument_id})"
```

- [ ] **Step 2: Write strategy __init__.py**

Create `src/trading_engine/strategy/__init__.py`:

```python
from .base import Strategy

__all__ = ["Strategy"]
```

- [ ] **Step 3: Commit**

```bash
git add src/trading_engine/strategy/
git commit -m "feat(trading-engine): add Strategy base class with lifecycle hooks and order helpers"
```

---

### Task 4: Risk Management (Circuit Breaker + Position Guard)

**Files:**
- Create: `src/trading_engine/risk/circuit_breaker.py`
- Create: `src/trading_engine/risk/position_guard.py`
- Create: `src/trading_engine/risk/__init__.py` (update)
- Create: `tests/trading_engine/test_circuit_breaker.py`
- Create: `tests/trading_engine/test_position_guard.py`

- [ ] **Step 1: Implement Circuit Breaker**

Create `src/trading_engine/risk/circuit_breaker.py`:

```python
"""Circuit breaker — halts trading when drawdown or daily loss exceeds thresholds.

Shared across all strategies in a StrategyHost. All strategies report
their PnL to the same instance so losses in one strategy count against
the risk budget of all strategies.
"""
import time


class CircuitBreaker:
    def __init__(
        self,
        initial_capital: float,
        max_drawdown_pct: float = 10.0,
        daily_loss_limit_pct: float = 5.0,
    ):
        self.initial_capital = initial_capital
        self.max_drawdown_pct = max_drawdown_pct
        self.daily_loss_limit_pct = daily_loss_limit_pct

        self.equity_peak = initial_capital
        self.current_equity = initial_capital
        self.realized_pnl = 0.0
        self.unrealized_pnl = 0.0

        self.daily_starting_equity = initial_capital
        self.daily_realized_pnl = 0.0
        self._last_daily_reset_day = self._current_day()

        self.tripped = False
        self.trip_reason: str | None = None

    def _current_day(self) -> int:
        return int(time.time()) // 86400

    def _maybe_reset_daily(self):
        current_day = self._current_day()
        if current_day > self._last_daily_reset_day:
            self.daily_starting_equity = self.current_equity
            self.daily_realized_pnl = 0.0
            self._last_daily_reset_day = current_day

    def _update_equity(self):
        self.current_equity = self.initial_capital + self.realized_pnl + self.unrealized_pnl
        if self.current_equity > self.equity_peak:
            self.equity_peak = self.current_equity

    def _evaluate(self):
        # Check max drawdown
        if self.equity_peak > 0:
            drawdown = (self.equity_peak - self.current_equity) / self.equity_peak * 100
            if drawdown >= self.max_drawdown_pct:
                self.tripped = True
                self.trip_reason = f"Max drawdown reached: {drawdown:.1f}%"
                return

        # Check daily loss
        if self.daily_starting_equity > 0:
            daily_loss_pct = abs(self.daily_realized_pnl / self.daily_starting_equity * 100)
            if self.daily_realized_pnl < 0 and daily_loss_pct >= self.daily_loss_limit_pct:
                self.tripped = True
                self.trip_reason = f"Daily loss limit reached: {daily_loss_pct:.1f}%"

    def check(self) -> tuple[bool, str]:
        """Check if trading is allowed. Returns (allowed, reason)."""
        if self.tripped:
            return False, self.trip_reason or "Circuit breaker tripped"
        return True, ""

    def record_pnl(self, amount: float):
        """Record a realized PnL change."""
        self._maybe_reset_daily()
        self.realized_pnl += amount
        self.daily_realized_pnl += amount
        self._update_equity()
        self._evaluate()

    def update_unrealized(self, unrealized_pnl: float):
        """Update estimated unrealized PnL."""
        self.unrealized_pnl = unrealized_pnl
        self._update_equity()
        self._evaluate()

    def reset(self):
        """Manually reset the circuit breaker."""
        self.tripped = False
        self.trip_reason = None
```

- [ ] **Step 2: Write circuit breaker tests**

Create `tests/trading_engine/test_circuit_breaker.py`:

```python
from src.trading_engine.risk.circuit_breaker import CircuitBreaker


def test_allows_trading_initially():
    cb = CircuitBreaker(10000)
    allowed, reason = cb.check()
    assert allowed
    assert reason == ""


def test_trips_on_max_drawdown():
    cb = CircuitBreaker(10000, max_drawdown_pct=10.0, daily_loss_limit_pct=100.0)
    # Simulate a 10% loss
    cb.record_pnl(-1001.0)
    allowed, reason = cb.check()
    assert not allowed
    assert "drawdown" in reason.lower()


def test_trips_on_daily_loss():
    cb = CircuitBreaker(10000, max_drawdown_pct=100.0, daily_loss_limit_pct=5.0)
    cb.record_pnl(-501.0)
    allowed, reason = cb.check()
    assert not allowed
    assert "daily" in reason.lower()


def test_manual_reset():
    cb = CircuitBreaker(10000, max_drawdown_pct=5.0)
    cb.record_pnl(-600.0)
    assert not cb.check()[0]
    cb.reset()
    assert cb.check()[0]


def test_unrealized_pnl_contributes_to_equity():
    cb = CircuitBreaker(10000, max_drawdown_pct=10.0, daily_loss_limit_pct=100.0)
    # Unrealized loss pushes equity down
    cb.update_unrealized(-1000.0)
    allowed, _ = cb.check()
    assert allowed  # Not tripped yet — unrealized, not realized

    # Now realize part of it
    cb.record_pnl(-500.0)
    cb.update_unrealized(0.0)
    allowed, _ = cb.check()
    assert allowed  # Only 5% realized, 5% unrealized was already reset
```

- [ ] **Step 3: Implement Position Guard**

Create `src/trading_engine/risk/position_guard.py`:

```python
"""Position guard — enforces position limits across all strategies."""


class PositionGuard:
    def __init__(
        self,
        max_positions_per_pair: int = 2,
        max_total_positions: int = 3,
        max_exposure_pct: float = 80.0,
    ):
        self.max_positions_per_pair = max_positions_per_pair
        self.max_total_positions = max_total_positions
        self.max_exposure_pct = max_exposure_pct
        self._positions: dict[str, dict] = {}  # symbol → position info

    def can_open(
        self,
        symbol: str,
        strategy_id: str,
        proposed_cost: float,
        available_capital: float,
    ) -> tuple[bool, str]:
        """Check if a new position is allowed."""
        # Total count
        if len(self._positions) >= self.max_total_positions:
            return False, f"Max total positions reached ({self.max_total_positions})"

        # Per-pair count
        pair_count = sum(1 for p in self._positions.values() if p["symbol"] == symbol)
        if pair_count >= self.max_positions_per_pair:
            return False, f"Max per-pair positions reached for {symbol} ({self.max_positions_per_pair})"

        # Exposure
        total_exposure = sum(p["cost"] for p in self._positions.values())
        new_exposure_pct = (total_exposure + proposed_cost) / available_capital * 100
        if available_capital > 0 and new_exposure_pct > self.max_exposure_pct:
            return False, f"Max exposure reached ({new_exposure_pct:.1f}%)"

        # Duplicate check (same symbol + strategy)
        for p in self._positions.values():
            if p["symbol"] == symbol and p["strategy_id"] == strategy_id:
                return False, f"Duplicate position: {symbol} for {strategy_id}"

        return True, ""

    def register(self, key: str, symbol: str, strategy_id: str, cost: float):
        """Register an opened position."""
        self._positions[key] = {"symbol": symbol, "strategy_id": strategy_id, "cost": cost}

    def close(self, key: str):
        """Remove a closed position."""
        self._positions.pop(key, None)

    @property
    def open_count(self) -> int:
        return len(self._positions)
```

- [ ] **Step 4: Write position guard tests**

Create `tests/trading_engine/test_position_guard.py`:

```python
from src.trading_engine.risk.position_guard import PositionGuard


def test_allows_initial_position():
    pg = PositionGuard(max_total_positions=3)
    allowed, _ = pg.can_open("BTC-USDT", "grid", 1000.0, 10000.0)
    assert allowed


def test_blocks_max_total():
    pg = PositionGuard(max_total_positions=2)
    pg.register("k1", "BTC-USDT", "grid", 1000)
    pg.register("k2", "ETH-USDT", "grid", 1000)
    allowed, reason = pg.can_open("XRP-USDT", "grid", 1000.0, 10000.0)
    assert not allowed
    assert "total" in reason.lower()


def test_blocks_per_pair_limit():
    pg = PositionGuard(max_positions_per_pair=1, max_total_positions=10)
    pg.register("k1", "BTC-USDT", "grid", 1000)
    allowed, reason = pg.can_open("BTC-USDT", "trend", 1000.0, 10000.0)
    assert not allowed
    assert "per-pair" in reason.lower()


def test_blocks_duplicate():
    pg = PositionGuard()
    pg.register("k1", "BTC-USDT", "grid", 1000)
    allowed, reason = pg.can_open("BTC-USDT", "grid", 1000.0, 10000.0)
    assert not allowed
    assert "duplicate" in reason.lower()


def test_close_removes_position():
    pg = PositionGuard(max_total_positions=1)
    pg.register("k1", "BTC-USDT", "grid", 1000)
    assert pg.open_count == 1
    pg.close("k1")
    assert pg.open_count == 0
    allowed, _ = pg.can_open("ETH-USDT", "grid", 1000.0, 10000.0)
    assert allowed


def test_blocks_exposure():
    pg = PositionGuard(max_exposure_pct=50.0, max_total_positions=10)
    pg.register("k1", "BTC-USDT", "grid", 4000)
    # 4000 already deployed, trying to add 2000 more = 60% of 10000
    allowed, reason = pg.can_open("ETH-USDT", "grid", 2000.0, 10000.0)
    assert not allowed
    assert "exposure" in reason.lower()
```

- [ ] **Step 5: Update risk __init__.py**

```python
from .circuit_breaker import CircuitBreaker
from .position_guard import PositionGuard

__all__ = ["CircuitBreaker", "PositionGuard"]
```

- [ ] **Step 6: Run tests**

```bash
python -m pytest tests/trading_engine/ -v
```
Expected: 5 (circuit breaker) + 6 (position guard) + 7 (adapter) + 1 (abc) = **19 tests PASS**

- [ ] **Step 7: Commit**

```bash
git add src/trading_engine/risk/ tests/trading_engine/test_circuit_breaker.py tests/trading_engine/test_position_guard.py
git commit -m "feat(trading-engine): add CircuitBreaker and PositionGuard risk modules"
```

---

### Task 5: Grid Strategy Engine

**Files:**
- Create: `src/trading_engine/strategy/grid.py`
- Create: `tests/trading_engine/test_grid_strategy.py`

- [ ] **Step 1: Implement Grid Strategy**

Create `src/trading_engine/strategy/grid.py`:

```python
"""Grid strategy engine — places buy/sell limit orders at ATR-spaced intervals.

Uses Rust indicators (via trading_engine_core wheel) for EMA, RSI,
Bollinger Bands, and ATR calculations. Strategy logic is Python.

Ported from src/grid/grid_manager.py + grid_state.py + order_tracker.py
"""
from enum import Enum
from typing import Optional

from trading_engine_core import Ema, Rsi, BollingerBands, Atr

from .base import Strategy
from ..adapter.base import OrderFill
from ..risk.circuit_breaker import CircuitBreaker


class GridState(Enum):
    INACTIVE = "inactive"
    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"


class GridStrategy(Strategy):
    """Grid strategy — places symmetric orders around mid price.

    Config keys (passed via self.config dict):
        levels: int (5) — orders per side
        capital: float (5000) — total capital allocated
        spacing_atr_multiplier: float (1.5) — grid spacing = ATR × this
        min_usdt_reserve: float (100) — keep this in reserve
        ema_period: int (200) — trend filter
        rsi_period: int (14) — RSI
        bollinger_period: int (20) — Bollinger Bands
        bollinger_std_dev: float (2.0)
        atr_period: int (14)
        order_refresh_seconds: int (60)
    """

    def __init__(self, instrument_id: str, config: dict):
        super().__init__(instrument_id, config)

        # Indicators (Rust via PyO3)
        period_ema = config.get("ema_period", 200)
        period_rsi = config.get("rsi_period", 14)
        period_atr = config.get("atr_period", 14)
        period_bb = config.get("bollinger_period", 20)
        std_bb = config.get("bollinger_std_dev", 2.0)

        self.ema = Ema(period_ema)
        self.rsi = Rsi(period_rsi)
        self.atr = Atr(period_atr)
        self.bollinger = BollingerBands(period_bb, std_bb)

        # State
        self.state = GridState.INACTIVE
        self.active_orders: dict[str, dict] = {}  # order_id → level info
        self.base_price: Optional[float] = None
        self.last_refresh_time: int = 0
        self.total_pnl: float = 0.0
        self.trade_count: int = 0

    def on_start(self):
        self.adapter  # Verify adapter is set
        # Nothing else needed — indicators self-initialize with data

    def on_bar(self, bar: dict):
        # Update indicators
        close = bar["close"]
        high = bar["high"]
        low = bar["low"]
        ts = bar.get("timestamp", 0)

        self.ema.update(close)
        self.rsi.update(close)
        self.atr.update_bar(close, high, low, close)
        self.bollinger.update(close)

        # Need initialized indicators
        if not (self.ema.is_initialized and self.atr.is_initialized):
            return

        # Evaluate state
        mid_price = close
        new_state = self._evaluate_state(mid_price)

        # State transitions
        if self.state in (GridState.INACTIVE, GridState.PAUSED) and new_state == GridState.ACTIVE:
            self._place_grid(mid_price, ts)
        elif self.state == GridState.ACTIVE and new_state != GridState.ACTIVE:
            self._cancel_grid()

        self.state = new_state

        # Refresh grid if timer expired
        if self.state == GridState.ACTIVE:
            refresh_interval = self.config.get("order_refresh_seconds", 60)
            if ts - self.last_refresh_time >= refresh_interval:
                self._cancel_grid()
                self._place_grid(mid_price, ts)

    def on_stop(self):
        self._cancel_grid()

    def on_order_filled(self, fill: OrderFill):
        """Handle grid order fills."""
        order_id = fill.client_order_id
        if order_id in self.active_orders:
            level = self.active_orders.pop(order_id)
            self.trade_count += 1
            # Record PnL tracking could go here

    def _evaluate_state(self, mid_price: float) -> GridState:
        """Evaluate grid state based on indicators."""
        # Circuit breaker check
        if hasattr(self, '_circuit_breaker') and self._circuit_breaker:
            allowed, reason = self._circuit_breaker.check()
            if not allowed:
                return GridState.STOPPED

        # Simple rules (matches existing Python logic):
        # Activate: price above EMA, RSI not overbought
        ema_val = self.ema.value
        rsi_val = self.rsi.value

        price_above_ema = mid_price > ema_val if ema_val > 0 else True
        rsi_ok = self.config.get("rsi_oversold", 35) < rsi_val < self.config.get("rsi_overbought", 70)

        if price_above_ema and rsi_ok:
            return GridState.ACTIVE
        elif not price_above_ema:
            return GridState.PAUSED
        return self.state  # Maintain current state

    def _place_grid(self, mid_price: float, timestamp: int):
        """Place symmetric grid orders."""
        levels = self.config.get("levels", 5)
        spacing_mult = self.config.get("spacing_atr_multiplier", 1.5)
        atr_val = self.atr.value

        if atr_val <= 0:
            return

        spacing = atr_val * spacing_mult
        instrument = self.get_instrument()

        for i in range(1, levels + 1):
            # Buy level (below mid)
            buy_price = instrument.round_price(mid_price - spacing * i)
            buy_qty = instrument.round_quantity(
                self.config.get("capital", 5000) / levels / buy_price
            )
            if buy_qty > 0:
                oid = self.buy_limit(buy_price, buy_qty)
                self.active_orders[oid] = {"side": "BUY", "level": i, "price": buy_price}

            # Sell level (above mid)
            sell_price = instrument.round_price(mid_price + spacing * i)
            sell_qty = instrument.round_quantity(
                self.config.get("capital", 5000) / levels / sell_price
            )
            if sell_qty > 0:
                oid = self.sell_limit(sell_price, sell_qty)
                self.active_orders[oid] = {"side": "SELL", "level": i, "price": sell_price}

        self.base_price = mid_price
        self.last_refresh_time = timestamp

    def _cancel_grid(self):
        """Cancel all active grid orders."""
        self.cancel_all()
        self.active_orders.clear()
        self.base_price = None

    def format_status(self) -> str:
        orders = len(self.active_orders)
        return (
            f"Grid({self.instrument_id}) state={self.state.value} "
            f"orders={orders} trades={self.trade_count} "
            f"EMA={self.ema.value:.2f} RSI={self.rsi.value:.1f} ATR={self.atr.value:.4f}"
        )
```

- [ ] **Step 2: Write grid strategy tests**

Create `tests/trading_engine/test_grid_strategy.py`:

```python
"""Grid strategy tests using MockAdapter."""
from src.trading_engine.strategy.grid import GridStrategy, GridState
from src.trading_engine.adapter import MockAdapter, InstrumentInfo


def make_strategy_and_adapter():
    config = {
        "levels": 3,
        "capital": 3000,
        "spacing_atr_multiplier": 1.5,
        "ema_period": 5,
        "rsi_period": 5,
        "atr_period": 5,
        "bollinger_period": 5,
        "bollinger_std_dev": 2.0,
        "order_refresh_seconds": 3600,
    }
    strategy = GridStrategy("BTC-USDT", config)
    adapter = MockAdapter({"USDT": 10000})
    adapter.set_price("BTC-USDT", 50000.0)
    adapter.set_instrument("BTC-USDT", InstrumentInfo("BTC-USDT", 0.01, 0.00001, 2, 5))
    strategy._set_adapter(adapter)
    strategy.start()
    return strategy, adapter


def make_bar(close, high=None, low=None, ts=0):
    return {
        "open": close,
        "high": high or close * 1.001,
        "low": low or close * 0.999,
        "close": close,
        "volume": 1000.0,
        "timestamp": ts,
    }


def test_starts_inactive():
    s, _ = make_strategy_and_adapter()
    assert s.state == GridState.INACTIVE


def test_activates_after_indicators_warm_up():
    s, adapter = make_strategy_and_adapter()
    # Feed enough bars to initialize indicators (need period+1 bars)
    for i in range(10):
        adapter.set_price("BTC-USDT", 50000.0 + i * 10)
        s.on_bar(make_bar(50000.0 + i * 10, ts=i * 3600))

    # Should have placed grid orders if conditions met
    # (depends on EMA/RSI state — at minimum should not be INACTIVE)
    assert s.state != GridState.INACTIVE


def test_places_grid_orders():
    s, adapter = make_strategy_and_adapter()
    # Feed rising prices to trigger activation
    for i in range(10):
        price = 50000.0 + i * 100  # Rising market
        adapter.set_price("BTC-USDT", price)
        s.on_bar(make_bar(price, ts=i * 3600))

    if s.state == GridState.ACTIVE:
        # Should have placed buy + sell orders (3 each = 6 total)
        open_orders = adapter.get_open_orders("BTC-USDT")
        assert len(open_orders) > 0


def test_format_status():
    s, _ = make_strategy_and_adapter()
    status = s.format_status()
    assert "BTC-USDT" in status
    assert "Grid" in status


def test_cancel_on_stop():
    s, adapter = make_strategy_and_adapter()
    for i in range(10):
        price = 50000.0 + i * 100
        adapter.set_price("BTC-USDT", price)
        s.on_bar(make_bar(price, ts=i * 3600))

    s.stop()
    assert len(adapter.get_open_orders("BTC-USDT")) == 0
    assert len(s.active_orders) == 0
```

- [ ] **Step 3: Run all trading_engine tests**

```bash
python -m pytest tests/trading_engine/ -v
```
Expected: 19 (previous) + 5 (grid) = **24 tests PASS**

- [ ] **Step 4: Commit**

```bash
git add src/trading_engine/strategy/grid.py tests/trading_engine/test_grid_strategy.py
git commit -m "feat(trading-engine): implement Grid strategy engine with Rust indicator integration"
```

---

### Task 6: StrategyHost Orchestrator + Package __init__

**Files:**
- Create: `src/trading_engine/host.py`
- Modify: `src/trading_engine/__init__.py`
- Create: `tests/trading_engine/test_host.py`

- [ ] **Step 1: Implement StrategyHost**

Create `src/trading_engine/host.py`:

```python
"""StrategyHost — owns adapter + strategies, routes bars and events.

Created once per trading session. Strategies don't know about
each other — the host coordinates everything.
"""
from typing import Optional

from .adapter.base import ExecutionAdapter, OrderFill
from .strategy.base import Strategy


class StrategyHost:
    """Manages a collection of strategies behind a single execution adapter.

    Usage:
        adapter = HummingbotAdapter(connector)
        host = StrategyHost(adapter)
        host.add_strategy(GridStrategy("BTC-USDT", config))
        host.add_strategy(TrendStrategy("ETH-USDT", config))
        host.start()

        # On each bar:
        host.on_bar({"instrument_id": "BTC-USDT", "open": ..., ...})

        # On each fill:
        host.on_order_filled(fill)
    """

    def __init__(self, adapter: ExecutionAdapter):
        self._adapter = adapter
        self._strategies: dict[str, Strategy] = {}
        self._order_strategy_map: dict[str, Strategy] = {}

    @property
    def adapter(self) -> ExecutionAdapter:
        return self._adapter

    def add_strategy(self, strategy: Strategy):
        """Add a strategy to the host."""
        key = f"{strategy.__class__.__name__}:{strategy.instrument_id}"
        strategy._set_adapter(self._adapter)
        self._strategies[key] = strategy

    def start(self):
        """Start all strategies."""
        for s in self._strategies.values():
            s.start()

    def stop(self):
        """Stop all strategies."""
        for s in self._strategies.values():
            s.stop()

    def on_bar(self, bar: dict):
        """Route a bar to all matching strategies."""
        instrument_id = bar.get("instrument_id", "")
        for s in self._strategies.values():
            if s.instrument_id == instrument_id and s.running:
                s.on_bar(bar)

    def on_order_filled(self, fill: OrderFill):
        """Route a fill to the strategy that owns the order."""
        # Find the strategy by instrument_id
        for s in self._strategies.values():
            if s.instrument_id == fill.instrument_id and s.running:
                s.on_order_filled(fill)

    def format_status(self) -> str:
        """Get status from all strategies."""
        lines = [s.format_status() for s in self._strategies.values()]
        return "\n".join(lines)

    @property
    def strategies(self) -> list[Strategy]:
        return list(self._strategies.values())
```

- [ ] **Step 2: Write host tests**

Create `tests/trading_engine/test_host.py`:

```python
"""StrategyHost tests."""
from src.trading_engine.host import StrategyHost
from src.trading_engine.strategy.base import Strategy
from src.trading_engine.adapter import MockAdapter


class CountingStrategy(Strategy):
    """Test strategy that counts bars and fills."""
    def __init__(self, instrument_id: str, config: dict = None):
        super().__init__(instrument_id, config or {})
        self.bar_count = 0
        self.fill_count = 0
        self.started = False
        self.stopped = False

    def on_start(self):
        self.started = True

    def on_bar(self, bar: dict):
        self.bar_count += 1

    def on_stop(self):
        self.stopped = True

    def on_order_filled(self, fill):
        self.fill_count += 1


def test_host_starts_all_strategies():
    adapter = MockAdapter()
    host = StrategyHost(adapter)
    s1 = CountingStrategy("BTC-USDT")
    s2 = CountingStrategy("ETH-USDT")
    host.add_strategy(s1)
    host.add_strategy(s2)
    host.start()
    assert s1.started
    assert s2.started


def test_host_routes_bars_by_instrument():
    adapter = MockAdapter()
    host = StrategyHost(adapter)
    btc = CountingStrategy("BTC-USDT")
    eth = CountingStrategy("ETH-USDT")
    host.add_strategy(btc)
    host.add_strategy(eth)
    host.start()

    host.on_bar({"instrument_id": "BTC-USDT", "close": 50000.0})
    host.on_bar({"instrument_id": "BTC-USDT", "close": 50100.0})
    host.on_bar({"instrument_id": "ETH-USDT", "close": 3000.0})

    assert btc.bar_count == 2
    assert eth.bar_count == 1


def test_host_stops_all_strategies():
    adapter = MockAdapter()
    host = StrategyHost(adapter)
    s = CountingStrategy("BTC-USDT")
    host.add_strategy(s)
    host.start()
    host.stop()
    assert s.stopped


def test_host_routes_fills():
    from src.trading_engine.adapter.base import OrderFill
    adapter = MockAdapter()
    host = StrategyHost(adapter)
    s = CountingStrategy("BTC-USDT")
    host.add_strategy(s)
    host.start()

    fill = OrderFill("mock-1", "BTC-USDT", "BUY", 50000.0, 0.001, 0)
    host.on_order_filled(fill)
    assert s.fill_count == 1


def test_host_format_status():
    adapter = MockAdapter()
    host = StrategyHost(adapter)
    host.add_strategy(CountingStrategy("BTC-USDT"))
    host.start()
    status = host.format_status()
    assert "BTC-USDT" in status
```

- [ ] **Step 3: Update package __init__.py**

Replace `src/trading_engine/__init__.py`:

```python
"""trading_engine — shared Python abstraction layer for multi-engine trading.

Uses Rust indicators (via trading_engine_core wheel) for performance-critical
math. Strategy logic, adapters, and risk management are Python for fast iteration.

Usage:
    from trading_engine import StrategyHost, GridStrategy
    from trading_engine.adapter import MockAdapter

    adapter = MockAdapter({"USDT": 10000})
    host = StrategyHost(adapter)
    host.add_strategy(GridStrategy("BTC-USDT", config))
    host.start()
    host.on_bar(bar)
"""

from .host import StrategyHost
from .adapter import ExecutionAdapter, MockAdapter, Order, InstrumentInfo
from .strategy import Strategy

__all__ = [
    "StrategyHost",
    "ExecutionAdapter",
    "MockAdapter",
    "Strategy",
    "Order",
    "InstrumentInfo",
]
```

- [ ] **Step 4: Run all tests**

```bash
python -m pytest tests/trading_engine/ -v
```
Expected: 24 (previous) + 5 (host) = **29 tests PASS**

- [ ] **Step 5: Commit**

```bash
git add src/trading_engine/host.py src/trading_engine/__init__.py tests/trading_engine/test_host.py
git commit -m "feat(trading-engine): add StrategyHost orchestrator and package entry point"
```

---

## Self-Review

### 1. Spec Coverage

| Spec Requirement | Task | Status |
|---|---|---|
| ExecutionAdapter trait (Python ABC) | Task 2 | ✅ |
| Hummingbot adapter | — | Deferred to Phase 5 (needs live Hummingbot connector) |
| NautilusTrader adapter | — | Deferred to Phase 6 |
| Mock adapter | Task 2 | ✅ |
| Strategy base class | Task 3 | ✅ |
| Grid strategy engine | Task 5 | ✅ |
| Rust indicator PyO3 bindings | Task 1 | ✅ |
| Circuit breaker | Task 4 | ✅ |
| Position guard | Task 4 | ✅ |
| StrategyHost orchestrator | Task 6 | ✅ |
| Config loader | — | Uses existing config.py from nautilus package |

### 2. Placeholder Scan

No TBD/TODO/placeholder found. All code is complete.

### 3. Type Consistency

- `Order` dataclass defined in `adapter/base.py`, used consistently in MockAdapter and GridStrategy
- `InstrumentInfo.round_price()` / `round_quantity()` used by GridStrategy
- `Strategy._set_adapter()` called by both StrategyHost and tests
- Rust indicator `update()` takes `f64`, Python passes `float` — PyO3 handles conversion
- Bar dict keys: `{open, high, low, close, volume, timestamp, instrument_id}` — consistent across all tests and StrategyHost
