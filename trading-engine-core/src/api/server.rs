use std::sync::Arc;
use axum::Router;
use axum::routing::{get, post, delete};
use tower_http::cors::CorsLayer;

use crate::connector::Connector;

use super::handlers;

/// Shared application state: an Arc-wrapped Connector accessible by all handlers.
#[derive(Clone)]
pub struct AppState {
    pub connector: Arc<dyn Connector>,
}

impl AppState {
    pub fn new(connector: Arc<dyn Connector>) -> Self {
        Self { connector }
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
        .layer(CorsLayer::permissive())
        .with_state(state)
}
