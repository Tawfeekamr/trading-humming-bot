use trading_engine_core::backtest::fills::FillSim;
use trading_engine_core::connector::types::{OrderRequest, OrderTypeReq};
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::models::bar::Bar;

fn mkt(side: OrderSide, qty: f64) -> OrderRequest {
    OrderRequest { symbol: "ETHUSDT".into(), side, order_type: OrderTypeReq::Market,
        price: None, quantity: qty, time_in_force: None, client_order_id: Some("c1".into()), reduce_only: false }
}

#[test]
fn market_buy_fills_at_close_plus_slippage_with_taker_fee() {
    let mut sim = FillSim::new(10.0, 10.0, 5.0); // 5 bps slippage
    let bar = Bar::new(100.0, 101.0, 99.0, 100.0, 1.0, 1000); // close 100
    let mut fills = Vec::new();
    sim.submit(vec![mkt(OrderSide::Buy, 1.0)], &bar, &mut fills);
    assert_eq!(fills.len(), 1);
    // buy => adverse => close * (1 + slip): 100 * 1.0005 = 100.05
    assert!((fills[0].price - 100.05).abs() < 1e-6);
    // fee = 1.0 * 100.05 * 10/1e4
    assert!((fills[0].fee - (1.0 * 100.05 * 10.0 / 1e4)).abs() < 1e-9);
    assert!(sim.resting_is_empty());
}

fn lim(side: OrderSide, price: f64, qty: f64) -> OrderRequest {
    OrderRequest { symbol: "ETHUSDT".into(), side, order_type: OrderTypeReq::Limit,
        price: Some(price), quantity: qty, time_in_force: None, client_order_id: Some("c2".into()), reduce_only: false }
}
fn stop(side: OrderSide, stop_price: f64, qty: f64) -> OrderRequest {
    OrderRequest { symbol: "ETHUSDT".into(), side, order_type: OrderTypeReq::StopMarket { stop_price },
        price: None, quantity: qty, time_in_force: None, client_order_id: Some("c3".into()), reduce_only: true }
}

#[test]
fn limit_buy_fills_when_next_bar_low_touches_price_at_maker_fee() {
    let mut sim = FillSim::new(10.0, 2.0, 0.0);
    let decide = Bar::new(100.0, 100.0, 100.0, 100.0, 1.0, 1000);
    let mut fills = Vec::new();
    sim.submit(vec![lim(OrderSide::Buy, 98.0, 1.0)], &decide, &mut fills);
    assert!(fills.is_empty());                 // didn't fill on placement
    // next bar: low 97 → touches 98
    let next = Bar::new(99.0, 100.0, 97.0, 99.5, 1.0, 2000);
    sim.evaluate(&next, &mut fills);
    assert_eq!(fills.len(), 1);
    assert!((fills[0].price - 98.0).abs() < 1e-9);          // resting price
    assert!((fills[0].fee - (1.0 * 98.0 * 2.0 / 1e4)).abs() < 1e-9); // maker fee
    assert!(sim.resting_is_empty());
}

#[test]
fn stop_long_exit_triggers_and_gap_down_fills_at_open() {
    let mut sim = FillSim::new(10.0, 2.0, 0.0);
    let decide = Bar::new(100.0, 100.0, 100.0, 100.0, 1.0, 1000);
    let mut fills = Vec::new();
    sim.submit(vec![stop(OrderSide::Sell, 95.0, 1.0)], &decide, &mut fills);
    // gap-down bar: opens at 90 (below stop) → fill at open 90, not 95
    let gap = Bar::new(90.0, 92.0, 89.0, 91.0, 1.0, 2000);
    sim.evaluate(&gap, &mut fills);
    assert_eq!(fills.len(), 1);
    assert!((fills[0].price - 90.0).abs() < 1e-9);
}

#[test]
fn cancel_drops_matching_resting_order() {
    let mut sim = FillSim::new(10.0, 2.0, 0.0);
    let decide = Bar::new(100.0, 100.0, 100.0, 100.0, 1.0, 1000);
    let mut fills = Vec::new();
    sim.submit(vec![lim(OrderSide::Buy, 98.0, 1.0)], &decide, &mut fills);
    sim.cancel(&["c2".into()]);
    assert!(sim.resting_is_empty());
}
