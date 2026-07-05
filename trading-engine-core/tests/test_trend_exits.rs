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
        perp_mark_source: None,
        funding_accrual: false,
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
        replay: false,
        timestamp: 0,
        capital: None,
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
    // Mimic production: on_tick sets pending_entry before the entry fill arrives,
    // so on_fill knows this Buy is an opening fill (not a closing one).
    strategy.pending_entry = Some(OrderSide::Buy);
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
    let sl = strategy.calculate_stop_loss(50000.0, OrderSide::Buy);
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

/// Test 2: Breakeven promotion at +1R (replaces old TP1 test — no more TPs)
/// The new exit design promotes stop_loss to entry when price reaches +1R,
/// instead of closing 33% at a TP level. Position stays open (let winners run).
#[tokio::test]
async fn test_breakeven_promotion_at_1r() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("BTCUSDT", &config, telegram);
    warmup(&mut strategy, 50000.0);
    enter_position(&mut strategy, 50000.0, 0.1).await;

    let sl = strategy.calculate_stop_loss(50000.0, OrderSide::Buy);
    let risk = 50000.0 - sl; // entry - stop = per-unit risk

    // Tick at +1R (the breakeven threshold)
    let mut bars = Vec::new();
    let ctx = make_tick(50000.0 + risk, &mut bars);
    let orders = strategy.on_tick(&ctx).await.unwrap();

    // No sell order at +1R (the new design lets winners run — no TP to close)
    let sells: Vec<_> = orders.iter().filter(|o| o.side == OrderSide::Sell).collect();
    assert!(sells.is_empty(), "No sell at +1R (no TPs): got {:?}", sells);

    // Position remains open with stop promoted to breakeven (≈ entry)
    let pos = strategy.position().expect("position stays open at +1R");
    assert!(
        (pos.stop_loss - 50000.0).abs() < 50.0,
        "stop promoted to ≈ entry (50000): got {}",
        pos.stop_loss
    );
}

/// Test 3: Trailing exit closes full position (replaces old all-TPs test)
/// Push price up (ratchet trail above entry), then reverse below trail → exit.
#[tokio::test]
async fn test_trailing_exit_closes_full_position() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("BTCUSDT", &config, telegram);
    warmup(&mut strategy, 50000.0);
    enter_position(&mut strategy, 50000.0, 0.1).await;

    let mut bars = Vec::new();

    // 1) Push price UP — ratchets the trailing stop above entry.
    let _ = strategy.on_tick(&make_tick(52000.0, &mut bars)).await.unwrap();
    assert!(strategy.position().is_some(), "no exit on up move");

    let trail = strategy.position().unwrap().trailing_stop.expect("trail set after up move");
    assert!(trail > 50000.0, "trail ratcheted above entry: got {}", trail);

    // 2) Reverse price DOWN well below the trail → trailing exit fires.
    let orders = strategy.on_tick(&make_tick(49000.0, &mut bars)).await.unwrap();
    assert!(strategy.position().is_none(), "position closed on reversal");

    let sell = orders.iter().find(|o| o.side == OrderSide::Sell && o.reduce_only);
    assert!(sell.is_some(), "reduce-only sell exit order produced: {:?}", orders);
    assert!(
        (sell.unwrap().quantity - 0.1).abs() < 0.001,
        "full remaining qty exited"
    );

    // 3) No more exit orders on subsequent tick (position is gone).
    let orders_after = strategy.on_tick(&make_tick(49000.0, &mut bars)).await.unwrap();
    let exit_orders: Vec<_> = orders_after.iter().filter(|o| o.side == OrderSide::Sell).collect();
    assert!(exit_orders.is_empty(), "no sells after close: {:?}", exit_orders);
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

/// Test 6: A position restored from disk must NOT be liquidated by a catch-up
/// burst of TP/exit orders on the first live tick. TPs already below the
/// current price are reconciled as filled; the bot resumes managing the position.
#[tokio::test]
async fn test_restored_position_skips_catchup_exit_burst() {
    let config = default_trend_config();
    let pair = "RESTORE-USDT"; // unique pair → isolated state file

    // 1. Enter + persist a position (on_fill writes data/RESTORE_USDT_trend_position.json).
    let tg1 = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut s1 = TrendStrategy::new(pair, &config, tg1);
    warmup(&mut s1, 50000.0);
    enter_position(&mut s1, 50000.0, 0.1).await;
    drop(s1);

    // 2. Fresh instance loads it (simulates a restart).
    let tg2 = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut s2 = TrendStrategy::new(pair, &config, tg2);
    assert!(s2.position().is_some(), "position restored from disk");
    warmup(&mut s2, 50000.0);

    // 3. Tick at a price well above ALL TP levels.
    let sl = s2.calculate_stop_loss(50000.0, OrderSide::Buy);
    let tp_levels = TrendPosition::calculate_tp_levels(50000.0, sl, config.risk_reward_ratio, 0.10, OrderSide::Buy);
    let high_price = tp_levels[2].price + 5000.0;
    let mut bars = Vec::new();
    let ctx = make_tick(high_price, &mut bars);
    let orders = s2.on_tick(&ctx).await.unwrap();

    // 4. No catch-up burst: no sells, position survives, overdue TPs reconciled as filled.
    assert!(orders.is_empty(), "restored position must not fire catch-up exits, got {:?}", orders);
    assert!(s2.position().is_some(), "restored position must survive the restart tick");
    let pos = s2.position().unwrap();
    assert!(pos.tp_levels.iter().all(|tp| tp.filled), "overdue TPs reconciled as filled");

    // cleanup the isolated state file
    let _ = std::fs::remove_file(format!("data/{}_trend_position.json", pair.replace("-", "_")));
}
