use trading_engine_core::models::order::OrderSide;
use trading_engine_core::strategy::swing_journal::SwingJournal;

#[test]
fn test_swing_journal_logging_and_persistence() {
    let path = std::env::temp_dir().join("test_swing_journal.db");
    let _ = std::fs::remove_file(&path); // clean up prior

    let journal = SwingJournal::open(path.to_str().unwrap()).unwrap();

    journal.log_trade(
        "BTC-USDT",
        OrderSide::Buy,
        50000.0,
        51000.0,
        0.1,
        100.0,
        "OppositeBand",
        60,
        "BandOrChandelier",
    );

    assert_eq!(journal.count().unwrap(), 1, "Should have one row after logging");

    // Test persistence and migrations on restart
    drop(journal);
    
    let journal_reopened = SwingJournal::open(path.to_str().unwrap()).unwrap();
    assert_eq!(journal_reopened.count().unwrap(), 1, "Row should survive reopen");
}
