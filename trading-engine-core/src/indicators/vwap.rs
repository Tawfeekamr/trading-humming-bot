/// Volume-Weighted Average Price (VWAP) indicator.
///
/// Rolling VWAP: `cumulative(typical_price * volume) / cumulative(volume)`
/// where `typical_price = (high + low + close) / 3`.
///
/// For crypto (24/7 markets), uses a configurable rolling window rather than
/// session-based reset. A period of ~390 bars approximates one trading day
/// at 1-minute bars.

#[derive(Debug, Clone)]
pub struct Vwap {
    cumulative_tp_volume: f64,
    cumulative_volume: f64,
    value: f64,
    initialized: bool,
    period: usize, // 0 = cumulative since creation, N = rolling over N bars
    window: Vec<(f64, f64)>, // (typical_price * volume, volume)
}

impl Vwap {
    pub fn new(period: u32) -> Self {
        Self {
            cumulative_tp_volume: 0.0,
            cumulative_volume: 0.0,
            value: 0.0,
            initialized: false,
            period: period as usize,
            window: Vec::new(),
        }
    }

    /// Update with a new bar's OHLCV data.
    pub fn update_bar(&mut self, high: f64, low: f64, close: f64, volume: f64) {
        let typical_price = (high + low + close) / 3.0;
        let tp_vol = typical_price * volume;

        self.cumulative_tp_volume += tp_vol;
        self.cumulative_volume += volume;
        self.window.push((tp_vol, volume));

        // Rolling window eviction
        if self.period > 0 && self.window.len() > self.period {
            let (old_tp_vol, old_vol) = self.window.remove(0);
            self.cumulative_tp_volume -= old_tp_vol;
            self.cumulative_volume -= old_vol;
        }

        if self.cumulative_volume > 0.0 {
            self.value = self.cumulative_tp_volume / self.cumulative_volume;
        }
        self.initialized = true;
    }

    pub fn value(&self) -> f64 {
        self.value
    }

    pub fn is_initialized(&self) -> bool {
        self.initialized
    }

    pub fn is_above(&self, price: f64) -> bool {
        price > self.value
    }

    pub fn reset(&mut self) {
        self.cumulative_tp_volume = 0.0;
        self.cumulative_volume = 0.0;
        self.value = 0.0;
        self.initialized = false;
        self.window.clear();
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_vwap_single_bar() {
        let mut vwap = Vwap::new(0);
        // high=102, low=98, close=100, volume=1000
        // typical_price = (102 + 98 + 100) / 3 = 100
        // VWAP = (100 * 1000) / 1000 = 100
        vwap.update_bar(102.0, 98.0, 100.0, 1000.0);
        assert!((vwap.value() - 100.0).abs() < 1e-10);
    }

    #[test]
    fn test_vwap_uniform_volume() {
        let mut vwap = Vwap::new(0);
        // Equal volume means VWAP = mean of typical prices
        vwap.update_bar(110.0, 100.0, 105.0, 100.0); // tp = 105
        vwap.update_bar(120.0, 110.0, 115.0, 100.0); // tp = 115
        // VWAP = (105*100 + 115*100) / (100 + 100) = 22000/200 = 110
        assert!((vwap.value() - 110.0).abs() < 1e-10);
    }

    #[test]
    fn test_vwap_weighted_volume() {
        let mut vwap = Vwap::new(0);
        vwap.update_bar(110.0, 100.0, 105.0, 1000.0); // tp = 105, weight = 1000
        vwap.update_bar(120.0, 110.0, 115.0, 100.0);  // tp = 115, weight = 100
        // VWAP = (105*1000 + 115*100) / (1000 + 100) = 116500/1100 ≈ 105.909
        let expected = (105.0 * 1000.0 + 115.0 * 100.0) / 1100.0;
        assert!((vwap.value() - expected).abs() < 1e-10);
    }

    #[test]
    fn test_vwap_rolling_window() {
        let mut vwap = Vwap::new(2);
        vwap.update_bar(110.0, 100.0, 105.0, 100.0); // tp = 105
        vwap.update_bar(120.0, 110.0, 115.0, 100.0); // tp = 115
        // At this point, window has 2 bars
        let v1 = vwap.value();
        let expected1 = (105.0 * 100.0 + 115.0 * 100.0) / 200.0;
        assert!((v1 - expected1).abs() < 1e-10);

        // Add third bar — should evict first
        vwap.update_bar(130.0, 120.0, 125.0, 100.0); // tp = 125
        // Now only bars 2 and 3: VWAP = (115*100 + 125*100) / 200 = 120
        let expected2 = (115.0 * 100.0 + 125.0 * 100.0) / 200.0;
        assert!((vwap.value() - expected2).abs() < 1e-10);
    }

    #[test]
    fn test_vwap_is_above() {
        let mut vwap = Vwap::new(0);
        vwap.update_bar(102.0, 98.0, 100.0, 1000.0); // VWAP = 100
        assert!(vwap.is_above(101.0));
        assert!(!vwap.is_above(99.0));
        assert!(!vwap.is_above(100.0)); // not strictly above
    }

    #[test]
    fn test_vwap_reset() {
        let mut vwap = Vwap::new(0);
        vwap.update_bar(102.0, 98.0, 100.0, 1000.0);
        assert!(vwap.is_initialized());
        vwap.reset();
        assert!(!vwap.is_initialized());
        assert_eq!(vwap.value(), 0.0);
    }
}
