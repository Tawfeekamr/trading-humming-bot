use std::fmt;

#[derive(Debug, Clone)]
#[cfg_attr(feature = "python", pyo3::pyclass)]
pub struct Atr {
    period: u32,
    value: f64,
    prev_close: Option<f64>,
    count: u32,
    initialized: bool,
    atr_history: Vec<f64>,
    atr_lookback: usize,
}

impl Atr {
    pub fn new(period: u32) -> Self {
        assert!(period > 0, "ATR period must be > 0");
        Self { period, value: 0.0, prev_close: None, count: 0, initialized: false, atr_history: Vec::with_capacity(64), atr_lookback: 20 }
    }
    pub fn update_bar(&mut self, _open: f64, high: f64, low: f64, close: f64) {
        self.count += 1;
        let tr = match self.prev_close {
            Some(prev) => { let hl = high - low; let hc = (high - prev).abs(); let lc = (low - prev).abs(); hl.max(hc).max(lc) }
            None => high - low,
        };
        if self.count <= self.period {
            self.value += tr;
            if self.count == self.period { self.value /= self.period as f64; self.initialized = true; self.atr_history.push(self.value); }
        } else {
            let p = self.period as f64;
            self.value = (self.value * (p - 1.0) + tr) / p;
            self.atr_history.push(self.value);
            if self.atr_history.len() > self.atr_lookback { self.atr_history.remove(0); }
        }
        self.prev_close = Some(close);
    }
    pub fn value(&self) -> f64 { self.value }
    pub fn is_initialized(&self) -> bool { self.initialized }
    pub fn count(&self) -> u32 { self.count }
    pub fn is_breakout(&self, _open: f64, high: f64, low: f64, _close: f64) -> bool {
        if self.atr_history.len() < 5 { return false; }
        let n = self.atr_history.len().min(10);
        let avg: f64 = self.atr_history.iter().rev().take(n).sum::<f64>() / n as f64;
        (high - low) > avg * 1.5
    }
    pub fn reset(&mut self) { self.value = 0.0; self.prev_close = None; self.count = 0; self.initialized = false; self.atr_history.clear(); }
}

#[cfg(feature = "python")]
#[pyo3::pymethods]
impl Atr {
    #[new]
    fn py_new(period: u32) -> Self { Self::new(period) }

    #[pyo3(name = "update_bar")]
    fn py_update_bar(&mut self, open: f64, high: f64, low: f64, close: f64) {
        self.update_bar(open, high, low, close);
    }

    #[getter]
    #[pyo3(name = "value")]
    fn py_value(&self) -> f64 { self.value() }

    #[getter]
    #[pyo3(name = "is_initialized")]
    fn py_is_initialized(&self) -> bool { self.is_initialized() }

    #[getter]
    #[pyo3(name = "count")]
    fn py_count(&self) -> u32 { self.count() }

    #[pyo3(name = "reset")]
    fn py_reset(&mut self) { self.reset(); }
}
