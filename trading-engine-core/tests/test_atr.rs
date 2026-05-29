use trading_engine_core::indicators::Atr;

#[test]
fn test_atr_not_initialized() {
    let mut a = Atr::new(14);
    for _ in 0..13 { a.update_bar(100.0, 101.0, 99.0, 100.5); }
    assert!(!a.is_initialized());
    a.update_bar(100.0, 101.0, 99.0, 100.5);
    assert!(a.is_initialized());
}

#[test]
fn test_atr_first_bar_range() {
    let mut a = Atr::new(3);
    a.update_bar(100.0, 105.0, 100.0, 103.0);
    a.update_bar(103.0, 108.0, 103.0, 106.0);
    a.update_bar(106.0, 111.0, 106.0, 109.0);
    assert!(a.is_initialized());
    assert!((a.value() - 5.0).abs() < 0.01);
}

#[test]
fn test_atr_true_range_gap() {
    let mut a = Atr::new(3);
    a.update_bar(96.0, 100.0, 95.0, 98.0);
    a.update_bar(100.0, 105.0, 100.0, 103.0);
    a.update_bar(103.0, 108.0, 103.0, 106.0);
    assert!(a.is_initialized());
    assert!((a.value() - 5.667).abs() < 0.1);
}

#[test]
fn test_atr_wilder_smoothing() {
    let mut a = Atr::new(3);
    for _ in 0..3 { a.update_bar(10.0, 12.0, 10.0, 11.0); }
    assert!((a.value() - 2.0).abs() < 0.01);
    a.update_bar(11.0, 15.0, 11.0, 14.0);
    assert!((a.value() - 2.667).abs() < 0.01);
}

#[test]
fn test_atr_breakout() {
    let mut a = Atr::new(3);
    for _ in 0..8 { a.update_bar(10.0, 10.5, 10.0, 10.2); }
    assert!(a.is_breakout(10.0, 15.0, 10.0, 14.0));
}

#[test]
fn test_atr_no_breakout() {
    let mut a = Atr::new(3);
    for _ in 0..8 { a.update_bar(10.0, 10.5, 10.0, 10.2); }
    assert!(!a.is_breakout(10.0, 10.6, 10.0, 10.3));
}

#[test]
fn test_atr_reset() {
    let mut a = Atr::new(3);
    for _ in 0..4 { a.update_bar(10.0, 12.0, 10.0, 11.0); }
    assert!(a.is_initialized());
    a.reset();
    assert!(!a.is_initialized());
    assert_eq!(a.count(), 0);
}
