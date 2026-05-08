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

        # Use ATR-based spacing anchored at the mid price.
        # This ensures buys are below mid and sells are above mid.
        spacing = atr_value * self.spacing_multiplier
        
        deployable = self.capital_usdt - self.min_reserve
        # Still divide by (levels * 2) to maintain the same conservative capital allocation
        order_value = deployable / (self.levels * 2)

        buy_levels = []
        sell_levels = []

        for i in range(1, self.levels + 1):
            # Buy levels: step DOWN from mid
            buy_price = bb.mid - (spacing * i)
            buy_qty = order_value / buy_price

            # Sell levels: step UP from mid
            sell_price = bb.mid + (spacing * i)
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

        # Sort buys descending (highest price first) and sells ascending (lowest price first)
        buy_levels.sort(key=lambda l: l["price"], reverse=True)
        sell_levels.sort(key=lambda l: l["price"])

        return GridLayout(
            buy_levels=buy_levels,
            sell_levels=sell_levels,
            spacing=round(spacing, 2),
            mid_price=bb.mid,
        )
