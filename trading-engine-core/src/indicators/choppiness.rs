/// Choppiness Index (CHOP) — windowed formula, no recursive smoothing.
///
/// Formula: `100 * log10(sum(TR, period) / (HH - LL)) / log10(period)`
///
/// Interpretation:
/// - > 61.8: strongly ranging / choppy (safe for grid)
/// - 38.2–61.8: neutral
/// - < 38.2: strongly trending (dangerous for grid)
///
/// This is a **rolling window** indicator — the value is recomputed from
/// the last `period` bars each time. It does NOT use Wilder/exponential
/// smoothing (unlike ADX/ATR). Making it stateful would be incorrect.

#[derive(Debug, Clone)]
pub struct Choppiness {
    period: usize,
    tr_window: Vec<f64>,
    high_window: Vec<f64>,
    low_window: Vec<f64>,
    value: f64,
    initialized: bool,
}

impl Choppiness {
    pub fn new(period: u32) -> Self {
        let p = period as usize;
        Self {
            period: p,
            tr_window: Vec::with_capacity(p + 1),
            high_window: Vec::with_capacity(p + 1),
            low_window: Vec::with_capacity(p + 1),
            value: 0.0,
            initialized: false,
        }
    }

    pub fn update_bar(&mut self, _open: f64, high: f64, low: f64, _close: f64, prev_close: Option<f64>) {
        // True Range
        let tr = match prev_close {
            Some(pc) => (high - low).max((high - pc).abs()).max((low - pc).abs()),
            None => high - low,
        };

        self.tr_window.push(tr);
        self.high_window.push(high);
        self.low_window.push(low);

        // Trim to period
        if self.tr_window.len() > self.period {
            self.tr_window.remove(0);
            self.high_window.remove(0);
            self.low_window.remove(0);
        }

        if self.tr_window.len() >= self.period {
            let atr_sum: f64 = self.tr_window.iter().sum();
            let hh = self.high_window.iter().cloned().fold(f64::NEG_INFINITY, f64::max);
            let ll = self.low_window.iter().cloned().fold(f64::INFINITY, f64::min);
            let range = hh - ll;

            if range > 0.0 {
                self.value = 100.0 * (atr_sum / range).log10() / (self.period as f64).log10();
            }
            self.initialized = true;
        }
    }

    pub fn value(&self) -> f64 { self.value }
    pub fn is_initialized(&self) -> bool { self.initialized }

    pub fn reset(&mut self) {
        self.tr_window.clear();
        self.high_window.clear();
        self.low_window.clear();
        self.value = 0.0;
        self.initialized = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_chop_ranging_market_high_value() {
        let mut chop = Choppiness::new(14);
        let mut prev_close = None;
        // Oscillating market — price bounces between 99 and 101
        for i in 0..20 {
            let base = 100.0 + ((i as f64 * 7.0).sin() * 1.0);
            let high = base + 0.5;
            let low = base - 0.5;
            let close = base;
            chop.update_bar(base, high, low, close, prev_close);
            prev_close = Some(close);
        }
        assert!(chop.is_initialized());
        // Ranging market should have high choppiness (> 50)
        assert!(chop.value() > 50.0,
            "Choppiness should be > 50 for ranging market, got {}", chop.value());
    }

    #[test]
    fn test_chop_trending_market_low_value() {
        let mut chop = Choppiness::new(14);
        let mut prev_close = None;
        // Strong uptrend — price moves consistently higher
        for i in 0..20 {
            let base = 100.0 + i as f64 * 2.0;
            let high = base + 1.0;
            let low = base - 0.5;
            let close = base + 1.5;
            chop.update_bar(base, high, low, close, prev_close);
            prev_close = Some(close);
        }
        assert!(chop.is_initialized());
        // Trending market should have low choppiness (< 50)
        assert!(chop.value() < 60.0,
            "Choppiness should be low for trending market, got {}", chop.value());
    }

    #[test]
    fn test_chop_not_initialized_before_period() {
        let mut chop = Choppiness::new(14);
        let mut prev_close = None;
        for _i in 0..13 {
            chop.update_bar(100.0, 101.0, 99.0, 100.0, prev_close);
            prev_close = Some(100.0);
            assert!(!chop.is_initialized());
        }
        chop.update_bar(100.0, 101.0, 99.0, 100.0, prev_close);
        assert!(chop.is_initialized());
    }

    #[test]
    fn test_chop_reset() {
        let mut chop = Choppiness::new(14);
        let mut prev_close = None;
        for _i in 0..15 {
            chop.update_bar(100.0, 101.0, 99.0, 100.0, prev_close);
            prev_close = Some(100.0);
        }
        assert!(chop.is_initialized());
        chop.reset();
        assert!(!chop.is_initialized());
    }
}
