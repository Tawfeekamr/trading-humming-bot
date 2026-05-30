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

impl AppConfig {
    pub fn load(path: &str) -> Result<Self> {
        let content = std::fs::read_to_string(path)?;
        let config: AppConfig = serde_yaml::from_str(&content)?;
        Ok(config)
    }
}
