use crate::config::{MeanReversionConfig, ClassifierCfg};
use crate::strategy::{Strategy, TickContext, StrategyStatus, MarketRegime};
// Journal removed — unified trades.db.
use crate::connector::types::{OrderRequest, Fill, OrderTypeReq};
use crate::models::order::OrderSide;
use crate::notifications::TelegramBot;
use async_trait::async_trait;
use anyhow::Result;
use serde::{Serialize, Deserialize};
use tracing::{info, warn};
use std::collections::VecDeque;

#[derive(Debug)]
pub struct ReversionSignal {
    pub retrace_frac: f64,
    pub bid_refill_ratio: f64,
    pub sell_flow_decay: f64,
    pub liq_cascade_score: f64,
    pub cross_market_corr: f64,
}

#[derive(Debug)]
pub enum Verdict {
    Trade { size_mult: f64 },
    Skip,
}

pub fn classify(s: &ReversionSignal, cfg: &ClassifierCfg) -> Verdict {
    let score = cfg.w_retrace * s.retrace_frac
              + cfg.w_refill  * s.bid_refill_ratio
              + cfg.w_exhaust * s.sell_flow_decay
              + cfg.w_liq     * s.liq_cascade_score
              - cfg.w_corr    * s.cross_market_corr;

    if score < cfg.enter_threshold {
        return Verdict::Skip;
    }
    
    let size_mult = ((score - cfg.enter_threshold) / cfg.full_size_margin).clamp(0.0, 1.0);
    Verdict::Trade { size_mult }
}

/// Stop-loss alert message for MR closes — loud (🛑 LOSS) with the engine's
/// running realized P&L so each alert is self-explanatory. Pure so it is
/// unit-testable without a network send.
pub fn mr_sl_message(pair: &str, price: f64, pnl: f64, running_pnl: f64) -> String {
    format!(
        "🛑 LOSS MR {} SL @ ${:.2} | this: ${:+.2} | MR running: ${:+.2}",
        pair, price, pnl, running_pnl
    )
}

struct TickData {
    price: f64,
    timestamp: i64,
    bid_depth: f64,
}

pub struct MeanReversionStrategy {
    pair: String,
    config: MeanReversionConfig,
    telegram: TelegramBot,
    tick_history: VecDeque<TickData>,
    in_position: bool,
    entry_price: f64,
    position_qty: f64,
    realized_pnl: f64,
    trades: u32,
    wins: u32,
    entry_time: i64,
    /// Cooldown after exit — prevents re-entering on the same flush signal
    /// (churn loop: exit → flush still in window → immediate re-buy → exit → ...).
    last_exit_time: i64,
    /// Real-clock startup time — used to skip the bar-replay warmup phase
    /// (same phantom-trade class as the trend replay bug). MR must not trade
    /// during the replay; it re-trades historical bars as if live.
    startup_time_ms: i64,
    /// Unified entry-suppression flag — set by `set_paused(true)` (C1) and by
    /// `force_flat()` (C2). Stops NEW flush entries but lets the active TP/SL
    /// exit logic keep running so a paused engine can still unwind. Cleared by
    /// `set_paused(false)` from `tick_strategies` when this engine becomes the
    /// active routing target on a non-flat decision.
    entries_suppressed: bool,
}

/// Persisted MR summary state (cumulative P&L across restarts).
#[derive(Serialize, Deserialize, Default)]
struct MrState {
    realized_pnl: f64,
    trades: u32,
    wins: u32,
}

impl MeanReversionStrategy {
    pub fn new(pair: &str, config: &MeanReversionConfig, telegram: TelegramBot) -> Self {
        let mut me = Self {
            pair: pair.to_string(),
            config: config.clone(),
            telegram,
            tick_history: VecDeque::with_capacity(1000),
            in_position: false,
            entry_price: 0.0,
            position_qty: 0.0,
            realized_pnl: 0.0,
            trades: 0,
            wins: 0,
            entry_time: 0,
            last_exit_time: 0,
            startup_time_ms: chrono::Utc::now().timestamp_millis(),
            entries_suppressed: false,
        };
        me.load_state();
        me
    }

    fn state_path(&self) -> std::path::PathBuf {
        std::path::PathBuf::from(format!("data/{}_mean_reversion_state.json", self.pair.replace("-", "_")))
    }

    fn load_state(&mut self) {
        if let Ok(content) = std::fs::read_to_string(self.state_path()) {
            if let Ok(s) = serde_json::from_str::<MrState>(&content) {
                self.realized_pnl = s.realized_pnl;
                self.trades = s.trades;
                self.wins = s.wins;
            }
        }
    }

    fn save_state(&self) {
        let path = self.state_path();
        if let Some(parent) = path.parent() { let _ = std::fs::create_dir_all(parent); }
        let state = MrState { realized_pnl: self.realized_pnl, trades: self.trades, wins: self.wins };
        let tmp = path.with_extension("json.tmp");
        if let Ok(json) = serde_json::to_string_pretty(&state) {
            if std::fs::write(&tmp, json).is_ok() { let _ = std::fs::rename(&tmp, &path); }
        }
    }

    /// Test hook: inject an open position to exercise TP/SL accounting.
    pub fn set_position_for_test(&mut self, entry: f64, qty: f64) {
        self.in_position = true;
        self.entry_price = entry;
        self.position_qty = qty;
    }

    fn calculate_bid_depth(bids: &[(f64, f64)], mid: f64, bps: f64) -> f64 {
        let threshold = mid * (1.0 - bps / 10000.0);
        bids.iter().filter(|(p, _)| *p >= threshold).map(|(_, q)| q).sum()
    }
}

#[async_trait]
impl Strategy for MeanReversionStrategy {
    fn name(&self) -> &str {
        "mean_reversion"
    }

    fn trading_pair(&self) -> &str {
        &self.pair
    }

    async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>> {
        // Skip bar-replay warmup: the engine replays cached historical bars on
        // every restart. Their timestamps are in the past (bar.timestamp), while
        // live ticks have timestamps within seconds of now. Skip any tick more
        // than 30s from real clock time — this prevents both phantom trades AND
        // tick_history pollution (replayed prices would seed fake flush signals).
        let now_ms = chrono::Utc::now().timestamp_millis();
        if (now_ms - ctx.timestamp).abs() > 30_000 {
            return Ok(Vec::new());
        }

        if !self.config.enabled {
            return Ok(Vec::new());
        }

        let mut orders = Vec::new();

        let mid = match ctx.order_book.mid_price() {
            Some(p) => p,
            None => return Ok(Vec::new()),
        };

        // 1. Maintain bounded history (keep last 30 seconds)
        let now = ctx.timestamp;
        let bid_depth = Self::calculate_bid_depth(&ctx.order_book.bids, mid, 50.0);
        self.tick_history.push_back(TickData { price: mid, timestamp: now, bid_depth });
        while let Some(front) = self.tick_history.front() {
            if now - front.timestamp > 30_000 {
                self.tick_history.pop_front();
            } else {
                break;
            }
        }

        let regime_safe = !self.config.regime_gate || ctx.regime != Some(MarketRegime::Trending);

        // C2: routing layer forced flat — close any open position at market
        // immediately. Entries are also suppressed, so we won't reopen. Mirrors
        // the strategy's TP/SL exit path (Market sell reduce-only + journal).
        if self.in_position && self.entries_suppressed {
            let pnl = (mid - self.entry_price) * self.position_qty;
            self.realized_pnl += pnl;
            self.trades += 1;
            if pnl > 0.0 { self.wins += 1; }
            warn!("[{}] MR force_flat @ {} | PnL: ${:+.2}", self.pair, mid, pnl);
            self.in_position = false;
            self.last_exit_time = now;
            self.save_state();
            crate::strategy::trade_journal::log_unified(
                "mr", &self.pair, Some("BUY"), Some(self.entry_price), Some(mid),
                Some(self.position_qty), pnl, Some("ForceFlat"),
                Some((now - self.entry_time) / 60_000),
            );
            orders.push(OrderRequest {
                symbol: self.pair.replace("-", ""), side: OrderSide::Sell,
                order_type: OrderTypeReq::Market, price: None, quantity: self.position_qty,
                time_in_force: None, client_order_id: Some(format!("mr_flat_{}", now)),
                reduce_only: true,
            });
            return Ok(orders);
        }

        // 2. Core Logic
        if !self.in_position && regime_safe && self.tick_history.len() > 10 && !self.entries_suppressed {
            // 60s cooldown after exit — prevents churn loop where the same flush
            // signal triggers immediate re-entry (buy → exit → buy → exit → ...).
            if now - self.last_exit_time < 60_000 {
                return Ok(orders);
            }
            let oldest = self.tick_history.front().unwrap();
            let drop_pct = (oldest.price - mid) / oldest.price;
            
            // Extreme drop trigger (-5% in 30s)
            if drop_pct > self.config.drop_thr {
                let lowest_price = self.tick_history.iter().map(|t| t.price).fold(f64::INFINITY, f64::min);
                let lowest_bid_depth = self.tick_history.iter().map(|t| t.bid_depth).fold(f64::INFINITY, f64::min);
                
                let retrace_frac = if oldest.price > lowest_price {
                    (mid - lowest_price) / (oldest.price - lowest_price)
                } else {
                    0.0
                };
                
                let sell_flow_decay = (drop_pct / 30.0) * 100.0;
                let liq_cascade_score = sell_flow_decay;
                
                let sig = ReversionSignal {
                    retrace_frac,
                    bid_refill_ratio: bid_depth / (lowest_bid_depth + 0.001),
                    sell_flow_decay,
                    liq_cascade_score,
                    cross_market_corr: if ctx.regime == Some(MarketRegime::Danger) { 0.8 } else { 0.2 },
                };

                if let Verdict::Trade { size_mult } = classify(&sig, &self.config.classifier) {
                    // Phase B2: cap base to available free capital (bounded cumulatively
                    // by capital.budgets.mean_reversion). Was hardcoded 100.0, which left
                    // MR sizing ~0 whenever grid had drained the shared pool.
                    let base = match &ctx.capital {
                        Some(cm) => cm.request_capital("mean_reversion", self.config.capital),
                        None => self.config.capital,
                    };
                    let qty = (base * size_mult) / mid;

                    // If the capital grant rounded to nothing (budget exhausted or
                    // pool dry), skip — don't open a 0-qty position that books $0
                    // on every TP/SL. Stays out of position this tick; retries next.
                    if qty <= 0.0 {
                        info!("[{}] MR skip: 0 capital granted (base={:.2})", self.pair, base);
                        return Ok(orders);
                    }

                    self.in_position = true;
                    self.entry_price = mid;
                    self.position_qty = qty;
                    self.entry_time = now;

                    info!("📉 MeanReversion Flush detected! Buying {:.4} @ {}", qty, mid);
                    // Fire-and-forget so Telegram latency can't stall the tick loop.
                    let tg = self.telegram.clone_for_signal();
                    let msg = format!("📉 MeanReversion Buying {:.4} @ {}", qty, mid);
                    tokio::spawn(async move { let _ = tg.send(&msg).await; });

                    orders.push(OrderRequest {
                        symbol: self.pair.replace("-", ""),
                        side: OrderSide::Buy,
                        order_type: OrderTypeReq::Limit,
                        price: Some(mid),
                        quantity: qty,
                        time_in_force: None,
                        client_order_id: Some(format!("mr_entry_{}", now)),
                        reduce_only: false,
                    });
                    
                    // Layer 1 Protective Stop (Exchange Backstop at -6%)
                    orders.push(OrderRequest {
                        symbol: self.pair.replace("-", ""),
                        side: OrderSide::Sell,
                        order_type: OrderTypeReq::StopMarket { stop_price: mid * 0.94 },
                        price: None,
                        quantity: qty,
                        time_in_force: None,
                        client_order_id: Some(format!("mr_stop_backstop_{}", now)),
                        reduce_only: true,
                    });
                }
            }
        } else if self.in_position {
            // Layer 2 Active Stop & Take profit
            if mid >= self.entry_price * (1.0 + self.config.tp_pct) {
                let pnl = (mid - self.entry_price) * self.position_qty;
                self.realized_pnl += pnl;
                self.trades += 1;
                if pnl > 0.0 { self.wins += 1; }
                info!("📈 MeanReversion TP hit @ {} | PnL: ${:+.2}", mid, pnl);
                self.in_position = false;
                self.last_exit_time = now;
                let pair = self.pair.clone();
                let tg = self.telegram.clone_for_signal();
                tokio::spawn(async move {
                    let _ = tg.send(&format!("📈 MR {} TP @ ${:.2} | PnL: ${:+.2}", pair, mid, pnl)).await;
                });
                self.save_state();
                crate::strategy::trade_journal::log_unified("mr", &self.pair, Some("BUY"), Some(self.entry_price), Some(mid), Some(self.position_qty), pnl, Some("TakeProfit"), Some((now - self.entry_time) / 60_000));
                orders.push(OrderRequest {
                    symbol: self.pair.replace("-", ""), side: OrderSide::Sell,
                    order_type: OrderTypeReq::Market, price: None, quantity: self.position_qty,
                    time_in_force: None, client_order_id: Some(format!("mr_exit_{}", now)),
                    reduce_only: true,
                });
            } else if mid <= self.entry_price * (1.0 - self.config.stop_pct) {
                let pnl = (mid - self.entry_price) * self.position_qty;
                self.realized_pnl += pnl;
                self.trades += 1;
                if pnl > 0.0 { self.wins += 1; }
                warn!("⚠️ MeanReversion SL hit @ {} | PnL: ${:+.2}", mid, pnl);
                self.in_position = false;
                self.last_exit_time = now;
                let pair = self.pair.clone();
                let running = self.realized_pnl;  // cumulative; already includes this SL (updated above)
                let tg = self.telegram.clone_for_signal();
                tokio::spawn(async move {
                    let _ = tg.send(&mr_sl_message(&pair, mid, pnl, running)).await;
                });
                self.save_state();
                crate::strategy::trade_journal::log_unified("mr", &self.pair, Some("BUY"), Some(self.entry_price), Some(mid), Some(self.position_qty), pnl, Some("StopLoss"), Some((now - self.entry_time) / 60_000));
                orders.push(OrderRequest {
                    symbol: self.pair.replace("-", ""), side: OrderSide::Sell,
                    order_type: OrderTypeReq::Market, price: None, quantity: self.position_qty,
                    time_in_force: None, client_order_id: Some(format!("mr_exit_{}", now)),
                    reduce_only: true,
                });
            }
        }

        Ok(orders)
    }

    /// Mean reversion places NO resting protective orders — exits are decided in
    /// `on_tick` (Layer 2: +2% take-profit / -4% stop) and emitted as MARKET
    /// sells, the same pattern `trend.rs` uses for all protection.
    ///
    /// This was previously a "Layer 1 protective backstop": a LIMIT SELL placed
    /// ~7% below entry from `on_fill`. That was a latent footgun — a sell limit
    /// fills whenever market >= limit (paper.rs), so it would exit at the limit
    /// price (~-7%) on the first fill-check rather than act as a stop. The
    /// engine also makes `on_fill` unreliable for state changes: fills are
    /// dispatched to *every* strategy sharing a symbol, and the paper engine
    /// sets `fill.order_id` to its own "paper_N" id (not the client_order_id),
    /// so a fill can't be unambiguously attributed to this strategy. Position
    /// state is therefore authoritative in `on_tick`, which sets it
    /// optimistically on entry (MARKET) and clears it on exit (MARKET).
    async fn on_fill(&mut self, _fill: &Fill) -> Result<Vec<OrderRequest>> {
        Ok(Vec::new())
    }

    async fn on_start(&mut self) -> Result<Vec<OrderRequest>> {
        // Seed from the unified trades table (source of truth) — overrides any
        // phantom P&L left in the JSON state file by the bar-replay bug.
        let unified_pnl = crate::strategy::trade_journal::realized_pnl("mr", &self.pair);
        if (unified_pnl - self.realized_pnl).abs() > 0.01 {
            info!("MR {} state reconciliation: resetting realized_pnl ${:.2} → ${:.2} (from trades.db)",
                  self.pair, self.realized_pnl, unified_pnl);
            self.realized_pnl = unified_pnl;
            self.trades = 0;
            self.wins = 0;
            self.save_state();
        }
        Ok(Vec::new())
    }
    async fn on_stop(&mut self) -> Result<()> { Ok(()) }

    fn status(&self) -> StrategyStatus {
        let win_rate = if self.trades > 0 { self.wins as f64 / self.trades as f64 * 100.0 } else { 0.0 };
        StrategyStatus {
            name: self.name().to_string(),
            pair: self.pair.clone(),
            state: if self.in_position { "IN TRADE".into() } else { "Scanning".into() },
            pnl: self.realized_pnl,
            open_orders: 0,
            details: format!(
                "Trades: {} | Wins: {} ({:.0}%) | TP +{:.0}% SL -{:.0}% | Buf: {}",
                self.trades, self.wins, win_rate,
                self.config.tp_pct * 100.0, self.config.stop_pct * 100.0,
                self.tick_history.len()
            ),
        }
    }

    fn realized_pnl(&self) -> f64 { self.realized_pnl }
    fn deployed_capital(&self) -> f64 { if self.in_position { self.position_qty * self.entry_price } else { 0.0 } }

    /// C1: suppress new flush entries while the TP/SL exit logic keeps running.
    /// The flag is the same one `force_flat` sets.
    fn set_paused(&mut self, paused: bool) {
        self.entries_suppressed = paused;
    }

    /// C2: MR places no resting orders (exits are reactive in on_tick), so the
    /// close happens on the next on_tick via the ForceFlat branch. Just flip the
    /// suppression flag here.
    fn force_flat(&mut self) {
        self.entries_suppressed = true;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::MeanReversionConfig;
    use crate::connector::types::{OrderBook, OrderRequest};

    fn mr_cfg() -> MeanReversionConfig {
        let mut c = MeanReversionConfig::default();
        c.enabled = true;
        c
    }

    fn tick_at(price: f64, ts: i64) -> TickContext {
        TickContext {
            order_book: OrderBook {
                symbol: "BTC-USDT".to_string(),
                bids: vec![(price - 1.0, 10.0)],
                asks: vec![(price + 1.0, 10.0)],
                timestamp: ts,
            },
            recent_bars: Vec::new(),
            balances: std::collections::HashMap::new(),
            open_orders: Vec::new(),
            regime: None,
            regime_confidence: 0.0,
            timestamp: ts,
            capital: None,
            replay: false,
        }
    }

    fn run_tick(s: &mut MeanReversionStrategy, ctx: TickContext) -> Vec<OrderRequest> {
        let rt = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap();
        rt.block_on(s.on_tick(&ctx)).unwrap()
    }

    /// C1: set_paused toggles the unified entries_suppressed flag.
    #[test]
    fn set_paused_toggles_entries_suppressed() {
        let mut s = MeanReversionStrategy::new("BTC-USDT", &mr_cfg(), TelegramBot::new("", ""));
        assert!(!s.entries_suppressed);
        s.set_paused(true);
        assert!(s.entries_suppressed);
        s.set_paused(false);
        assert!(!s.entries_suppressed);
    }

    /// C2: force_flat on an open position emits a Market reduce-only sell,
    /// books P&L, clears the position, and suppresses re-entry.
    #[test]
    fn force_flat_closes_open_position_and_suppresses_reentry() {
        let mut s = MeanReversionStrategy::new("BTC-USDT", &mr_cfg(), TelegramBot::new("", ""));
        s.set_position_for_test(100.0, 1.0); // entry 100, qty 1
        let realized_before = s.realized_pnl;

        s.force_flat();
        assert!(s.entries_suppressed);

        // Tick at 105 (above entry) → close realizes +5 (long).
        let now = chrono::Utc::now().timestamp_millis();
        let orders = run_tick(&mut s, tick_at(105.0, now));
        assert!(!s.in_position, "position cleared by force_flat");
        assert!((s.realized_pnl - realized_before - 5.0).abs() < 1e-9,
            "force_flat booked +5 P&L: got {}", s.realized_pnl - realized_before);
        assert_eq!(orders.len(), 1, "one Market close order");
        assert_eq!(orders[0].side, OrderSide::Sell);
        assert!(orders[0].reduce_only);
        assert_eq!(orders[0].order_type, OrderTypeReq::Market);

        // A flush at the same tick can't easily be re-triggered without history,
        // but verify the suppression flag persists so on_tick's entry gate blocks.
        assert!(s.entries_suppressed, "still suppressed after the close");
    }
}
