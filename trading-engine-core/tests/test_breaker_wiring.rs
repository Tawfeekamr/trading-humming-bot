use std::collections::HashMap;
use trading_engine_core::connector::types::{OrderRequest, OrderTypeReq, TimeInForceReq, OrderBook};
use trading_engine_core::engine::Engine;
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::risk::circuit_breaker::CircuitBreaker;
use trading_engine_core::risk::{RiskManager, PositionGuard};

#[test]
fn test_breaker_trips_on_drawdown_and_persists_fields() {
    let mut cb = CircuitBreaker::new(10.0, 5.0);
    cb.set_peak_equity(10000.0);
    cb.set_start_of_day_equity(10000.0);
    cb.set_last_reset_date("2026-06-14".to_string());
    assert!(!cb.check(9500.0), "5% drop from peak is under 10% DD");
    assert!(cb.check(8900.0), "11% drop trips max-drawdown");
    assert!(cb.is_halted_raw(), "halted flag set");
    assert_eq!(cb.last_reset_date(), "2026-06-14", "reset date stored");
}

#[test]
fn test_breaker_daily_loss_trips() {
    let mut cb = CircuitBreaker::new(10.0, 5.0);
    cb.set_start_of_day_equity(10000.0);
    assert!(cb.check_daily(9400.0), "6% daily loss trips");
    assert!(cb.is_halted_raw());
}

#[test]
fn test_risk_state_roundtrip() {
    let dir = std::env::temp_dir().join("test_risk_state_rt");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("risk_state.json");
    let _ = std::fs::remove_file(&path);

    let mut cb = CircuitBreaker::new(10.0, 5.0);
    cb.set_peak_equity(12000.0);
    cb.set_start_of_day_equity(11500.0);
    cb.set_last_reset_date("2026-06-14".to_string());
    trading_engine_core::risk::save_state(&cb, path.to_str().unwrap());

    let mut cb2 = CircuitBreaker::new(10.0, 5.0);
    trading_engine_core::risk::load_state(&mut cb2, path.to_str().unwrap(), 10000.0);
    assert_eq!(cb2.peak_equity(), 12000.0, "peak restored");
    assert_eq!(cb2.start_of_day_equity(), 11500.0, "SOD restored");
    assert_eq!(cb2.last_reset_date(), "2026-06-14");
}

#[test]
fn test_risk_state_missing_initializes_from_equity() {
    let mut cb = CircuitBreaker::new(10.0, 5.0);
    trading_engine_core::risk::load_state(&mut cb, "/nonexistent/risk_state.json", 9000.0);
    assert_eq!(cb.peak_equity(), 9000.0, "no file -> peak = current equity");
    assert_eq!(cb.start_of_day_equity(), 9000.0);
}

#[test]
fn test_record_equity_halts_at_drawdown_threshold() {
    let mut rm = RiskManager::new(PositionGuard::new(80.0, 100.0, 10000.0), CircuitBreaker::new(10.0, 5.0));
    rm.circuit_breaker.set_start_of_day_equity(10000.0);
    rm.record_equity(10000.0); // establishes peak
    assert!(rm.check_trading_allowed().is_ok(), "not halted at baseline");
    rm.record_equity(8900.0); // 11% drawdown
    assert!(rm.circuit_breaker.is_halted_raw(), "halted after 11% drawdown");
    assert!(rm.check_trading_allowed().is_err(), "trading blocked once halted");
}

#[test]
fn test_portfolio_equity_mtm() {
    let mut balances = HashMap::new();
    balances.insert("USDT".to_string(), 5000.0);
    balances.insert("BNB".to_string(), 10.0);
    let mut obs = HashMap::new();
    obs.insert("BNB-USDT".to_string(), OrderBook {
        symbol: "BNB-USDT".to_string(),
        bids: vec![(599.0, 1.0)],
        asks: vec![(601.0, 1.0)],
        timestamp: 0,
    });
    // MTM = 5000 USDT + 10 BNB × 600 (mid of 599/601) = 11000.
    // A grid buy (USDT down, BNB up at same price) leaves this unchanged.
    let eq = Engine::portfolio_equity_mtm(&balances, &obs);
    assert!((eq - 11000.0).abs() < 0.01, "MTM = USDT + base*mid, got {}", eq);
}

#[test]
fn test_load_state_resets_stale_realized_peak() {
    let dir = std::env::temp_dir().join("test_risk_state_migrate");
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("risk_state_migrate.json");
    // Legacy (pre-MTM) state: inflated realized-based peak + halted, old metric.
    std::fs::write(&path, r#"{"peak_equity":44707.0,"start_of_day_equity":20000.0,"halted":true,"halted_at_unix":1700000000,"last_reset_date":"2026-06-14","metric":"realized"}"#).unwrap();
    let mut cb = CircuitBreaker::new(10.0, 5.0);
    trading_engine_core::risk::load_state(&mut cb, path.to_str().unwrap(), 11000.0);
    assert!((cb.peak_equity() - 11000.0).abs() < 1e-6, "stale realized peak reset to current MTM equity");
    assert!(!cb.is_halted_raw(), "halt cleared on metric migration");
}

#[test]
fn test_reduce_only_flag_is_set_on_exit_order() {
    // Guard: exits must carry reduce_only=true so the breaker can't trap them.
    let req = OrderRequest {
        symbol: "BTCUSDT".into(),
        side: OrderSide::Sell,
        order_type: OrderTypeReq::Limit,
        price: Some(50000.0),
        quantity: 0.1,
        time_in_force: Some(TimeInForceReq::Gtc),
        client_order_id: None,
        reduce_only: true,
    };
    assert!(req.reduce_only, "exit order is reduce-only");
}
