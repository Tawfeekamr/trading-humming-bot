class PositionGuard:
    def __init__(self, max_btc_exposure_pct: float = 80.0,
                 min_usdt_reserve: float = 50.0, total_capital: float = 200.0):
        self.max_btc_exposure_pct = max_btc_exposure_pct
        self.min_usdt_reserve = min_usdt_reserve
        self.total_capital = total_capital

    def btc_exposure_pct(self, current_btc: float, btc_price: float) -> float:
        btc_value = current_btc * btc_price
        return (btc_value / self.total_capital) * 100

    def can_place_order(self, current_btc: float, btc_price: float,
                        current_usdt: float, order_usdt: float) -> bool:
        if (current_usdt - order_usdt) < self.min_usdt_reserve:
            return False
        new_btc_value = (current_btc * btc_price) + order_usdt
        new_exposure_pct = (new_btc_value / self.total_capital) * 100
        if new_exposure_pct > self.max_btc_exposure_pct:
            return False
        return True
