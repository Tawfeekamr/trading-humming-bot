use std::collections::HashMap;
use crate::config::GridConfig;
use crate::models::order::OrderSide;

const MIN_NOTIONAL: f64 = 5.0;
const SIZE_FACTOR: f64 = 0.08;

#[derive(Debug, Clone)]
pub struct GridLevel {
    pub price: f64,
    pub quantity: f64,
    pub side: OrderSide,
}

#[derive(Debug, Clone)]
pub struct GridLayout {
    pub buy_levels: Vec<GridLevel>,
    pub sell_levels: Vec<GridLevel>,
    pub mid_price: f64,
    pub buy_spacing: f64,
    pub sell_spacing: f64,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum GridState {
    Active,
    Paused,
    Disabled,
}

pub struct GridStrategy {
    pair: String,
    config: GridConfig,
    tick_size: f64,
    step_size: f64,
    state: GridState,
    grid_layout: Option<GridLayout>,
    orders: HashMap<String, GridOrder>,
    total_pnl: f64,
    peak_equity: f64,
    initial_capital: f64,
    current_capital: f64,
}

#[derive(Debug, Clone)]
struct GridOrder {
    order_id: String,
    level_index: usize,
    side: OrderSide,
    price: f64,
    quantity: f64,
}

impl GridStrategy {
    pub fn new(pair: &str, config: &GridConfig, tick_size: f64, step_size: f64) -> Self {
        Self {
            pair: pair.to_string(),
            config: GridConfig {
                levels: config.levels,
                capital_usdt: config.capital_usdt,
                min_reserve: config.min_reserve,
                spacing_multiplier: config.spacing_multiplier,
            },
            tick_size,
            step_size,
            state: GridState::Paused,
            grid_layout: None,
            orders: HashMap::new(),
            total_pnl: 0.0,
            peak_equity: config.capital_usdt,
            initial_capital: config.capital_usdt,
            current_capital: config.capital_usdt,
        }
    }

    /// Calculate grid levels based on BB center, ATR, and BB bounds
    /// Direct port of Python GridManager.calculate_grid()
    pub fn calculate_levels(
        &self,
        bb_center: f64,
        atr_value: f64,
        bb_lower: f64,
        bb_upper: f64,
    ) -> GridLayout {
        let available = self.config.capital_usdt - self.config.min_reserve;

        // ATR-based spacing
        let atr_spacing = atr_value * self.config.spacing_multiplier;

        // Constrain spacing to BB bounds
        let max_buy_spacing = (bb_center - bb_lower) / self.config.levels as f64;
        let max_sell_spacing = (bb_upper - bb_center) / self.config.levels as f64;

        let buy_spacing = atr_spacing.min(max_buy_spacing);
        let sell_spacing = (atr_spacing * 0.75).min(max_sell_spacing);

        // Generate buy levels with geometric scaling
        let mut buy_levels = Vec::new();
        let base_buy_value = available * 0.4 / self.config.levels as f64;

        for i in 0..self.config.levels {
            let price = round_down(bb_center - buy_spacing * (i + 1) as f64, self.tick_size);
            if price <= 0.0 { continue; }

            let scaled_value = base_buy_value * (1.0 + SIZE_FACTOR).powi(i as i32);
            let quantity = round_down(scaled_value / price, self.step_size);

            if price * quantity >= MIN_NOTIONAL {
                buy_levels.push(GridLevel {
                    price,
                    quantity,
                    side: OrderSide::Buy,
                });
            }
        }

        // Generate sell levels with uniform allocation
        let mut sell_levels = Vec::new();
        let sell_capital = available * 0.6;
        let base_sell_value = sell_capital / self.config.levels as f64;

        for i in 0..self.config.levels {
            let price = round_down(bb_center + sell_spacing * (i + 1) as f64, self.tick_size);
            let quantity = round_down(base_sell_value / price, self.step_size);

            if price * quantity >= MIN_NOTIONAL {
                sell_levels.push(GridLevel {
                    price,
                    quantity,
                    side: OrderSide::Sell,
                });
            }
        }

        GridLayout {
            buy_levels,
            sell_levels,
            mid_price: bb_center,
            buy_spacing,
            sell_spacing,
        }
    }
}

fn round_down(value: f64, increment: f64) -> f64 {
    if increment <= 0.0 { return value; }
    (value / increment).floor() * increment
}
