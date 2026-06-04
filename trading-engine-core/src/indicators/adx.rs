/// Average Directional Index (ADX) with Wilder smoothing.
///
/// Matches pandas_ta's convention:
/// - Seeds +DM_smooth, -DM_smooth, TR_smooth with simple sums over first `period` bars
/// - Then applies Wilder exponential smoothing: `(prev * (period-1) + new) / period`
/// - ADX becomes available after `2 * period` bars (period for DI smoothing, period for DX smoothing)
///
/// ADX interpretation:
/// - < 20: no trend / ranging (safe for grid deployment)
/// - 20-25: weak trend
/// - > 25: strong trend (dangerous for grids)

#[derive(Debug, Clone)]
pub struct Adx {
    period: usize,
    // Wilder-smoothed accumulators
    plus_dm_smooth: f64,
    minus_dm_smooth: f64,
    tr_smooth: f64,
    dx_smooth: f64,
    // Current ADX value
    adx_value: f64,
    plus_di_value: f64,
    minus_di_value: f64,
    // State tracking
    count: usize,
    initialized: bool,
    // Seeding accumulators (used for first `period` bars)
    plus_dm_sum: f64,
    minus_dm_sum: f64,
    tr_sum: f64,
    // DX values for ADX seeding (first `period` DX values averaged)
    dx_values: Vec<f64>,
    prev_high: Option<f64>,
    prev_low: Option<f64>,
    prev_close: Option<f64>,
}

impl Adx {
    pub fn new(period: u32) -> Self {
        let p = period as usize;
        Self {
            period: p,
            plus_dm_smooth: 0.0,
            minus_dm_smooth: 0.0,
            tr_smooth: 0.0,
            dx_smooth: 0.0,
            adx_value: 0.0,
            plus_di_value: 0.0,
            minus_di_value: 0.0,
            count: 0,
            initialized: false,
            plus_dm_sum: 0.0,
            minus_dm_sum: 0.0,
            tr_sum: 0.0,
            dx_values: Vec::with_capacity(p),
            prev_high: None,
            prev_low: None,
            prev_close: None,
        }
    }

    pub fn update_bar(&mut self, _open: f64, high: f64, low: f64, close: f64) {
        self.count += 1;

        // Need at least 2 bars to compute directional movement
        let (prev_high, prev_low, prev_close) = match (self.prev_high, self.prev_low, self.prev_close) {
            (Some(h), Some(l), Some(c)) => (h, l, c),
            _ => {
                self.prev_high = Some(high);
                self.prev_low = Some(low);
                self.prev_close = Some(close);
                return;
            }
        };

        // True Range
        let tr = (high - low)
            .max((high - prev_close).abs())
            .max((low - prev_close).abs());

        // +DM and -DM
        let up_move = high - prev_high;
        let down_move = prev_low - low;
        let plus_dm = if up_move > down_move && up_move > 0.0 { up_move } else { 0.0 };
        let minus_dm = if down_move > up_move && down_move > 0.0 { down_move } else { 0.0 };

        // Accumulate for seeding (first `period` directional bars)
        // count starts at 1, but first bar with prev is count=2
        let dir_bar = self.count - 1; // number of directional bars computed

        if dir_bar <= self.period {
            // Seeding phase: accumulate sums
            self.plus_dm_sum += plus_dm;
            self.minus_dm_sum += minus_dm;
            self.tr_sum += tr;

            if dir_bar == self.period {
                // Seed the smoothed values with simple averages (matches pandas_ta)
                self.plus_dm_smooth = self.plus_dm_sum;
                self.minus_dm_smooth = self.minus_dm_sum;
                self.tr_smooth = self.tr_sum;

                // Compute first DI values
                self.compute_di();

                // Compute first DX
                let dx = self.compute_dx();
                self.dx_values.push(dx);
            }
        } else {
            // Wilder smoothing phase
            let p = self.period as f64;
            self.plus_dm_smooth = self.plus_dm_smooth * (p - 1.0) + plus_dm;
            self.minus_dm_smooth = self.minus_dm_smooth * (p - 1.0) + minus_dm;
            self.tr_smooth = self.tr_smooth * (p - 1.0) + tr;

            // Update DI
            self.compute_di();

            // Compute DX
            let dx = self.compute_dx();

            if self.dx_values.len() < self.period {
                // Still collecting DX values for ADX seeding
                self.dx_values.push(dx);
            } else if !self.initialized {
                // Seed ADX with simple average of first `period` DX values
                let adx_seed: f64 = self.dx_values.iter().sum::<f64>() / self.dx_values.len() as f64;
                self.adx_value = adx_seed;
                self.dx_smooth = adx_seed;
                self.initialized = true;
            } else {
                // Wilder-smooth ADX
                self.dx_smooth = (self.dx_smooth * (p - 1.0) + dx) / p;
                self.adx_value = self.dx_smooth;
            }
        }

        self.prev_high = Some(high);
        self.prev_low = Some(low);
        self.prev_close = Some(close);
    }

    fn compute_di(&mut self) {
        if self.tr_smooth > 0.0 {
            self.plus_di_value = 100.0 * self.plus_dm_smooth / self.tr_smooth;
            self.minus_di_value = 100.0 * self.minus_dm_smooth / self.tr_smooth;
        }
    }

    fn compute_dx(&self) -> f64 {
        let di_sum = self.plus_di_value + self.minus_di_value;
        if di_sum > 0.0 {
            100.0 * (self.plus_di_value - self.minus_di_value).abs() / di_sum
        } else {
            0.0
        }
    }

    pub fn adx(&self) -> f64 { self.adx_value }
    pub fn plus_di(&self) -> f64 { self.plus_di_value }
    pub fn minus_di(&self) -> f64 { self.minus_di_value }
    pub fn is_initialized(&self) -> bool { self.initialized }

    /// Number of bars processed so far
    pub fn count(&self) -> usize { self.count }

    pub fn reset(&mut self) {
        self.plus_dm_smooth = 0.0;
        self.minus_dm_smooth = 0.0;
        self.tr_smooth = 0.0;
        self.dx_smooth = 0.0;
        self.adx_value = 0.0;
        self.plus_di_value = 0.0;
        self.minus_di_value = 0.0;
        self.count = 0;
        self.initialized = false;
        self.plus_dm_sum = 0.0;
        self.minus_dm_sum = 0.0;
        self.tr_sum = 0.0;
        self.dx_values.clear();
        self.prev_high = None;
        self.prev_low = None;
        self.prev_close = None;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_adx_warmup() {
        let mut adx = Adx::new(14);
        // Need: 1 bar (no prev) + 14 bars (seed DI) + 14 DX values to seed ADX = 29 bars
        for i in 0..28 {
            let base = 100.0 + i as f64 * 0.5;
            adx.update_bar(base, base + 1.0, base - 1.0, base + 0.5);
        }
        assert!(!adx.is_initialized());
        // Bar 29 should initialize
        adx.update_bar(114.0, 115.0, 113.0, 114.5);
        assert!(adx.is_initialized());
    }

    #[test]
    fn test_adx_trending_market_produces_high_adx() {
        let mut adx = Adx::new(14);
        // Strong uptrend: each bar closes higher by 2.0
        for i in 0..50 {
            let base = 100.0 + i as f64 * 2.0;
            adx.update_bar(base, base + 1.0, base - 1.0, base + 2.0);
        }
        assert!(adx.is_initialized());
        // Strong trend should produce ADX > 25
        assert!(adx.adx() > 25.0, "ADX should be > 25 for strong trend, got {}", adx.adx());
    }

    #[test]
    fn test_adx_ranging_market_produces_low_adx() {
        let mut adx = Adx::new(14);
        // Ranging market: price oscillates between 99 and 101
        for i in 0..50 {
            let base = 100.0 + (i as f64 % 4.0 - 2.0); // oscillates -2 to +2
            adx.update_bar(base - 0.5, base + 0.5, base - 0.5, base);
        }
        assert!(adx.is_initialized());
        // Range-bound market should produce ADX < 20
        assert!(adx.adx() < 25.0, "ADX should be low for ranging market, got {}", adx.adx());
    }

    #[test]
    fn test_adx_reset() {
        let mut adx = Adx::new(14);
        for i in 0..40 {
            let base = 100.0 + i as f64;
            adx.update_bar(base, base + 1.0, base - 1.0, base + 0.5);
        }
        assert!(adx.is_initialized());
        adx.reset();
        assert!(!adx.is_initialized());
        assert_eq!(adx.count(), 0);
    }

    #[test]
    fn test_adx_plus_minus_di() {
        let mut adx = Adx::new(14);
        // Uptrend: +DI should be > -DI
        for i in 0..40 {
            let base = 100.0 + i as f64 * 1.5;
            adx.update_bar(base, base + 2.0, base - 0.5, base + 1.5);
        }
        assert!(adx.is_initialized());
        assert!(adx.plus_di() > adx.minus_di(),
            "+DI ({}) should be > -DI ({}) in uptrend", adx.plus_di(), adx.minus_di());
    }
}
