use trading_engine_core::config::GridConfig;
use trading_engine_core::strategy::grid::GridStrategy;
use trading_engine_core::strategy::Strategy;

fn cfg() -> GridConfig {
    GridConfig {
        levels: 5,
        capital_usdt: 10000.0,
        min_reserve: 100.0,
        spacing_multiplier: 1.5,
        adx_range_max: 25.0,
        chop_range_min: 50.0,
        natr_floor: 0.005,
        natr_ceil: 0.04,
        fill_cooldown_secs: 60,
        ml_trending_block_threshold: 0.75,
        ml_danger_block_threshold: 0.55,
    }
}

#[test]
fn test_state_roundtrips_through_file() {
    let dir = std::env::temp_dir().join("test_grid_state_rt");
    std::fs::create_dir_all(&dir).unwrap();
    let _ = std::fs::remove_file(dir.join("DOGE_USDT_grid_state.json"));

    let mut grid = GridStrategy::new_with_state_dir("DOGE-USDT", &cfg(), 0.0001, 1.0, dir.to_str().unwrap());
    grid.record_pnl(250.0);
    grid.set_level_cooldown("buy_2".to_string(), 1_700_000_000_000);
    grid.save_state_to(dir.to_str().unwrap());

    // Fresh instance loads the persisted state.
    let grid2 = GridStrategy::new_with_state_dir("DOGE-USDT", &cfg(), 0.0001, 1.0, dir.to_str().unwrap());
    assert!((grid2.realized_pnl() - 250.0).abs() < 1e-6, "realized_pnl restored");
    assert_eq!(grid2.peak_equity_pub(), 10250.0, "peak equity = initial + realized");
    assert!(grid2.has_level_cooldown("buy_2"), "cooldown restored");
}

#[test]
fn test_corrupt_state_starts_fresh() {
    let dir = std::env::temp_dir().join("test_grid_state_corrupt");
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("ETH_USDT_grid_state.json"), "{ not valid json").unwrap();
    let grid = GridStrategy::new_with_state_dir("ETH-USDT", &cfg(), 0.0001, 1.0, dir.to_str().unwrap());
    assert!(grid.realized_pnl().abs() < 1e-6, "corrupt file -> fresh start, no panic");
}

#[test]
fn test_mtm_uses_cached_balances() {
    let dir = std::env::temp_dir().join("test_grid_mtm");
    std::fs::create_dir_all(&dir).unwrap();
    let mut grid = GridStrategy::new_with_state_dir("DOGE-USDT", &cfg(), 0.0001, 1.0, dir.to_str().unwrap());
    grid.set_mtm_snapshot_for_test(5000.0, 9500.0, 0.12);
    let status = grid.status();
    // MTM = base*mid + quote = 5000*0.12 + 9500 = 10100
    assert!(status.details.contains("MTM $10100"), "details show MTM; got: {}", status.details);
}
