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
