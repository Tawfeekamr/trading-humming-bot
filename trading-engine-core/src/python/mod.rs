#[cfg(feature = "python")]
use pyo3::prelude::*;

#[cfg(feature = "python")]
use crate::indicators::{Ema, Rsi, Atr, BollingerBands};

#[cfg(feature = "python")]
#[pyfunction]
fn version() -> String { env!("CARGO_PKG_VERSION").to_string() }

#[cfg(feature = "python")]
pub fn register_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    m.add_class::<Ema>()?;
    m.add_class::<Rsi>()?;
    m.add_class::<Atr>()?;
    m.add_class::<BollingerBands>()?;
    Ok(())
}
