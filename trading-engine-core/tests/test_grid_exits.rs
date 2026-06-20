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

/// #13 cost-basis: a BUY adds inventory at cost — it does NOT realize a loss.
#[tokio::test]
async fn test_buy_fill_accumulates_inventory_at_cost() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new_with_state_dir("BTCUSDT", &config, 0.01, 0.00001, &isolated_state_dir("buy"), TelegramBot::disabled());

    let fill = Fill {
        fill_id: "fill_1".to_string(),
        order_id: "grid_buy_0".to_string(),
        client_order_id: Some("grid_buy_0".to_string()),
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Buy,
        price: 49500.0,
        quantity: 0.01,
        fee: 49500.0 * 0.01 * 0.001, // 0.495
        timestamp: 0,
    };

    let orders = strategy.on_fill(&fill).await.unwrap();
    assert!(orders.is_empty(), "no replacement orders on fill");

    // Buy must NOT realize a loss (cost-basis accounting)...
    assert!(strategy.realized_pnl().abs() < 1e-9, "buy realizes no PnL, got {}", strategy.realized_pnl());
    // ...and inventory is held at cost = price*qty + fee.
    let cost = 49500.0 * 0.01 + fill.fee;
    assert!((strategy.deployed_capital() - cost).abs() < 1e-6,
        "deployed_capital should equal inventory cost basis {}, got {}", cost, strategy.deployed_capital());
}

/// #13 cost-basis: a SELL realizes NET PnL = sell_revenue − avg_cost_basis_sold.
#[tokio::test]
async fn test_sell_fill_realizes_net_round_trip() {
    let config = default_grid_config();
    let mut strategy = GridStrategy::new_with_state_dir("BTCUSDT", &config, 0.01, 0.00001, &isolated_state_dir("sell"), TelegramBot::disabled());

    // Buy @ 49500 qty 0.01 fee 0.495 → inventory_cost = 495.495
    let buy_fill = Fill {
        fill_id: "fill_buy".to_string(),
        order_id: "grid_buy_0".to_string(),
        client_order_id: Some("grid_buy_0".to_string()),
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Buy,
        price: 49500.0,
        quantity: 0.01,
        fee: 0.495,
        timestamp: 0,
    };
    strategy.on_fill(&buy_fill).await.unwrap();

    // Sell @ 50500 qty 0.01 fee 0.505 → realized = (505 − 0.505) − 495.495 = 9.0
    let sell_fill = Fill {
        fill_id: "fill_sell".to_string(),
        order_id: "grid_sell_0".to_string(),
        client_order_id: Some("grid_sell_0".to_string()),
        symbol: "BTCUSDT".to_string(),
        side: OrderSide::Sell,
        price: 50500.0,
        quantity: 0.01,
        fee: 0.505,
        timestamp: 0,
    };
    strategy.on_fill(&sell_fill).await.unwrap();

    assert!((strategy.realized_pnl() - 9.0).abs() < 1e-2,
        "net round-trip PnL should be ~9.0, got {}", strategy.realized_pnl());
    assert!(strategy.realized_pnl() > 0.0, "buy-low sell-high is profitable");
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
