use anyhow::Result;
use tracing::{info, error};
use tracing_subscriber::EnvFilter;

fn main() -> Result<()> {
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| EnvFilter::new("info"))
        )
        .with_target(false)
        .init();

    let rt = tokio::runtime::Runtime::new()?;
    rt.block_on(async_main())
}

async fn async_main() -> Result<()> {
    info!("Trading Engine v0.2.0 starting...");

    let config_path = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "config/strategy.yaml".to_string());
    let config = trading_engine_core::config::AppConfig::load(&config_path)?;
    info!("Config loaded from {}", config_path);

    let api_key = std::env::var(&config.exchange.api_key_env).unwrap_or_default();
    let api_secret = std::env::var(&config.exchange.api_secret_env).unwrap_or_default();
    let telegram_token = std::env::var(&config.telegram.token_env).unwrap_or_default();
    let telegram_chat_id = std::env::var(&config.telegram.chat_id_env).unwrap_or_default();

    let connector: Box<dyn trading_engine_core::connector::Connector> = if config.exchange.testnet {
        info!("Using PAPER TRADE engine");
        let mut balances = std::collections::HashMap::new();
        balances.insert("USDT".to_string(), config.grid.capital_usdt);
        Box::new(trading_engine_core::connector::paper::PaperTradeConnector::new(balances))
    } else {
        info!("Using LIVE Binance connector");
        Box::new(trading_engine_core::connector::binance_rest::BinanceConnector::new(
            &api_key, &api_secret, false
        ))
    };

    let risk = trading_engine_core::risk::RiskManager::new(
        trading_engine_core::risk::PositionGuard::new(
            config.risk.max_exposure_pct,
            config.grid.min_reserve,
            config.grid.capital_usdt,
        ),
        trading_engine_core::risk::CircuitBreaker::new(
            config.risk.max_drawdown_pct,
            config.risk.daily_loss_limit_pct,
        ),
    );

    let telegram = trading_engine_core::notifications::TelegramBot::new(&telegram_token, &telegram_chat_id);

    let mut engine = trading_engine_core::engine::Engine::new(config, connector, risk, telegram);

    tokio::select! {
        result = engine.run() => {
            if let Err(e) = result {
                error!("Engine error: {}", e);
            }
        }
        _ = tokio::signal::ctrl_c() => {
            info!("Shutdown signal received, stopping...");
        }
    }
    info!("Trading engine stopped.");
    Ok(())
}
