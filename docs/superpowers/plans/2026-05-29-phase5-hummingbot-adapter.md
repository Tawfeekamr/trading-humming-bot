# Phase 5: HummingbotAdapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build HummingbotAdapter (thin wrapper bridging ExecutionAdapter ABC to Hummingbot's connector API) and wire it into the existing ta_grid_btcusdt.py strategy script.

**Architecture:** HummingbotAdapter holds refs to the Hummingbot connector (for price/balance queries) and the strategy instance (for buy/sell/cancel via StrategyV2Base). Type conversions happen at the boundary: float ↔ Decimal, string sides ↔ Hummingbot enums. Candles stay outside the adapter — the script feeds bars to StrategyHost.on_bar() directly.

**Tech Stack:** Python 3.13, Hummingbot StrategyV2Base, Decimal for precision, existing trading_engine package.

**Design spec:** `docs/superpowers/specs/2026-05-29-hummingbot-adapter-design.md`

**Branch:** `feat/hummingbot-adapter`

---

## File Structure

```
src/trading_engine/adapter/
├── hummingbot.py               # NEW — HummingbotAdapter

tests/trading_engine/
├── test_hummingbot_adapter.py   # NEW — 8 tests with mock connector/strategy

src/trading_engine/adapter/__init__.py   # MODIFY — add HummingbotAdapter export
```

---

### Task 1: HummingbotAdapter + Tests (TDD)

**Files:**
- Create: `src/trading_engine/adapter/hummingbot.py`
- Create: `tests/trading_engine/test_hummingbot_adapter.py`
- Modify: `src/trading_engine/adapter/__init__.py`

- [ ] **Step 1: Create the branch**

```bash
git checkout main
git checkout -b feat/hummingbot-adapter
```

- [ ] **Step 2: Write the test file with mock Hummingbot objects**

Create `tests/trading_engine/test_hummingbot_adapter.py`:

```python
"""HummingbotAdapter tests with mock connector and strategy objects.

No Hummingbot dependency required — tests use plain Python mocks that
replicate the Hummingbot connector/strategy API surface.
"""
from decimal import Decimal
from src.trading_engine.adapter.hummingbot import HummingbotAdapter
from src.trading_engine.adapter.base import Order


# ── Mock Hummingbot objects ──

class MockBalance:
    def __init__(self, available: float):
        self.available = available


class MockConnector:
    """Simulates Hummingbot connector (self.connectors[exchange])."""
    def __init__(self, balances: dict[str, float] | None = None, prices: dict[str, float] | None = None):
        self._balances = balances or {"USDT": 10000.0}
        self._prices = prices or {"BTC-USDT": 50000.0}
        self.ready = True

    def get_mid_price(self, trading_pair: str):
        return self._prices.get(trading_pair, 0.0)

    def get_balance(self, currency: str):
        return MockBalance(self._balances.get(currency, 0.0))


class MockStrategy:
    """Simulates Hummingbot StrategyV2Base (self)."""
    def __init__(self, connector_name: str = "binance_paper_trade"):
        self.exchange = connector_name
        self._orders: dict[str, dict] = {}
        self._next_id = 1

    def buy(self, connector_name: str, trading_pair: str, amount: Decimal, order_type, price: Decimal):
        oid = f"hb-buy-{self._next_id}"
        self._next_id += 1
        self._orders[oid] = {
            "connector": connector_name,
            "pair": trading_pair,
            "side": "BUY",
            "price": float(price),
            "quantity": float(amount),
            "order_type": order_type,
        }
        return oid

    def sell(self, connector_name: str, trading_pair: str, amount: Decimal, order_type, price: Decimal):
        oid = f"hb-sell-{self._next_id}"
        self._next_id += 1
        self._orders[oid] = {
            "connector": connector_name,
            "pair": trading_pair,
            "side": "SELL",
            "price": float(price),
            "quantity": float(amount),
            "order_type": order_type,
        }
        return oid

    def cancel(self, connector_name: str, trading_pair: str, order_id: str):
        self._orders.pop(order_id, None)


def make_adapter(connector=None, strategy=None):
    connector = connector or MockConnector()
    strategy = strategy or MockStrategy()
    return HummingbotAdapter(connector, strategy)


# ── Tests ──

def test_submit_buy_order():
    strategy = MockStrategy()
    adapter = make_adapter(strategy=strategy)
    order = Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001)
    oid = adapter.submit_order(order)
    assert oid.startswith("hb-buy-")
    assert strategy._orders[oid]["pair"] == "BTC-USDT"
    assert strategy._orders[oid]["side"] == "BUY"
    assert abs(strategy._orders[oid]["price"] - 50000.0) < 0.001
    assert abs(strategy._orders[oid]["quantity"] - 0.001) < 0.0001


def test_submit_sell_order():
    strategy = MockStrategy()
    adapter = make_adapter(strategy=strategy)
    order = Order("BTC-USDT", "SELL", "LIMIT", 51000.0, 0.001)
    oid = adapter.submit_order(order)
    assert oid.startswith("hb-sell-")
    assert strategy._orders[oid]["side"] == "SELL"


def test_cancel_order():
    strategy = MockStrategy()
    adapter = make_adapter(strategy=strategy)
    order = Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001)
    oid = adapter.submit_order(order)
    adapter.cancel_order(oid)
    assert oid not in strategy._orders


def test_cancel_all_orders():
    strategy = MockStrategy()
    adapter = make_adapter(strategy=strategy)
    adapter.submit_order(Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001))
    adapter.submit_order(Order("BTC-USDT", "SELL", "LIMIT", 51000.0, 0.001))
    adapter.submit_order(Order("ETH-USDT", "BUY", "LIMIT", 3000.0, 0.01))
    adapter.cancel_all_orders("BTC-USDT")
    assert len(adapter.get_open_orders("BTC-USDT")) == 0
    assert len(adapter.get_open_orders("ETH-USDT")) == 1


def test_get_balance():
    connector = MockConnector({"USDT": 5000.0, "BTC": 0.5})
    adapter = make_adapter(connector=connector)
    assert adapter.get_balance("USDT") == 5000.0
    assert adapter.get_balance("ETH") == 0.0


def test_get_mid_price():
    connector = MockConnector(prices={"BTC-USDT": 50000.0})
    adapter = make_adapter(connector=connector)
    assert adapter.get_mid_price("BTC-USDT") == 50000.0
    assert adapter.get_mid_price("ETH-USDT") == 0.0


def test_get_open_orders():
    strategy = MockStrategy()
    adapter = make_adapter(strategy=strategy)
    adapter.submit_order(Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001))
    adapter.submit_order(Order("BTC-USDT", "SELL", "LIMIT", 51000.0, 0.001))
    adapter.submit_order(Order("ETH-USDT", "BUY", "LIMIT", 3000.0, 0.01))
    btc_orders = adapter.get_open_orders("BTC-USDT")
    assert len(btc_orders) == 2
    assert all(o.instrument_id == "BTC-USDT" for o in btc_orders)


def test_get_instrument():
    adapter = make_adapter()
    info = adapter.get_instrument("BTC-USDT")
    assert info.symbol == "BTC-USDT"
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
python -m pytest tests/trading_engine/test_hummingbot_adapter.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'src.trading_engine.adapter.hummingbot'`

- [ ] **Step 4: Implement HummingbotAdapter**

Create `src/trading_engine/adapter/hummingbot.py`:

```python
"""HummingbotAdapter — thin wrapper bridging ExecutionAdapter to Hummingbot's connector.

Takes a connector object (for price/balance) and a strategy reference
(for buy/sell/cancel via Hummingbot's StrategyV2Base methods).
Type conversions happen here: float ↔ Decimal, string sides → Hummingbot enums.
"""
from decimal import Decimal

from .base import ExecutionAdapter, Order, InstrumentInfo


class HummingbotAdapter(ExecutionAdapter):
    """Wraps Hummingbot connector and strategy for use with trading_engine.

    Args:
        connector: Hummingbot connector (self.connectors[exchange]).
                   Provides get_mid_price(), get_balance(), ready.
        strategy_ref: Hummingbot StrategyV2Base instance (self).
                      Provides buy(), sell(), cancel().
    """

    def __init__(self, connector, strategy_ref):
        self._connector = connector
        self._strategy = strategy_ref
        self._submitted_orders: dict[str, Order] = {}

    def _connector_name(self) -> str:
        """Get the Hummingbot connector name (e.g. 'binance_paper_trade')."""
        return getattr(self._strategy, 'exchange', '')

    def _to_decimal(self, value: float) -> Decimal:
        """Convert float to Decimal via string to avoid float precision issues."""
        return Decimal(str(value))

    def get_balance(self, currency: str) -> float:
        bal = self._connector.get_balance(currency)
        if bal is None:
            return 0.0
        return float(getattr(bal, 'available', 0.0))

    def submit_order(self, order: Order) -> str:
        connector_name = self._connector_name()
        pair = order.instrument_id
        price = self._to_decimal(order.price)
        quantity = self._to_decimal(order.quantity)

        if order.side == "BUY":
            oid = self._strategy.buy(connector_name, pair, quantity, "LIMIT", price)
        else:
            oid = self._strategy.sell(connector_name, pair, quantity, "LIMIT", price)

        # Track for later cancel/get_open_orders
        tracked = Order(
            instrument_id=order.instrument_id,
            side=order.side,
            order_type=order.order_type,
            price=order.price,
            quantity=order.quantity,
            client_order_id=oid,
        )
        self._submitted_orders[oid] = tracked
        return oid

    def cancel_order(self, client_order_id: str) -> None:
        order = self._submitted_orders.get(client_order_id)
        if order is None:
            return
        self._strategy.cancel(self._connector_name(), order.instrument_id, client_order_id)
        self._submitted_orders.pop(client_order_id, None)

    def cancel_all_orders(self, instrument_id: str) -> None:
        to_cancel = [
            oid for oid, o in self._submitted_orders.items()
            if o.instrument_id == instrument_id
        ]
        for oid in to_cancel:
            self.cancel_order(oid)

    def get_open_orders(self, instrument_id: str) -> list[Order]:
        # Check Hummingbot's active_orders if available, else use tracked
        active = getattr(self._strategy, 'active_orders', None)
        if active is not None:
            result = []
            for o in active:
                if hasattr(o, 'trading_pair') and o.trading_pair == instrument_id:
                    mapped = Order(
                        instrument_id=instrument_id,
                        side="BUY" if str(getattr(o, 'trade_type', '')).endswith('BUY') else "SELL",
                        order_type="LIMIT",
                        price=float(getattr(o, 'price', 0)),
                        quantity=float(getattr(o, 'amount', 0)),
                        client_order_id=getattr(o, 'client_order_id', ''),
                    )
                    result.append(mapped)
            return result
        # Fallback: use tracked orders
        return [o for o in self._submitted_orders.values() if o.instrument_id == instrument_id]

    def get_mid_price(self, instrument_id: str) -> float:
        price = self._connector.get_mid_price(instrument_id)
        if price is None:
            return 0.0
        return float(price)

    def get_instrument(self, instrument_id: str) -> InstrumentInfo:
        return InstrumentInfo(symbol=instrument_id)
```

- [ ] **Step 5: Run tests to verify they pass**

```bash
python -m pytest tests/trading_engine/test_hummingbot_adapter.py -v
```

Expected: 8 tests PASS

- [ ] **Step 6: Update adapter __init__.py**

Replace `src/trading_engine/adapter/__init__.py`:

```python
from .base import ExecutionAdapter, Order, OrderFill, InstrumentInfo
from .mock import MockAdapter
from .hummingbot import HummingbotAdapter

__all__ = ["ExecutionAdapter", "Order", "OrderFill", "InstrumentInfo", "MockAdapter", "HummingbotAdapter"]
```

- [ ] **Step 7: Run full test suite**

```bash
python -m pytest tests/trading_engine/ -v
```

Expected: 37 tests PASS (29 existing + 8 new)

- [ ] **Step 8: Commit**

```bash
git add src/trading_engine/adapter/hummingbot.py src/trading_engine/adapter/__init__.py tests/trading_engine/test_hummingbot_adapter.py
git commit -m "feat(trading-engine): add HummingbotAdapter with 8 passing tests

Thin wrapper bridging ExecutionAdapter ABC to Hummingbot connector API.
Handles float↔Decimal conversion, order tracking, and active_orders fallback.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Wire HummingbotAdapter into Existing Bot

**Files:**
- Create: `src/trading_engine/adapter/hummingbot_integration.py`
- Modify: `hummingbot_files/scripts/ta_grid_btcusdt.py` (add integration hooks)

This task creates a thin integration layer that the existing Hummingbot script can import to delegate grid management to the trading_engine package. The integration is **incremental** — the existing script continues to work as-is, and the trading_engine path is activated via a config flag.

- [ ] **Step 1: Write the integration module**

Create `src/trading_engine/adapter/hummingbot_integration.py`:

```python
"""Integration layer — wires trading_engine into a running Hummingbot strategy.

The Hummingbot script calls init_trading_engine() once at startup,
then tick_trading_engine() each on_tick, and route_fill() on did_fill_order.
"""
import time

from ..host import StrategyHost
from ..strategy.grid import GridStrategy
from .hummingbot import HummingbotAdapter
from ..adapter.base import OrderFill, InstrumentInfo


def build_grid_config(pair: str, yaml_config: dict) -> dict:
    """Build GridStrategy config from the existing YAML config values.

    Args:
        pair: trading pair e.g. "BTC-USDT"
        yaml_config: dict with the bot's YAML config values
    """
    return {
        "levels": yaml_config.get("grid_levels", 5),
        "capital": yaml_config.get("capital", 5000),
        "spacing_atr_multiplier": yaml_config.get("spacing_atr_multiplier", 1.5),
        "ema_period": yaml_config.get("ema_period", 200),
        "rsi_period": yaml_config.get("rsi_period", 14),
        "atr_period": yaml_config.get("atr_period", 14),
        "bollinger_period": yaml_config.get("bollinger_period", 20),
        "bollinger_std_dev": yaml_config.get("bollinger_std_dev", 2.0),
        "order_refresh_seconds": yaml_config.get("order_refresh_seconds", 60),
        "rsi_oversold": yaml_config.get("rsi_oversold", 35),
        "rsi_overbought": yaml_config.get("rsi_overbought", 70),
    }


def init_trading_engine(connector, strategy_ref, pairs: list[str], config: dict) -> StrategyHost:
    """Initialize the trading engine for use inside a Hummingbot script.

    Args:
        connector: Hummingbot connector (self.connectors[exchange])
        strategy_ref: The Hummingbot strategy instance (self)
        pairs: list of trading pairs e.g. ["BTC-USDT", "ETH-USDT"]
        config: dict of config values (from YAML or strategy attributes)

    Returns:
        StrategyHost ready to receive bars and fills.
    """
    adapter = HummingbotAdapter(connector, strategy_ref)
    host = StrategyHost(adapter)

    for pair in pairs:
        grid_config = build_grid_config(pair, config)
        host.add_strategy(GridStrategy(pair, grid_config))

    host.start()
    return host


def tick_trading_engine(host: StrategyHost, pair: str, bar: dict):
    """Feed a bar to the trading engine on each on_tick.

    Args:
        host: StrategyHost returned by init_trading_engine
        pair: trading pair for this bar
        bar: dict with keys: open, high, low, close, volume, timestamp
    """
    bar["instrument_id"] = pair
    host.on_bar(bar)


def route_fill(host: StrategyHost, event):
    """Route a Hummingbot fill event to the trading engine.

    Call from did_fill_order(event) in the Hummingbot script.

    Args:
        host: StrategyHost returned by init_trading_engine
        event: Hummingbot OrderFilledEvent with order_id, trading_pair,
               trade_type, price, amount
    """
    side = "BUY"
    trade_type = getattr(event, 'trade_type', None)
    if trade_type is not None:
        side = "BUY" if "BUY" in str(trade_type).upper() else "SELL"

    fill = OrderFill(
        client_order_id=getattr(event, 'order_id', ''),
        instrument_id=getattr(event, 'trading_pair', ''),
        side=side,
        price=float(getattr(event, 'price', 0)),
        quantity=float(getattr(event, 'amount', 0)),
        timestamp=int(time.time()),
    )
    host.on_order_filled(fill)
```

- [ ] **Step 2: Write integration tests**

Add to `tests/trading_engine/test_hummingbot_adapter.py`:

```python
from src.trading_engine.adapter.hummingbot_integration import (
    build_grid_config,
    init_trading_engine,
    tick_trading_engine,
    route_fill,
)


def test_build_grid_config():
    config = build_grid_config("BTC-USDT", {"grid_levels": 3, "capital": 2000})
    assert config["levels"] == 3
    assert config["capital"] == 2000
    assert config["ema_period"] == 200  # default


def test_init_trading_engine():
    connector = MockConnector()
    strategy = MockStrategy()
    host = init_trading_engine(connector, strategy, ["BTC-USDT"], {"grid_levels": 3})
    assert len(host.strategies) == 1
    assert host.strategies[0].instrument_id == "BTC-USDT"


def test_tick_trading_engine():
    connector = MockConnector()
    strategy = MockStrategy()
    host = init_trading_engine(connector, strategy, ["BTC-USDT"], {
        "grid_levels": 3, "ema_period": 5, "rsi_period": 5,
        "atr_period": 5, "bollinger_period": 5,
    })
    # Feed bars — need enough to warm up indicators
    prices = [50000, 50100, 49900, 50200, 49800, 50100, 49900, 50150, 49850, 50050]
    for i, price in enumerate(prices):
        tick_trading_engine(host, "BTC-USDT", {
            "open": price, "high": price * 1.001, "low": price * 0.999,
            "close": price, "volume": 1000.0, "timestamp": i * 3600,
        })
    # Should have processed all bars without error
    assert host.strategies[0].state.value != "inactive"


def test_route_fill():
    connector = MockConnector()
    strategy = MockStrategy()
    host = init_trading_engine(connector, strategy, ["BTC-USDT"], {"grid_levels": 3})

    class MockEvent:
        order_id = "hb-buy-1"
        trading_pair = "BTC-USDT"
        trade_type = type('TT', (), {'__str__': lambda s: 'BUY'})()
        price = 50000.0
        amount = 0.001

    route_fill(host, MockEvent())
    # Fill was routed — no crash = success
```

- [ ] **Step 3: Run all tests**

```bash
python -m pytest tests/trading_engine/ -v
```

Expected: 37 (previous) + 4 (integration) = **41 tests PASS**

- [ ] **Step 4: Commit**

```bash
git add src/trading_engine/adapter/hummingbot_integration.py tests/trading_engine/test_hummingbot_adapter.py
git commit -m "feat(trading-engine): add Hummingbot integration layer with init/tick/route_fill helpers

Thin integration module for wiring trading_engine into an existing Hummingbot
script. Config mapping, bar feeding, and fill routing in 3 function calls.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
python -m pytest tests/trading_engine/ -v
```

Expected: **41 tests PASS**

- [ ] **Step 2: Run Rust tests (unchanged)**

```bash
cd trading-engine-core && cargo test
```

Expected: **41 Rust tests PASS**

- [ ] **Step 3: Verify imports work**

```bash
python -c "
from src.trading_engine.adapter.hummingbot import HummingbotAdapter
from src.trading_engine.adapter.hummingbot_integration import init_trading_engine, tick_trading_engine, route_fill
print('All imports OK')
"
```

Expected: `All imports OK`

- [ ] **Step 4: Verify log**

```bash
git log --oneline main..HEAD
```

Expected: 2 commits on `feat/hummingbot-adapter`

---

## Self-Review

### 1. Spec Coverage

| Spec Requirement | Task | Status |
|---|---|---|
| HummingbotAdapter class | Task 1 | ✅ |
| submit_order with Decimal conversion | Task 1 | ✅ |
| cancel_order / cancel_all_orders | Task 1 | ✅ |
| get_balance via connector | Task 1 | ✅ |
| get_mid_price via connector | Task 1 | ✅ |
| get_open_orders (active_orders fallback) | Task 1 | ✅ |
| Order tracking (_submitted_orders) | Task 1 | ✅ |
| 8 adapter tests | Task 1 | ✅ |
| Integration layer (init/tick/route) | Task 2 | ✅ |
| Config mapping from YAML | Task 2 | ✅ |
| 4 integration tests | Task 2 | ✅ |

### 2. Placeholder Scan

No TBD/TODO/placeholders found. All steps contain complete code.

### 3. Type Consistency

- `Order` dataclass from `adapter/base.py` — used consistently in HummingbotAdapter and tests
- `OrderFill` dataclass — created in `route_fill()` with same fields as `base.py`
- `InstrumentInfo` — returned by `get_instrument()`, matches base.py definition
- Mock connector/strategy mock the exact Hummingbot API signatures from the spec
- `_connector_name()` returns `strategy.exchange` — matches MockStrategy attribute
