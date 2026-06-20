use trading_engine_core::strategy::trend::TrendStrategy;
use trading_engine_core::config::TrendConfig;
use trading_engine_core::models::bar::Bar;
use trading_engine_core::connector::types::OrderBook;
use trading_engine_core::strategy::{TickContext, Strategy};
use trading_engine_core::notifications::TelegramBot;

fn make_config() -> TrendConfig {
    TrendConfig {
        ema_fast: 9,
        ema_slow: 21,
        ema_trend: 50,
        rsi_period: 14,
        rsi_min: 30.0,
        rsi_max: 70.0,
        min_signal_score: 2,
        confirmation_ticks: 3,
        risk_reward_ratio: 2.0,
        capital: 10000.0,
        risk_per_trade_pct: 0.02,
        max_position_pct: 0.1,
        trailing_stop_pct: 0.0,
        trailing_stop_atr_mult: 0.0,
        trailing_activation_pct: 0.0,
        exit_signal_threshold: 0,
        sl_buffer_pct: 0.0,
        adx_gate_threshold: 25.0,
        adx_exit_threshold: 20.0,
        choppiness_threshold: 38.0,
        volume_ratio_threshold: 1.5,
        entry_score_threshold: 5,
        rsi_long_max: 70.0,
        rsi_short_min: 30.0,
        atr_trailing_mult: 0.0,
        trade_shorts: false,
    }
}

fn make_bars(count: usize) -> Vec<Bar> {
    let base_time = 1640995200000i64;
    (0..count).map(|i| Bar {
        timestamp: base_time + (i as i64 * 60000),
        open: 100.0 + i as f64 * 0.5,
        high: 100.0 + i as f64 * 0.5 + 1.0,
        low: 100.0 + i as f64 * 0.5 - 0.5,
        close: 100.0 + i as f64 * 0.5 + 0.3,
        volume: 1000.0,
    }).collect()
}

fn make_ctx(bars: Vec<Bar>) -> TickContext {
    TickContext {
        order_book: OrderBook {
            symbol: "BTC-USDT".to_string(),
            bids: vec![(109.0, 1.0)],
            asks: vec![(111.0, 1.0)],
            timestamp: 1640995200000,
        },
        recent_bars: bars,
        balances: std::collections::HashMap::new(),
        open_orders: vec![],
        regime: None,
        regime_confidence: 0.0,
        timestamp: 1640995200000,
        capital: None,
    }
}

#[tokio::test]
async fn test_trend_does_not_reprocess_old_bars() {
    let config = make_config();
    let mut strategy = TrendStrategy::new("BTC-USDT", &config, TelegramBot::disabled());

    // First tick: feed 40 bars (should warm up ADX which needs 28)
    let bars_40 = make_bars(40);
    let ctx1 = make_ctx(bars_40.clone());
    strategy.on_tick(&ctx1).await.unwrap();

    let status1 = strategy.status();
    assert!(
        !status1.details.contains("warming up"),
        "After 40 bars, indicators should be ready. Got: {}",
        status1.details
    );

    // Second tick: same 40 bars (no new bars) — indicators should be unchanged
    let ctx2 = make_ctx(bars_40.clone());
    strategy.on_tick(&ctx2).await.unwrap();

    let status2 = strategy.status();
    assert!(
        !status2.details.contains("warming up"),
        "After re-tick with same bars, indicators should still be ready. Got: {}",
        status2.details
    );
}

#[tokio::test]
async fn test_trend_adx_valid_after_incremental_feed() {
    let config = make_config();
    let mut strategy = TrendStrategy::new("BTC-USDT", &config, TelegramBot::disabled());

    // Feed 50 bars incrementally (simulating live bar-by-bar arrivals)
    for batch_end in 1..=50 {
        let bars = make_bars(batch_end);
        let ctx = make_ctx(bars);
        strategy.on_tick(&ctx).await.unwrap();
    }

    let status = strategy.status();
    // ADX should be initialized and show a non-zero value for trending data
    assert!(
        status.details.contains("ADX="),
        "Should show ADX after 50 bars. Got: {}",
        status.details
    );
    // ADX should NOT be 0.0 (safety-net value) after 50 bars of trending data
    assert!(
        !status.details.contains("ADX=0.0"),
        "ADX should not be 0.0 after 50 bars of trending data. Got: {}",
        status.details
    );
}
