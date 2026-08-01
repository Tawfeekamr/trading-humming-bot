use trading_engine_core::strategy::trend::{TrendStrategy, TrendPosition};
use trading_engine_core::strategy::Strategy;
use trading_engine_core::config::TrendConfig;
use trading_engine_core::models::bar::Bar;
use trading_engine_core::models::order::OrderSide;

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
        trailing_stop_atr_mult: 2.5,
        exit_signal_threshold: 2,
        sl_buffer_pct: 0.2,
        adx_gate_threshold: 25.0,
        adx_exit_threshold: 20.0,
        choppiness_threshold: 38.0,
        volume_ratio_threshold: 1.2,
        entry_score_threshold: 5,
        rsi_long_max: 65.0,
        rsi_short_min: 35.0,
        trade_shorts: false,
        perp_mark_source: None,
        funding_accrual: false,
        ..Default::default()
    }
}

fn make_bar(close: f64) -> Bar {
    Bar::new(close - 10.0, close + 10.0, close - 5.0, close, 100.0, 0)
}

#[test]
fn test_stop_loss_and_hybrid_target_calculation() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let strategy = TrendStrategy::new("TP-CALC-USDT", &config, telegram);

    let entry = 50_000.0;
    let stop = 49_000.0;
    let levels = TrendPosition::calculate_tp_levels(entry, stop, 2.0, 0.10, OrderSide::Buy);
    assert_eq!(levels.len(), 2, "hybrid trend uses exactly TP1 and TP2");
    assert_eq!(levels[0].price, 51_000.0);
    assert_eq!(levels[1].price, 51_500.0);
    assert_eq!(levels[0].close_pct, 0.33);
    assert_eq!(levels[1].close_pct, 0.50);
    assert!(!levels[0].filled && !levels[1].filled);

    let short_levels = TrendPosition::calculate_tp_levels(entry, 51_000.0, 2.0, 0.10, OrderSide::Sell);
    assert_eq!(short_levels.len(), 2);
    assert_eq!(short_levels[0].price, 49_000.0);
    assert_eq!(short_levels[1].price, 48_500.0);
    assert_eq!(short_levels[0].close_pct, 0.33);
    assert_eq!(short_levels[1].close_pct, 0.50);
    let _ = strategy;
}

#[test]
fn test_indicators_not_ready_initially() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    // Fresh pair (no persisted position file) — else a parallel test writing
    // data/BTCUSDT_trend_position.json loads a position here and makes this
    // non-deterministic under parallel test scheduling (recurring CI flake).
    let mut strategy = TrendStrategy::new("FRESH-INDICATORS-USDT", &config, telegram);

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
    // Use a pair with no persisted position file so the strategy is genuinely fresh
    // (test_trend_journal writes data/BTCUSDT_trend_position.json and would otherwise
    // be loaded, restoring a non-zero realized_pnl).
    let strategy = TrendStrategy::new("FRESHPAIR-USDT", &config, telegram);
    use trading_engine_core::strategy::Strategy;
    assert_eq!(strategy.realized_pnl(), 0.0, "fresh strategy has zero realized PnL");
}


#[test]
fn test_deployed_capital_zero_when_flat() {
    let config = default_trend_config();
    // Fresh pair (no persisted position file) — else a parallel test writing
    // data/BTCUSDT_trend_position.json loads a position and deployed_capital > 0
    // (recurring CI flake under parallel test scheduling).
    let strategy = TrendStrategy::new(
        "FRESH-DEPCAP-USDT", &config, trading_engine_core::notifications::TelegramBot::new("", ""));
    assert!(strategy.deployed_capital() < 1e-9, "flat trend has no deployed capital");
}
