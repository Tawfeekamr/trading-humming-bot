use std::collections::HashMap;
use trading_engine_core::config::{RunnerExitMode, SwingConfig};
use trading_engine_core::connector::types::OrderBook;
use trading_engine_core::models::bar::Bar;
use trading_engine_core::notifications::TelegramBot;
use trading_engine_core::strategy::swing::SwingStrategy;
use trading_engine_core::strategy::{Strategy, TickContext};

fn default_config() -> SwingConfig {
    SwingConfig {
        enabled: true,
        runner_exit: RunnerExitMode::BandOrChandelier,
        htf_period: "1h".to_string(),
        ltf_period: "5m".to_string(),
        donchian_period: 20,
        band_atr_mult: 0.5,
        rsi_period: 14,
        rsi_oversold: 30.0,
        volume_multiplier: 1.5,
        volume_avg_period: 20,
        atr_period: 14,
        atr_stop_mult: 1.5,
        min_rr: 2.0,
        min_score: 3,
        risk_per_trade_pct: 1.0,
        adx_range_entry: 22.0,
        adx_trend_exit: 28.0,
        capital: 1000.0,
        max_bars_in_trade: 48,
        enabled_pairs: vec![],
        step_size: None,
        tick_size: None,
        maker_entry: false,
        entry_timeout_bars: 2,
    }
}

fn make_tick(bars: Vec<Bar>) -> TickContext {
    TickContext {
        order_book: OrderBook {
            symbol: "BTCUSDT".to_string(),
            bids: vec![(bars.last().unwrap().close, 1.0)],
            asks: vec![(bars.last().unwrap().close, 1.0)],
            timestamp: bars.last().unwrap().timestamp,
        },
        recent_bars: bars,
        balances: HashMap::new(),
        open_orders: vec![],
        regime: None,
        regime_confidence: 0.0,
        timestamp: 0,
        capital: None,
    }
}

#[tokio::test]
async fn test_swing_strategy_skips_insufficient_bars() {
    let config = default_config();
    let mut strategy = SwingStrategy::new("BTC-USDT", &config, TelegramBot::new("", ""));

    // Need at least 50 bars. Provide 10.
    let mut bars = vec![];
    for i in 0..10 {
        bars.push(Bar::new(100.0, 105.0, 95.0, 100.0, 100.0, i * 60_000));
    }

    let orders = strategy.on_tick(&make_tick(bars)).await.unwrap();
    assert!(orders.is_empty(), "Should not trade without enough bars");
}


#[test]
fn test_deployed_capital_zero_when_flat() {
    let config = default_config();
    let strategy = SwingStrategy::new("BTC-USDT", &config, TelegramBot::new("", ""));
    assert!(strategy.deployed_capital() < 1e-9, "flat swing has no deployed capital");
}
