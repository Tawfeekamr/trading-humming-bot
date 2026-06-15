use anyhow::{Result, anyhow};
use std::collections::HashMap;
use crate::connector::types::*;
use crate::connector::binance_rest::BinanceRest;
use crate::models::order::OrderSide;

const FEE_RATE: f64 = 0.001; // 0.1% per side

struct PaperOrder {
    id: String,
    client_order_id: Option<String>,
    symbol: String,
    side: OrderSide,
    price: Option<f64>,
    quantity: f64,
    order_type: OrderTypeReq,
}

pub struct PaperTradeEngine {
    balances: HashMap<String, f64>,
    open_orders: Vec<PaperOrder>,
    trade_history: Vec<Fill>,
    next_order_id: u64,
    /// Minimum gap between fills on the same symbol (paper instant-fill churn
    /// guard). 0 = disabled. See set_fill_cooldown.
    fill_cooldown_ms: i64,
    last_fill_ms: HashMap<String, i64>,
}

impl PaperTradeEngine {
    pub fn new(balances: HashMap<String, f64>) -> Self {
        Self {
            balances,
            open_orders: Vec::new(),
            trade_history: Vec::new(),
            next_order_id: 1,
            fill_cooldown_ms: 0,
            last_fill_ms: HashMap::new(),
        }
    }

    /// Set the per-symbol fill cooldown. After a fill on a symbol, further
    /// fills on that symbol are suppressed for this many milliseconds — this
    /// keeps paper mode from instantly re-filling entry/exit loops.
    pub fn set_fill_cooldown(&mut self, ms: i64) {
        self.fill_cooldown_ms = ms.max(0);
    }

    pub fn place_order(&mut self, req: &OrderRequest) -> Result<OrderResponse> {
        let id = format!("paper_{}", self.next_order_id);
        self.next_order_id += 1;

        self.open_orders.push(PaperOrder {
            id: id.clone(),
            client_order_id: req.client_order_id.clone(),
            symbol: req.symbol.clone(),
            side: req.side,
            price: req.price,
            quantity: req.quantity,
            order_type: req.order_type,
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

    /// Try to fill open orders for `symbol` at the given market price.
    /// Only orders whose symbol matches are evaluated — orders for other pairs
    /// are left untouched so they don't fill against an unrelated price.
    pub fn try_fill_at_price(&mut self, symbol: &str, market_price: f64) -> Vec<Fill> {
        let sym_norm = symbol.replace("-", "");

        // Per-symbol cooldown: if this pair filled very recently, leave its
        // orders in the book so entry/exit can't instantly refuel a churn loop.
        let now_ms = chrono::Utc::now().timestamp_millis();
        if self.fill_cooldown_ms > 0 {
            if let Some(&last) = self.last_fill_ms.get(&sym_norm) {
                if now_ms - last < self.fill_cooldown_ms {
                    return Vec::new();
                }
            }
        }

        let mut fills = Vec::new();
        let mut remaining = Vec::new();

        for order in self.open_orders.drain(..) {
            // Skip orders for other pairs — a BNB order must not fill just
            // because the XRP orderbook price crossed its limit.
            if order.symbol.replace("-", "") != sym_norm {
                remaining.push(order);
                continue;
            }

            let should_fill = match order.order_type {
                // Stop-market: triggers when price crosses the stop, then fills
                // at market (taker). Sells trigger on the way down, buys on the way up.
                OrderTypeReq::StopMarket { stop_price } => match order.side {
                    OrderSide::Sell => market_price <= stop_price,
                    OrderSide::Buy => market_price >= stop_price,
                },
                // Limit / LimitMaker fill when the limit is crossed (maker vs
                // taker distinction isn't modeled in paper — same fee either way);
                // Market always fills.
                _ => match (order.side, order.price) {
                    (OrderSide::Buy, Some(limit_price)) => market_price <= limit_price,
                    (OrderSide::Sell, Some(limit_price)) => market_price >= limit_price,
                    (_, None) => true, // Market orders always fill
                },
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
                    client_order_id: order.client_order_id.clone(),
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
        if !fills.is_empty() && self.fill_cooldown_ms > 0 {
            self.last_fill_ms.insert(sym_norm, now_ms);
        }
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
    market_data: Option<BinanceRest>,
}

impl PaperTradeConnector {
    /// Paper-only constructor (no real market data). Kept for backward compat.
    pub fn new(balances: std::collections::HashMap<String, f64>) -> Self {
        Self {
            engine: std::sync::Mutex::new(PaperTradeEngine::new(balances)),
            market_data: None,
        }
    }

    /// Paper trading with real Binance market data for klines/orderbook.
    /// Trading operations (place/cancel/balances) remain paper/simulated.
    pub fn with_market_data(
        balances: std::collections::HashMap<String, f64>,
        api_key: &str,
        api_secret: &str,
        testnet: bool,
    ) -> Self {
        Self {
            engine: std::sync::Mutex::new(PaperTradeEngine::new(balances)),
            market_data: Some(BinanceRest::new(api_key, api_secret, testnet)),
        }
    }

    /// Set the per-symbol fill cooldown (ms). Call after constructing.
    pub fn with_fill_cooldown(self, ms: i64) -> Self {
        if let Ok(mut engine) = self.engine.lock() {
            engine.set_fill_cooldown(ms);
        }
        self
    }
}

#[async_trait::async_trait]
impl crate::connector::Connector for PaperTradeConnector {
    async fn place_order(&self, req: &OrderRequest) -> anyhow::Result<OrderResponse> {
        let mut engine = self.engine.lock().unwrap();
        engine.place_order(req)
    }

    async fn cancel_order(&self, _symbol: &str, order_id: &str) -> anyhow::Result<()> {
        let mut engine = self.engine.lock().unwrap();
        engine.cancel_order(order_id)
    }

    async fn cancel_all_orders(&self, _symbol: &str) -> anyhow::Result<Vec<CancelResult>> {
        Ok(Vec::new()) // Not implemented for paper
    }

    async fn get_balances(&self) -> anyhow::Result<std::collections::HashMap<String, f64>> {
        let engine = self.engine.lock().unwrap();
        Ok(engine.balances().clone())
    }

    async fn get_open_orders(&self, _symbol: &str) -> anyhow::Result<Vec<OpenOrder>> {
        Ok(Vec::new()) // Not implemented for paper
    }

    async fn get_order_book(&self, symbol: &str, limit: u16) -> anyhow::Result<OrderBook> {
        if let Some(ref md) = self.market_data {
            md.get_order_book(symbol, limit).await
        } else {
            Ok(OrderBook {
                symbol: symbol.to_string(),
                bids: Vec::new(),
                asks: Vec::new(),
                timestamp: 0,
            })
        }
    }

    async fn get_klines(&self, symbol: &str, interval: &str, limit: u16) -> anyhow::Result<Vec<crate::models::bar::Bar>> {
        if let Some(ref md) = self.market_data {
            md.get_klines(symbol, interval, limit).await
        } else {
            Ok(Vec::new())
        }
    }

    async fn try_fill_at_price(&self, symbol: &str, market_price: f64) -> Vec<Fill> {
        let mut engine = self.engine.lock().unwrap();
        engine.try_fill_at_price(symbol, market_price)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn engine() -> PaperTradeEngine {
        let mut bal = HashMap::new();
        bal.insert("BTC".to_string(), 1.0);
        bal.insert("USDT".to_string(), 10_000.0);
        PaperTradeEngine::new(bal)
    }

    fn sell_stop(stop: f64, qty: f64) -> OrderRequest {
        OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Sell,
            order_type: OrderTypeReq::StopMarket { stop_price: stop },
            price: None,
            quantity: qty,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }
    }

    #[test]
    fn sell_stop_does_not_trigger_above_stop_price() {
        let mut e = engine();
        e.place_order(&sell_stop(50_000.0, 0.5)).unwrap();
        // Price still above the stop → protective exit must NOT fire.
        assert!(e.try_fill_at_price("BTCUSDT", 51_000.0).is_empty());
    }

    #[test]
    fn sell_stop_triggers_when_price_falls_through() {
        let mut e = engine();
        e.place_order(&sell_stop(50_000.0, 0.5)).unwrap();
        let fills = e.try_fill_at_price("BTCUSDT", 49_900.0);
        assert_eq!(fills.len(), 1, "stop should trigger once price <= stop");
        // STOP_MARKET fills at market (the trigger price), not the stop price.
        assert!((fills[0].price - 49_900.0).abs() < 1e-9);
        assert_eq!(fills[0].side, OrderSide::Sell);
    }

    #[test]
    fn buy_stop_triggers_on_upside_cross_only() {
        let mut e = engine();
        e.place_order(&OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::StopMarket { stop_price: 50_000.0 },
            price: None,
            quantity: 0.1,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }).unwrap();
        assert!(e.try_fill_at_price("BTCUSDT", 49_000.0).is_empty());
        assert_eq!(e.try_fill_at_price("BTCUSDT", 50_500.0).len(), 1);
    }

    #[test]
    fn limit_maker_fills_like_a_passive_limit_at_its_price() {
        let mut e = engine();
        e.place_order(&OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::LimitMaker,
            price: Some(50_000.0),
            quantity: 0.1,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }).unwrap();
        // Buy limit rests below market → no fill while price is higher.
        assert!(e.try_fill_at_price("BTCUSDT", 51_000.0).is_empty());
        let fills = e.try_fill_at_price("BTCUSDT", 50_000.0);
        assert_eq!(fills.len(), 1);
        // Maker fills at its resting price, not the market price.
        assert!((fills[0].price - 50_000.0).abs() < 1e-9);
    }
}
