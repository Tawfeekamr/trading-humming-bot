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
