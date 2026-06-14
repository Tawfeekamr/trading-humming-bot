use trading_engine_core::strategy::trend::{TrendStrategy, TrendPosition};
use trading_engine_core::config::TrendConfig;
use trading_engine_core::models::bar::Bar;

fn default_trend_config() -> TrendConfig {
    TrendConfig {
        ema_fast: 20,
        ema_slow: 50,
        ema_trend: 200,
        rsi_period: 14,
        rsi_min: 40.0,
        rsi_max: 80.0,
        min_signal_score: 3,
        confirmation_ticks: 2,
        risk_reward_ratio: 2.0,
        capital: 10000.0,
        risk_per_trade_pct: 2.0,
        max_position_pct: 25.0,
        trailing_stop_pct: 1.5,
        trailing_stop_atr_mult: 2.5,
        trailing_activation_pct: 1.5,
        exit_signal_threshold: 2,
        sl_buffer_pct: 0.2,
        adx_gate_threshold: 25.0,
        adx_exit_threshold: 20.0,
        choppiness_threshold: 38.0,
        volume_ratio_threshold: 1.2,
        entry_score_threshold: 5,
        rsi_long_max: 65.0,
        rsi_short_min: 35.0,
        atr_trailing_mult: 3.0,
        trade_shorts: false,
    }
}

fn make_bar(close: f64) -> Bar {
    Bar::new(close - 10.0, close + 10.0, close - 5.0, close, 100.0, 0)
}

#[test]
fn test_stop_loss_and_take_profit_calculation() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("BTCUSDT", &config, telegram);

    for i in 0..250 {
        let price = 50000.0 + (i as f64 * 0.5);
        strategy.update_indicators(&make_bar(price));
    }

    let sl = strategy.calculate_stop_loss(50000.0);
    assert!(sl < 50000.0, "Stop loss should be below entry");

    let tp_levels = TrendPosition::calculate_tp_levels(50000.0, sl, 2.0, 0.10);
    assert_eq!(tp_levels.len(), 3, "Should have 3 TP levels");
    assert!(tp_levels[0].price > 50000.0, "TP1 should be above entry");
    assert!(tp_levels[2].price > tp_levels[1].price, "TPs should be ascending");

    let risk = 50000.0 - sl;
    let expected_tp3 = 50000.0 + risk * 2.0;
    assert!((tp_levels[2].price - expected_tp3).abs() < 0.01,
        "TP3 should match 2:1 R:R, got {}", tp_levels[2].price);
}

#[test]
fn test_indicators_not_ready_initially() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("BTCUSDT", &config, telegram);

    // Feed only a few bars — not enough to fully warm all indicators
    for _i in 0..5 {
        strategy.update_indicators(&make_bar(50000.0));
    }

    // Strategy should report no position
    assert!(strategy.position().is_none(), "Should have no position before any entry");
}

#[test]
fn test_realized_pnl_accessor_default_zero() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let strategy = TrendStrategy::new("BTCUSDT", &config, telegram);
    use trading_engine_core::strategy::Strategy;
    assert_eq!(strategy.realized_pnl(), 0.0, "fresh strategy has zero realized PnL");
}
