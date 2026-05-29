"""HummingbotAdapter — bridges ExecutionAdapter ABC to Hummingbot's connector API.

This adapter wraps Hummingbot's trading connector and StrategyV2Base to provide
a unified execution interface for trading strategies.

Usage:
    connector = hummingbot_connector  # Hummingbot connector instance
    strategy = strategy_v2_base        # Hummingbot StrategyV2Base instance
    adapter = HummingbotAdapter(connector, strategy)

    # Now strategy code can use the adapter
    price = adapter.get_mid_price("BTC-USDT")
    balance = adapter.get_balance("USDT")
    order = Order("BTC-USDT", "BUY", "LIMIT", price, 0.001)
    order_id = adapter.submit_order(order)
"""
from decimal import Decimal
from typing import TYPE_CHECKING

from .base import ExecutionAdapter, Order, InstrumentInfo

if TYPE_CHECKING:
    # Avoid hard dependency on Hummingbot at import time
    pass


class HummingbotAdapter(ExecutionAdapter):
    """Thin wrapper around Hummingbot connector and strategy.

    Args:
        connector: Hummingbot connector with get_mid_price(), get_balance(), ready
        strategy_ref: Hummingbot StrategyV2Base with buy(), sell(), cancel(),
                     active_orders, and exchange attributes
    """

    def __init__(self, connector, strategy_ref):
        self._connector = connector
        self._strategy = strategy_ref
        self._submitted_orders: dict[str, Order] = {}

    def submit_order(self, order: Order) -> str:
        """Submit an order via strategy buy/sell methods.

        Converts float to Decimal to avoid precision issues.
        Tracks submitted orders for cancellation/retrieval.

        Args:
            order: Order with instrument_id, side, order_type, price, quantity

        Returns:
            client_order_id: The order ID returned by Hummingbot
        """
        connector_name = self._connector_name()
        pair = order.instrument_id
        quantity = Decimal(str(order.quantity))
        price = Decimal(str(order.price))
        order_type = order.order_type

        if order.side == "BUY":
            order_id = self._strategy.buy(
                connector_name, pair, quantity, order_type, price
            )
        else:
            order_id = self._strategy.sell(
                connector_name, pair, quantity, order_type, price
            )

        # Track the order with its client_order_id
        tracked_order = Order(
            instrument_id=order.instrument_id,
            side=order.side,
            order_type=order.order_type,
            price=order.price,
            quantity=order.quantity,
            client_order_id=order_id,
        )
        self._submitted_orders[order_id] = tracked_order

        return order_id

    def cancel_order(self, client_order_id: str) -> None:
        """Cancel an existing order.

        Args:
            client_order_id: Order ID to cancel
        """
        if client_order_id not in self._submitted_orders:
            return

        order = self._submitted_orders[client_order_id]
        connector_name = self._connector_name()
        pair = order.instrument_id

        self._strategy.cancel(connector_name, pair, client_order_id)
        del self._submitted_orders[client_order_id]

    def cancel_all_orders(self, instrument_id: str) -> None:
        """Cancel all open orders for an instrument.

        Args:
            instrument_id: Trading pair symbol (e.g., "BTC-USDT")
        """
        to_cancel = [
            oid
            for oid, order in self._submitted_orders.items()
            if order.instrument_id == instrument_id
        ]

        for order_id in to_cancel:
            self.cancel_order(order_id)

    def get_open_orders(self, instrument_id: str) -> list[Order]:
        """Get all open orders for an instrument.

        First tries Hummingbot's active_orders (live orders on exchange).
        Falls back to tracked orders if active_orders is empty.

        Args:
            instrument_id: Trading pair symbol

        Returns:
            List of Order objects for the instrument
        """
        # Try Hummingbot's live orders first
        if hasattr(self._strategy, "active_orders") and self._strategy.active_orders:
            hummingbot_orders = []
            for order_id, hb_order in self._strategy.active_orders.items():
                # Map Hummingbot order fields to our Order dataclass
                order = Order(
                    instrument_id=getattr(hb_order, "trading_pair", instrument_id),
                    side=getattr(hb_order, "side", "BUY"),
                    order_type=getattr(hb_order, "order_type", "LIMIT"),
                    price=float(getattr(hb_order, "price", 0.0)),
                    quantity=float(getattr(hb_order, "quantity", 0.0)),
                    client_order_id=order_id,
                )
                if order.instrument_id == instrument_id:
                    hummingbot_orders.append(order)
            return hummingbot_orders

        # Fall back to tracked orders
        return [
            order
            for order in self._submitted_orders.values()
            if order.instrument_id == instrument_id
        ]

    def get_mid_price(self, instrument_id: str) -> float:
        """Get current mid price for an instrument.

        Args:
            instrument_id: Trading pair symbol

        Returns:
            Mid price as float, or 0.0 if not available
        """
        try:
            price = self._connector.get_mid_price(instrument_id)
            return float(price) if price is not None else 0.0
        except (AttributeError, TypeError):
            return 0.0

    def get_balance(self, currency: str) -> float:
        """Get available balance for a currency.

        Args:
            currency: Currency code (e.g., "USDT", "BTC")

        Returns:
            Available balance as float, or 0.0 if not available
        """
        try:
            balance = self._connector.get_balance(currency)
            return float(balance.available) if balance is not None else 0.0
        except (AttributeError, TypeError):
            return 0.0

    def get_instrument(self, instrument_id: str) -> InstrumentInfo:
        """Get instrument metadata.

        Returns a basic InstrumentInfo with default precision values.
        Hummingbot doesn't expose instrument metadata via connector,
        so we return defaults.

        Args:
            instrument_id: Trading pair symbol

        Returns:
            InstrumentInfo with symbol and default precision values
        """
        return InstrumentInfo(symbol=instrument_id)

    def _connector_name(self) -> str:
        """Get the connector name from strategy.

        Returns:
            Connector name (e.g., "binance_paper_trade")
        """
        return getattr(self._strategy, "exchange", "unknown")
