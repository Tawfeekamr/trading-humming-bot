use trading_engine_core::strategy::grid_journal::GridJournal;
use trading_engine_core::models::order::OrderSide;

#[test]
fn test_log_fill_inserts_row() {
    let path = std::env::temp_dir().join("test_grid_journal_log.db");
    let _ = std::fs::remove_file(&path);
    let journal = GridJournal::open(path.to_str().unwrap()).expect("open");
    journal.log_fill("DOGE-USDT", OrderSide::Buy, "buy_2", 0.1234, 1000.0, 0.12, -123.52, -123.52);
    assert_eq!(journal.count().unwrap(), 1, "one row after a single fill");
}

#[test]
fn test_migration_idempotent_on_restart() {
    let path = std::env::temp_dir().join("test_grid_journal_migrate.db");
    let _ = std::fs::remove_file(&path);
    let j1 = GridJournal::open(path.to_str().unwrap()).expect("open");
    j1.log_fill("ETH-USDT", OrderSide::Sell, "sell_0", 3000.0, 1.0, 3.0, 50.0, 50.0);
    drop(j1);
    // Re-open: migrations must not error on existing schema.
    let j2 = GridJournal::open(path.to_str().unwrap()).expect("reopen");
    assert_eq!(j2.count().unwrap(), 1, "row survives reopen");
}
