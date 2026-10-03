use std::collections::HashMap;
use trading_engine_core::connector::paper::PaperTradeConnector;
use trading_engine_core::connector::types::{OrderRequest, OrderTypeReq, TimeInForceReq};
use trading_engine_core::connector::Connector;
use trading_engine_core::engine::Engine;
use trading_engine_core::models::order::OrderSide;
use trading_engine_core::signal::types::SignalPosition;

fn seeded_connector() -> PaperTradeConnector {
    let mut bal = HashMap::new();
    bal.insert("USDT".to_string(), 100_000.0);
    PaperTradeConnector::new(bal)
}

fn spot_position(symbol: &str, amount: f64, closed: f64, entry: f64, side: &str) -> SignalPosition {
    SignalPosition {
        symbol: symbol.to_string(),
        entry_price: entry,
        amount,
        amount_closed: closed,
        side: side.to_string(),
        is_closed: false,
        ..Default::default()
    }
}

async fn balances_of(c: &PaperTradeConnector) -> HashMap<String, f64> {
    c.get_balances().await.unwrap()
}

// Root cause C (Oct 2026): signal_positions.json persists across restarts but
// the paper book's balances do not — a restart wiped the base a position had
// bought, while its exits (and pre-4efac99 double-exits) still credited USDT.
// Boot reconciliation reconstructs each open spot long into the reseeded book:
// credit `remaining` base, debit `remaining × entry_price` cash. Both halves of
// the original BUY are restored, so this cannot create USDT from nothing — at
// entry-price marks equity is exactly what it was before the restart.
#[tokio::test]
async fn reconcile_funds_remaining_long_inventory() {
    let c = seeded_connector();
    let positions = vec![spot_position("SKYUSDT", 10.0, 4.0, 20.0, "long")];
    Engine::reconcile_signal_positions(&c, &positions).await;

    let bal = balances_of(&c).await;
    assert!((bal.get("SKY").copied().unwrap_or(0.0) - 6.0).abs() < 1e-9,
        "remaining 6.0 SKY must be re-credited, got {:?}", bal.get("SKY"));
    assert!((bal.get("USDT").copied().unwrap_or(0.0) - 99_880.0).abs() < 1e-6,
        "cost 6.0 × $20 must be debited from USDT, got {:?}", bal.get("USDT"));
}

// Spot shorts are not backed by base inventory and closed positions own
// nothing — neither reconstructs into the book.
#[tokio::test]
async fn reconcile_skips_shorts_and_closed_positions() {
    let c = seeded_connector();
    let mut short = spot_position("SKYUSDT", 10.0, 0.0, 20.0, "short");
    short.is_closed = false;
    let mut closed = spot_position("ETHUSDT", 5.0, 0.0, 3_000.0, "long");
    closed.is_closed = true;
    Engine::reconcile_signal_positions(&c, &[short, closed]).await;

    let bal = balances_of(&c).await;
    assert!(!bal.contains_key("SKY"), "short must not be reconstructed: {:?}", bal);
    assert!(!bal.contains_key("ETH"), "closed position must not be reconstructed: {:?}", bal);
    assert!((bal.get("USDT").copied().unwrap_or(0.0) - 100_000.0).abs() < 1e-9);
}

// A double boot (or re-run of the same file) must not fund twice.
#[tokio::test]
async fn reconcile_is_idempotent_across_double_boot() {
    let c = seeded_connector();
    let positions = vec![spot_position("SKYUSDT", 10.0, 4.0, 20.0, "long")];
    Engine::reconcile_signal_positions(&c, &positions).await;
    Engine::reconcile_signal_positions(&c, &positions).await;

    let bal = balances_of(&c).await;
    assert!((bal.get("SKY").copied().unwrap_or(0.0) - 6.0).abs() < 1e-9);
    assert!((bal.get("USDT").copied().unwrap_or(0.0) - 99_880.0).abs() < 1e-6);
}

// If cash cannot cover the position's cost, funding it would mint value —
// skip instead; the naked-sell clamp (root cause B) still bounds its exits.
#[tokio::test]
async fn reconcile_skips_when_cash_insufficient() {
    let mut bal = HashMap::new();
    bal.insert("USDT".to_string(), 100.0);
    let c = PaperTradeConnector::new(bal);
    let positions = vec![spot_position("BTCUSDT", 1.0, 0.0, 50_000.0, "long")];
    Engine::reconcile_signal_positions(&c, &positions).await;

    let after = balances_of(&c).await;
    assert!(!after.contains_key("BTC"), "unaffordable position must not be funded");
    assert!((after.get("USDT").copied().unwrap_or(0.0) - 100.0).abs() < 1e-9);
}

// The spec invariant, end to end: after a restart + reconciliation, no exit of
// the position can credit proceeds without a matching base balance — a sell
// larger than the reconstructed inventory clamps to it.
#[tokio::test]
async fn funded_position_exit_cannot_credit_more_than_reconstructed_base() {
    let c = seeded_connector();
    let positions = vec![spot_position("SKYUSDT", 10.0, 4.0, 20.0, "long")];
    Engine::reconcile_signal_positions(&c, &positions).await;

    // Exit tries to sell 10.0 SKY (the original full amount) at a profitable price.
    c.place_order(&OrderRequest {
        symbol: "SKYUSDT".to_string(),
        side: OrderSide::Sell,
        order_type: OrderTypeReq::Limit,
        price: Some(21.0),
        quantity: 10.0,
        time_in_force: Some(TimeInForceReq::Gtc),
        client_order_id: Some("listener-exit".into()),
        reduce_only: true,
    }).await.unwrap();

    let fills = c.try_fill_at_price("SKYUSDT", 21.0).await;
    assert_eq!(fills.len(), 1);
    assert!((fills[0].quantity - 6.0).abs() < 1e-9,
        "exit fills only the 6.0 reconstructed base, got {}", fills[0].quantity);

    let bal = c.get_balances().await.unwrap();
    let sky = bal.get("SKY").copied().unwrap_or(0.0);
    assert!(sky.abs() < 1e-9, "base must not go negative, got {}", sky);
    // USDT: 99,880 + 6.0 × $21 − maker fee (6 × 21 × 10bps = 0.126)
    let usdt = bal.get("USDT").copied().unwrap_or(0.0);
    assert!((usdt - 100_005.874).abs() < 1e-6,
        "proceeds credited for the clamped 6.0 only, got {}", usdt);
}
