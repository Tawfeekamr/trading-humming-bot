#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderSide { Buy, Sell }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum OrderType { Market, Limit, StopMarket, StopLimit, TrailingStopMarket }

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TimeInForce { Gtc, Ioc, Fok }

#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct ClientOrderId { value: String }

impl ClientOrderId {
    pub fn new(value: &str) -> Self { Self { value: value.to_string() } }
    pub fn value(&self) -> &str { &self.value }
}
