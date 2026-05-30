use anyhow::Result;
use serde::Deserialize;
use std::collections::HashMap;

#[derive(Debug, Deserialize)]
pub struct AppConfig {
    pub exchange: ExchangeConfig,
    pub pairs: HashMap<String, PairConfig>,
    pub grid: GridConfig,
    pub trend: TrendConfig,
    pub risk: RiskConfig,
    pub telegram: TelegramConfig,
    pub ml: Option<MlConfig>,
    pub signal: Option<SignalConfig>,
}

#[derive(Debug, Deserialize)]
pub struct ExchangeConfig {
    pub name: String,
    pub api_key_env: String,
    pub api_secret_env: String,
    pub testnet: bool,
}

#[derive(Debug, Deserialize)]
pub struct PairConfig {
    pub step_size: f64,
    pub tick_size: f64,
    pub enabled: bool,
}

#[derive(Debug, Deserialize)]
pub struct GridConfig {
    pub levels: u8,
    pub capital_usdt: f64,
    pub min_reserve: f64,
    pub spacing_multiplier: f64,
}

#[derive(Debug, Deserialize)]
pub struct TrendConfig {
    pub ema_fast: u32,
    pub ema_slow: u32,
    pub ema_trend: u32,
    pub rsi_period: u32,
    pub min_signal_score: u8,
    pub confirmation_ticks: u8,
    pub risk_reward_ratio: f64,
}

#[derive(Debug, Deserialize)]
pub struct RiskConfig {
    pub max_drawdown_pct: f64,
    pub daily_loss_limit_pct: f64,
    pub max_exposure_pct: f64,
}

#[derive(Debug, Deserialize)]
pub struct TelegramConfig {
    pub token_env: String,
    pub chat_id_env: String,
    pub enabled: bool,
}

#[derive(Debug, Deserialize)]
pub struct MlConfig {
    pub model_path: String,
    pub enabled: bool,
}

#[derive(Debug, Deserialize)]
pub struct SignalConfig {
    pub enabled: bool,
    #[serde(default)]
    pub audit_mode: bool,
    #[serde(default = "default_ai_model")]
    pub ai_model: String,
    #[serde(default = "default_max_positions")]
    pub max_positions: u8,
    #[serde(default = "default_3")]
    pub per_trade_risk_pct: f64,
    #[serde(default = "default_10")]
    pub capital_pct: f64,
    #[serde(default = "default_1000")]
    pub max_capital_usdt: f64,
    #[serde(default)]
    pub min_rr_ratio: f64,
    #[serde(default = "default_10")]
    pub max_sl_distance_pct: f64,
    #[serde(default = "default_2")]
    pub default_sl_atr_multiplier: f64,
    #[serde(default = "default_3")]
    pub max_entry_zone_pct: f64,
    #[serde(default = "default_5")]
    pub min_quality_score: u8,
    #[serde(default = "default_33")]
    pub tp1_close_pct: f64,
    #[serde(default = "default_50")]
    pub tp2_close_pct: f64,
    #[serde(default = "default_5_f64")]
    pub daily_loss_limit_pct: f64,
    #[serde(default = "default_10_u32")]
    pub max_trades_per_day: u32,
    #[serde(default = "default_5_u64")]
    pub cooldown_minutes: u64,
    #[serde(default)]
    pub use_btc_correlation_gate: bool,
    #[serde(default)]
    pub blacklisted_pairs: Vec<String>,
    #[serde(default = "default_session_name")]
    pub session_name: String,
}

fn default_ai_model() -> String { "deepseek-chat".to_string() }
fn default_max_positions() -> u8 { 3 }
fn default_3() -> f64 { 3.0 }
fn default_10() -> f64 { 10.0 }
fn default_1000() -> f64 { 1000.0 }
fn default_2() -> f64 { 2.0 }
fn default_5() -> u8 { 5 }
fn default_5_f64() -> f64 { 5.0 }
fn default_33() -> f64 { 33.0 }
fn default_50() -> f64 { 50.0 }
fn default_10_u32() -> u32 { 10 }
fn default_5_u64() -> u64 { 5 }
fn default_session_name() -> String { "signal_listener".to_string() }

impl AppConfig {
    pub fn load(path: &str) -> Result<Self> {
        let content = std::fs::read_to_string(path)?;
        let config: AppConfig = serde_yaml::from_str(&content)?;
        Ok(config)
    }
}
