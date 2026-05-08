import pytest
from src.risk.position_guard import PositionGuard


class TestPositionGuardNegativeOrders:
    """Test validation of negative or zero order amounts."""

    def test_rejects_negative_order_usdt(self):
        """Should reject negative order amount."""
        pg = PositionGuard(max_btc_exposure_pct=80, min_usdt_reserve=50,
                          total_capital=200)
        result = pg.can_place_order(
            current_btc=0.001, btc_price=100_000,
            current_usdt=100, order_usdt=-20
        )
        assert result is False

    def test_rejects_zero_order_usdt(self):
        """Should reject zero order amount."""
        pg = PositionGuard(max_btc_exposure_pct=80, min_usdt_reserve=50,
                          total_capital=200)
        result = pg.can_place_order(
            current_btc=0.001, btc_price=100_000,
            current_usdt=100, order_usdt=0
        )
        assert result is False


class TestPositionGuard:
    def test_allows_order_within_exposure(self):
        pg = PositionGuard(max_btc_exposure_pct=80, min_usdt_reserve=50,
                           total_capital=200)
        assert pg.can_place_order(
            current_btc=0.001, btc_price=100_000,
            current_usdt=100, order_usdt=20
        )

    def test_blocks_order_exceeding_exposure(self):
        pg = PositionGuard(max_btc_exposure_pct=80, min_usdt_reserve=50,
                           total_capital=200)
        assert not pg.can_place_order(
            current_btc=0.0015, btc_price=100_000,
            current_usdt=50, order_usdt=30
        )

    def test_blocks_order_below_usdt_reserve(self):
        pg = PositionGuard(max_btc_exposure_pct=80, min_usdt_reserve=50,
                           total_capital=200)
        assert not pg.can_place_order(
            current_btc=0.0005, btc_price=100_000,
            current_usdt=60, order_usdt=20
        )

    def test_btc_exposure_pct_calculation(self):
        pg = PositionGuard(max_btc_exposure_pct=80, min_usdt_reserve=50,
                           total_capital=200)
        pct = pg.btc_exposure_pct(current_btc=0.001, btc_price=100_000)
        assert pct == 50.0
