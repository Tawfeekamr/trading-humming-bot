from dataclasses import dataclass
from src.indicators.bollinger import BBResult


@dataclass
class GridLayout:
    buy_levels: list[dict]
    sell_levels: list[dict]
    spacing: float
    mid_price: float


class GridManager:
    def __init__(self, levels: int = 8, capital_usdt: float = 200,
                 min_reserve: float = 50, spacing_multiplier: float = 0.8):
        if levels <= 0:
            raise ValueError(f"levels must be positive, got {levels}")
        if capital_usdt <= 0:
            raise ValueError(f"capital_usdt must be positive, got {capital_usdt}")
        if spacing_multiplier <= 0:
            raise ValueError(f"spacing_multiplier must be positive, got {spacing_multiplier}")
        self.levels = levels
        self.capital_usdt = capital_usdt
        self.min_reserve = min_reserve
        self.spacing_multiplier = spacing_multiplier

    def calculate_grid(self, bb: BBResult, atr_value: float) -> GridLayout:
        if atr_value <= 0:
            raise ValueError(f"atr_value must be positive, got {atr_value}")

        bb_range = bb.upper - bb.lower
        if bb_range <= 0:
            raise ValueError(f"BB range must be positive, got {bb_range}")

        deployable = self.capital_usdt - self.min_reserve
        order_value = deployable / (self.levels * 2)

        # Spread levels evenly across the BB range.
        # Each level gets an equal slice of the band.
        spacing = bb_range / (self.levels + 1)

        buy_levels = []
        sell_levels = []

        for i in range(1, self.levels + 1):
            # Buy levels: from BB lower upward
            buy_price = bb.lower + spacing * i
            buy_qty = order_value / buy_price

            # Sell levels: from BB upper downward
            sell_price = bb.upper - spacing * i
            sell_qty = order_value / sell_price

            buy_levels.append({
                "price": round(buy_price, 2),
                "quantity": round(buy_qty, 8),
                "level": i,
            })
            sell_levels.append({
                "price": round(sell_price, 2),
                "quantity": round(sell_qty, 8),
                "level": i,
            })

        # Sort buys descending (closest to mid first) and sells ascending
        buy_levels.sort(key=lambda l: l["price"], reverse=True)
        sell_levels.sort(key=lambda l: l["price"])

        return GridLayout(
            buy_levels=buy_levels,
            sell_levels=sell_levels,
            spacing=round(spacing, 2),
            mid_price=bb.mid,
        )
