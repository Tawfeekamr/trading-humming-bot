use trading_engine_core::notifications::telegram::TelegramBot;

#[test]
fn test_telegram_formats_status_message() {
    let bot = TelegramBot::new("test_token", "test_chat_id");
    let msg = bot.format_status_message("BTCUSDT", "Active", 150.5, 5, "Grid running");
    assert!(msg.contains("BTCUSDT"));
    assert!(msg.contains("Active"));
    assert!(msg.contains("150.50"));
}

#[test]
fn test_telegram_formats_startup_message() {
    let bot = TelegramBot::new("test_token", "test_chat_id");
    let msg = bot.format_startup_message("production", "BTCUSDT, ETHUSDT", "Grid/Trend");
    assert!(msg.contains("production"));
    assert!(msg.contains("BTCUSDT, ETHUSDT"));
    assert!(msg.contains("Grid/Trend"));
}

#[test]
fn test_telegram_formats_error_message() {
    let bot = TelegramBot::new("test_token", "test_chat_id");
    let msg = bot.format_error_message("grid_engine", "Order failed: insufficient balance");
    assert!(msg.contains("grid_engine"));
    assert!(msg.contains("insufficient balance"));
}

#[test]
fn test_telegram_disabled_when_empty_config() {
    let bot = TelegramBot::new("", "");
    assert!(!bot.enabled());
}
