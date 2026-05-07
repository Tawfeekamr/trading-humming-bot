import pytest
from src.grid.order_tracker import OrderTracker, GridOrder, OrderSide, OrderStatus


class TestOrderTracker:
    def test_add_and_retrieve(self):
        tracker = OrderTracker()
        order = GridOrder("ord-1", 1, OrderSide.BUY, 99_000, 0.001)
        tracker.add(order)
        assert tracker.total_pending == 1

    def test_mark_filled(self):
        tracker = OrderTracker()
        tracker.add(GridOrder("ord-1", 1, OrderSide.BUY, 99_000, 0.001))
        filled = tracker.mark_filled("ord-1")
        assert filled.status == OrderStatus.FILLED
        assert tracker.total_pending == 0

    def test_cancel_all(self):
        tracker = OrderTracker()
        tracker.add(GridOrder("ord-1", 1, OrderSide.BUY, 99_000, 0.001))
        tracker.add(GridOrder("ord-2", 2, OrderSide.BUY, 98_000, 0.001))
        cancelled = tracker.cancel_all()
        assert len(cancelled) == 2
        assert tracker.total_pending == 0

    def test_clear_history(self):
        tracker = OrderTracker()
        tracker.add(GridOrder("ord-1", 1, OrderSide.BUY, 99_000, 0.001))
        tracker.mark_filled("ord-1")
        tracker.add(GridOrder("ord-2", 2, OrderSide.BUY, 98_000, 0.001))
        tracker.clear_history()
        assert tracker.total_pending == 1
        assert len(tracker.filled_orders()) == 0

    def test_nonexistent_fill_returns_none(self):
        tracker = OrderTracker()
        result = tracker.mark_filled("ghost")
        assert result is None
