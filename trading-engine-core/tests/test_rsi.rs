use trading_engine_core::indicators::Rsi;

#[test]
fn test_rsi_not_initialized_before_period() {
    let mut r = Rsi::new(14);
    for _ in 0..13 { r.update(100.0); }
    assert!(!r.is_initialized());
    r.update(100.0);
    assert!(r.is_initialized());
}

#[test]
fn test_rsi_constant_is_50() {
    let mut r = Rsi::new(5);
    for _ in 0..6 { r.update(100.0); }
    assert!(r.is_initialized());
    assert_eq!(r.value(), 50.0);
}

#[test]
fn test_rsi_all_gains_near_100() {
    let mut r = Rsi::new(5);
    for i in 1..=7 { r.update(i as f64); }
    assert!(r.is_initialized());
    assert!((r.value() - 100.0).abs() < 1.0);
}

#[test]
fn test_rsi_all_losses_near_0() {
    let mut r = Rsi::new(5);
    for i in (1..=7).rev() { r.update(i as f64); }
    assert!(r.is_initialized());
    assert!(r.value() < 5.0);
}

#[test]
fn test_rsi_alternating_near_50() {
    let mut r = Rsi::new(3);
    for p in [10.0, 11.0, 10.0, 11.0, 10.0, 11.0, 10.0] { r.update(p); }
    assert!(r.is_initialized());
    assert!((r.value() - 50.0).abs() < 15.0);
}

#[test]
fn test_rsi_reset() {
    let mut r = Rsi::new(5);
    for i in 0..6 { r.update(i as f64); }
    assert!(r.is_initialized());
    r.reset();
    assert!(!r.is_initialized());
    assert_eq!(r.count(), 0);
}

#[test]
fn test_rsi_count() {
    let mut r = Rsi::new(3);
    assert_eq!(r.count(), 0);
    r.update(1.0);
    assert_eq!(r.count(), 1);
}
