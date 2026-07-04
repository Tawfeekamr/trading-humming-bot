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
    // 2 units remain @ avg 105; equity at mark 130 = cash + 2*130
    assert!((p.equity(130.0) - (p.cash + 2.0 * 130.0)).abs() < 1e-6);
}
