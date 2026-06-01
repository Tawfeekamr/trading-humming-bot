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
    }
}

fn make_bar(close: f64) -> Bar {
    Bar::new(close - 10.0, close + 10.0, close - 5.0, close, 100.0, 0)
}

#[test]
fn test_signal_scoring_returns_score() {
    let config = default_trend_config();
    let mut strategy = TrendStrategy::new("BTCUSDT", &config);

    for i in 0..250 {
        let price = 50000.0 + (i as f64 * 0.5);
        strategy.update_indicators(&make_bar(price));
    }

    let score = strategy.evaluate_signals(52000.0);
    assert!(score.total <= 8);
    assert!(!score.details.is_empty());
}

#[test]
fn test_strong_uptrend_scores_high() {
    let config = default_trend_config();
    let mut strategy = TrendStrategy::new("BTCUSDT", &config);

    // Create a moderate uptrend from 40000 to 46000
    // Less steep than before to keep RSI in valid range
    for i in 0..250 {
        let price = 40000.0 + (i as f64 * 25.0);
        // Add some noise to prevent RSI from going too high
        let noise = (i as f64 * 7.0).sin() * 50.0;
        strategy.update_indicators(&make_bar(price + noise));
    }

    let score = strategy.evaluate_signals(46000.0);
    // Should have EMA cross (+1) + trend filter (+1) at minimum
    assert!(score.total >= 2, "Uptrend should score >= 2, got {}", score.total);
}

#[test]
fn test_should_enter_requires_min_score() {
    let config = default_trend_config();
    let strategy = TrendStrategy::new("BTCUSDT", &config);

    use trading_engine_core::strategy::trend::SignalScore;
    let low_score = SignalScore { total: 2, details: vec![] };
    let high_score = SignalScore { total: 4, details: vec![] };

    assert!(!strategy.should_enter(&low_score));
    assert!(strategy.should_enter(&high_score));
}

#[test]
fn test_stop_loss_and_take_profit_calculation() {
    let config = default_trend_config();
    let mut strategy = TrendStrategy::new("BTCUSDT", &config);

    for i in 0..250 {
        let price = 50000.0 + (i as f64 * 0.5);
        strategy.update_indicators(&make_bar(price));
    }

    let sl = strategy.calculate_stop_loss(50000.0);
    assert!(sl < 50000.0, "Stop loss should be below entry");

    // TP levels are now in TrendPosition
    let tp_levels = TrendPosition::calculate_tp_levels(50000.0, sl, 2.0, 0.10);
    assert_eq!(tp_levels.len(), 3, "Should have 3 TP levels");
    assert!(tp_levels[0].price > 50000.0, "TP1 should be above entry");
    assert!(tp_levels[2].price > tp_levels[1].price, "TPs should be ascending");

    let risk = 50000.0 - sl;
    // TP3 should be at entry + risk * risk_reward_ratio
    let expected_tp3 = 50000.0 + risk * 2.0;
    assert!((tp_levels[2].price - expected_tp3).abs() < 0.01,
        "TP3 should match 2:1 R:R, got {}", tp_levels[2].price);
}

#[test]
fn test_indicators_not_ready_initially() {
    let config = default_trend_config();
    let mut strategy = TrendStrategy::new("BTCUSDT", &config);

    // Feed only a few bars — not enough to initialize
    for _i in 0..10 {
        strategy.update_indicators(&make_bar(50000.0));
    }

    let score = strategy.evaluate_signals(50000.0);
    assert_eq!(score.total, 0, "Should return 0 when indicators not initialized");
}
