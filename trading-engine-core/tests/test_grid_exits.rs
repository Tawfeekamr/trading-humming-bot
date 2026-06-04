use trading_engine_core::strategy::grid::GridStrategy;
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
    }
}

#[tokio::test]
async fn test_buy_fill_records_negative_pnl() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new("BTCUSDT", &config, 0.01, 0.00001);
    let initial_capital = strategy.current_capital();

    let fill = Fill {
        fill_id: "fill_1".to_string(),
        order_id: "grid_buy_0".to_string(),
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

#[tokio::test]
async fn test_sell_fill_records_positive_pnl() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new("BTCUSDT", &config, 0.01, 0.00001);

    // First simulate a buy to have inventory
    let buy_fill = Fill {
        fill_id: "fill_buy".to_string(),
        order_id: "grid_buy_0".to_string(),
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
