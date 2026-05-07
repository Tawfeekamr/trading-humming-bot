import threading
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class OrderSide(Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(Enum):
    PENDING = "PENDING"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"


@dataclass
class GridOrder:
    order_id: str
    level: int
    side: OrderSide
    price: float
    quantity: float
    status: OrderStatus = OrderStatus.PENDING


class OrderTracker:
    def __init__(self):
        self._orders: dict[str, GridOrder] = {}
        self._lock = threading.Lock()

    def add(self, order: GridOrder) -> None:
        with self._lock:
            self._orders[order.order_id] = order

    def mark_filled(self, order_id: str) -> Optional[GridOrder]:
        with self._lock:
            order = self._orders.get(order_id)
            if order:
                order.status = OrderStatus.FILLED
            return order

    def cancel_all(self) -> list[str]:
        with self._lock:
            cancelled_ids = []
            for order in self._orders.values():
                if order.status == OrderStatus.PENDING:
                    order.status = OrderStatus.CANCELLED
                    cancelled_ids.append(order.order_id)
            return cancelled_ids

    def pending_orders(self) -> list[GridOrder]:
        with self._lock:
            return [o for o in self._orders.values() if o.status == OrderStatus.PENDING]

    def filled_orders(self) -> list[GridOrder]:
        with self._lock:
            return [o for o in self._orders.values() if o.status == OrderStatus.FILLED]

    def clear_history(self) -> None:
        with self._lock:
            self._orders = {
                oid: o for oid, o in self._orders.items()
                if o.status == OrderStatus.PENDING
            }

    @property
    def total_pending(self) -> int:
        with self._lock:
            return sum(1 for o in self._orders.values() if o.status == OrderStatus.PENDING)
