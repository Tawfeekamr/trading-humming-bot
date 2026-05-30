use serde::{Serialize, Deserialize};
use std::fmt;
use std::ops::{Add, Sub, Mul};

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
pub struct Currency {
    code: String,
}

impl Currency {
    pub fn new(code: &str) -> Self { Self { code: code.to_uppercase() } }
    pub fn usdt() -> Self { Self::new("USDT") }
    pub fn code(&self) -> &str { &self.code }
}

impl fmt::Display for Currency {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { write!(f, "{}", self.code) }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Price {
    value: f64,
    precision: u32,
}

impl Price {
    pub fn new(value: f64, precision: u32) -> Self { Self { value, precision } }
    pub fn value(&self) -> f64 { self.value }
    pub fn rounded(&self) -> f64 {
        let factor = 10f64.powi(self.precision as i32);
        (self.value * factor).round() / factor
    }
}

impl fmt::Display for Price {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result { write!(f, "{:.1$}", self.value, self.precision as usize) }
}

impl Add for Price {
    type Output = Price;
    fn add(self, rhs: Price) -> Self::Output { Price::new(self.value + rhs.value, self.precision.max(rhs.precision)) }
}

impl Sub for Price {
    type Output = Price;
    fn sub(self, rhs: Price) -> Self::Output { Price::new(self.value - rhs.value, self.precision.max(rhs.precision)) }
}

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
pub struct Quantity { value: f64 }

impl Quantity {
    pub fn new(value: f64) -> Self { Self { value } }
    pub fn value(&self) -> f64 { self.value }
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Money { pub amount: f64, pub currency: Currency }

impl Money {
    pub fn new(amount: f64, currency: Currency) -> Self { Self { amount, currency } }
    pub fn usdt(amount: f64) -> Self { Self { amount, currency: Currency::usdt() } }
    pub fn zero(currency: Currency) -> Self { Self { amount: 0.0, currency } }
}

impl Add for Money {
    type Output = Money;
    fn add(self, rhs: Money) -> Self::Output { Money { amount: self.amount + rhs.amount, currency: self.currency } }
}

impl Sub for Money {
    type Output = Money;
    fn sub(self, rhs: Money) -> Self::Output { Money { amount: self.amount - rhs.amount, currency: self.currency } }
}

impl Mul<f64> for Money {
    type Output = Money;
    fn mul(self, rhs: f64) -> Self::Output { Money { amount: self.amount * rhs, currency: self.currency } }
}
