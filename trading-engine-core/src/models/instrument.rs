use serde::{Serialize, Deserialize};

fn decimal_places(n: f64) -> u32 {
    if n == 0.0 { return 0; }
    let mut count = 0u32;
    let mut value = n.abs();
    while value < 1.0 && count < 20 {
        value *= 10.0;
        count += 1;
    }
    // Handle values >= 1 by checking if they're whole numbers
    if n.abs() >= 1.0 {
        let rounded = n.round();
        if (n - rounded).abs() < f64::EPSILON {
            return 0;
        }
    }
    count
}

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Instrument {
    pub symbol: String,
    pub tick_size: f64,
    pub step_size: f64,
    pub pip_size: f64,
    pub price_precision: u32,
    pub quantity_precision: u32,
}

impl Instrument {
    pub fn new(symbol: &str, tick_size: f64, step_size: f64, price_precision: u32, quantity_precision: u32) -> Self {
        let pip_size = if symbol.contains("JPY") || symbol.contains("jpy") { 0.01 }
            else if tick_size >= 0.01 { 0.01 } else { 0.0001 };
        Self { symbol: symbol.to_string(), tick_size, step_size, pip_size, price_precision, quantity_precision }
    }
    pub fn round_price(&self, price: f64) -> f64 {
        let factor = 10f64.powi(self.price_precision as i32);
        (price * factor).round() / factor
    }
    pub fn round_quantity(&self, quantity: f64) -> f64 {
        let factor = 10f64.powi(self.quantity_precision as i32);
        (quantity * factor).floor() / factor
    }
    pub fn crypto(symbol: &str, tick_size: f64, step_size: f64) -> Self {
        let price_precision = decimal_places(tick_size);
        let quantity_precision = decimal_places(step_size);
        Self::new(symbol, tick_size, step_size, price_precision, quantity_precision)
    }
    pub fn forex(symbol: &str, pip_size: f64) -> Self {
        let price_precision = decimal_places(pip_size);
        Self { symbol: symbol.to_string(), tick_size: pip_size, step_size: 1.0, pip_size, price_precision, quantity_precision: 0 }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn test_decimal_places() { assert_eq!(decimal_places(0.0001), 4); assert_eq!(decimal_places(0.01), 2); assert_eq!(decimal_places(1.0), 0); }
    #[test]
    fn test_round_price() { let i = Instrument::crypto("BTC-USDT", 0.01, 0.00001); assert_eq!(i.round_price(50000.126), 50000.13); }
    #[test]
    fn test_round_quantity() { let i = Instrument::crypto("BTC-USDT", 0.01, 0.00001); assert_eq!(i.round_quantity(0.123456789), 0.12345); }
    #[test]
    fn test_forex_pip() { let e = Instrument::forex("EUR/USD", 0.0001); assert_eq!(e.pip_size, 0.0001); let j = Instrument::forex("USD/JPY", 0.01); assert_eq!(j.pip_size, 0.01); }
}
