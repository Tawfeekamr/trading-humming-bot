pub mod types;
pub mod binance_rest;
pub mod binance_ws;
pub mod gateio_rest;
pub mod gateio_ws;
pub mod paper;
pub mod perp_price;
pub mod price_verify;

use async_trait::async_trait;
use anyhow::Result;
use std::collections::HashMap;
use types::*;

#[async_trait]
pub trait Connector: Send + Sync {
    async fn place_order(&self, req: &OrderRequest) -> Result<OrderResponse>;
    async fn cancel_order(&self, symbol: &str, order_id: &str) -> Result<()>;
    async fn cancel_all_orders(&self, symbol: &str) -> Result<Vec<CancelResult>>;
    async fn get_balances(&self) -> Result<HashMap<String, f64>>;
    async fn get_open_orders(&self, symbol: &str) -> Result<Vec<OpenOrder>>;
    async fn get_order_book(&self, symbol: &str, limit: u16) -> Result<OrderBook>;
    async fn get_klines(&self, symbol: &str, interval: &str, limit: u16) -> Result<Vec<crate::models::bar::Bar>>;

    /// Attempt to fill open paper orders for `symbol` at the given market price.
    /// Only orders matching `symbol` are evaluated — orders for other pairs must
    /// not fill against this price. Default: no-op (real exchanges fill server-side).
    async fn try_fill_at_price(&self, _symbol: &str, _market_price: f64) -> Vec<types::Fill> {
        Vec::new()
    }
}
