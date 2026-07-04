//! Per-engine portfolio: SIGNED-inventory accounting (long + short, partial
//! closes, flips), cash, realized PnL, trade journal.
//!
//! `inventory_qty` is signed: `+` long, `−` short. `avg_price` is the
//! sign-agnostic entry average. `apply_fill` handles all four cases uniformly:
//! Buy opens/extends long OR closes/flips short; Sell opens/extends short OR
//! closes/flips long. `cash -= delta * price` works for every case (Buy pays,
//! Sell receives — open or close, long or short). Fees are booked to `cash`
//! (already deducted by the exchange); `realized` accumulates the GROSS PnL,
//! while `Trade.pnl` is the NET PnL (fee subtracted).
use crate::connector::types::Fill;
use crate::models::order::OrderSide;

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
    pub inventory_qty: f64, // signed: + long, − short
    pub avg_price: f64,     // entry average (sign-agnostic)
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
            avg_price: 0.0,
            realized: 0.0,
            trades: Vec::new(),
            budget,
        }
    }

    pub fn apply_fill(&mut self, f: &Fill) {
        // Fee is booked to cash (the exchange already took it from the wallet).
        self.cash -= f.fee;
        let delta = match f.side {
            OrderSide::Buy => f.quantity,
            OrderSide::Sell => -f.quantity,
        };
        let prev = self.inventory_qty;
        let new_qty = prev + delta;

        // Opening/extending iff the FILL direction matches the existing position
        // direction. (NOT new_qty.signum()==prev.signum(): a partial close leaves
        // new_qty with the same sign as prev but must still realize.)
        if prev == 0.0 || delta.signum() == prev.signum() {
            // opening or extending same direction → weighted avg, no realized
            self.avg_price = if prev == 0.0 {
                f.price
            } else {
                (self.avg_price * prev.abs() + f.quantity * f.price) / new_qty.abs()
            };
        } else {
            // reducing / closing / flipping
            let close = f.quantity.min(prev.abs());
            let dir = prev.signum(); // +1 long closed-by-Sell, −1 short closed-by-Buy
            let gross = dir * (f.price - self.avg_price) * close;
            self.realized += dir * (f.price - self.avg_price) * close;
            let entry_side = if dir > 0.0 { OrderSide::Buy } else { OrderSide::Sell };
            if close > 0.0 {
                self.trades.push(Trade {
                    side: entry_side,
                    qty: close,
                    entry_price: self.avg_price,
                    exit_price: f.price,
                    pnl: gross - f.fee,
                    ts: f.timestamp,
                });
            }
            // Post-close avg: flat → 0; partial close same direction → unchanged;
            // flip (closed all + opened opposite) → leftover enters at f.price.
            self.avg_price = if new_qty == 0.0 {
                0.0
            } else if new_qty.signum() == prev.signum() {
                self.avg_price
            } else {
                f.price
            };
        }
        self.inventory_qty = new_qty;
        self.cash -= delta * f.price; // Buy pays, Sell receives — uniform across open/close, long/short
    }

    pub fn equity(&self, mark: f64) -> f64 {
        self.cash + self.inventory_qty * mark
    }

    pub fn mtm(&self, mark: f64) -> f64 {
        self.equity(mark) - self.init_cash
    }

    pub fn deployed(&self, mark: f64) -> f64 {
        self.inventory_qty.abs() * mark
    }
}
