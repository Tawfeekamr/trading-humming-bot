use trading_engine_core::backtest::perp::HistoricalPerpSource;
use trading_engine_core::connector::perp_price::PerpPriceSource;
use trading_engine_core::models::bar::Bar;

#[tokio::test]
async fn mark_returns_close_at_or_before_clock() {
    let bars = vec![
        Bar::new(0.0, 0.0, 0.0, 100.0, 0.0, 1_000),   // close 100 @ t=1000ms
        Bar::new(0.0, 0.0, 0.0, 120.0, 0.0, 2_000),   // close 120 @ t=2000ms
    ];
    let src = HistoricalPerpSource::from_bars(bars, Some(0.0001));
    src.set_clock(1_500);             // between bar 1 and 2 → must use bar 1 (no lookahead)
    assert_eq!(src.mark("ETH-USDT").await, Some(100.0));
    src.set_clock(2_500);
    assert_eq!(src.mark("ETH-USDT").await, Some(120.0));
    assert_eq!(src.funding_rate("ETH-USDT").await, Some(0.0001));
}
