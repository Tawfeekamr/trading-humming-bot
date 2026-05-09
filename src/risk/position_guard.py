import logging

logger = logging.getLogger(__name__)


class PositionGuard:
    def __init__(self, max_base_exposure_pct: float = 80.0,
                 min_usdt_reserve: float = 50.0, total_capital: float = 200.0):
        self.max_base_exposure_pct = max_base_exposure_pct
        self.min_usdt_reserve = min_usdt_reserve
        self.total_capital = total_capital

    def base_exposure_pct(self, current_base: float, base_price: float,
                          equity: float = 0.0) -> float:
        base_value = current_base * base_price
        base = equity if equity > 0 else self.total_capital
        return (base_value / base) * 100

    def can_place_order(self, current_base: float, base_price: float,
                        current_usdt: float, order_usdt: float,
                        equity: float = 0.0) -> bool:
        # Reject negative or zero order amounts
        if order_usdt <= 0:
            logger.warning(f"Rejected order with non-positive amount: {order_usdt}")
            return False

        if (current_usdt - order_usdt) < self.min_usdt_reserve:
            return False
        base = equity if equity > 0 else self.total_capital
        new_base_value = (current_base * base_price) + order_usdt
        new_exposure_pct = (new_base_value / base) * 100
        if new_exposure_pct > self.max_base_exposure_pct:
            return False
        return True
