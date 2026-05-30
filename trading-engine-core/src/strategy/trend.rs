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

#[derive(Debug, Clone)]
pub struct TrendPosition {
    pub side: OrderSide,
    pub entry_price: f64,
    pub stop_loss: f64,
    pub take_profit: f64,
    pub quantity: f64,
    pub trailing_stop: Option<f64>,
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
}

impl TrendStrategy {
    pub fn new(pair: &str, config: &TrendConfig) -> Self {
        Self {
            pair: pair.to_string(),
            config: TrendConfig {
                ema_fast: config.ema_fast,
                ema_slow: config.ema_slow,
                ema_trend: config.ema_trend,
                rsi_period: config.rsi_period,
                min_signal_score: config.min_signal_score,
                confirmation_ticks: config.confirmation_ticks,
                risk_reward_ratio: config.risk_reward_ratio,
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
        if rsi_val > 40.0 && rsi_val < 70.0 {
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
        score.total <= 2
    }

    pub fn calculate_stop_loss(&self, entry_price: f64) -> f64 {
        let atr_sl = entry_price - 2.0 * self.atr.value();
        atr_sl
    }

    pub fn calculate_take_profit(&self, entry_price: f64, stop_loss: f64) -> f64 {
        let risk = entry_price - stop_loss;
        entry_price + risk * self.config.risk_reward_ratio
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

        let score = self.evaluate_signals(current_price);

        if self.position.is_none() {
            // No position — check if we should enter
            if self.should_enter(&score) {
                let stop_loss = self.calculate_stop_loss(current_price);
                let _take_profit = self.calculate_take_profit(current_price, stop_loss);

                // Calculate quantity: fixed 100 USDT worth
                let quantity = 100.0 / current_price;

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
        } else {
            // Have position — check if we should exit
            if self.should_exit(&score) {
                if let Some(pos) = &self.position {
                    orders.push(OrderRequest {
                        symbol: self.pair.clone(),
                        side: OrderSide::Sell,
                        order_type: OrderTypeReq::Limit,
                        price: Some(current_price),
                        quantity: pos.quantity,
                        time_in_force: Some(TimeInForceReq::Gtc),
                        client_order_id: None,
                    });
                }
            }
        }

        Ok(orders)
    }

    async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>> {
        let mut orders = Vec::new();

        match fill.side {
            OrderSide::Buy => {
                // Buy fill — set position
                let stop_loss = self.calculate_stop_loss(fill.price);
                let take_profit = self.calculate_take_profit(fill.price, stop_loss);

                let pos = TrendPosition {
                    side: OrderSide::Buy,
                    entry_price: fill.price,
                    stop_loss,
                    take_profit,
                    quantity: fill.quantity,
                    trailing_stop: None,
                };
                self.set_position(Some(pos));
            }
            OrderSide::Sell => {
                // Sell fill — clear position
                self.set_position(None);
            }
        }

        Ok(orders)
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
                    OrderSide::Buy => (current_price - pos.entry_price) * pos.quantity,
                    OrderSide::Sell => (pos.entry_price - current_price) * pos.quantity,
                }
            } else {
                0.0
            };

            let side_str = match pos.side {
                OrderSide::Buy => "LONG",
                OrderSide::Sell => "SHORT",
            };

            (
                "POSITION".to_string(),
                format!(
                    "{} pos @ ${:.2} | SL: ${:.2} | TP: ${:.2}",
                    side_str, pos.entry_price, pos.stop_loss, pos.take_profit
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
        0.0 // Trend strategy uses shared capital pool
    }

    fn initial_capital(&self) -> f64 {
        0.0 // Trend strategy uses shared capital pool
    }
}
