use std::sync::Arc;
use axum::Router;
use axum::routing::{get, post, delete};
use tower_http::cors::CorsLayer;

use crate::bar_cache::BarCache;
use crate::connector::Connector;
use crate::strategy::status_cache::StrategyStatusCache;
use crate::strategy::regime_cache::RegimeCache;
use crate::strategy::routing_cache::RoutingCache;
use crate::capital::CapitalManager;
use super::order_command::EngineCommandBus;

use super::handlers;

/// Shared application state: an Arc-wrapped Connector accessible by all handlers.
#[derive(Clone)]
pub struct AppState {
    pub connector: Arc<dyn Connector>,
    pub bars: BarCache,
    pub strategies: StrategyStatusCache,
    pub regime_cache: RegimeCache,
    pub routing_cache: RoutingCache,
    pub capital: CapitalManager,
    pub order_commands: EngineCommandBus,
}

impl AppState {
    pub fn new(connector: Arc<dyn Connector>, bars: BarCache, strategies: StrategyStatusCache, regime_cache: RegimeCache, routing_cache: RoutingCache, capital: CapitalManager, order_commands: EngineCommandBus) -> Self {
        Self { connector, bars, strategies, regime_cache, routing_cache, capital, order_commands }
    }
}

/// Build the axum router with all API endpoints.
pub fn create_router(state: AppState) -> Router {
    Router::new()
        .route("/api/v1/health", get(handlers::health))
        .route("/api/v1/order", post(handlers::place_order))
        .route("/api/v1/order", delete(handlers::cancel_order))
        .route("/api/v1/orders", delete(handlers::cancel_all_orders))
        .route("/api/v1/balances", get(handlers::get_balances))
        .route("/api/v1/orders", get(handlers::get_open_orders))
        .route("/api/v1/orderbook", get(handlers::get_order_book))
        .route("/api/v1/klines", get(handlers::get_klines))
        .route("/api/v1/strategies", get(handlers::get_strategies))
        .route("/api/v1/regime", post(handlers::update_regime))
        .route("/api/v1/routing", post(handlers::update_routing))
        .route("/api/v1/capital", get(handlers::get_capital))
        .route("/api/v1/promotion_report", get(handlers::get_promotion_report))
        .route("/api/v1/trades", get(handlers::get_trades))
        .route("/api/v1/positions", get(handlers::get_positions))
        .layer(CorsLayer::permissive())
        .with_state(state)
}
