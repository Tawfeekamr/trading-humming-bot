//! Validate the swing bot's order types against Binance Spot **TESTNET**.
//!
//! This is the one gate the unit tests can't cover: they prove the request
//! *shape*, only the exchange proves *acceptance*. The script exercises the REAL
//! connector (`BinanceRest` with testnet=true) — the exact code path that runs
//! live — for every order type the rewired strategy emits:
//!
//!   - LIMIT          (baseline — must place + cancel)
//!   - LIMIT_MAKER    (TP1 scale-out: post-only sell, rests above market)
//!   - LIMIT_MAKER    (negative: a crossing maker must be REJECTED by the exchange)
//!   - STOP_LOSS      (hard stop: marketable-on-trigger sell, rests below market.
//!                     Guards the futures-only STOP_MARKET mistake.)
//!
//! `reduceOnly` correctness is implicit: our `build_spot_order_params` never sends
//! it, so if any order below came back `-1104 "Not all sent parameters were read"`
//! it would mean a stray reduceOnly (or other bad param) leaked onto the spot path.
//!
//! Usage:
//!   BINANCE_TESTNET_KEY=... BINANCE_TESTNET_SECRET=... \
//!       cargo run --bin validate_testnet_orders -- [SYMBOL]
//!   # SYMBOL defaults to BTCUSDT. Testnet account needs funds (testnet faucet).

use trading_engine_core::connector::binance_rest::BinanceRest;
use trading_engine_core::connector::types::{OrderRequest, OrderTypeReq, TimeInForceReq};
use trading_engine_core::models::order::OrderSide;

async fn place_and_cancel(rest: &BinanceRest, req: &OrderRequest, expect_reject: bool, label: &str) -> bool {
    let result = rest.place_order(req).await;
    let (ok, detail) = match result {
        Ok(resp) => (true, format!("accepted, id={}", resp.order_id)),
        Err(e) => (false, e.to_string()),
    };

    if expect_reject {
        // A rejection here is the PASS condition (e.g. crossing LIMIT_MAKER).
        if !ok && detail.to_lowercase().contains("match") {
            println!("PASS  {} — correctly rejected: {}", label, detail);
            return true;
        }
        println!("FAIL  {} — expected a rejection but got: ok={} {}", label, ok, detail);
        return false;
    }

    if ok {
        // Cancel the resting order we just placed.
        match rest.cancel_order(&req.symbol, &parse_order_id(&detail)).await {
            Ok(()) => println!("PASS  {} — placed ({}) and cancelled", label, detail),
            Err(e) => println!("PASS* {} — placed ({}) BUT cancel failed: {} (ok if it filled/expired)", label, detail, e),
        }
        true
    } else {
        println!("FAIL  {} — {}", label, detail);
        false
    }
}

fn parse_order_id(detail: &str) -> String {
    // detail looks like "accepted, id=12345"
    detail.split("id=").nth(1).unwrap_or("").to_string()
}

#[tokio::main]
async fn main() {
    let key = std::env::var("BINANCE_TESTNET_KEY")
        .expect("set BINANCE_TESTNET_KEY");
    let secret = std::env::var("BINANCE_TESTNET_SECRET")
        .expect("set BINANCE_TESTNET_SECRET");
    let args: Vec<String> = std::env::args().collect();
    let symbol = args.get(1).cloned().unwrap_or_else(|| "BTCUSDT".to_string());

    let rest = BinanceRest::new(&key, &secret, true); // testnet=true

    println!("=== Binance Spot TESTNET order-type validation ({}) ===", symbol);

    // Reference mid price, to set far-from-market resting levels.
    let ob = match rest.get_order_book(&symbol, 5).await {
        Ok(ob) => ob,
        Err(e) => {
            println!("FATAL: can't read {} order book on testnet: {}", symbol, e);
            println!("       check keys + that testnet.binance.vision is reachable.");
            return;
        }
    };
    let mid = match ob.mid_price() {
        Some(m) => m,
        None => { println!("FATAL: empty testnet order book for {}", symbol); return; }
    };
    println!("    {} mid ~ {:.2}\n", symbol, mid);

    // BTCUSDT defaults; adjust if you pass another symbol (LOT_SIZE/PRICE_FILTER).
    // Far-from-market so orders REST (don't fill) and we can cancel them.
    let qty: f64 = 0.0001;                                  // base qty
    let buy_low = (mid * 0.50 * 100.0).round() / 100.0;     // LIMIT buy, far below
    let sell_high = (mid * 1.50 * 100.0).round() / 100.0;   // LIMIT_MAKER sell, far above
    let sell_above = (mid * 1.01 * 100.0).round() / 100.0;  // LIMIT_MAKER that CROSSES (would take)
    let stop_low = (mid * 0.80 * 100.0).round() / 100.0;    // STOP_LOSS sell, below market

    let mut results = Vec::new();

    // 1) LIMIT baseline — must place + cancel. Needs quote balance.
    results.push(place_and_cancel(&rest, &OrderRequest {
        symbol: symbol.clone(), side: OrderSide::Buy,
        order_type: OrderTypeReq::Limit, price: Some(buy_low), quantity: qty,
        time_in_force: Some(TimeInForceReq::Gtc), client_order_id: Some("val_limit".into()),
        reduce_only: false,
    }, false, "LIMIT buy (baseline)").await);

    // 2) LIMIT_MAKER resting sell (the TP1 scale-out). Needs base balance.
    results.push(place_and_cancel(&rest, &OrderRequest {
        symbol: symbol.clone(), side: OrderSide::Sell,
        order_type: OrderTypeReq::LimitMaker, price: Some(sell_high), quantity: qty,
        time_in_force: None, client_order_id: Some("val_tpmaker".into()),
        reduce_only: true,
    }, false, "LIMIT_MAKER sell (TP1, rests)").await);

    // 3) LIMIT_MAKER that would cross — exchange MUST reject (post-only semantics).
    results.push(place_and_cancel(&rest, &OrderRequest {
        symbol: symbol.clone(), side: OrderSide::Sell,
        order_type: OrderTypeReq::LimitMaker, price: Some(sell_above), quantity: qty,
        time_in_force: None, client_order_id: Some("val_maker_cross".into()),
        reduce_only: true,
    }, true, "LIMIT_MAKER crossing (expect REJECT)").await);

    // 4) STOP_LOSS resting sell (the hard stop). The critical spot-vs-futures check.
    results.push(place_and_cancel(&rest, &OrderRequest {
        symbol: symbol.clone(), side: OrderSide::Sell,
        order_type: OrderTypeReq::StopMarket { stop_price: stop_low },
        price: None, quantity: qty, time_in_force: None,
        client_order_id: Some("val_stoploss".into()), reduce_only: true,
    }, false, "STOP_LOSS sell (hard stop, rests)").await);

    println!("\n=== summary: {}/{} passed ===", results.iter().filter(|&&r| r).count(), results.len());
    if results.iter().all(|&r| r) {
        println!("ALL PASS — spot accepts LIMIT_MAKER + STOP_LOSS, post-only rejects crossings,");
        println!("           and no -1104 (so no stray reduceOnly). Safe to paper-deploy the rewire.");
    } else {
        println!("FAILURES present — do NOT deploy until each FAIL is understood.");
        println!("  Common causes: insufficient testnet balance (fund via testnet faucet),");
        println!("  symbol filter mismatch (adjust qty/price for your symbol), or a real param bug.");
    }
}
