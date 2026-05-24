# tests/test_grid_manager.py
import pytest
from src.grid.grid_manager import GridManager
from src.indicators.bollinger import BBResult


class TestGridManager:
    def test_calculate_grid_levels_count(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=180, mid=170, lower=160),
            atr_value=1.5,
        )
        assert len(grid.buy_levels) == 8
        assert len(grid.sell_levels) == 8

    def test_buy_levels_within_bb_bounds(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=180, mid=170, lower=160),
            atr_value=1.5,
        )
        for level in grid.buy_levels:
            assert level["price"] >= 160
            assert level["price"] <= 180

    def test_sell_levels_within_bb_bounds(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=180, mid=170, lower=160),
            atr_value=1.5,
        )
        for level in grid.sell_levels:
            assert level["price"] >= 160
            assert level["price"] <= 180

    def test_order_size_bounded_by_capital(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=180, mid=170, lower=160),
            atr_value=1.5,
        )
        deployable = 200 - 50
        total_buy = sum(l["quantity"] * l["price"] for l in grid.buy_levels)
        assert total_buy <= deployable * 1.01

    def test_spacing_based_on_bb_range(self):
        gm = GridManager(levels=4, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=174, mid=170, lower=166),
            atr_value=1.0,
        )
        # atr_spacing = 1.0 * 0.8 = 0.8
        # max_spacing = (170 - 166) / 5 = 0.8
        # spacing = min(0.8, 0.8) = 0.8
        assert grid.buy_spacing > 0

    def test_levels_spread_across_bb_range(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=172.0, mid=170.0, lower=168.0),
            atr_value=1.0,
        )
        assert len(grid.buy_levels) == 8
        assert len(grid.sell_levels) == 8
        # All buys should be within BB bounds
        for level in grid.buy_levels:
            assert level["price"] >= 168.0
            assert level["price"] < 170.0
        for level in grid.sell_levels:
            assert level["price"] <= 172.0
            assert level["price"] > 170.0
        # Spacing should be capped by BB bands
        # max_buy_spacing = (170 - 168) / 9 = 0.2222...
        expected_spacing = (170.0 - 168.0) / 9
        assert abs(grid.buy_spacing - round(expected_spacing, 2)) < 0.01

    def test_zero_atr_raises_error(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        with pytest.raises(ValueError, match="atr_value must be positive"):
            gm.calculate_grid(
                bb=BBResult(upper=180, mid=170, lower=160),
                atr_value=0.0,
            )

    def test_negative_atr_raises_error(self):
        gm = GridManager(levels=8, capital_usdt=200, min_reserve=50)
        with pytest.raises(ValueError, match="atr_value must be positive"):
            gm.calculate_grid(
                bb=BBResult(upper=180, mid=170, lower=160),
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

    def test_buy_levels_have_geometric_spacing(self):
        """Buy levels should spread wider at deeper levels (geometric, not linear).
        Sorted by level number, distance from mid should grow geometrically."""
        gm = GridManager(levels=4, capital_usdt=1000, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=660, mid=650, lower=640),
            atr_value=3.0,
        )
        assert len(grid.buy_levels) == 4
        # Sort by level so level 1 (closest to mid) comes first
        by_level = sorted(grid.buy_levels, key=lambda l: l["level"])
        dists_from_mid = [650.0 - l["price"] for l in by_level]
        for i in range(len(dists_from_mid) - 1):
            assert dists_from_mid[i + 1] > dists_from_mid[i], \
                f"Level {i+2} dist ({dists_from_mid[i+1]:.4f}) should be > level {i+1} dist ({dists_from_mid[i]:.4f})"

    def test_buy_levels_have_increasing_size(self):
        """Deeper buy levels should have larger order sizes."""
        gm = GridManager(levels=4, capital_usdt=1000, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=660, mid=650, lower=640),
            atr_value=3.0,
        )
        assert len(grid.buy_levels) == 4
        # Sort by price descending (closest to mid first)
        sorted_levels = sorted(grid.buy_levels, key=lambda l: l["price"], reverse=True)
        # Deeper levels (lower price) should buy more
        for i in range(len(sorted_levels) - 1):
            assert sorted_levels[i]["quantity"] < sorted_levels[i + 1]["quantity"], \
                f"Level {i} qty ({sorted_levels[i]['quantity']}) should be < level {i+1} qty ({sorted_levels[i+1]['quantity']})"

    def test_sell_levels_have_uniform_spacing(self):
        """Sell levels should remain uniformly spaced (not geometric)."""
        gm = GridManager(levels=4, capital_usdt=1000, min_reserve=50)
        grid = gm.calculate_grid(
            bb=BBResult(upper=660, mid=650, lower=640),
            atr_value=3.0,
        )
        assert len(grid.sell_levels) == 4
        prices = sorted([l["price"] for l in grid.sell_levels])
        gaps = [round(prices[i + 1] - prices[i], 2) for i in range(len(prices) - 1)]
        # All gaps should be equal (uniform)
        assert len(set(gaps)) == 1, f"Sell gaps should be uniform, got {gaps}"

    def test_total_buy_capital_matches_budget(self):
        """Total buy-side capital should equal half of deployable capital."""
        gm = GridManager(levels=4, capital_usdt=1000, min_reserve=100)
        grid = gm.calculate_grid(
            bb=BBResult(upper=660, mid=650, lower=640),
            atr_value=3.0,
        )
        deployable_buy = (1000 - 100) / 2  # 450
        total_buy = sum(l["quantity"] * l["price"] for l in grid.buy_levels)
        assert abs(total_buy - deployable_buy) < 5.0, f"Total buy {total_buy} should be ~{deployable_buy}"


class TestGridManagerTickSize:
    """Tests for dynamic tick_size precision rounding."""

    def test_default_tick_size_is_0_01(self):
        gm = GridManager(levels=4, capital_usdt=200)
        assert gm.tick_size == 0.01

    def test_doge_tick_size_precision(self):
        """DOGE has tick_size=0.00001 — prices should be rounded to 5 decimals."""
        gm = GridManager(levels=4, capital_usdt=200, tick_size=0.00001, step_size=1)
        grid = gm.calculate_grid(
            bb=BBResult(upper=0.26, mid=0.25, lower=0.24),
            atr_value=0.002,
        )
        for level in grid.buy_levels + grid.sell_levels:
            # Price should be a multiple of 0.00001
            assert round(level["price"] / 0.00001) * 0.00001 == pytest.approx(level["price"], abs=1e-8)

    def test_xrp_tick_size_precision(self):
        """XRP has tick_size=0.0001 — prices should be rounded to 4 decimals."""
        gm = GridManager(levels=4, capital_usdt=200, tick_size=0.0001, step_size=0.1)
        grid = gm.calculate_grid(
            bb=BBResult(upper=2.60, mid=2.50, lower=2.40),
            atr_value=0.02,
        )
        for level in grid.buy_levels + grid.sell_levels:
            assert round(level["price"] / 0.0001) * 0.0001 == pytest.approx(level["price"], abs=1e-7)

    def test_eth_tick_size_precision(self):
        """ETH has tick_size=0.01 — prices should be rounded to 2 decimals."""
        gm = GridManager(levels=4, capital_usdt=5000, tick_size=0.01, step_size=0.0001)
        grid = gm.calculate_grid(
            bb=BBResult(upper=2700, mid=2600, lower=2500),
            atr_value=20.0,
        )
        for level in grid.buy_levels + grid.sell_levels:
            assert round(level["price"], 2) == pytest.approx(level["price"], abs=1e-9)

    def test_tick_size_does_not_affect_spacing_logic(self):
        """Different tick_size shouldn't change the number of valid levels."""
        gm1 = GridManager(levels=5, capital_usdt=200, tick_size=0.01)
        gm2 = GridManager(levels=5, capital_usdt=200, tick_size=0.00001)
        grid1 = gm1.calculate_grid(BBResult(upper=180, mid=170, lower=160), atr_value=1.5)
        grid2 = gm2.calculate_grid(BBResult(upper=180, mid=170, lower=160), atr_value=1.5)
        # Same number of levels should be generated
        assert len(grid1.buy_levels) == len(grid2.buy_levels)
        assert len(grid1.sell_levels) == len(grid2.sell_levels)
