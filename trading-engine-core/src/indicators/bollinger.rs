use std::fmt;

#[derive(Debug, Clone)]
#[cfg_attr(feature = "python", pyo3::pyclass)]
pub struct BollingerBands {
    period: u32,
    std_dev_multiplier: f64,
    window: Vec<f64>,
    upper: f64,
    middle: f64,
    lower: f64,
    bandwidth: f64,
    percent_b: f64,
    initialized: bool,
}

impl BollingerBands {
    pub fn new(period: u32, std_dev: f64) -> Self {
        assert!(period > 0, "BB period must be > 0");
        Self { period, std_dev_multiplier: std_dev, window: Vec::with_capacity(period as usize + 1), upper: 0.0, middle: 0.0, lower: 0.0, bandwidth: 0.0, percent_b: 0.5, initialized: false }
    }
    pub fn update(&mut self, close: f64) {
        self.window.push(close);
        if self.window.len() > self.period as usize { self.window.remove(0); }
        if self.window.len() < self.period as usize { return; }
        self.initialized = true;
        let sum: f64 = self.window.iter().sum();
        self.middle = sum / self.period as f64;
        let variance: f64 = self.window.iter().map(|v| (v - self.middle).powi(2)).sum::<f64>() / self.period as f64;
        let sigma = variance.sqrt();
        self.upper = self.middle + self.std_dev_multiplier * sigma;
        self.lower = self.middle - self.std_dev_multiplier * sigma;
        self.bandwidth = if self.middle != 0.0 { (self.upper - self.lower) / self.middle } else { 0.0 };
        self.percent_b = if (self.upper - self.lower).abs() > 1e-10 { (close - self.lower) / (self.upper - self.lower) } else { 0.5 };
    }
    pub fn upper(&self) -> f64 { self.upper }
    pub fn middle(&self) -> f64 { self.middle }
    pub fn lower(&self) -> f64 { self.lower }
    pub fn bandwidth(&self) -> f64 { self.bandwidth }
    pub fn percent_b(&self) -> f64 { self.percent_b }
    pub fn is_initialized(&self) -> bool { self.initialized }
    pub fn reset(&mut self) { self.window.clear(); self.upper = 0.0; self.middle = 0.0; self.lower = 0.0; self.bandwidth = 0.0; self.percent_b = 0.5; self.initialized = false; }
}

#[cfg(feature = "python")]
#[pyo3::pymethods]
impl BollingerBands {
    #[new]
    fn py_new(period: u32, std_dev: f64) -> Self { Self::new(period, std_dev) }

    #[pyo3(name = "update")]
    fn py_update(&mut self, close: f64) { self.update(close); }

    #[getter]
    #[pyo3(name = "upper")]
    fn py_upper(&self) -> f64 { self.upper() }

    #[getter]
    #[pyo3(name = "middle")]
    fn py_middle(&self) -> f64 { self.middle() }

    #[getter]
    #[pyo3(name = "lower")]
    fn py_lower(&self) -> f64 { self.lower() }

    #[getter]
    #[pyo3(name = "bandwidth")]
    fn py_bandwidth(&self) -> f64 { self.bandwidth() }

    #[getter]
    #[pyo3(name = "percent_b")]
    fn py_percent_b(&self) -> f64 { self.percent_b() }

    #[getter]
    #[pyo3(name = "is_initialized")]
    fn py_is_initialized(&self) -> bool { self.is_initialized() }

    #[pyo3(name = "reset")]
    fn py_reset(&mut self) { self.reset(); }
}
