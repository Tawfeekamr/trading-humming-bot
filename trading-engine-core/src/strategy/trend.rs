use crate::config::TrendConfig;
use crate::indicators::{Ema, Rsi, Atr, Adx, Choppiness, Macd, VolumeSma};
use crate::models::bar::Bar;
use crate::models::order::OrderSide;
use crate::strategy::{Strategy, TickContext, StrategyStatus};
use crate::strategy::trend_journal::TrendJournal;
use crate::connector::types::{OrderRequest, Fill, OrderTypeReq, TimeInForceReq};
use async_trait::async_trait;
use anyhow::Result;
use serde::{Serialize, Deserialize};
use std::fs;
use tracing::{warn, debug};

/// Direction from EMA cross + price position.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Direction {
    Up,    // +1: EMA fast > slow AND close > slow
    Down,  // -1: EMA fast < slow AND close < slow
    Flat,  //  0: mixed signals
}

/// Weighted signal scores (0–9 total).
/// ADX(0-3) + CHOP(0-2) + VOL(0-2) + MACD(0-1) + RSI(0-1) = max 9.
#[derive(Debug, Clone, Copy, Default)]
pub struct SignalScores {
    pub adx: u8,
    pub chop: u8,
    pub volume: u8,
    pub macd: u8,
    pub rsi: u8,
    pub total: u8,
}

/// Take-profit level with close percentage
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TpLevel {
    pub price: f64,
    pub close_pct: f64,
    pub filled: bool,
}

/// A trend position with direction-aware trailing stop.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TrendPosition {
    pub side: OrderSide,
    pub entry_price: f64,
    pub stop_loss: f64,
    pub quantity: f64,
    pub remaining_qty: f64,
    pub trailing_stop: Option<f64>,
    pub highest_since_entry: f64,
    pub lowest_since_entry: f64,
    pub tp_levels: Vec<TpLevel>,
    /// Unix-seconds timestamp of the opening fill (serde default keeps old state files loadable).
    #[serde(default)]
    pub entry_time: i64,
}

impl TrendPosition {
    pub fn calculate_tp_levels(entry_price: f64, stop_loss: f64, risk_reward_ratio: f64, runner_pct: f64) -> Vec<TpLevel> {
        let risk = entry_price - stop_loss;
        // Guard: a missing/zero RR would place TP3 at the entry price, making it
        // fire instantly. Fall back to 2:1 so the position always has real targets.
        let rr = if risk_reward_ratio > 0.0 { risk_reward_ratio } else { 2.0 };
        let tp3_close = if runner_pct > 0.0 { 1.0 - runner_pct } else { 1.0 };
        vec![
            TpLevel { price: entry_price + risk * 1.0, close_pct: 0.33, filled: false },
            TpLevel { price: entry_price + risk * 1.5, close_pct: 0.50, filled: false },
            TpLevel { price: entry_price + risk * rr, close_pct: tp3_close, filled: false },
        ]
    }
}

/// Persisted state for trend strategy (position + PnL tracking).
#[derive(Serialize, Deserialize)]
struct TrendPositionState {
    position: TrendPosition,
    realized_pnl: f64,
}

pub struct TrendStrategy {
    pair: String,
    config: TrendConfig,
    // Direction indicators
    ema_fast: Ema,
    ema_slow: Ema,
    // Gate indicators
    adx: Adx,
    choppiness: Choppiness,
    // Confirmation indicators
    macd: Macd,
    volume_sma: VolumeSma,
    rsi: Rsi,
    // Exit indicator
    atr: Atr,
    // State
    position: Option<TrendPosition>,
    last_bar_count: usize,
    // Capital tracking
    initial_capital: f64,
    realized_pnl: f64,
    // Trade journal (fail-soft: None if the DB cannot be opened)
    journal: Option<TrendJournal>,
}

impl TrendStrategy {
    pub fn new(pair: &str, config: &TrendConfig) -> Self {
        let journal = match TrendJournal::new() {
            Ok(j) => Some(j),
            Err(e) => {
                warn!("Trend journal disabled for {}: {}", pair, e);
                None
            }
        };
        Self::new_with_journal(pair, config, journal)
    }

    /// Construct with an explicit journal (used by tests; production uses `new`).
    pub fn new_with_journal(pair: &str, config: &TrendConfig, journal: Option<TrendJournal>) -> Self {
        let capital = config.capital;
        let mut me = Self {
            pair: pair.to_string(),
            config: TrendConfig {
                ema_fast: config.ema_fast,
                ema_slow: config.ema_slow,
                ema_trend: config.ema_trend,
                rsi_period: config.rsi_period,
                rsi_min: config.rsi_min,
                rsi_max: config.rsi_max,
                min_signal_score: config.min_signal_score,
                confirmation_ticks: config.confirmation_ticks,
                risk_reward_ratio: config.risk_reward_ratio,
                capital: config.capital,
                risk_per_trade_pct: config.risk_per_trade_pct,
                max_position_pct: config.max_position_pct,
                trailing_stop_pct: config.trailing_stop_pct,
                trailing_stop_atr_mult: config.trailing_stop_atr_mult,
                trailing_activation_pct: config.trailing_activation_pct,
                exit_signal_threshold: config.exit_signal_threshold,
                sl_buffer_pct: config.sl_buffer_pct,
                adx_gate_threshold: config.adx_gate_threshold,
                adx_exit_threshold: config.adx_exit_threshold,
                choppiness_threshold: config.choppiness_threshold,
                volume_ratio_threshold: config.volume_ratio_threshold,
                entry_score_threshold: config.entry_score_threshold,
                rsi_long_max: config.rsi_long_max,
                rsi_short_min: config.rsi_short_min,
                atr_trailing_mult: config.atr_trailing_mult,
                trade_shorts: config.trade_shorts,
            },
            ema_fast: Ema::new(config.ema_fast),
            ema_slow: Ema::new(config.ema_slow),
            adx: Adx::new(14),
            choppiness: Choppiness::new(14),
            macd: Macd::default_12_26_9(),
            volume_sma: VolumeSma::new(20),
            rsi: Rsi::new(config.rsi_period),
            atr: Atr::new(14),
            position: None,
            last_bar_count: 0,
            initial_capital: capital,
            realized_pnl: 0.0,
            journal,
        };
        me.load_position();
        me
    }

    /// Log a close event to the journal (no-op if the journal is unavailable).
    #[allow(clippy::too_many_arguments)]
    fn log_close(
        &self,
        side: OrderSide,
        entry_price: f64,
        exit_price: f64,
        amount: f64,
        pnl: f64,
        stop_loss: f64,
        take_profit: f64,
        exit_reason: &str,
        now_ts: i64,
        entry_time: i64,
    ) {
        if let Some(j) = &self.journal {
            let duration = if now_ts > 0 && entry_time > 0 {
                ((now_ts - entry_time) / 60).max(0)
            } else {
                0
            };
            j.log_trade(
                &self.pair, side, entry_price, exit_price, amount, pnl,
                stop_loss, take_profit, exit_reason, duration,
            );
        }
    }

    pub fn update_indicators(&mut self, bar: &Bar) {
        self.ema_fast.update(bar.close);
        self.ema_slow.update(bar.close);
        self.adx.update_bar(bar.open, bar.high, bar.low, bar.close);
        self.choppiness.update_bar(bar.open, bar.high, bar.low, bar.close, None);
        self.macd.update(bar.close);
        self.volume_sma.update(bar.volume);
        self.rsi.update(bar.close);
        self.atr.update_bar(bar.open, bar.high, bar.low, bar.close);
    }

    /// Global readiness gate — all indicators must be initialized.
    fn indicators_ready(&self) -> bool {
        self.ema_fast.is_initialized()
            && self.ema_slow.is_initialized()
            && self.adx.is_initialized()
            && self.choppiness.is_initialized()
            && self.macd.is_initialized()
            && self.rsi.is_initialized()
            && self.atr.is_initialized()
            && self.volume_sma.is_initialized()
    }

    /// Unified scoring — replaces binary gate + score with weighted 0–9 system.
    ///   ADX(0-3)  + CHOP(0-2) + VOL(0-2) + MACD(0-1) + RSI(0-1) = max 9
    fn compute_score(&self, dir: Direction) -> SignalScores {
        // ADX: trend strength (higher ADX = stronger trend)
        let adx_val = self.adx.adx();
        let adx = if adx_val > 50.0 { 3 }
                  else if adx_val > 30.0 { 2 }
                  else if adx_val > 20.0 { 1 }
                  else { 0 };

        // CHOP: trend quality (lower = cleaner trend, higher = choppy)
        let chop_val = self.choppiness.value();
        let chop = if chop_val < 30.0 { 2 }
                   else if chop_val < 50.0 { 1 }
                   else { 0 };

        // Volume: market participation
        let vol_ratio = self.volume_sma.volume_ratio();
        let volume = if vol_ratio > 1.5 { 2 }
                     else if vol_ratio > 0.9 { 1 }
                     else { 0 };

        // MACD: momentum alignment with direction
        let dir_sign = match dir {
            Direction::Up => 1.0,
            Direction::Down => -1.0,
            Direction::Flat => 0.0,
        };
        let macd = if dir != Direction::Flat
            && self.macd.histogram().signum() == dir_sign { 1 } else { 0 };

        // RSI: entry timing (not overbought for longs, not oversold for shorts)
        let rsi_val = self.rsi.value();
        let rsi_long_max = if self.config.rsi_long_max > 0.0 { self.config.rsi_long_max } else { 65.0 };
        let rsi = match dir {
            Direction::Up if rsi_val < rsi_long_max => 1,
            Direction::Down if rsi_val > 35.0 => 1,
            _ => 0,
        };

        let total = adx + chop + volume + macd + rsi;
        SignalScores { adx, chop, volume, macd, rsi, total }
    }

    /// Layer 2: DIRECTION — +1 / -1 / 0
    fn direction(&self, price: f64) -> Direction {
        let ema_fast_val = self.ema_fast.value();
        let ema_slow_val = self.ema_slow.value();
        if ema_fast_val > ema_slow_val && price > ema_slow_val {
            Direction::Up
        } else if ema_fast_val < ema_slow_val && price < ema_slow_val {
            Direction::Down
        } else {
            Direction::Flat
        }
    }

    /// Activate — enter when score meets threshold AND direction is clear.
    fn should_activate(&self, price: f64) -> bool {
        let dir = self.direction(price);
        if self.config.trade_shorts {
            if dir == Direction::Flat { return false; }
        } else {
            if dir != Direction::Up { return false; }
        }
        let threshold = if self.config.entry_score_threshold > 0 { self.config.entry_score_threshold } else { 5 };
        let scores = self.compute_score(dir);
        scores.total >= threshold
    }

    /// Layer 5: EXIT — ADX dying OR direction flipped.
    fn should_exit_signal(&self, price: f64, entry_dir: Direction) -> (bool, String) {
        let adx_exit = if self.config.adx_exit_threshold > 0.0 { self.config.adx_exit_threshold } else { 20.0 };
        if self.adx.adx() < adx_exit {
            return (true, format!("ADX dying ({:.1}<{:.0})", self.adx.adx(), adx_exit));
        }
        let current_dir = self.direction(price);
        if current_dir != entry_dir && current_dir != Direction::Flat {
            return (true, "Direction flipped".to_string());
        }
        (false, String::new())
    }

    pub fn calculate_stop_loss(&self, entry_price: f64) -> f64 {
        entry_price - 2.0 * self.atr.value()
    }

    fn calculate_quantity(&self, entry_price: f64, stop_loss: f64) -> f64 {
        let sl_distance = entry_price - stop_loss;
        if sl_distance <= 0.0 { return 0.0; }
        let current_capital = self.config.capital + self.realized_pnl;
        let risk_amount = current_capital * (self.config.risk_per_trade_pct / 100.0);
        let max_position_value = current_capital * (self.config.max_position_pct / 100.0);
        let qty_by_risk = risk_amount / sl_distance;
        let max_qty = max_position_value / entry_price;
        qty_by_risk.min(max_qty)
    }

    pub fn position(&self) -> Option<&TrendPosition> { self.position.as_ref() }

    fn position_file_path(pair: &str) -> std::path::PathBuf {
        std::path::PathBuf::from(format!("data/{}_trend_position.json", pair.replace("-", "_")))
    }

    fn save_position(&self) {
        let path = Self::position_file_path(&self.pair);
        if let Some(pos) = &self.position {
            let state = TrendPositionState {
                position: pos.clone(),
                realized_pnl: self.realized_pnl,
            };
            match serde_json::to_string_pretty(&state) {
                Ok(json) => { let _ = fs::write(&path, json); }
                Err(e) => warn!("Failed to serialize trend position for {}: {}", self.pair, e),
            }
        } else {
            let _ = fs::remove_file(&path);
        }
    }

    fn load_position(&mut self) {
        let path = Self::position_file_path(&self.pair);
        if !path.exists() { return; }
        match fs::read_to_string(&path) {
            Ok(content) => {
                match serde_json::from_str::<TrendPositionState>(&content) {
                    Ok(state) => {
                        self.position = Some(state.position);
                        self.realized_pnl = state.realized_pnl;
                    }
                    Err(e) => warn!("Failed to parse trend position for {}: {}", self.pair, e),
                }
            }
            Err(e) => warn!("Failed to read trend position for {}: {}", self.pair, e),
        }
    }
}

#[async_trait]
impl Strategy for TrendStrategy {
    fn name(&self) -> &str { "trend" }
    fn trading_pair(&self) -> &str { &self.pair }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>> {
        let mut orders = Vec::new();

        // Only process NEW bars since last tick (same pattern as GridStrategy)
        let bars_to_process = if ctx.recent_bars.len() > self.last_bar_count {
            &ctx.recent_bars[self.last_bar_count..]
        } else if ctx.recent_bars.len() < self.last_bar_count {
            &ctx.recent_bars[..] // buffer was reset (e.g. engine restart)
        } else {
            &ctx.recent_bars[0..0] // no new bars
        };

        for bar in bars_to_process {
            self.update_indicators(bar);
        }
        self.last_bar_count = ctx.recent_bars.len();

        if !self.indicators_ready() { return Ok(orders); }

        let current_price = ctx.order_book.mid_price().unwrap_or_else(|| {
            ctx.recent_bars.last().map(|b| b.close).unwrap_or(0.0)
        });
        if current_price <= 0.0 { return Ok(orders); }

        // ── If in position: check exits ──
        // Snapshot entry metadata for journaling (all fields Copy; fixed at entry,
        // so valid for the whole tick). Captured before the mutable borrow below.
        let snap = self.position.as_ref().map(|p| (
            p.side,
            p.entry_price,
            p.stop_loss,
            p.tp_levels.last().map(|t| t.price).unwrap_or(p.entry_price),
            p.entry_time,
        ));
        if let Some(pos) = &mut self.position {
            if current_price > pos.highest_since_entry { pos.highest_since_entry = current_price; }
            if current_price < pos.lowest_since_entry { pos.lowest_since_entry = current_price; }

            // Stop-loss
            if current_price <= pos.stop_loss {
                let qty = pos.remaining_qty;
                let pnl = (current_price - pos.entry_price) * qty;
                self.realized_pnl += pnl;
                self.position = None;
                if let Some((s, ep, sl, tp3, et)) = snap {
                    self.log_close(s, ep, current_price, qty, pnl, sl, tp3, "stop_loss", ctx.timestamp, et);
                }
                orders.push(OrderRequest {
                    symbol: self.pair.clone(), side: OrderSide::Sell,
                    order_type: OrderTypeReq::Limit, price: Some(current_price),
                    quantity: qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                });
                self.save_position();
                return Ok(orders);
            }

            // TP partial exits — collect events to journal after the mutable borrow ends.
            let mut tp_exits: Vec<(f64, f64, &'static str)> = Vec::new();
            for (idx, tp) in pos.tp_levels.iter_mut().enumerate() {
                if tp.filled { continue; }
                if current_price >= tp.price {
                    let sell_qty = pos.remaining_qty * tp.close_pct;
                    if sell_qty > 0.0 {
                        tp.filled = true;
                        pos.remaining_qty -= sell_qty;
                        let pnl = (current_price - pos.entry_price) * sell_qty;
                        self.realized_pnl += pnl;
                        let reason = match idx { 0 => "tp1", 1 => "tp2", _ => "tp3" };
                        tp_exits.push((sell_qty, pnl, reason));
                        orders.push(OrderRequest {
                            symbol: self.pair.clone(), side: OrderSide::Sell,
                            order_type: OrderTypeReq::Limit, price: Some(current_price),
                            quantity: sell_qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                        });
                        if pos.remaining_qty <= 0.0001 { self.position = None; self.save_position(); break; }
                    }
                }
            }
            // Journal TP fills now that `pos` is no longer borrowed.
            for (qty, pnl, reason) in &tp_exits {
                if let Some((s, ep, sl, tp3, et)) = snap {
                    self.log_close(s, ep, current_price, *qty, *pnl, sl, tp3, reason, ctx.timestamp, et);
                }
            }
            if self.position.is_none() { return Ok(orders); }

            // ATR trailing stop (Chandelier Exit)
            if let Some(pos) = &mut self.position {
                let atr_mult = if self.config.atr_trailing_mult > 0.0 { self.config.atr_trailing_mult } else { 3.0 };
                let atr_val = self.atr.value();
                let new_trail = match pos.side {
                    OrderSide::Buy => pos.highest_since_entry - atr_mult * atr_val,
                    OrderSide::Sell => pos.lowest_since_entry + atr_mult * atr_val,
                };
                pos.trailing_stop = Some(match pos.trailing_stop {
                    Some(prev) => match pos.side {
                        OrderSide::Buy => new_trail.max(prev),
                        OrderSide::Sell => new_trail.min(prev),
                    },
                    None => new_trail,
                });
                if let Some(trail) = pos.trailing_stop {
                    let hit = match pos.side {
                        OrderSide::Buy => current_price <= trail,
                        OrderSide::Sell => current_price >= trail,
                    };
                    if hit {
                        let qty = pos.remaining_qty;
                        let pnl = (current_price - pos.entry_price) * qty;
                        self.realized_pnl += pnl;
                        self.position = None;
                        if let Some((s, ep, sl, tp3, et)) = snap {
                            self.log_close(s, ep, current_price, qty, pnl, sl, tp3, "trailing_stop", ctx.timestamp, et);
                        }
                        orders.push(OrderRequest {
                            symbol: self.pair.clone(), side: OrderSide::Sell,
                            order_type: OrderTypeReq::Limit, price: Some(current_price),
                            quantity: qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                        });
                        self.save_position();
                        return Ok(orders);
                    }
                }
            }

            // Direction flip / ADX exit
            if let Some(pos) = &self.position {
                let entry_dir = match pos.side { OrderSide::Buy => Direction::Up, OrderSide::Sell => Direction::Down };
                let (exit, _reason) = self.should_exit_signal(current_price, entry_dir);
                if exit {
                    let qty = pos.remaining_qty;
                    let pnl = (current_price - pos.entry_price) * qty;
                    self.realized_pnl += pnl;
                    self.position = None;
                    if let Some((s, ep, sl, tp3, et)) = snap {
                        self.log_close(s, ep, current_price, qty, pnl, sl, tp3, "signal_exit", ctx.timestamp, et);
                    }
                    orders.push(OrderRequest {
                        symbol: self.pair.clone(), side: OrderSide::Sell,
                        order_type: OrderTypeReq::Limit, price: Some(current_price),
                        quantity: qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                    });
                    self.save_position();
                    return Ok(orders);
                }
            }
        }

        // Persist any trailing stop / TP updates from this tick
        self.save_position();

        // ── No position: check for entry ──
        if self.position.is_none() {
            if self.should_activate(current_price) {
                let stop_loss = self.calculate_stop_loss(current_price);
                let quantity = self.calculate_quantity(current_price, stop_loss);
                if quantity > 0.0 {
                    orders.push(OrderRequest {
                        symbol: self.pair.clone(), side: OrderSide::Buy,
                        order_type: OrderTypeReq::Limit, price: Some(current_price),
                        quantity, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                    });
                }
            } else {
                // Log WHY entry was skipped — score breakdown, direction, threshold
                let dir = self.direction(current_price);
                let scores = self.compute_score(dir);
                let threshold = if self.config.entry_score_threshold > 0 { self.config.entry_score_threshold } else { 5 };
                debug!(
                    "[{}] Entry skipped: dir={:?} score={}/{} need≥={} | ADX={:.1} CHOP={:.0} VOL={:.2} MACD={} RSI={:.1}",
                    self.pair, dir, scores.total, 9, threshold,
                    self.adx.adx(), self.choppiness.value(),
                    self.volume_sma.volume_ratio(),
                    scores.macd, self.rsi.value()
                );
            }
        }
        Ok(orders)
    }

    async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>> {
        match fill.side {
            OrderSide::Buy => {
                let stop_loss = self.calculate_stop_loss(fill.price);
                let tp_levels = TrendPosition::calculate_tp_levels(fill.price, stop_loss, self.config.risk_reward_ratio, 0.10);
                self.position = Some(TrendPosition {
                    side: OrderSide::Buy, entry_price: fill.price, stop_loss,
                    quantity: fill.quantity, remaining_qty: fill.quantity,
                    trailing_stop: None, highest_since_entry: fill.price,
                    lowest_since_entry: fill.price, tp_levels,
                    entry_time: fill.timestamp,
                });
                self.save_position();
            }
            OrderSide::Sell => {
                if let Some(mut pos) = self.position.take() {
                    pos.remaining_qty -= fill.quantity;
                    if pos.remaining_qty <= 0.0001 { self.position = None; }
                    else { self.position = Some(pos); }
                }
                self.save_position();
            }
        }
        Ok(Vec::new())
    }

    async fn on_start(&mut self) -> Result<Vec<OrderRequest>> { Ok(Vec::new()) }
    async fn on_stop(&mut self) -> Result<()> { Ok(()) }

    fn status(&self) -> StrategyStatus {
        let (state, details, pnl) = if let Some(pos) = &self.position {
            let unrealized = match pos.side {
                OrderSide::Buy => (self.ema_fast.value() - pos.entry_price) * pos.remaining_qty,
                OrderSide::Sell => (pos.entry_price - self.ema_fast.value()) * pos.remaining_qty,
            };
            let side_str = match pos.side { OrderSide::Buy => "LONG", OrderSide::Sell => "SHORT" };
            let trail_str = match pos.trailing_stop {
                Some(ts) => format!(" | Trail: ${:.2}", ts), None => String::new(),
            };
            let dir_str = match self.direction(self.ema_fast.value()) {
                Direction::Up => "+1", Direction::Down => "-1", Direction::Flat => "0",
            };
            (
                "POSITION".to_string(),
                format!("{} {:.4} @ ${:.2} | SL: ${:.2}{} | ADX: {:.1} | dir: {}",
                    side_str, pos.remaining_qty, pos.entry_price, pos.stop_loss, trail_str, self.adx.adx(), dir_str),
                unrealized,
            )
        } else if !self.indicators_ready() {
            ("WAITING".to_string(), "⏳ All indicators warming up".to_string(), 0.0)
        } else {
            let dir = self.direction(self.ema_fast.value());
            let scores = self.compute_score(dir);
            let threshold = if self.config.entry_score_threshold > 0 { self.config.entry_score_threshold } else { 5 };
            let dir_str = match dir { Direction::Up => "+1", Direction::Down => "-1", Direction::Flat => "0" };
            let reason = if dir == Direction::Flat { "Mixed direction".to_string() }
                         else if dir == Direction::Down && !self.config.trade_shorts { "dir=-1 blocks longs".to_string() }
                         else if scores.total < threshold { format!("Need {} more", threshold - scores.total) }
                         else { "Ready".to_string() };
            (
                "WAITING".to_string(),
                format!("Score:{}/9 (A:{} C:{} V:{} M:{} R:{}) need≥{} | dir:{} | ADX={:.1} CHOP={:.0} RSI={:.1} | {}",
                    scores.total,
                    scores.adx, scores.chop, scores.volume, scores.macd, scores.rsi,
                    threshold,
                    dir_str,
                    self.adx.adx(), self.choppiness.value(), self.rsi.value(), reason),
                0.0,
            )
        };
        StrategyStatus { name: self.name().to_string(), pair: self.pair.clone(), state, pnl, open_orders: 0, details }
    }

    fn current_capital(&self) -> f64 { self.config.capital + self.realized_pnl }
    fn initial_capital(&self) -> f64 { self.initial_capital }
}
