use anyhow::Result;
use tracing::info;
use tracing_subscriber::EnvFilter;

fn main() -> Result<()> {
    // Initialize structured logging
    tracing_subscriber::fmt()
        .with_env_filter(
            EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| EnvFilter::new("info"))
        )
        .with_target(false)
        .init();

    info!("Trading Engine v0.2.0 starting...");

    // Load config
    let config_path = std::env::args()
        .nth(1)
        .unwrap_or_else(|| "config/strategy.yaml".to_string());

    let config = trading_engine_core::config::AppConfig::load(&config_path)?;
    info!("Config loaded from {}", config_path);
    info!("Exchange: {}", config.exchange.name);
    info!("Pairs: {:?}", config.pairs.keys().collect::<Vec<_>>());

    // TODO: Engine initialization will be added in Phase 8
    info!("Trading engine initialized (skeleton)");

    Ok(())
}
