use trading_engine_core::config::{ClassifierCfg, MeanReversionConfig};
use trading_engine_core::connector::types::{Fill, OrderBook};
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::notifications::TelegramBot;
use trading_engine_core::strategy::mean_reversion::MeanReversionStrategy;
use trading_engine_core::strategy::{Strategy, TickContext};
use std::collections::HashMap;

fn enabled_config() -> MeanReversionConfig {
    MeanReversionConfig {
        enabled: true,
        drop_thr: 0.05,
        tp_pct: 0.02,
        stop_pct: 0.04,
        regime_gate: false,
        classifier: ClassifierCfg::default(),
    }
}

fn make_strategy() -> MeanReversionStrategy {
    // Empty creds → telegram disabled, send() is a no-op (no network in tests).
    let telegram = TelegramBot::new("", "");
    MeanReversionStrategy::new("BTC-USDT", &enabled_config(), telegram)
}

/// A tick whose bid/ask straddle `price` within 50bps so `calculate_bid_depth`
/// counts the bid quantity, and mid_price() == price exactly.
fn make_tick(price: f64, bid_qty: f64) -> TickContext {
    TickContext {
        order_book: OrderBook {
            symbol: "BTCUSDT".to_string(),
            bids: vec![(price, bid_qty)],
            asks: vec![(price, bid_qty)],
            timestamp: 0,
        },
        recent_bars: vec![],
        balances: HashMap::new(),
        open_orders: vec![],
        regime: None,
        regime_confidence: 0.0,
        timestamp: 0,
        capital: None,
    }
}

fn make_fill(order_id: &str, side: OrderSide, price: f64, quantity: f64) -> Fill {
    Fill {
        fill_id: format!("fill_{}", order_id),
        order_id: order_id.to_string(),
        client_order_id: None,
        symbol: "BTCUSDT".to_string(),
        side,
        price,
        quantity,
        fee: price * quantity * 0.001,
        timestamp: 0,
    }
}

/// Regression for the instant-close showstopper.
///
/// The Layer-1 "protective backstop" was a LIMIT SELL resting ~7% below entry.
/// Because the connector has no stop-order type, a sell-limit fills whenever
/// `market >= limit` (paper.rs) — so it fired the moment the per-symbol cooldown
/// lifted and closed the position at the limit price (~-7%), before the Layer-2
/// on_tick TP/stop logic ever ran. `on_fill` must only record entry state.
#[tokio::test]
async fn on_fill_entry_does_not_place_protective_backstop() {
    let mut s = make_strategy();
    let entry = make_fill("mr_entry_1", OrderSide::Buy, 100.0, 0.5);

    let orders = s.on_fill(&entry).await.unwrap();

    assert!(
        orders.is_empty(),
        "on_fill must not emit a protective backstop sell on entry (it fills instantly). Got: {:?}",
        orders
    );
}

/// Drive a full entry -> hold -> take-profit cycle through on_tick.
///
/// Proves the position is HELD while price stays within the stop/TP band and
/// exits via the Layer-2 on_tick logic (+2% TP), not via any bogus backstop.
#[tokio::test]
async fn position_holds_then_exits_at_take_profit_via_on_tick() {
    let mut s = make_strategy();

    // Warm up the 30s history window at 100 (12 ticks, all timestamp 0).
    for _ in 0..12 {
        s.on_tick(&make_tick(100.0, 10.0)).await.unwrap();
    }
    // Flush: price drops 6% in the window -> entry triggers.
    let entry = s.on_tick(&make_tick(94.0, 10.0)).await.unwrap();
    assert_eq!(entry.len(), 1, "flush should trigger one entry buy");
    assert_eq!(entry[0].side, OrderSide::Buy);

    // Entry fills — must NOT spawn a protective backstop.
    let after_fill = s
        .on_fill(&make_fill("mr_entry_tp", OrderSide::Buy, 94.0, entry[0].quantity))
        .await
        .unwrap();
    assert!(after_fill.is_empty(), "no backstop after entry fill");

    // Price flat near entry: within the band (94*0.96=90.24 stop, 94*1.02=95.88 TP)
    // -> position must HOLD (no orders).
    let held = s.on_tick(&make_tick(94.0, 10.0)).await.unwrap();
    assert!(
        held.is_empty(),
        "position must hold while price is within the stop/TP band, got {:?}",
        held
    );

    // Price recovers to +2% over entry -> Layer-2 take-profit sells.
    let tp = s.on_tick(&make_tick(96.0, 10.0)).await.unwrap();
    assert_eq!(tp.len(), 1, "should emit exactly one TP sell");
    assert_eq!(tp[0].side, OrderSide::Sell);
}

/// The -4% Layer-2 stop must still protect the position after the backstop is gone.
#[tokio::test]
async fn position_exits_at_layer2_stop_loss() {
    let mut s = make_strategy();
    for _ in 0..12 {
        s.on_tick(&make_tick(100.0, 10.0)).await.unwrap();
    }
    let entry = s.on_tick(&make_tick(94.0, 10.0)).await.unwrap();
    assert_eq!(entry.len(), 1);
    s.on_fill(&make_fill("mr_entry_sl", OrderSide::Buy, 94.0, entry[0].quantity))
        .await
        .unwrap();

    // Price falls to -4% stop (94*0.96 = 90.24). 90.0 <= 90.24 -> stop sell.
    let stop = s.on_tick(&make_tick(90.0, 10.0)).await.unwrap();
    assert_eq!(stop.len(), 1, "should emit exactly one stop-loss sell");
    assert_eq!(stop[0].side, OrderSide::Sell);
}


#[test]
fn test_deployed_capital_zero_when_flat() {
    let strategy = make_strategy();
    assert!(strategy.deployed_capital() < 1e-9, "flat MR has no deployed capital");
}
