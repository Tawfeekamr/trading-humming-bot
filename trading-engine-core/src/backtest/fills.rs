//! Order → fill simulation. Market fills at decision-bar close ± slippage;
//! limit/maker/stop rest and fill on a later bar whose range crosses. No lookahead.
use crate::connector::types::{OrderRequest, OrderTypeReq, Fill};
use crate::models::bar::Bar;
use crate::models::order::OrderSide;

pub struct RestingOrder { pub req: OrderRequest, pub placed_ts: i64 }

pub struct FillSim {
    pub(crate) resting: Vec<RestingOrder>,
    taker_fee_bps: f64,
    maker_fee_bps: f64,
    slippage_bps: f64,
    seq: u64,
}

impl FillSim {
    pub fn new(taker_fee_bps: f64, maker_fee_bps: f64, slippage_bps: f64) -> Self {
        Self { resting: Vec::new(), taker_fee_bps, maker_fee_bps, slippage_bps, seq: 0 }
    }
    pub fn resting_is_empty(&self) -> bool { self.resting.is_empty() }

    fn slip(&self, side: OrderSide, price: f64) -> f64 {
        let s = price * (self.slippage_bps / 1e4);
        match side { OrderSide::Buy => price + s, OrderSide::Sell => price - s }
    }
    fn taker_fee(&self, qty: f64, price: f64) -> f64 { qty * price * (self.taker_fee_bps / 1e4) }
    fn maker_fee(&self, qty: f64, price: f64) -> f64 { qty * price * (self.maker_fee_bps / 1e4) }
    fn next_id(&mut self) -> String { self.seq += 1; format!("bfill-{}", self.seq) }

    pub fn submit(&mut self, orders: Vec<OrderRequest>, decision_bar: &Bar, out: &mut Vec<Fill>) {
        for req in orders {
            match req.order_type {
                OrderTypeReq::Market => {
                    let px = self.slip(req.side, decision_bar.close);
                    out.push(Fill {
                        fill_id: self.next_id(), order_id: req.client_order_id.clone().unwrap_or_default(),
                        client_order_id: req.client_order_id.clone(), symbol: req.symbol.clone(),
                        side: req.side, price: px, quantity: req.quantity,
                        fee: self.taker_fee(req.quantity, px), timestamp: decision_bar.timestamp,
                    });
                }
                OrderTypeReq::Limit | OrderTypeReq::LimitMaker | OrderTypeReq::StopMarket { .. } => {
                    self.resting.push(RestingOrder { req, placed_ts: decision_bar.timestamp });
                }
            }
        }
    }

    pub fn cancel(&mut self, cids: &[String]) {
        self.resting.retain(|r| !cids.iter().any(|c| r.req.client_order_id.as_deref() == Some(c.as_str())));
    }

    pub fn evaluate(&mut self, bar: &Bar, out: &mut Vec<Fill>) {
        let mut keep = Vec::with_capacity(self.resting.len());
        let mut pending = std::mem::take(&mut self.resting);
        for ro in pending.drain(..) {
            let req = ro.req; // take ownership so subsequent &mut self calls are legal
            let crossed = match (&req.order_type, req.side) {
                (OrderTypeReq::Limit, OrderSide::Buy) | (OrderTypeReq::LimitMaker, OrderSide::Buy) => {
                    req.price.is_some_and(|p| bar.low <= p)
                }
                (OrderTypeReq::Limit, OrderSide::Sell) | (OrderTypeReq::LimitMaker, OrderSide::Sell) => {
                    req.price.is_some_and(|p| bar.high >= p)
                }
                (OrderTypeReq::StopMarket { stop_price }, OrderSide::Sell) => {
                    bar.low <= *stop_price            // long-position stop
                }
                (OrderTypeReq::StopMarket { stop_price }, OrderSide::Buy) => {
                    bar.high >= *stop_price           // short-position stop
                }
                _ => false,
            };
            if crossed {
                let (px, is_taker) = match req.order_type {
                    OrderTypeReq::Limit | OrderTypeReq::LimitMaker => (req.price.unwrap(), false),
                    OrderTypeReq::StopMarket { stop_price } => {
                        // Gap handling: if the bar opens beyond the stop, fill at the open (worse).
                        //  Sell stop (long-position exit, stop below entry): fill at min(stop, open)
                        //  Buy  stop (short-position exit, stop above entry): fill at max(stop, open)
                        let p = match req.side {
                            OrderSide::Sell => stop_price.min(bar.open),
                            OrderSide::Buy  => stop_price.max(bar.open),
                        };
                        (p, true)
                    }
                    OrderTypeReq::Market => (bar.close, true), // defensive; Market never rests in practice
                };
                let fee = if is_taker { self.taker_fee(req.quantity, px) } else { self.maker_fee(req.quantity, px) };
                out.push(Fill {
                    fill_id: self.next_id(), order_id: req.client_order_id.clone().unwrap_or_default(),
                    client_order_id: req.client_order_id.clone(), symbol: req.symbol.clone(),
                    side: req.side, price: px, quantity: req.quantity, fee, timestamp: bar.timestamp,
                });
            } else {
                keep.push(RestingOrder { req, placed_ts: ro.placed_ts });
            }
        }
        self.resting = keep;
    }
}
