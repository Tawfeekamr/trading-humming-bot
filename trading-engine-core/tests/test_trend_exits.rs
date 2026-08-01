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

/// Test 2: Breakeven promotion and TP1 partial close at +1R.
#[tokio::test]
async fn test_breakeven_and_tp1_partial_at_1r() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("TP1-PARTIAL-USDT", &config, telegram);
    warmup(&mut strategy, 50000.0);
    enter_position(&mut strategy, 50000.0, 0.1).await;

    let sl = strategy.calculate_stop_loss(50000.0, OrderSide::Buy);
    let risk = 50000.0 - sl;
    let mut bars = Vec::new();
    let orders = strategy.on_tick(&make_tick(50000.0 + risk, &mut bars)).await.unwrap();
    let sell = orders.iter().find(|o| o.side == OrderSide::Sell && o.reduce_only)
        .expect("TP1 should emit a reduce-only sell at +1R");
    assert!((sell.quantity - 0.033).abs() < 1e-9, "TP1 closes 33% of initial qty");
    let remaining_after_order = strategy.position().expect("runner remains after TP1").remaining_qty;
    assert!((remaining_after_order - 0.067).abs() < 1e-9);
    // The strategy books reactive exits optimistically in on_tick; the matching
    // fill callback must reconcile the already-booked quantity exactly once.
    strategy.on_fill(&make_fill(OrderSide::Sell, sell.price.unwrap_or(50000.0), sell.quantity)).await.unwrap();
    let pos = strategy.position().expect("runner remains after TP1 fill");
    assert!((pos.remaining_qty - 0.067).abs() < 1e-9, "exit fill must not double-deduct");
    assert!((pos.stop_loss - 50000.0).abs() < 50.0, "stop promoted to breakeven");
    assert!(pos.tp_levels[0].filled);
}

/// An optimistic TP booked before restart must remain booked when its fill
/// arrives after restart; the callback must not deduct it a second time.
#[tokio::test]
async fn test_partial_exit_fill_reconciles_across_restart() {
    let config = default_trend_config();
    let pair = "RESTART-PARTIAL-USDT";
    let tg1 = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut s1 = TrendStrategy::new(pair, &config, tg1);
    warmup(&mut s1, 50000.0);
    enter_position(&mut s1, 50000.0, 0.1).await;
    let risk = 50000.0 - s1.calculate_stop_loss(50000.0, OrderSide::Buy);
    let mut bars = Vec::new();
    let orders = s1.on_tick(&make_tick(50000.0 + risk, &mut bars)).await.unwrap();
    let tp1 = orders.iter().find(|o| o.reduce_only).expect("TP1 order");
    drop(s1);

    let tg2 = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut s2 = TrendStrategy::new(pair, &config, tg2);
    assert!((s2.position().unwrap().remaining_qty - 0.067).abs() < 1e-9);
    s2.on_fill(&make_fill(OrderSide::Sell, tp1.price.unwrap_or(50000.0), tp1.quantity)).await.unwrap();
    assert!((s2.position().unwrap().remaining_qty - 0.067).abs() < 1e-9);
    let _ = std::fs::remove_file(format!("data/{}_trend_position.json", pair.replace("-", "_")));

}
/// TP2 closes 50% of the quantity remaining after TP1, leaving the runner.
#[tokio::test]
async fn test_tp2_closes_half_of_remaining_quantity() {
    let config = default_trend_config();
    let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut strategy = TrendStrategy::new("TP2-PARTIAL-USDT", &config, telegram);
    warmup(&mut strategy, 50000.0);
    enter_position(&mut strategy, 50000.0, 0.1).await;

    let sl = strategy.calculate_stop_loss(50000.0, OrderSide::Buy);
    let risk = 50000.0 - sl;
    let mut bars = Vec::new();
    let _ = strategy.on_tick(&make_tick(50000.0 + risk * 1.5, &mut bars)).await.unwrap();
    let pos = strategy.position().expect("runner remains after both targets");
    assert!((pos.remaining_qty - 0.0335).abs() < 1e-9, "TP2 closes 50% of post-TP1 qty");
    assert!(pos.tp_levels.iter().all(|tp| tp.filled));
}

/// Test 3: Chandelier trailing exits the remaining runner after both targets.
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
        (sell.unwrap().quantity - 0.0335).abs() < 0.001,
        "full remaining runner qty exited"
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

    // Trailing stop = highest - trailing_stop_atr_mult * ATR
    // With default trailing_stop_atr_mult=2.5, trail ≈ 52000 - 2.5*ATR
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
/// A trailing-only persisted position with empty tp_levels is migrated to the
/// two-target ladder, and restore reconciliation emits no catch-up exits.
#[tokio::test]
async fn test_restored_position_backfills_hybrid_targets_without_exits() {
    let config = default_trend_config();
    let pair = "RESTORE-HYBRID-USDT";

    let tg1 = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut s1 = TrendStrategy::new(pair, &config, tg1);
    warmup(&mut s1, 50000.0);
    enter_position(&mut s1, 50000.0, 0.1).await;
    drop(s1);

    let path = format!("data/{}_trend_position.json", pair.replace("-", "_"));
    let content = std::fs::read_to_string(&path).expect("persisted position");
    let mut state: serde_json::Value = serde_json::from_str(&content).unwrap();
    state["position"]["tp_levels"] = serde_json::json!([]);
    std::fs::write(&path, serde_json::to_string(&state).unwrap()).unwrap();

    let tg2 = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut s2 = TrendStrategy::new(pair, &config, tg2);
    let pos = s2.position().expect("position restored from disk");
    assert_eq!(pos.tp_levels.len(), 2, "empty persisted ladder is backfilled");
    let sl = s2.calculate_stop_loss(50000.0, OrderSide::Buy);
    let high_price = TrendPosition::calculate_tp_levels(
        50000.0, sl, config.risk_reward_ratio, 0.0, OrderSide::Buy
    )[1].price + 5000.0;
    warmup(&mut s2, 50000.0);
    let mut bars = Vec::new();
    let orders = s2.on_tick(&make_tick(high_price, &mut bars)).await.unwrap();
    assert!(orders.is_empty(), "restore must not fire catch-up exits");
    assert!(s2.position().is_some(), "restored position survives first tick");
    assert!(s2.position().unwrap().tp_levels.iter().all(|tp| tp.filled));

    let _ = std::fs::remove_file(path);
}

/// Restored legacy ladders with a TP3 are normalized to the two hybrid targets.
#[tokio::test]
async fn test_restored_three_target_ladder_discards_tp3() {
    let config = default_trend_config();
    let pair = "RESTORE-LEGACY-3TP-USDT";
    let tg1 = trading_engine_core::notifications::TelegramBot::new("", "");
    let mut s1 = TrendStrategy::new(pair, &config, tg1);
    warmup(&mut s1, 50000.0);
    enter_position(&mut s1, 50000.0, 0.1).await;
    drop(s1);

    let path = format!("data/{}_trend_position.json", pair.replace("-", "_"));
    let content = std::fs::read_to_string(&path).unwrap();
    let mut state: serde_json::Value = serde_json::from_str(&content).unwrap();
    state["position"]["tp_levels"] = serde_json::json!([
        {"price": 50020.0, "close_pct": 0.33, "filled": true},
        {"price": 50030.0, "close_pct": 0.50, "filled": false},
        {"price": 50040.0, "close_pct": 0.80, "filled": false}
    ]);
    std::fs::write(&path, serde_json::to_string(&state).unwrap()).unwrap();

    let tg2 = trading_engine_core::notifications::TelegramBot::new("", "");
    let s2 = TrendStrategy::new(pair, &config, tg2);
    let pos = s2.position().expect("legacy position restored");
    assert_eq!(pos.tp_levels.len(), 2);
    assert!(pos.tp_levels[0].filled);
    assert!(!pos.tp_levels[1].filled);
    assert_eq!(pos.tp_levels[1].close_pct, 0.50);
    let _ = std::fs::remove_file(path);
}

/// Step 1: the Chandelier trail MUST read `trailing_stop_atr_mult` (not the old
/// unread `atr_trailing_mult` default). Two configs differing ONLY in that field
/// must produce different trails — proving the knob binds. Before the fix the
/// code reads `atr_trailing_mult` (=3.0 in default_trend_config) for both, so the
/// trails are identical and `tight > loose` fails.
#[tokio::test]
async fn test_trailing_stop_binds_to_atr_mult_field() {
    async fn trail_for(mult: f64) -> f64 {
        let mut cfg = default_trend_config();
        cfg.trailing_stop_atr_mult = mult;
        let telegram = trading_engine_core::notifications::TelegramBot::new("", "");
        let mut s = TrendStrategy::new("BTCUSDT", &cfg, telegram);
        warmup(&mut s, 50000.0);
        enter_position(&mut s, 50000.0, 0.1).await;
        let mut bars = Vec::new();
        for p in [50500.0, 51000.0, 51500.0, 52000.0] {
            s.update_indicators(&make_bar(p));
            s.on_tick(&make_tick(p, &mut bars)).await.unwrap();
        }
        s.position().unwrap().trailing_stop.expect("trail set after up-move")
    }

    // smaller mult ⇒ higher (tighter) trail for a long; larger mult ⇒ lower (looser)
    let tight = trail_for(2.0).await;
    let loose = trail_for(4.0).await;
    assert!(tight > loose,
        "tighter mult must produce a higher trail for a long: tight={} loose={}", tight, loose);
    // trail = highest - mult·ATR with identical highest/ATR ⇒ gap = (4-2)·ATR ≫ 0
    assert!(tight - loose > 1.0,
        "trail gap must reflect Δmult·ATR (ATR>0 in this setup), got {}", tight - loose);
}
