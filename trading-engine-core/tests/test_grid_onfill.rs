use trading_engine_core::config::GridConfig;
use trading_engine_core::notifications::TelegramBot;
use trading_engine_core::strategy::grid::GridStrategy;
use trading_engine_core::strategy::Strategy;
use trading_engine_core::connector::types::Fill;
use trading_engine_core::models::order::OrderSide;

fn grid_cfg() -> GridConfig {
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
        max_inventory_pct: 60.0,
    }
}

/// #13 cost-basis: a BUY fill persists state + does NOT realize a loss.
#[tokio::test]
async fn test_grid_on_fill_persists_state() {
    // Isolate the journal DB so the test doesn't write to the production path.
    let jpath = std::env::temp_dir().join("test_grid_onfill_journal.db");
    let _ = std::fs::remove_file(&jpath);
    std::env::set_var("GRID_JOURNAL_PATH", jpath.to_str().unwrap());

    let dir = std::env::temp_dir().join("test_grid_onfill_state");
    std::fs::create_dir_all(&dir).unwrap();
    let _ = std::fs::remove_file(dir.join("DOGE_USDT_grid_state.json"));

    let mut grid = GridStrategy::new_with_state_dir("DOGE-USDT", &grid_cfg(), 0.0001, 1.0, dir.to_str().unwrap(), TelegramBot::disabled());
    let fill = Fill {
        fill_id: "f1".into(),
        order_id: "grid_DOGE-USDT_buy_2".into(),
        client_order_id: Some("grid_DOGE-USDT_buy_2".into()),
        symbol: "DOGEUSDT".into(),
        side: OrderSide::Buy,
        price: 0.10,
        quantity: 1000.0,
        fee: 0.1,
        timestamp: 1,
    };
    grid.on_fill(&fill).await.unwrap();
    // #13: a buy adds inventory at cost — it is NOT a realized loss.
    assert!(grid.realized_pnl().abs() < 1e-9, "buy realizes no PnL, got {}", grid.realized_pnl());
    assert!(dir.join("DOGE_USDT_grid_state.json").exists(), "on_fill persisted grid state");
}
