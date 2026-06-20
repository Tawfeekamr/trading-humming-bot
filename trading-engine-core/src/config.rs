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
    pub capital: CapitalConfig,
    #[serde(default)]
    pub telegram: TelegramConfig,
    #[serde(default)]
    pub mean_reversion: MeanReversionConfig,
    pub ml: Option<MlConfig>,
    #[serde(default, alias = "signal_copy")]
    pub signal: Option<SignalConfig>,
    pub swing: Option<SwingConfig>,
    #[serde(default = "default_timeframe")]
    pub timeframe: String,
}

fn default_timeframe() -> String { "1m".to_string() }

/// Centralized capital accounting config (Phase A: visibility only).
#[derive(Debug, Deserialize)]
pub struct CapitalConfig {
    /// Minimum portfolio reserve kept in USDT, as a % of total equity.
    #[serde(default = "default_reserve_pct")]
    pub reserve_limit_pct: f64,
}

impl Default for CapitalConfig {
    fn default() -> Self {
        Self { reserve_limit_pct: 20.0 }
    }
}

fn default_reserve_pct() -> f64 { 20.0 }

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
            ExchangeRaw::NameOnly(name) => {
                let (api_key_env, api_secret_env) = if name.contains("gate") {
                    ("GATE_API_KEY".to_string(), "GATE_API_SECRET".to_string())
                } else {
                    ("BINANCE_API_KEY".to_string(), "BINANCE_API_SECRET".to_string())
                };
                Self {
                    name,
                    api_key_env,
                    api_secret_env,
                    testnet: false,
                }
            }
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
    // Configurable grid gate thresholds (defaults match old hard-coded constants)
    #[serde(default = "default_22")]
    pub adx_range_max: f64,
    #[serde(default = "default_55")]
    pub chop_range_min: f64,
    #[serde(default = "default_005")]
    pub natr_floor: f64,
    #[serde(default = "default_04")]
    pub natr_ceil: f64,
    #[serde(default = "default_60")]
    pub fill_cooldown_secs: i64,
    /// Block grid when ML regime=Trending AND confidence >= this threshold.
    /// Default 0.75 (was hard-coded 0.55 which blocked everything).
    #[serde(default = "default_075")]
    pub ml_trending_block_threshold: f64,
    /// Block grid when ML regime=Danger AND confidence >= this threshold.
    #[serde(default = "default_055")]
    pub ml_danger_block_threshold: f64,
}

fn default_1_5() -> f64 { 1.5 }
fn default_22() -> f64 { 22.0 }
fn default_55() -> f64 { 55.0 }
fn default_005() -> f64 { 0.005 }
fn default_04() -> f64 { 0.04 }
fn default_60() -> i64 { 60 }
fn default_075() -> f64 { 0.75 }
fn default_055() -> f64 { 0.55 }

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
    pub rsi_min: f64,
    #[serde(default)]
    pub rsi_max: f64,
    #[serde(default)]
    pub min_signal_score: u8,
    #[serde(default)]
    pub confirmation_ticks: u8,
    #[serde(default = "default_2", alias = "rr_ratio")]
    pub risk_reward_ratio: f64,
    #[serde(default = "default_10k")]
    pub capital: f64,
    #[serde(default = "default_2")]
    pub risk_per_trade_pct: f64,
    #[serde(default = "default_25")]
    pub max_position_pct: f64,
    #[serde(default = "default_1_5")]
    pub trailing_stop_pct: f64,
    #[serde(default = "default_2_5")]
    pub trailing_stop_atr_mult: f64,
    #[serde(default = "default_1_5")]
    pub trailing_activation_pct: f64,
    #[serde(default = "default_2_u8")]
    pub exit_signal_threshold: u8,
    #[serde(default = "default_0_2")]
    pub sl_buffer_pct: f64,
    #[serde(default = "default_25")]
    pub adx_gate_threshold: f64,
    #[serde(default = "default_20")]
    pub adx_exit_threshold: f64,
    #[serde(default = "default_38")]
    pub choppiness_threshold: f64,
    #[serde(default = "default_1_2")]
    pub volume_ratio_threshold: f64,
    #[serde(default = "default_4_u8")]
    pub entry_score_threshold: u8,
    #[serde(default = "default_65")]
    pub rsi_long_max: f64,
    #[serde(default = "default_35")]
    pub rsi_short_min: f64,
    #[serde(default = "default_3")]
    pub atr_trailing_mult: f64,
    /// Allow short trades. Default false (long-only). When true, Direction::Down generates sell entries.
    #[serde(default)]
    pub trade_shorts: bool,
}

fn default_10k() -> f64 { 10000.0 }
fn default_25() -> f64 { 25.0 }
fn default_2_u8() -> u8 { 2 }
fn default_0_2() -> f64 { 0.2 }
fn default_2_5() -> f64 { 2.5 }
fn default_38() -> f64 { 38.0 }
fn default_1_2() -> f64 { 1.2 }
fn default_4_u8() -> u8 { 4 }
fn default_65() -> f64 { 65.0 }
fn default_35() -> f64 { 35.0 }
fn default_20() -> f64 { 20.0 }

#[derive(Debug, Deserialize)]
pub struct RiskConfig {
    pub max_drawdown_pct: f64,
    pub daily_loss_limit_pct: f64,
    #[serde(default, alias = "max_base_exposure_pct")]
    pub max_exposure_pct: f64,
}

#[derive(Debug, Deserialize)]
pub struct TelegramConfig {
    #[serde(default = "default_telegram_token_env")]
    pub token_env: String,
    #[serde(default = "default_telegram_chat_id_env")]
    pub chat_id_env: String,
    #[serde(default = "default_true")]
    pub enabled: bool,
}

impl Default for TelegramConfig {
    fn default() -> Self {
        Self {
            token_env: default_telegram_token_env(),
            chat_id_env: default_telegram_chat_id_env(),
            enabled: true,
        }
    }
}

fn default_telegram_token_env() -> String { "TELEGRAM_BOT_TOKEN".to_string() }
fn default_telegram_chat_id_env() -> String { "TELEGRAM_CHAT_ID".to_string() }

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

#[derive(Debug, Clone, Deserialize)]
pub struct MeanReversionConfig {
    #[serde(default)]
    pub enabled: bool,
    /// Flush trigger: enter when price drops more than this fraction in the 30s window.
    #[serde(default = "default_drop_thr")]
    pub drop_thr: f64,
    /// Take-profit fraction above entry (default 2% — the high-win config from the backtest).
    #[serde(default = "default_tp_pct")]
    pub tp_pct: f64,
    /// Stop-loss fraction below entry (default 3%).
    #[serde(default = "default_stop_pct")]
    pub stop_pct: f64,
    /// If true, skip entries during Trending regime (default false — the backtest showed
    /// the regime filter blocked 97% of flushes; relaxed gates give a real edge at 2%).
    #[serde(default)]
    pub regime_gate: bool,
    #[serde(default)]
    pub classifier: ClassifierCfg,
}

impl Default for MeanReversionConfig {
    fn default() -> Self {
        Self {
            enabled: false,
            drop_thr: default_drop_thr(),
            tp_pct: default_tp_pct(),
            stop_pct: default_stop_pct(),
            regime_gate: false,
            classifier: ClassifierCfg::default(),
        }
    }
}

#[derive(Debug, Clone, Deserialize)]
pub struct ClassifierCfg {
    #[serde(default = "default_w_retrace")]
    pub w_retrace: f64,
    #[serde(default = "default_w_refill")]
    pub w_refill: f64,
    #[serde(default = "default_w_exhaust")]
    pub w_exhaust: f64,
    #[serde(default = "default_w_liq")]
    pub w_liq: f64,
    #[serde(default = "default_w_corr")]
    pub w_corr: f64,
    #[serde(default = "default_enter_threshold")]
    pub enter_threshold: f64,
    #[serde(default = "default_full_size_margin")]
    pub full_size_margin: f64,
}

impl Default for ClassifierCfg {
    fn default() -> Self {
        Self {
            w_retrace: default_w_retrace(),
            w_refill: default_w_refill(),
            w_exhaust: default_w_exhaust(),
            w_liq: default_w_liq(),
            w_corr: default_w_corr(),
            enter_threshold: default_enter_threshold(),
            full_size_margin: default_full_size_margin(),
        }
    }
}

fn default_w_retrace() -> f64 { 1.0 }
fn default_w_refill() -> f64 { 1.0 }
fn default_w_exhaust() -> f64 { 1.0 }
fn default_w_liq() -> f64 { 0.5 }
fn default_w_corr() -> f64 { 1.5 }
fn default_enter_threshold() -> f64 { 0.0 }
fn default_full_size_margin() -> f64 { 1.5 }
fn default_drop_thr() -> f64 { 0.02 }
fn default_tp_pct() -> f64 { 0.02 }
fn default_stop_pct() -> f64 { 0.03 }

#[derive(Debug, Clone, Copy, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
pub enum RunnerExitMode {
    OppositeBand,
    ChandelierOnly,
    BandOrChandelier,
}

impl Default for RunnerExitMode {
    fn default() -> Self { Self::BandOrChandelier }
}

#[derive(Debug, Clone, Deserialize)]
pub struct SwingConfig {
    #[serde(default)]
    pub enabled: bool,
    #[serde(default = "default_runner_exit")]
    pub runner_exit: RunnerExitMode,
    #[serde(default = "default_htf")]
    pub htf_period: String,
    #[serde(default = "default_ltf")]
    pub ltf_period: String,
    #[serde(default = "default_20_usize")]
    pub donchian_period: usize,
    #[serde(default = "default_0_5")]
    pub band_atr_mult: f64,
    #[serde(default = "default_14_usize")]
    pub rsi_period: usize,
    #[serde(default = "default_30")]
    pub rsi_oversold: f64,
    #[serde(default = "default_1_5")]
    pub volume_multiplier: f64,
    #[serde(default = "default_20_usize")]
    pub volume_avg_period: usize,
    #[serde(default = "default_14_usize")]
    pub atr_period: usize,
    #[serde(default = "default_1_5")]
    pub atr_stop_mult: f64,
    #[serde(default = "default_2")]
    pub min_rr: f64,
    #[serde(default = "default_1")]
    pub risk_per_trade_pct: f64,
    #[serde(default = "default_22")]
    pub adx_range_entry: f64,
    #[serde(default = "default_28")]
    pub adx_trend_exit: f64,
    #[serde(default = "default_10k")]
    pub capital: f64,
    #[serde(default = "default_48_usize")]
    pub max_bars_in_trade: usize,
    /// Restrict the swing strategy to these pairs (dash-free or dashed, e.g.
    /// "ETH-USDT" / "ETHUSDT"). Empty = run on every enabled pair. The backtest
    /// showed edge concentrated on ETH (DOGE loses, XRP inconclusive), so
    /// production should set this explicitly.
    #[serde(default)]
    pub enabled_pairs: Vec<String>,
    /// Exchange LOT_SIZE step + PRICE_FILTER tick, set per-symbol at wiring time
    /// (main.rs reads them from PairConfig). Used to round resting-order qty/price
    /// so live orders don't get rejected for filter mismatches. None on test/backtest.
    #[serde(default)]
    pub step_size: Option<f64>,
    #[serde(default)]
    pub tick_size: Option<f64>,
}

fn default_runner_exit() -> RunnerExitMode { RunnerExitMode::BandOrChandelier }
fn default_htf() -> String { "1h".to_string() }
fn default_ltf() -> String { "5m".to_string() }
fn default_20_usize() -> usize { 20 }
fn default_14_usize() -> usize { 14 }
fn default_0_5() -> f64 { 0.5 }
fn default_30() -> f64 { 30.0 }
fn default_1() -> f64 { 1.0 }
fn default_28() -> f64 { 28.0 }
fn default_48_usize() -> usize { 48 }
