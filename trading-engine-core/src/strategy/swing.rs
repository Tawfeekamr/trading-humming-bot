use crate::config::{SwingConfig, RunnerExitMode};
use crate::indicators::{Adx, Atr, CandlestickPatterns, Pattern, DonchianChannel, Rsi, VolumeSma, Macd};
use crate::models::bar::Bar;
use crate::models::order::OrderSide;
use crate::strategy::{Strategy, TickContext, StrategyStatus};
// Journal removed — unified trades.db.
use crate::connector::types::{OrderRequest, Fill, OrderTypeReq, TimeInForceReq};
use crate::notifications::TelegramBot;
use async_trait::async_trait;
use anyhow::Result;
use serde::{Serialize, Deserialize};
use tracing::debug;

#[derive(Serialize, Deserialize, Clone)]
pub struct SwingPosition {
    pub side: OrderSide,
    pub entry_price: f64,
    pub stop_loss: f64,
    pub quantity: f64,
    pub remaining_qty: f64,
    pub highest_since_entry: f64,
    pub entry_time: i64,
    pub midline_scaled_out: bool,
}

pub struct SwingStrategy {
    pair: String,
    config: SwingConfig,
    position: Option<SwingPosition>,
    realized_pnl: f64,
    telegram: TelegramBot,
    pending_buy: bool,
    pending_sell: bool,
    pending_buy_ts: i64,
    pending_sell_ts: i64,
    ranging_regime: bool,
    entry_qty: f64,
    entry_stop: f64,
    /// TP1 (midline) price captured at the entry decision, used to place the
    /// resting LIMIT_MAKER scale-out when the entry fills.
    entry_tp1: f64,
    /// client-id of the resting LIMIT_MAKER TP1 (None once it fills or is cancelled).
    resting_tp1_cid: Option<String>,
    /// client-id of the resting STOP_LOSS hard stop currently protecting the position.
    resting_stop_cid: Option<String>,
    /// client-ids the strategy wants the engine to cancel next cycle.
    cancel_queue: Vec<String>,
}

fn parse_tf_ms(tf: &str) -> i64 {
    match tf {
        "1m" => 60_000,
        "5m" => 300_000,
        "15m" => 900_000,
        "1h" => 3_600_000,
        "4h" => 14_400_000,
        "1d" => 86_400_000,
        _ => 60_000,
    }
}

/// Round a qty/price down to the exchange filter step/tick. No-op when unset
/// (tests/backtest), so live orders pass LOT_SIZE / PRICE_FILTER cleanly.
fn round_step(value: f64, step: Option<f64>) -> f64 {
    match step {
        Some(s) if s > 0.0 => (value / s).floor() * s,
        _ => value,
    }
}

/// Swing entry policy. The setup CORE — a ranging regime with price at/under the
/// Donchian lower band (i.e. buying range lows) — is required. Each confirmation
/// (RSI oversold, RSI bullish divergence, MACD turn-up, reversal candle, volume
/// spike) adds a point; an entry needs `min_score` of them.
///
/// Reversal candle + volume spike used to be HARD gates, which left the strategy
/// idle (0 entries in production): on the coarse 1h feed those signals fire far
/// less often than the 5m timeframe the strategy was backtested on. They now
/// boost the score instead of blocking. The R:R gate in `on_tick` still protects
/// every entry.
fn swing_entry_ready(ranging: bool, near_lower_band: bool, score: usize, min_score: usize) -> bool {
    ranging && near_lower_band && score >= min_score
}

fn aggregate_closed_bars(bars: &[Bar], interval_ms: i64) -> Vec<Bar> {
    let mut agg = Vec::new();
    if bars.is_empty() { return agg; }
    
    let mut current_bucket = bars[0].timestamp / interval_ms;
    let mut open = bars[0].open;
    let mut high = bars[0].high;
    let mut low = bars[0].low;
    let mut close = bars[0].close;
    let mut volume = bars[0].volume;
    let mut ts = bars[0].timestamp;

    for bar in &bars[1..] {
        let bucket = bar.timestamp / interval_ms;
        if bucket == current_bucket {
            high = high.max(bar.high);
            low = low.min(bar.low);
            close = bar.close;
            volume += bar.volume;
            ts = bar.timestamp;
        } else {
            agg.push(Bar::new(open, high, low, close, volume, ts));
            current_bucket = bucket;
            open = bar.open;
            high = bar.high;
            low = bar.low;
            close = bar.close;
            volume = bar.volume;
            ts = bar.timestamp;
        }
    }
    
    let base_interval = if bars.len() >= 2 { bars[1].timestamp - bars[0].timestamp } else { 60_000 };
    if ts + base_interval >= (current_bucket + 1) * interval_ms {
        agg.push(Bar::new(open, high, low, close, volume, ts));
    }
    
    agg
}

impl SwingStrategy {
    pub fn new(pair: &str, config: &SwingConfig, telegram: TelegramBot) -> Self {
        Self {
            pair: pair.to_string(),
            config: config.clone(),
            position: None,
            realized_pnl: 0.0,
            telegram,
            pending_buy: false,
            pending_sell: false,
            pending_buy_ts: 0,
            pending_sell_ts: 0,
            ranging_regime: false,
            entry_qty: 0.0,
            entry_stop: 0.0,
            entry_tp1: 0.0,
            resting_tp1_cid: None,
            resting_stop_cid: None,
            cancel_queue: Vec::new(),
        }
    }

    fn update_indicators(&self, htf_bars: &[Bar], ltf_bars: &[Bar]) -> (DonchianChannel, Adx, Rsi, Atr, VolumeSma, Macd, f64, bool) {
        let mut donchian = DonchianChannel::new(self.config.donchian_period);
        let mut adx = Adx::new(14);
        let mut rsi = Rsi::new(self.config.rsi_period as u32);
        let mut macd = Macd::default_12_26_9();

        let mut prev_macd_hist = 0.0;
        let mut curr_macd_hist = 0.0;
        let mut rsi_history = Vec::new();
        let mut htf_closes = Vec::new();

        for bar in htf_bars {
            donchian.update(bar.high, bar.low);
            adx.update_bar(bar.open, bar.high, bar.low, bar.close);
            rsi.update(bar.close);
            rsi_history.push(rsi.value());
            htf_closes.push(bar.close);
            prev_macd_hist = curr_macd_hist;
            macd.update(bar.close);
            curr_macd_hist = macd.histogram();
        }
        
        let mut rsi_divergence = false;
        if rsi_history.len() >= 4 {
            let curr_rsi = rsi_history.last().unwrap();
            let curr_close = htf_closes.last().unwrap();
            let old_rsi = rsi_history[rsi_history.len() - 4];
            let old_close = htf_closes[htf_closes.len() - 4];
            if curr_close < &old_close && curr_rsi > &old_rsi {
                rsi_divergence = true;
            }
        }

        let mut atr = Atr::new(self.config.atr_period as u32);
        let mut volume_sma = VolumeSma::new(self.config.volume_avg_period as u32);
        
        for bar in ltf_bars {
            atr.update_bar(bar.open, bar.high, bar.low, bar.close);
            volume_sma.update(bar.volume);
        }

        (donchian, adx, rsi, atr, volume_sma, macd, prev_macd_hist, rsi_divergence)
    }

    fn position_file(&self) -> std::path::PathBuf {
        std::path::PathBuf::from(format!("data/swing_{}.json", self.pair))
    }

    fn save_position(&self) {
        let path = self.position_file();
        if let Some(pos) = &self.position {
            if let Ok(data) = serde_json::to_string(pos) {
                let _ = std::fs::write(path, data);
            }
        } else {
            let _ = std::fs::remove_file(path);
        }
    }

    fn load_position(&mut self) {
        let path = self.position_file();
        if let Ok(data) = std::fs::read_to_string(path) {
            if let Ok(pos) = serde_json::from_str::<SwingPosition>(&data) {
                self.position = Some(pos);
            }
        }
    }

    fn get_mid_price(&self, ctx: &TickContext) -> Option<f64> {
        ctx.order_book.mid_price()
    }

    /// Queue the resting TP1 and hard stop for cancellation — used when a
    /// reactive exit (chandelier / opposite-band / time-stop) fires first and
    /// Market-closes the remainder. The engine drains `cancel_queue` next cycle.
    fn cancel_resting(&mut self) {
        if let Some(c) = self.resting_tp1_cid.take() { self.cancel_queue.push(c); }
        if let Some(c) = self.resting_stop_cid.take() { self.cancel_queue.push(c); }
    }
}

#[async_trait]
impl Strategy for SwingStrategy {
    fn name(&self) -> &str { "swing" }
    fn trading_pair(&self) -> &str { &self.pair }
    fn realized_pnl(&self) -> f64 { self.realized_pnl }
    fn deployed_capital(&self) -> f64 { self.position.as_ref().map_or(0.0, |p| p.remaining_qty * p.entry_price) }
    fn current_capital(&self) -> f64 { self.config.capital + self.realized_pnl }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>> {
        if !self.config.enabled || ctx.recent_bars.len() < 50 {
            return Ok(vec![]);
        }

        let htf_ms = parse_tf_ms(&self.config.htf_period);
        let htf_bars = aggregate_closed_bars(&ctx.recent_bars, htf_ms);
        let ltf_bars = &ctx.recent_bars;

        if htf_bars.is_empty() || ltf_bars.is_empty() {
            return Ok(vec![]);
        }

        let (donchian, adx, rsi, atr, volume_sma, macd, prev_macd_hist, rsi_divergence) = self.update_indicators(&htf_bars, ltf_bars);
        
        let Some(mid_price) = self.get_mid_price(ctx) else { return Ok(vec![]) };
        let current_close = ltf_bars.last().unwrap().close;
        let atr_val = atr.value();

        let mut orders = Vec::new();

        if self.pending_sell {
            if ctx.timestamp - self.pending_sell_ts > 60_000 {
                self.pending_sell = false;
            } else {
                return Ok(vec![]);
            }
        }
        if self.pending_buy {
            if ctx.timestamp - self.pending_buy_ts > 60_000 {
                self.pending_buy = false;
            } else {
                return Ok(vec![]);
            }
        }

        let mut save_needed = false;
        // Defer the reactive-exit cancel+emit to after the `pos` borrow ends
        // (cancel_resting takes &mut self).
        let mut pending_exit: Option<(String, f64)> = None;
        if let Some(pos) = &mut self.position {
            if mid_price > pos.highest_since_entry {
                pos.highest_since_entry = mid_price;
                save_needed = true;
            }

            let mut exit_reason = None;

            // Hard stop is a resting STOP_LOSS on the exchange (placed at entry,
            // replaced to runner-qty after TP1). No reactive duplicate — it fires
            // via on_fill when price crosses pos.stop_loss, and survives a crash.
            let bars_in_trade = (ctx.timestamp - pos.entry_time) / parse_tf_ms(&self.config.ltf_period);
            if bars_in_trade as usize >= self.config.max_bars_in_trade {
                exit_reason = Some("TimeStop");
            }

            // Scale-out is the resting LIMIT_MAKER TP1 — no reactive scale-out.

            let chandelier_stop = pos.highest_since_entry - (atr_val * self.config.atr_stop_mult);
            match self.config.runner_exit {
                RunnerExitMode::OppositeBand => {
                    if current_close >= donchian.upper_band() {
                        exit_reason = Some("OppositeBand");
                    }
                }
                RunnerExitMode::ChandelierOnly => {
                    if mid_price <= chandelier_stop {
                        exit_reason = Some("ChandelierStop");
                    }
                }
                RunnerExitMode::BandOrChandelier => {
                    if current_close >= donchian.upper_band() {
                        exit_reason = Some("OppositeBand");
                    } else if mid_price <= chandelier_stop {
                        exit_reason = Some("ChandelierStop");
                    }
                }
            }

            if let Some(reason) = exit_reason {
                pending_exit = Some((reason.to_string(), pos.remaining_qty));
            }
        } else {

            let len = ltf_bars.len();
            if len >= 2 && donchian.is_initialized() {
                let prev = &ltf_bars[len - 2];
                let curr = &ltf_bars[len - 1];
                let candlesticks = CandlestickPatterns::new(0.4);
                
                let pattern = candlesticks.detect(curr, Some(prev));
                let is_reversal = pattern == Pattern::Hammer || pattern == Pattern::BullishEngulfing;
                let near_lower_band = current_close <= donchian.lower_band() + (atr_val * self.config.band_atr_mult);
                
                let current_adx = adx.adx();
                if current_adx < self.config.adx_range_entry {
                    self.ranging_regime = true;
                } else if current_adx > self.config.adx_trend_exit {
                    self.ranging_regime = false;
                }
                let ranging = self.ranging_regime;
                
                let rsi_oversold = rsi.value() < self.config.rsi_oversold;
                let macd_turn = macd.histogram() > prev_macd_hist && prev_macd_hist < 0.0;
                let volume_spike = curr.volume > volume_sma.value() * self.config.volume_multiplier;

                // Each confirmation adds a point. Reversal candle + volume spike
                // are NOT hard gates — on the 1h prod feed they fire far less often
                // than the 5m this was backtested on, and hard-requiring them left
                // the engine idle (0 entries). They still boost the score, and the
                // R:R gate below still protects every entry.
                let mut score = 0;
                if rsi_oversold { score += 1; }
                if rsi_divergence { score += 1; }
                if macd_turn { score += 1; }
                if is_reversal { score += 1; }
                if volume_spike { score += 1; }

                const SWING_MIN_SCORE: usize = 3;
                if swing_entry_ready(ranging, near_lower_band, score, SWING_MIN_SCORE) {
                    let alloc = self.current_capital();
                    let risk_amt = alloc * (self.config.risk_per_trade_pct / 100.0);
                    let stop_dist = atr_val * self.config.atr_stop_mult;
                    
                    if stop_dist > 0.0 {
                        let reward_dist = donchian.mid_band() - mid_price;
                        let rr = reward_dist / stop_dist;
                        if rr >= self.config.min_rr {
                            let mut qty = risk_amt / stop_dist;
                            let max_qty = alloc / mid_price;
                            if qty > max_qty { qty = max_qty; }

                            // Phase B2: cap to available free capital.
                            if let Some(cm) = &ctx.capital {
                                let notional = qty * mid_price;
                                let granted = cm.request_capital("swing", notional);
                                if notional > 0.0 { qty *= granted / notional; }
                            }

                            self.entry_stop = mid_price - stop_dist;
                            self.entry_qty = qty;
                            // Take-profit at 1.5R, not the Donchian mid-band (~2R).
                            // Backtest (MFE diagnostic) showed avg favorable excursion
                            // is only ~1R, so the mid-band TP almost never filled and
                            // winners reversed into losers. Cuts full-period loss ~80%
                            // and flips ETH OOS positive (see PR + swing_bot memory).
                            self.entry_tp1 = mid_price + 1.5 * stop_dist;

                            orders.push(OrderRequest {
                                symbol: self.pair.clone(),
                                side: OrderSide::Buy,
                                order_type: OrderTypeReq::Market,
                                quantity: qty,
                                price: None,
                                time_in_force: Some(TimeInForceReq::Gtc),
                                client_order_id: Some("entry".to_string()),
                                reduce_only: false,
                            });
                            self.pending_buy = true;
                            self.pending_buy_ts = ctx.timestamp;
                        }
                    }
                } else if ranging && near_lower_band {
                    // Valid setup zone (ranging + at the lower band) but too few
                    // confirmations — surface it so the strategy isn't silently
                    // flat. debug! is filtered at the default info level.
                    debug!(
                        "[{}] Swing setup at range low, {}/{} confirmations \
                         (rsi_oversold={} divergence={} macd_turn={} reversal={} vol_spike={})",
                        self.pair, score, SWING_MIN_SCORE,
                        rsi_oversold, rsi_divergence, macd_turn, is_reversal, volume_spike
                    );
                }
            }
        }

        // Reactive exit (pos borrow released): cancel resting orders so they
        // can't double-exit, then Market-close the remainder (reduce-only).
        if let Some((reason, qty)) = pending_exit {
            self.cancel_resting();
            orders.push(OrderRequest {
                symbol: self.pair.clone(),
                side: OrderSide::Sell,
                order_type: OrderTypeReq::Market,
                quantity: qty,
                price: None,
                time_in_force: Some(TimeInForceReq::Gtc),
                client_order_id: Some(format!("exit_{}", reason)),
                reduce_only: true,
            });
            self.pending_sell = true;
            self.pending_sell_ts = ctx.timestamp;
        }

        if save_needed { self.save_position(); }

        Ok(orders)
    }

    async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>> {
        let cid = fill.client_order_id.clone().unwrap_or_default();
        let mut new_orders = Vec::new();

        if fill.side == OrderSide::Buy && self.pending_buy {
            self.position = Some(SwingPosition {
                side: OrderSide::Buy,
                entry_price: fill.price,
                stop_loss: self.entry_stop,
                quantity: fill.quantity,
                remaining_qty: fill.quantity,
                highest_since_entry: fill.price,
                entry_time: fill.timestamp,
                midline_scaled_out: false,
            });
            self.save_position();
            self.pending_buy = false;

            // Resting TP1 (maker) at the midline — 50% rounded to the symbol's
            // LOT_SIZE step (100% if half rounds below it); price → tick filter.
            let tick = self.config.tick_size;
            let step = self.config.step_size;
            let tp1_price = round_step(self.entry_tp1, tick);
            let stop_price = round_step(self.entry_stop, tick);
            let full_qty = round_step(fill.quantity, step);
            let half = round_step(fill.quantity * 0.5, step);
            let tp1_qty = if half <= 0.0 || half < round_step(fill.quantity * 0.1, step) { full_qty } else { half };
            let tp1_cid = format!("swing_tp1_{}", fill.timestamp);
            new_orders.push(OrderRequest {
                symbol: self.pair.clone(),
                side: OrderSide::Sell,
                order_type: OrderTypeReq::LimitMaker,
                quantity: tp1_qty,
                price: Some(tp1_price),
                time_in_force: None,
                client_order_id: Some(tp1_cid.clone()),
                reduce_only: true,
            });
            self.resting_tp1_cid = Some(tp1_cid);

            // Resting hard stop (STOP_LOSS) for the full position — survives a
            // process crash; replaced to runner-qty after TP1 fills.
            let stop_cid = format!("swing_stop_{}", fill.timestamp);
            new_orders.push(OrderRequest {
                symbol: self.pair.clone(),
                side: OrderSide::Sell,
                order_type: OrderTypeReq::StopMarket { stop_price },
                quantity: full_qty,
                price: None,
                time_in_force: None,
                client_order_id: Some(stop_cid.clone()),
                reduce_only: true,
            });
            self.resting_stop_cid = Some(stop_cid);

            let msg = format!("🚀 *Swing Entry*\nPair: {}\nPrice: {:.4}\nQty: {:.4}\nStop: {:.4} | TP1: {:.4}",
                                self.pair, fill.price, fill.quantity, self.entry_stop, self.entry_tp1);
            let _ = self.telegram.send(&msg).await;
        } else if fill.side == OrderSide::Sell {
            // Sells now arrive from resting orders too (TP1, hard stop), not just
            // reactive exits — so route by client-id instead of gating on pending_sell.
            if cid.contains("swing_tp1") {
                // TP1 (midline) scale-out filled.
                let runner = if let Some(pos) = &mut self.position {
                    let pnl = (fill.price - pos.entry_price) * fill.quantity;
                    self.realized_pnl += pnl;
                    pos.remaining_qty -= fill.quantity;
                    pos.midline_scaled_out = true;
                        let dur = (fill.timestamp - pos.entry_time) / 60_000;
                        crate::strategy::trade_journal::log_unified("swing", &self.pair, Some(pos.entry_price), Some(fill.price), Some(fill.quantity), pnl, Some("ScaleOut"), Some(dur));
                    let msg = format!("🔔 *Swing Scale-Out*\nPair: {}\nPrice: {:.4}\nPnL: {:.2} USDT", self.pair, fill.price, pnl);
                    let _ = self.telegram.send(&msg).await;
                    Some((pos.stop_loss, pos.remaining_qty))
                } else { None };

                if let Some((stop_price, remaining)) = runner {
                    self.save_position();
                    self.resting_tp1_cid = None; // TP1 consumed
                    // Replace the full-qty stop with a runner-qty stop at the same level.
                    if let Some(old) = self.resting_stop_cid.take() { self.cancel_queue.push(old); }
                    let runner_cid = format!("swing_stop_run_{}", fill.timestamp);
                    let rs_price = round_step(stop_price, self.config.tick_size);
                    let rs_qty = round_step(remaining, self.config.step_size);
                    new_orders.push(OrderRequest {
                        symbol: self.pair.clone(),
                        side: OrderSide::Sell,
                        order_type: OrderTypeReq::StopMarket { stop_price: rs_price },
                        quantity: rs_qty,
                        price: None,
                        time_in_force: None,
                        client_order_id: Some(runner_cid.clone()),
                        reduce_only: true,
                    });
                    self.resting_stop_cid = Some(runner_cid);
                }
            } else {
                // Full exit: resting hard stop fired, or a reactive Market exit filled.
                if let Some(pos) = self.position.take() {
                    let pnl = (fill.price - pos.entry_price) * fill.quantity;
                    self.realized_pnl += pnl;
                    let reason = if cid.contains("swing_stop") { "StopLoss" }
                        else if cid.contains("TimeStop") { "TimeStop" }
                        else if cid.contains("ChandelierStop") { "ChandelierStop" }
                        else if cid.contains("OppositeBand") { "OppositeBand" }
                        else { "Exit" };
                        let dur = (fill.timestamp - pos.entry_time) / 60_000;
                        crate::strategy::trade_journal::log_unified("swing", &self.pair, Some(pos.entry_price), Some(fill.price), Some(fill.quantity), pnl, Some(reason), Some(dur));
                    let msg = format!("🔔 *Swing Exit* ({})\nPair: {}\nPrice: {:.4}\nPnL: {:.2} USDT",
                                      reason, self.pair, fill.price, pnl);
                    let _ = self.telegram.send(&msg).await;
                    // The other resting order (if any) is now stale — cancel it so
                    // it can't fill against a closed position.
                    if let Some(c) = self.resting_tp1_cid.take() { self.cancel_queue.push(c); }
                    if let Some(c) = self.resting_stop_cid.take() { self.cancel_queue.push(c); }
                    self.pending_sell = false;
                    self.save_position();
                }
            }
        }

        Ok(new_orders)
    }

    fn pending_cancels(&mut self) -> Vec<String> {
        std::mem::take(&mut self.cancel_queue)
    }

    async fn on_start(&mut self) -> Result<Vec<OrderRequest>> {
        self.realized_pnl = crate::strategy::trade_journal::realized_pnl("swing", &self.pair);
        self.load_position();
        Ok(Vec::new())
    }

    async fn on_stop(&mut self) -> Result<()> { Ok(()) }

    fn status(&self) -> StrategyStatus {
        StrategyStatus {
            name: self.name().to_string(),
            pair: self.pair.clone(),
            state: if self.position.is_some() { "IN_POSITION".to_string() } else { "SEARCHING".to_string() },
            pnl: self.realized_pnl,
            open_orders: if self.position.is_some() { 1 } else { 0 },
            details: format!("Capital: {:.2}", self.current_capital()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::SwingConfig;

    fn cfg() -> SwingConfig {
        SwingConfig {
            enabled: true, runner_exit: RunnerExitMode::BandOrChandelier,
            htf_period: "1h".into(), ltf_period: "5m".into(), donchian_period: 20,
            band_atr_mult: 0.5, rsi_period: 14, rsi_oversold: 30.0, volume_multiplier: 1.5,
            volume_avg_period: 20, atr_period: 14, atr_stop_mult: 1.5, min_rr: 2.0,
            risk_per_trade_pct: 1.0, adx_range_entry: 22.0, adx_trend_exit: 28.0,
            capital: 10_000.0, max_bars_in_trade: 48,
            enabled_pairs: vec![], step_size: None, tick_size: None,
        }
    }

    fn buy_fill(price: f64, qty: f64, ts: i64) -> Fill {
        Fill {
            fill_id: "f".into(), order_id: "o".into(), client_order_id: Some("entry".into()),
            symbol: "BTCUSDT".into(), side: OrderSide::Buy, price, quantity: qty, fee: 0.0, timestamp: ts,
        }
    }

    /// Entry fill must place a resting LIMIT_MAKER TP1 (50% @ midline) AND a
    /// resting STOP_LOSS hard stop (full qty @ stop), and remember both cids.
    #[tokio::test]
    async fn entry_fill_places_resting_tp1_and_stop() {
        let mut s = SwingStrategy::new("BTCUSDT", &cfg(), TelegramBot::new("", ""));
        // Simulate the state on_tick's entry branch sets.
        s.pending_buy = true;
        s.entry_stop = 48_500.0;
        s.entry_tp1 = 51_500.0;
        s.entry_qty = 1.0;

        let orders = s.on_fill(&buy_fill(50_000.0, 1.0, 1_000)).await.unwrap();
        assert_eq!(orders.len(), 2, "entry fill should place TP1 + hard stop");

        let (tp1, stop) = (&orders[0], &orders[1]);
        assert!(matches!(tp1.order_type, OrderTypeReq::LimitMaker), "first resting order is maker TP1");
        assert_eq!(tp1.price, Some(51_500.0), "TP1 rests at the midline");
        assert!((tp1.quantity - 0.5).abs() < 1e-9, "TP1 is 50% of the position");
        assert!(tp1.reduce_only);

        assert!(matches!(stop.order_type, OrderTypeReq::StopMarket { stop_price } if (stop_price - 48_500.0).abs() < 1e-9),
            "second resting order is a STOP_LOSS at the hard stop");
        assert!((stop.quantity - 1.0).abs() < 1e-9, "hard stop covers the FULL position");
        assert!(stop.reduce_only);

        assert!(s.resting_tp1_cid.is_some() && s.resting_stop_cid.is_some());
        assert!(s.position.is_some(), "position opened");
        assert!((s.position.as_ref().unwrap().remaining_qty - 1.0).abs() < 1e-9);
    }

    /// A TP1 fill must replace the full-qty stop with a runner-qty stop and queue
    /// the old stop for cancellation.
    #[tokio::test]
    async fn tp1_fill_replaces_stop_and_queues_cancel() {
        let mut s = SwingStrategy::new("BTCUSDT", &cfg(), TelegramBot::new("", ""));
        s.pending_buy = true;
        s.entry_stop = 48_500.0;
        s.entry_tp1 = 51_500.0;
        s.entry_qty = 1.0;
        s.on_fill(&buy_fill(50_000.0, 1.0, 1_000)).await.unwrap();

        let old_stop = s.resting_stop_cid.clone().unwrap();
        let tp1_cid = s.resting_tp1_cid.clone().unwrap();

        // TP1 fills at the midline, half qty.
        let tp1_fill = Fill {
            fill_id: "f2".into(), order_id: "o2".into(),
            client_order_id: Some(tp1_cid), symbol: "BTCUSDT".into(),
            side: OrderSide::Sell, price: 51_500.0, quantity: 0.5, fee: 0.0, timestamp: 2_000,
        };
        let orders = s.on_fill(&tp1_fill).await.unwrap();
        assert_eq!(orders.len(), 1, "TP1 fill places the runner stop");
        assert!(matches!(orders[0].order_type, OrderTypeReq::StopMarket { stop_price } if (stop_price - 48_500.0).abs() < 1e-9));
        assert!((orders[0].quantity - 0.5).abs() < 1e-9, "runner stop covers the remaining 50%");

        // Old full-qty stop is queued for the engine to cancel; runner stop is the new cid.
        let cancels = s.pending_cancels();
        assert_eq!(cancels, vec![old_stop], "old full-qty stop must be cancelled");
        assert!(s.resting_stop_cid.is_some(), "runner stop now tracked");
        assert!(s.resting_tp1_cid.is_none(), "TP1 consumed");
        assert!((s.position.as_ref().unwrap().remaining_qty - 0.5).abs() < 1e-9);
        assert!(s.position.as_ref().unwrap().midline_scaled_out);
    }

    /// A resting hard-stop fill closes the position fully (no pending_sell needed).
    #[tokio::test]
    async fn hard_stop_fill_closes_position_without_pending_sell() {
        let mut s = SwingStrategy::new("BTCUSDT", &cfg(), TelegramBot::new("", ""));
        s.pending_buy = true;
        s.entry_stop = 48_500.0;
        s.entry_tp1 = 51_500.0;
        s.entry_qty = 1.0;
        s.on_fill(&buy_fill(50_000.0, 1.0, 1_000)).await.unwrap();
        let stop_cid = s.resting_stop_cid.clone().unwrap();
        assert!(!s.pending_sell);

        let stop_fill = Fill {
            fill_id: "f3".into(), order_id: "o3".into(),
            client_order_id: Some(stop_cid), symbol: "BTCUSDT".into(),
            side: OrderSide::Sell, price: 48_500.0, quantity: 1.0, fee: 0.0, timestamp: 3_000,
        };
        let orders = s.on_fill(&stop_fill).await.unwrap();
        assert!(orders.is_empty(), "stop fill closes, places nothing");
        assert!(s.position.is_none(), "position cleared by hard stop");
        assert!(s.resting_stop_cid.is_none() && s.resting_tp1_cid.is_none());
    }

    /// The entry policy must fire on the core setup (ranging + at the lower band)
    /// with only 2 confirmations — WITHOUT hard-requiring a reversal candle or a
    /// volume spike. Hard-requiring those left swing idle (0 entries) on the coarse
    /// 1h production feed, where such candles fire far less often than the 5m the
    /// strategy was backtested on.
    #[test]
    fn entry_fires_without_reversal_or_volume_when_core_setup_confirmed() {
        // Core setup + 2 confirmations (e.g. RSI oversold + MACD turn) is enough.
        assert!(swing_entry_ready(true, true, 2, 2));
        // 5 confirmations but missing the core setup → no entry.
        assert!(!swing_entry_ready(false, true, 5, 2), "ranging is required");
        assert!(!swing_entry_ready(true, false, 5, 2), "near-lower-band is required");
        // Core setup but only 1 confirmation → no entry.
        assert!(!swing_entry_ready(true, true, 1, 2), "min 2 confirmations required");
    }
}
