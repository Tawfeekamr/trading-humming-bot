use crate::config::SignalConfig;
use crate::connector::Connector;
use crate::signal::types::*;
use crate::signal::risk::SignalRiskGuard;
use crate::signal::position::SignalPositionManager;
use crate::signal::journal::SignalJournal;
use crate::notifications::TelegramBot;
use chrono::Utc;
use std::sync::Arc;
use tokio::sync::Mutex;
use tracing::{info, warn, error};

pub struct SignalEngine {
    config: SignalConfig,
    enabled: bool,
    manual_pause: bool,
    risk: Arc<Mutex<SignalRiskGuard>>,
    position_mgr: Arc<Mutex<SignalPositionManager>>,
    journal: Arc<SignalJournal>,
    telegram: Option<TelegramBot>,
}

impl SignalEngine {
    pub fn new(config: &SignalConfig, telegram: Option<TelegramBot>) -> Self {
        let risk = SignalRiskGuard::new(config);
        let mut position_mgr = SignalPositionManager::new(config);

        let journal = match SignalJournal::new() {
            Ok(j) => Arc::new(j),
            Err(e) => {
                error!("Failed to create signal journal: {}", e);
                // Will panic if we try to use it — but engine should be disabled
                panic!("Signal journal creation failed");
            }
        };

        // Self-heal: mark any open position closed if the journal already closed
        // it. Clears stale-open positions left by the Rust/Python dual-write era so
        // they aren't re-managed / re-closed (the duplicate-close bug).
        if let Ok(closed) = journal.closed_entries() {
            position_mgr.reconcile_closed(&closed);
        }

        Self {
            enabled: config.enabled,
            manual_pause: false,
            config: config.clone(),
            risk: Arc::new(Mutex::new(risk)),
            position_mgr: Arc::new(Mutex::new(position_mgr)),
            journal,
            telegram,
        }
    }

    pub fn is_enabled(&self) -> bool {
        self.enabled
    }

    /// Access position manager for Telegram commands
    pub async fn position_mgr(&self) -> tokio::sync::MutexGuard<'_, SignalPositionManager> {
        self.position_mgr.lock().await
    }

    /// Access journal for Telegram commands
    pub fn journal(&self) -> &SignalJournal {
        &self.journal
    }

    pub fn pause(&mut self) {
        self.manual_pause = true;
        info!("Signal engine paused");
    }

    pub fn resume(&mut self) {
        self.manual_pause = false;
        info!("Signal engine resumed");
    }

    /// Manage open positions (SL/TP checks). Call on every tick.
    pub async fn manage_positions(&self, connector: &dyn Connector) {
        if !self.enabled || self.manual_pause { return; }

        // Snapshot positions under a brief lock, then RELEASE. We must not hold the
        // position lock across the per-pair HTTP price fetches (or the telegram /
        // journal side-effects below) — doing so stalls all position management on
        // network I/O. (#3 of the concurrency audit.)
        let positions: Vec<SignalPosition> = {
            let mut mgr = self.position_mgr.lock().await;
            mgr.reload_state(); // Pick up new positions opened by Python since last tick
            mgr.get_open_positions().into_iter().cloned().collect()
        };
        if positions.is_empty() { return; }

        // Fetch each position's price WITHOUT the position lock held.
        let mut prices: std::collections::HashMap<String, f64> = std::collections::HashMap::new();
        for pos in &positions {
            prices.insert(pos.symbol.clone(), self.get_current_price(connector, &pos.symbol).await);
        }

        // Apply decisions under the lock; collect side-effects to run after release.
        let mode = if self.config.audit_mode { "AUDIT" } else { "LIVE" };
        let mut notifications: Vec<String> = Vec::new();
        let mut closes: Vec<(SignalPosition, f64, String, Option<f64>)> = Vec::new();
        {
            let mut mgr = self.position_mgr.lock().await;
            for pos in &positions {
                let current_price = match prices.get(&pos.symbol) { Some(p) => *p, None => continue };
                if current_price <= 0.0 { continue; }

                // Stop-loss check
                if current_price <= pos.stop_loss {
                    let pnl = mgr.close_position(&pos.symbol, current_price, "stop_loss");
                    notifications.push(format!("[{}] 🛑 SL hit: {} @ ${:.2}, PnL: ${:.2}", mode, pos.symbol, current_price, pnl.unwrap_or(0.0)));
                    closes.push((pos.clone(), current_price, "stop_loss".to_string(), pnl));
                    continue;
                }

                // TP1 hit
                if !pos.tp1_hit && !pos.take_profits.is_empty() && current_price >= pos.take_profits[0] {
                    mgr.get_position_mut(&pos.symbol).map(|p| p.tp1_hit = true);
                    mgr.partial_close(&pos.symbol, pos.tp1_close_pct, pos.take_profits[0], "tp1");
                    mgr.update_stop_loss(&pos.symbol, pos.entry_price); // Move SL to breakeven
                    notifications.push(format!("[{}] TP1 hit: {} @ ${:.2}, SL → breakeven", mode, pos.symbol, pos.take_profits[0]));
                }

                // TP2 hit
                if !pos.tp2_hit && pos.take_profits.len() >= 2 && current_price >= pos.take_profits[1] {
                    mgr.get_position_mut(&pos.symbol).map(|p| p.tp2_hit = true);
                    mgr.partial_close(&pos.symbol, pos.tp2_close_pct, pos.take_profits[1], "tp2");
                    mgr.update_stop_loss(&pos.symbol, pos.take_profits[0]); // Move SL to TP1
                    notifications.push(format!("[{}] TP2 hit: {} @ ${:.2}", mode, pos.symbol, pos.take_profits[1]));
                }

                // TP3 hit — start trailing runner instead of full close
                if !pos.tp3_hit && pos.take_profits.len() >= 3 && current_price >= pos.take_profits[2] {
                    mgr.get_position_mut(&pos.symbol).map(|p| p.tp3_hit = true);
                    // Move SL to TP3 (breakeven for the runner) — don't close.
                    // The remaining position trails upward, capturing TPs 4-8.
                    mgr.update_stop_loss(&pos.symbol, pos.take_profits[2]);
                    notifications.push(format!("[{}] TP3 hit: {} @ ${:.2}, SL → TP3 (trailing runner for TPs 4+)", mode, pos.symbol, pos.take_profits[2]));
                }

                // Trailing runner after TP3: ratchet SL upward toward remaining TPs
                if pos.tp3_hit && pos.take_profits.len() > 3 {
                    // Find the next un-hit TP level above current price
                    let next_tp_idx = (3..pos.take_profits.len())
                        .find(|&i| pos.take_profits[i] > pos.stop_loss)
                        .unwrap_or(pos.take_profits.len() - 1);
                    let next_tp = pos.take_profits[next_tp_idx];
                    // Trail: when price reaches the next TP, ratchet SL up to halfway
                    if current_price >= next_tp && next_tp > pos.stop_loss {
                        let new_sl = (next_tp + pos.stop_loss) / 2.0;
                        mgr.update_stop_loss(&pos.symbol, new_sl);
                        notifications.push(format!("[{}] Trail ratchet: {} SL → ${:.2} (approaching TP{})", mode, pos.symbol, new_sl, next_tp_idx + 1));
                    }
                }
            }
        } // position lock released

        // Side-effects (telegram + journal + unified-journal) run WITHOUT the lock.
        for msg in &notifications {
            self.notify(msg).await;
        }
        for (pos, price, reason, pnl) in &closes {
            self.record_close(pos, *price, reason, *pnl).await;
        }
    }

    /// Get engine status for Telegram commands
    pub async fn get_status(&self) -> SignalEngineStatus {
        let risk_status = self.risk.lock().await.get_status();
        let open_count = self.position_mgr.lock().await.get_open_positions().len();
        SignalEngineStatus {
            state: if !self.enabled { "DISABLED".to_string() }
                    else if self.manual_pause { "PAUSED".to_string() }
                    else { "LISTENING".to_string() },
            audit_mode: self.config.audit_mode,
            open_positions: open_count,
            trades_today: risk_status.trades_today,
            max_trades: risk_status.max_trades,
            daily_pnl: risk_status.daily_pnl,
            halted: risk_status.halted,
        }
    }

    pub async fn manual_close(&self, symbol: &str) -> bool {
        self.position_mgr.lock().await.close_position(symbol, 0.0, "manual").is_some()
    }

    async fn get_current_price(&self, connector: &dyn Connector, symbol: &str) -> f64 {
        // Try connector first
        if let Ok(book) = connector.get_order_book(symbol, 1).await {
            if let Some(mid) = book.mid_price() {
                return mid;
            }
        }

        // Gate.io fallback
        let gate_pair = symbol.replace("-", "_");
        let url = format!("https://api.gateio.ws/api/v4/spot/tickers?currency_pair={}", gate_pair);
        match reqwest::get(&url).await {
            Ok(resp) => {
                if let Ok(data) = resp.json::<Vec<serde_json::Value>>().await {
                    if let Some(first) = data.first() {
                        if let Some(last) = first["last"].as_str() {
                            if let Ok(price) = last.parse::<f64>() {
                                return price;
                            }
                        }
                    }
                }
            }
            Err(e) => warn!("Gate.io price fallback failed for {}: {}", symbol, e),
        }
        0.0
    }

    async fn record_close(&self, pos: &SignalPosition, price: f64, reason: &str, pnl: Option<f64>) {
        if let Some(pnl_val) = pnl {
            self.risk.lock().await.record_trade_closed(pnl_val);
        }
        self.journal.log_trade(&SignalTrade {
            timestamp: Utc::now().to_rfc3339(),
            symbol: pos.symbol.clone(),
            channel_name: pos.channel_name.clone(),
            action: format!("CLOSE_{}", reason),
            entry_price: pos.entry_price,
            current_price: price,
            quantity: pos.remaining_amount(),
            realized_pnl: pnl.unwrap_or(0.0),
            exit_reason: reason.to_string(),
            signal_confidence: pos.signal_confidence.clone(),
            stop_loss: pos.stop_loss,
            take_profits: serde_json::to_string(&pos.take_profits).unwrap_or_default(),
            tp1_hit: pos.tp1_hit as i32,
            tp2_hit: pos.tp2_hit as i32,
            tp3_hit: pos.tp3_hit as i32,
            raw_message: pos.raw_message.chars().take(500).collect(),
            parse_reasoning: String::new(),
            is_audit: if self.config.audit_mode { 1 } else { 0 },
        });
        crate::strategy::trade_journal::log_unified(
            "signal", &pos.symbol, Some(pos.entry_price), Some(price),
            Some(pos.remaining_amount()), pnl.unwrap_or(0.0), Some(reason), None,
        );
        self.notify(&format!(
            "[{}] Closed: {} ({}) @ ${:.2}, PnL: ${:.2}",
            if self.config.audit_mode { "AUDIT" } else { "LIVE" },
            pos.symbol, reason, price, pnl.unwrap_or(0.0)
        )).await;
    }

    async fn notify(&self, message: &str) {
        if let Some(ref tg) = self.telegram {
            let _ = tg.send(message).await;
        }
    }
}

pub struct SignalEngineStatus {
    pub state: String,
    pub audit_mode: bool,
    pub open_positions: usize,
    pub trades_today: u32,
    pub max_trades: u32,
    pub daily_pnl: f64,
    pub halted: bool,
}

impl Clone for SignalConfig {
    fn clone(&self) -> Self {
        Self {
            enabled: self.enabled,
            audit_mode: self.audit_mode,
            ai_model: self.ai_model.clone(),
            max_positions: self.max_positions,
            per_trade_risk_pct: self.per_trade_risk_pct,
            capital_pct: self.capital_pct,
            max_capital_usdt: self.max_capital_usdt,
            min_rr_ratio: self.min_rr_ratio,
            max_sl_distance_pct: self.max_sl_distance_pct,
            default_sl_atr_multiplier: self.default_sl_atr_multiplier,
            max_entry_zone_pct: self.max_entry_zone_pct,
            min_quality_score: self.min_quality_score,
            tp1_close_pct: self.tp1_close_pct,
            tp2_close_pct: self.tp2_close_pct,
            daily_loss_limit_pct: self.daily_loss_limit_pct,
            max_trades_per_day: self.max_trades_per_day,
            cooldown_minutes: self.cooldown_minutes,
            use_btc_correlation_gate: self.use_btc_correlation_gate,
            blacklisted_pairs: self.blacklisted_pairs.clone(),
            session_name: self.session_name.clone(),
        }
    }
}
