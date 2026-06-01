//! # trading-engine-core
//!
//! Pure Rust trading engine — grid, trend strategies with Binance connector,
//! ML regime detection, risk management, and Telegram monitoring.

pub mod models;
pub mod indicators;
pub mod config;
pub mod connector;
pub mod strategy;
pub mod risk;
pub mod ml;
pub mod notifications;
pub mod signal;
pub mod engine;
pub mod api;

// ── PyO3 Python module (enabled with --features python) ─────────────
#[cfg(feature = "python")]
use pyo3::prelude::*;

#[cfg(feature = "python")]
#[pymodule]
fn trading_engine_core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<indicators::Ema>()?;
    m.add_class::<indicators::Rsi>()?;
    m.add_class::<indicators::Atr>()?;
    m.add_class::<indicators::BollingerBands>()?;
    Ok(())
}
