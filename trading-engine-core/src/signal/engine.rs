use crate::config::SignalConfig;
use crate::connector::Connector;
use crate::connector::types::OrderRequest;
use crate::models::order::OrderSide;
use crate::signal::types::*;
use crate::signal::parser::SignalParser;
use crate::signal::validator::SignalValidator;
use crate::signal::risk::SignalRiskGuard;
use crate::signal::position::SignalPositionManager;
use crate::signal::journal::SignalJournal;
use crate::notifications::TelegramBot;
use anyhow::Result;
use chrono::Utc;
use std::collections::HashSet;
use std::sync::Arc;
use tokio::sync::Mutex;
use tracing::{info, warn, error};

pub struct SignalEngine {
    config: SignalConfig,
    enabled: bool,
    manual_pause: bool,
    parser: SignalParser,
    validator: Arc<Mutex<SignalValidator>>,
    risk: Arc<Mutex<SignalRiskGuard>>,
    position_mgr: Arc<Mutex<SignalPositionManager>>,
    journal: Arc<SignalJournal>,
    telegram: Option<TelegramBot>,
    available_pairs: Arc<Mutex<HashSet<String>>>,
}

impl SignalEngine {
    pub fn new(config: &SignalConfig, telegram: Option<TelegramBot>) -> Self {
        let api_key = std::env::var("DEEPSEEK_API_KEY").unwrap_or_default();
        let parser = SignalParser::new(&api_key, &config.ai_model);
        let validator = SignalValidator::new(config);
        let risk = SignalRiskGuard::new(config);
        let position_mgr = SignalPositionManager::new(config);

        let journal = match SignalJournal::new() {
            Ok(j) => Arc::new(j),
            Err(e) => {
                error!("Failed to create signal journal: {}", e);
                // Will panic if we try to use it — but engine should be disabled
                panic!("Signal journal creation failed");
            }
        };

        Self {
            enabled: config.enabled,
            manual_pause: false,
            config: config.clone(),
            parser,
            validator: Arc::new(Mutex::new(validator)),
            risk: Arc::new(Mutex::new(risk)),
            position_mgr: Arc::new(Mutex::new(position_mgr)),
            journal,
            telegram,
            available_pairs: Arc::new(Mutex::new(HashSet::new())),
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

    /// Process a message from the channel listener
    pub async fn process_message(&self, msg: &ChannelMessage) {
        if !self.enabled || self.manual_pause { return; }

        let signal = self.parser.parse(&msg.text).await;

        info!("Signal parsed: action={}, pair={}, reasoning={:.80}",
            signal.action, signal.pair.as_deref().unwrap_or("none"), signal.reasoning);

        // Log raw message
        self.journal.log_raw_message(
            msg.channel_id,
            &msg.channel_name,
            msg.message_id,
            &msg.text,
            &signal.action,
            signal.pair.as_deref().unwrap_or(""),
            &signal.reasoning,
        );

        let action = signal.signal_action();
        if action == SignalAction::NotASignal { return; }

        // Handle CLOSE signals
        if action == SignalAction::Close {
            self.handle_close(&signal, &msg.channel_name).await;
            return;
        }

        // Handle UPDATE signals
        if action == SignalAction::UpdateSl {
            if let (Some(pair), Some(sl)) = (&signal.pair, signal.stop_loss) {
                self.position_mgr.lock().await.update_stop_loss(pair, sl);
                info!("Signal SL updated: {} → ${:.2}", pair, sl);
            }
            return;
        }

        if action != SignalAction::OpenLong { return; }

        // Fill missing stop-loss
        if signal.stop_loss.is_none() {
            // Would need ATR fetch — simplified for now
            let entry = signal.entry_high.unwrap_or(signal.entry_low.unwrap_or(0.0));
            if entry > 0.0 {
                let default_sl = entry * 0.95; // 5% default SL
                warn!("No SL for {} — using default 5%: ${:.2}", signal.pair.as_deref().unwrap_or("?"), default_sl);
                // Create a mutable copy is tricky — we log and continue
                self.notify(&format!(
                    "⚠️ No SL for {} — using default 5%",
                    signal.pair.as_deref().unwrap_or("?")
                )).await;
            }
        }

        // Validate
        let (valid, reason) = self.validator.lock().await.validate(&signal);
        if !valid {
            info!("Signal rejected ({}): {}", msg.channel_name, reason);
            self.notify(&format!("Signal rejected: {} — {}", signal.pair.as_deref().unwrap_or("?"), reason)).await;
            self.log_audit_trade(&signal, &msg.channel_name, "rejected", 0.0, &reason);
            return;
        }

        // Risk checks
        if !self.risk.lock().await.can_trade() {
            info!("Signal blocked by risk guard");
            self.log_audit_trade(&signal, &msg.channel_name, "blocked_risk", 0.0, "risk_limit");
            return;
        }

        // Execute
        if self.config.audit_mode {
            self.simulate_entry(&signal, &msg.channel_name).await;
        }
        // Live execution would be wired through connector — TODO in engine integration
    }

    /// Manage open positions (SL/TP checks). Call on every tick.
    pub async fn manage_positions(&self, connector: &dyn Connector) {
        if !self.enabled || self.manual_pause { return; }

        let mut mgr = self.position_mgr.lock().await;
        mgr.reload_state(); // Pick up new positions opened by Python since last tick
        let positions: Vec<SignalPosition> = mgr.get_open_positions().into_iter().cloned().collect();

        for pos in &positions {
            let current_price = self.get_current_price(connector, &pos.symbol).await;
            if current_price <= 0.0 { continue; }

            // Stop-loss check
            if current_price <= pos.stop_loss {
                let pnl = mgr.close_position(&pos.symbol, current_price, "stop_loss");
                self.notify(&format!(
                    "[{}] 🛑 SL hit: {} @ ${:.2}, PnL: ${:.2}",
                    if self.config.audit_mode { "AUDIT" } else { "LIVE" },
                    pos.symbol, current_price, pnl.unwrap_or(0.0)
                )).await;
                self.record_close(&pos, current_price, "stop_loss", pnl).await;
                continue;
            }

            // TP1 hit
            if !pos.tp1_hit && !pos.take_profits.is_empty() && current_price >= pos.take_profits[0] {
                mgr.get_position_mut(&pos.symbol).map(|p| p.tp1_hit = true);
                mgr.partial_close(&pos.symbol, pos.tp1_close_pct, pos.take_profits[0], "tp1");
                mgr.update_stop_loss(&pos.symbol, pos.entry_price); // Move SL to breakeven
                self.notify(&format!(
                    "[{}] TP1 hit: {} @ ${:.2}, SL → breakeven",
                    if self.config.audit_mode { "AUDIT" } else { "LIVE" },
                    pos.symbol, pos.take_profits[0]
                )).await;
            }

            // TP2 hit
            if !pos.tp2_hit && pos.take_profits.len() >= 2 && current_price >= pos.take_profits[1] {
                mgr.get_position_mut(&pos.symbol).map(|p| p.tp2_hit = true);
                mgr.partial_close(&pos.symbol, pos.tp2_close_pct, pos.take_profits[1], "tp2");
                mgr.update_stop_loss(&pos.symbol, pos.take_profits[0]); // Move SL to TP1
                self.notify(&format!(
                    "[{}] TP2 hit: {} @ ${:.2}",
                    if self.config.audit_mode { "AUDIT" } else { "LIVE" },
                    pos.symbol, pos.take_profits[1]
                )).await;
            }

            // TP3 hit
            if !pos.tp3_hit && pos.take_profits.len() >= 3 && current_price >= pos.take_profits[2] {
                mgr.get_position_mut(&pos.symbol).map(|p| p.tp3_hit = true);
                let pnl = mgr.close_position(&pos.symbol, pos.take_profits[2], "tp3");
                self.notify(&format!(
                    "[{}] TP3 hit: {} @ ${:.2}, position closed",
                    if self.config.audit_mode { "AUDIT" } else { "LIVE" },
                    pos.symbol, pos.take_profits[2]
                )).await;
                self.record_close(&pos, pos.take_profits[2], "tp3", pnl).await;
            }
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

    async fn simulate_entry(&self, signal: &ParsedSignal, channel_name: &str) {
        let entry = signal.entry_high.unwrap_or(signal.entry_low.unwrap_or(0.0));
        if entry <= 0.0 { return; }

        let sl = signal.stop_loss.unwrap_or(entry * 0.95);
        let result = self.position_mgr.lock().await.open_position(
            signal.pair.as_deref().unwrap_or("?"),
            entry,
            100.0, // Simulated amount
            sl,
            signal.take_profits.clone(),
            &signal.confidence,
            &signal.raw_message,
            channel_name,
        );

        match result {
            Ok(()) => {
                self.risk.lock().await.record_trade_opened();
                self.log_audit_trade(signal, channel_name, "OPEN_LONG", entry, "audit_entry");
                self.notify(&format!(
                    "[AUDIT] Signal entered: {}\nEntry: ${:.2}\nSL: ${:.2}\nTPs: {}\nChannel: {}",
                    signal.pair.as_deref().unwrap_or("?"), entry, sl,
                    signal.take_profits.iter().map(|t| format!("${:.2}", t)).collect::<Vec<_>>().join(", "),
                    channel_name
                )).await;
                info!("[AUDIT] Signal entered: {} @ ${:.2} from {}", signal.pair.as_deref().unwrap_or("?"), entry, channel_name);
            }
            Err(reason) => {
                info!("Signal skipped ({}): {}", channel_name, reason);
                self.log_audit_trade(signal, channel_name, "rejected_position", 0.0, &reason);
                self.notify(&format!(
                    "⚠️ Signal skipped: {}\n{}\nChannel: {}",
                    signal.pair.as_deref().unwrap_or("?"), reason, channel_name
                )).await;
            }
        }
    }

    async fn handle_close(&self, signal: &ParsedSignal, channel_name: &str) {
        let pair = match &signal.pair {
            Some(p) => p,
            None => return,
        };
        let mgr = self.position_mgr.lock().await;
        let pos = match mgr.get_position(pair) {
            Some(p) => p.clone(),
            None => return,
        };
        drop(mgr);

        let close_price = signal.entry_low.unwrap_or(pos.entry_price);
        let pnl = self.position_mgr.lock().await.close_position(pair, close_price, "trader_close");
        self.record_close(&pos, close_price, "trader_close", pnl).await;
        self.notify(&format!("Signal closed by trader: {}", pair)).await;
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
        self.notify(&format!(
            "[{}] Closed: {} ({}) @ ${:.2}, PnL: ${:.2}",
            if self.config.audit_mode { "AUDIT" } else { "LIVE" },
            pos.symbol, reason, price, pnl.unwrap_or(0.0)
        )).await;
    }

    fn log_audit_trade(&self, signal: &ParsedSignal, channel_name: &str, action: &str, price: f64, reason: &str) {
        self.journal.log_trade(&SignalTrade {
            timestamp: Utc::now().to_rfc3339(),
            symbol: signal.pair.clone().unwrap_or_default(),
            channel_name: channel_name.to_string(),
            action: action.to_string(),
            entry_price: signal.entry_high.unwrap_or(signal.entry_low.unwrap_or(0.0)),
            current_price: price,
            quantity: 0.0,
            realized_pnl: 0.0,
            exit_reason: reason.to_string(),
            signal_confidence: signal.confidence.clone(),
            stop_loss: signal.stop_loss.unwrap_or(0.0),
            take_profits: serde_json::to_string(&signal.take_profits).unwrap_or_default(),
            tp1_hit: 0, tp2_hit: 0, tp3_hit: 0,
            raw_message: signal.raw_message.chars().take(500).collect(),
            parse_reasoning: signal.reasoning.clone(),
            is_audit: if self.config.audit_mode { 1 } else { 0 },
        });
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
