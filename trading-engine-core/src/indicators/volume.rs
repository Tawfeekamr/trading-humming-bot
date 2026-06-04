/// Volume Simple Moving Average indicator.
///
/// Tracks a rolling window of volume values and computes the average.
/// The key metric is `volume_ratio = current_volume / avg_volume`, used
/// to determine if trading activity is above or below normal levels.

#[derive(Debug, Clone)]
pub struct VolumeSma {
    period: usize,
    window: Vec<f64>,
    current_volume: f64,
    avg_volume: f64,
    initialized: bool,
}

impl VolumeSma {
    pub fn new(period: u32) -> Self {
        Self {
            period: period as usize,
            window: Vec::with_capacity(period as usize),
            current_volume: 0.0,
            avg_volume: 0.0,
            initialized: false,
        }
    }

    /// Update with the latest bar's volume.
    pub fn update(&mut self, volume: f64) {
        self.current_volume = volume;
        self.window.push(volume);
        if self.window.len() > self.period {
            self.window.remove(0);
        }
        if self.window.len() >= self.period {
            self.avg_volume = self.window.iter().sum::<f64>() / self.period as f64;
            self.initialized = true;
        }
    }

    /// The SMA of volume over the configured period.
    pub fn value(&self) -> f64 {
        self.avg_volume
    }

    /// The most recently pushed volume value.
    pub fn current_volume(&self) -> f64 {
        self.current_volume
    }

    /// Ratio of current volume to average volume.
    /// Returns 1.0 if average is zero (avoid division by zero).
    pub fn volume_ratio(&self) -> f64 {
        if self.avg_volume > 0.0 {
            self.current_volume / self.avg_volume
        } else {
            1.0
        }
    }

    pub fn is_initialized(&self) -> bool {
        self.initialized
    }

    pub fn reset(&mut self) {
        self.window.clear();
        self.current_volume = 0.0;
        self.avg_volume = 0.0;
        self.initialized = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_volume_sma_simple() {
        let mut vsma = VolumeSma::new(3);
        vsma.update(100.0);
        assert!(!vsma.is_initialized());
        vsma.update(200.0);
        assert!(!vsma.is_initialized());
        vsma.update(300.0);
        assert!(vsma.is_initialized());
        // avg = (100 + 200 + 300) / 3 = 200
        assert!((vsma.value() - 200.0).abs() < 1e-10);
    }

    #[test]
    fn test_volume_ratio() {
        let mut vsma = VolumeSma::new(3);
        vsma.update(100.0);
        vsma.update(100.0);
        vsma.update(100.0);
        // avg = 100, current = 100, ratio = 1.0
        assert!((vsma.volume_ratio() - 1.0).abs() < 1e-10);

        vsma.update(200.0);
        // window = [100, 100, 200], avg = 400/3 ≈ 133.33
        // ratio = 200 / 133.33 = 1.5
        let expected_ratio = 200.0 / (400.0 / 3.0);
        assert!((vsma.volume_ratio() - expected_ratio).abs() < 1e-8);
    }

    #[test]
    fn test_volume_not_initialized_before_period() {
        let mut vsma = VolumeSma::new(5);
        for _ in 0..4 {
            vsma.update(100.0);
            assert!(!vsma.is_initialized());
        }
        vsma.update(100.0);
        assert!(vsma.is_initialized());
    }

    #[test]
    fn test_volume_window_eviction() {
        let mut vsma = VolumeSma::new(3);
        vsma.update(10.0);
        vsma.update(20.0);
        vsma.update(30.0);
        assert!((vsma.value() - 20.0).abs() < 1e-10);

        vsma.update(40.0);
        // window = [20, 30, 40], avg = 30
        assert!((vsma.value() - 30.0).abs() < 1e-10);
    }

    #[test]
    fn test_volume_reset() {
        let mut vsma = VolumeSma::new(3);
        vsma.update(100.0);
        vsma.update(200.0);
        vsma.update(300.0);
        assert!(vsma.is_initialized());
        vsma.reset();
        assert!(!vsma.is_initialized());
        assert_eq!(vsma.value(), 0.0);
        assert_eq!(vsma.current_volume(), 0.0);
    }

    #[test]
    fn test_volume_ratio_zero_avg() {
        let vsma = VolumeSma::new(3);
        // avg is 0, ratio should return 1.0 to avoid division by zero
        assert!((vsma.volume_ratio() - 1.0).abs() < 1e-10);
    }
}
