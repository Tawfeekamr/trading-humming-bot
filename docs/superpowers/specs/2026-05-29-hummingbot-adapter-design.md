# Phase 5: HummingbotAdapter Design Spec

**Date:** 2026-05-29
**Branch:** `feat/hummingbot-adapter`
**Depends on:** Phase 2 (merged to main)

## Goal

Build `HummingbotAdapter` — a thin wrapper that bridges our `ExecutionAdapter` ABC to Hummingbot's connector API. Wire it into the existing `ta_grid_btcusdt.py` strategy script so the trading_engine package drives order management instead of direct connector calls.

## Architecture

```
Hummingbot Script (StrategyV2Base)
  │
  ├── on_tick():
  │     connector = self.connectors[exchange]
  │     candles = self.candle_feeds[pair].fetch_candles()
  │     host.on_bar({instrument_id, OHLCV, timestamp})
  │
  ├── HummingbotAdapter(connector, strategy_ref)
  │     ├── submit_order(Order) → strategy_ref.buy/sell()
  │     ├── cancel_order(id) → strategy_ref.cancel()
  │     ├── get_balance(currency) → connector.get_balance()
  │     ├── get_mid_price(pair) → connector.get_mid_price()
  │     └── get_open_orders(pair) → strategy_ref.active_orders
  │
  ├── did_fill_order(event):
  │     fill = OrderFill(...)
  │     host.on_order_filled(fill)
  │
  └── StrategyHost(adapter)
        └── GridStrategy("BTC-USDT", config)
```

## File Structure

```
src/trading_engine/adapter/
├── hummingbot.py             # NEW — HummingbotAdapter

tests/trading_engine/
├── test_hummingbot_adapter.py  # NEW — tests with mock connector/strategy
```

No changes to existing trading_engine files.

## HummingbotAdapter Implementation

### Constructor

```python
class HummingbotAdapter(ExecutionAdapter):
    def __init__(self, connector, strategy_ref):
        """
        Args:
            connector: Hummingbot connector object (self.connectors[exchange])
                       Provides: get_mid_price(), get_balance(), ready
            strategy_ref: Hummingbot StrategyV2Base instance (self)
                          Provides: buy(), sell(), cancel(), active_orders
        """
```

### Method Mappings

| ExecutionAdapter method | Hummingbot call | Type conversion |
|---|---|---|
| `submit_order(order)` | `strategy_ref.buy/sell(connector_name, pair, Decimal(qty), OrderType.LIMIT, Decimal(price))` | float → Decimal |
| `cancel_order(client_order_id)` | `strategy_ref.cancel(connector_name, pair, order_id)` | parse pair from tracked orders |
| `cancel_all_orders(instrument_id)` | iterate tracked orders → cancel each | — |
| `get_open_orders(instrument_id)` | `strategy_ref.active_orders` filtered by pair | Hummingbot Order → our Order |
| `get_mid_price(instrument_id)` | `connector.get_mid_price(pair)` | returns float or None → 0.0 |
| `get_balance(currency)` | `connector.get_balance(asset).available` | — |
| `get_instrument(instrument_id)` | hardcoded defaults per exchange | configurable via set_instrument |

### Type Conversions

```python
from decimal import Decimal
from hummingbot.core.data_type.common import OrderType, TradeType

# Our side string → Hummingbot TradeType
SIDE_MAP = {"BUY": TradeType.BUY, "SELL": TradeType.SELL}

# Our Order → Hummingbot buy/sell call
# price: float → Decimal(str(price))  (str intermediate avoids float precision issues)
# quantity: float → Decimal(str(quantity))
```

### Order Tracking

The adapter maintains an internal dict `_submitted_orders: dict[str, Order]` mapping client_order_ids to our Order objects. This is needed because:
- `cancel_order(client_order_id)` needs to know the connector_name and trading_pair
- `cancel_all_orders(instrument_id)` needs to find all orders for an instrument
- `get_open_orders(instrument_id)` filters by instrument

When Hummingbot fills an order, `did_fill_order()` provides the order_id which maps back.

### Instrument ID → Trading Pair

Our system uses `"BTC-USDT"`, Hummingbot uses `"BTC-USDT"` too — same format. No conversion needed.

The connector name (e.g., `"binance_paper_trade"`) is stored in the adapter and passed to all buy/sell/cancel calls.

## Wiring into Existing Bot

The existing `ta_grid_btcusdt.py` will be modified to:

1. **Import trading_engine:**
```python
from src.trading_engine import StrategyHost
from src.trading_engine.adapter.hummingbot import HummingbotAdapter
from src.trading_engine.strategy.grid import GridStrategy
```

2. **Initialize in `on_tick()` first call:**
```python
# In __init__ or first on_tick:
connector = self.connectors[self.exchange]
adapter = HummingbotAdapter(connector, self)
self.host = StrategyHost(adapter)
for pair in self.pairs:
    config = self._build_grid_config(pair)
    self.host.add_strategy(GridStrategy(pair, config))
self.host.start()
```

3. **Replace direct order logic in `on_tick()` with:**
```python
# Before: direct connector calls for grid management
# After:
bar = {"instrument_id": symbol, "open": o, "high": h, "low": l, "close": c, "volume": v, "timestamp": ts}
self.host.on_bar(bar)
```

4. **Route fills in `did_fill_order()`:**
```python
def did_fill_order(self, event):
    fill = OrderFill(
        client_order_id=event.order_id,
        instrument_id=event.trading_pair,
        side="BUY" if event.trade_type == TradeType.BUY else "SELL",
        price=float(event.price),
        quantity=float(event.amount),
        timestamp=int(time.time()),
    )
    self.host.on_order_filled(fill)
```

5. **Cleanup in `on_stop()`:**
```python
def on_stop(self):
    self.host.stop()
```

## Testing Strategy

Tests use mock connector and mock strategy objects (plain Python classes, no Hummingbot dependency):

```python
class MockConnector:
    """Simulates Hummingbot connector."""
    def get_mid_price(self, pair): return 50000.0
    def get_balance(self, asset): return type('Bal', (), {'available': 10000.0})()
    ready = True

class MockStrategy:
    """Simulates Hummingbot StrategyV2Base."""
    def __init__(self):
        self.exchange = "binance_paper_trade"
        self._orders = {}
        self._next_id = 1
    def buy(self, connector, pair, amount, order_type, price):
        oid = f"hb-{self._next_id}"; self._next_id += 1
        self._orders[oid] = {"pair": pair, "side": "BUY", "price": float(price), "qty": float(amount)}
        return oid
    def sell(self, connector, pair, amount, order_type, price):
        oid = f"hb-{self._next_id}"; self._next_id += 1
        self._orders[oid] = {"pair": pair, "side": "SELL", "price": float(price), "qty": float(amount)}
        return oid
    def cancel(self, connector, pair, order_id):
        self._orders.pop(order_id, None)
```

### Test cases (8 tests)

1. `test_submit_buy_order` — verify buy() called with correct Decimal params
2. `test_submit_sell_order` — verify sell() called
3. `test_cancel_order` — cancel by client_order_id
4. `test_cancel_all_orders` — cancel all for an instrument, leave others
5. `test_get_balance` — returns available balance
6. `test_get_mid_price` — returns float
7. `test_get_open_orders` — filters by instrument_id
8. `test_get_instrument` — returns default InstrumentInfo

## Scope

### In scope
- HummingbotAdapter class with all ExecutionAdapter methods
- Tests with mock connector/strategy
- Wiring guide in spec (actual wiring deferred to separate task)

### Out of scope
- NautilusTrader adapter (Phase 6)
- Candle/OHLCV integration (stays outside adapter, fed via host.on_bar)
- Replacing the entire ta_grid_btcusdt.py strategy logic (incremental wiring)
- Config loader for Hummingbot YAML config

## Risks

1. **Hummingbot import not available in tests** — adapter imports `hummingbot` modules only inside methods or at runtime. Tests use mocks, no Hummingbot dependency needed.
2. **Decimal precision** — using `Decimal(str(price))` avoids float precision issues.
3. **Active orders API** — Hummingbot's `active_orders` may vary between versions. Adapter handles None/missing gracefully.
