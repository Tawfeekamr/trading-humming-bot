use trading_engine_core::backtest::bars::parse_kline_csv;

#[test]
fn parses_binance_kline_csv_rows_into_bars() {
    // Binance kline CSV (no header): open_time, open, high, low, close, volume,
    // close_time, quote_vol, count, taker_buy_vol, taker_buy_quote_vol, ignore
    let csv = "1717200000000,100.5,101.0,99.8,100.8,1200.0,1717203599999,5000.0,50,600.0,3000.0,ignore\n\
               1717203600000,100.8,102.0,100.7,101.5,900.0,1717207199999,4000.0,40,500.0,2500.0,ignore\n";
    let bars = parse_kline_csv(csv.as_bytes()).unwrap();
    assert_eq!(bars.len(), 2);
    assert_eq!(bars[0].open, 100.5);
    assert_eq!(bars[0].high, 101.0);
    assert_eq!(bars[0].close, 100.8);
    assert_eq!(bars[0].timestamp, 1_717_200_000_000); // ms
    assert!(bars[0].volume > 0.0);
    // sorted ascending by time
    assert!(bars[1].timestamp > bars[0].timestamp);
}
