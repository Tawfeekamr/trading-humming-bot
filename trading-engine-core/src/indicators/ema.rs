#[derive(Debug, Clone)]
pub struct Ema {
    period: u32,
    alpha: f64,
    value: f64,
    count: u32,
    initialized: bool,
}

impl Ema {
    pub fn new(period: u32) -> Self {
        assert!(period > 0, "EMA period must be > 0");
        Self { period, alpha: 2.0 / (period as f64 + 1.0), value: 0.0, count: 0, initialized: false }
    }
    pub fn update(&mut self, price: f64) {
        self.count += 1;
        if self.count == 1 { self.value = price; }
        else { self.value = self.alpha * price + (1.0 - self.alpha) * self.value; }
        if self.count >= self.period { self.initialized = true; }
    }
    pub fn value(&self) -> f64 { self.value }
    pub fn is_initialized(&self) -> bool { self.initialized }
    pub fn reset(&mut self) { self.value = 0.0; self.count = 0; self.initialized = false; }
    pub fn count(&self) -> u32 { self.count }
    pub fn period(&self) -> u32 { self.period }
}
