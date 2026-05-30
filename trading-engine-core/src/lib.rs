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
