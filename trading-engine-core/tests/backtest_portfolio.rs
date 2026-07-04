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
fn sell_more_than_held_clamps_qty_and_credits_cash_only_for_clamped_units() {
    // Regression: a SELL that exceeds open long inventory must be clamped for
    // ALL accounting paths — including cash. Previously cash used the raw fill
    // quantity, so over-selling credited phantom money that was never deducted
    // from inventory.
    let init_cash = 10_000.0;
    let mut p = Portfolio::new(init_cash, 10_000.0);
    p.apply_fill(&fill(OrderSide::Buy, 100.0, 2.0, 0.0));    // hold 2 @ avg 100
    assert!((p.inventory_qty - 2.0).abs() < 1e-9);
    // SELL 3 @ 120 against only 2 held → qty clamps to 2.
    p.apply_fill(&fill(OrderSide::Sell, 120.0, 3.0, 0.0));
    // Inventory can never go negative.
    assert!((p.inventory_qty - 0.0).abs() < 1e-9);
    // Exactly one trade recorded, with the clamped qty (not 3).
    assert_eq!(p.trades.len(), 1);
    assert!((p.trades[0].qty - 2.0).abs() < 1e-9);
    // Realized gross = 2 * (120 - 100) = 40.0 (the third unit never existed).
    assert!((p.realized - 40.0).abs() < 1e-9);
    // Cash must reflect only 2 units sold: init - 2*100 + 2*120 = 10040.
    // Old buggy code returned init - 2*100 + 3*120 = 10160 (phantom +120).
    let expected_cash = init_cash - 2.0 * 100.0 + 2.0 * 120.0;
    assert!((p.cash - expected_cash).abs() < 1e-9);
    assert!((p.cash - 10_040.0).abs() < 1e-9);
}
