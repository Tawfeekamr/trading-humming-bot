# tests/test_grid_manager.py
import pytest
from src.grid.grid_manager import GridManager
from src.indicators.bollinger import BBResult
from src.grid.grid_state import GridState


class TestGridManager:
    def test_calculate_grid_levels_count(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        # Due to deduplication, when spacing exceeds BB range, levels are limited
        # BB range is 95k-105k (10k range), spacing is 640 (800*0.8)
        # Max levels from mid before hitting boundary: 100k-640*8=94880 (<95k lower)
        # So we break early at level 7 to avoid duplicates
        assert len(grid.buy_levels) == 7
        assert len(grid.sell_levels) == 7

    def test_buy_levels_below_mid(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        for level in grid.buy_levels:
            assert level["price"] < 100_000

    def test_sell_levels_above_mid(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        for level in grid.sell_levels:
            assert level["price"] > 100_000

    def test_order_size_bounded_by_capital(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        deployable = 200 - 50
        total_buy = sum(l["quantity"] * l["price"] for l in grid.buy_levels)
        assert total_buy <= deployable * 1.01

    def test_spacing_matches_atr(self):
        gm = GridManager(levels=4, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=104_000, mid=100_000, lower=96_000),
            atr_value=500,
        )
        spacing = 500 * 0.8
        assert abs(grid.buy_levels[0]["price"] - (100_000 - spacing)) < 1.0

    def test_zero_atr_raises_error(self):
        """Zero ATR should raise ValueError to prevent invalid grid."""
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        with pytest.raises(ValueError, match="atr_value must be positive"):
            gm.calculate_grid(
                bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
                atr_value=0.0,
            )

    def test_negative_atr_raises_error(self):
        """Negative ATR should raise ValueError."""
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        with pytest.raises(ValueError, match="atr_value must be positive"):
            gm.calculate_grid(
                bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
                atr_value=-100.0,
            )

    def test_invalid_levels_raises_error(self):
        """Non-positive levels should raise ValueError."""
        with pytest.raises(ValueError, match="levels must be positive"):
            GridManager(levels=0, capital_usdt=200, min_reserve=50)

    def test_invalid_capital_raises_error(self):
        """Non-positive capital should raise ValueError."""
        with pytest.raises(ValueError, match="capital_usdt must be positive"):
            GridManager(levels=8, capital_usdt=0, min_reserve=50)

    def test_invalid_spacing_multiplier_raises_error(self):
        """Non-positive spacing_multiplier should raise ValueError."""
        with pytest.raises(ValueError, match="spacing_multiplier must be positive"):
            GridManager(levels=8, capital_usdt=200, min_reserve=50, spacing_multiplier=0.0)
