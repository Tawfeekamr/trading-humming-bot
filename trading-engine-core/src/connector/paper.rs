use anyhow::{Result, anyhow};
use std::collections::HashMap;
use std::path::PathBuf;
use serde::{Deserialize, Serialize};
use tracing::warn;
use crate::connector::types::*;
use crate::connector::binance_rest::BinanceRest;
use crate::models::order::OrderSide;

#[derive(Clone, Serialize, Deserialize)]
struct PaperOrder {
    id: String,
    client_order_id: Option<String>,
    symbol: String,
    side: OrderSide,
    price: Option<f64>,
    quantity: f64,
    order_type: OrderTypeReq,
    reduce_only: bool,
}

#[derive(Serialize, Deserialize)]
struct PaperState {
    open_orders: Vec<PaperOrder>,
    next_order_id: u64,
}

/// Split a trading symbol into (base, quote), borrowing from the input.
/// Handles "BTC-USDT" and "BTCUSDT" forms.
fn split_pair(symbol: &str) -> (&str, &str) {
    if let Some(pos) = symbol.find('-') {
        (&symbol[..pos], &symbol[pos + 1..])
    } else if symbol.ends_with("USDT") || symbol.ends_with("BUSD") {
        let pos = symbol.len() - 4;
        (&symbol[..pos], &symbol[pos..])
    } else if symbol.ends_with("BTC") || symbol.ends_with("ETH") {
        let pos = symbol.len() - 3;
        (&symbol[..pos], &symbol[pos..])
    } else {
        let pos = symbol.len().saturating_sub(4);
        (&symbol[..pos], &symbol[pos..])
    }
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
    /// Adverse slippage in bps applied to TAKER fills only (Market, StopMarket).
    /// Maker limits fill at their resting price. 0 = off (preserve old behavior).
    slippage_bps: f64,
    taker_fee_bps: f64,
    maker_fee_bps: f64,
    /// Cumulative realized PnL across round trips (sells against average cost,
    /// fees deducted). Backs the peak-equity invariant bound.
    realized_pnl: f64,
    /// Average cost basis per base asset: (quantity held, average entry price).
    /// Buys weight into it (fees capitalize); sells draw it down. Backs the
    /// unrealised-PnL half of the invariant bound.
    cost_basis: HashMap<String, (f64, f64)>,
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
            slippage_bps: 0.0,
            taker_fee_bps: 10.0, // 0.1%
            maker_fee_bps: 10.0, // 0.1%
            realized_pnl: 0.0,
            cost_basis: HashMap::new(),
        }
    }

    /// Set the per-symbol fill cooldown. After a fill on a symbol, further
    /// fills on that symbol are suppressed for this many milliseconds — this
    /// keeps paper mode from instantly re-filling entry/exit loops.
    pub fn set_fill_cooldown(&mut self, ms: i64) {
        self.fill_cooldown_ms = ms.max(0);
    }

    /// Configure slippage (taker-only) and tiered fees. Defaults (0 / 10 / 10)
    /// reproduce the original flat-0.1%-fee, zero-slippage behavior.
    pub fn set_realism(&mut self, slippage_bps: f64, taker_fee_bps: f64, maker_fee_bps: f64) {
        self.slippage_bps = slippage_bps.max(0.0);
        self.taker_fee_bps = taker_fee_bps.max(0.0);
        self.maker_fee_bps = maker_fee_bps.max(0.0);
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
            reduce_only: req.reduce_only,
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
                let (base, quote) = split_pair(&order.symbol);

                // Enforce reduce_only for BUY-side short closes: a reduce_only
                // buy may only CLOSE an existing short (base <= -qty), never
                // open one. (The sell side is subsumed by the naked-sell rule
                // below, which applies to reduce_only and plain sells alike.)
                if order.reduce_only && order.side == OrderSide::Buy {
                    let base_bal = *self.balances.get(base).unwrap_or(&0.0);
                    if base_bal > -order.quantity + 1e-12 {
                        remaining.push(order);
                        continue;
                    }
                }

                // A SELL may never exceed the base actually held: filling beyond
                // it pushes the base negative and credits phantom USDT (Oct 2026
                // incident: a +$24.9k phantom peak inflated the breaker until it
                // latched forever). Zero base → reject and drop the order (it can
                // never fill); partial base → clamp to what is held. Reduce-only
                // bypasses the circuit-breaker HALT, never this balance check.
                let fill_qty = if order.side == OrderSide::Sell {
                    let base_bal = *self.balances.get(base).unwrap_or(&0.0);
                    if base_bal <= 1e-12 {
                        warn!(
                            "NAKED SELL rejected: sell {} {} attempted with {} held — crediting nothing \
                             (order {}, client_order_id={:?}, reduce_only={})",
                            order.quantity, base, base_bal, order.id, order.client_order_id, order.reduce_only
                        );
                        continue; // drop: a sell with no base can never fill
                    }
                    if order.quantity > base_bal + 1e-12 {
                        warn!(
                            "sell clamped to held base: requested {} {}, held {} — filling {} only \
                             (order {}, client_order_id={:?}, reduce_only={})",
                            order.quantity, base, base_bal, base_bal, order.id, order.client_order_id, order.reduce_only
                        );
                        base_bal
                    } else {
                        order.quantity
                    }
                } else {
                    order.quantity
                };
                // Maker limits fill at their resting price (no slippage);
                // taker orders (Market, StopMarket) fill at the mark minus
                // adverse slippage (buys higher, sells lower).
                let is_maker = matches!(order.order_type, OrderTypeReq::Limit | OrderTypeReq::LimitMaker);
                let fill_price = if is_maker {
                    order.price.unwrap_or(market_price)
                } else {
                    let adverse = match order.side {
                        OrderSide::Buy => 1.0,
                        OrderSide::Sell => -1.0,
                    };
                    market_price * (1.0 + adverse * self.slippage_bps / 1e4)
                };
                let fee_bps = if is_maker { self.maker_fee_bps } else { self.taker_fee_bps };
                let fee = fill_price * fill_qty * (fee_bps / 1e4);

                match order.side {
                    OrderSide::Buy => {
                        *self.balances.entry(base.to_string()).or_insert(0.0) += fill_qty;
                        *self.balances.entry(quote.to_string()).or_insert(0.0) -= fill_price * fill_qty + fee;
                        // Weight into average cost; the fee capitalizes into basis.
                        let (bq, bp) = self.cost_basis.get(base).copied().unwrap_or((0.0, 0.0));
                        let total_qty = bq + fill_qty;
                        let avg = if total_qty > 0.0 {
                            (bq * bp + fill_price * fill_qty + fee) / total_qty
                        } else {
                            0.0
                        };
                        self.cost_basis.insert(base.to_string(), (total_qty, avg));
                    }
                    OrderSide::Sell => {
                        *self.balances.entry(base.to_string()).or_insert(0.0) -= fill_qty;
                        *self.balances.entry(quote.to_string()).or_insert(0.0) += fill_price * fill_qty - fee;
                        // Realize against average cost. Seeded inventory with no
                        // tracked basis counts as basis 0 — the bound errs loose
                        // (fewer false alerts), and phantom credits are still
                        // caught by the naked-sell clamp upstream.
                        let (bq, bp) = self.cost_basis.get(base).copied().unwrap_or((0.0, 0.0));
                        self.realized_pnl += (fill_price - bp) * fill_qty - fee;
                        let remaining_basis_qty = (bq - fill_qty).max(0.0);
                        if remaining_basis_qty > 1e-12 {
                            self.cost_basis.insert(base.to_string(), (remaining_basis_qty, bp));
                        } else {
                            self.cost_basis.remove(base);
                        }
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

    /// Cumulative realized PnL across round trips (fees deducted).
    pub fn realized_pnl(&self) -> f64 {
        self.realized_pnl
    }

    /// Average cost basis per base asset: (quantity, average entry price).
    pub fn cost_basis(&self) -> &HashMap<String, (f64, f64)> {
        &self.cost_basis
    }

    pub fn open_order_count(&self) -> usize {
        self.open_orders.len()
    }

    pub fn trade_history(&self) -> &[Fill] {
        &self.trade_history
    }

    pub fn open_orders(&self, symbol: &str) -> Vec<OpenOrder> {
        let sym_norm = symbol.replace("-", "");
        self.open_orders
            .iter()
            .filter(|order| order.symbol.replace("-", "") == sym_norm)
            .map(|order| OpenOrder {
                order_id: order.id.clone(),
                symbol: order.symbol.clone(),
                side: order.side,
                price: order.price.unwrap_or(0.0),
                quantity: order.quantity,
                filled_quantity: 0.0,
                status: OrderStatus::New,
            })
            .collect()
    }

    pub fn cancel_all_orders(&mut self, symbol: &str) -> Vec<CancelResult> {
        let sym_norm = symbol.replace("-", "");
        let mut cancelled = Vec::new();
        self.open_orders.retain(|order| {
            if order.symbol.replace("-", "") == sym_norm {
                cancelled.push(CancelResult {
                    order_id: order.id.clone(),
                    symbol: order.symbol.clone(),
                });
                false
            } else {
                true
            }
        });
        cancelled
    }
}

/// Connector trait implementation for paper trading
pub struct PaperTradeConnector {
    engine: std::sync::Mutex<PaperTradeEngine>,
    market_data: Option<BinanceRest>,
    state_path: Option<PathBuf>,
}

impl PaperTradeConnector {
    /// Paper-only constructor (no real market data). Kept for backward compat.
    pub fn new(balances: std::collections::HashMap<String, f64>) -> Self {
        Self {
            engine: std::sync::Mutex::new(PaperTradeEngine::new(balances)),
            market_data: None,
            state_path: None,
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
            state_path: None,
        }
    }

    pub fn with_state_path(mut self, path: impl Into<PathBuf>) -> Self {
        let path = path.into();
        if let Ok(content) = std::fs::read_to_string(&path) {
            if let Ok(state) = serde_json::from_str::<PaperState>(&content) {
                if let Ok(mut engine) = self.engine.lock() {
                    engine.open_orders = state.open_orders;
                    engine.next_order_id = state.next_order_id.max(1);
                }
            }
        }
        self.state_path = Some(path);
        self
    }

    fn persist_state(&self) -> anyhow::Result<()> {
        let Some(path) = &self.state_path else { return Ok(()); };
        let content = {
            let engine = self.engine.lock().unwrap();
            serde_json::to_string_pretty(&PaperState {
                open_orders: engine.open_orders.clone(),
                next_order_id: engine.next_order_id,
            })?
        };
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        std::fs::write(path, content)?;
        Ok(())
    }

    /// Set the per-symbol fill cooldown (ms). Call after constructing.
    pub fn with_fill_cooldown(self, ms: i64) -> Self {
        if let Ok(mut engine) = self.engine.lock() {
            engine.set_fill_cooldown(ms);
        }
        self
    }

    /// Configure slippage + tiered fees. Call after constructing.
    pub fn with_realism(self, slippage_bps: f64, taker_fee_bps: f64, maker_fee_bps: f64) -> Self {
        if let Ok(mut engine) = self.engine.lock() {
            engine.set_realism(slippage_bps, taker_fee_bps, maker_fee_bps);
        }
        self
    }
}

#[async_trait::async_trait]
impl crate::connector::Connector for PaperTradeConnector {
    async fn place_order(&self, req: &OrderRequest) -> anyhow::Result<OrderResponse> {
        let resp = {
            let mut engine = self.engine.lock().unwrap();
            engine.place_order(req)?
        };
        self.persist_state()?;
        Ok(resp)
    }

    async fn cancel_order(&self, _symbol: &str, order_id: &str) -> anyhow::Result<()> {
        {
            let mut engine = self.engine.lock().unwrap();
            engine.cancel_order(order_id)?;
        }
        self.persist_state()
    }

    async fn cancel_all_orders(&self, symbol: &str) -> anyhow::Result<Vec<CancelResult>> {
        let cancelled = {
            let mut engine = self.engine.lock().unwrap();
            engine.cancel_all_orders(symbol)
        };
        self.persist_state()?;
        Ok(cancelled)
    }

    async fn get_balances(&self) -> anyhow::Result<std::collections::HashMap<String, f64>> {
        let engine = self.engine.lock().unwrap();
        Ok(engine.balances().clone())
    }

    async fn get_open_orders(&self, symbol: &str) -> anyhow::Result<Vec<OpenOrder>> {
        let engine = self.engine.lock().unwrap();
        Ok(engine.open_orders(symbol))
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
        let (fills, book_changed) = {
            let mut engine = self.engine.lock().unwrap();
            let before = engine.open_order_count();
            let fills = engine.try_fill_at_price(symbol, market_price);
            (fills, engine.open_order_count() != before)
        };
        // Persist on fills, but also when the book shrank without one — a
        // rejected naked sell is dropped from the book and must not resurrect
        // from paper_orders.json on the next restart.
        if !fills.is_empty() || book_changed {
            let _ = self.persist_state();
        }
        fills
    }

    /// Boot reconciliation for a reseeded paper book: re-fund a position whose
    /// base was wiped by a restart. Credits `qty` base AND debits
    /// `qty × entry_price` cash — both halves of the original BUY — so no USDT
    /// is created: at entry-price marks, equity is unchanged by this call.
    /// Returns 0.0 without touching the book when the base is already held
    /// (idempotent) or when cash cannot cover the cost (funding would mint
    /// value; the naked-sell clamp still bounds that position's exits).
    async fn fund_reconstructed_position(&self, symbol: &str, qty: f64, entry_price: f64) -> anyhow::Result<f64> {
        let mut engine = self.engine.lock().unwrap();
        let (base, quote) = {
            let (b, q) = split_pair(symbol);
            (b.to_string(), q.to_string())
        };
        let held = *engine.balances().get(&base).unwrap_or(&0.0);
        if held >= qty - 1e-12 {
            return Ok(0.0); // already funded — nothing to reconstruct
        }
        let cost = qty * entry_price;
        let cash = *engine.balances().get(&quote).unwrap_or(&0.0);
        if cash + 1e-9 < cost {
            warn!(
                "position {} NOT reconstructed into paper book: needs ${:.2} cash, book holds ${:.2} — funding it would create USDT from nothing",
                symbol, cost, cash
            );
            return Ok(0.0);
        }
        *engine.balances.entry(base.clone()).or_insert(0.0) += qty;
        *engine.balances.entry(quote).or_insert(0.0) -= cost;
        // Seed cost basis at the entry price so the funded position's
        // unrealised P&L counts toward the peak-equity invariant bound —
        // otherwise reconstruction itself would look like a violation.
        let (bq, bp) = engine.cost_basis.get(&base).copied().unwrap_or((0.0, 0.0));
        let total_qty = bq + qty;
        let avg = if total_qty > 0.0 { (bq * bp + qty * entry_price) / total_qty } else { 0.0 };
        engine.cost_basis.insert(base, (total_qty, avg));
        Ok(qty)
    }

    /// Paper-book stats backing the peak-equity invariant (Task 6). Only the
    /// paper connector tracks these; live connectors keep balances at the
    /// exchange and return the trait defaults (None).
    fn paper_realized_pnl(&self) -> Option<f64> {
        let engine = self.engine.lock().unwrap();
        Some(engine.realized_pnl())
    }

    fn paper_cost_basis(&self) -> Option<HashMap<String, (f64, f64)>> {
        let engine = self.engine.lock().unwrap();
        Some(engine.cost_basis().clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::connector::Connector;

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

    fn limit_sell(qty: f64, reduce_only: bool) -> OrderRequest {
        OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Sell,
            order_type: OrderTypeReq::Limit,
            price: Some(51_000.0),
            quantity: qty,
            time_in_force: None,
            client_order_id: None,
            reduce_only,
        }
    }

    /// A reduce_only sell must NOT fill when the account holds none of the base —
    /// otherwise paper lets grid (and any flat strategy) "sell" inventory it never
    /// bought, opening a naked short that shows as fake profit. Live exchanges
    /// reject reduce_only sells with no position; paper must match.
    #[test]
    fn reduce_only_sell_does_not_fill_without_inventory() {
        let mut bal = HashMap::new();
        bal.insert("USDT".to_string(), 10_000.0); // no BTC held
        let mut e = PaperTradeEngine::new(bal);
        e.place_order(&limit_sell(0.5, true)).unwrap();
        // Price rises to the sell limit — but no inventory ⇒ must not fill.
        let fills = e.try_fill_at_price("BTCUSDT", 51_000.0);
        assert!(fills.is_empty(), "reduce_only sell must NOT fill with no inventory");
        let btc = e.balances().get("BTC").copied().unwrap_or(0.0);
        assert!(btc >= 0.0, "reduce_only sell must never push base balance negative (no naked short)");
    }

    /// A reduce_only sell must still fill normally when the base IS held (closing a
    /// long) — this is MR/swing/trend exits and a real grid round-trip.
    #[test]
    fn reduce_only_sell_fills_when_inventory_exists() {
        let mut e = engine(); // holds 1.0 BTC
        e.place_order(&limit_sell(0.5, true)).unwrap();
        let fills = e.try_fill_at_price("BTCUSDT", 51_000.0);
        assert_eq!(fills.len(), 1, "reduce_only sell must fill when you hold the base");
        let btc = e.balances().get("BTC").copied().unwrap_or(0.0);
        assert!((btc - 0.5).abs() < 1e-9, "BTC should drop 1.0 → 0.5");
    }

    fn market_sell(qty: f64, reduce_only: bool) -> OrderRequest {
        OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Sell,
            order_type: OrderTypeReq::Market,
            price: None,
            quantity: qty,
            time_in_force: None,
            client_order_id: None,
            reduce_only,
        }
    }

    // Root cause B (Oct 2026 phantom peak): a paper SELL beyond the base
    // actually held pushed the base negative and credited phantom USDT — the
    // +$24.9k phantom peak. A sell may never exceed held base: zero base →
    // reject (no fill, no credit, order removed); partial base → clamp.
    // Reduce-only bypasses the HALT, never this balance check.
    #[test]
    fn zero_base_reduce_only_sell_is_rejected_and_credits_nothing() {
        let mut bal = HashMap::new();
        bal.insert("USDT".to_string(), 10_000.0); // no BTC held
        let mut e = PaperTradeEngine::new(bal);
        e.place_order(&market_sell(0.5, true)).unwrap();
        let fills = e.try_fill_at_price("BTCUSDT", 50_000.0);
        assert!(fills.is_empty(), "naked reduce-only sell must be rejected, not filled");
        assert_eq!(
            e.balances().get("USDT").copied().unwrap_or(0.0),
            10_000.0,
            "a rejected sell must credit no USDT"
        );
        assert_eq!(e.open_order_count(), 0, "rejected sell is removed from the book, not left resting");
    }

    #[test]
    fn zero_base_plain_sell_is_rejected_and_credits_nothing() {
        let mut bal = HashMap::new();
        bal.insert("USDT".to_string(), 10_000.0);
        let mut e = PaperTradeEngine::new(bal);
        e.place_order(&market_sell(0.5, false)).unwrap();
        let fills = e.try_fill_at_price("BTCUSDT", 50_000.0);
        assert!(fills.is_empty(), "non-reduce-only naked sell must also be rejected");
        assert_eq!(
            e.balances().get("USDT").copied().unwrap_or(0.0),
            10_000.0,
            "no phantom USDT from a sell with no inventory"
        );
        let btc = e.balances().get("BTC").copied().unwrap_or(0.0);
        assert!(btc.abs() < 1e-12, "base must stay at zero, got {}", btc);
    }

    /// 4efac99 + root-cause-B regression at the book layer: two exit sells for
    /// one position, the second 4s after the first. The first consumes the
    /// reconstructed base and credits proceeds exactly once; the ghost exit
    /// finds zero base and must credit NOTHING.
    #[tokio::test]
    async fn second_exit_sell_4s_after_first_credits_nothing() {
        let mut bal = HashMap::new();
        bal.insert("SKY".to_string(), 6.0);
        bal.insert("USDT".to_string(), 100_000.0);
        let c = PaperTradeConnector::new(bal);

        let exit = OrderRequest {
            symbol: "SKYUSDT".to_string(),
            side: OrderSide::Sell,
            order_type: OrderTypeReq::Limit,
            price: Some(21.0),
            quantity: 6.0,
            time_in_force: Some(TimeInForceReq::Gtc),
            client_order_id: Some("listener-exit".into()),
            reduce_only: true,
        };
        c.place_order(&exit).await.unwrap();
        let fills = c.try_fill_at_price("SKYUSDT", 21.0).await;
        assert_eq!(fills.len(), 1, "first exit fills");
        let after_first = c.get_balances().await.unwrap();
        assert!(after_first.get("SKY").copied().unwrap_or(0.0).abs() < 1e-9, "base consumed");
        // 100_000 + 6 × 21 − maker fee (6 × 21 × 10bps = 0.126)
        assert!(
            (after_first.get("USDT").copied().unwrap_or(0.0) - 100_125.874).abs() < 1e-6,
            "credited exactly once"
        );

        // The ghost exit, seconds later: identical sell, nothing left to sell.
        c.place_order(&exit).await.unwrap();
        let ghost_fills = c.try_fill_at_price("SKYUSDT", 21.0).await;
        assert!(ghost_fills.is_empty(), "ghost exit must be rejected, not filled");
        let after_second = c.get_balances().await.unwrap();
        assert!(
            (after_second.get("USDT").copied().unwrap_or(0.0)
                - after_first.get("USDT").copied().unwrap_or(0.0)).abs() < 1e-9,
            "USDT must not move twice for one position"
        );
    }

    #[test]
    fn sell_beyond_held_is_clamped_to_held() {
        let mut bal = HashMap::new();
        bal.insert("BTC".to_string(), 0.3); // holds less than the 0.5 sell
        bal.insert("USDT".to_string(), 10_000.0);
        let mut e = PaperTradeEngine::new(bal);
        e.place_order(&limit_sell(0.5, false)).unwrap(); // maker limit @ 51,000
        let fills = e.try_fill_at_price("BTCUSDT", 51_000.0);
        assert_eq!(fills.len(), 1);
        assert!(
            (fills[0].quantity - 0.3).abs() < 1e-9,
            "fill quantity must clamp to the 0.3 actually held, got {}",
            fills[0].quantity
        );
        let btc = e.balances().get("BTC").copied().unwrap_or(0.0);
        assert!(btc.abs() < 1e-9, "base must not go negative, got {}", btc);
        // Proceeds credited for the clamped 0.3 only: 10_000 + 51_000*0.3 - fee(51_000*0.3*10bps = 15.3)
        let usdt = e.balances().get("USDT").copied().unwrap_or(0.0);
        assert!((usdt - 25_284.7).abs() < 1e-6, "USDT credited for clamped qty only, got {}", usdt);
    }

    fn market_buy(qty: f64) -> OrderRequest {
        OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::Market,
            price: None,
            quantity: qty,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }
    }

    #[test]
    fn taker_buy_fills_above_mark_with_slippage() {
        let mut e = engine();
        e.set_realism(10.0, 5.0, 2.0); // 10 bps slippage
        e.place_order(&market_buy(0.1)).unwrap();
        let fills = e.try_fill_at_price("BTCUSDT", 50_000.0);
        assert_eq!(fills.len(), 1);
        // Buy slippage adverse (higher): 50000 * (1 + 10/10000) = 50050
        assert!((fills[0].price - 50_050.0).abs() < 1e-6, "got {}", fills[0].price);
    }

    #[test]
    fn taker_sell_fills_below_mark_with_slippage() {
        let mut e = engine();
        e.set_realism(10.0, 5.0, 2.0);
        e.place_order(&sell_stop(50_000.0, 0.5)).unwrap(); // StopMarket Sell
        let fills = e.try_fill_at_price("BTCUSDT", 49_900.0);
        assert_eq!(fills.len(), 1);
        // Sell slippage adverse (lower): 49900 * (1 - 10/10000) = 49850.1
        assert!((fills[0].price - 49_850.1).abs() < 1e-3, "got {}", fills[0].price);
    }

    #[test]
    fn maker_limit_unaffected_by_slippage() {
        let mut e = engine();
        e.set_realism(10.0, 5.0, 2.0);
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
        let fills = e.try_fill_at_price("BTCUSDT", 50_000.0);
        // Maker fills at its resting limit, no slippage.
        assert!((fills[0].price - 50_000.0).abs() < 1e-9);
    }

    // Task 6: the peak-equity invariant bound needs realized PnL + average
    // cost basis from the paper book. Buys set average cost (fees capitalize
    // into basis); sells realize (price − basis) × qty − fee.
    #[test]
    fn paper_stats_track_realized_pnl_and_cost_basis() {
        let mut bal = HashMap::new();
        bal.insert("USDT".to_string(), 10_000.0);
        let mut e = PaperTradeEngine::new(bal);
        e.set_realism(0.0, 0.0, 0.0); // zero fees → exact numbers

        e.place_order(&market_buy(1.0)).unwrap();
        e.try_fill_at_price("BTCUSDT", 100.0);
        assert!((e.realized_pnl() - 0.0).abs() < 1e-9, "a buy realizes nothing");
        assert_eq!(e.cost_basis().get("BTC").copied(), Some((1.0, 100.0)));

        e.place_order(&market_sell(0.5, false)).unwrap();
        e.try_fill_at_price("BTCUSDT", 120.0);
        assert!((e.realized_pnl() - 10.0).abs() < 1e-9, "(120 − 100) × 0.5 = 10");
        assert_eq!(
            e.cost_basis().get("BTC").copied(),
            Some((0.5, 100.0)),
            "basis quantity falls with the holding, price unchanged"
        );
    }

    #[test]
    fn buy_fees_capitalize_into_cost_basis() {
        let mut bal = HashMap::new();
        bal.insert("USDT".to_string(), 10_000.0);
        let mut e = PaperTradeEngine::new(bal);
        e.set_realism(0.0, 0.0, 10.0); // maker 10 bps, zero slippage

        e.place_order(&OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::LimitMaker,
            price: Some(100.0),
            quantity: 1.0,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }).unwrap();
        e.try_fill_at_price("BTCUSDT", 100.0);
        // fee = 100 × 1 × 10bps = 0.1 → average cost 100.1
        assert_eq!(e.cost_basis().get("BTC").copied(), Some((1.0, 100.1)));

        e.place_order(&OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Sell,
            order_type: OrderTypeReq::LimitMaker,
            price: Some(100.0),
            quantity: 1.0,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }).unwrap();
        e.try_fill_at_price("BTCUSDT", 100.0);
        // realized = (100 − 100.1) × 1 − 0.1 fee = −0.2
        assert!((e.realized_pnl() - (-0.2)).abs() < 1e-9, "got {}", e.realized_pnl());
    }

    #[test]
    fn tiered_fees_maker_vs_taker() {
        let mut e = engine();
        e.set_realism(0.0, 5.0, 2.0); // taker 5bps, maker 2bps
        e.place_order(&market_buy(1.0)).unwrap();
        let taker_fee = e.try_fill_at_price("BTCUSDT", 50_000.0)[0].fee;
        // 50000 * 1.0 * 5/10000 = 25.0
        assert!((taker_fee - 25.0).abs() < 1e-6, "taker fee {}", taker_fee);

        let mut e2 = engine();
        e2.set_realism(0.0, 5.0, 2.0);
        e2.place_order(&OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::Limit,
            price: Some(50_000.0),
            quantity: 1.0,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }).unwrap();
        let maker_fee = e2.try_fill_at_price("BTCUSDT", 50_000.0)[0].fee;
        // 50000 * 1.0 * 2/10000 = 10.0
        assert!((maker_fee - 10.0).abs() < 1e-6, "maker fee {}", maker_fee);
    }

    #[tokio::test]
    async fn connector_returns_resting_paper_open_orders_for_symbol() {
        let connector = PaperTradeConnector::new(HashMap::new());
        connector.place_order(&OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::Limit,
            price: Some(50_000.0),
            quantity: 0.25,
            time_in_force: Some(TimeInForceReq::Gtc),
            client_order_id: Some("paper-client-1".into()),
            reduce_only: false,
        }).await.unwrap();
        connector.place_order(&OrderRequest {
            symbol: "ETHUSDT".to_string(),
            side: OrderSide::Sell,
            order_type: OrderTypeReq::Limit,
            price: Some(3_000.0),
            quantity: 1.0,
            time_in_force: Some(TimeInForceReq::Gtc),
            client_order_id: None,
            reduce_only: false,
        }).await.unwrap();

        let orders = connector.get_open_orders("BTC-USDT").await.unwrap();

        assert_eq!(orders.len(), 1);
        assert_eq!(orders[0].order_id, "paper_1");
        assert_eq!(orders[0].symbol, "BTCUSDT");
        assert_eq!(orders[0].side, OrderSide::Buy);
        assert_eq!(orders[0].price, 50_000.0);
        assert_eq!(orders[0].quantity, 0.25);
        assert_eq!(orders[0].filled_quantity, 0.0);
        assert_eq!(orders[0].status, OrderStatus::New);
    }

    #[tokio::test]
    async fn connector_cancel_all_orders_removes_only_matching_symbol() {
        let connector = PaperTradeConnector::new(HashMap::new());
        connector.place_order(&OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::Limit,
            price: Some(50_000.0),
            quantity: 0.25,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }).await.unwrap();
        connector.place_order(&OrderRequest {
            symbol: "ETHUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::Limit,
            price: Some(3_000.0),
            quantity: 1.0,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }).await.unwrap();

        let cancelled = connector.cancel_all_orders("BTC-USDT").await.unwrap();

        assert_eq!(cancelled.len(), 1);
        assert_eq!(cancelled[0].order_id, "paper_1");
        assert_eq!(connector.get_open_orders("BTCUSDT").await.unwrap().len(), 0);
        assert_eq!(connector.get_open_orders("ETHUSDT").await.unwrap().len(), 1);
    }

    #[tokio::test]
    async fn connector_persists_open_orders_across_restart() {
        let path = std::env::temp_dir().join(format!(
            "paper_orders_{}.json",
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        {
            let connector = PaperTradeConnector::new(HashMap::new()).with_state_path(path.clone());
            connector.place_order(&OrderRequest {
                symbol: "BTCUSDT".to_string(),
                side: OrderSide::Buy,
                order_type: OrderTypeReq::Limit,
                price: Some(50_000.0),
                quantity: 0.25,
                time_in_force: None,
                client_order_id: Some("persist-me".into()),
                reduce_only: false,
            }).await.unwrap();
        }

        let restarted = PaperTradeConnector::new(HashMap::new()).with_state_path(path.clone());
        let orders = restarted.get_open_orders("BTCUSDT").await.unwrap();

        assert_eq!(orders.len(), 1);
        assert_eq!(orders[0].order_id, "paper_1");
        assert_eq!(orders[0].quantity, 0.25);

        let _ = std::fs::remove_file(path);
    }
}
