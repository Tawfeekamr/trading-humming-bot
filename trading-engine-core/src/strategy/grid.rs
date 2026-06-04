use std::collections::HashMap;
use crate::config::GridConfig;
use crate::models::order::OrderSide;
use crate::indicators::{SupportResistance, LevelKind, Vwap, VolumeSma};

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
    // Indicator modules
    sr: SupportResistance,
    vwap: Vwap,
    volume_sma: VolumeSma,
    last_bar_count: usize,
    // Last evaluated indicator values for diagnostics
    diag_price: f64,
    diag_rsi: f64,
    diag_ema200: f64,
    diag_bb_lower: f64,
    diag_bb_upper: f64,
    diag_bars_count: usize,
    diag_vwap: f64,
    diag_volume_ratio: f64,
    diag_near_support: bool,
    diag_near_resistance: bool,
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
            sr: SupportResistance::new(50, 0.01),
            vwap: Vwap::new(390),
            volume_sma: VolumeSma::new(20),
            last_bar_count: 0,
            diag_price: 0.0,
            diag_rsi: 0.0,
            diag_ema200: 0.0,
            diag_bb_lower: 0.0,
            diag_bb_upper: 0.0,
            diag_bars_count: 0,
            diag_vwap: 0.0,
            diag_volume_ratio: 0.0,
            diag_near_support: false,
            diag_near_resistance: false,
        }
    }

    /// Calculate grid levels based on BB center, ATR, and BB bounds.
    /// Snap buy levels away from resistance and sell levels away from support.
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

        let sr_levels = self.sr.get_levels();

        // Generate buy levels with geometric scaling
        let mut buy_levels = Vec::new();
        let base_buy_value = available * 0.4 / self.config.levels as f64;

        for i in 0..self.config.levels {
            let mut price = round_down(bb_center - buy_spacing * (i + 1) as f64, self.tick_size);
            if price <= 0.0 { continue; }

            // Snap buy level away from resistance (shift to nearest support below)
            for sr in sr_levels.iter().filter(|l| l.kind == LevelKind::Resistance) {
                let distance_pct = (sr.price - price).abs() / price;
                if distance_pct < 0.005 {
                    if let Some(nearest_support) = sr_levels.iter()
                        .filter(|l| l.kind == LevelKind::Support && l.price < price)
                        .max_by(|a, b| a.price.partial_cmp(&b.price).unwrap())
                    {
                        price = round_down(nearest_support.price, self.tick_size);
                    }
                }
            }

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
            let mut price = round_down(bb_center + sell_spacing * (i + 1) as f64, self.tick_size);

            // Snap sell level away from support (shift to nearest resistance above)
            for sr in sr_levels.iter().filter(|l| l.kind == LevelKind::Support) {
                let distance_pct = (sr.price - price).abs() / price;
                if distance_pct < 0.005 {
                    if let Some(nearest_resistance) = sr_levels.iter()
                        .filter(|l| l.kind == LevelKind::Resistance && l.price > price)
                        .min_by(|a, b| a.price.partial_cmp(&b.price).unwrap())
                    {
                        price = round_down(nearest_resistance.price, self.tick_size);
                    }
                }
            }

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
        // Store diagnostics
        self.diag_price = price;
        self.diag_rsi = rsi;
        self.diag_ema200 = ema_200;
        self.diag_bb_lower = bb_lower;
        self.diag_bb_upper = bb_upper;

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
                let above_vwap = if self.vwap.is_initialized() { price > self.vwap.value() } else { true };
                let volume_ok = if self.volume_sma.is_initialized() { self.volume_sma.volume_ratio() > 0.8 } else { true };

                let score = price_above_ema as u8 + rsi_neutral as u8 + within_bb as u8
                    + above_vwap as u8 + volume_ok as u8;
                if score >= 3 {
                    self.state = GridState::Active;
                }
            }
            GridState::Active => {
                let rsi_extreme = rsi < 25.0 || rsi > 80.0;
                let outside_bb = price < bb_lower * 0.98 || price > bb_upper * 1.02;
                let below_ema = price < ema_200 * 0.97;
                // High-volume dump below VWAP — potential capitulation
                let volume_dump = self.vwap.is_initialized()
                    && price < self.vwap.value() * 0.99
                    && self.volume_sma.is_initialized()
                    && self.volume_sma.volume_ratio() > 2.0;

                if rsi_extreme || outside_bb || below_ema || volume_dump {
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
        // Track bar availability for diagnostics (before any early return)
        self.diag_bars_count = ctx.recent_bars.len();

        // Feed new bars to indicator modules (avoid double-counting)
        let bars_to_process = if ctx.recent_bars.len() > self.last_bar_count {
            &ctx.recent_bars[self.last_bar_count..]
        } else if ctx.recent_bars.len() < self.last_bar_count {
            &ctx.recent_bars[..] // buffer was reset
        } else {
            &[][..] // no new bars
        };
        for bar in bars_to_process {
            self.sr.update_bar(bar.open, bar.high, bar.low, bar.close, bar.timestamp);
            self.vwap.update_bar(bar.high, bar.low, bar.close, bar.volume);
            self.volume_sma.update(bar.volume);
        }
        self.last_bar_count = ctx.recent_bars.len();

        // Get mid price from order book
        let mid_price = match ctx.order_book.mid_price() {
            Some(price) => price,
            None => return Ok(Vec::new()),
        };

        // Store VWAP and volume diagnostics
        self.diag_vwap = self.vwap.value();
        self.diag_volume_ratio = self.volume_sma.volume_ratio();
        self.diag_near_support = self.sr.near_support(mid_price);
        self.diag_near_resistance = self.sr.near_resistance(mid_price);
        if ctx.recent_bars.len() >= 20 {
            let closes: Vec<f64> = ctx.recent_bars.iter().map(|b| b.close).collect();
            let mean = closes.iter().sum::<f64>() / closes.len() as f64;
            let stddev = {
                let variance = closes.iter().map(|c| (c - mean).powi(2)).sum::<f64>() / closes.len() as f64;
                variance.sqrt()
            };
            let bb_lower = mean - 2.0 * stddev;
            let bb_upper = mean + 2.0 * stddev;

            // Simple RSI estimate from recent bars
            let mut gains = 0.0;
            let mut losses = 0.0;
            for i in 1..closes.len() {
                let diff = closes[i] - closes[i - 1];
                if diff > 0.0 { gains += diff; } else { losses += diff.abs(); }
            }
            let avg_gain = gains / (closes.len() - 1) as f64;
            let avg_loss = losses / (closes.len() - 1) as f64;
            let rsi = if avg_loss == 0.0 { 100.0 } else {
                100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
            };

            // Simple EMA-200 estimate (use mean as proxy until we have 200 bars)
            let ema_200 = mean;

            // Get ML regime from context or default to 0 (Ranging)
            let (ml_regime, ml_confidence) = match ctx.regime {
                Some(MarketRegime::Danger) => (2, 0.8),
                Some(MarketRegime::Trending) => (1, 0.6),
                _ => (0, 0.0),
            };

            self.evaluate_state_with_ml(mid_price, rsi, ema_200, bb_lower, bb_upper, ml_regime, ml_confidence);
        }

        // If not active after evaluation, return empty orders
        if self.state != GridState::Active {
            return Ok(Vec::new());
        }

        // Calculate grid layout using indicator estimates
        let (bb_lower, bb_upper, atr_estimate) = if ctx.recent_bars.len() >= 20 {
            let closes: Vec<f64> = ctx.recent_bars.iter().map(|b| b.close).collect();
            let mean = closes.iter().sum::<f64>() / closes.len() as f64;
            let stddev = {
                let variance = closes.iter().map(|c| (c - mean).powi(2)).sum::<f64>() / closes.len() as f64;
                variance.sqrt()
            };
            let avg_range: f64 = ctx.recent_bars.iter().map(|b| b.high - b.low).sum::<f64>()
                / ctx.recent_bars.len() as f64;
            (mean - 2.0 * stddev, mean + 2.0 * stddev, avg_range)
        } else {
            // Fallback: simple percentages
            (mid_price * 0.98, mid_price * 1.02, mid_price * 0.01)
        };

        let layout = self.calculate_levels(mid_price, atr_estimate, bb_lower, bb_upper);
        self.grid_layout = Some(layout.clone());

        // Generate orders only if we don't already have pending grid orders
        let pending_count = self.orders.len();
        if pending_count > 0 {
            return Ok(Vec::new());
        }

        let mut orders = Vec::new();

        // Place buy limit orders and track them
        for (i, level) in layout.buy_levels.iter().enumerate() {
            let id = format!("grid_{}_buy_{}", self.pair, i);
            let req = OrderRequest {
                symbol: self.pair.replace("-", ""),
                side: OrderSide::Buy,
                order_type: OrderTypeReq::Limit,
                price: Some(level.price),
                quantity: level.quantity,
                time_in_force: Some(TimeInForceReq::Gtc),
                client_order_id: Some(id.clone()),
            };
            self.orders.insert(id.clone(), GridOrder {
                order_id: id,
                level_index: i,
                side: OrderSide::Buy,
                price: level.price,
                quantity: level.quantity,
            });
            orders.push(req);
        }

        // Place sell limit orders and track them
        for (i, level) in layout.sell_levels.iter().enumerate() {
            let id = format!("grid_{}_sell_{}", self.pair, i);
            let req = OrderRequest {
                symbol: self.pair.replace("-", ""),
                side: OrderSide::Sell,
                order_type: OrderTypeReq::Limit,
                price: Some(level.price),
                quantity: level.quantity,
                time_in_force: Some(TimeInForceReq::Gtc),
                client_order_id: Some(id.clone()),
            };
            self.orders.insert(id.clone(), GridOrder {
                order_id: id,
                level_index: i,
                side: OrderSide::Sell,
                price: level.price,
                quantity: level.quantity,
            });
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
        // Remove the filled order from tracking
        self.orders.retain(|_, o| o.order_id != fill.order_id);

        // Calculate rough PnL estimate from fill
        let pnl = match fill.side {
            OrderSide::Buy => -(fill.price * fill.quantity + fill.fee),
            OrderSide::Sell => fill.price * fill.quantity - fill.fee,
        };

        self.record_pnl(pnl);
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

        let details = if self.state == GridState::Paused && self.diag_bars_count > 0 {
            let price_ok = self.diag_price > self.diag_ema200;
            let rsi_ok = self.diag_rsi > 30.0 && self.diag_rsi < 70.0;
            let bb_ok = self.diag_price > self.diag_bb_lower && self.diag_price < self.diag_bb_upper;
            let vwap_ok = self.diag_vwap > 0.0 && self.diag_price > self.diag_vwap;
            let vol_ok = self.diag_volume_ratio > 0.8;
            let score = price_ok as u8 + rsi_ok as u8 + bb_ok as u8 + vwap_ok as u8 + vol_ok as u8;
            format!(
                "Capital: ${:.2} | Score: {}/5 | Price: ${:.2} | RSI: {:.1} | EMA: ${:.2} | BB: [{:.2}, {:.2}] | VWAP: ${:.2} | Vol: {:.2}x | S/R: {}{} | {}{}{}{}{}",
                self.current_capital,
                score,
                self.diag_price,
                self.diag_rsi,
                self.diag_ema200,
                self.diag_bb_lower,
                self.diag_bb_upper,
                self.diag_vwap,
                self.diag_volume_ratio,
                if self.diag_near_support { "S" } else { "-" },
                if self.diag_near_resistance { "R" } else { "-" },
                if price_ok { "✅" } else { "❌" },
                if rsi_ok { "✅" } else { "❌" },
                if bb_ok { "✅" } else { "❌" },
                if vwap_ok { "✅" } else { "❌" },
                if vol_ok { "✅" } else { "❌" },
            )
        } else if self.state == GridState::Paused && self.diag_bars_count == 0 {
            format!(
                "Capital: ${:.2} | ⏳ Warming up (no bars yet)",
                self.current_capital,
            )
        } else if self.state == GridState::Active {
            format!(
                "Capital: ${:.2} / ${:.2} (Growth: {:.2}%)",
                self.current_capital,
                self.initial_capital,
                (self.growth_ratio() - 1.0) * 100.0
            )
        } else {
            format!(
                "Capital: ${:.2} / ${:.2} (Growth: {:.2}%)",
                self.current_capital,
                self.initial_capital,
                (self.growth_ratio() - 1.0) * 100.0
            )
        };

        StrategyStatus {
            name: self.name().to_string(),
            pair: self.pair.clone(),
            state: state_str.to_string(),
            pnl: self.total_pnl,
            open_orders: self.orders.len().max(
                self.grid_layout.as_ref().map(|l| l.buy_levels.len() + l.sell_levels.len()).unwrap_or(0)
            ),
            details,
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
