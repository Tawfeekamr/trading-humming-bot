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
