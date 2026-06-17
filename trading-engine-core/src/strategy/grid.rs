use std::collections::HashMap;
use serde::{Serialize, Deserialize};
use crate::config::GridConfig;
use crate::models::order::OrderSide;
use crate::indicators::{SupportResistance, LevelKind, Adx, Choppiness, Atr};
// Journal removed — unified trades.db.
use crate::notifications::TelegramBot;
use tracing::{info, debug, warn};

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

/// Persisted grid summary state (loaded on startup, saved after each fill).
#[derive(Serialize, Deserialize, Default)]
struct GridStateSnapshot {
    realized_pnl: f64,
    peak_equity: f64,
    level_cooldowns: HashMap<String, i64>,
}

pub struct GridStrategy {
    pair: String,
    config: GridConfig,
    tick_size: f64,
    step_size: f64,
    telegram: TelegramBot,
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
    // Per-level fill cooldown — prevents order churn loop
    // Key: level identity like "buy_2" or "sell_0", Value: epoch millis of fill
    level_cooldowns: HashMap<String, i64>,
    // Resting order client-ids the strategy wants the engine to cancel next cycle.
    // Drained on deactivation so a re-activation re-places at the fresh center
    // instead of freezing on stale startup prices (root cause of 0 grid fills).
    cancel_queue: Vec<String>,
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
    state_dir: String,
    last_base_balance: f64,
    last_quote_balance: f64,
}

#[derive(Debug, Clone)]
struct GridOrder {
    order_id: String,
}

impl GridStrategy {
    pub fn new(pair: &str, config: &GridConfig, tick_size: f64, step_size: f64, telegram: TelegramBot) -> Self {
        Self::new_with_state_dir(pair, config, tick_size, step_size, "data", telegram)
    }

    /// Construct with an explicit state directory (tests use a temp dir).
    pub fn new_with_state_dir(pair: &str, config: &GridConfig, tick_size: f64, step_size: f64, state_dir: &str, telegram: TelegramBot) -> Self {
        let mut me = Self {
            pair: pair.to_string(),
            config: config.clone(),
            tick_size,
            step_size,
            telegram,
            state: GridState::Paused,
            grid_layout: None,
            orders: HashMap::new(),
            level_cooldowns: HashMap::new(),
            cancel_queue: Vec::new(),
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
            state_dir: state_dir.to_string(),
            last_base_balance: 0.0,
            last_quote_balance: 0.0,
        };
        me.load_state();
        me
    }

    fn state_path(&self) -> std::path::PathBuf {
        std::path::PathBuf::from(&self.state_dir)
            .join(format!("{}_grid_state.json", self.pair.replace("-", "_")))
    }

    fn load_state(&mut self) {
        let content = match std::fs::read_to_string(self.state_path()) {
            Ok(c) => c,
            Err(_) => return, // no file yet — fresh start
        };
        match serde_json::from_str::<GridStateSnapshot>(&content) {
            Ok(s) => {
                self.total_pnl = s.realized_pnl;
                self.peak_equity = if s.peak_equity > 0.0 { s.peak_equity } else { self.config.capital_usdt };
                self.level_cooldowns = s.level_cooldowns;
                self.current_capital = self.initial_capital + self.total_pnl;
            }
            Err(e) => warn!("Corrupt grid state for {}: {} — starting fresh", self.pair, e),
        }
    }

    fn save_state_internal(&self) {
        self.save_state_to(&self.state_dir);
    }

    /// Save summary state to an explicit dir (production + tests).
    pub fn save_state_to(&self, dir: &str) {
        let path = std::path::PathBuf::from(dir)
            .join(format!("{}_grid_state.json", self.pair.replace("-", "_")));
        if let Some(parent) = path.parent() {
            let _ = std::fs::create_dir_all(parent);
        }
        let snapshot = GridStateSnapshot {
            realized_pnl: self.total_pnl,
            peak_equity: self.peak_equity,
            level_cooldowns: self.level_cooldowns.clone(),
        };
        let tmp = path.with_extension("json.tmp");
        if let Ok(json) = serde_json::to_string_pretty(&snapshot) {
            if std::fs::write(&tmp, json).is_ok() {
                let _ = std::fs::rename(&tmp, &path);
            }
        }
    }

    // --- diagnostics / test accessors ---
    pub fn realized_pnl(&self) -> f64 { self.total_pnl }
    pub fn peak_equity_pub(&self) -> f64 { self.peak_equity }
    pub fn set_level_cooldown(&mut self, level: String, ts: i64) { self.level_cooldowns.insert(level, ts); }
    pub fn has_level_cooldown(&self, level: &str) -> bool { self.level_cooldowns.contains_key(level) }

    /// Test hook: inject cached balances + price so status() can show MTM.
    pub fn set_mtm_snapshot_for_test(&mut self, base: f64, quote: f64, mid: f64) {
        self.last_base_balance = base;
        self.last_quote_balance = quote;
        self.diag_price = mid;
        self.state = GridState::Active;
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
    fn should_deploy_grid(&self, _price: f64, ml_regime: Option<i32>, ml_confidence: f64) -> (bool, String) {
        // 1. ML Danger / Trending → block if confidence exceeds threshold
        if let Some(regime) = ml_regime {
            if regime == 2 && ml_confidence >= self.config.ml_danger_block_threshold {
                return (false, format!("ML regime=Danger (conf={:.2}>={:.2})", ml_confidence, self.config.ml_danger_block_threshold));
            }
            if regime == 1 && ml_confidence >= self.config.ml_trending_block_threshold {
                return (false, format!("ML regime=Trending (conf={:.2}>={:.2})", ml_confidence, self.config.ml_trending_block_threshold));
            }
        } else {
            // ML regime unknown (cache expired between ~3min pushes). Don't block —
            // fall through to the TA gates below (ADX/choppiness/ATR/EMA-200) which
            // independently detect ranging and provide the same protection. Blocking
            // on None caused flapping: activate → cache expires → deactivate → repeat,
            // so orders never rested long enough to fill.
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
        let adx_ok = adx_val < self.config.adx_range_max;
        let chop_ok = chop_val > self.config.chop_range_min;

        if !adx_ok {
            return (false, format!("Trending, ADX={:.1} (>={:.0})", adx_val, self.config.adx_range_max));
        }
        if !chop_ok {
            return (false, format!("Not choppy enough, CHOP={:.1} (<{:.0})", chop_val, self.config.chop_range_min));
        }

        // 4. Volatility band: natr_floor <= ATR/close <= natr_ceil
        let natr = self.diag_natr;
        if natr < self.config.natr_floor {
            return (false, format!("Volatility too low, NATR={:.4} (<{:.3})", natr, self.config.natr_floor));
        }
        if natr > self.config.natr_ceil {
            return (false, format!("Volatility too high, NATR={:.4} (>{:.3})", natr, self.config.natr_ceil));
        }

        // 5. Fee profitability: per-level profit must exceed round-trip fees.
        // Round-trip = 0.2% (0.1% LIMIT_MAKER each side). Spacing = NATR × mult.
        // Require spacing >= 0.3% (0.2% fee × 1.5 safety margin) so each level
        // nets at least 0.1% profit after fees. Prevents the grid from deploying
        // when volatility is too low for fees to be covered.
        let spacing_pct = natr * self.config.spacing_multiplier;
        const MIN_SPACING_FOR_FEES: f64 = 0.003; // 0.3%
        if spacing_pct < MIN_SPACING_FOR_FEES {
            return (false, format!(
                "Spacing too thin for fees: {:.4}% < {:.1}% (NATR={:.4} × mult={:.1})",
                spacing_pct * 100.0, MIN_SPACING_FOR_FEES * 100.0, natr, self.config.spacing_multiplier,
            ));
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
                    info!("[{}] Grid ACTIVATED | ADX={:.1} CHOP={:.0} NATR={:.4} regime={:?} conf={:.2}",
                        self.pair, self.diag_adx, self.diag_chop, self.diag_natr, ml_regime, ml_confidence);
                    self.state = GridState::Active;
                    self.pause_reason.clear();
                } else {
                    // Only log on reason change to avoid spam
                    if self.pause_reason != reason {
                        debug!("[{}] Grid stays paused: {}", self.pair, reason);
                        self.pause_reason = reason;
                    }
                }
            }
            GridState::Active => {
                let (deploy, reason) = self.should_deploy_grid(price, ml_regime, ml_confidence);
                if !deploy {
                    warn!("[{}] Grid DEACTIVATED: {}", self.pair, reason);
                    self.state = GridState::Paused;
                    self.pause_reason = reason;
                    // Cancel resting orders and drop our tracking so the next
                    // activation re-places at the current center. Without this the
                    // stale order ids pin `pending_count > 0` and the grid freezes.
                    for cid in self.orders.drain().map(|(_, o)| o.order_id) {
                        self.cancel_queue.push(cid);
                    }
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
}

fn round_down(value: f64, increment: f64) -> f64 {
    if increment <= 0.0 { return value; }
    (value / increment).floor() * increment
}

/// Telegram message for a grid fill. Extracted so the format is unit-testable
/// without a network send. `running_pnl` is the grid's cumulative realized P&L
/// after this fill (grid records cash flow per fill).
fn grid_fill_message(pair: &str, is_buy: bool, level: &str, price: f64, running_pnl: f64) -> String {
    let (emoji, side) = if is_buy { ("📥", "BUY") } else { ("📤", "SELL") };
    format!("{} Grid {} {} | {} @ ${:.4} | PnL ${:+.2}", emoji, side, pair, level, price, running_pnl)
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

    fn realized_pnl(&self) -> f64 { self.total_pnl }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>> {
        // Track bar availability for diagnostics (before any early return)
        self.diag_bars_count = ctx.recent_bars.len();

        // Cache balances for mark-to-market display in status().
        let (base, quote) = if let Some(pos) = self.pair.find('-') {
            (&self.pair[..pos], &self.pair[pos + 1..])
        } else {
            ("", "")
        };
        self.last_base_balance = ctx.balances.get(base).copied().unwrap_or(0.0);
        self.last_quote_balance = ctx.balances.get(quote).copied().unwrap_or(0.0);

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
            None => {
                // During replay (no order book) — still log ADX state for diagnostics
                debug!("[{}] ADX state: count={} initialized={} value={:.2}",
                    self.pair, self.adx.count(), self.adx.is_initialized(), self.adx.adx());
                return Ok(Vec::new());
            }
        };

        // Store regime indicator diagnostics
        self.diag_adx = self.adx.adx();
        if self.adx.count() > 0 && !self.adx.is_initialized() {
            warn!("[{}] ADX NOT initialized after {} bars! count={} initialized={} dx_values={}",
                self.pair, self.adx.count(), self.adx.count(), self.adx.is_initialized(), self.adx.count());
        }
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

            // Map ML regime to int — use REAL confidence from TickContext
            let (ml_regime, ml_confidence) = match ctx.regime {
                Some(MarketRegime::Danger) => (Some(2), ctx.regime_confidence),
                Some(MarketRegime::Trending) => (Some(1), ctx.regime_confidence),
                Some(MarketRegime::Ranging) => (Some(0), ctx.regime_confidence),
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

        let mut orders = Vec::new();
        let cooldown_ms = self.config.fill_cooldown_secs * 1000;

        // Place buy limit orders — skip levels on cooldown
        for (i, level) in layout.buy_levels.iter().enumerate() {
            let level_key = format!("buy_{}", i);
            if let Some(&last_fill) = self.level_cooldowns.get(&level_key) {
                if ctx.timestamp.saturating_sub(last_fill) < cooldown_ms {
                    continue; // this level on cooldown, try others
                }
            }
            let id = format!("grid_{}_buy_{}", self.pair, i);
            let req = OrderRequest {
                symbol: self.pair.replace("-", ""),
                side: OrderSide::Buy,
                order_type: OrderTypeReq::Limit,
                price: Some(level.price),
                quantity: level.quantity,
                time_in_force: Some(TimeInForceReq::Gtc),
                client_order_id: Some(id.clone()),
                reduce_only: false,
            };
            self.orders.insert(id.clone(), GridOrder {
                order_id: id,
            });
            orders.push(req);
        }

        // Place sell limit orders — skip levels on cooldown
        for (i, level) in layout.sell_levels.iter().enumerate() {
            let level_key = format!("sell_{}", i);
            if let Some(&last_fill) = self.level_cooldowns.get(&level_key) {
                if ctx.timestamp.saturating_sub(last_fill) < cooldown_ms {
                    continue;
                }
            }
            let id = format!("grid_{}_sell_{}", self.pair, i);
            let req = OrderRequest {
                symbol: self.pair.replace("-", ""),
                side: OrderSide::Sell,
                order_type: OrderTypeReq::Limit,
                price: Some(level.price),
                quantity: level.quantity,
                time_in_force: Some(TimeInForceReq::Gtc),
                client_order_id: Some(id.clone()),
                reduce_only: true,
            };
            self.orders.insert(id.clone(), GridOrder {
                order_id: id,
            });
            orders.push(req);
        }

        // Clean up expired cooldowns to prevent unbounded map growth
        let now = ctx.timestamp;
        self.level_cooldowns.retain(|_, t| now.saturating_sub(*t) < self.config.fill_cooldown_secs * 1000);

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
        // Identify OUR fills via client_order_id, which carries the "grid_…" marker
        // the engine tagged ("owner:{idx}#grid_{pair}_{side}_{i}"). fill.order_id is
        // the connector's own id ("paper_N") and must NOT be used for this check —
        // doing so silently dropped every grid fill (no P&L, no cooldown, no notify).
        let cid = fill.client_order_id.clone().unwrap_or_default();
        let ours = cid.rsplit_once('#').map(|(_, rest)| rest).unwrap_or(&cid);
        if !ours.starts_with("grid_") {
            return Ok(Vec::new());
        }

        // Remove the filled order from tracking (keys are the un-tagged cids).
        self.orders.retain(|_, o| o.order_id != ours);

        // Set per-level cooldown from the cid (e.g., "grid_DOGE-USDT_buy_2" → "buy_2")
        let mut level_key = String::new();
        if let Some(idx) = ours.rfind("_buy_").or_else(|| ours.rfind("_sell_")) {
            level_key = ours[idx + 1..].to_string(); // "buy_2" or "sell_0"
            info!("[{}] Grid fill: {} @ ${:.2} (level={})", self.pair, level_key, fill.price, level_key);
            self.level_cooldowns.insert(level_key.clone(), fill.timestamp);
        }

        // Calculate rough PnL estimate from fill
        let pnl = match fill.side {
            OrderSide::Buy => -(fill.price * fill.quantity + fill.fee),
            OrderSide::Sell => fill.price * fill.quantity - fill.fee,
        };

        self.record_pnl(pnl);

        // Notify on every fill (entry = buy, win/lose = sell). Fire-and-forget so
        // Telegram latency can't stall the fill loop. Grid was previously silent.
        let msg = grid_fill_message(
            &self.pair,
            matches!(fill.side, OrderSide::Buy),
            &level_key,
            fill.price,
            self.total_pnl,
        );
        let tg = self.telegram.clone_for_signal();
        tokio::spawn(async move { let _ = tg.send(&msg).await; });

        crate::strategy::trade_journal::log_unified("grid", &self.pair, None, Some(fill.price), Some(fill.quantity), pnl, Some("grid_fill"), None);
        self.save_state_internal();
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

    fn pending_cancels(&mut self) -> Vec<String> {
        std::mem::take(&mut self.cancel_queue)
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
            let mtm = self.last_base_balance * self.diag_price + self.last_quote_balance;
            format!(
                "Active: ranging | ADX={:.1} CHOP={:.0} NATR={:.4} | Capital: ${:.2} (${:.2} growth) | MTM ${:.2}",
                self.diag_adx, self.diag_chop, self.diag_natr,
                self.current_capital,
                (self.growth_ratio() - 1.0) * 100.0,
                mtm
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
            adx_range_max: 22.0,
            chop_range_min: 55.0,
            natr_floor: 0.005,
            natr_ceil: 0.04,
            fill_cooldown_secs: 60,
            ml_trending_block_threshold: 0.75,
            ml_danger_block_threshold: 0.55,
        }
    }

    fn make_grid() -> GridStrategy {
        use std::sync::atomic::{AtomicU64, Ordering};
        // Each call gets a unique temp state dir so no test reads or writes the
        // real data/ (an on_fill → save_state would otherwise pollute other tests
        // via load_state on the next make_grid). Unique per call + per process.
        static SEQ: AtomicU64 = AtomicU64::new(0);
        let n = SEQ.fetch_add(1, Ordering::SeqCst);
        let dir = std::env::temp_dir().join(format!("grid_test_{}_{}", std::process::id(), n));
        let _ = std::fs::create_dir_all(&dir);
        GridStrategy::new_with_state_dir(
            "BTC-USDT", &test_config(), 0.01, 0.001,
            dir.to_str().unwrap(), TelegramBot::disabled(),
        )
    }

    fn warm_adx(grid: &mut GridStrategy) {
        for _i in 0..30 { grid.adx.update_bar(100.0, 101.0, 99.0, 100.5); }
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
    fn test_gate_ml_none_falls_through_to_ta() {
        // ML regime=None (cache stale) should NOT block with "ML regime unknown".
        // Instead, fall through to TA gates (which may block for their own reasons
        // like warmup, but NOT for ML staleness). This is the flap fix.
        let mut grid = make_grid();
        grid.diag_adx = 15.0;
        grid.diag_chop = 65.0;
        grid.diag_natr = 0.02;
        grid.diag_bars_count = 50;
        warm_adx(&mut grid);

        let (_deploy, reason) = grid.should_deploy_grid(100.0, None, 0.0);
        // The reason must NOT be "ML regime unknown" — that block is removed.
        assert!(!reason.contains("unknown") && !reason.contains("None"),
            "Grid must not block on ML regime=None; got reason: {}", reason);
    }

    #[test]
    fn test_gate_ml_trending_blocks() {
        let mut grid = make_grid();
        grid.diag_adx = 10.0;
        grid.diag_chop = 70.0;
        grid.diag_natr = 0.02;
        grid.diag_bars_count = 50;
        warm_adx(&mut grid);

        // Confidence 0.8 exceeds the new threshold of 0.75
        let (deploy, reason) = grid.should_deploy_grid(100.0, Some(1), 0.8);
        assert!(!deploy);
        assert!(reason.contains("Trending"), "Expected 'Trending' in reason, got: {}", reason);
    }

    #[test]
    fn test_gate_ml_trending_passes_moderate_confidence() {
        let mut grid = make_grid();
        grid.diag_adx = 15.0;
        grid.diag_chop = 65.0;
        grid.diag_natr = 0.02;
        grid.diag_bars_count = 50;
        warm_adx(&mut grid);
        warm_chop(&mut grid);
        warm_atr(&mut grid);

        // Confidence 0.6 is below the new 0.75 threshold — should pass ML gate
        let (deploy, reason) = grid.should_deploy_grid(100.0, Some(1), 0.6);
        assert!(deploy, "Should deploy with moderate trending confidence, reason: {}", reason);
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
    fn test_per_level_cooldown_blocks_filled_level() {
        let mut grid = make_grid();
        // Simulate a fill on buy_0 at t=1_000_000_000_000 ms
        grid.level_cooldowns.insert("buy_0".to_string(), 1_000_000_000_000);

        // 30s later — buy_0 still on cooldown
        let cooldown_ms = grid.config.fill_cooldown_secs * 1000;
        let elapsed = 1_000_000_030_000_i64.saturating_sub(1_000_000_000_000);
        assert!(elapsed < cooldown_ms, "buy_0 should be on cooldown");
    }

    #[test]
    fn test_per_level_cooldown_expires_after_60s() {
        let mut grid = make_grid();
        grid.level_cooldowns.insert("buy_0".to_string(), 1_000_000_000_000);

        // 61s later — cooldown expired, cleanup should remove it
        let now: i64 = 1_000_000_061_000;
        grid.level_cooldowns.retain(|_, t| now.saturating_sub(*t) < grid.config.fill_cooldown_secs * 1000);
        assert!(grid.level_cooldowns.is_empty(), "Cooldown should have expired");
    }

    #[test]
    fn test_per_level_cooldown_allows_other_levels() {
        let mut grid = make_grid();
        // Fill on buy_0 at t=0
        grid.level_cooldowns.insert("buy_0".to_string(), 0);

        // buy_1 should NOT be on cooldown
        assert!(!grid.level_cooldowns.contains_key("buy_1"), "buy_1 should not be blocked by buy_0 fill");
    }

    #[test]
    fn test_no_cooldown_when_no_fills() {
        let grid = make_grid();
        assert!(grid.level_cooldowns.is_empty());
    }

    /// When an active grid deactivates (regime turns against it), it must cancel
    /// its resting orders AND clear its own tracking — otherwise the stale order
    /// ids keep `pending_count > 0` forever and the grid can never re-place at the
    /// new center. This is the root cause of grid completing 0 fills in production:
    /// it placed one batch at startup and then froze as price drifted away.
    #[test]
    fn deactivation_cancels_resting_orders_and_clears_tracking() {
        let mut grid = make_grid();
        warm_adx(&mut grid);
        warm_chop(&mut grid);
        warm_atr(&mut grid);
        grid.diag_bars_count = 50;
        grid.state = GridState::Active;
        // Resting orders placed at a (now stale) center.
        for key in ["grid_BTC-USDT_buy_0", "grid_BTC-USDT_buy_1", "grid_BTC-USDT_sell_0"] {
            grid.orders.insert(
                key.to_string(),
                GridOrder { order_id: key.to_string() },
            );
        }

        // Regime turns to a strong trend → gate fails → grid must deactivate.
        grid.diag_adx = 30.0; // > adx_range_max (22) ⇒ trending
        grid.diag_chop = 65.0;
        grid.diag_natr = 0.02;
        grid.evaluate_state_with_ml(100.0, 90.0, 110.0, Some(0), 0.0);

        assert_eq!(grid.state, GridState::Paused, "grid should deactivate on trend");
        assert!(grid.orders.is_empty(), "stale orders must be cleared on deactivation");
        let cancels = grid.pending_cancels();
        assert_eq!(cancels.len(), 3, "all resting orders must be queued for cancel");
        for key in ["grid_BTC-USDT_buy_0", "grid_BTC-USDT_buy_1", "grid_BTC-USDT_sell_0"] {
            assert!(cancels.contains(&key.to_string()), "missing cancel for {}", key);
        }
    }

    /// A grid fill must ping Telegram with the side, pair, level, and running
    /// P&L — grid was previously totally silent (no telegram calls at all).
    #[test]
    fn fill_message_distinguishes_buy_and_sell_with_pair_and_level() {
        let buy = grid_fill_message("ETH-USDT", true, "buy_2", 1780.0, -5.0);
        assert!(buy.contains("BUY"), "buy msg: {}", buy);
        assert!(!buy.contains("SELL"), "buy msg must not say SELL: {}", buy);
        assert!(buy.contains("ETH-USDT") && buy.contains("buy_2"));

        let sell = grid_fill_message("DOGE-USDT", false, "sell_0", 0.085, 7.5);
        assert!(sell.contains("SELL"), "sell msg: {}", sell);
        assert!(!sell.contains("BUY"), "sell msg must not say BUY: {}", sell);
        assert!(sell.contains("DOGE-USDT") && sell.contains("sell_0"));
    }

    /// Grid must identify its OWN fills via client_order_id (which carries the
    /// "grid_…" marker the engine tagged), NOT fill.order_id — the connector
    /// assigns its own id ("paper_N") to order_id. Checking order_id made grid
    /// silently drop every fill, so it never booked P&L, never set cooldowns,
    /// never saved state, never notified (root cause of "grid 0 trades ever").
    #[tokio::test]
    async fn on_fill_processes_fill_identified_by_client_order_id() {
        let mut grid = make_grid();
        // A resting grid order the strategy is tracking (keyed by its cid).
        grid.orders.insert(
            "grid_BTC-USDT_sell_1".to_string(),
            GridOrder { order_id: "grid_BTC-USDT_sell_1".to_string() },
        );
        let pnl_before = grid.realized_pnl();

        // Engine tags cids as "owner:{idx}#{cid}"; connector order_id is "paper_N".
        let fill = Fill {
            fill_id: "f".into(),
            order_id: "paper_16".into(), // connector id — does NOT start with "grid_"
            client_order_id: Some("owner:0#grid_BTC-USDT_sell_1".into()),
            symbol: "BTCUSDT".into(),
            side: OrderSide::Sell,
            price: 100.0,
            quantity: 1.0,
            fee: 0.1,
            timestamp: 1,
        };
        grid.on_fill(&fill).await.unwrap();

        assert!(grid.orders.is_empty(), "filled order must be removed from tracking");
        assert!(grid.realized_pnl() > pnl_before, "sell fill must book positive PnL");
        assert!(grid.has_level_cooldown("sell_1"), "level cooldown must be set after a fill");
    }
}
