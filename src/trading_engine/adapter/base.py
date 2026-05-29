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
