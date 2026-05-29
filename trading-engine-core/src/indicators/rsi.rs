use std::fmt;

#[derive(Debug, Clone)]
pub struct Rsi {
    period: u32,
    avg_gain: f64,
    avg_loss: f64,
    prev_close: Option<f64>,
    value: f64,
    count: u32,
    initialized: bool,
}

impl Rsi {
    pub fn new(period: u32) -> Self {
        assert!(period > 0, "RSI period must be > 0");
        Self { period, avg_gain: 0.0, avg_loss: 0.0, prev_close: None, value: 50.0, count: 0, initialized: false }
    }
    pub fn update(&mut self, close: f64) {
        self.count += 1;
        if let Some(prev) = self.prev_close {
            let gain = (close - prev).max(0.0);
            let loss = (prev - close).max(0.0);
            if self.count <= self.period {
                self.avg_gain += gain;
                self.avg_loss += loss;
                if self.count == self.period {
                    self.avg_gain /= self.period as f64;
                    self.avg_loss /= self.period as f64;
                    self.compute_rsi();
                    self.initialized = true;
                }
            } else {
                let p = self.period as f64;
                self.avg_gain = (self.avg_gain * (p - 1.0) + gain) / p;
                self.avg_loss = (self.avg_loss * (p - 1.0) + loss) / p;
                self.compute_rsi();
            }
        }
        self.prev_close = Some(close);
    }
    fn compute_rsi(&mut self) {
        if self.avg_loss == 0.0 && self.avg_gain == 0.0 { self.value = 50.0; }
        else if self.avg_loss == 0.0 { self.value = 100.0; }
        else { let rs = self.avg_gain / self.avg_loss; self.value = 100.0 - (100.0 / (1.0 + rs)); }
    }
    pub fn value(&self) -> f64 { self.value }
    pub fn is_initialized(&self) -> bool { self.initialized }
    pub fn count(&self) -> u32 { self.count }
    pub fn reset(&mut self) { self.avg_gain = 0.0; self.avg_loss = 0.0; self.prev_close = None; self.value = 50.0; self.count = 0; self.initialized = false; }
}
