use trading_engine_core::strategy::swing::SwingStrategy;
use trading_engine_core::strategy::Strategy;
use trading_engine_core::config::{SwingConfig, RunnerExitMode};
use trading_engine_core::notifications::TelegramBot;
use trading_engine_core::models::bar::Bar;

fn cfg() -> SwingConfig {
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
        risk_per_trade_pct: 1.0,
        adx_range_entry: 22.0,
        adx_trend_exit: 28.0,
        capital: 10000.0,
        max_bars_in_trade: 48,
        enabled_pairs: vec!["BTC-USDT".into()],
        step_size: None,
        tick_size: None,
    }
}

fn bars(n: usize, base: f64) -> Vec<Bar> {
    (0..n).map(|i| Bar::new(base - 5.0, base + 5.0, base - 3.0, base, 100.0, i as i64 * 60_000)).collect()
}

#[test]
fn test_swing_realized_pnl_starts_zero() {
    let s = SwingStrategy::new("BTC-USDT", &cfg(), TelegramBot::new("", ""));
    assert_eq!(s.realized_pnl(), 0.0);
}

#[test]
fn test_swing_current_capital_no_pnl() {
    let s = SwingStrategy::new("BTC-USDT", &cfg(), TelegramBot::new("", ""));
    assert_eq!(s.current_capital(), 10000.0);
}

#[test]
fn test_swing_deployed_capital_zero_when_flat() {
    let s = SwingStrategy::new("BTC-USDT", &cfg(), TelegramBot::new("", ""));
    assert!(s.deployed_capital() < 1e-9);
}

#[tokio::test]
async fn test_swing_no_entry_with_insufficient_bars() {
    let mut s = SwingStrategy::new("BTC-USDT", &cfg(), TelegramBot::new("", ""));
    // Only 5 bars — swing needs warmup (ATR, Donchian etc.)
    let recent = bars(5, 100.0);
    let ctx = trading_engine_core::strategy::TickContext {
        order_book: trading_engine_core::connector::types::OrderBook {
            symbol: "BTCUSDT".into(),
            bids: vec![(99.0, 1.0)], asks: vec![(101.0, 1.0)], timestamp: 0,
        },
        recent_bars: recent, balances: Default::default(),
        open_orders: vec![], regime: None, regime_confidence: 0.0,
        timestamp: 0, capital: None,
    };
    let orders = s.on_tick(&ctx).await.unwrap();
    assert!(orders.is_empty(), "should not enter with insufficient bars");
}
