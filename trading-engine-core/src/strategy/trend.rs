use crate::config::TrendConfig;
use crate::indicators::{Ema, Rsi, Atr, SupportResistance, CandlestickPatterns};
use crate::models::bar::Bar;
use crate::models::order::OrderSide;
use crate::strategy::{Strategy, TickContext, StrategyStatus};
use crate::connector::types::{OrderRequest, Fill, OrderTypeReq, TimeInForceReq};
use async_trait::async_trait;
use anyhow::Result;

#[derive(Debug, Clone)]
pub struct SignalScore {
    pub total: u8,
    pub details: Vec<SignalDetail>,
}

#[derive(Debug, Clone)]
pub struct SignalDetail {
    pub name: String,
    pub score: u8,
    pub direction: Option<OrderSide>,
}

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum TrendDirection {
    Bullish,
    Bearish,
    Neutral,
}

/// Take-profit level with close percentage
#[derive(Debug, Clone)]
pub struct TpLevel {
    pub price: f64,
    pub close_pct: f64, // fraction of remaining position to close
    pub filled: bool,
}

#[derive(Debug, Clone)]
pub struct TrendPosition {
    pub side: OrderSide,
    pub entry_price: f64,
    pub stop_loss: f64,
    pub quantity: f64,
    pub remaining_qty: f64,
    pub trailing_stop: Option<f64>,
    pub trailing_activated: bool,
    pub tp_levels: Vec<TpLevel>,
}

impl TrendPosition {
    /// Calculate 3 TP levels based on risk (Gemini recommendation):
    /// TP1: entry + 1× risk → close 33%
    /// TP2: entry + 1.5× risk → close 50% of remaining
    /// TP3: entry + 2× risk → close 80% of remaining (leave 10% runner)
    pub fn calculate_tp_levels(entry_price: f64, stop_loss: f64, risk_reward_ratio: f64, runner_pct: f64) -> Vec<TpLevel> {
        let risk = entry_price - stop_loss;
        // TP3 closes everything except the runner
        let tp3_close = if runner_pct > 0.0 { 1.0 - runner_pct } else { 1.0 };
        vec![
            TpLevel {
                price: entry_price + risk * 1.0,
                close_pct: 0.33,
                filled: false,
            },
            TpLevel {
                price: entry_price + risk * 1.5,
                close_pct: 0.50,
                filled: false,
            },
            TpLevel {
                price: entry_price + risk * risk_reward_ratio,
                close_pct: tp3_close,
                filled: false,
            },
        ]
    }
}

pub struct TrendStrategy {
    pair: String,
    config: TrendConfig,

    // Indicators
    ema_fast: Ema,
    ema_slow: Ema,
    ema_trend: Ema,
    rsi: Rsi,
    atr: Atr,
    sr: SupportResistance,
    candlestick: CandlestickPatterns,

    // State
    confirm_count: u8,
    last_signal: Option<TrendDirection>,
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
            },
            ema_fast: Ema::new(config.ema_fast),
            ema_slow: Ema::new(config.ema_slow),
            ema_trend: Ema::new(config.ema_trend),
            rsi: Rsi::new(config.rsi_period),
            atr: Atr::new(14),
            sr: SupportResistance::new(50, 0.01),
            candlestick: CandlestickPatterns::new(0.3),
            confirm_count: 0,
            last_signal: None,
            position: None,
            initial_capital: capital,
            realized_pnl: 0.0,
        }
    }

    pub fn update_indicators(&mut self, bar: &Bar) {
        self.ema_fast.update(bar.close);
        self.ema_slow.update(bar.close);
        self.ema_trend.update(bar.close);
        self.rsi.update(bar.close);
        self.atr.update_bar(bar.open, bar.high, bar.low, bar.close);
        self.sr.update_bar(bar.open, bar.high, bar.low, bar.close, bar.timestamp);
    }

    /// Evaluate all signals and return a score (max 8)
    pub fn evaluate_signals(&self, current_price: f64) -> SignalScore {
        let mut score = SignalScore {
            total: 0,
            details: Vec::new(),
        };

        if !self.indicators_ready() {
            return score;
        }

        let ema_fast_val = self.ema_fast.value();
        let ema_slow_val = self.ema_slow.value();
        let ema_trend_val = self.ema_trend.value();
        let rsi_val = self.rsi.value();
        let rsi_min = if self.config.rsi_min > 0.0 { self.config.rsi_min } else { 40.0 };
        let rsi_max = if self.config.rsi_max > 0.0 { self.config.rsi_max } else { 70.0 };

        // Signal 1: EMA cross (+1)
        if ema_fast_val > ema_slow_val {
            score.total += 1;
            score.details.push(SignalDetail {
                name: "ema_cross".into(),
                score: 1,
                direction: Some(OrderSide::Buy),
            });
        }

        // Signal 2: Trend filter (+1)
        if current_price > ema_trend_val && ema_fast_val > ema_slow_val {
            score.total += 1;
            score.details.push(SignalDetail {
                name: "trend_filter".into(),
                score: 1,
                direction: Some(OrderSide::Buy),
            });
        }

        // Signal 3: RSI confirmation (+1) — not overbought
        if rsi_val > rsi_min && rsi_val < rsi_max {
            score.total += 1;
            score.details.push(SignalDetail {
                name: "rsi_confirm".into(),
                score: 1,
                direction: Some(OrderSide::Buy),
            });
        }

        // Signal 4: At support (+2)
        if self.sr.near_support(current_price) {
            score.total += 2;
            score.details.push(SignalDetail {
                name: "at_support".into(),
                score: 2,
                direction: Some(OrderSide::Buy),
            });
        }

        score
    }

    pub fn should_enter(&self, score: &SignalScore) -> bool {
        score.total >= self.config.min_signal_score
    }

    pub fn should_exit(&self, score: &SignalScore) -> bool {
        let threshold = if self.config.exit_signal_threshold > 0 {
            self.config.exit_signal_threshold
        } else {
            2
        };
        score.total <= threshold
    }

    pub fn calculate_stop_loss(&self, entry_price: f64) -> f64 {
        let atr_sl = entry_price - 2.0 * self.atr.value();
        // Apply buffer: slightly wider than ATR-based SL
        let buffered = atr_sl * (1.0 - self.config.sl_buffer_pct / 100.0);
        buffered
    }

    /// Dynamic position sizing based on risk percentage and SL distance
    pub fn calculate_quantity(&self, entry_price: f64, stop_loss: f64) -> f64 {
        let sl_distance = entry_price - stop_loss;
        if sl_distance <= 0.0 {
            return 0.0;
        }

        let current_capital = self.config.capital + self.realized_pnl;
        let risk_amount = current_capital * (self.config.risk_per_trade_pct / 100.0);
        let max_position_value = current_capital * (self.config.max_position_pct / 100.0);

        // Quantity based on how much we're willing to lose
        let qty_by_risk = risk_amount / sl_distance;

        // Clamp: position value must not exceed max_position_pct of capital
        let max_qty = max_position_value / entry_price;

        qty_by_risk.min(max_qty)
    }

    fn indicators_ready(&self) -> bool {
        self.ema_fast.is_initialized()
            && self.ema_slow.is_initialized()
            && self.ema_trend.is_initialized()
            && self.rsi.is_initialized()
            && self.atr.is_initialized()
    }

    pub fn position(&self) -> Option<&TrendPosition> {
        self.position.as_ref()
    }

    pub fn set_position(&mut self, pos: Option<TrendPosition>) {
        self.position = pos;
    }

    pub fn set_paused(&mut self, _paused: bool) {
        // Trend strategy doesn't have a pause state — no-op
    }
}

#[async_trait]
impl Strategy for TrendStrategy {
    fn name(&self) -> &str {
        "trend"
    }

    fn trading_pair(&self) -> &str {
        &self.pair
    }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>> {
        let mut orders = Vec::new();

        // Update indicators from recent bars
        for bar in &ctx.recent_bars {
            self.update_indicators(bar);
        }

        // Only generate orders if indicators are ready
        if !self.indicators_ready() {
            return Ok(orders);
        }

        let current_price = ctx.order_book.mid_price().unwrap_or_else(|| {
            ctx.recent_bars
                .last()
                .map(|b| b.close)
                .unwrap_or(0.0)
        });

        if current_price <= 0.0 {
            return Ok(orders);
        }

        // ── Priority 1: SL / Trailing Stop / TP hit detection ──
        if let Some(pos) = &mut self.position {
            // Stop-loss hit
            if current_price <= pos.stop_loss {
                let sell_qty = pos.remaining_qty;
                let entry = pos.entry_price;
                self.realized_pnl += (current_price - entry) * sell_qty;
                self.position = None;
                orders.push(OrderRequest {
                    symbol: self.pair.clone(),
                    side: OrderSide::Sell,
                    order_type: OrderTypeReq::Limit,
                    price: Some(current_price),
                    quantity: sell_qty,
                    time_in_force: Some(TimeInForceReq::Gtc),
                    client_order_id: None,
                });
                return Ok(orders);
            }

            // Trailing stop hit
            if pos.trailing_activated {
                if let Some(ts) = pos.trailing_stop {
                    if current_price <= ts {
                        let sell_qty = pos.remaining_qty;
                        let entry = pos.entry_price;
                        self.realized_pnl += (current_price - entry) * sell_qty;
                        self.position = None;
                        orders.push(OrderRequest {
                            symbol: self.pair.clone(),
                            side: OrderSide::Sell,
                            order_type: OrderTypeReq::Limit,
                            price: Some(current_price),
                            quantity: sell_qty,
                            time_in_force: Some(TimeInForceReq::Gtc),
                            client_order_id: None,
                        });
                        return Ok(orders);
                    }
                }
            }

            // TP level hits — partial exits
            for tp in &mut pos.tp_levels {
                if tp.filled {
                    continue;
                }
                if current_price >= tp.price {
                    let sell_qty = pos.remaining_qty * tp.close_pct;
                    if sell_qty > 0.0 {
                        tp.filled = true;
                        pos.remaining_qty -= sell_qty;
                        let entry = pos.entry_price;
                        self.realized_pnl += (current_price - entry) * sell_qty;

                        orders.push(OrderRequest {
                            symbol: self.pair.clone(),
                            side: OrderSide::Sell,
                            order_type: OrderTypeReq::Limit,
                            price: Some(current_price),
                            quantity: sell_qty,
                            time_in_force: Some(TimeInForceReq::Gtc),
                            client_order_id: None,
                        });

                        // If all remaining closed, clear position
                        if pos.remaining_qty <= 0.0001 {
                            self.position = None;
                            return Ok(orders);
                        }
                    }
                }
            }

            // ── Update trailing stop if position still open (ATR-based Chandelier Exit) ──
            if let Some(pos) = &mut self.position {
                let activation_pct = self.config.trailing_activation_pct / 100.0;

                // Activate trailing once price moves activation_pct above entry
                if !pos.trailing_activated {
                    let gain = (current_price - pos.entry_price) / pos.entry_price;
                    if gain >= activation_pct {
                        pos.trailing_activated = true;
                    }
                }

                // Update trailing stop — ATR-based (Chandelier Exit)
                if pos.trailing_activated {
                    let atr_val = self.atr.value();
                    let atr_mult = if self.config.trailing_stop_atr_mult > 0.0 {
                        self.config.trailing_stop_atr_mult
                    } else {
                        2.5 // sensible default
                    };
                    let new_trail = current_price - atr_val * atr_mult;
                    pos.trailing_stop = Some(match pos.trailing_stop {
                        Some(prev) => new_trail.max(prev), // only move up, never down
                        None => new_trail,
                    });
                }
            }
        }

        // ── Priority 2: Signal-based entry/exit ──
        let score = self.evaluate_signals(current_price);

        if self.position.is_none() {
            // No position — check if we should enter
            if self.should_enter(&score) {
                let stop_loss = self.calculate_stop_loss(current_price);
                let quantity = self.calculate_quantity(current_price, stop_loss);

                if quantity > 0.0 {
                    orders.push(OrderRequest {
                        symbol: self.pair.clone(),
                        side: OrderSide::Buy,
                        order_type: OrderTypeReq::Limit,
                        price: Some(current_price),
                        quantity,
                        time_in_force: Some(TimeInForceReq::Gtc),
                        client_order_id: None,
                    });
                }
            }
        } else if let Some(pos) = &self.position {
            // Have position — check if signal degrades
            if self.should_exit(&score) {
                let sell_qty = pos.remaining_qty;
                let entry = pos.entry_price;
                self.realized_pnl += (current_price - entry) * sell_qty;
                self.position = None;
                orders.push(OrderRequest {
                    symbol: self.pair.clone(),
                    side: OrderSide::Sell,
                    order_type: OrderTypeReq::Limit,
                    price: Some(current_price),
                    quantity: sell_qty,
                    time_in_force: Some(TimeInForceReq::Gtc),
                    client_order_id: None,
                });
            }
        }

        Ok(orders)
    }

    async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>> {
        match fill.side {
            OrderSide::Buy => {
                // Buy fill — open position with SL + multi-TP
                let stop_loss = self.calculate_stop_loss(fill.price);
                let tp_levels = TrendPosition::calculate_tp_levels(
                    fill.price, stop_loss, self.config.risk_reward_ratio, 0.10, // 10% runner
                );

                let pos = TrendPosition {
                    side: OrderSide::Buy,
                    entry_price: fill.price,
                    stop_loss,
                    quantity: fill.quantity,
                    remaining_qty: fill.quantity,
                    trailing_stop: None,
                    trailing_activated: false,
                    tp_levels,
                };
                self.set_position(Some(pos));
            }
            OrderSide::Sell => {
                // Sell fill — reduce or clear position
                if let Some(mut pos) = self.position.take() {
                    pos.remaining_qty -= fill.quantity;
                    if pos.remaining_qty <= 0.0001 {
                        // Fully closed
                        self.set_position(None);
                    } else {
                        self.set_position(Some(pos));
                    }
                }
            }
        }

        Ok(Vec::new())
    }

    async fn on_start(&mut self) -> Result<Vec<OrderRequest>> {
        Ok(Vec::new())
    }

    async fn on_stop(&mut self) -> Result<()> {
        Ok(())
    }

    fn status(&self) -> StrategyStatus {
        let (state, details, pnl) = if let Some(pos) = &self.position {
            let unrealized_pnl = if let Some(current_price) = self.ema_fast.value().into() {
                match pos.side {
                    OrderSide::Buy => (current_price - pos.entry_price) * pos.remaining_qty,
                    OrderSide::Sell => (pos.entry_price - current_price) * pos.remaining_qty,
                }
            } else {
                0.0
            };

            let side_str = match pos.side {
                OrderSide::Buy => "LONG",
                OrderSide::Sell => "SHORT",
            };

            let filled_tps: Vec<usize> = pos.tp_levels.iter()
                .enumerate()
                .filter(|(_, tp)| tp.filled)
                .map(|(i, _)| i + 1)
                .collect();

            let trail_str = match pos.trailing_stop {
                Some(ts) => format!(" | Trail: ${:.2}", ts),
                None => String::new(),
            };

            let tp_str = if filled_tps.is_empty() {
                String::new()
            } else {
                format!(" | TP{} hit", filled_tps.iter().map(|n| n.to_string()).collect::<Vec<_>>().join(","))
            };

            (
                "POSITION".to_string(),
                format!(
                    "{} {:.4} @ ${:.2} | SL: ${:.2}{}{}",
                    side_str, pos.remaining_qty, pos.entry_price, pos.stop_loss, trail_str, tp_str
                ),
                unrealized_pnl,
            )
        } else {
            (
                "WAITING".to_string(),
                "No position — waiting for signal".to_string(),
                0.0,
            )
        };

        StrategyStatus {
            name: self.name().to_string(),
            pair: self.pair.clone(),
            state,
            pnl,
            open_orders: 0,
            details,
        }
    }

    fn set_paused(&mut self, _paused: bool) {
        // No-op — trend strategy doesn't have pause state
    }

    fn current_capital(&self) -> f64 {
        self.config.capital + self.realized_pnl
    }

    fn initial_capital(&self) -> f64 {
        self.initial_capital
    }
}
