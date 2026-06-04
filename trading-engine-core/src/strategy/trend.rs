use crate::config::TrendConfig;
use crate::indicators::{Ema, Rsi, Atr, Adx, Choppiness, Macd, VolumeSma};
use crate::models::bar::Bar;
use crate::models::order::OrderSide;
use crate::strategy::{Strategy, TickContext, StrategyStatus};
use crate::connector::types::{OrderRequest, Fill, OrderTypeReq, TimeInForceReq};
use async_trait::async_trait;
use anyhow::Result;

/// Direction from EMA cross + price position.
#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Direction {
    Up,    // +1: EMA fast > slow AND close > slow
    Down,  // -1: EMA fast < slow AND close < slow
    Flat,  //  0: mixed signals
}

/// Spot long-only: dir=Down exits longs and blocks new entries, never shorts.
const TRADE_SHORTS: bool = false;

/// Take-profit level with close percentage
#[derive(Debug, Clone)]
pub struct TpLevel {
    pub price: f64,
    pub close_pct: f64,
    pub filled: bool,
}

/// A trend position with direction-aware trailing stop.
#[derive(Debug, Clone)]
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
}

impl TrendPosition {
    pub fn calculate_tp_levels(entry_price: f64, stop_loss: f64, risk_reward_ratio: f64, runner_pct: f64) -> Vec<TpLevel> {
        let risk = entry_price - stop_loss;
        let tp3_close = if runner_pct > 0.0 { 1.0 - runner_pct } else { 1.0 };
        vec![
            TpLevel { price: entry_price + risk * 1.0, close_pct: 0.33, filled: false },
            TpLevel { price: entry_price + risk * 1.5, close_pct: 0.50, filled: false },
            TpLevel { price: entry_price + risk * risk_reward_ratio, close_pct: tp3_close, filled: false },
        ]
    }
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
    // Capital tracking
    initial_capital: f64,
    realized_pnl: f64,
}

impl TrendStrategy {
    pub fn new(pair: &str, config: &TrendConfig) -> Self {
        let capital = config.capital;
        Self {
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
                rsi_long_max: config.rsi_long_max,
                rsi_short_min: config.rsi_short_min,
                atr_trailing_mult: config.atr_trailing_mult,
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
            initial_capital: capital,
            realized_pnl: 0.0,
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

    /// Layer 1: GATE — does a trend exist?
    fn gate(&self) -> bool {
        let adx_thresh = if self.config.adx_gate_threshold > 0.0 { self.config.adx_gate_threshold } else { 25.0 };
        let chop_thresh = if self.config.choppiness_threshold > 0.0 { self.config.choppiness_threshold } else { 38.0 };
        self.adx.adx() > adx_thresh && self.choppiness.value() < chop_thresh
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

    /// Layer 3: SCORE — S1 (momentum), S2 (participation), S3 (entry timing).
    fn score(&self, dir: Direction) -> (bool, bool, bool) {
        let dir_sign = match dir {
            Direction::Up => 1.0,
            Direction::Down => -1.0,
            Direction::Flat => 0.0,
        };
        let s1 = dir != Direction::Flat && self.macd.histogram().signum() == dir_sign;
        let vol_thresh = if self.config.volume_ratio_threshold > 0.0 { self.config.volume_ratio_threshold } else { 1.2 };
        let s2 = self.volume_sma.volume_ratio() > vol_thresh;
        let rsi_val = self.rsi.value();
        let rsi_long_max = if self.config.rsi_long_max > 0.0 { self.config.rsi_long_max } else { 65.0 };
        let rsi_short_min = if self.config.rsi_short_min > 0.0 { self.config.rsi_short_min } else { 35.0 };
        let s3 = match dir {
            Direction::Up => rsi_val < rsi_long_max,
            Direction::Down => rsi_val > rsi_short_min,
            Direction::Flat => false,
        };
        (s1, s2, s3)
    }

    /// Layer 4: ACTIVATE — trend_exists AND dir==Up AND S2 AND (S1 OR S3).
    fn should_activate(&self, price: f64) -> bool {
        if !self.gate() { return false; }
        let dir = self.direction(price);
        if TRADE_SHORTS {
            if dir == Direction::Flat { return false; }
        } else {
            if dir != Direction::Up { return false; }
        }
        let (s1, s2, s3) = self.score(dir);
        s2 && (s1 || s3)
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

    fn calculate_stop_loss(&self, entry_price: f64) -> f64 {
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
}

#[async_trait]
impl Strategy for TrendStrategy {
    fn name(&self) -> &str { "trend" }
    fn trading_pair(&self) -> &str { &self.pair }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>> {
        let mut orders = Vec::new();
        for bar in &ctx.recent_bars {
            self.update_indicators(bar);
        }
        if !self.indicators_ready() { return Ok(orders); }

        let current_price = ctx.order_book.mid_price().unwrap_or_else(|| {
            ctx.recent_bars.last().map(|b| b.close).unwrap_or(0.0)
        });
        if current_price <= 0.0 { return Ok(orders); }

        // ── If in position: check exits ──
        if let Some(pos) = &mut self.position {
            if current_price > pos.highest_since_entry { pos.highest_since_entry = current_price; }
            if current_price < pos.lowest_since_entry { pos.lowest_since_entry = current_price; }

            // Stop-loss
            if current_price <= pos.stop_loss {
                let qty = pos.remaining_qty;
                self.realized_pnl += (current_price - pos.entry_price) * qty;
                self.position = None;
                orders.push(OrderRequest {
                    symbol: self.pair.clone(), side: OrderSide::Sell,
                    order_type: OrderTypeReq::Limit, price: Some(current_price),
                    quantity: qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                });
                return Ok(orders);
            }

            // TP partial exits
            for tp in &mut pos.tp_levels {
                if tp.filled { continue; }
                if current_price >= tp.price {
                    let sell_qty = pos.remaining_qty * tp.close_pct;
                    if sell_qty > 0.0 {
                        tp.filled = true;
                        pos.remaining_qty -= sell_qty;
                        self.realized_pnl += (current_price - pos.entry_price) * sell_qty;
                        orders.push(OrderRequest {
                            symbol: self.pair.clone(), side: OrderSide::Sell,
                            order_type: OrderTypeReq::Limit, price: Some(current_price),
                            quantity: sell_qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                        });
                        if pos.remaining_qty <= 0.0001 { self.position = None; return Ok(orders); }
                    }
                }
            }

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
                        self.realized_pnl += (current_price - pos.entry_price) * qty;
                        self.position = None;
                        orders.push(OrderRequest {
                            symbol: self.pair.clone(), side: OrderSide::Sell,
                            order_type: OrderTypeReq::Limit, price: Some(current_price),
                            quantity: qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                        });
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
                    self.realized_pnl += (current_price - pos.entry_price) * qty;
                    self.position = None;
                    orders.push(OrderRequest {
                        symbol: self.pair.clone(), side: OrderSide::Sell,
                        order_type: OrderTypeReq::Limit, price: Some(current_price),
                        quantity: qty, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                    });
                    return Ok(orders);
                }
            }
        }

        // ── No position: check for entry ──
        if self.position.is_none() && self.should_activate(current_price) {
            let stop_loss = self.calculate_stop_loss(current_price);
            let quantity = self.calculate_quantity(current_price, stop_loss);
            if quantity > 0.0 {
                orders.push(OrderRequest {
                    symbol: self.pair.clone(), side: OrderSide::Buy,
                    order_type: OrderTypeReq::Limit, price: Some(current_price),
                    quantity, time_in_force: Some(TimeInForceReq::Gtc), client_order_id: None,
                });
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
                });
            }
            OrderSide::Sell => {
                if let Some(mut pos) = self.position.take() {
                    pos.remaining_qty -= fill.quantity;
                    if pos.remaining_qty <= 0.0001 { self.position = None; }
                    else { self.position = Some(pos); }
                }
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
            let gate = self.gate();
            let dir = self.direction(self.ema_fast.value());
            let (s1, s2, s3) = self.score(dir);
            let dir_str = match dir { Direction::Up => "+1", Direction::Down => "-1", Direction::Flat => "0" };
            let reason = if !gate { "No trend gate" }
                         else if dir == Direction::Flat { "Mixed direction" }
                         else if dir == Direction::Down && !TRADE_SHORTS { "dir=-1 blocks longs" }
                         else if !s2 { "No volume (S2 mandatory)" }
                         else { "Waiting for confirmation" };
            (
                "WAITING".to_string(),
                format!("Gate: {}{} | dir: {} | S1:{} S2:{} S3:{} | ADX={:.1} CHOP={:.0} RSI={:.1} | {}",
                    if gate { "✅" } else { "❌" },
                    if self.choppiness.value() < 38.0 { "✅" } else { "❌" },
                    dir_str,
                    if s1 { "✅" } else { "❌" },
                    if s2 { "✅" } else { "❌" },
                    if s3 { "✅" } else { "❌" },
                    self.adx.adx(), self.choppiness.value(), self.rsi.value(), reason),
                0.0,
            )
        };
        StrategyStatus { name: self.name().to_string(), pair: self.pair.clone(), state, pnl, open_orders: 0, details }
    }

    fn current_capital(&self) -> f64 { self.config.capital + self.realized_pnl }
    fn initial_capital(&self) -> f64 { self.initial_capital }
}
