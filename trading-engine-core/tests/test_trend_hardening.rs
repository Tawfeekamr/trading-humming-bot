use trading_engine_core::strategy::trend::TrendStrategy;
use trading_engine_core::strategy::{Strategy, TickContext};
use trading_engine_core::config::TrendConfig;
use trading_engine_core::connector::types::{OrderBook, Fill, OrderTypeReq};
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::models::bar::Bar;
use trading_engine_core::notifications::TelegramBot;
use std::collections::HashMap;

/// Hardening regression coverage:
///   1. ADX entry gate — a pair with no real trend must NOT enter, even if the
///      weighted score clears the threshold on volume/RSI alone. This is the
///      root cause of the XRP churn (1,807 entry→signal_exit trades/day on a
///      ranging pair with ADX≈0).
///   2. Protective exits must be MARKET orders — a LIMIT stop-loss can fail to
///      fill in a fast crash (gap risk). TP exits stay limit (profit targets).

fn default_trend_config() -> TrendConfig {
    TrendConfig {
        ema_fast: 20, ema_slow: 50, ema_trend: 200, rsi_period: 14,
        rsi_min: 40.0, rsi_max: 80.0, min_signal_score: 3, confirmation_ticks: 2,
        risk_reward_ratio: 2.0, capital: 10000.0, risk_per_trade_pct: 2.0,
        max_position_pct: 25.0, trailing_stop_pct: 1.5, trailing_stop_atr_mult: 2.5,
        trailing_activation_pct: 1.5, exit_signal_threshold: 2, sl_buffer_pct: 0.2,
        adx_gate_threshold: 25.0, adx_exit_threshold: 20.0, choppiness_threshold: 38.0,
        volume_ratio_threshold: 1.2, entry_score_threshold: 5, rsi_long_max: 65.0,
        rsi_short_min: 35.0, atr_trailing_mult: 3.0, trade_shorts: false,
    }
}

fn make_bar(close: f64) -> Bar {
    Bar::new(close - 10.0, close + 10.0, close - 5.0, close, 100.0, 0)
}

fn make_tick(price: f64, bars: &mut Vec<Bar>) -> TickContext {
    bars.push(make_bar(price));
    TickContext {
        order_book: OrderBook {
            symbol: "GATEUSDT".to_string(),
            bids: vec![(price - 5.0, 1.0)],
            asks: vec![(price + 5.0, 1.0)],
            timestamp: 0,
        },
        recent_bars: bars.clone(),
        balances: HashMap::from([("USDT".to_string(), 10000.0)]),
        open_orders: vec![],
        regime: None,
        regime_confidence: 0.0,
        timestamp: 0,
    }
}

/// Strong, steady uptrend warmup → high ADX, clean direction Up, score well
/// above threshold. This is the regime where entry SHOULD fire.
fn warmup_uptrend(strategy: &mut TrendStrategy, base_price: f64) {
    for i in 0..250 {
        let price = base_price + (i as f64 * 2.0);
        strategy.update_indicators(&make_bar(price));
    }
}

/// CONTROL: with a normal adx_gate_threshold (25), a clean uptrend whose ADX
/// clears the gate MUST generate an entry. This proves the warmup produces
/// entries, so the blocked test below can't pass for the wrong reason.
#[tokio::test]
async fn test_adx_gate_allows_entry_when_trend_is_strong() {
    let config = default_trend_config(); // adx_gate_threshold = 25
    let mut strategy = TrendStrategy::new_with_journal(
        "GATEUSDT", &config, None, TelegramBot::disabled(),
    );
    strategy.on_start().await.unwrap();
    warmup_uptrend(&mut strategy, 50000.0);

    let mut bars = Vec::new();
    let ctx = make_tick(50600.0, &mut bars);
    let orders = strategy.on_tick(&ctx).await.unwrap();

    assert!(
        orders.iter().any(|o| o.side == OrderSide::Buy),
        "control: a strong uptrend (ADX>gate) must enter — if this fails the warmup is too weak"
    );
}

/// The fix: with an unreachable adx_gate_threshold (999), entry must be blocked
/// regardless of score/direction. Without the gate, this uptrend WOULD enter.
#[tokio::test]
async fn test_adx_gate_blocks_entry_when_threshold_unreachable() {
    let mut config = default_trend_config();
    config.adx_gate_threshold = 999.0; // impossible — real ADX never reaches this
    let mut strategy = TrendStrategy::new_with_journal(
        "GATEUSDT", &config, None, TelegramBot::disabled(),
    );
    strategy.on_start().await.unwrap();
    warmup_uptrend(&mut strategy, 50000.0);

    let mut bars = Vec::new();
    let ctx = make_tick(50600.0, &mut bars);
    let orders = strategy.on_tick(&ctx).await.unwrap();

    assert!(
        orders.iter().all(|o| o.side != OrderSide::Buy),
        "ADX gate=999 must block all entries even on a strong uptrend; got a Buy order"
    );
}

/// A stop-loss exit must be a MARKET order so it fills even if price gaps
/// through it. A LIMIT stop can sit unfilled in a fast crash.
#[tokio::test]
async fn test_stop_loss_exit_is_market_order() {
    let db_path = std::env::temp_dir().join("test_trend_hardening_sl.db");
    let _ = std::fs::remove_file(&db_path);
    use trading_engine_core::strategy::trend_journal::TrendJournal;
    let journal = TrendJournal::open(db_path.to_str().unwrap()).expect("journal open");

    let config = default_trend_config();
    let mut strategy = TrendStrategy::new_with_journal(
        "GATEUSDT", &config, Some(journal), TelegramBot::disabled(),
    );
    strategy.on_start().await.unwrap();
    warmup_uptrend(&mut strategy, 50000.0);

    // Open a LONG at 50000.
    let fill = Fill {
        fill_id: "f1".into(),
        order_id: "o1".into(),
        client_order_id: None,
        symbol: "GATEUSDT".into(),
        side: OrderSide::Buy,
        price: 50000.0,
        quantity: 0.1,
        fee: 0.0,
        timestamp: 1000,
    };
    strategy.on_fill(&fill).await.unwrap();

    // Drive price below the stop-loss to trigger the exit.
    let sl = strategy.calculate_stop_loss(50000.0);
    let mut bars = Vec::new();
    let ctx = make_tick(sl - 100.0, &mut bars);
    let orders = strategy.on_tick(&ctx).await.unwrap();

    let exit = orders
        .iter()
        .find(|o| o.side == OrderSide::Sell)
        .expect("expected a stop-loss sell order");
    assert!(
        matches!(exit.order_type, OrderTypeReq::Market),
        "stop-loss exit must be a Market order (guaranteed fill), got Limit"
    );
    let _ = OrderTypeReq::Market; // keep import used even if assertion form changes

    let _ = std::fs::remove_file(&db_path);
}
