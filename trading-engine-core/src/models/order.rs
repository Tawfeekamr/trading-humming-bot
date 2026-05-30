use serde::{Serialize, Deserialize};
use std::fmt;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderSide {
    #[serde(rename = "BUY")]
    Buy,
    #[serde(rename = "SELL")]
    Sell,
}

impl fmt::Display for OrderSide {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            OrderSide::Buy => write!(f, "BUY"),
            OrderSide::Sell => write!(f, "SELL"),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderType { Market, Limit, StopMarket, StopLimit, TrailingStopMarket }

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum TimeInForce { Gtc, Ioc, Fok }

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct ClientOrderId { value: String }

impl ClientOrderId {
    pub fn new(value: &str) -> Self { Self { value: value.to_string() } }
    pub fn value(&self) -> &str { &self.value }
}
