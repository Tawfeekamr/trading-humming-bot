//! Pre-trade order checks and the hourly book reconciliation.
//!
//! Added after the 2026-11-27 XRP incident (144 trend entries in 5 s put the
//! whole paper book into one coin). Pure functions and small state only, so
//! every rule is unit-tested here; the engine gathers the inputs and acts.

use std::collections::{HashMap, VecDeque};

use crate::connector::types::{OrderRequest, OrderTypeReq};
use crate::models::order::OrderSide;

/// Limits applied to every new (non-reduce-only) order.
#[derive(Debug, Clone, Copy)]
pub struct PreTradeLimits {
    /// A buy limit may sit at most this % above the mid (a sell, below). A limit
    /// that crosses the book by more is a stale or wrong price — e.g. the
    /// 2026-11-26 grid buys placed above market from a lagging centre.
    pub max_cross_pct: f64,
    /// Any limit price further than this % from the mid is rejected (fat finger).
    pub max_distance_pct: f64,
    /// New non-reduce orders allowed per symbol per rolling second.
    pub max_orders_per_sec: usize,
    /// Open orders one strategy may hold on one symbol (grid ladders need ~10).
    pub max_open_per_owner_symbol: usize,
}

impl Default for PreTradeLimits {
    fn default() -> Self {
        Self { max_cross_pct: 1.0, max_distance_pct: 15.0, max_orders_per_sec: 20, max_open_per_owner_symbol: 16 }
    }
}

/// "XRP-USDT" / "XRPUSDT" → "XRPUSDT".
pub fn norm_symbol(symbol: &str) -> String {
    symbol.replace('-', "").to_uppercase()
}

/// "XRP-USDT" / "XRPUSDT" → "XRP". Empty if the symbol is not USDT-quoted.
pub fn base_asset(symbol: &str) -> String {
    let n = norm_symbol(symbol);
    n.strip_suffix("USDT").unwrap_or("").to_string()
}

/// Reject a limit price that crosses the book too far or sits too far away.
/// Market orders and orders with no known mid pass (nothing to compare with).
pub fn check_price_band(req: &OrderRequest, mid: Option<f64>, lim: &PreTradeLimits) -> Result<(), String> {
    let (Some(price), Some(mid)) = (req.price, mid) else { return Ok(()) };
    if !matches!(req.order_type, OrderTypeReq::Limit | OrderTypeReq::LimitMaker) || mid <= 0.0 {
        return Ok(());
    }
    let dev_pct = (price / mid - 1.0) * 100.0;
    if dev_pct.abs() > lim.max_distance_pct {
        return Err(format!("limit {price} is {dev_pct:+.1}% from mid {mid} (max ±{}%)", lim.max_distance_pct));
    }
    let crosses = match req.side { OrderSide::Buy => dev_pct, OrderSide::Sell => -dev_pct };
    if crosses > lim.max_cross_pct {
        return Err(format!("{:?} limit {price} crosses mid {mid} by {crosses:.2}% (max {}%)",
            req.side, lim.max_cross_pct));
    }
    Ok(())
}

/// Rolling one-second count of new orders per symbol.
#[derive(Debug, Default)]
pub struct OrderRateLimiter {
    recent: HashMap<String, VecDeque<i64>>,
}

impl OrderRateLimiter {
    /// True (and records the order) if fewer than `max` orders were allowed for
    /// `symbol` in the last 1,000 ms.
    pub fn allow(&mut self, symbol: &str, now_ms: i64, max: usize) -> bool {
        let q = self.recent.entry(norm_symbol(symbol)).or_default();
        while matches!(q.front(), Some(&t) if now_ms - t >= 1_000) {
            q.pop_front();
        }
        if q.len() >= max {
            return false;
        }
        q.push_back(now_ms);
        true
    }
}

/// One reconciliation finding: a dedupe key and a human message.
#[derive(Debug, Clone, PartialEq)]
pub struct Finding {
    pub key: String,
    pub message: String,
}

/// Compare the book with what the strategies think they hold.
///
/// * `balances` — asset → quantity held in the book
/// * `mids` — base asset → mid price (streamed coins only)
/// * `tracked` — base asset → quantity the strategies (and signal positions) own
///
/// Flags: one coin above `max_share` of equity; untracked inventory (book holds
/// more than any strategy owns — the XRP orphan); phantom inventory (strategies
/// think they hold more than the book has); free capital stuck at zero.
pub fn reconcile(
    balances: &HashMap<String, f64>,
    mids: &HashMap<String, f64>,
    tracked: &HashMap<String, f64>,
    equity: f64,
    free_capital_zero_for_mins: Option<i64>,
    max_share: f64,
) -> Vec<Finding> {
    let mut out = Vec::new();
    let tolerance = (equity * 0.01).max(100.0);
    let mut assets: Vec<&String> = mids.keys().collect();
    assets.sort();
    for base in assets {
        let mid = mids[base];
        if mid <= 0.0 {
            continue;
        }
        let held = balances.get(base).copied().unwrap_or(0.0);
        let owned = tracked.get(base).copied().unwrap_or(0.0);
        if equity > 0.0 && held * mid / equity > max_share {
            out.push(Finding {
                key: format!("concentration:{base}"),
                message: format!("{base} is {:.0}% of equity (${:.0} of ${:.0}); limit {:.0}%",
                    held * mid / equity * 100.0, held * mid, equity, max_share * 100.0),
            });
        }
        let gap = (held - owned) * mid;
        if gap > tolerance {
            out.push(Finding {
                key: format!("untracked:{base}"),
                message: format!("{base}: book holds {held:.4} but strategies own {owned:.4} — ${gap:.0} untracked (orphaned position?)"),
            });
        } else if -gap > tolerance {
            out.push(Finding {
                key: format!("phantom:{base}"),
                message: format!("{base}: strategies own {owned:.4} but book holds {held:.4} — ${:.0} phantom", -gap),
            });
        }
    }
    if let Some(mins) = free_capital_zero_for_mins {
        if mins >= 60 {
            out.push(Finding {
                key: "free_capital_zero".into(),
                message: format!("free capital has been $0 for {mins} min — every strategy is starved"),
            });
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    fn limit(side: OrderSide, price: f64) -> OrderRequest {
        OrderRequest { symbol: "XRP-USDT".into(), side, order_type: OrderTypeReq::Limit, price: Some(price),
            quantity: 1.0, time_in_force: None, client_order_id: None, reduce_only: false }
    }

    #[test]
    fn symbol_helpers() {
        assert_eq!(norm_symbol("xrp-usdt"), "XRPUSDT");
        assert_eq!(base_asset("XRP-USDT"), "XRP");
        assert_eq!(base_asset("BNBUSDT"), "BNB");
        assert_eq!(base_asset("ETHBTC"), "");
    }

    #[test]
    fn price_band_rejects_crossing_and_far_limits() {
        let lim = PreTradeLimits::default();
        assert!(check_price_band(&limit(OrderSide::Buy, 100.0), Some(100.0), &lim).is_ok());
        assert!(check_price_band(&limit(OrderSide::Buy, 95.0), Some(100.0), &lim).is_ok());
        assert!(check_price_band(&limit(OrderSide::Sell, 106.0), Some(100.0), &lim).is_ok());
        // the 26 Nov grid bug: buy limits several % above market
        assert!(check_price_band(&limit(OrderSide::Buy, 103.0), Some(100.0), &lim).is_err());
        assert!(check_price_band(&limit(OrderSide::Sell, 97.0), Some(100.0), &lim).is_err());
        // fat finger / stale price far away (the ETH sell at $2,748 vs $1,649)
        assert!(check_price_band(&limit(OrderSide::Sell, 166.0), Some(100.0), &lim).is_err());
        // no mid or a market order: nothing to compare against
        assert!(check_price_band(&limit(OrderSide::Buy, 300.0), None, &lim).is_ok());
        let mut m = limit(OrderSide::Buy, 300.0);
        m.order_type = OrderTypeReq::Market;
        assert!(check_price_band(&m, Some(100.0), &lim).is_ok());
    }

    #[test]
    fn rate_limiter_caps_orders_per_second_per_symbol() {
        let mut r = OrderRateLimiter::default();
        // the incident shape: 144 orders, one every 35 ms (~5 s)
        let allowed = (0..144).filter(|i| r.allow("XRP-USDT", 1_000 + i * 35, 20)).count();
        assert!(allowed <= 6 * 20 && allowed < 144, "at most 20 per rolling second, got {allowed}");
        // other symbols are independent
        assert!(r.allow("BNBUSDT", 1_100, 20));
        // after a quiet second the window clears
        assert!(r.allow("XRPUSDT", 100_000, 20));
    }

    #[test]
    fn rate_limiter_blocks_the_xrp_burst_within_one_second() {
        let mut r = OrderRateLimiter::default();
        let first_second = (0..29).filter(|i| r.allow("XRP-USDT", i * 35, 20)).count();
        assert_eq!(first_second, 20);
    }

    fn hm(pairs: &[(&str, f64)]) -> HashMap<String, f64> {
        pairs.iter().map(|(k, v)| (k.to_string(), *v)).collect()
    }

    #[test]
    fn reconcile_flags_the_xrp_orphan_and_concentration() {
        let f = reconcile(&hm(&[("USDT", 0.0), ("XRP", 103_455.0)]), &hm(&[("XRP", 0.94), ("BNB", 600.0)]),
            &hm(&[]), 97_250.0, Some(1_100), 0.5);
        let keys: Vec<&str> = f.iter().map(|x| x.key.as_str()).collect();
        assert_eq!(keys, vec!["concentration:XRP", "untracked:XRP", "free_capital_zero"]);
    }

    #[test]
    fn reconcile_is_quiet_when_book_matches_strategies() {
        let f = reconcile(&hm(&[("USDT", 60_000.0), ("BNB", 20.0)]), &hm(&[("BNB", 600.0), ("XRP", 0.94)]),
            &hm(&[("BNB", 20.0)]), 72_000.0, None, 0.5);
        assert!(f.is_empty(), "{f:?}");
    }

    #[test]
    fn reconcile_flags_phantom_and_ignores_small_gaps() {
        let f = reconcile(&hm(&[("USDT", 90_000.0), ("XRP", 100.0)]), &hm(&[("XRP", 1.0)]),
            &hm(&[("XRP", 4_000.0)]), 90_100.0, Some(30), 0.5);
        assert_eq!(f.len(), 1);
        assert_eq!(f[0].key, "phantom:XRP");
        // a $50 dust gap is below the $100 / 1% tolerance
        let quiet = reconcile(&hm(&[("XRP", 150.0)]), &hm(&[("XRP", 1.0)]), &hm(&[("XRP", 100.0)]),
            50_000.0, None, 0.5);
        assert!(quiet.is_empty());
    }
}
