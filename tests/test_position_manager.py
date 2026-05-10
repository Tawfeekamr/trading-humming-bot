import pytest
import tempfile
from pathlib import Path
from src.trend.position_manager import PositionManager, TrendPosition


@pytest.fixture
def manager():
    return PositionManager(capital=2000.0)


class TestTrendPosition:
    def test_create_position(self):
        pos = TrendPosition(
            entry_order_id="abc123",
            entry_price=94.20,
            amount=14.0,
            stop_loss=91.30,
            take_profit=100.00,
            entry_time="2026-05-11T10:00:00Z",
        )
        assert pos.entry_price == 94.20
        assert pos.trailing_stop == 91.30
        assert pos.trailing_activated is False


class TestPositionManager:
    def test_open_position(self, manager):
        pos = manager.open_position("abc123", 94.20, 14.0, 91.30, 100.00, "2026-05-11T10:00:00Z")
        assert pos is not None
        assert manager.open_count == 1
        assert manager.can_open() is False

    def test_can_open_respects_max_positions(self):
        mgr = PositionManager(capital=2000.0, max_positions=2)
        mgr.open_position("id1", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        assert mgr.can_open() is True
        mgr.open_position("id2", 95.0, 14.0, 92.0, 101.0, "2026-05-11T10:00:00Z")
        assert mgr.can_open() is False

    def test_close_position(self, manager):
        manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        closed = manager.close_position("abc", exit_price=100.0, exit_reason="take_profit")
        assert closed is not None
        assert closed["pnl"] > 0
        assert closed["exit_reason"] == "take_profit"
        assert manager.open_count == 0

    def test_close_position_pnl_calculation(self, manager):
        manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        closed = manager.close_position("abc", exit_price=91.0, exit_reason="stop_loss")
        expected_pnl = (91.0 - 94.0) * 14.0
        assert abs(closed["pnl"] - expected_pnl) < 0.01
        assert closed["pnl"] < 0

    def test_check_exits_hit_take_profit(self, manager):
        manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        exits = manager.check_exits(current_price=100.5)
        assert len(exits) == 1
        assert exits[0]["reason"] == "take_profit"

    def test_check_exits_hit_stop_loss(self, manager):
        manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        exits = manager.check_exits(current_price=90.5)
        assert len(exits) == 1
        assert exits[0]["reason"] == "stop_loss"

    def test_check_exits_trailing_stop(self, manager):
        pos = manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        manager.update_trailing(pos, current_price=96.0)
        assert pos.trailing_activated is True
        assert pos.trailing_stop > 91.0
        exits = manager.check_exits(current_price=pos.trailing_stop - 0.1)
        assert len(exits) == 1
        assert exits[0]["reason"] == "trailing_stop"

    def test_trailing_stop_only_moves_up(self, manager):
        pos = manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        manager.update_trailing(pos, current_price=96.0)
        trail_after_rise = pos.trailing_stop
        manager.update_trailing(pos, current_price=94.5)
        assert pos.trailing_stop == trail_after_rise

    def test_position_size_calculation(self, manager):
        size = manager.calculate_position_size(entry_price=94.0, stop_loss_price=91.3)
        expected_size = (2000.0 * 0.02) / (94.0 - 91.3)
        assert abs(size - expected_size) < 0.01

    def test_position_size_capped_at_25pct(self):
        mgr = PositionManager(capital=100.0, max_position_pct=25.0)
        size = mgr.calculate_position_size(entry_price=94.0, stop_loss_price=93.99)
        max_notional = 100.0 * 0.25
        assert size * 94.0 <= max_notional + 1.0

    def test_get_open_position(self, manager):
        manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        pos = manager.get_position("abc")
        assert pos is not None
        assert pos.entry_price == 94.0

    def test_get_position_returns_none_if_not_found(self, manager):
        assert manager.get_position("nonexistent") is None

    def test_save_and_load_state(self, manager):
        tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        tmp.close()
        path = Path(tmp.name)

        manager.open_position("abc", 94.0, 14.0, 91.0, 100.0, "2026-05-11T10:00:00Z")
        manager.save_state(path)

        mgr2 = PositionManager(capital=2000.0)
        mgr2.load_state(path)
        assert mgr2.open_count == 1
        pos = mgr2.get_position("abc")
        assert pos.entry_price == 94.0

        Path(tmp.name).unlink()
