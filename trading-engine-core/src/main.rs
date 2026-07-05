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
    let swing_cfg = config.swing.clone();

    let connector: Arc<dyn trading_engine_core::connector::Connector> = if config.exchange.testnet {
        info!("Using PAPER TRADE engine with real Binance market data");
        let fill_cooldown_ms: i64 = std::env::var("PAPER_FILL_COOLDOWN_MS")
            .ok().and_then(|v| v.parse().ok())
            .unwrap_or(5000);
        info!("Paper fill cooldown: {}ms", fill_cooldown_ms);
        let mut balances = std::collections::HashMap::new();
        balances.insert("USDT".to_string(), config.capital.account_usdt);
        Arc::new(
            trading_engine_core::connector::paper::PaperTradeConnector::with_market_data(
                balances,
                &api_key,
                &api_secret,
                true, // testnet flag for BinanceRest
            )
            .with_fill_cooldown(fill_cooldown_ms)
            .with_realism(
                config.paper.slippage_bps,
                config.paper.taker_fee_bps,
                config.paper.maker_fee_bps,
            ),
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
            config.capital.account_usdt,
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
    // Routing cache: Python PPO router POSTs {active_engine, size_mult, flat} every
    // tick; Rust reads it from the engine (Task 6 wires it into Engine::new).
    // 3min TTL matches regime_cache (3× the 60s Python push cadence).
    let routing_cache = trading_engine_core::strategy::routing_cache::RoutingCache::new("data/routing_cache.json", 180_000);
    let capital = trading_engine_core::capital::CapitalManager::new(config.capital.reserve_limit_pct)
        .with_budgets(config.capital.budgets.clone());
    let mut engine = trading_engine_core::engine::Engine::new(config, connector.clone(), risk, telegram.clone_for_signal(), bar_cache.clone(), status_cache.clone(), regime_cache.clone(), routing_cache.clone(), capital.clone());

    // Build the perp mark source ONCE for all pairs when trend opts into it.
    // Cloned as Arc per pair below; a single shared client + cache serves all.
    let perp_source: Option<Arc<dyn trading_engine_core::connector::perp_price::PerpPriceSource>> =
        if trend_cfg.perp_mark_source.as_deref() == Some("gateio_usdt_perp") {
            Some(Arc::new(trading_engine_core::connector::perp_price::GateioPerpSource::new()))
        } else {
            None
        };

    // Add strategies for each enabled pair
    for (symbol, pc) in &pair_configs {
        // Grid strategy per pair
        let grid = trading_engine_core::strategy::grid::GridStrategy::new(
            symbol,
            &grid_cfg,
            pc.tick_size,
            pc.step_size,
            telegram.clone_for_signal(),
        );
        engine.add_strategy(Box::new(grid));

        // Trend strategy per pair
        let mut trend = trading_engine_core::strategy::trend::TrendStrategy::new(
            symbol,
            &trend_cfg,
            telegram.clone_for_signal(),
        );
        if let Some(p) = &perp_source {
            trend = trend.with_perp(p.clone());
        }
        engine.add_strategy(Box::new(trend));

        // Mean Reversion strategy per pair — gated by mean_reversion.enabled
        // (grid/trend have no enabled flag and always run; MR is the only one
        // that can be switched off via config.)
        if mr_cfg.enabled {
            let mean_reversion = trading_engine_core::strategy::mean_reversion::MeanReversionStrategy::new(
                symbol,
                &mr_cfg,
                telegram.clone_for_signal(),
            );
            engine.add_strategy(Box::new(mean_reversion));
        }

        // Swing strategy — only on configured pairs (empty = all). The backtest
        // showed edge concentrated on ETH (DOGE loses, XRP inconclusive, BNB needs
        // maker entries), so production gates this explicitly via enabled_pairs.
        if let Some(base) = &swing_cfg {
            let sym_norm = symbol.replace('-', "");
            let allowed = base.enabled_pairs.is_empty()
                || base.enabled_pairs.iter().any(|p| p.replace('-', "") == sym_norm);
            if allowed {
                let mut cfg = base.clone();
                cfg.tick_size = Some(pc.tick_size);
                cfg.step_size = Some(pc.step_size);
                let swing = trading_engine_core::strategy::swing::SwingStrategy::new(
                    symbol,
                    &cfg,
                    telegram.clone_for_signal(),
                );
                engine.add_strategy(Box::new(swing));
            }
        }
    }

    // Spawn HTTP API server
    let api_port: u16 = std::env::var("API_PORT")
        .unwrap_or_else(|_| "3030".to_string())
        .parse()
        .unwrap_or(3030);
    let app_state = trading_engine_core::api::server::AppState::new(connector, bar_cache, status_cache, regime_cache, routing_cache, capital.clone());
    let router = trading_engine_core::api::server::create_router(app_state);
    let listener = tokio::net::TcpListener::bind(format!("0.0.0.0:{}", api_port)).await?;
    info!("API server listening on port {}", api_port);

    // Backfill the unified analytics table from the per-engine journals (once, when
    // empty) so /pnl_all has history before any new trades close.
    trading_engine_core::strategy::trade_journal::backfill_unified_if_empty();

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
