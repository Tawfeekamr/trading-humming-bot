use trading_engine_core::strategy::trend::{TrendStrategy, TrendPosition};
use trading_engine_core::strategy::trend_journal::TrendJournal;
use trading_engine_core::strategy::{Strategy, TickContext};
use trading_engine_core::config::TrendConfig;
use trading_engine_core::connector::types::{OrderBook, Fill};
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::models::bar::Bar;
use trading_engine_core::notifications::TelegramBot;
use rusqlite::Connection;
use std::collections::HashMap;

fn user_version(conn: &Connection) -> i64 {
    conn.query_row("PRAGMA user_version", [], |r| r.get(0)).unwrap()
}

fn has_column(conn: &Connection, table: &str, col: &str) -> bool {
    let mut stmt = conn.prepare(&format!("PRAGMA table_info({})", table)).unwrap();
    let names: Vec<String> = stmt
        .query_map([], |r| r.get::<_, String>(1))
        .unwrap()
        .flatten()
        .collect();
    names.iter().any(|c| c == col)
}

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

fn make_bar(close: f64) -> Bar { Bar::new(close - 10.0, close + 10.0, close - 5.0, close, 100.0, 0) }

fn make_tick(price: f64, bars: &mut Vec<Bar>) -> TickContext {
    bars.push(make_bar(price));
    TickContext {
        order_book: OrderBook {
            symbol: "BTCUSDT".to_string(),
            bids: vec![(price - 5.0, 1.0)], asks: vec![(price + 5.0, 1.0)], timestamp: 0,
        },
        recent_bars: bars.clone(),
        balances: HashMap::from([("USDT".to_string(), 10000.0)]),
        open_orders: vec![], regime: None, regime_confidence: 0.0, timestamp: 0,
    }
}

fn warmup(strategy: &mut TrendStrategy, base_price: f64) {
    for i in 0..250 {
        let price = base_price + (i as f64 * 0.5);
        strategy.update_indicators(&make_bar(price));
    }
}

/// A stop-loss exit must be written to the trend journal with the correct fields.
#[tokio::test]
async fn test_stop_loss_logs_to_journal() {
    let db_path = std::env::temp_dir().join("test_trend_journal_stoploss.db");
    let _ = std::fs::remove_file(&db_path);

    let journal = TrendJournal::open(db_path.to_str().unwrap()).expect("journal open");
    let config = default_trend_config();
    let mut strategy = TrendStrategy::new_with_journal("BTCUSDT", &config, Some(journal), TelegramBot::disabled());

    warmup(&mut strategy, 50000.0);
    // Enter a LONG at 50000
    let fill = Fill {
        fill_id: "f1".into(), order_id: "o1".into(), client_order_id: None, symbol: "BTCUSDT".into(),
        side: OrderSide::Buy, price: 50000.0, quantity: 0.1, fee: 0.0, timestamp: 1000,
    };
    strategy.on_fill(&fill).await.unwrap();
    assert!(strategy.position().is_some());

    // Trigger stop-loss
    let sl = strategy.calculate_stop_loss(50000.0);
    let mut bars = Vec::new();
    let ctx = make_tick(sl - 100.0, &mut bars);
    let orders = strategy.on_tick(&ctx).await.unwrap();
    assert!(orders.iter().any(|o| o.side == OrderSide::Sell), "expected sell order");

    // ── Verify the journal captured the close ──
    let conn = Connection::open(&db_path).unwrap();
    let row: (String, String, f64, f64, f64, String) = conn
        .query_row(
            "SELECT pair, side, entry_price, amount, pnl, exit_reason
             FROM trend_trades WHERE exit_reason='stop_loss' ORDER BY id DESC LIMIT 1",
            [],
            |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?, r.get(3)?, r.get(4)?, r.get(5)?)),
        )
        .expect("expected a stop_loss row in the journal");

    assert_eq!(row.0, "BTCUSDT", "pair");
    assert_eq!(row.1, "BUY", "side");
    assert!((row.2 - 50000.0).abs() < 1.0, "entry_price ~50000, got {}", row.2);
    assert!((row.3 - 0.1).abs() < 0.001, "amount ~0.1, got {}", row.3);
    assert!(row.4 < 0.0, "stop-loss pnl must be negative, got {}", row.4);
    assert_eq!(row.5, "stop_loss");

    let _ = std::fs::remove_file(&db_path);
}

/// A TP partial exit must be logged with its tp level (tp1/tp2/tp3).
#[tokio::test]
async fn test_tp_partial_logs_to_journal() {
    let db_path = std::env::temp_dir().join("test_trend_journal_tp.db");
    let _ = std::fs::remove_file(&db_path);

    let journal = TrendJournal::open(db_path.to_str().unwrap()).expect("journal open");
    let config = default_trend_config();
    let mut strategy = TrendStrategy::new_with_journal("BTCUSDT", &config, Some(journal), TelegramBot::disabled());

    warmup(&mut strategy, 50000.0);
    let fill = Fill {
        fill_id: "f1".into(), order_id: "o1".into(), client_order_id: None, symbol: "BTCUSDT".into(),
        side: OrderSide::Buy, price: 50000.0, quantity: 0.1, fee: 0.0, timestamp: 1000,
    };
    strategy.on_fill(&fill).await.unwrap();

    // Tick at TP1 price
    let sl = strategy.calculate_stop_loss(50000.0);
    let tp_levels = TrendPosition::calculate_tp_levels(50000.0, sl, config.risk_reward_ratio, 0.10);
    let mut bars = Vec::new();
    let ctx = make_tick(tp_levels[0].price, &mut bars);
    let _ = strategy.on_tick(&ctx).await.unwrap();

    let conn = Connection::open(&db_path).unwrap();
    let count: i64 = conn
        .query_row(
            "SELECT COUNT(*) FROM trend_trades WHERE exit_reason='tp1'",
            [], |r| r.get(0),
        )
        .unwrap();
    assert!(count >= 1, "expected at least one tp1 journal row, got {}", count);

    let _ = std::fs::remove_file(&db_path);
}

/// Re-opening the same DB (a restart) must not re-run migrations or error.
/// This is the core guarantee rusqlite_migration gives us via user_version.
#[test]
fn test_migration_idempotent_on_restart() {
    let db_path = std::env::temp_dir().join("test_trend_journal_restart.db");
    let _ = std::fs::remove_file(&db_path);

    // First boot: migrations apply.
    let _ = TrendJournal::open(db_path.to_str().unwrap()).expect("first open");
    {
        let conn = Connection::open(&db_path).unwrap();
        assert_eq!(user_version(&conn), 2, "user_version should be 2 after migration");
        assert!(has_column(&conn, "trend_trades", "pair"), "pair column must exist");
    }

    // Restart: opening again must succeed and NOT re-run the ALTER (no
    // "duplicate column name" error). user_version stays 2.
    let _ = TrendJournal::open(db_path.to_str().unwrap()).expect("reopen on restart");
    {
        let conn = Connection::open(&db_path).unwrap();
        assert_eq!(user_version(&conn), 2, "user_version must not regress on restart");
    }

    let _ = std::fs::remove_file(&db_path);
}

/// A legacy table that predates the `pair` column (the EC2 situation) must be
/// migrated in place — pair added, no data loss, no error.
#[test]
fn test_migration_adds_pair_to_legacy_table() {
    let db_path = std::env::temp_dir().join("test_trend_journal_legacy.db");
    let _ = std::fs::remove_file(&db_path);

    // Simulate the legacy Python engine's table (no pair column) + a real row.
    {
        let conn = Connection::open(&db_path).unwrap();
        conn.execute_batch(
            "CREATE TABLE trend_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
                side TEXT NOT NULL, entry_price REAL NOT NULL, exit_price REAL NOT NULL,
                amount REAL NOT NULL, fee REAL DEFAULT 0, pnl REAL NOT NULL,
                pnl_pct REAL NOT NULL, stop_loss REAL NOT NULL, take_profit REAL NOT NULL,
                exit_reason TEXT NOT NULL, signal_score INTEGER DEFAULT 0,
                duration_minutes INTEGER DEFAULT 0
            );
            INSERT INTO trend_trades (timestamp, side, entry_price, exit_price, amount,
                fee, pnl, pnl_pct, stop_loss, take_profit, exit_reason)
            VALUES ('2026-05-30', 'SELL', 694.0, 685.475, 1.0, 0, 12.35, 1.26, 0, 0, 'force_close_signal_degradation');"
        ).unwrap();
        assert_eq!(user_version(&conn), 0);
        assert!(!has_column(&conn, "trend_trades", "pair"));
    }

    // Opening via TrendJournal runs the migration: v1 no-ops (table exists),
    // v2 adds pair. Legacy row is preserved.
    let _ = TrendJournal::open(db_path.to_str().unwrap()).expect("migrate legacy");

    let conn = Connection::open(&db_path).unwrap();
    assert_eq!(user_version(&conn), 2, "legacy DB migrated to v2");
    assert!(has_column(&conn, "trend_trades", "pair"), "pair added to legacy table");
    let (rows, pair_val): (i64, String) = conn
        .query_row("SELECT COUNT(*), MAX(pair) FROM trend_trades", [], |r| {
            Ok((r.get(0)?, r.get(1)?))
        })
        .unwrap();
    assert_eq!(rows, 1, "legacy row preserved through migration");
    assert_eq!(pair_val, "", "legacy rows backfilled with default pair ''");

    let _ = std::fs::remove_file(&db_path);
}

/// A zero/missing RR must NOT place TP3 at the entry price. The guard falls
/// back to 2:1 so the position always has a real target above entry.
#[test]
fn test_tp3_guard_when_rr_zero() {
    let tps = TrendPosition::calculate_tp_levels(100.0, 98.0, 0.0, 0.10);
    // With the guard, RR=0 → 2.0, so TP3 = 100 + (2*2) = 104, NOT 100 (entry).
    assert!(tps[2].price > 100.0, "TP3 must be above entry even with RR=0, got {}", tps[2].price);
    assert!((tps[2].price - 104.0).abs() < 0.001, "TP3 should be 104 (2:1), got {}", tps[2].price);
    // TP1/TP2 unaffected and ascending.
    assert!(tps[0].price > 100.0 && tps[1].price > tps[0].price);
}
