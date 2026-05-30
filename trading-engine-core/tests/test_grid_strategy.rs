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
