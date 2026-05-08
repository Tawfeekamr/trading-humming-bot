# tests/test_grid_manager.py
import pytest
from src.grid.grid_manager import GridManager
from src.indicators.bollinger import BBResult


class TestGridManager:
    def test_calculate_grid_levels_count(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        assert len(grid.buy_levels) == 8
        assert len(grid.sell_levels) == 8

    def test_buy_levels_within_bb_bounds(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        for level in grid.buy_levels:
            assert level["price"] >= 95_000
            assert level["price"] <= 105_000

    def test_sell_levels_within_bb_bounds(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        for level in grid.sell_levels:
            assert level["price"] >= 95_000
            assert level["price"] <= 105_000

    def test_order_size_bounded_by_capital(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
            atr_value=800,
        )
        deployable = 200 - 50
        total_buy = sum(l["quantity"] * l["price"] for l in grid.buy_levels)
        assert total_buy <= deployable * 1.01

    def test_spacing_based_on_bb_range(self):
        gm = GridManager(levels=4, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=104_000, mid=100_000, lower=96_000),
            atr_value=500,
        )
        # atr_spacing = 500 * 0.8 = 400
        # max_spacing = (100000 - 96000) / 5 = 800
        # spacing = min(400, 800) = 400
        assert grid.spacing == 400.0
        # Buy level 1 (closest to mid): bb.lower + spacing * 4 = 96000 + 1600*4 = 102400
        # Wait, buys sorted descending — first buy is the one closest to mid
        # buy prices: 96000+1600*1=97600, 96000+1600*2=99200, 96000+1600*3=100800(>mid!), 96000+1600*4=102400(>mid!)
        # Hmm, with BB mid at 100k and lower at 96k, buy at level 3 = 100800 > mid
        # But the test_buy_levels_below_mid test checks < 100000
        # Let me recalculate: buys go from bb.lower upward
        # level 1: 96k + 1600 = 97600 (< 100k ✓)
        # level 2: 96k + 3200 = 99200 (< 100k ✓)
        # level 3: 96k + 4800 = 100800 (> 100k ✗!)
        # This means some buy levels can be above mid when BB mid is close to bb.lower
        # The real BTC scenario: bb.lower=79375, bb.mid=79858, bb.upper=80340
        # bb_range = 965, levels=8, spacing = 965/9 ≈ 107
        # buy levels: 79375+107, 79375+214, ..., 79375+856 = 80231 (all below mid 79858? No, level 5+ would exceed mid)
        # Actually 79375 + 107*5 = 79910 > 79858
        # So buys can go above mid. That's fine — the strategy filters out buys above current price.
        assert grid.spacing > 0

    def test_levels_spread_across_bb_range(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=80_340, mid=79_858, lower=79_375),
            atr_value=324,
        )
        assert len(grid.buy_levels) == 8
        assert len(grid.sell_levels) == 8
        # All buys should be within BB bounds
        for level in grid.buy_levels:
            assert level["price"] >= 79_375
            assert level["price"] < 79_858
        for level in grid.sell_levels:
            assert level["price"] <= 80_340
            assert level["price"] > 79_858
        # Spacing should be capped by BB bands
        # atr_spacing = 324 * 0.8 = 259.2
        # max_buy_spacing = (79858 - 79375) / 9 = 53.666...
        expected_spacing = (79_858 - 79_375) / 9
        assert abs(grid.spacing - round(expected_spacing, 2)) < 0.01

    def test_zero_atr_raises_error(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        with pytest.raises(ValueError, match="atr_value must be positive"):
            gm.calculate_grid(
                bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
                atr_value=0.0,
            )

    def test_negative_atr_raises_error(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        with pytest.raises(ValueError, match="atr_value must be positive"):
            gm.calculate_grid(
                bb=BBResult(upper=105_000, mid=100_000, lower=95_000),
                atr_value=-100.0,
            )

    def test_invalid_levels_raises_error(self):
        with pytest.raises(ValueError, match="levels must be positive"):
            GridManager(levels=0, capital_usdt=200, min_reserve=50)

    def test_invalid_capital_raises_error(self):
        with pytest.raises(ValueError, match="capital_usdt must be positive"):
            GridManager(levels=8, capital_usdt=0, min_reserve=50)

    def test_invalid_spacing_multiplier_raises_error(self):
        with pytest.raises(ValueError, match="spacing_multiplier must be positive"):
            GridManager(levels=8, capital_usdt=200, min_reserve=50, spacing_multiplier=0.0)
