use std::sync::Arc;
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

    // Extract strategy config before moving config into engine
    let pair_configs: Vec<(String, trading_engine_core::config::PairConfig)> = config.pairs
        .iter()
        .filter(|(_, pc)| pc.enabled)
        .map(|(s, pc)| (s.clone(), pc.clone()))
        .collect();
    let grid_cfg = config.grid.clone();
    let trend_cfg = config.trend.clone();
    let mr_cfg = config.mean_reversion.clone();

    let connector: Arc<dyn trading_engine_core::connector::Connector> = if config.exchange.testnet {
        info!("Using PAPER TRADE engine with real Binance market data");
        let fill_cooldown_ms: i64 = std::env::var("PAPER_FILL_COOLDOWN_MS")
            .ok().and_then(|v| v.parse().ok())
            .unwrap_or(5000);
        info!("Paper fill cooldown: {}ms", fill_cooldown_ms);
        let mut balances = std::collections::HashMap::new();
        balances.insert("USDT".to_string(), config.grid.capital_usdt);
        Arc::new(
            trading_engine_core::connector::paper::PaperTradeConnector::with_market_data(
                balances,
                &api_key,
                &api_secret,
                true, // testnet flag for BinanceRest
            )
            .with_fill_cooldown(fill_cooldown_ms),
        )
    } else if config.exchange.name.contains("gate") {
        info!("Using LIVE Gate.io connector");
        Arc::new(trading_engine_core::connector::gateio_rest::GateioConnector::new(
            &api_key, &api_secret
        ))
    } else {
        info!("Using LIVE Binance connector");
        Arc::new(trading_engine_core::connector::binance_rest::BinanceConnector::new(
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

    let bar_cache = trading_engine_core::bar_cache::BarCache::new();
    let status_cache = trading_engine_core::strategy::status_cache::StrategyStatusCache::new();
    let regime_cache = trading_engine_core::strategy::regime_cache::RegimeCache::new("data/regime_cache.json", 180_000); // 3min TTL = 3×60s poll
    let mut engine = trading_engine_core::engine::Engine::new(config, connector.clone(), risk, telegram.clone_for_signal(), bar_cache.clone(), status_cache.clone(), regime_cache.clone());

    // Add strategies for each enabled pair
    for (symbol, pc) in &pair_configs {
        // Grid strategy per pair
        let grid = trading_engine_core::strategy::grid::GridStrategy::new(
            symbol,
            &grid_cfg,
            pc.tick_size,
            pc.step_size,
        );
        engine.add_strategy(Box::new(grid));

        // Trend strategy per pair
        let trend = trading_engine_core::strategy::trend::TrendStrategy::new(
            symbol,
            &trend_cfg,
        );
        engine.add_strategy(Box::new(trend));

        // Mean Reversion strategy per pair
        let mean_reversion = trading_engine_core::strategy::mean_reversion::MeanReversionStrategy::new(
            symbol,
            &mr_cfg,
            telegram.clone_for_signal(),
        );
        engine.add_strategy(Box::new(mean_reversion));
    }

    // Spawn HTTP API server
    let api_port: u16 = std::env::var("API_PORT")
        .unwrap_or_else(|_| "3030".to_string())
        .parse()
        .unwrap_or(3030);
    let app_state = trading_engine_core::api::server::AppState::new(connector, bar_cache, status_cache, regime_cache);
    let router = trading_engine_core::api::server::create_router(app_state);
    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{}", api_port)).await?;
    info!("API server listening on port {}", api_port);

    tokio::select! {
        result = engine.run() => {
            if let Err(e) = result {
                error!("Engine error: {}", e);
            }
        }
        result = axum::serve(listener, router) => {
            if let Err(e) = result {
                error!("API server error: {}", e);
            }
        }
        _ = tokio::signal::ctrl_c() => {
            info!("Shutdown signal received, stopping...");
        }
    }
    info!("Trading engine stopped.");
    Ok(())
}
