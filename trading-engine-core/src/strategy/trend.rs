use crate::config::TrendConfig;
use crate::indicators::{Ema, Rsi, Atr, Adx, Choppiness, Macd, VolumeSma};
use crate::models::bar::Bar;
use crate::models::order::OrderSide;
use crate::strategy::{Strategy, TickContext, StrategyStatus};
// Journal removed — trades go to the unified trades.db via log_unified.
use crate::connector::types::{OrderRequest, Fill, OrderTypeReq, TimeInForceReq};
use crate::notifications::TelegramBot;
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
    /// True when the position was just loaded from disk. The first on_tick after
    /// load reconciles overdue TPs without firing a catch-up exit burst.
    #[serde(default)]
    pub restored: bool,
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
    /// Last live order-book price seen by on_tick. status() uses this (not the
    /// lagging ema_fast) to compute direction + unrealized MTM, so "Ready" only
    /// shows when an entry would actually fire (price must be > ema_slow).
    last_price: f64,
    telegram: TelegramBot,
}

impl TrendStrategy {
    pub fn new(pair: &str, config: &TrendConfig, telegram: TelegramBot) -> Self {
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
            last_price: 0.0,
            telegram,
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
        let duration = duration_minutes(now_ts, entry_time);
        crate::strategy::trade_journal::log_unified("trend", &self.pair, Some(entry_price), Some(exit_price), Some(amount), pnl, Some(exit_reason), Some(duration));
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
        // Trend-strength gate: never enter a pair with no real trend, even if
        // the weighted score clears the threshold on volume/RSI alone. Without
        // this a ranging pair (ADX≈0) enters then immediately trips the
        // "ADX dying" exit → an entry/exit churn loop (XRP logged 1,807 such
        // trades in one day on a no-trend pair).
        let adx_gate = if self.config.adx_gate_threshold > 0.0 { self.config.adx_gate_threshold } else { 20.0 };
        if self.adx.adx() < adx_gate { return false; }
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
                        let mut pos = state.position;
                        pos.restored = true; // reconcile on the first on_tick after load
                        self.position = Some(pos);
                        self.realized_pnl = state.realized_pnl;
                    }
                    Err(e) => warn!("Failed to parse trend position for {}: {}", self.pair, e),
                }
            }
            Err(e) => warn!("Failed to read trend position for {}: {}", self.pair, e),
        }
    }

    fn notify_exit(&self, exit_price: f64, pnl: f64, reason: &str) {
        let msg = trend_exit_message(&self.pair, reason, exit_price, pnl, self.realized_pnl);
        // Fire-and-forget: never block the tick loop on Telegram latency.
        let tg = self.telegram.clone_for_signal();
        tokio::spawn(async move { let _ = tg.send(&msg).await; });
    }

    /// Ping on position open so a new trade is visible immediately (trend
    /// previously only notified on exit). Mirrors notify_exit's fire-and-forget.
    fn notify_entry(&self, entry_price: f64, qty: f64, stop_loss: f64) {
        let msg = trend_entry_message(&self.pair, entry_price, qty, stop_loss);
        let tg = self.telegram.clone_for_signal();
        tokio::spawn(async move { let _ = tg.send(&msg).await; });
    }
}

#[async_trait]
impl Strategy for TrendStrategy {
    fn name(&self) -> &str { "trend" }
    fn trading_pair(&self) -> &str { &self.pair }

    fn realized_pnl(&self) -> f64 { self.realized_pnl }
    fn deployed_capital(&self) -> f64 { self.position.as_ref().map_or(0.0, |p| p.remaining_qty * p.entry_price) }

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

        // Require a live order-book quote. The previous fallback to
        // recent_bars.last().close let the strategy trade at a stale bar close
        // when the order book was empty — most importantly during bar-replay
        // warmup on restart, where the replay ctx has an empty order book, so
        // every historical bar's close became "current_price" and fired phantom
        // TP exits at prices the live market never reached (ETH "exits" at
        // $1832.52 while real ETH was ~$1.6k). No live quote => hold.
        let current_price = ctx.order_book.mid_price().unwrap_or(0.0);
        if current_price <= 0.0 { return Ok(orders); }
        self.last_price = current_price;

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
        // Restore reconciliation: a position just loaded from disk may have TP
        // levels already below the current price (they "happened" while we were
        // down). Mark those TPs filled WITHOUT re-firing notifications/orders,
        // and skip exit evaluation for this one tick — a restart must not
        // liquidate the position with a catch-up burst.
        let mut skip_exits_for_restore = false;
        if let Some(pos) = &mut self.position {
            if pos.restored {
                for tp in pos.tp_levels.iter_mut() {
                    if !tp.filled && current_price >= tp.price {
                        tp.filled = true;
                    }
                }
                pos.restored = false;
                skip_exits_for_restore = true;
            }
        }
        if skip_exits_for_restore {
            self.save_position();
            return Ok(orders);
        }

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
                self.notify_exit(current_price, pnl, "stop_loss");
                orders.push(OrderRequest {
                    symbol: self.pair.clone(), side: OrderSide::Sell, reduce_only: true,
                    order_type: OrderTypeReq::Market, price: None,
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
                            symbol: self.pair.clone(), side: OrderSide::Sell, reduce_only: true,
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
                self.notify_exit(current_price, *pnl, reason);
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
                        self.notify_exit(current_price, pnl, "trailing_stop");
                        orders.push(OrderRequest {
                            symbol: self.pair.clone(), side: OrderSide::Sell, reduce_only: true,
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
                    self.notify_exit(current_price, pnl, "signal_exit");
                    orders.push(OrderRequest {
                        symbol: self.pair.clone(), side: OrderSide::Sell, reduce_only: true,
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
                // Phase B2: cap to available free capital (compute-then-cap).
                let quantity = match &ctx.capital {
                    Some(cm) => {
                        let notional = quantity * current_price;
                        let granted = cm.request_capital("trend", notional);
                        if notional > 0.0 { quantity * granted / notional } else { 0.0 }
                    }
                    None => quantity,
                };
                if quantity > 0.0 {
                    orders.push(OrderRequest {
                        symbol: self.pair.clone(), side: OrderSide::Buy, reduce_only: false,
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
                    restored: false,
                });
                self.save_position();
                self.notify_entry(fill.price, fill.quantity, stop_loss);
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

    async fn on_start(&mut self) -> Result<Vec<OrderRequest>> {
        // Reconstitute cumulative realized P&L from the authoritative journal.
        // The position file is deleted on close, so without this a restart
        // while flat would silently reset realized_pnl to $0 — hiding the
        // engine's real result from /trend's total and from position-sizing
        // capital (calculate_qty uses capital + realized_pnl).
        if true {
            self.realized_pnl = crate::strategy::trade_journal::realized_pnl("trend", &self.pair);
        }
        Ok(Vec::new())
    }
    async fn on_stop(&mut self) -> Result<()> { Ok(()) }

    fn status(&self) -> StrategyStatus {
        // Reference price for direction + mark-to-market: the last LIVE tick
        // price, not the lagging ema_fast. Otherwise "Ready / dir:+1" shows
        // whenever ema_fast > ema_slow, even when the live price has dropped
        // below ema_slow (where on_tick's entry check would skip). Falls back
        // to ema_fast before the first live tick.
        let ref_price = if self.last_price > 0.0 { self.last_price } else { self.ema_fast.value() };
        let (state, details, pnl) = if let Some(pos) = &self.position {
            let unrealized = match pos.side {
                OrderSide::Buy => (ref_price - pos.entry_price) * pos.remaining_qty,
                OrderSide::Sell => (pos.entry_price - ref_price) * pos.remaining_qty,
            };
            let side_str = match pos.side { OrderSide::Buy => "LONG", OrderSide::Sell => "SHORT" };
            let trail_str = match pos.trailing_stop {
                Some(ts) => format!(" | Trail: ${:.2}", ts), None => String::new(),
            };
            let dir_str = match self.direction(ref_price) {
                Direction::Up => "+1", Direction::Down => "-1", Direction::Flat => "0",
            };
            (
                "POSITION".to_string(),
                format!("{} {:.4} @ ${:.2} | SL: ${:.2}{} | ADX: {:.1} | dir: {}\nRealized: ${:.0} | Unrealized: ${:.2}",
                    side_str, pos.remaining_qty, pos.entry_price, pos.stop_loss, trail_str, self.adx.adx(), dir_str,
                    self.realized_pnl, unrealized),
                self.realized_pnl + unrealized,
            )
        } else if !self.indicators_ready() {
            ("WAITING".to_string(), "⏳ All indicators warming up".to_string(), 0.0)
        } else {
            let dir = self.direction(ref_price);
            let scores = self.compute_score(dir);
            let threshold = if self.config.entry_score_threshold > 0 { self.config.entry_score_threshold } else { 5 };
            let dir_str = match dir { Direction::Up => "+1", Direction::Down => "-1", Direction::Flat => "0" };
            let reason = if dir == Direction::Flat { "Mixed direction".to_string() }
                         else if dir == Direction::Down && !self.config.trade_shorts { "dir=-1 blocks longs".to_string() }
                         else if scores.total < threshold { format!("Need {} more", threshold - scores.total) }
                         else { "Ready".to_string() };
            (
                "WAITING".to_string(),
                format!("Score:{}/9 (A:{} C:{} V:{} M:{} R:{}) need≥{} | dir:{} | ADX={:.1} CHOP={:.0} RSI={:.1} | {} | Realized: ${:.0}",
                    scores.total,
                    scores.adx, scores.chop, scores.volume, scores.macd, scores.rsi,
                    threshold,
                    dir_str,
                    self.adx.adx(), self.choppiness.value(), self.rsi.value(), reason,
                    self.realized_pnl),
                self.realized_pnl,
            )
        };
        StrategyStatus { name: self.name().to_string(), pair: self.pair.clone(), state, pnl, open_orders: 0, details }
    }

    fn current_capital(&self) -> f64 { self.config.capital + self.realized_pnl }
    fn initial_capital(&self) -> f64 { self.initial_capital }
}

/// Hold time in minutes from two millisecond timestamps. 0 if either is unset.
///
/// Timestamps here are ms (Binance/chrono epoch-ms). The previous code divided by
/// 60, inflating durations 1000× — a 6-hour ETH hold logged as ~250 "days" in
/// trades.db. MR and swing already divide by 60_000; this brings trend in line.
fn duration_minutes(now_ts: i64, entry_time: i64) -> i64 {
    if now_ts > 0 && entry_time > 0 {
        ((now_ts - entry_time) / 60_000).max(0)
    } else {
        0
    }
}

/// Telegram message for a trend ENTRY (position open). Extracted so the format
/// is unit-testable without a network send.
fn trend_entry_message(pair: &str, entry_price: f64, qty: f64, stop_loss: f64) -> String {
    format!(
        "🚀 Trend ENTRY {} | Buy @ ${:.2} | Qty {:.4} | Stop ${:.2}",
        pair, entry_price, qty, stop_loss
    )
}

/// Telegram message for a trend EXIT. Losses are loud (🛑 LOSS) and carry the
/// engine's running realized P&L so each alert is self-explanatory. Extracted
/// so the format is unit-testable without a network send.
fn trend_exit_message(pair: &str, reason: &str, exit_price: f64, pnl: f64, running_pnl: f64) -> String {
    let marker = if pnl < 0.0 { "🛑 LOSS Trend" } else { "📈 Trend" };
    format!(
        "{} {} {} @ ${:.2} | this: ${:+.2} | Trend running: ${:+.2}",
        marker, pair, reason, exit_price, pnl, running_pnl
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duration_minutes_uses_millisecond_timestamps() {
        let entry = 1_700_000_000_000_i64;
        // 2 hours = 7,200,000 ms.
        assert_eq!(duration_minutes(entry + 7_200_000, entry), 120, "2h hold = 120 min");
        // Unset timestamps → 0, never negative.
        assert_eq!(duration_minutes(0, entry), 0);
        assert_eq!(duration_minutes(entry, 0), 0);
    }

    #[test]
    fn entry_message_flags_a_new_trade_with_price_and_stop() {
        let msg = trend_entry_message("ETH-USDT", 1795.93, 1.10, 1563.91);
        assert!(msg.contains("ENTRY"), "must flag a new trade: {}", msg);
        assert!(msg.contains("ETH-USDT"), "must name the pair: {}", msg);
        assert!(msg.contains("1795.9"), "must show entry price: {}", msg);
        assert!(msg.contains("1563.9"), "must show stop: {}", msg);
    }

    #[test]
    fn trend_exit_message_loss_is_loud_with_running_total() {
        let msg = trend_exit_message("ETH-USDT", "stop_loss", 1800.0, -259.25, -472.20);
        assert!(msg.starts_with("🛑 LOSS Trend ETH-USDT stop_loss"), "got: {msg}");
        assert!(msg.contains("$-259.25"), "should show this trade pnl: {msg}");
        assert!(msg.contains("Trend running: $-472.20"), "should show running total: {msg}");
    }

    #[test]
    fn trend_exit_message_win_is_rocket_no_loss_marker() {
        let msg = trend_exit_message("BNB-USDT", "tp1", 614.5, 30.02, 93.49);
        assert!(msg.starts_with("📈 Trend BNB-USDT tp1"), "got: {msg}");
        assert!(!msg.contains("LOSS"), "wins must not be marked LOSS: {msg}");
        assert!(msg.contains("Trend running: $+93.49"), "should show running total: {msg}");
    }
}
