use trading_engine_core::backtest::portfolio::Portfolio;
use trading_engine_core::connector::types::Fill;
use trading_engine_core::models::order::OrderSide;

fn fill(side: OrderSide, price: f64, qty: f64, fee: f64) -> Fill {
    Fill { fill_id: "f".into(), order_id: "o".into(), client_order_id: Some("c".into()),
        symbol: "ETHUSDT".into(), side, price, quantity: qty, fee, timestamp: 0 }
}

#[test]
fn buys_accumulate_then_sell_realizes_vs_average_cost() {
    let mut p = Portfolio::new(10_000.0, 10_000.0);
    p.apply_fill(&fill(OrderSide::Buy, 100.0, 2.0, 0.2));   // cost 200 + fee 0.2
    assert_eq!(p.trades.len(), 0);                           // buy doesn't realize
    assert!((p.inventory_qty - 2.0).abs() < 1e-9);
    p.apply_fill(&fill(OrderSide::Buy, 110.0, 2.0, 0.2));   // avg cost now 105
    p.apply_fill(&fill(OrderSide::Sell, 120.0, 2.0, 0.2));  // realize vs 105
    assert_eq!(p.trades.len(), 1);
    // net trade pnl = 2 * (120 - 105) - 0.2 = 29.8
    assert!((p.trades[0].pnl - 29.8).abs() < 1e-6);
    // gross realized = 2 * (120 - 105) = 30.0 (fee booked to cash, not realized)
    assert!((p.realized - 30.0).abs() < 1e-6);
    // Hardcoded cash check: init 10000 - 2*100 - 0.2 - 2*110 - 0.2 + 2*120 - 0.2 = 9819.4
    assert!((p.cash - 9_819.4).abs() < 1e-6);
    // equity at mark 130 = cash + 2*130 = 9819.4 + 260 = 10079.4
    assert!((p.equity(130.0) - 10_079.4).abs() < 1e-6);
    // End-state: 2 units remain after the single SELL (2 bought+sold, 2 still held).
    assert!((p.inventory_qty - 2.0).abs() < 1e-9);
}

#[test]
fn sell_more_than_held_flips_to_short() {
    // Phase 2 signed-inventory semantics: a SELL that exceeds open long
    // inventory FLIPS the position to short instead of clamping. The close
    // portion realizes vs the long avg; the over-sold leftover opens a short
    // at the fill price.
    let mut p = Portfolio::new(10_000.0, 10_000.0);
    p.apply_fill(&fill(OrderSide::Buy, 100.0, 2.0, 0.0));   // long 2 @100
    p.apply_fill(&fill(OrderSide::Sell, 120.0, 3.0, 0.0));  // close 2 long, open 1 short @120
    assert!((p.realized - 40.0).abs() < 1e-6);              // long realized (120-100)*2
    assert!((p.inventory_qty - (-1.0)).abs() < 1e-9);       // flipped to short 1
    assert!((p.cash - 10_160.0).abs() < 1e-6);              // 10000 - 200 + 360
    assert_eq!(p.trades.len(), 1);
    assert!((p.trades[0].pnl - 40.0).abs() < 1e-6);
}

#[test]
fn short_open_extend_and_close_realizes_vs_short_avg() {
    let mut p = Portfolio::new(10_000.0, 10_000.0);
    p.apply_fill(&fill(OrderSide::Sell, 100.0, 2.0, 0.0));   // open short @100
    assert!((p.inventory_qty - (-2.0)).abs() < 1e-9);
    assert!((p.cash - 10_200.0).abs() < 1e-6);              // received 2*100
    p.apply_fill(&fill(OrderSide::Sell, 110.0, 2.0, 0.0));   // extend short → avg 105
    p.apply_fill(&fill(OrderSide::Buy, 90.0, 2.0, 0.0));     // close 2 @90
    // short realized = (avg 105 - exit 90) * 2 = 30
    assert!((p.realized - 30.0).abs() < 1e-6);
    assert_eq!(p.trades.len(), 1);
    assert!((p.trades[0].pnl - 30.0).abs() < 1e-6);          // fee 0 → net == gross
    assert!((p.inventory_qty - (-2.0)).abs() < 1e-9);        // 2 still short
}

#[test]
fn flip_long_to_short_realizes_long_then_opens_short() {
    let mut p = Portfolio::new(10_000.0, 10_000.0);
    p.apply_fill(&fill(OrderSide::Buy, 100.0, 2.0, 0.0));    // long 2 @100
    p.apply_fill(&fill(OrderSide::Sell, 120.0, 4.0, 0.0));   // close 2 long @120, open 2 short @120
    // long realized = (120-100)*2 = 40
    assert!((p.realized - 40.0).abs() < 1e-6);
    assert!((p.inventory_qty - (-2.0)).abs() < 1e-9);        // now short 2
    // equity at mark 110: cash + (-2)*110. Cash = 10000 -200 (buy) +480 (sell 4@120) = 10280
    assert!((p.cash - 10_280.0).abs() < 1e-6);
    assert!((p.equity(110.0) - (10_280.0 - 220.0)).abs() < 1e-6);
}

#[test]
fn over_buy_vs_short_clamps_to_zero_no_phantom() {
    let mut p = Portfolio::new(10_000.0, 10_000.0);
    p.apply_fill(&fill(OrderSide::Sell, 100.0, 2.0, 0.0));   // short 2
    p.apply_fill(&fill(OrderSide::Buy, 90.0, 5.0, 0.0));     // buy 5 vs short 2 → close 2 only
    assert!((p.inventory_qty - 3.0).abs() < 1e-9);           // flips to long 3
    // realized short = (100-90)*2 = 20
    assert!((p.realized - 20.0).abs() < 1e-6);
    assert_eq!(p.trades.len(), 1);
    assert!((p.trades[0].pnl - 20.0).abs() < 1e-6);
}
