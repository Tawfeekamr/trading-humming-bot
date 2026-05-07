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
        self.levels = levels
        self.capital_usdt = capital_usdt
        self.min_reserve = min_reserve
        self.spacing_multiplier = spacing_multiplier

    def calculate_grid(self, bb: BBResult, atr_value: float) -> GridLayout:
        spacing = atr_value * self.spacing_multiplier
        deployable = self.capital_usdt - self.min_reserve
        order_value = deployable / (self.levels * 2)

        buy_levels = []
        sell_levels = []

        for i in range(1, self.levels + 1):
            buy_price = bb.mid - spacing * i
            buy_price = max(buy_price, bb.lower)
            buy_qty = order_value / buy_price

            sell_price = bb.mid + spacing * i
            sell_price = min(sell_price, bb.upper)
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

        return GridLayout(
            buy_levels=buy_levels,
            sell_levels=sell_levels,
            spacing=spacing,
            mid_price=bb.mid,
        )
