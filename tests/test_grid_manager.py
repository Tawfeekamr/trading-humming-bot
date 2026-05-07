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
