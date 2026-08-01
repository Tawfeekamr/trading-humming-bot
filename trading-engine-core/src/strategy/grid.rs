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
    granted_capital: f64,  // Phase B2: capped by CapitalManager
    // Open inventory accumulated from BUY fills (avg-cost basis). In-memory only:
    // paper balances reset on restart, so inventory must too. Only SELLS realize
    // P&L (against this basis); buys are not losses.
    inventory_qty: f64,
    inventory_cost: f64,
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
    /// Regime/routing snapshot captured with the first inventory entry.
    entry_attribution: Option<crate::strategy::trade_journal::RegimeAttribution>,
    /// C2: Market sell emitted by `force_flat()` to flatten inventory. Since
    /// `force_flat` returns `()`, the order is stashed here and drained at the
    /// top of the next `on_tick` (which runs immediately after, in the same
    /// engine cycle). `None` when no flat-close is pending.
    force_flat_close: Option<OrderRequest>,
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
            granted_capital: config.capital_usdt,
            inventory_qty: 0.0,
            inventory_cost: 0.0,
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
            entry_attribution: None,
            force_flat_close: None,
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

    /// True when accumulated inventory notional (qty × price) has reached the
    /// `max_inventory_pct` cap of granted capital — at that point grid stops
    /// placing new BUYS (sells still place, to unwind the bag).
    pub fn buys_capped(&self, price: f64) -> bool {
        self.inventory_qty * price >= self.granted_capital * (self.config.max_inventory_pct / 100.0)
    }

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
        let available = self.granted_capital - self.config.min_reserve;

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

/// Telegram message for a grid BUY. Shows accumulated inventory + cost basis —
/// NOT "PnL", because a buy is an asset swap (cash → inventory), not a loss.
fn grid_buy_message(pair: &str, level: &str, price: f64, inv_qty: f64, inv_cost: f64) -> String {
    let base = pair.split('-').next().unwrap_or(pair);
    format!("📥 Grid BUY {} | {} @ ${:.4} | holding {:.4} {} (${:.2} basis)",
        pair, level, price, inv_qty, base, inv_cost)
}

/// Telegram message for a grid SELL — the only event that realizes P&L.
/// `realized` is this fill's profit vs avg cost; `total` is cumulative realized.
fn grid_sell_message(pair: &str, level: &str, price: f64, realized: f64, total: f64) -> String {
    format!("📤 Grid SELL {} | {} @ ${:.4} | realized ${:+.2} (total ${:+.2})",
        pair, level, price, realized, total)
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
    fn deployed_capital(&self) -> f64 { self.inventory_cost }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>> {
        // Track bar availability for diagnostics (before any early return)
        self.diag_bars_count = ctx.recent_bars.len();

        // C2: drain a pending force_flat Market sell BEFORE the Paused-state early
        // return — otherwise the inventory-close order would never reach the
        // engine. force_flat() stashed this after flipping state to Paused.
        if let Some(close_req) = self.force_flat_close.take() {
            return Ok(vec![close_req]);
        }

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

        // Phase B2: claim capital for the grid layout from the shared pool.
        if let Some(cm) = &ctx.capital {
            self.granted_capital = cm.request_capital("grid", self.config.capital_usdt);
        }
        let layout = self.calculate_levels(center, atr_estimate, bb_lower, bb_upper);
        self.grid_layout = Some(layout.clone());

        // Generate orders only if we don't already have pending grid orders
        let pending_count = self.orders.len();
        if pending_count > 0 {
            return Ok(Vec::new());
        }

        let mut orders = Vec::new();
        let cooldown_ms = self.config.fill_cooldown_secs * 1000;

        // Place buy limit orders — skip levels on cooldown.
        // Inventory cap: once accumulated inventory (qty × center) reaches
        // max_inventory_pct of granted capital, place NO new buys (sells still
        // place below, to unwind). Stops a downtrend from building an oversized bag.
        let buys_capped = self.buys_capped(center);
        for (i, level) in layout.buy_levels.iter().enumerate() {
            if buys_capped { break; }
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
            if self.entry_attribution.is_none() {
                self.entry_attribution = Some(crate::strategy::trade_journal::RegimeAttribution {
                    regime_at_entry: ctx.regime.map(|regime| match regime {
                        MarketRegime::Ranging => "ranging",
                        MarketRegime::Trending => "trending",
                        MarketRegime::Danger => "danger",
                    }.to_string()),
                    regime_confidence: ctx.regime.map(|_| ctx.regime_confidence),
                    ml_gate_decision: Some(if ctx.regime.is_some() { "allowed" } else { "ta_fallback" }.to_string()),
                    decision_timestamp: Some(ctx.timestamp),
                    ..Default::default()
                });
            }
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
            self.level_cooldowns.insert(level_key.clone(), fill.timestamp);
        }

        let tg = self.telegram.clone_for_signal();
        match fill.side {
            OrderSide::Buy => {
                // A buy accumulates inventory at cost — it is NOT a realized loss.
                self.inventory_qty += fill.quantity;
                self.inventory_cost += fill.price * fill.quantity + fill.fee;
                info!("[{}] Grid BUY {} @ ${:.4} (level={}) → holding {:.4} (${:.2} basis)",
                    self.pair, fill.quantity, fill.price, level_key, self.inventory_qty, self.inventory_cost);
                let msg = grid_buy_message(&self.pair, &level_key, fill.price, self.inventory_qty, self.inventory_cost);
                tokio::spawn(async move { let _ = tg.send(&msg).await; });
                // No trade-journal row: a buy opens inventory, it doesn't realize P&L.
            }
            OrderSide::Sell => {
                // Realize profit against average cost. reduce_only guarantees we
                // hold inventory; if not (e.g. restart wiped it), realize $0.
                let mut realized = 0.0;
                let mut avg_cost = fill.price;
                if self.inventory_qty > 1e-12 {
                    avg_cost = self.inventory_cost / self.inventory_qty;
                    let cost_sold = (avg_cost * fill.quantity).min(self.inventory_cost);
                    realized = (fill.price * fill.quantity - fill.fee) - cost_sold;
                    self.inventory_qty = (self.inventory_qty - fill.quantity).max(0.0);
                    self.inventory_cost = (self.inventory_cost - cost_sold).max(0.0);
                }
                self.record_pnl(realized);
                info!("[{}] Grid SELL {} @ ${:.4} (level={}) → realized ${:+.2}",
                    self.pair, fill.quantity, fill.price, level_key, realized);
                let msg = grid_sell_message(&self.pair, &level_key, fill.price, realized, self.total_pnl);
                tokio::spawn(async move { let _ = tg.send(&msg).await; });
                // Only the SELL (a realized round-trip) is journaled — like other engines.
                let mut grid_ctx = crate::strategy::trade_journal::TradeContext {
                    entry_reason: Some(level_key.clone()),
                    context_json: Some(serde_json::json!({"entry_reason": level_key}).to_string()),
                    ..Default::default()
                };
                if let Some(attribution) = &self.entry_attribution {
                    grid_ctx.context_json = crate::strategy::trade_journal::merge_regime_context_json(
                        grid_ctx.context_json.as_deref(),
                        attribution,
                    );
                }
                crate::strategy::trade_journal::log_unified(
                    "grid", &self.pair, Some("BUY"), Some(avg_cost), Some(fill.price),
                    Some(fill.quantity), realized, Some("grid_sell"), None, &grid_ctx);
                if self.inventory_qty <= 1e-12 {
                    self.entry_attribution = None;
                }
            }
        }
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

    /// C2: close inventory + clear the ladder + suppress new entries.
    /// 1. Queue ALL resting grid orders (buy + sell levels) for engine cancel.
    /// 2. Emit a Market SELL (reduce-only) for the full inventory qty at the
    ///    last seen mid — the on_fill Sell path realizes P&L against avg cost,
    ///    same as a normal grid SELL.
    /// 3. Set state = Paused so on_tick doesn't re-place the ladder while flat.
    /// `diag_price` is the last mid seen in on_tick; using it (not a fresh book
    /// query) keeps force_flat synchronous and side-effect-free.
    fn force_flat(&mut self) {
        // 1. Cancel every resting order (keys are the un-tagged cids the engine
        //    prefixed with "owner:N#").
        let cids: Vec<String> = self.orders.keys().cloned().collect();
        for cid in cids {
            self.cancel_queue.push(cid);
        }
        self.orders.clear();
        // 2. Flatten inventory at market — cid keeps the "grid_<pair>_sell_<n>"
        //    shape so on_fill's guard + level-cooldown extraction both work.
        //    Guard: only stash if no close is already pending. force_flat() is
        //    called every tick while flat=true; without this guard, a slow paper
        //    fill would let the next tick stash a SECOND sell for inventory the
        //    first hasn't cleared yet → duplicate reduce-only sells.
        if self.inventory_qty > 1e-12 && self.diag_price > 0.0 && self.force_flat_close.is_none() {
            let cid = format!("grid_{}_sell_flat", self.pair);
            self.force_flat_close = Some(OrderRequest {
                symbol: self.pair.replace("-", ""),
                side: OrderSide::Sell,
                order_type: OrderTypeReq::Market,
                price: None,
                quantity: self.inventory_qty,
                time_in_force: Some(TimeInForceReq::Gtc),
                client_order_id: Some(cid),
                reduce_only: true,
            });
        }
        // 3. Suppress new ladder placement until the routing layer reactivates.
        self.state = GridState::Paused;
        self.pause_reason = "routing force_flat".into();
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
            max_inventory_pct: 60.0,
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

    #[test]
    fn buys_capped_when_inventory_notional_reaches_pct_of_granted() {
        let mut g = make_grid();
        g.granted_capital = 10_000.0;
        g.config.max_inventory_pct = 60.0; // cap = 6000 notional
        g.inventory_qty = 1_000.0;
        // 1000 @ $6.00 = $6000 → at cap → new buys blocked (sells still place).
        assert!(g.buys_capped(6.0));
        // Below cap → buys allowed.
        g.inventory_qty = 500.0; // $3000 < $6000
        assert!(!g.buys_capped(6.0));
    }

    /// Build a fill with the engine-tagged client_order_id and connector order_id.
    fn gf(cid: &str, side: OrderSide, price: f64, qty: f64) -> Fill {
        Fill {
            fill_id: "f".into(),
            order_id: "paper_1".into(),
            client_order_id: Some(cid.into()),
            symbol: "BTCUSDT".into(),
            side,
            price,
            quantity: qty,
            fee: price * qty * 0.001,
            timestamp: 1,
        }
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

    /// Grid must identify its OWN fills via client_order_id (the engine-tagged
    /// "owner:idx#grid_…"), never fill.order_id (the connector's "paper_N").
    #[tokio::test]
    async fn on_fill_ignores_non_grid_fills() {
        let mut grid = make_grid();
        // A trend-strategy fill (cid has no "grid_") must be ignored entirely.
        grid.on_fill(&gf("owner:1#trend_BTC-USDT_entry", OrderSide::Buy, 100.0, 1.0)).await.unwrap();
        assert_eq!(grid.realized_pnl(), 0.0, "non-grid fill must not affect grid");
    }

    /// A BUY accumulates inventory at cost — it must NOT book realized P&L
    /// (the old cash-flow accounting logged buys as fake "-$792 losses").
    #[tokio::test]
    async fn buy_accumulates_inventory_without_realizing_pnl() {
        let mut grid = make_grid();
        grid.on_fill(&gf("owner:0#grid_BTC-USDT_buy_0", OrderSide::Buy, 100.0, 1.0)).await.unwrap();
        assert_eq!(grid.realized_pnl(), 0.0, "a buy must not book realized PnL");
    }

    /// A SELL realizes profit against the average buy cost — only this is real
    /// grid P&L, and only this gets journaled (matches trend/MR/signal).
    #[tokio::test]
    async fn sell_realizes_profit_against_average_buy_cost() {
        let mut grid = make_grid();
        // Buy 1.0 @ 100 → cost basis 100 + 0.1 fee = 100.1
        grid.on_fill(&gf("owner:0#grid_BTC-USDT_buy_0", OrderSide::Buy, 100.0, 1.0)).await.unwrap();
        assert_eq!(grid.realized_pnl(), 0.0, "still nothing realized after buy");
        // Sell 1.0 @ 110 → proceeds 110 − 0.11 fee = 109.89; realized 109.89 − 100.1 = 9.79
        grid.on_fill(&gf("owner:0#grid_BTC-USDT_sell_0", OrderSide::Sell, 110.0, 1.0)).await.unwrap();
        let r = grid.realized_pnl();
        assert!((r - 9.79).abs() < 0.05, "realized should be ~9.79, got {}", r);
    }

    /// A sell must still set the per-level cooldown + clear order tracking.
    #[tokio::test]
    async fn sell_sets_cooldown_and_clears_tracking() {
        let mut grid = make_grid();
        grid.orders.insert("grid_BTC-USDT_sell_1".into(), GridOrder { order_id: "grid_BTC-USDT_sell_1".into() });
        grid.on_fill(&gf("owner:0#grid_BTC-USDT_buy_0", OrderSide::Buy, 100.0, 2.0)).await.unwrap();
        grid.on_fill(&gf("owner:0#grid_BTC-USDT_sell_1", OrderSide::Sell, 101.0, 1.0)).await.unwrap();
        assert!(grid.orders.is_empty(), "filled order must be removed from tracking");
        assert!(grid.has_level_cooldown("sell_1"), "level cooldown must be set after a fill");
    }

    #[test]
    fn buy_message_shows_inventory_not_pnl() {
        let m = grid_buy_message("DOGE-USDT", "buy_0", 0.0858, 9235.0, 792.0);
        assert!(m.contains("BUY") && m.contains("DOGE-USDT"));
        assert!(!m.contains("PnL"), "buy message must not show PnL (it's inventory): {}", m);
    }

    #[test]
    fn sell_message_shows_realized_pnl() {
        let m = grid_sell_message("DOGE-USDT", "sell_0", 0.09, 9.79, 9.79);
        assert!(m.contains("SELL") && m.contains("DOGE-USDT") && m.contains("realized"));
    }

    // ── C2: force_flat (routing layer go-flat) ──

    /// force_flat must: (1) queue all resting orders for cancel, (2) clear the
    /// orders map, (3) stash a Market sell for the inventory, (4) flip state to
    /// Paused so on_tick won't re-place the ladder. The close order is drained
    /// on the next on_tick (before the Paused early-return).
    #[tokio::test]
    async fn force_flat_queues_cancels_and_emits_inventory_close() {
        let mut grid = make_grid();
        // Simulate two resting orders + accumulated inventory.
        grid.orders.insert("grid_BTC-USDT_buy_0".into(), GridOrder { order_id: "grid_BTC-USDT_buy_0".into() });
        grid.orders.insert("grid_BTC-USDT_sell_0".into(), GridOrder { order_id: "grid_BTC-USDT_sell_0".into() });
        grid.inventory_qty = 2.0;
        grid.inventory_cost = 200.0;
        grid.diag_price = 105.0; // last seen mid — force_flat prices the market sell off this
        grid.state = GridState::Active;

        grid.force_flat();

        // (1) + (2): resting orders cleared from map, both cids queued for cancel.
        assert!(grid.orders.is_empty(), "orders map cleared");
        let cancels = grid.pending_cancels();
        assert_eq!(cancels.len(), 2, "both resting orders queued for cancel");
        assert!(cancels.contains(&"grid_BTC-USDT_buy_0".to_string()));
        assert!(cancels.contains(&"grid_BTC-USDT_sell_0".to_string()));

        // (3): Market sell stashed for the inventory.
        let close = grid.force_flat_close.as_ref().expect("inventory close stashed");
        assert!(matches!(close.order_type, OrderTypeReq::Market));
        assert_eq!(close.side, OrderSide::Sell);
        assert!(close.reduce_only);
        assert!((close.quantity - 2.0).abs() < 1e-9, "closes full inventory qty");
        assert!(close.client_order_id.as_deref().unwrap_or("").starts_with("grid_"),
            "cid keeps grid_ prefix so on_fill recognizes it: {:?}", close.client_order_id);

        // (4): state flipped to Paused.
        assert_eq!(grid.state, GridState::Paused);
    }

    /// If inventory is zero, force_flat skips stashing a close (nothing to sell)
    /// but still cancels resting orders + pauses.
    #[tokio::test]
    async fn force_flat_with_no_inventory_skips_close_order() {
        let mut grid = make_grid();
        grid.orders.insert("grid_BTC-USDT_buy_0".into(), GridOrder { order_id: "grid_BTC-USDT_buy_0".into() });
        grid.inventory_qty = 0.0;
        grid.diag_price = 100.0;
        grid.state = GridState::Active;

        grid.force_flat();
        assert!(grid.force_flat_close.is_none(), "no close order when inventory is 0");
        assert_eq!(grid.pending_cancels().len(), 1, "resting order still cancelled");
        assert_eq!(grid.state, GridState::Paused);
    }
}
