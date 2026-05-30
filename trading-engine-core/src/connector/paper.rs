use anyhow::{Result, anyhow};
use std::collections::HashMap;
use crate::connector::types::*;
use crate::models::order::OrderSide;

const FEE_RATE: f64 = 0.001; // 0.1% per side

struct PaperOrder {
    id: String,
    symbol: String,
    side: OrderSide,
    order_type: OrderTypeReq,
    price: Option<f64>,
    quantity: f64,
}

pub struct PaperTradeEngine {
    balances: HashMap<String, f64>,
    open_orders: Vec<PaperOrder>,
    trade_history: Vec<Fill>,
    next_order_id: u64,
}

impl PaperTradeEngine {
    pub fn new(balances: HashMap<String, f64>) -> Self {
        Self {
            balances,
            open_orders: Vec::new(),
            trade_history: Vec::new(),
            next_order_id: 1,
        }
    }

    pub fn place_order(&mut self, req: &OrderRequest) -> Result<OrderResponse> {
        let id = format!("paper_{}", self.next_order_id);
        self.next_order_id += 1;

        self.open_orders.push(PaperOrder {
            id: id.clone(),
            symbol: req.symbol.clone(),
            side: req.side,
            order_type: req.order_type,
            price: req.price,
            quantity: req.quantity,
        });

        Ok(OrderResponse {
            order_id: id,
            client_order_id: req.client_order_id.clone(),
            symbol: req.symbol.clone(),
            side: req.side,
            price: req.price.unwrap_or(0.0),
            quantity: req.quantity,
            status: OrderStatus::New,
        })
    }

    pub fn cancel_order(&mut self, order_id: &str) -> Result<()> {
        let before = self.open_orders.len();
        self.open_orders.retain(|o| o.id != order_id);
        if self.open_orders.len() == before {
            return Err(anyhow!("Order {} not found", order_id));
        }
        Ok(())
    }

    /// Try to fill open orders at the given market price
    pub fn try_fill_at_price(&mut self, market_price: f64) -> Vec<Fill> {
        let mut fills = Vec::new();
        let mut remaining = Vec::new();

        for order in self.open_orders.drain(..) {
            let should_fill = match (order.side, order.price) {
                (OrderSide::Buy, Some(limit_price)) => market_price <= limit_price,
                (OrderSide::Sell, Some(limit_price)) => market_price >= limit_price,
                (_, None) => true, // Market orders always fill
            };

            if should_fill {
                let fill_price = order.price.unwrap_or(market_price);
                let fill_qty = order.quantity;
                let fee = fill_price * fill_qty * FEE_RATE;

                // Extract base/quote from symbol (handles "BTCUSDT" and "BTC-USDT" formats)
                let (base, quote) = if let Some(pos) = order.symbol.find('-') {
                    (&order.symbol[..pos], &order.symbol[pos+1..])
                } else if order.symbol.ends_with("USDT") {
                    let pos = order.symbol.len() - 4;
                    (&order.symbol[..pos], &order.symbol[pos..])
                } else if order.symbol.ends_with("BUSD") {
                    let pos = order.symbol.len() - 4;
                    (&order.symbol[..pos], &order.symbol[pos..])
                } else if order.symbol.ends_with("BTC") {
                    let pos = order.symbol.len() - 3;
                    (&order.symbol[..pos], &order.symbol[pos..])
                } else if order.symbol.ends_with("ETH") {
                    let pos = order.symbol.len() - 3;
                    (&order.symbol[..pos], &order.symbol[pos..])
                } else {
                    let pos = order.symbol.len().saturating_sub(4);
                    (&order.symbol[..pos], &order.symbol[pos..])
                };

                match order.side {
                    OrderSide::Buy => {
                        *self.balances.entry(base.to_string()).or_insert(0.0) += fill_qty;
                        *self.balances.entry(quote.to_string()).or_insert(0.0) -= fill_price * fill_qty + fee;
                    }
                    OrderSide::Sell => {
                        *self.balances.entry(base.to_string()).or_insert(0.0) -= fill_qty;
                        *self.balances.entry(quote.to_string()).or_insert(0.0) += fill_price * fill_qty - fee;
                    }
                }

                let fill = Fill {
                    fill_id: format!("fill_{}", self.trade_history.len()),
                    order_id: order.id,
                    symbol: order.symbol,
                    side: order.side,
                    price: fill_price,
                    quantity: fill_qty,
                    fee,
                    timestamp: chrono::Utc::now().timestamp_millis(),
                };
                fills.push(fill.clone());
                self.trade_history.push(fill);
            } else {
                remaining.push(order);
            }
        }

        self.open_orders = remaining;
        fills
    }

    pub fn balances(&self) -> &HashMap<String, f64> {
        &self.balances
    }

    pub fn open_order_count(&self) -> usize {
        self.open_orders.len()
    }

    pub fn trade_history(&self) -> &[Fill] {
        &self.trade_history
    }
}

/// Connector trait implementation for paper trading
pub struct PaperTradeConnector {
    engine: std::sync::Mutex<PaperTradeEngine>,
}

impl PaperTradeConnector {
    pub fn new(balances: std::collections::HashMap<String, f64>) -> Self {
        Self {
            engine: std::sync::Mutex::new(PaperTradeEngine::new(balances)),
        }
    }
}

#[async_trait::async_trait]
impl crate::connector::Connector for PaperTradeConnector {
    async fn place_order(&self, req: &OrderRequest) -> anyhow::Result<OrderResponse> {
        let mut engine = self.engine.lock().unwrap();
        engine.place_order(req)
    }

    async fn cancel_order(&self, symbol: &str, order_id: &str) -> anyhow::Result<()> {
        let mut engine = self.engine.lock().unwrap();
        engine.cancel_order(order_id)
    }

    async fn cancel_all_orders(&self, symbol: &str) -> anyhow::Result<Vec<CancelResult>> {
        Ok(Vec::new()) // Not implemented for paper
    }

    async fn get_balances(&self) -> anyhow::Result<std::collections::HashMap<String, f64>> {
        let engine = self.engine.lock().unwrap();
        Ok(engine.balances().clone())
    }

    async fn get_open_orders(&self, symbol: &str) -> anyhow::Result<Vec<OpenOrder>> {
        Ok(Vec::new()) // Not implemented for paper
    }

    async fn get_order_book(&self, symbol: &str, limit: u16) -> anyhow::Result<OrderBook> {
        Ok(OrderBook {
            symbol: symbol.to_string(),
            bids: Vec::new(),
            asks: Vec::new(),
            timestamp: 0,
        })
    }
}
