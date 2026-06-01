use crate::signal::types::SignalPosition;
use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};
use tracing::{info, warn};

pub struct SignalPositionManager {
    max_positions: u8,
    tp1_close_pct: f64,
    tp2_close_pct: f64,
    data_dir: PathBuf,
    positions: HashMap<String, SignalPosition>,
}

impl SignalPositionManager {
    pub fn new(config: &crate::config::SignalConfig) -> Self {
        let data_dir = PathBuf::from("data");
        let _ = fs::create_dir_all(&data_dir);
        let mut mgr = Self {
            max_positions: config.max_positions,
            tp1_close_pct: config.tp1_close_pct / 100.0,
            tp2_close_pct: config.tp2_close_pct / 100.0,
            data_dir,
            positions: HashMap::new(),
        };
        mgr.load_state();
        mgr
    }

    pub fn has_open_position(&self, symbol: &str) -> bool {
        self.positions.get(symbol).map(|p| !p.is_closed).unwrap_or(false)
    }

    pub fn get_open_positions(&self) -> Vec<&SignalPosition> {
        self.positions.values().filter(|p| !p.is_closed).collect()
    }

    pub fn get_open_positions_mut(&mut self) -> Vec<&mut SignalPosition> {
        self.positions.values_mut().filter(|p| !p.is_closed).collect()
    }

    pub fn get_position(&self, symbol: &str) -> Option<&SignalPosition> {
        self.positions.get(symbol).filter(|p| !p.is_closed)
    }

    pub fn get_position_mut(&mut self, symbol: &str) -> Option<&mut SignalPosition> {
        self.positions.get_mut(symbol).filter(|p| !p.is_closed)
    }

    pub fn open_position(
        &mut self,
        symbol: &str,
        entry_price: f64,
        amount: f64,
        stop_loss: f64,
        take_profits: Vec<f64>,
        signal_confidence: &str,
        raw_message: &str,
        channel_name: &str,
    ) -> Option<&SignalPosition> {
        let open_count = self.positions.values().filter(|p| !p.is_closed).count();
        if open_count >= self.max_positions as usize {
            warn!("Max signal positions ({}) reached", self.max_positions);
            return None;
        }
        if self.positions.get(symbol).map(|p| !p.is_closed).unwrap_or(false) {
            warn!("Signal position already open for {}", symbol);
            return None;
        }

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_secs() as f64;

        let pos = SignalPosition {
            symbol: symbol.to_string(),
            entry_price,
            amount,
            stop_loss,
            take_profits,
            signal_confidence: signal_confidence.to_string(),
            raw_message: raw_message.to_string(),
            channel_name: channel_name.to_string(),
            entry_timestamp: now,
            tp1_close_pct: self.tp1_close_pct,
            tp2_close_pct: self.tp2_close_pct,
            ..Default::default()
        };
        self.positions.insert(symbol.to_string(), pos);
        self.save_state();
        self.positions.get(symbol)
    }

    pub fn partial_close(&mut self, symbol: &str, close_pct: f64, price: f64, reason: &str) -> f64 {
        if let Some(pos) = self.positions.get_mut(symbol) {
            if pos.is_closed { return 0.0; }
            let close_amount = pos.remaining_amount() * close_pct;
            let pnl = (price - pos.entry_price) * close_amount;
            pos.amount_closed += close_amount;
            pos.realized_pnl += pnl;
            info!("Signal partial close {}: {:.0}% @ ${:.2} ({}, PnL: ${:.2})",
                symbol, close_pct * 100.0, price, reason, pnl);
            self.save_state();
            close_amount
        } else {
            0.0
        }
    }

    pub fn close_position(&mut self, symbol: &str, price: f64, reason: &str) -> Option<f64> {
        if let Some(pos) = self.positions.get_mut(symbol) {
            if pos.is_closed { return None; }
            let remaining = pos.remaining_amount();
            let pnl = (price - pos.entry_price) * remaining;
            pos.amount_closed = pos.amount;
            pos.realized_pnl += pnl;
            pos.is_closed = true;
            pos.exit_reason = reason.to_string();
            let total_pnl = pos.realized_pnl;
            info!("Signal close {}: {} @ ${:.2} (total PnL: ${:.2})", symbol, reason, price, total_pnl);
            self.save_state();
            Some(total_pnl)
        } else {
            None
        }
    }

    pub fn update_stop_loss(&mut self, symbol: &str, new_sl: f64) {
        if let Some(pos) = self.positions.get_mut(symbol) {
            if !pos.is_closed {
                pos.stop_loss = new_sl;
                self.save_state();
            }
        }
    }

    fn save_state(&self) {
        let path = self.data_dir.join("signal_positions.json");
        let mut state = serde_json::Map::new();
        let now = now_secs();

        for (symbol, pos) in &self.positions {
            // Keep open positions and recently closed (within 24h)
            if !pos.is_closed || (now - pos.entry_timestamp as u64) < 86400 {
                state.insert(symbol.clone(), serde_json::to_value(pos).unwrap_or_default());
            }
        }

        if let Ok(json) = serde_json::to_string_pretty(&state) {
            let _ = fs::write(&path, json);
        }
    }

    fn load_state(&mut self) {
        let path = self.data_dir.join("signal_positions.json");
        if !path.exists() { return; }

        match fs::read_to_string(&path) {
            Ok(content) => {
                match serde_json::from_str::<HashMap<String, SignalPosition>>(&content) {
                    Ok(positions) => {
                        for (symbol, pos) in positions {
                            if !pos.is_closed {
                                self.positions.insert(symbol, pos);
                            }
                        }
                    }
                    Err(e) => warn!("Failed to parse signal positions: {}", e),
                }
            }
            Err(e) => warn!("Failed to read signal positions: {}", e),
        }
    }

    /// Reload positions from file, merging only NEW ones (won't overwrite existing tracking state)
    pub fn reload_state(&mut self) {
        let path = self.data_dir.join("signal_positions.json");
        if !path.exists() { return; }

        match fs::read_to_string(&path) {
            Ok(content) => {
                match serde_json::from_str::<HashMap<String, SignalPosition>>(&content) {
                    Ok(positions) => {
                        for (symbol, pos) in positions {
                            // Only insert if we don't already track this position
                            // (avoids overwriting tp1_hit, tp2_hit, etc. from our own monitoring)
                            if !pos.is_closed && !self.positions.contains_key(&symbol) {
                                info!("Signal position loaded from Python: {}", symbol);
                                self.positions.insert(symbol, pos);
                            }
                        }
                    }
                    Err(e) => warn!("Failed to parse signal positions on reload: {}", e),
                }
            }
            Err(e) => warn!("Failed to read signal positions on reload: {}", e),
        }
    }
}

impl Default for SignalPosition {
    fn default() -> Self {
        Self {
            symbol: String::new(),
            entry_price: 0.0,
            amount: 0.0,
            stop_loss: 0.0,
            take_profits: Vec::new(),
            signal_confidence: String::new(),
            raw_message: String::new(),
            channel_name: String::new(),
            entry_timestamp: 0.0,
            tp1_hit: false,
            tp2_hit: false,
            tp3_hit: false,
            amount_closed: 0.0,
            realized_pnl: 0.0,
            is_closed: false,
            exit_reason: String::new(),
            tp1_close_pct: 0.33,
            tp2_close_pct: 0.50,
            order_id: String::new(),
        }
    }
}

fn now_secs() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).unwrap_or_default().as_secs()
}
