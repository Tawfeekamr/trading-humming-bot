//! # trading-engine-core
//!
//! Shared trading engine core — grid, trend, and signal strategies
//! with pluggable execution adapters. Written in Rust, exposed to Python via PyO3.

pub mod models;
pub mod indicators;
pub mod strategy;
pub mod risk;
pub mod adapter;

#[cfg(feature = "python")]
pub mod python;

/// PyO3 module entry point. Only used when building the Python extension.
#[cfg(feature = "python")]
use pyo3::prelude::*;

#[cfg(feature = "python")]
#[pymodule]
fn trading_engine_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    python::register_module(m)?;
    Ok(())
}
