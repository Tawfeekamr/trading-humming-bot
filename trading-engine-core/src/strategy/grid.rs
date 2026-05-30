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

    pub fn state(&self) -> GridState {
        self.state
    }

    /// Evaluate grid state based on indicators
    pub fn evaluate_state(
        &mut self,
        price: f64,
        rsi: f64,
        ema_200: f64,
        bb_lower: f64,
        bb_upper: f64,
    ) {
        self.evaluate_state_with_ml(price, rsi, ema_200, bb_lower, bb_upper, 0, 0.0);
    }

    /// Evaluate with ML regime overlay
    pub fn evaluate_state_with_ml(
        &mut self,
        price: f64,
        rsi: f64,
        ema_200: f64,
        bb_lower: f64,
        bb_upper: f64,
        ml_regime: i32,
        ml_confidence: f64,
    ) {
        // ML Danger check — immediate pause
        if ml_regime == 2 && ml_confidence >= 0.55 {
            self.state = GridState::Paused;
            return;
        }

        match self.state {
            GridState::Paused | GridState::Disabled => {
                let price_above_ema = price > ema_200;
                let rsi_neutral = rsi > 30.0 && rsi < 70.0;
                let within_bb = price > bb_lower && price < bb_upper;

                if price_above_ema && rsi_neutral && within_bb {
                    self.state = GridState::Active;
                }
            }
            GridState::Active => {
                let rsi_extreme = rsi < 25.0 || rsi > 80.0;
                let outside_bb = price < bb_lower * 0.98 || price > bb_upper * 1.02;
                let below_ema = price < ema_200 * 0.97;

                if rsi_extreme || outside_bb || below_ema {
                    self.state = GridState::Paused;
                }
            }
        }
    }

    pub fn set_grid_layout(&mut self, layout: GridLayout) {
        self.grid_layout = Some(layout);
    }

    /// Record profit/loss from a fill — used for auto-compound
    pub fn record_pnl(&mut self, pnl: f64) {
        self.total_pnl += pnl;
        self.current_capital += pnl;
        if self.current_capital > self.peak_equity {
            self.peak_equity = self.current_capital;
        }
    }

    pub fn current_capital(&self) -> f64 {
        self.current_capital
    }

    pub fn initial_capital(&self) -> f64 {
        self.initial_capital
    }

    pub fn set_paused(&mut self, paused: bool) {
        if paused {
            self.state = GridState::Paused;
        } else {
            self.state = GridState::Active;
        }
    }

    pub fn peak_equity(&self) -> f64 {
        self.peak_equity
    }

    /// Calculate auto-compound growth ratio
    pub fn growth_ratio(&self) -> f64 {
        self.current_capital / self.initial_capital
    }
}

fn round_down(value: f64, increment: f64) -> f64 {
    if increment <= 0.0 { return value; }
    (value / increment).floor() * increment
}

use crate::strategy::{Strategy, TickContext, StrategyStatus, MarketRegime};
use crate::connector::types::{OrderRequest, Fill, OrderTypeReq, TimeInForceReq};
use async_trait::async_trait;
use anyhow::Result;

#[async_trait]
impl Strategy for GridStrategy {
    fn name(&self) -> &str {
        "grid"
    }

    fn trading_pair(&self) -> &str {
        &self.pair
    }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>> {
        // If not active, return empty orders
        if self.state != GridState::Active {
            return Ok(Vec::new());
        }

        // Get mid price from order book
        let mid_price = match ctx.order_book.mid_price() {
            Some(price) => price,
            None => return Ok(Vec::new()),
        };

        // Calculate or update grid layout
        // Use BB-like estimates from recent bars if available, else simple defaults
        let (bb_lower, bb_upper, atr_estimate) = if ctx.recent_bars.len() >= 20 {
            // Compute simple estimates from bars
            let closes: Vec<f64> = ctx.recent_bars.iter().map(|b| b.close).collect();
            let mean = closes.iter().sum::<f64>() / closes.len() as f64;
            let stddev = {
                let variance = closes.iter().map(|c| (c - mean).powi(2)).sum::<f64>() / closes.len() as f64;
                variance.sqrt()
            };
            let bb_center = mean;
            let bb_low = bb_center - 2.0 * stddev;
            let bb_high = bb_center + 2.0 * stddev;
            // ATR estimate: average of (high - low) over recent bars
            let avg_range: f64 = ctx.recent_bars.iter().map(|b| b.high - b.low).sum::<f64>()
                / ctx.recent_bars.len() as f64;
            (bb_low, bb_high, avg_range)
        } else {
            // Fallback: simple percentages
            let atr = mid_price * 0.01;
            (mid_price * 0.98, mid_price * 0.02, atr)
        };

        let layout = self.calculate_levels(mid_price, atr_estimate, bb_lower, bb_upper);
        self.grid_layout = Some(layout.clone());

        // Generate orders only if we don't already have pending orders
        if !self.orders.is_empty() {
            return Ok(Vec::new());
        }

        let mut orders = Vec::new();

        // Place buy limit orders
        for (i, level) in layout.buy_levels.iter().enumerate() {
            let req = OrderRequest {
                symbol: self.pair.replace("-", ""),
                side: OrderSide::Buy,
                order_type: OrderTypeReq::Limit,
                price: Some(level.price),
                quantity: level.quantity,
                time_in_force: Some(TimeInForceReq::Gtc),
                client_order_id: Some(format!("grid_{}_buy_{}", self.pair, i)),
            };
            orders.push(req);
        }

        // Place sell limit orders
        for (i, level) in layout.sell_levels.iter().enumerate() {
            let req = OrderRequest {
                symbol: self.pair.replace("-", ""),
                side: OrderSide::Sell,
                order_type: OrderTypeReq::Limit,
                price: Some(level.price),
                quantity: level.quantity,
                time_in_force: Some(TimeInForceReq::Gtc),
                client_order_id: Some(format!("grid_{}_sell_{}", self.pair, i)),
            };
            orders.push(req);
        }

        // Update state based on ML regime
        if let Some(regime) = ctx.regime {
            if regime == MarketRegime::Danger {
                self.state = GridState::Paused;
            }
        }

        Ok(orders)
    }

    async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>> {
        // Calculate rough PnL estimate from fill
        // For buys: negative value (capital outflow)
        // For sells: positive value (capital inflow) - fee
        let pnl = match fill.side {
            OrderSide::Buy => -(fill.price * fill.quantity + fill.fee),
            OrderSide::Sell => fill.price * fill.quantity - fill.fee,
        };

        self.record_pnl(pnl);

        // Grid orders are managed separately, so no new orders here
        Ok(Vec::new())
    }

    async fn on_start(&mut self) -> Result<Vec<OrderRequest>> {
        // Grid orders are managed separately
        Ok(Vec::new())
    }

    async fn on_stop(&mut self) -> Result<()> {
        // Clean up any state if needed
        Ok(())
    }

    fn status(&self) -> StrategyStatus {
        let state_str = match self.state {
            GridState::Active => "Active",
            GridState::Paused => "Paused",
            GridState::Disabled => "Disabled",
        };

        StrategyStatus {
            name: self.name().to_string(),
            pair: self.pair.clone(),
            state: state_str.to_string(),
            pnl: self.total_pnl,
            open_orders: self.orders.len(),
            details: format!(
                "Capital: ${:.2} / ${:.2} (Growth: {:.2}%)",
                self.current_capital,
                self.initial_capital,
                (self.growth_ratio() - 1.0) * 100.0
            ),
        }
    }

    fn set_paused(&mut self, paused: bool) {
        self.set_paused(paused);
    }

    fn current_capital(&self) -> f64 {
        self.current_capital()
    }

    fn initial_capital(&self) -> f64 {
        self.initial_capital()
    }
}
