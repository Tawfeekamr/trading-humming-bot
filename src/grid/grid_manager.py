import math
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
    # Binance exchange filters (defaults suit most USDT pairs)
    MIN_NOTIONAL = 5.0       # $5 minimum order value
    TICK_SIZE = 0.01          # $0.01 price step
    FEE_RATE = 0.001          # 0.1% per side (Binance default)

    # Asymmetric grid: geometric scaling factors (buy-side only)
    SPACING_FACTOR = 0.10    # α: levels spread wider as price drops
    SIZE_FACTOR = 0.08       # β: buy more at lower prices

    def __init__(self, levels: int = 8, capital_usdt: float = 200,
                 min_reserve: float = 50, spacing_multiplier: float = 0.8,
                 step_size: float = 0.01):
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
        self.step_size = step_size

    def _validate_order(self, price: float, quantity: float) -> tuple:
        if not (math.isfinite(price) and math.isfinite(quantity) and price > 0 and quantity > 0):
            return None, None
        price = round(price / self.TICK_SIZE) * self.TICK_SIZE
        quantity = round(quantity / self.step_size) * self.step_size
        if not (math.isfinite(price) and math.isfinite(quantity)) or price * quantity < self.MIN_NOTIONAL:
            return None, None
        return round(price, 2), round(quantity, 8)

    def calculate_grid(self, bb: BBResult, atr_value: float) -> GridLayout:
        if atr_value <= 0:
            raise ValueError(f"atr_value must be positive, got {atr_value}")

        # 1. Calculate the desired spacing based on volatility (ATR).
        atr_spacing = atr_value * self.spacing_multiplier

        # 2. Minimum profitable spacing: round-trip fee × profit_multiplier (3x = net 2x fees profit).
        min_profit_spacing = bb.mid * self.FEE_RATE * 2 * 3

        # 3. Calculate the maximum allowed spacing to keep the grid within BB bands.
        max_buy_spacing = (bb.mid - bb.lower) / (self.levels + 1)
        max_sell_spacing = (bb.upper - bb.mid) / (self.levels + 1)

        # 4. Buy spacing: standard ATR-based, capped by BB lower band.
        buy_spacing = min(atr_spacing, max_buy_spacing)
        if min_profit_spacing <= max_buy_spacing:
            buy_spacing = max(buy_spacing, min_profit_spacing)

        # 5. Sell spacing: tighter (75% of buy) so sells fill faster in falling markets.
        sell_spacing = min(atr_spacing * 0.75, max_sell_spacing)
        if min_profit_spacing <= max_sell_spacing:
            sell_spacing = max(sell_spacing, min_profit_spacing)

        deployable = self.capital_usdt - self.min_reserve
        deployable_buy = deployable / 2
        deployable_sell = deployable / 2

        # Buy side: geometric scaling — spacing widens, size grows with depth
        alpha = self.SPACING_FACTOR
        beta = self.SIZE_FACTOR
        geometric_sum = sum((1 + beta) ** i for i in range(1, self.levels + 1))
        base_buy_value = deployable_buy / geometric_sum if geometric_sum > 0 else 0

        # Sell side: uniform allocation
        order_value_sell = deployable_sell / self.levels if self.levels > 0 else 0

        buy_levels = []
        sell_levels = []

        for i in range(1, self.levels + 1):
            # Buy levels: geometric spacing stepping DOWN from mid
            buy_price = bb.mid - (buy_spacing * (1 + alpha) ** i)
            buy_value = base_buy_value * (1 + beta) ** i
            buy_qty = buy_value / buy_price if buy_price > 0 else 0
            buy_price, buy_qty = self._validate_order(buy_price, buy_qty)

            # Sell levels: uniform spacing stepping UP from mid
            sell_price = bb.mid + (sell_spacing * i)
            sell_qty = order_value_sell / sell_price if sell_price > 0 else 0
            sell_price, sell_qty = self._validate_order(sell_price, sell_qty)

            if buy_price and buy_qty:
                buy_levels.append({"price": buy_price, "quantity": buy_qty, "level": i})
            if sell_price and sell_qty:
                sell_levels.append({"price": sell_price, "quantity": sell_qty, "level": i})

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
