use axum::extract::{State, Query};
use axum::response::IntoResponse;
use axum::Json;
use serde::Deserialize;

use crate::connector::types::*;
use crate::strategy::regime_cache::RegimeUpdate;
use super::server::AppState;

// ── Query parameter structs ──────────────────────────────────────────

#[derive(Debug, Deserialize)]
pub struct SymbolQuery {
    pub symbol: String,
}

#[derive(Debug, Deserialize)]
pub struct CancelOrderQuery {
    pub symbol: String,
    pub order_id: String,
}

#[derive(Debug, Deserialize)]
pub struct OrderBookQuery {
    pub symbol: String,
    pub limit: Option<u16>,
}

#[derive(Debug, Deserialize)]
pub struct KlinesQuery {
    pub symbol: String,
    pub interval: Option<String>,
    pub limit: Option<u16>,
}

// ── Health check ─────────────────────────────────────────────────────

pub async fn health() -> Json<serde_json::Value> {
    Json(serde_json::json!({ "status": "ok" }))
}

// ── Order placement ──────────────────────────────────────────────────

pub async fn place_order(
    State(state): State<AppState>,
    Json(req): Json<OrderRequest>,
) -> impl IntoResponse {
    match state.connector.place_order(&req).await {
        Ok(resp) => Ok(Json(resp)),
        Err(e) => Err((
            axum::http::StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "error": e.to_string() })),
        )),
    }
}

// ── Order cancellation ───────────────────────────────────────────────

pub async fn cancel_order(
    State(state): State<AppState>,
    Query(params): Query<CancelOrderQuery>,
) -> impl IntoResponse {
    match state.connector.cancel_order(&params.symbol, &params.order_id).await {
        Ok(()) => Ok(Json(serde_json::json!({ "cancelled": true }))),
        Err(e) => Err((
            axum::http::StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "error": e.to_string() })),
        )),
    }
}

pub async fn cancel_all_orders(
    State(state): State<AppState>,
    Query(params): Query<SymbolQuery>,
) -> impl IntoResponse {
    match state.connector.cancel_all_orders(&params.symbol).await {
        Ok(results) => Ok(Json(results)),
        Err(e) => Err((
            axum::http::StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "error": e.to_string() })),
        )),
    }
}

// ── Account data ─────────────────────────────────────────────────────

pub async fn get_balances(
    State(state): State<AppState>,
) -> impl IntoResponse {
    match state.connector.get_balances().await {
        Ok(balances) => {
            let list: Vec<Balance> = balances
                .into_iter()
                .map(|(asset, free)| Balance {
                    asset,
                    free,
                    locked: 0.0,
                })
                .collect();
            Ok(Json(list))
        }
        Err(e) => Err((
            axum::http::StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "error": e.to_string() })),
        )),
    }
}

// ── Open orders ──────────────────────────────────────────────────────

pub async fn get_open_orders(
    State(state): State<AppState>,
    Query(params): Query<SymbolQuery>,
) -> impl IntoResponse {
    match state.connector.get_open_orders(&params.symbol).await {
        Ok(orders) => Ok(Json(orders)),
        Err(e) => Err((
            axum::http::StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "error": e.to_string() })),
        )),
    }
}

// ── Order book ───────────────────────────────────────────────────────

pub async fn get_order_book(
    State(state): State<AppState>,
    Query(params): Query<OrderBookQuery>,
) -> impl IntoResponse {
    let limit = params.limit.unwrap_or(10);
    match state.connector.get_order_book(&params.symbol, limit).await {
        Ok(book) => Ok(Json(book)),
        Err(e) => Err((
            axum::http::StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "error": e.to_string() })),
        )),
    }
}

// ── Klines / candlesticks ───────────────────────────────────────────

pub async fn get_klines(
    State(state): State<AppState>,
    Query(params): Query<KlinesQuery>,
) -> impl IntoResponse {
    let interval = params.interval.as_deref().unwrap_or("1m");
    let limit = params.limit.unwrap_or(100) as usize;

    // Try the engine's bar cache first (handles dash/no-dash normalization).
    let cached = state.bars.get(&params.symbol, limit).await;
    if cached.len() >= limit {
        return Ok::<_, (axum::http::StatusCode, Json<serde_json::Value>)>(Json(cached));
    }

    // Cache has fewer bars than requested — supplement from connector.
    match state.connector.get_klines(&params.symbol, interval, limit as u16).await {
        Ok(bars) => {
            if bars.len() > cached.len() {
                Ok(Json(bars))
            } else if !cached.is_empty() {
                Ok(Json(cached))
            } else {
                Ok(Json(bars))
            }
        }
        Err(_) if !cached.is_empty() => {
            // Connector failed but we have cached bars — return what we have
            Ok(Json(cached))
        }
        Err(e) => Err((
            axum::http::StatusCode::BAD_GATEWAY,
            Json(serde_json::json!({ "error": e.to_string() })),
        )),
    }
}

/// Get current status of all strategies (grid + trend per pair)
pub async fn get_strategies(
    State(state): State<AppState>,
) -> Json<Vec<crate::strategy::StrategyStatus>> {
    let statuses = state.strategies.snapshot().await;
    Json(statuses)
}

// ── Regime update (pushed by Python ML) ───────────────────────────────

pub async fn update_regime(
    State(state): State<AppState>,
    Json(updates): Json<Vec<RegimeUpdate>>,
) -> Json<serde_json::Value> {
    let count = updates.len();
    state.regime_cache.update(&updates).await;
    state.regime_cache.persist().await;
    tracing::info!("Regime updated via API: {} entries", count);
    Json(serde_json::json!({ "updated": count }))
}

// ── Tests ────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use axum::Router;
    use std::collections::HashMap;
    use std::sync::Arc;
    use tower::ServiceExt;

    use crate::connector::Connector;
    use crate::connector::paper::PaperTradeConnector;
    use crate::bar_cache::BarCache;
    use crate::strategy::status_cache::StrategyStatusCache;

    use crate::api::server::{AppState, create_router};
    use crate::strategy::regime_cache::RegimeCache;

    fn test_app() -> Router {
        let mut balances = HashMap::new();
        balances.insert("USDT".to_string(), 10000.0);
        let connector = Arc::new(PaperTradeConnector::new(balances)) as Arc<dyn Connector>;
        let regime_cache = RegimeCache::new("data/regime_cache.json");
        create_router(AppState::new(connector, BarCache::new(), StrategyStatusCache::new(), regime_cache))
    }

    #[tokio::test]
    async fn test_health() {
        let app = test_app();
        let req = Request::builder()
            .uri("/api/v1/health")
            .body(Body::empty())
            .unwrap();
        let resp = app.oneshot(req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_get_balances() {
        let app = test_app();
        let req = Request::builder()
            .uri("/api/v1/balances")
            .body(Body::empty())
            .unwrap();
        let resp = app.oneshot(req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_place_order() {
        let app = test_app();
        let body = serde_json::json!({
            "symbol": "BTCUSDT",
            "side": "BUY",
            "order_type": "Limit",
            "price": 50000.0,
            "quantity": 0.001,
            "time_in_force": "Gtc",
            "client_order_id": null
        });
        let req = Request::builder()
            .method("POST")
            .uri("/api/v1/order")
            .header("content-type", "application/json")
            .body(Body::from(serde_json::to_string(&body).unwrap()))
            .unwrap();
        let resp = app.oneshot(req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_get_order_book() {
        let app = test_app();
        let req = Request::builder()
            .uri("/api/v1/orderbook?symbol=BTCUSDT&limit=5")
            .body(Body::empty())
            .unwrap();
        let resp = app.oneshot(req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_get_klines() {
        let app = test_app();
        let req = Request::builder()
            .uri("/api/v1/klines?symbol=BTCUSDT&interval=1m&limit=10")
            .body(Body::empty())
            .unwrap();
        let resp = app.oneshot(req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn test_get_open_orders() {
        let app = test_app();
        let req = Request::builder()
            .uri("/api/v1/orders?symbol=BTCUSDT")
            .body(Body::empty())
            .unwrap();
        let resp = app.oneshot(req).await.unwrap();
        assert_eq!(resp.status(), StatusCode::OK);
    }
}
