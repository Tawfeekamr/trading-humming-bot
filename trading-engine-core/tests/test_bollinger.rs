use trading_engine_core::indicators::BollingerBands;

#[test]
fn test_bb_not_initialized() {
    let mut bb = BollingerBands::new(5, 2.0);
    for _ in 0..4 { bb.update(100.0); }
    assert!(!bb.is_initialized());
    bb.update(100.0);
    assert!(bb.is_initialized());
}

#[test]
fn test_bb_constant_equal_bands() {
    let mut bb = BollingerBands::new(5, 2.0);
    for _ in 0..6 { bb.update(100.0); }
    assert!((bb.upper() - 100.0).abs() < 1e-10);
    assert!((bb.middle() - 100.0).abs() < 1e-10);
    assert!((bb.lower() - 100.0).abs() < 1e-10);
}

#[test]
fn test_bb_known_values() {
    let mut bb = BollingerBands::new(5, 2.0);
    for p in [100.0, 102.0, 98.0, 101.0, 99.0] { bb.update(p); }
    assert!((bb.middle() - 100.0).abs() < 0.01);
    assert!((bb.upper() - 102.828).abs() < 0.05);
    assert!((bb.lower() - 97.172).abs() < 0.05);
}

#[test]
fn test_bb_percent_b() {
    let mut bb = BollingerBands::new(5, 2.0);
    for p in [100.0, 102.0, 98.0, 101.0, 99.0] { bb.update(p); }
    assert!(bb.percent_b() < 0.5);
    assert!(bb.percent_b() > 0.3);
}

#[test]
fn test_bb_bandwidth() {
    let mut bb = BollingerBands::new(5, 2.0);
    for p in [100.0, 102.0, 98.0, 101.0, 99.0] { bb.update(p); }
    assert!(bb.bandwidth() > 0.04);
    assert!(bb.bandwidth() < 0.07);
}

#[test]
fn test_bb_rolling_window() {
    let mut bb = BollingerBands::new(3, 2.0);
    bb.update(100.0); bb.update(200.0); bb.update(50.0); bb.update(60.0); bb.update(70.0);
    assert!((bb.middle() - 60.0).abs() < 0.01);
}

#[test]
fn test_bb_reset() {
    let mut bb = BollingerBands::new(5, 2.0);
    for i in 0..6 { bb.update(i as f64); }
    assert!(bb.is_initialized());
    bb.reset();
    assert!(!bb.is_initialized());
}
