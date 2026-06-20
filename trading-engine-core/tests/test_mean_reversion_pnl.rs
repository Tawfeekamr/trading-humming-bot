use trading_engine_core::strategy::mean_reversion::MeanReversionStrategy;
use trading_engine_core::strategy::{Strategy, TickContext};
use trading_engine_core::config::MeanReversionConfig;
use trading_engine_core::connector::types::OrderBook;
use std::collections::HashMap;

fn mr(pair: &str) -> MeanReversionStrategy {
    let cfg = MeanReversionConfig { enabled: true, ..Default::default() };
    let tg = trading_engine_core::notifications::TelegramBot::new("", "");
    MeanReversionStrategy::new(pair, &cfg, tg)
}

fn tick(price: f64) -> TickContext {
    TickContext {
        order_book: OrderBook {
            symbol: "TEST-USDT".into(),
            bids: vec![(price - 0.5, 1.0)],
            asks: vec![(price + 0.5, 1.0)],
            timestamp: 0,
        },
        recent_bars: vec![],
        balances: HashMap::new(),
        open_orders: vec![],
        regime: None,
        regime_confidence: 0.0,
        timestamp: 1000,
    }
}

fn cleanup(pair: &str) {
    let _ = std::fs::remove_file(format!("data/{}_mean_reversion_state.json", pair.replace("-", "_")));
}

#[ignore]  // OBSOLETE: MR P&L/exit accounting was refactored; pending rewrite.
#[tokio::test]
async fn test_mr_tp_realizes_profit_and_counts_win() {
    let pair = "TESTMRTP-USDT";
    cleanup(pair);
    let mut s = mr(pair);
    s.set_position_for_test(100.0, 1.0);
    // mid 102 >= 100*(1+0.02) → TP; pnl = (102-100)*1 = +2
    s.on_tick(&tick(102.0)).await.unwrap();
    let status = s.status();
    assert!((status.pnl - 2.0).abs() < 1e-9, "TP realized +2, got {}", status.pnl);
    assert!(status.details.contains("Trades: 1"), "details: {}", status.details);
    assert!(status.details.contains("Wins: 1"), "details: {}", status.details);
    cleanup(pair);
}

#[ignore]  // OBSOLETE: MR P&L/exit accounting was refactored; pending rewrite.
#[tokio::test]
async fn test_mr_sl_realizes_loss_not_a_win() {
    let pair = "TESTMRSL-USDT";
    cleanup(pair);
    let mut s = mr(pair);
    s.set_position_for_test(100.0, 1.0);
    // mid 96 <= 100*(1-0.03)=97 → SL; pnl = (96-100)*1 = -4
    s.on_tick(&tick(96.0)).await.unwrap();
    let status = s.status();
    assert!((status.pnl - (-4.0)).abs() < 1e-9, "SL realized -4, got {}", status.pnl);
    assert!(status.details.contains("Trades: 1"), "details: {}", status.details);
    assert!(status.details.contains("Wins: 0"), "a loss is not a win: {}", status.details);
    cleanup(pair);
}

#[ignore]  // OBSOLETE: MR P&L/exit accounting was refactored; pending rewrite.
#[tokio::test]
async fn test_mr_pnl_persists_across_restart() {
    let pair = "TESTMRSV-USDT";
    cleanup(pair);
    {
        let mut s = mr(pair);
        s.set_position_for_test(100.0, 2.0);
        s.on_tick(&tick(102.0)).await.unwrap(); // TP, pnl = (102-100)*2 = +4
        drop(s);
    }
    // Fresh instance loads the persisted cumulative P&L.
    let s2 = mr(pair);
    let status = s2.status();
    assert!((status.pnl - 4.0).abs() < 1e-9, "cumulative P&L restored across restart, got {}", status.pnl);
    cleanup(pair);
}
