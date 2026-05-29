#[derive(Debug, Clone)]
#[cfg_attr(feature = "python", pyo3::pyclass)]
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

#[cfg(feature = "python")]
#[pyo3::pymethods]
impl Ema {
    #[new]
    fn py_new(period: u32) -> Self { Self::new(period) }

    #[pyo3(name = "update")]
    fn py_update(&mut self, price: f64) { self.update(price); }

    #[getter]
    #[pyo3(name = "value")]
    fn py_value(&self) -> f64 { self.value() }

    #[getter]
    #[pyo3(name = "is_initialized")]
    fn py_is_initialized(&self) -> bool { self.is_initialized() }

    #[getter]
    #[pyo3(name = "count")]
    fn py_count(&self) -> u32 { self.count() }

    #[getter]
    #[pyo3(name = "period")]
    fn py_period(&self) -> u32 { self.period() }

    #[pyo3(name = "reset")]
    fn py_reset(&mut self) { self.reset(); }
}
