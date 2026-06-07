use std::collections::HashMap;
use crate::config::GridConfig;
use crate::models::order::OrderSide;
use crate::indicators::{SupportResistance, LevelKind, Adx, Choppiness, Atr};

const MIN_NOTIONAL: f64 = 5.0;
const SIZE_FACTOR: f64 = 0.08;

// ── Grid deploy gate thresholds ──
// Grid bots profit in RANGING markets; these detect range and safe volatility.
// ⚠️ NATR_FLOOR and NATR_CEIL are placeholders — TUNE PER ASSET/TIMEFRAME.
const ADX_RANGE_MAX: f64 = 22.0;     // ADX below this → no strong trend
const CHOP_RANGE_MIN: f64 = 55.0;    // Choppiness above this → ranging confirmed
const NATR_FLOOR: f64 = 0.005;       // Min normalized ATR for grid to capture profit
const NATR_CEIL: f64 = 0.04;         // Max normalized ATR — above this, risk too high
const FILL_COOLDOWN_SECS: i64 = 60;  // Prevent re-placement at same levels after fill

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
    // Regime + volatility indicators
    adx: Adx,
    choppiness: Choppiness,
    atr: Atr,
    sr: SupportResistance,
    last_bar_count: usize,
    pause_reason: String,
    // Fill cooldown — prevents order churn loop
    last_fill_time: Option<i64>,  // epoch millis of most recent fill
    // Diagnostic values
    diag_price: f64,
    diag_rsi: f64,
    diag_bb_lower: f64,
    diag_bb_upper: f64,
    diag_adx: f64,
    diag_chop: f64,
    diag_natr: f64,
    diag_bars_count: usize,
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
            last_fill_time: None,
            total_pnl: 0.0,
            peak_equity: config.capital_usdt,
            initial_capital: config.capital_usdt,
            current_capital: config.capital_usdt,
            adx: Adx::new(14),
            choppiness: Choppiness::new(14),
            atr: Atr::new(14),
            sr: SupportResistance::new(50, 0.01),
            last_bar_count: 0,
            pause_reason: "Warming up".to_string(),
            diag_price: 0.0,
            diag_rsi: 0.0,
            diag_bb_lower: 0.0,
            diag_bb_upper: 0.0,
            diag_adx: 0.0,
            diag_chop: 0.0,
            diag_natr: 0.0,
            diag_bars_count: 0,
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

    /// Regime-based grid deployment gate.
    /// Returns (should_deploy: bool, reason_if_not: String).
    fn should_deploy_grid(&self, price: f64, ml_regime: Option<i32>, ml_confidence: f64) -> (bool, String) {
        // 1. ML Danger / Trending → block regardless
        if let Some(regime) = ml_regime {
            if regime == 2 && ml_confidence >= 0.55 {
                return (false, format!("ML regime=Danger (conf={:.2})", ml_confidence));
            }
            if regime == 1 && ml_confidence >= 0.55 {
                return (false, format!("ML regime=Trending (conf={:.2})", ml_confidence));
            }
        } else {
            // Unknown regime → block (not safe to assume ranging)
            return (false, "ML regime unknown (None)".to_string());
        }

        // 2. Indicator warm-up check
        if !self.adx.is_initialized() {
            let needed = 29; // 2*period + 1 for period=14
            let have = self.diag_bars_count;
            return (false, format!("Warming up (ADX needs {} bars, have {})", needed, have));
        }
        if !self.choppiness.is_initialized() {
            return (false, "Warming up (Choppiness needs 14 bars)".to_string());
        }
        if !self.atr.is_initialized() {
            return (false, "Warming up (ATR needs 14 bars)".to_string());
        }

        // 3. Range condition: ADX low AND Choppiness high
        let adx_val = self.diag_adx;
        let chop_val = self.diag_chop;
        let adx_ok = adx_val < ADX_RANGE_MAX;
        let chop_ok = chop_val > CHOP_RANGE_MIN;

        if !adx_ok {
            return (false, format!("Trending, ADX={:.1} (>={:.0})", adx_val, ADX_RANGE_MAX));
        }
        if !chop_ok {
            return (false, format!("Not choppy enough, CHOP={:.1} (<{:.0})", chop_val, CHOP_RANGE_MIN));
        }

        // 4. Volatility band: NATR_FLOOR <= ATR/close <= NATR_CEIL
        let natr = self.diag_natr;
        if natr < NATR_FLOOR {
            return (false, format!("Volatility too low, NATR={:.4} (<{:.3})", natr, NATR_FLOOR));
        }
        if natr > NATR_CEIL {
            return (false, format!("Volatility too high, NATR={:.4} (>{:.3})", natr, NATR_CEIL));
        }

        // All conditions met
        (true, String::new())
    }

    /// Evaluate grid state based on regime + volatility gate
    pub fn evaluate_state_with_ml(
        &mut self,
        price: f64,
        bb_lower: f64,
        bb_upper: f64,
        ml_regime: Option<i32>,
        ml_confidence: f64,
    ) {
        // Store diagnostics
        self.diag_price = price;
        self.diag_bb_lower = bb_lower;
        self.diag_bb_upper = bb_upper;

        match self.state {
            GridState::Paused | GridState::Disabled => {
                let (deploy, reason) = self.should_deploy_grid(price, ml_regime, ml_confidence);
                if deploy {
                    self.state = GridState::Active;
                    self.pause_reason.clear();
                } else {
                    self.pause_reason = reason;
                }
            }
            GridState::Active => {
                let (deploy, reason) = self.should_deploy_grid(price, ml_regime, ml_confidence);
                if !deploy {
                    self.state = GridState::Paused;
                    self.pause_reason = reason;
                }
            }
        }
    }

    /// Backward-compat wrapper that passes no ML regime (blocks deployment)
    pub fn evaluate_state(
        &mut self,
        price: f64,
        bb_lower: f64,
        bb_upper: f64,
    ) {
        self.evaluate_state_with_ml(price, bb_lower, bb_upper, None, 0.0);
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

    /// Check whether the fill cooldown is still active at the given time
    pub fn is_on_cooldown(&self, now_ms: i64) -> bool {
        match self.last_fill_time {
            Some(last) => now_ms.saturating_sub(last) < FILL_COOLDOWN_SECS * 1000,
            None => false,
        }
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
        let mut prev_close: Option<f64> = None;
        let bars_to_process = if ctx.recent_bars.len() > self.last_bar_count {
            // Set prev_close from the bar before our window
            if self.last_bar_count > 0 && self.last_bar_count <= ctx.recent_bars.len() {
                prev_close = Some(ctx.recent_bars[self.last_bar_count - 1].close);
            }
            &ctx.recent_bars[self.last_bar_count..]
        } else if ctx.recent_bars.len() < self.last_bar_count {
            &ctx.recent_bars[..] // buffer was reset
        } else {
            &[][..] // no new bars
        };

        for bar in bars_to_process {
            self.adx.update_bar(bar.open, bar.high, bar.low, bar.close);
            self.choppiness.update_bar(bar.open, bar.high, bar.low, bar.close, prev_close);
            self.atr.update_bar(bar.open, bar.high, bar.low, bar.close);
            self.sr.update_bar(bar.open, bar.high, bar.low, bar.close, bar.timestamp);
            prev_close = Some(bar.close);
        }
        self.last_bar_count = ctx.recent_bars.len();

        // Get mid price from order book
        let mid_price = match ctx.order_book.mid_price() {
            Some(price) => price,
            None => return Ok(Vec::new()),
        };

        // Store regime indicator diagnostics
        self.diag_adx = self.adx.adx();
        self.diag_chop = self.choppiness.value();
        self.diag_natr = if self.atr.is_initialized() && mid_price > 0.0 {
            self.atr.value() / mid_price // matches Python: atr_14 / close (no ×100)
        } else {
            0.0
        };
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

            // RSI (kept for grid center bias, not gate)
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
            self.diag_rsi = rsi;

            // Map ML regime to int (None = unknown → block deployment)
            let (ml_regime, ml_confidence) = match ctx.regime {
                Some(MarketRegime::Danger) => (Some(2), 0.8),
                Some(MarketRegime::Trending) => (Some(1), 0.6),
                Some(MarketRegime::Ranging) => (Some(0), 0.0),
                None => (None, 0.0),
            };

            self.evaluate_state_with_ml(mid_price, bb_lower, bb_upper, ml_regime, ml_confidence);
        }

        // If not active after evaluation, return empty orders
        if self.state != GridState::Active {
            return Ok(Vec::new());
        }

        // Calculate grid layout using BB bounds and ATR for spacing
        let (bb_lower, bb_upper, atr_estimate) = if ctx.recent_bars.len() >= 20 {
            let closes: Vec<f64> = ctx.recent_bars.iter().map(|b| b.close).collect();
            let mean = closes.iter().sum::<f64>() / closes.len() as f64;
            let stddev = {
                let variance = closes.iter().map(|c| (c - mean).powi(2)).sum::<f64>() / closes.len() as f64;
                variance.sqrt()
            };
            // Use proper ATR if available, else fall back to avg range
            let atr_val = if self.atr.is_initialized() { self.atr.value() } else {
                ctx.recent_bars.iter().map(|b| b.high - b.low).sum::<f64>() / ctx.recent_bars.len() as f64
            };
            (mean - 2.0 * stddev, mean + 2.0 * stddev, atr_val)
        } else {
            (mid_price * 0.98, mid_price * 1.02, mid_price * 0.01)
        };

        // RSI bias: shift grid center toward oversold region (buy lower)
        let center = if self.diag_rsi < 40.0 {
            bb_lower + (bb_upper - bb_lower) * 0.4 // bias lower when oversold
        } else if self.diag_rsi > 60.0 {
            bb_lower + (bb_upper - bb_lower) * 0.6 // bias higher when overbought
        } else {
            (bb_lower + bb_upper) / 2.0
        };

        let layout = self.calculate_levels(center, atr_estimate, bb_lower, bb_upper);
        self.grid_layout = Some(layout.clone());

        // Generate orders only if we don't already have pending grid orders
        let pending_count = self.orders.len();
        if pending_count > 0 {
            return Ok(Vec::new());
        }

        // Fill cooldown: prevent re-placement at same levels after a fill
        // (stops paper-trading churn loop where fill → remove → re-place → fill)
        if let Some(last_fill) = self.last_fill_time {
            let cooldown_ms = FILL_COOLDOWN_SECS * 1000;
            if ctx.timestamp.saturating_sub(last_fill) < cooldown_ms {
                return Ok(Vec::new());
            }
            // Cooldown expired — clear it
            self.last_fill_time = None;
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
                self.pause_reason = "ML regime=Danger override".to_string();
            }
        }

        Ok(orders)
    }

    async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>> {
        // Remove the filled order from tracking
        self.orders.retain(|_, o| o.order_id != fill.order_id);

        // Record fill timestamp for cooldown (prevents churn loop)
        self.last_fill_time = Some(fill.timestamp);

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
            if !self.pause_reason.is_empty() {
                format!(
                    "Paused: {} | ADX={:.1} CHOP={:.0} NATR={:.4} | Price: ${:.2} | Bars: {}",
                    self.pause_reason,
                    self.diag_adx,
                    self.diag_chop,
                    self.diag_natr,
                    self.diag_price,
                    self.diag_bars_count,
                )
            } else {
                format!(
                    "Paused | ADX={:.1} CHOP={:.0} NATR={:.4} | Price: ${:.2} | Bars: {}",
                    self.diag_adx, self.diag_chop, self.diag_natr,
                    self.diag_price, self.diag_bars_count,
                )
            }
        } else if self.state == GridState::Paused && self.diag_bars_count == 0 {
            format!(
                "Capital: ${:.2} | ⏳ Warming up (no bars yet)",
                self.current_capital,
            )
        } else if self.state == GridState::Active {
            format!(
                "Active: ranging | ADX={:.1} CHOP={:.0} NATR={:.4} | Capital: ${:.2} (${:.2} growth)",
                self.diag_adx, self.diag_chop, self.diag_natr,
                self.current_capital,
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::GridConfig;

    fn test_config() -> GridConfig {
        GridConfig {
            levels: 5,
            capital_usdt: 10000.0,
            min_reserve: 500.0,
            spacing_multiplier: 1.5,
        }
    }

    fn make_grid() -> GridStrategy {
        GridStrategy::new("BTC-USDT", &test_config(), 0.01, 0.001)
    }

    fn warm_adx(grid: &mut GridStrategy) {
        for i in 0..30 { grid.adx.update_bar(100.0, 101.0, 99.0, 100.5); }
    }

    fn warm_chop(grid: &mut GridStrategy) {
        let mut prev = None;
        for _ in 0..15 {
            grid.choppiness.update_bar(100.0, 100.5, 99.5, 100.0, prev);
            prev = Some(100.0);
        }
    }

    fn warm_atr(grid: &mut GridStrategy) {
        for _ in 0..15 { grid.atr.update_bar(100.0, 100.5, 99.5, 100.0); }
    }

    #[test]
    fn test_gate_ml_none_blocks() {
        let mut grid = make_grid();
        grid.diag_adx = 15.0;
        grid.diag_chop = 65.0;
        grid.diag_natr = 0.02;
        grid.diag_bars_count = 50;
        warm_adx(&mut grid);

        let (deploy, reason) = grid.should_deploy_grid(100.0, None, 0.0);
        assert!(!deploy);
        assert!(reason.contains("unknown") || reason.contains("None"), "Expected 'unknown' in reason, got: {}", reason);
    }

    #[test]
    fn test_gate_ml_trending_blocks() {
        let mut grid = make_grid();
        grid.diag_adx = 10.0;
        grid.diag_chop = 70.0;
        grid.diag_natr = 0.02;
        grid.diag_bars_count = 50;
        warm_adx(&mut grid);

        let (deploy, reason) = grid.should_deploy_grid(100.0, Some(1), 0.7);
        assert!(!deploy);
        assert!(reason.contains("Trending"), "Expected 'Trending' in reason, got: {}", reason);
    }

    #[test]
    fn test_gate_ml_danger_blocks() {
        let mut grid = make_grid();
        grid.diag_adx = 10.0;
        grid.diag_chop = 70.0;
        grid.diag_natr = 0.02;
        grid.diag_bars_count = 50;
        warm_adx(&mut grid);

        let (deploy, reason) = grid.should_deploy_grid(100.0, Some(2), 0.8);
        assert!(!deploy);
        assert!(reason.contains("Danger"), "Expected 'Danger' in reason, got: {}", reason);
    }

    #[test]
    fn test_gate_strong_trend_stays_paused() {
        let mut grid = make_grid();
        grid.diag_adx = 30.0;
        grid.diag_chop = 35.0;
        grid.diag_natr = 0.02;
        grid.diag_bars_count = 50;
        warm_adx(&mut grid);
        warm_chop(&mut grid);
        warm_atr(&mut grid);

        let (deploy, reason) = grid.should_deploy_grid(100.0, Some(0), 0.0);
        assert!(!deploy);
        assert!(reason.contains("Trending") || reason.contains("ADX=30"),
            "Expected trend block, got: {}", reason);
    }

    #[test]
    fn test_gate_volatility_too_high() {
        let mut grid = make_grid();
        grid.diag_adx = 15.0;
        grid.diag_chop = 65.0;
        grid.diag_natr = 0.06;
        grid.diag_bars_count = 50;
        warm_adx(&mut grid);
        warm_chop(&mut grid);
        warm_atr(&mut grid);

        let (deploy, reason) = grid.should_deploy_grid(100.0, Some(0), 0.0);
        assert!(!deploy);
        assert!(reason.contains("too high"), "Expected vol too high, got: {}", reason);
    }

    #[test]
    fn test_gate_volatility_too_low() {
        let mut grid = make_grid();
        grid.diag_adx = 15.0;
        grid.diag_chop = 65.0;
        grid.diag_natr = 0.001;
        grid.diag_bars_count = 50;
        warm_adx(&mut grid);
        warm_chop(&mut grid);
        warm_atr(&mut grid);

        let (deploy, reason) = grid.should_deploy_grid(100.0, Some(0), 0.0);
        assert!(!deploy);
        assert!(reason.contains("too low"), "Expected vol too low, got: {}", reason);
    }

    #[test]
    fn test_gate_confirmed_range_deploys() {
        let mut grid = make_grid();
        grid.diag_adx = 15.0;
        grid.diag_chop = 65.0;
        grid.diag_natr = 0.015;
        grid.diag_bars_count = 50;
        warm_adx(&mut grid);
        warm_chop(&mut grid);
        warm_atr(&mut grid);

        let (deploy, reason) = grid.should_deploy_grid(100.0, Some(0), 0.0);
        assert!(deploy, "Should deploy in confirmed range, reason: {}", reason);
    }

    #[test]
    fn test_gate_not_warmed_up_stays_paused() {
        let mut grid = make_grid();
        grid.diag_adx = 15.0;
        grid.diag_chop = 65.0;
        grid.diag_natr = 0.015;
        grid.diag_bars_count = 10;
        // Don't warm up ADX — it won't be initialized

        let (deploy, reason) = grid.should_deploy_grid(100.0, Some(0), 0.0);
        assert!(!deploy);
        assert!(reason.contains("Warming up"), "Expected warmup block, got: {}", reason);
    }

    #[test]
    fn test_cooldown_blocks_after_fill() {
        let mut grid = make_grid();
        // Simulate a fill at t=1_000_000_000_000 ms
        grid.last_fill_time = Some(1_000_000_000_000);

        // 30 s later — still on cooldown
        assert!(grid.is_on_cooldown(1_000_000_030_000));
    }

    #[test]
    fn test_cooldown_expires_after_60s() {
        let mut grid = make_grid();
        grid.last_fill_time = Some(1_000_000_000_000);

        // 61 s later — cooldown expired
        assert!(!grid.is_on_cooldown(1_000_000_061_000));
    }

    #[test]
    fn test_no_cooldown_when_no_fills() {
        let grid = make_grid();
        assert!(!grid.is_on_cooldown(0));
    }
}
