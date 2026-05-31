pub mod types;
pub mod binance_rest;
pub mod binance_ws;
pub mod paper;

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
}
