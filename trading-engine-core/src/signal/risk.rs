use crate::signal::types::{ParsedSignal, SignalConfidence};
use std::time::{SystemTime, UNIX_EPOCH};
use std::path::PathBuf;
use std::fs;
use chrono::Utc;
use tracing::{info, warn};
use serde::{Serialize, Deserialize};

pub struct SignalRiskGuard {
    capital_pct: f64,
    max_capital: f64,
    max_positions: u8,
    per_trade_pct: f64,
    daily_loss_limit_pct: f64,
    max_trades_per_day: u32,
    cooldown_secs: u64,
    trades_today: u32,
    daily_pnl: f64,
    signal_budget: f64,
    last_trade_time: u64,
    halted: bool,
    last_reset_date: String,
    data_dir: PathBuf,
}

#[derive(Serialize, Deserialize)]
struct RiskState {
    trades_today: u32,
    daily_pnl: f64,
    halted: bool,
    last_trade_time: u64,
    last_reset_date: String,
}

impl SignalRiskGuard {
    pub fn new(config: &crate::config::SignalConfig) -> Self {
        let data_dir = PathBuf::from("data");
        let _ = fs::create_dir_all(&data_dir);
        let mut guard = Self {
            capital_pct: config.capital_pct,
            max_capital: config.max_capital_usdt,
            max_positions: config.max_positions,
            per_trade_pct: config.per_trade_risk_pct,
            daily_loss_limit_pct: config.daily_loss_limit_pct,
            max_trades_per_day: config.max_trades_per_day,
            cooldown_secs: config.cooldown_minutes * 60,
            trades_today: 0,
            daily_pnl: 0.0,
            signal_budget: 0.0,
            last_trade_time: 0,
            halted: false,
            last_reset_date: String::new(),
            data_dir,
        };
        guard.load_state();
        guard
    }

    pub fn can_trade(&mut self) -> bool {
        self.maybe_reset_daily();
        if self.halted { return false; }
        if self.trades_today >= self.max_trades_per_day { return false; }
        let now = now_secs();
        if now - self.last_trade_time < self.cooldown_secs { return false; }
        true
    }

    pub fn get_budget_for_trade(&mut self, signal: &ParsedSignal, total_equity: f64) -> f64 {
        let total_budget = self.max_capital.min(total_equity * self.capital_pct / 100.0);
        self.signal_budget = total_budget;

        let conf = signal.signal_confidence();
        let mult = conf.multiplier();
        let risk_amount = total_budget * self.per_trade_pct / 100.0 * mult;

        if let (Some(sl), Some(entry)) = (signal.stop_loss, signal.entry_high) {
            if entry > 0.0 {
                let sl_distance_pct = (entry - sl) / entry;
                if sl_distance_pct > 0.0 {
                    let position_size = risk_amount / sl_distance_pct;
                    return position_size.min(total_budget / self.max_positions as f64);
                }
            }
        }

        total_budget / self.max_positions as f64
    }

    pub fn record_trade_opened(&mut self) {
        self.trades_today += 1;
        self.last_trade_time = now_secs();
        self.save_state();
    }

    pub fn record_trade_closed(&mut self, pnl: f64) {
        self.daily_pnl += pnl;
        if self.signal_budget > 0.0 &&
            self.daily_pnl <= -(self.signal_budget * self.daily_loss_limit_pct / 100.0)
        {
            self.halted = true;
        }
        self.save_state();
    }

    pub fn get_status(&mut self) -> SignalRiskStatus {
        self.maybe_reset_daily();
        let now = now_secs();
        SignalRiskStatus {
            trades_today: self.trades_today,
            max_trades: self.max_trades_per_day,
            daily_pnl: self.daily_pnl,
            budget: self.signal_budget,
            halted: self.halted,
            cooldown_remaining: self.cooldown_secs.saturating_sub(now - self.last_trade_time),
        }
    }

    fn maybe_reset_daily(&mut self) {
        let today = Utc::now().format("%Y-%m-%d").to_string();
        if today != self.last_reset_date {
            info!("Signal risk guard daily reset: {}", today);
            self.trades_today = 0;
            self.daily_pnl = 0.0;
            self.halted = false;
            self.last_reset_date = today;
            self.save_state();
        }
    }

    fn save_state(&self) {
        let path = self.data_dir.join("signal_risk.json");
        let state = RiskState {
            trades_today: self.trades_today,
            daily_pnl: self.daily_pnl,
            halted: self.halted,
            last_trade_time: self.last_trade_time,
            last_reset_date: self.last_reset_date.clone(),
        };
        if let Ok(json) = serde_json::to_string_pretty(&state) {
            let _ = fs::write(&path, json);
        }
    }

    fn load_state(&mut self) {
        let path = self.data_dir.join("signal_risk.json");
        if !path.exists() { return; }

        match fs::read_to_string(&path) {
            Ok(content) => {
                match serde_json::from_str::<RiskState>(&content) {
                    Ok(state) => {
                        self.trades_today = state.trades_today;
                        self.daily_pnl = state.daily_pnl;
                        self.halted = state.halted;
                        self.last_trade_time = state.last_trade_time;
                        self.last_reset_date = state.last_reset_date;
                        // Apply daily reset in case state is from a previous day
                        self.maybe_reset_daily();
                        info!("Signal risk guard loaded: trades={}, pnl={:.2}, date={}",
                            self.trades_today, self.daily_pnl, self.last_reset_date);
                    }
                    Err(e) => warn!("Failed to parse risk state: {}", e),
                }
            }
            Err(e) => warn!("Failed to read risk state: {}", e),
        }
    }
}

pub struct SignalRiskStatus {
    pub trades_today: u32,
    pub max_trades: u32,
    pub daily_pnl: f64,
    pub budget: f64,
    pub halted: bool,
    pub cooldown_remaining: u64,
}

fn now_secs() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
