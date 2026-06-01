"""trading_engine — shared Python abstraction layer for multi-engine trading.

Uses Rust indicators (via trading_engine_core wheel) for performance-critical
math. Strategy logic, adapters, and risk management are Python for fast iteration.

Usage (hybrid mode — Rust engine):
    EXECUTION_MODE=rust python -m src.trading_engine.runner

Usage (programmatic):
    from trading_engine import StrategyHost
    from trading_engine.adapter import create_adapter

    adapter = create_adapter()  # respects EXECUTION_MODE env var
    host = StrategyHost(adapter)
    host.add_strategy(GridStrategy("BTC-USDT", config))
    host.start()
    host.on_bar(bar)
"""

from .host import StrategyHost
from .adapter import ExecutionAdapter, MockAdapter, RustEngineAdapter, create_adapter, Order, InstrumentInfo
from .strategy import Strategy
from .data_feed import DataFeed

__all__ = [
    "StrategyHost",
    "ExecutionAdapter",
    "MockAdapter",
    "RustEngineAdapter",
    "create_adapter",
    "Strategy",
    "Order",
    "InstrumentInfo",
    "DataFeed",
]
