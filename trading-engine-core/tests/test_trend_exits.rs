use trading_engine_core::strategy::trend::{TrendStrategy, TrendPosition};
use trading_engine_core::strategy::{Strategy, TickContext};
use trading_engine_core::config::TrendConfig;
use trading_engine_core::connector::types::{OrderBook, Fill};
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::models::bar::Bar;
use std::collections::HashMap;

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

/// Creates a tick context with an incrementally growing bar buffer,
/// matching the live engine behavior where bars accumulate over time.
fn make_tick(price: f64, bars: &mut Vec<Bar>) -> TickContext {
    bars.push(make_bar(price));
    TickContext {
        order_book: OrderBook {
            symbol: "BTCUSDT".to_string(),
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

fn make_fill(side: OrderSide, price: f64, quantity: f64) -> Fill {
    Fill {
        fill_id: format!("fill_{}", price),
        order_id: format!("order_{}", price),
        client_order_id: None,
        symbol: "BTCUSDT".to_string(),
        side,
        price,
        quantity,
        fee: price * quantity * 0.001,
        timestamp: 0,
    }
}

fn warmup(strategy: &mut TrendStrategy, base_price: f64) {
    for i in 0..250 {
        let price = base_price + (i as f64 * 0.5);
        strategy.update_indicators(&make_bar(price));
    }
}

async fn enter_position(strategy: &mut TrendStrategy, price: f64, qty: f64) {
    let fill = make_fill(OrderSide::Buy, price, qty);
    strategy.on_fill(&fill).await.unwrap();
}

/// Test 1: Stop-loss triggers exit order
#[tokio::test]
async fn test_stop_loss_triggers_exit() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("BTCUSDT", &config, telegram);
    warmup(&mut strategy, 50000.0);

    // Enter a position
    enter_position(&mut strategy, 50000.0, 0.1).await;
    assert!(strategy.position().is_some(), "Position should exist after entry");

    // Get stop loss level
    let sl = strategy.calculate_stop_loss(50000.0);
    assert!(sl < 50000.0, "Stop loss should be below entry, got {}", sl);

    // Tick at price below stop loss
    let mut bars = Vec::new();
    let ctx = make_tick(sl - 100.0, &mut bars);
    let orders = strategy.on_tick(&ctx).await.unwrap();

    // Should generate a sell order for the full quantity
    assert!(
        orders.iter().any(|o| o.side == OrderSide::Sell && (o.quantity - 0.1).abs() < 0.001),
        "Expected sell order for ~0.1, got {:?}", orders
    );

    // Position should be cleared
    assert!(strategy.position().is_none(), "Position should be cleared after stop loss exit");
}

/// Test 2: TP1 partial exit
#[tokio::test]
async fn test_tp1_partial_exit() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("BTCUSDT", &config, telegram);
    warmup(&mut strategy, 50000.0);
    enter_position(&mut strategy, 50000.0, 0.1).await;

    let sl = strategy.calculate_stop_loss(50000.0);
    let tp_levels = TrendPosition::calculate_tp_levels(50000.0, sl, config.risk_reward_ratio, 0.10);

    // Tick at TP1 price
    let mut bars = Vec::new();
    let ctx = make_tick(tp_levels[0].price, &mut bars);
    let orders = strategy.on_tick(&ctx).await.unwrap();

    let tp1_sell = orders.iter().find(|o| o.side == OrderSide::Sell);
    assert!(tp1_sell.is_some(), "Expected TP1 sell order, got {:?}", orders);

    // TP1 closes 33% of remaining
    let expected_qty = 0.1 * 0.33;
    assert!(
        (tp1_sell.unwrap().quantity - expected_qty).abs() < 0.01,
        "TP1 should close ~33%, expected {}, got {}",
        expected_qty,
        tp1_sell.unwrap().quantity
    );

    // Position should still exist (partial exit)
    assert!(strategy.position().is_some(), "Position should remain after TP1 partial exit");
}

/// Test 3: All TP levels fill — position closed
/// With tight ATR (gentle warmup), TP levels are close together.
/// TP2 and TP3 may trigger in the same tick or an exit signal may fire.
/// The key invariant: after ticking through all TP prices, the position is fully closed.
#[tokio::test]
async fn test_all_tp_levels_close_position() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("BTCUSDT", &config, telegram);
    warmup(&mut strategy, 50000.0);
    enter_position(&mut strategy, 50000.0, 0.1).await;

    let sl = strategy.calculate_stop_loss(50000.0);
    let tp_levels = TrendPosition::calculate_tp_levels(50000.0, sl, config.risk_reward_ratio, 0.10);

    // Tick through all TP levels — some may fire together in one tick
    let mut bars = Vec::new();
    let mut total_sell_qty = 0.0;
    for tp in &tp_levels {
        let ctx = make_tick(tp.price, &mut bars);
        let orders = strategy.on_tick(&ctx).await.unwrap();
        for o in &orders {
            if o.side == OrderSide::Sell {
                total_sell_qty += o.quantity;
            }
        }
        // If position is already closed, no need to continue
        if strategy.position().is_none() {
            break;
        }
    }

    // Position should be fully closed
    assert!(
        strategy.position().is_none(),
        "Position should be fully closed after all TP levels, but still has {:?}",
        strategy.position().map(|p| p.remaining_qty)
    );

    // Total sold should approximately equal the original quantity
    assert!(
        (total_sell_qty - 0.1).abs() < 0.005,
        "Total sell quantity ({}) should approximate entry quantity (0.1)",
        total_sell_qty
    );

    // Verify no more exit orders on subsequent tick (position is gone)
    let ctx_after = make_tick(tp_levels[2].price + 100.0, &mut bars);
    let orders_after = strategy.on_tick(&ctx_after).await.unwrap();
    let exit_orders: Vec<_> = orders_after.iter().filter(|o| o.side == OrderSide::Sell).collect();
    assert!(exit_orders.is_empty(), "No sell orders expected after position closed, got {:?}", exit_orders);
}

/// Test 4: Trailing stop (Chandelier Exit)
#[tokio::test]
async fn test_trailing_stop_chandelier_exit() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("BTCUSDT", &config, telegram);
    warmup(&mut strategy, 50000.0);
    enter_position(&mut strategy, 50000.0, 0.1).await;

    // Push price up to raise highest_since_entry and trailing stop
    // After warmup with 0.5 step, ATR ≈ (20 * 0.5) = ~10 range per bar
    // Feed rising prices to update indicators and trail
    let mut bars = Vec::new();
    for p in [50500.0, 51000.0, 51500.0, 52000.0] {
        strategy.update_indicators(&make_bar(p));
        let ctx = make_tick(p, &mut bars);
        strategy.on_tick(&ctx).await.unwrap();
    }

    // Trailing stop = highest - atr_trailing_mult * ATR
    // With default atr_trailing_mult=3.0, trail ≈ 52000 - 3.0*ATR
    // ATR after big moves should be at least 10+
    // Drop price far below trailing stop
    let drop_price = 50000.0; // well below peak at 52000
    let ctx = make_tick(drop_price, &mut bars);
    let orders = strategy.on_tick(&ctx).await.unwrap();

    assert!(
        orders.iter().any(|o| o.side == OrderSide::Sell && o.quantity > 0.0),
        "Trailing stop should trigger sell order, got {:?}", orders
    );
}

/// Test 5: Direction flip exit
#[tokio::test]
async fn test_direction_flip_exit() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("BTCUSDT", &config, telegram);
    warmup(&mut strategy, 50000.0);
    enter_position(&mut strategy, 50000.0, 0.1).await;

    // Feed sharply declining bars to force EMA fast below EMA slow (direction flip)
    for i in 0..100 {
        let price = 50000.0 - (i as f64 * 50.0);
        strategy.update_indicators(&make_bar(price));
    }

    // Tick at a very low price — direction should be Down, triggering exit for long position
    let mut bars = Vec::new();
    let ctx = make_tick(45000.0, &mut bars);
    let orders = strategy.on_tick(&ctx).await.unwrap();

    assert!(
        orders.iter().any(|o| o.side == OrderSide::Sell),
        "Direction flip should trigger sell order, got {:?}", orders
    );
}
