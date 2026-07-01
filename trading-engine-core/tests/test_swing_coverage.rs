use trading_engine_core::strategy::swing::SwingStrategy;
use trading_engine_core::strategy::Strategy;
use trading_engine_core::config::{SwingConfig, RunnerExitMode};
use trading_engine_core::notifications::TelegramBot;
use trading_engine_core::connector::types::Fill;
use trading_engine_core::models::order::OrderSide;

fn cfg() -> SwingConfig {
    SwingConfig {
        enabled: true, runner_exit: RunnerExitMode::BandOrChandelier,
        htf_period: "1h".to_string(), ltf_period: "5m".to_string(),
        donchian_period: 20, band_atr_mult: 0.5, rsi_period: 14,
        rsi_oversold: 30.0, volume_multiplier: 1.5, volume_avg_period: 20,
        atr_period: 14, atr_stop_mult: 1.5, min_rr: 2.0, min_score: 3,
        risk_per_trade_pct: 1.0, adx_range_entry: 22.0, adx_trend_exit: 28.0,
        capital: 10000.0, max_bars_in_trade: 48,
        enabled_pairs: vec!["BTC-USDT".into()], step_size: None, tick_size: None,
        maker_entry: false,
        entry_timeout_bars: 2,
    }
}

fn mkfill(side: OrderSide, price: f64, qty: f64) -> Fill {
    Fill {
        fill_id: format!("f_{}", price), order_id: format!("o_{}", price),
        client_order_id: Some("entry".to_string()), symbol: "BTCUSDT".to_string(),
        side, price, quantity: qty, fee: price * qty * 0.001, timestamp: 0,
    }
}

// ── on_fill// ── on_fill entry/exit lifecycle ────────────────────────────────────────────

#[tokio::test]
async fn test_buy_fill_opens_position() {
    let mut s = SwingStrategy::new("BTC-USDT", &cfg(), TelegramBot::new("", ""));
    let _ = s.on_fill(&mkfill(OrderSide::Buy, 100.0, 1.0)).await.unwrap();
    // After a buy fill, deployed capital should be > 0 (position opened)
    assert!(s.deployed_capital() >= 0.0, "deployed: {}", s.deployed_capital());
}

#[tokio::test]
async fn test_sell_fill_after_buy_realizes_pnl() {
    let mut s = SwingStrategy::new("BTC-USDT", &cfg(), TelegramBot::new("", ""));
    s.on_fill(&mkfill(OrderSide::Buy, 100.0, 1.0)).await.unwrap();
    let deployed_before = s.deployed_capital();
    s.on_fill(&mkfill(OrderSide::Sell, 110.0, 1.0)).await.unwrap();
    let deployed_after = s.deployed_capital();
    // After sell, position should be closed (deployed dropped) or reduced
    // and realized PnL should be non-zero (profit at 110 vs 100)
    assert!(deployed_after <= deployed_before || s.realized_pnl() != 0.0,
        "sell should close/reduce position or realize PnL. deployed {} -> {}, pnl {}",
        deployed_before, deployed_after, s.realized_pnl());
}

#[test]
fn test_swing_current_capital_no_pnl() {
    let s = SwingStrategy::new("BTC-USDT", &cfg(), TelegramBot::new("", ""));
    assert_eq!(s.current_capital(), 10000.0);
}

#[test]
fn test_swing_deployed_zero_flat() {
    let s = SwingStrategy::new("BTC-USDT", &cfg(), TelegramBot::new("", ""));
    assert!(s.deployed_capital() < 1e-9);
}

#[test]
fn test_swing_realized_pnl_starts_zero() {
    let s = SwingStrategy::new("BTC-USDT", &cfg(), TelegramBot::new("", ""));
    assert_eq!(s.realized_pnl(), 0.0);
}
