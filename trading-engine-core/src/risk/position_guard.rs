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

        // max_exposure_pct <= 0 means "not configured": skip the exposure cap
        // rather than refusing every buy.
        if self.max_exposure_pct > 0.0 && base_price > 0.0 {
            // current_base_value is a base-asset QUANTITY (held + resting buys).
            let new_base_qty = current_base_value + order_usdt / base_price;
            let exposure_pct = (new_base_qty * base_price) / equity * 100.0;
            if exposure_pct > self.max_exposure_pct {
                return false;
            }
        }

        true
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn blocks_a_buy_that_would_put_most_of_equity_in_one_coin() {
        let g = PositionGuard::new(80.0, 0.0, 100_000.0);
        // 0 XRP held, $100k cash, equity $100k: a $20k buy is fine…
        assert!(g.can_place_order(0.0, 1.0, 100_000.0, 20_000.0, 100_000.0));
        // …but holding 80k XRP already, another $5k would make 85%.
        assert!(!g.can_place_order(80_000.0, 1.0, 20_000.0, 5_000.0, 100_000.0));
    }

    #[test]
    fn refuses_a_buy_larger_than_cash_minus_reserve() {
        let g = PositionGuard::new(80.0, 0.0, 100_000.0);
        assert!(!g.can_place_order(0.0, 1.0, 1_000.0, 1_500.0, 100_000.0));
    }

    #[test]
    fn unset_exposure_cap_does_not_block_buys() {
        let g = PositionGuard::new(0.0, 0.0, 100_000.0);
        assert!(g.can_place_order(90_000.0, 1.0, 10_000.0, 5_000.0, 100_000.0));
    }
}
