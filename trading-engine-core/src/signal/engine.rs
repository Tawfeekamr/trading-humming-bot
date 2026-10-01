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
        // This mirror must NOT manage positions: the Python listener is the sole
        // manager AND sole exchange executor. Both loops ran snapshot → HTTP
        // price fetch → decide/close against the shared signal_positions.json,
        // so whenever one closed during the other's fetch window the stale
        // snapshot closed it AGAIN — duplicate CLOSE rows in signal_journal.db
        // and ghost exit prices in trades.db (2026-09/10 audit). Default-off
        // via signal_copy.manage_positions; kept switchable for paper
        // experiments where the Python listener is absent.
        if !self.config.manage_positions { return; }

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
                let is_short = pos.side == "short";

                // Stop-loss check (direction-aware)
                let sl_hit = if is_short {
                    current_price >= pos.stop_loss
                } else {
                    current_price <= pos.stop_loss
                };
                if sl_hit {
                    let pnl = mgr.close_position(&pos.symbol, current_price, "stop_loss");
                    notifications.push(format!("[{}] 🛑 SL hit: {} @ ${:.2}, PnL: ${:.2}", mode, pos.symbol, current_price, pnl.unwrap_or(0.0)));
                    closes.push((pos.clone(), current_price, "stop_loss".to_string(), pnl));
                    continue;
                }

                // TP hit helper: direction-aware price comparison
                let tp_hit = |tp_price: f64| -> bool {
                    if is_short { current_price <= tp_price } else { current_price >= tp_price }
                };

                // TP1 hit
                if !pos.tp1_hit && !pos.take_profits.is_empty() && tp_hit(pos.take_profits[0]) {
                    mgr.get_position_mut(&pos.symbol).map(|p| p.tp1_hit = true);
                    mgr.partial_close(&pos.symbol, pos.tp1_close_pct, pos.take_profits[0], "tp1");
                    mgr.update_stop_loss(&pos.symbol, pos.entry_price); // Move SL to breakeven
                    notifications.push(format!("[{}] TP1 hit: {} @ ${:.2}, SL → breakeven", mode, pos.symbol, pos.take_profits[0]));
                }

                // TP2 hit
                if !pos.tp2_hit && pos.take_profits.len() >= 2 && tp_hit(pos.take_profits[1]) {
                    mgr.get_position_mut(&pos.symbol).map(|p| p.tp2_hit = true);
                    mgr.partial_close(&pos.symbol, pos.tp2_close_pct, pos.take_profits[1], "tp2");
                    mgr.update_stop_loss(&pos.symbol, pos.take_profits[0]); // Move SL to TP1
                    notifications.push(format!("[{}] TP2 hit: {} @ ${:.2}", mode, pos.symbol, pos.take_profits[1]));
                }

                // TP3 hit — start trailing runner instead of full close
                if !pos.tp3_hit && pos.take_profits.len() >= 3 && tp_hit(pos.take_profits[2]) {
                    mgr.get_position_mut(&pos.symbol).map(|p| p.tp3_hit = true);
                    // Move SL to TP3 (breakeven for the runner) — don't close.
                    mgr.update_stop_loss(&pos.symbol, pos.take_profits[2]);
                    notifications.push(format!("[{}] TP3 hit: {} @ ${:.2}, SL → TP3 (trailing runner)", mode, pos.symbol, pos.take_profits[2]));
                }

                // Trailing runner after TP3: ratchet SL toward remaining TPs
                if pos.tp3_hit && pos.take_profits.len() > 3 {
                    let next_tp_idx = if is_short {
                        // Short: find next TP BELOW current SL (ratchet downward)
                        (3..pos.take_profits.len())
                            .find(|&i| pos.take_profits[i] < pos.stop_loss)
                            .unwrap_or(pos.take_profits.len() - 1)
                    } else {
                        // Long: find next TP ABOVE current SL (ratchet upward)
                        (3..pos.take_profits.len())
                            .find(|&i| pos.take_profits[i] > pos.stop_loss)
                            .unwrap_or(pos.take_profits.len() - 1)
                    };
                    let next_tp = pos.take_profits[next_tp_idx];
                    let should_trail = if is_short {
                        current_price <= next_tp && next_tp < pos.stop_loss
                    } else {
                        current_price >= next_tp && next_tp > pos.stop_loss
                    };
                    if should_trail {
                        let new_sl = (next_tp + pos.stop_loss) / 2.0;
                        mgr.update_stop_loss(&pos.symbol, new_sl);
                        notifications.push(format!("[{}] Trail ratchet: {} SL → ${:.2} (approaching TP{})", mode, pos.symbol, new_sl, next_tp_idx + 1));
                    }
                }
            }
        } // position lock released
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
        // Full audit context: real SL, TP1, the signal's reasoning, and the
        // complete TP ladder + hit flags + raw channel message in context_json.
        let sig_qty = pos.remaining_amount();
        let sig_risk = (pos.entry_price - pos.stop_loss).abs() * sig_qty;
        let sig_ctx = crate::strategy::trade_journal::TradeContext {
            sl_price: Some(pos.stop_loss),
            tp_price: pos.take_profits.first().copied(),
            entry_reason: Some(pos.signal_confidence.clone()),
            r_multiple: if sig_risk > 0.0 { Some(pnl.unwrap_or(0.0) / sig_risk) } else { None },
            context_json: Some(serde_json::json!({
                "take_profits": pos.take_profits,
                "tp_hits": [pos.tp1_hit, pos.tp2_hit, pos.tp3_hit],
                "channel": pos.channel_name,
                "raw_message": pos.raw_message.chars().take(500).collect::<String>(),
            }).to_string()),
            ..Default::default()
        };
        crate::strategy::trade_journal::log_unified(
            "signal", &pos.symbol, Some("BUY"), Some(pos.entry_price), Some(price),
            Some(sig_qty), pnl.unwrap_or(0.0), Some(reason), None, &sig_ctx,
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::SignalConfig;
    use crate::connector::types::*;
    use crate::connector::Connector;
    use async_trait::async_trait;
    use std::collections::HashMap;

    fn config(manage_positions: bool) -> SignalConfig {
        SignalConfig {
            enabled: true,
            manage_positions,
            audit_mode: false,
            ai_model: "test".to_string(),
            // High cap: the manager loads cwd data/signal_positions.json at
            // construction, which locally carries stale opens leaked by months
            // of position.rs test runs (CI is ephemeral and starts clean).
            max_positions: 200,
            per_trade_risk_pct: 3.0,
            capital_pct: 10.0,
            max_capital_usdt: 1000.0,
            min_rr_ratio: 0.0,
            max_sl_distance_pct: 10.0,
            default_sl_atr_multiplier: 2.0,
            max_entry_zone_pct: 3.0,
            min_quality_score: 5,
            tp1_close_pct: 33.0,
            tp2_close_pct: 50.0,
            daily_loss_limit_pct: 5.0,
            max_trades_per_day: 10,
            cooldown_minutes: 5,
            use_btc_correlation_gate: false,
            blacklisted_pairs: Vec::new(),
            session_name: "test".to_string(),
        }
    }

    /// Connector stub whose order book always mids at `mid`. Only get_order_book
    /// is implemented — a manage pass that respects the manage_positions=false
    /// gate never reaches any other method (they'd panic).
    struct StubBook { mid: f64 }

    #[async_trait]
    impl Connector for StubBook {
        async fn place_order(&self, _req: &OrderRequest) -> anyhow::Result<OrderResponse> { unimplemented!() }
        async fn cancel_order(&self, _symbol: &str, _order_id: &str) -> anyhow::Result<()> { unimplemented!() }
        async fn cancel_all_orders(&self, _symbol: &str) -> anyhow::Result<Vec<CancelResult>> { unimplemented!() }
        async fn get_balances(&self) -> anyhow::Result<HashMap<String, f64>> { unimplemented!() }
        async fn get_open_orders(&self, _symbol: &str) -> anyhow::Result<Vec<OpenOrder>> { unimplemented!() }
        async fn get_order_book(&self, symbol: &str, _limit: u16) -> anyhow::Result<OrderBook> {
            Ok(OrderBook {
                symbol: symbol.to_string(),
                bids: vec![(self.mid - 0.5, 10.0)],
                asks: vec![(self.mid + 0.5, 10.0)],
                timestamp: 0,
            })
        }
        async fn get_klines(&self, _symbol: &str, _interval: &str, _limit: u16)
            -> anyhow::Result<Vec<crate::models::bar::Bar>> { unimplemented!() }
    }

    fn unique_symbol(prefix: &str) -> String {
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap_or_default()
            .subsec_nanos();
        format!("{}{}-USDT", prefix, nanos)
    }

    /// Open a long (entry 100, SL 95) whose stop is breached by the stub mid.
    async fn open_breached_position(engine: &SignalEngine, symbol: &str) {
        let mut mgr = engine.position_mgr().await;
        mgr.open_position(symbol, 100.0, 1.0, 95.0, vec![105.0, 110.0, 115.0],
                          "high", "test", "test", "long")
            .expect("open_position");
    }

    /// Best-effort removal of this test's entry from the shared state file so
    /// repeated local runs don't accumulate (position.rs tests already leak).
    fn remove_from_state_file(symbol: &str) {
        let path = std::path::Path::new("data/signal_positions.json");
        let Ok(file) = std::fs::File::open(path) else { return };
        let Ok(mut disk) = serde_json::from_reader::<_, serde_json::Value>(file) else { return };
        if let Some(map) = disk.as_object_mut() {
            if map.remove(symbol).is_some() {
                let _ = std::fs::write(path, serde_json::to_string_pretty(&disk).unwrap_or_default());
            }
        }
    }

    #[tokio::test]
    async fn manage_disabled_leaves_breached_position_open() {
        let engine = SignalEngine::new(&config(false), None);
        let symbol = unique_symbol("NOOP");
        open_breached_position(&engine, &symbol).await;

        engine.manage_positions(&StubBook { mid: 90.0 }).await; // 90 <= SL 95

        let mgr = engine.position_mgr().await;
        let still_open = mgr.get_position(&symbol).is_some();
        drop(mgr);
        remove_from_state_file(&symbol);
        assert!(still_open,
                "manage_positions=false must leave even an SL-breached position untouched — \
                 the Python listener is the sole manager");
    }

    #[tokio::test]
    async fn manage_enabled_closes_breached_position() {
        let engine = SignalEngine::new(&config(true), None);
        let symbol = unique_symbol("SL");
        open_breached_position(&engine, &symbol).await;

        engine.manage_positions(&StubBook { mid: 90.0 }).await;

        let mgr = engine.position_mgr().await;
        let still_open = mgr.get_position(&symbol).is_some();
        drop(mgr);
        remove_from_state_file(&symbol);
        assert!(!still_open,
                "manage_positions=true must close the SL-breach (control test for the stub)");
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
            manage_positions: self.manage_positions,
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
