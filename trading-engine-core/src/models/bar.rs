use serde::{Serialize, Deserialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Bar {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub timestamp: i64,
}

impl Bar {
    pub fn new(open: f64, high: f64, low: f64, close: f64, volume: f64, timestamp: i64) -> Self {
        Self { open, high, low, close, volume, timestamp }
    }
    pub fn typical_price(&self) -> f64 { (self.high + self.low + self.close) / 3.0 }
    pub fn range(&self) -> f64 { self.high - self.low }
    pub fn body_size(&self) -> f64 { (self.close - self.open).abs() }
    pub fn upper_wick(&self) -> f64 { self.high - self.open.max(self.close) }
    pub fn lower_wick(&self) -> f64 { self.open.min(self.close) - self.low }
    pub fn is_bullish(&self) -> bool { self.close > self.open }
    pub fn body_ratio(&self) -> f64 {
        let range = self.high - self.low;
        if range == 0.0 { return 0.0; }
        self.body_size() / range
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Timeframe {
    OneMinute, FiveMinutes, FifteenMinutes, OneHour, FourHours, OneDay,
}

impl Timeframe {
    pub fn as_seconds(&self) -> u64 {
        match self {
            Timeframe::OneMinute => 60,
            Timeframe::FiveMinutes => 300,
            Timeframe::FifteenMinutes => 900,
            Timeframe::OneHour => 3600,
            Timeframe::FourHours => 14400,
            Timeframe::OneDay => 86400,
        }
    }
}
