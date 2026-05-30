pub struct PositionGuard {
    max_exposure_pct: f64,
    min_usdt_reserve: f64,
    total_capital: f64,
}

impl PositionGuard {
    pub fn new(max_exposure_pct: f64, min_usdt_reserve: f64, total_capital: f64) -> Self {
        Self { max_exposure_pct, min_usdt_reserve, total_capital }
    }

    pub fn can_place_order(
        &self,
        current_base_value: f64,
        base_price: f64,
        current_usdt: f64,
        order_usdt: f64,
        equity: f64,
    ) -> bool {
        let equity = if equity > 0.0 { equity } else { self.total_capital };

        if current_usdt - order_usdt < self.min_usdt_reserve {
            return false;
        }

        let new_base_value = current_base_value + order_usdt / base_price;
        let exposure_pct = (new_base_value * base_price) / equity * 100.0;
        if exposure_pct > self.max_exposure_pct {
            return false;
        }

        true
    }
}
