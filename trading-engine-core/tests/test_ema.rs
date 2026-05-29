use trading_engine_core::indicators::Ema;

#[test]
fn test_ema_initial_value() {
    let mut e = Ema::new(10);
    e.update(100.0);
    assert_eq!(e.value(), 100.0);
}

#[test]
fn test_ema_not_initialized_before_period() {
    let mut e = Ema::new(5);
    for _ in 0..4 { e.update(1.0); }
    assert!(!e.is_initialized());
    e.update(1.0);
    assert!(e.is_initialized());
}

#[test]
fn test_ema_alpha() {
    let mut e = Ema::new(3);
    e.update(10.0);
    e.update(12.0);
    assert!((e.value() - 11.0).abs() < 1e-10);
    e.update(14.0);
    assert!((e.value() - 12.5).abs() < 1e-10);
}

#[test]
fn test_ema_smoothing() {
    let mut e = Ema::new(10);
    for _ in 0..10 { e.update(100.0); }
    e.update(110.0);
    assert!(e.is_initialized());
    let expected = 100.0 + (2.0/11.0) * 10.0;
    assert!((e.value() - expected).abs() < 0.01);
}

#[test]
fn test_ema_reset() {
    let mut e = Ema::new(5);
    for i in 0..5 { e.update(i as f64); }
    assert!(e.is_initialized());
    e.reset();
    assert!(!e.is_initialized());
    assert_eq!(e.count(), 0);
}

#[test]
fn test_ema_count() {
    let mut e = Ema::new(3);
    assert_eq!(e.count(), 0);
    e.update(1.0);
    assert_eq!(e.count(), 1);
}
