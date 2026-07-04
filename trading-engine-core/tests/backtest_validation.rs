use trading_engine_core::backtest::validation::split_is_oos;
use trading_engine_core::models::bar::Bar;

fn bars(n: usize) -> Vec<Bar> {
    (0..n).map(|i| Bar::new(100.0, 101.0, 99.0, 100.0, 1.0, i as i64 * 3_600_000)).collect()
}

#[test]
fn split_is_two_thirds_one_third_contiguous_no_overlap() {
    let b = bars(300);
    let (is_b, oos_b) = split_is_oos(&b, 1.0 / 3.0);
    assert_eq!(is_b.len(), 200);
    assert_eq!(oos_b.len(), 100);
    // contiguous: IS ends where OOS begins
    assert_eq!(is_b.last().unwrap().timestamp, 199 * 3_600_000);
    assert_eq!(oos_b.first().unwrap().timestamp, 200 * 3_600_000);
    assert!(oos_b.last().unwrap().timestamp > is_b.last().unwrap().timestamp);
}

#[test]
fn split_empty_input_returns_two_empty_vecs() {
    let (is_b, oos_b) = split_is_oos(&[], 1.0 / 3.0);
    assert!(is_b.is_empty() && oos_b.is_empty());
}
