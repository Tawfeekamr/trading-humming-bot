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
