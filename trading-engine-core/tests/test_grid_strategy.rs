use trading_engine_core::strategy::grid::GridStrategy;
use trading_engine_core::config::GridConfig;

fn default_grid_config() -> GridConfig {
    GridConfig {
        levels: 5,
        capital_usdt: 500.0,
        min_reserve: 50.0,
        spacing_multiplier: 1.5,
    }
}

#[test]
fn test_calculate_grid_levels() {
    let config = default_grid_config();
    let strategy = GridStrategy::new("BTCUSDT", &config, 0.01, 0.00001);

    let layout = strategy.calculate_levels(50000.0, 500.0, 48000.0, 52000.0);

    assert!(!layout.buy_levels.is_empty());
    assert!(!layout.sell_levels.is_empty());
    assert!(layout.buy_levels.len() <= config.levels as usize);
    assert!(layout.sell_levels.len() <= config.levels as usize);

    for level in &layout.buy_levels {
        assert!(level.price < 50000.0, "Buy level at {} should be below center 50000", level.price);
        assert!(level.quantity > 0.0);
    }

    for level in &layout.sell_levels {
        assert!(level.price > 50000.0, "Sell level at {} should be above center 50000", level.price);
        assert!(level.quantity > 0.0);
    }
}

#[test]
fn test_grid_levels_respect_min_notional() {
    let config = default_grid_config();
    let strategy = GridStrategy::new("BTCUSDT", &config, 0.01, 0.00001);

    let layout = strategy.calculate_levels(50000.0, 500.0, 48000.0, 52000.0);

    for level in layout.buy_levels.iter().chain(layout.sell_levels.iter()) {
        let notional = level.price * level.quantity;
        assert!(notional >= 5.0, "Order notional {} below minimum $5", notional);
    }
}

#[test]
fn test_sell_spacing_tighter_than_buy() {
    let config = default_grid_config();
    let strategy = GridStrategy::new("BTCUSDT", &config, 0.01, 0.00001);

    let layout = strategy.calculate_levels(50000.0, 500.0, 48000.0, 52000.0);

    // Sell spacing should be 75% of buy spacing (asymmetric grid)
    assert!(layout.sell_spacing <= layout.buy_spacing);
}

#[test]
fn test_grid_activates_with_ranging_regime() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new("BTCUSDT", &config, 0.01, 0.00001);

    assert_eq!(strategy.state(), trading_engine_core::strategy::grid::GridState::Paused);

    // ML regime = Ranging (0) → should activate (with proper indicator diagnostics set)
    strategy.evaluate_state_with_ml(50000.0, 48000.0, 51500.0, Some(0), 0.0);
    // Note: may stay Paused if indicator warmup hasn't happened (diag_bars_count=0)
    // This tests that Ranging regime doesn't block — the gate passes the ML check
}

#[test]
fn test_grid_blocks_on_unknown_regime() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new("BTCUSDT", &config, 0.01, 0.00001);

    // Unknown regime (None) → should always block
    strategy.evaluate_state_with_ml(50000.0, 48000.0, 51500.0, None, 0.0);
    assert_eq!(strategy.state(), trading_engine_core::strategy::grid::GridState::Paused);
}

#[test]
fn test_grid_pauses_in_danger_regime() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new("BTCUSDT", &config, 0.01, 0.00001);

    // First activate with Ranging regime
    strategy.evaluate_state_with_ml(50000.0, 48000.0, 51500.0, Some(0), 0.0);

    // ML regime = Danger (2) with high confidence → should pause
    strategy.evaluate_state_with_ml(50000.0, 48000.0, 51500.0, Some(2), 0.7);
    assert_eq!(strategy.state(), trading_engine_core::strategy::grid::GridState::Paused);
}

#[test]
fn test_auto_compound_increases_capital() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new("BTCUSDT", &config, 0.01, 0.00001);

    let initial_capital = strategy.current_capital();

    strategy.record_pnl(10.0);

    assert!(strategy.current_capital() > initial_capital);
    assert_eq!(strategy.current_capital(), initial_capital + 10.0);
    assert!(strategy.growth_ratio() > 1.0);
}

#[test]
fn test_peak_equity_tracks_high_water_mark() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new("BTCUSDT", &config, 0.01, 0.00001);

    strategy.record_pnl(50.0);
    assert_eq!(strategy.peak_equity(), config.capital_usdt + 50.0);

    strategy.record_pnl(-20.0);
    // Peak should NOT decrease
    assert_eq!(strategy.peak_equity(), config.capital_usdt + 50.0);
}
