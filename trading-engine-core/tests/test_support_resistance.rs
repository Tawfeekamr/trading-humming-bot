use trading_engine_core::indicators::{SupportResistance, LevelKind};

#[test]
fn test_sr_no_levels_without_data() {
    let sr = SupportResistance::new(3, 0.005);
    assert!(sr.get_levels().is_empty());
}

#[test]
fn test_sr_detects_resistance() {
    // Build a series where index 3 is the pivot high
    // Window=3 means a high is resistance if it's the highest in 3 bars on each side
    let mut sr = SupportResistance::new(3, 0.005);
    // After processing bar 6, we check if bar 3 (6 - 3) is a pivot
    // prices: low, low, low, HIGH, low, low, low
    let data = [
        (100.0, 101.0, 99.0, 100.5),   // bar 0
        (100.5, 102.0, 100.0, 101.5),   // bar 1
        (101.5, 103.0, 101.0, 102.0),   // bar 2
        (102.0, 110.0, 101.0, 102.5),   // bar 3: HIGH = 110.0
        (102.5, 104.0, 102.0, 103.0),   // bar 4
        (103.0, 105.0, 102.5, 103.5),   // bar 5
        (103.5, 106.0, 103.0, 104.0),   // bar 6
    ];
    for (o, h, l, c) in data {
        sr.update_bar(o, h, l, c, 0);
    }
    let levels = sr.get_levels();
    assert!(!levels.is_empty());
    // Should detect 110.0 as a resistance
    assert!(levels.iter().any(|l| (l.price - 110.0).abs() < 0.1));
}

#[test]
fn test_sr_near_support() {
    let mut sr = SupportResistance::new(3, 0.005);
    // Create a support level at ~99.0
    // After bar 6, len=7, pivot_idx=7-1-3=3, so bar 3 is checked
    // For bar 3 to be a support, its low must be lowest in window [0,1,2,3,4,5,6]
    let data = [
        (100.0, 102.0, 99.8, 101.0),   // bar 0
        (101.0, 103.0, 99.7, 102.0),   // bar 1
        (102.0, 104.0, 99.6, 100.0),   // bar 2
        (100.0, 103.0, 99.0, 101.0),   // bar 3: Low=99.0, will be checked as pivot after bar 6
        (101.0, 104.0, 99.5, 100.0),   // bar 4
        (102.0, 105.0, 99.4, 101.0),   // bar 5
        (103.0, 106.0, 99.3, 102.0),   // bar 6: now we check bar 3 as pivot
    ];
    for (o, h, l, c) in data {
        sr.update_bar(o, h, l, c, 0);
    }
    // Price near 99.0 should be near support
    assert!(sr.near_support(99.1));
    assert!(!sr.near_support(105.0));
}

#[test]
fn test_sr_merge_close_levels() {
    let mut sr = SupportResistance::new(3, 0.01); // 1% merge threshold
    // Two nearby lows that should merge
    let data = [
        (100.0, 102.0, 99.0, 101.0),
        (101.0, 103.0, 98.5, 102.0),
        (102.0, 104.0, 99.0, 100.0),
        (100.0, 103.0, 98.7, 101.0),
        (101.0, 104.0, 99.0, 100.0),
    ];
    for (o, h, l, c) in data {
        sr.update_bar(o, h, l, c, 0);
    }
    // With 1% merge, 98.5 and 99.0 should merge (0.5% apart)
    let support_levels: Vec<_> = sr.get_levels().iter()
        .filter(|l| matches!(l.kind, LevelKind::Support))
        .collect();
    // Should be merged into fewer levels
    assert!(support_levels.len() <= 2);
}
