/// PyO3 bindings for trading-engine-core.
/// Only compiled when the `python` feature is enabled.
///
/// Phase 1: Module registration only. Full indicator bindings (with #[pyclass])
/// come in Phase 5 when strategies need Python interop.

#[cfg(feature = "python")]
use pyo3::prelude::*;

/// Returns the crate version.
#[cfg(feature = "python")]
#[pyfunction]
fn version() -> String {
    env!("CARGO_PKG_VERSION").to_string()
}

#[cfg(feature = "python")]
pub fn register_module(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(version, m)?)?;
    Ok(())
}
