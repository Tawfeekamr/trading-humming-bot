use crate::config::{MeanReversionConfig, ClassifierCfg};
use crate::strategy::{Strategy, TickContext, StrategyStatus, MarketRegime};
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

        // 2. Core Logic
        if !self.in_position && regime_safe && self.tick_history.len() > 10 {
            let oldest = self.tick_history.front().unwrap();
            let drop_pct = (oldest.price - mid) / oldest.price;
            
            // Extreme drop trigger (-5% in 30s)
            if drop_pct > self.config.drop_thr {
                let sig = ReversionSignal {
                    retrace_frac: 0.8,
                    bid_refill_ratio: bid_depth / (oldest.bid_depth + 0.001),
                    sell_flow_decay: 0.8,
                    liq_cascade_score: 0.8,
                    cross_market_corr: 0.2, // low correlation is good
                };

                if let Verdict::Trade { size_mult } = classify(&sig, &self.config.classifier) {
                    let qty = (100.0 * size_mult) / mid; // 100 USDT base allocation
                    
                    self.in_position = true;
                    self.entry_price = mid;
                    self.position_qty = qty;

                    info!("📉 MeanReversion Flush detected! Buying {:.4} @ {}", qty, mid);
                    // Fire-and-forget so Telegram latency can't stall the tick loop.
                    let tg = self.telegram.clone_for_signal();
                    let msg = format!("📉 MeanReversion Buying {:.4} @ {}", qty, mid);
                    tokio::spawn(async move { let _ = tg.send(&msg).await; });

                    orders.push(OrderRequest {
                        symbol: self.pair.replace("-", ""),
                        side: OrderSide::Buy,
                        order_type: OrderTypeReq::Market,
                        price: None,
                        quantity: qty,
                        time_in_force: None,
                        client_order_id: Some(format!("mr_entry_{}", now)),
                        reduce_only: false,
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
                let pair = self.pair.clone();
                let tg = self.telegram.clone_for_signal();
                tokio::spawn(async move {
                    let _ = tg.send(&format!("📈 MR {} TP @ ${:.2} | PnL: ${:+.2}", pair, mid, pnl)).await;
                });
                self.save_state();
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
                let pair = self.pair.clone();
                let tg = self.telegram.clone_for_signal();
                tokio::spawn(async move {
                    let _ = tg.send(&format!("⚠️ MR {} SL @ ${:.2} | PnL: ${:+.2}", pair, mid, pnl)).await;
                });
                self.save_state();
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

    async fn on_start(&mut self) -> Result<Vec<OrderRequest>> { Ok(Vec::new()) }
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
}
