use trading_engine_core::strategy::grid::GridStrategy;
use trading_engine_core::notifications::TelegramBot;
use trading_engine_core::config::GridConfig;
use trading_engine_core::connector::types::Fill;
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::strategy::Strategy;

fn default_grid_config() -> GridConfig {
    GridConfig {
        levels: 5,
        capital_usdt: 500.0,
        min_reserve: 50.0,
        spacing_multiplier: 1.5,
        adx_range_max: 22.0,
        chop_range_min: 55.0,
        natr_floor: 0.005,
        natr_ceil: 0.04,
        fill_cooldown_secs: 60,
        ml_trending_block_threshold: 0.75,
        ml_danger_block_threshold: 0.55,
    }
}

/// Isolated temp state dir (unique per test) so on_fill writes don't pollute
/// other tests or load stale data/*.json.
fn isolated_state_dir(tag: &str) -> String {
    let dir = std::env::temp_dir().join(format!("test_grid_exits_{}_{}", tag, std::process::id()));
    let _ = std::fs::remove_file(dir.join("BTCUSDT_grid_state.json"));
    std::fs::create_dir_all(&dir).unwrap();
    dir.to_str().unwrap().to_string()
}

/// OBSOLETE: asserts pre-#13 accounting where a BUY fill reduced current_capital
/// by cost+fee. Since #13 (realized cost-basis), buys add inventory at cost and do
/// NOT change current_capital — so this assertion no longer holds. Ignored pending a
/// rewrite against the current cost-basis model.
#[ignore]
#[tokio::test]
async fn test_buy_fill_records_negative_pnl() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new_with_state_dir("BTCUSDT", &config, 0.01, 0.00001, &isolated_state_dir("buy"), TelegramBot::disabled());
    let initial_capital = strategy.current_capital();

    let fill = Fill {
        fill_id: "fill_1".to_string(),
        order_id: "grid_buy_0".to_string(),
        client_order_id: None,
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Buy,
        price: 49500.0,
        quantity: 0.01,
        fee: 49500.0 * 0.01 * 0.001, // 0.1% fee = 0.495
        timestamp: 0,
    };

    let orders = strategy.on_fill(&fill).await.unwrap();

    // on_fill returns empty vec (no replacement orders — known gap)
    assert!(
        orders.is_empty(),
        "Grid on_fill should return empty Vec (no replenishment)"
    );

    // Capital should decrease: -(price * qty + fee)
    let expected_pnl = -(49500.0 * 0.01 + fill.fee);
    let actual_change = strategy.current_capital() - initial_capital;
    assert!(
        (actual_change - expected_pnl).abs() < 0.01,
        "Capital should decrease by cost + fee. Expected change: {}, got: {}",
        expected_pnl,
        actual_change
    );
}

/// OBSOLETE: asserts pre-#13 gross-revenue accounting on SELL. Since #13, sells
/// realize NET P&L (sell − avg cost basis − fees), not gross revenue. Ignored
/// pending a rewrite against the current cost-basis model.
#[ignore]
#[tokio::test]
async fn test_sell_fill_records_positive_pnl() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new_with_state_dir("BTCUSDT", &config, 0.01, 0.00001, &isolated_state_dir("sell"), TelegramBot::disabled());

    // First simulate a buy to have inventory
    let buy_fill = Fill {
        fill_id: "fill_buy".to_string(),
        order_id: "grid_buy_0".to_string(),
        client_order_id: None,
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Buy,
        price: 49500.0,
        quantity: 0.01,
        fee: 0.495,
        timestamp: 0,
    };
    strategy.on_fill(&buy_fill).await.unwrap();

    let capital_after_buy = strategy.current_capital();

    // Now sell at higher price
    let sell_fill = Fill {
        fill_id: "fill_sell".to_string(),
        order_id: "grid_sell_0".to_string(),
        client_order_id: None,
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Sell,
        price: 50500.0,
        quantity: 0.01,
        fee: 0.505,
        timestamp: 0,
    };

    let orders = strategy.on_fill(&sell_fill).await.unwrap();
    assert!(orders.is_empty());

    // Capital should increase: +(price * qty - fee)
    let expected_pnl = 50500.0 * 0.01 - 0.505;
    let actual_change = strategy.current_capital() - capital_after_buy;
    assert!(
        (actual_change - expected_pnl).abs() < 0.01,
        "Capital should increase by revenue - fee. Expected change: {}, got: {}",
        expected_pnl,
        actual_change
    );

    // Net P&L across both fills: sell revenue - buy cost - both fees
    let net_pnl = strategy.current_capital() - config.capital_usdt;
    // buy cost: 49500*0.01 = 495 + fee 0.495 = -495.495
    // sell revenue: 50500*0.01 = 505 - fee 0.505 = +504.495
    // net = +504.495 - 495.495 = +9.0
    assert!(
        net_pnl > 0.0,
        "Net P&L should be positive after buy low + sell high, got {}",
        net_pnl
    );
}


#[tokio::test]
async fn test_deployed_capital_tracks_inventory_cost() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new_with_state_dir(
        "BTCUSDT", &config, 0.01, 0.00001,
        &isolated_state_dir("deployed"), TelegramBot::disabled(),
    );
    assert!(strategy.deployed_capital() < 1e-9, "flat grid has no deployed capital");
    let fill = Fill {
        fill_id: "d1".to_string(), order_id: "grid_BTCUSDT_buy_0".to_string(),
        client_order_id: Some("grid_BTCUSDT_buy_0".to_string()),
        symbol: "BTCUSDT".to_string(), side: OrderSide::Buy,
        price: 49500.0, quantity: 0.01, fee: 0.495, timestamp: 0,
    };
    strategy.on_fill(&fill).await.unwrap();
    let expected = 49500.0 * 0.01 + 0.495; // inventory_cost = price*qty + fee
    assert!((strategy.deployed_capital() - expected).abs() < 1e-6,
        "deployed_capital should equal inventory cost basis after a buy");
}
