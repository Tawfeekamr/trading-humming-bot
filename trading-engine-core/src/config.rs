use anyhow::Result;
use serde::Deserialize;
use std::collections::HashMap;

#[derive(Debug, Deserialize)]
pub struct AppConfig {
    #[serde(default)]
    pub exchange: ExchangeConfig,
    pub pairs: PairList,
    pub grid: GridConfig,
    #[serde(default)]
    pub trend: TrendConfig,
    pub risk: RiskConfig,
    #[serde(default)]
    pub telegram: TelegramConfig,
    pub ml: Option<MlConfig>,
    #[serde(default, alias = "signal_copy")]
    pub signal: Option<SignalConfig>,
    #[serde(default = "default_timeframe")]
    pub timeframe: String,
}

fn default_timeframe() -> String { "1m".to_string() }

#[derive(Debug, Deserialize, Default)]
#[serde(from = "ExchangeRaw")]
pub struct ExchangeConfig {
    pub name: String,
    pub api_key_env: String,
    pub api_secret_env: String,
    pub testnet: bool,
}

/// Accept both string ("binance") and full struct formats
#[derive(Deserialize)]
#[serde(untagged)]
enum ExchangeRaw {
    Full { name: String, api_key_env: String, api_secret_env: String, testnet: bool },
    NameOnly(String),
}

impl Default for ExchangeRaw {
    fn default() -> Self { Self::NameOnly("binance".to_string()) }
}

impl From<ExchangeRaw> for ExchangeConfig {
    fn from(raw: ExchangeRaw) -> Self {
        match raw {
            ExchangeRaw::Full { name, api_key_env, api_secret_env, testnet } => Self {
                name, api_key_env, api_secret_env, testnet,
            },
            ExchangeRaw::NameOnly(name) => Self {
                name,
                api_key_env: "BINANCE_API_KEY".to_string(),
                api_secret_env: "BINANCE_API_SECRET".to_string(),
                testnet: false,
            },
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct PairConfig {
    pub symbol: String,
    pub step_size: f64,
    pub tick_size: f64,
    #[serde(default = "default_true")]
    pub enabled: bool,
}

fn default_true() -> bool { true }

/// Parses YAML pairs list into a HashMap keyed by symbol
#[derive(Debug, Deserialize)]
#[serde(from = "Vec<PairConfig>")]
pub struct PairList(pub HashMap<String, PairConfig>);

impl From<Vec<PairConfig>> for PairList {
    fn from(list: Vec<PairConfig>) -> Self {
        let map = list.into_iter()
            .map(|p| (p.symbol.clone(), p))
            .collect();
        Self(map)
    }
}

impl std::ops::Deref for PairList {
    type Target = HashMap<String, PairConfig>;
    fn deref(&self) -> &Self::Target { &self.0 }
}

#[derive(Debug, Clone, Deserialize)]
pub struct GridConfig {
    pub levels: u8,
    pub capital_usdt: f64,
    #[serde(default, alias = "min_usdt_reserve")]
    pub min_reserve: f64,
    #[serde(default = "default_1_5")]
    pub spacing_multiplier: f64,
}

fn default_1_5() -> f64 { 1.5 }

#[derive(Debug, Clone, Default, Deserialize)]
pub struct TrendConfig {
    #[serde(default)]
    pub ema_fast: u32,
    #[serde(default)]
    pub ema_slow: u32,
    #[serde(default)]
    pub ema_trend: u32,
    #[serde(default)]
    pub rsi_period: u32,
    #[serde(default)]
    pub min_signal_score: u8,
    #[serde(default)]
    pub confirmation_ticks: u8,
    #[serde(default)]
    pub risk_reward_ratio: f64,
}

#[derive(Debug, Deserialize)]
pub struct RiskConfig {
    pub max_drawdown_pct: f64,
    pub daily_loss_limit_pct: f64,
    #[serde(default, alias = "max_base_exposure_pct")]
    pub max_exposure_pct: f64,
}

#[derive(Debug, Default, Deserialize)]
pub struct TelegramConfig {
    #[serde(default)]
    pub token_env: String,
    #[serde(default)]
    pub chat_id_env: String,
    #[serde(default)]
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
