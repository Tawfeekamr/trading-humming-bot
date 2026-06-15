use crate::signal::types::SignalPosition;
use std::collections::HashMap;
use std::fs;
use std::path::PathBuf;
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
    ) -> Result<(), String> {
        let open_count = self.positions.values().filter(|p| !p.is_closed).count();
        if open_count >= self.max_positions as usize {
            let reason = format!(
                "Max signal positions ({}/{}) reached — skipping {}",
                open_count, self.max_positions, symbol
            );
            warn!("{}", reason);
            return Err(reason);
        }
        if self.positions.get(symbol).map(|p| !p.is_closed).unwrap_or(false) {
            let reason = format!("Position already open for {} — skipping duplicate", symbol);
            warn!("{}", reason);
            return Err(reason);
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
        Ok(())
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

    /// Self-heal: mark any open position closed if `closed` (from the journal)
    /// shows it was already closed — matched by symbol + entry_price so a new
    /// open for the same symbol isn't falsely closed. Clears stale-open positions
    /// left by the Rust/Python dual-write era so they aren't re-managed/re-closed.
    pub fn reconcile_closed(&mut self, closed: &[(String, f64, String, bool)]) {
        let mut changed = false;
        for (symbol, entry_price, reason, tp3) in closed {
            if let Some(pos) = self.positions.get_mut(symbol) {
                if !pos.is_closed && (pos.entry_price - entry_price).abs() < 1e-6 {
                    pos.is_closed = true;
                    pos.exit_reason = reason.clone();
                    if *tp3 { pos.tp3_hit = true; }
                    info!("Signal self-heal: {} marked closed (journal CLOSE @ entry ${:.4})", symbol, entry_price);
                    changed = true;
                }
            }
        }
        if changed { self.save_state(); }
    }

    fn save_state(&self) {
        use fs2::FileExt;
        let path = self.data_dir.join("signal_positions.json");
        let tmp_path = self.data_dir.join("signal_positions.json.tmp");
        let lock_path = self.data_dir.join("signal_positions.lock");

        // Cross-process advisory lock shared with the Python signal listener.
        // Non-blocking: if Python holds the lock, defer this save to the next
        // tick (state stays in memory, nothing is lost).
        let lock_file = match fs::OpenOptions::new()
            .create(true).read(true).write(true).open(&lock_path)
        {
            Ok(f) => f,
            Err(e) => { warn!("signal_positions lock open failed: {}", e); return; }
        };
        if let Err(e) = lock_file.try_lock_exclusive() {
            warn!("signal_positions lock busy, deferring save: {}", e);
            return;
        }

        // Read current disk state and preserve entries we don't track (e.g.
        // a position Python just opened) so our write can't erase it.
        let mut merged: serde_json::Map<String, serde_json::Value> = match fs::read_to_string(&path) {
            Ok(content) => serde_json::from_str(&content).unwrap_or_default(),
            Err(_) => serde_json::Map::new(),
        };
        for (symbol, pos) in &self.positions {
            // Don't clobber a same-position disk entry that's closed or further
            // along than this in-memory copy. Two managers (Rust + Python) race
            // on this file; without this guard a stale in-memory copy reverts a
            // real close → the position re-opens → gets closed AGAIN on the next
            // tick/restart (duplicate-close / phantom-PnL bug). A genuinely new
            // open (different entry_timestamp) still overwrites.
            if let Some(disk_val) = merged.get(symbol) {
                if disk_more_advanced(disk_val, pos) { continue; }
            }
            // Persist closed positions too (do NOT prune by entry age). Pruning a
            // just-closed position held >24h left the other manager's stale OPEN
            // copy as the only one on disk → re-close loops. Re-opens of the same
            // symbol (different entry_timestamp) still overwrite, so growth is
            // bounded to one entry per distinct position.
            merged.insert(symbol.clone(), serde_json::to_value(pos).unwrap_or_default());
        }

        // Atomic publish: write temp, then rename — readers never see a partial file.
        if let Ok(json) = serde_json::to_string_pretty(&merged) {
            if fs::write(&tmp_path, json).is_ok() {
                let _ = fs::rename(&tmp_path, &path);
            }
        }
        let _ = lock_file.unlock();
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

/// True if the on-disk JSON entry is the SAME position (same entry_timestamp) as
/// `pos` AND has progressed further — closed, or a TP hit that `pos` lacks. In
/// that case the in-memory copy is stale and must not overwrite disk. A different
/// entry_timestamp means a new open for the same symbol, which should overwrite.
fn disk_more_advanced(disk: &serde_json::Value, pos: &SignalPosition) -> bool {
    let obj = match disk.as_object() { Some(o) => o, None => return false };
    let disk_ts = obj.get("entry_timestamp").and_then(|v| v.as_f64()).unwrap_or(0.0);
    if (disk_ts - pos.entry_timestamp).abs() > 1e-3 { return false; }
    let disk_closed = obj.get("is_closed").and_then(|v| v.as_bool()).unwrap_or(false);
    if disk_closed && !pos.is_closed { return true; }
    for (key, py_hit) in [("tp1_hit", pos.tp1_hit), ("tp2_hit", pos.tp2_hit), ("tp3_hit", pos.tp3_hit)] {
        let disk_hit = obj.get(key).and_then(|v| v.as_bool()).unwrap_or(false);
        if disk_hit && !py_hit { return true; }
    }
    false
}
