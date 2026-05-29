use trading_engine_core::indicators::{CandlestickPatterns, Pattern};
use trading_engine_core::models::bar::Bar;

fn bar(o: f64, h: f64, l: f64, c: f64) -> Bar { Bar::new(o, h, l, c, 1000.0, 0) }

#[test]
fn test_doji() {
    let cp = CandlestickPatterns::new(0.1);
    assert_eq!(cp.detect(&bar(100.0, 105.0, 95.0, 100.1), None), Pattern::Doji);
}

#[test]
fn test_hammer() {
    let cp = CandlestickPatterns::new(0.1);
    assert_eq!(cp.detect(&bar(100.0, 101.0, 95.0, 100.5), None), Pattern::Hammer);
}

#[test]
fn test_bullish_engulfing() {
    let cp = CandlestickPatterns::new(0.1);
    let prev = bar(102.0, 103.0, 100.0, 100.5);
    let curr = bar(99.0, 104.0, 98.0, 103.5);
    assert_eq!(cp.detect(&curr, Some(&prev)), Pattern::BullishEngulfing);
}

#[test]
fn test_bearish_engulfing() {
    let cp = CandlestickPatterns::new(0.1);
    let prev = bar(100.0, 103.0, 99.0, 102.5);
    let curr = bar(103.0, 104.0, 99.5, 100.0);
    assert_eq!(cp.detect(&curr, Some(&prev)), Pattern::BearishEngulfing);
}

#[test]
fn test_no_pattern() {
    let cp = CandlestickPatterns::new(0.1);
    assert_eq!(cp.detect(&bar(100.0, 105.0, 99.0, 104.0), None), Pattern::None);
}

#[test]
fn test_inverted_hammer() {
    let cp = CandlestickPatterns::new(0.1);
    assert_eq!(cp.detect(&bar(100.0, 106.0, 99.5, 100.5), None), Pattern::InvertedHammer);
}
