use trading_engine_core::strategy::trend::TrendStrategy;
use trading_engine_core::strategy::trend_journal::TrendJournal;
use trading_engine_core::strategy::{Strategy, TickContext};
use trading_engine_core::config::TrendConfig;
use trading_engine_core::connector::types::{OrderBook, Fill};
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::models::bar::Bar;
use trading_engine_core::notifications::TelegramBot;
use std::collections::HashMap;

/// Regression coverage for the "Total P&L: $0.00 after restart" bug.
///
/// Root cause: trend `realized_pnl` was persisted inside the position file, and
/// `save_position()` DELETES that file when the position closes. So a restart
/// while flat reset cumulative realized P&L to $0, and `status()` returned 0.0
/// whenever flat — hiding the day's closed-trade result from `/trend`.
///
/// Fix: the journal is the single source of truth. On startup (`on_start`) the
/// strategy reconstitutes `realized_pnl` as `SUM(pnl)` filtered by pair, and
/// `status()` surfaces that value (not 0.0) when flat.

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
            symbol: "TESTUSDT".to_string(),
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

fn warmup(strategy: &mut TrendStrategy, base_price: f64) {
    for i in 0..250 {
        let price = base_price + (i as f64 * 0.5);
        strategy.update_indicators(&make_bar(price));
    }
}

/// On startup, realized P&L must be reconstituted from the journal as the
/// pair-filtered SUM of every closed trade. This is what makes the engine
/// remember its cumulative result across a restart-while-flat instead of
/// resetting to $0. A different pair's trades must NOT leak into the sum.
#[tokio::test]
async fn test_realized_pnl_seeded_from_journal_on_startup() {
    let db_path = std::env::temp_dir().join("test_trend_realized_seed.db");
    let _ = std::fs::remove_file(&db_path);

    // Seed the journal with two closed TESTUSDT trades (+50, -100) and one
    // OTHERUSDT trade (+10) that must be excluded from the TESTUSDT sum.
    {
        let journal = TrendJournal::open(db_path.to_str().unwrap()).expect("journal open");
        journal.log_trade(
            "TESTUSDT", OrderSide::Buy, 100.0, 105.0, 1.0, 50.0, 98.0, 110.0, "tp1", 10,
        );
        journal.log_trade(
            "TESTUSDT", OrderSide::Buy, 100.0, 90.0, 1.0, -100.0, 98.0, 110.0, "stop_loss", 20,
        );
        journal.log_trade(
            "OTHERUSDT", OrderSide::Buy, 50.0, 60.0, 1.0, 10.0, 48.0, 70.0, "tp1", 5,
        );
    }

    // Simulate a restart: a fresh strategy bound to the same journal. There is
    // no open position, so the position file does not exist — the only record
    // of prior P&L is the journal.
    let config = default_trend_config();
    let mut strategy = TrendStrategy::new_with_journal(
        "TESTUSDT",
        &config,
        Some(TrendJournal::open(db_path.to_str().unwrap()).expect("reopen journal")),
        TelegramBot::disabled(),
    );
    strategy.on_start().await.expect("on_start");

    let seeded = strategy.realized_pnl();
    assert!(
        (seeded - (-50.0)).abs() < 0.01,
        "realized_pnl should be the pair-filtered journal SUM = -50 (50 - 100), got {}",
        seeded
    );

    let _ = std::fs::remove_file(&db_path);
}

/// When flat (no open position), `status()` must report cumulative realized
/// P&L, not $0.00. Previously a closed stop-loss vanished from the summary
/// because the no-position branch hard-coded pnl=0.0.
#[tokio::test]
async fn test_status_reports_realized_pnl_when_flat() {
    let db_path = std::env::temp_dir().join("test_trend_realized_status.db");
    let _ = std::fs::remove_file(&db_path);
    let journal = TrendJournal::open(db_path.to_str().unwrap()).expect("journal open");

    let config = default_trend_config();
    let mut strategy = TrendStrategy::new_with_journal(
        "TESTUSDT",
        &config,
        Some(journal),
        TelegramBot::disabled(),
    );
    strategy.on_start().await.expect("on_start");

    warmup(&mut strategy, 50000.0);

    // Enter a LONG, then stop it out → realized P&L goes negative and the
    // position closes, leaving the strategy flat.
    let fill = Fill {
        fill_id: "f1".into(),
        order_id: "o1".into(),
        client_order_id: None,
        symbol: "TESTUSDT".into(),
        side: OrderSide::Buy,
        price: 50000.0,
        quantity: 0.1,
        fee: 0.0,
        timestamp: 1000,
    };
    strategy.on_fill(&fill).await.unwrap();

    let sl = strategy.calculate_stop_loss(50000.0);
    let mut bars = Vec::new();
    let ctx = make_tick(sl - 100.0, &mut bars);
    let _ = strategy.on_tick(&ctx).await.unwrap();
    assert!(
        strategy.position().is_none(),
        "position should be closed after the stop-loss"
    );

    // Flat now: status().pnl must reflect the realized loss, NOT 0.0.
    let status = strategy.status();
    assert!(
        status.pnl < 0.0,
        "flat status should report the realized stop-loss loss (<0), got pnl={}",
        status.pnl
    );

    let _ = std::fs::remove_file(&db_path);
}
