from dataclasses import dataclass
from src.indicators.bollinger import BBResult


@dataclass
class GridLayout:
    buy_levels: list[dict]
    sell_levels: list[dict]
    buy_spacing: float
    sell_spacing: float
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

        # 1. Calculate the desired spacing based on volatility (ATR).
        atr_spacing = atr_value * self.spacing_multiplier
        
        # 2. Calculate the maximum allowed spacing to keep the grid within BB bands.
        # We use (levels + 1) to ensure even the outermost level is within the band.
        max_buy_spacing = (bb.mid - bb.lower) / (self.levels + 1)
        max_sell_spacing = (bb.upper - bb.mid) / (self.levels + 1)
        
        # 3. Use the smaller of the two to ensure safety and logic.
        buy_spacing = min(atr_spacing, max_buy_spacing)
        sell_spacing = min(atr_spacing, max_sell_spacing)
        
        deployable = self.capital_usdt - self.min_reserve
        # Still divide by (levels * 2) to maintain the same conservative capital allocation
        order_value = deployable / (self.levels * 2)

        buy_levels = []
        sell_levels = []

        for i in range(1, self.levels + 1):
            # Buy levels: step DOWN from mid
            buy_price = bb.mid - (buy_spacing * i)
            buy_qty = order_value / buy_price

            # Sell levels: step UP from mid
            sell_price = bb.mid + (sell_spacing * i)
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
            buy_spacing=round(buy_spacing, 2),
            sell_spacing=round(sell_spacing, 2),
            mid_price=bb.mid,
        )
