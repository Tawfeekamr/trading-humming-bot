use trading_engine_core::config::AppConfig;

#[test]
fn test_load_config_from_yaml() {
    let yaml = r#"
exchange:
  name: binance
  api_key_env: BINANCE_API_KEY
  api_secret_env: BINANCE_API_SECRET
  testnet: false

pairs:
  - symbol: "BTC-USDT"
    step_size: 0.00001
    tick_size: 0.01
    enabled: true
  - symbol: "ETH-USDT"
    step_size: 0.0001
    tick_size: 0.01
    enabled: true

grid:
  levels: 5
  capital_usdt: 10000
  min_reserve: 100
  spacing_multiplier: 1.5

trend:
  ema_fast: 20
  ema_slow: 50
  ema_trend: 200
  rsi_period: 14
  min_signal_score: 3
  confirmation_ticks: 2
  risk_reward_ratio: 2.0

risk:
  max_drawdown_pct: 10.0
  daily_loss_limit_pct: 5.0
  max_exposure_pct: 80.0

telegram:
  token_env: TELEGRAM_BOT_TOKEN
  chat_id_env: TELEGRAM_CHAT_ID
  enabled: true

ml:
  model_path: models/regime.onnx
  enabled: true
"#;

    let config: AppConfig = serde_yaml::from_str(yaml).expect("Failed to parse config");
    assert_eq!(config.exchange.name, "binance");
    assert_eq!(config.grid.levels, 5);
    assert_eq!(config.pairs.len(), 2);
    assert!(config.pairs["BTC-USDT"].enabled);
    assert_eq!(config.trend.risk_reward_ratio, 2.0);
    assert_eq!(config.risk.max_drawdown_pct, 10.0);
}

#[test]
fn test_config_ml_optional() {
    let yaml = r#"
exchange:
  name: binance
  api_key_env: BINANCE_API_KEY
  api_secret_env: BINANCE_API_SECRET
  testnet: false
pairs: []
grid:
  levels: 5
  capital_usdt: 10000
  min_reserve: 100
  spacing_multiplier: 1.5
trend:
  ema_fast: 20
  ema_slow: 50
  ema_trend: 200
  rsi_period: 14
  min_signal_score: 3
  confirmation_ticks: 2
  risk_reward_ratio: 2.0
risk:
  max_drawdown_pct: 10.0
  daily_loss_limit_pct: 5.0
  max_exposure_pct: 80.0
telegram:
  token_env: TELEGRAM_BOT_TOKEN
  chat_id_env: TELEGRAM_CHAT_ID
  enabled: true
"#;

    let config: AppConfig = serde_yaml::from_str(yaml).expect("Failed to parse config");
    assert!(config.ml.is_none());
}
