//! Per-engine portfolio: inventory accounting (buy accumulates, sell realizes
//! vs average cost), cash, realized PnL, trade journal.
//!
//! Grid/trend accounting: BUYs accumulate inventory at cost (no realized PnL);
//! SELLs realize `qty * (price - avg_cost)` against the average cost basis.
//! Fees are booked to `cash` (already deducted by the exchange); `realized`
//! accumulates the GROSS PnL, while `Trade.pnl` is the NET PnL (fee subtracted).
use crate::connector::types::Fill;
use crate::models::order::OrderSide;
use tracing::warn;

#[derive(Clone, Debug)]
pub struct Trade {
    pub side: OrderSide,
    pub qty: f64,
    pub entry_price: f64,
    pub exit_price: f64,
    pub pnl: f64,
    pub ts: i64,
}

pub struct Portfolio {
    pub init_cash: f64,
    pub cash: f64,
    pub inventory_qty: f64,
    pub inventory_cost: f64, // total cost basis of current inventory
    pub realized: f64,
    pub trades: Vec<Trade>,
    pub budget: f64,
}

impl Portfolio {
    pub fn new(init_cash: f64, budget: f64) -> Self {
        Self {
            init_cash,
            cash: init_cash,
            inventory_qty: 0.0,
            inventory_cost: 0.0,
            realized: 0.0,
            trades: Vec::new(),
            budget,
        }
    }

    fn avg_cost(&self) -> f64 {
        if self.inventory_qty.abs() < 1e-12 {
            0.0
        } else {
            self.inventory_cost / self.inventory_qty
        }
    }

    pub fn apply_fill(&mut self, f: &Fill) {
        // Fee is booked to cash (the exchange already took it from the wallet).
        self.cash -= f.fee;
        match f.side {
            OrderSide::Buy => {
                self.inventory_qty += f.quantity;
                self.inventory_cost += f.quantity * f.price;
                self.cash -= f.quantity * f.price;
            }
            OrderSide::Sell => {
                let avg = self.avg_cost();
                // Defensive: never realize more than the open long inventory.
                let qty = f.quantity.min(self.inventory_qty.max(0.0));
                let gross = qty * (f.price - avg);
                let pnl = gross - f.fee; // net (fee already debited from cash too)
                self.realized += gross;
                self.inventory_qty -= qty;
                self.inventory_cost -= qty * avg;
                if qty < f.quantity {
                    warn!(
                        requested = f.quantity,
                        filled_clamped = qty,
                        inventory_before = self.inventory_qty + qty,
                        "SELL over-sell clamped to open long inventory; cash credited for clamped qty only"
                    );
                }
                self.cash += qty * f.price;
                if qty > 0.0 {
                    self.trades.push(Trade {
                        side: f.side,
                        qty,
                        entry_price: avg,
                        exit_price: f.price,
                        pnl,
                        ts: f.timestamp,
                    });
                }
            }
        }
    }

    pub fn equity(&self, mark: f64) -> f64 {
        self.cash + self.inventory_qty * mark
    }

    pub fn mtm(&self, mark: f64) -> f64 {
        self.equity(mark) - self.init_cash
    }

    pub fn deployed(&self, mark: f64) -> f64 {
        self.inventory_qty.max(0.0) * mark
    }
}
