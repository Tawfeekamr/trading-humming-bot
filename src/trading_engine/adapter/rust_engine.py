"""RustEngineAdapter — HTTP client that implements ExecutionAdapter ABC.

Routes all exchange operations through the Rust trading engine's REST API,
removing the Hummingbot dependency while preserving the Python strategy layer.

Usage:
    adapter = RustEngineAdapter("http://localhost:3030")
    price = adapter.get_mid_price("BTC-USDT")
    balance = adapter.get_balance("USDT")
    order = Order("BTC-USDT", "BUY", "LIMIT", 50000.0, 0.001)
    order_id = adapter.submit_order(order)
"""
import logging
from typing import Any

import requests

from .base import ExecutionAdapter, Order, InstrumentInfo

logger = logging.getLogger(__name__)

# Default instrument precision per quote currency
_DEFAULT_PRECISION = {
    "USDT": {"price": 2, "quantity": 4},
    "BTC": {"price": 8, "quantity": 6},
}


class RustEngineAdapter(ExecutionAdapter):
    """HTTP client adapter that talks to the Rust trading engine API.

    The Rust engine exposes a REST API (axum) on localhost:3030 with endpoints
    that map 1:1 to the Connector trait methods. This adapter translates between
    the Python ExecutionAdapter interface and those HTTP endpoints.

    Args:
        base_url: Base URL of the Rust engine API (default: http://localhost:3030)
        timeout: HTTP request timeout in seconds (default: 10)
    """

    def __init__(self, base_url: str = "http://localhost:3030", timeout: float = 10.0):
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})
        self._submitted_orders: dict[str, Order] = {}
        self._instruments: dict[str, InstrumentInfo] = {}

    # ── Health check ──────────────────────────────────────────────────

    def is_healthy(self) -> bool:
        """Check if the Rust engine API is responding."""
        try:
            resp = self._session.get(
                f"{self._base_url}/api/v1/health", timeout=self._timeout
            )
            return resp.status_code == 200
        except requests.RequestException:
            return False

    # ── Order management ──────────────────────────────────────────────

    def submit_order(self, order: Order) -> str:
        """Submit an order via the Rust engine API.

        Args:
            order: Order dataclass with instrument_id, side, order_type, price, quantity

        Returns:
            Exchange order ID
        """
        # Convert instrument format: "BTC-USDT" → "BTCUSDT" for exchange
        symbol = order.instrument_id.replace("-", "")

        payload = {
            "symbol": symbol,
            "side": order.side.upper(),
            "order_type": order.order_type.capitalize(),
            "price": order.price,
            "quantity": order.quantity,
            "time_in_force": "Gtc",
            "client_order_id": order.client_order_id or None,
        }

        resp = self._session.post(
            f"{self._base_url}/api/v1/order",
            json=payload,
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        order_id = data.get("orderId", data.get("order_id", ""))
        # Track submitted order
        tracked = Order(
            instrument_id=order.instrument_id,
            side=order.side,
            order_type=order.order_type,
            price=order.price,
            quantity=order.quantity,
            client_order_id=order_id,
        )
        self._submitted_orders[order_id] = tracked
        logger.info("Order submitted: %s %s %s @ %s", order_id, order.side, symbol, order.price)
        return order_id

    def cancel_order(self, client_order_id: str) -> None:
        """Cancel an existing order.

        Args:
            client_order_id: Exchange order ID to cancel
        """
        order = self._submitted_orders.get(client_order_id)
        if order is None:
            logger.warning("Cancel called for unknown order: %s", client_order_id)
            return

        symbol = order.instrument_id.replace("-", "")
        resp = self._session.delete(
            f"{self._base_url}/api/v1/order",
            params={"symbol": symbol, "order_id": client_order_id},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        self._submitted_orders.pop(client_order_id, None)
        logger.info("Order cancelled: %s", client_order_id)

    def cancel_all_orders(self, instrument_id: str) -> None:
        """Cancel all open orders for an instrument.

        Args:
            instrument_id: Trading pair symbol (e.g., "BTC-USDT")
        """
        symbol = instrument_id.replace("-", "")
        resp = self._session.delete(
            f"{self._base_url}/api/v1/orders",
            params={"symbol": symbol},
            timeout=self._timeout,
        )
        resp.raise_for_status()

        # Clean up tracked orders
        to_remove = [
            oid for oid, o in self._submitted_orders.items()
            if o.instrument_id == instrument_id
        ]
        for oid in to_remove:
            del self._submitted_orders[oid]
        logger.info("Cancelled all orders for %s (%d tracked)", instrument_id, len(to_remove))

    def get_open_orders(self, instrument_id: str) -> list[Order]:
        """Get all open orders for an instrument from the exchange.

        Args:
            instrument_id: Trading pair symbol

        Returns:
            List of Order objects
        """
        symbol = instrument_id.replace("-", "")
        resp = self._session.get(
            f"{self._base_url}/api/v1/orders",
            params={"symbol": symbol},
            timeout=self._timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        orders = []
        for raw in data:
            # Convert exchange symbol back to instrument format
            raw_symbol = raw.get("symbol", symbol)
            pair = _exchange_to_pair(raw_symbol)
            side = "BUY" if str(raw.get("side", "")).upper() == "BUY" else "SELL"
            orders.append(Order(
                instrument_id=pair,
                side=side,
                order_type="LIMIT",
                price=float(raw.get("price", 0.0)),
                quantity=float(raw.get("origQty", raw.get("quantity", 0.0))),
                client_order_id=str(raw.get("orderId", raw.get("order_id", ""))),
            ))
        return orders

    # ── Market data ───────────────────────────────────────────────────

    def get_mid_price(self, instrument_id: str) -> float:
        """Get current mid price via the order book endpoint.

        Args:
            instrument_id: Trading pair symbol

        Returns:
            Mid price as float, or 0.0 if unavailable
        """
        symbol = instrument_id.replace("-", "")
        try:
            resp = self._session.get(
                f"{self._base_url}/api/v1/orderbook",
                params={"symbol": symbol, "limit": 1},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            bids = data.get("bids", [])
            asks = data.get("asks", [])
            if bids and asks:
                best_bid = float(bids[0][0]) if isinstance(bids[0], (list, tuple)) else float(bids[0])
                best_ask = float(asks[0][0]) if isinstance(asks[0], (list, tuple)) else float(asks[0])
                return (best_bid + best_ask) / 2.0
        except requests.RequestException as e:
            logger.warning("Failed to get mid price for %s: %s", instrument_id, e)
        return 0.0

    # ── Account data ──────────────────────────────────────────────────

    def get_balance(self, currency: str) -> float:
        """Get available balance for a currency.

        Args:
            currency: Currency code (e.g., "USDT")

        Returns:
            Available balance as float
        """
        try:
            resp = self._session.get(
                f"{self._base_url}/api/v1/balances",
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            for entry in data:
                if entry.get("asset") == currency:
                    return float(entry.get("free", 0.0))
        except requests.RequestException as e:
            logger.warning("Failed to get balance for %s: %s", currency, e)
        return 0.0

    def get_instrument(self, instrument_id: str) -> InstrumentInfo:
        """Get instrument metadata.

        Returns cached info if available, otherwise infers from pair name.

        Args:
            instrument_id: Trading pair symbol

        Returns:
            InstrumentInfo with precision values
        """
        if instrument_id in self._instruments:
            return self._instruments[instrument_id]

        # Infer precision from quote currency
        quote = instrument_id.split("-")[-1] if "-" in instrument_id else "USDT"
        prec = _DEFAULT_PRECISION.get(quote, {"price": 4, "quantity": 4})
        return InstrumentInfo(
            symbol=instrument_id,
            price_precision=prec["price"],
            quantity_precision=prec["quantity"],
        )


def _exchange_to_pair(symbol: str) -> str:
    """Convert exchange symbol format to pair format.

    Examples:
        "BTCUSDT" → "BTC-USDT"
        "DOGEUSDT" → "DOGE-USDT"
        "BTC-USDT" → "BTC-USDT" (already in pair format)
    """
    if "-" in symbol:
        return symbol
    # Common quote currencies to split on
    for quote in ("USDT", "USDC", "BTC", "ETH", "BUSD", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            base = symbol[:-len(quote)]
            return f"{base}-{quote}"
    return symbol
