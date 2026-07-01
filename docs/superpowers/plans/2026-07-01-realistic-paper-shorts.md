# Realistic Paper Shorts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Rust trend engine's paper short trades honest (slippage on taker exits, perp pricing + funding on shorts) so the September review reflects realistic P&L — while staying paper / 1× / no real money.

**Architecture:** Three independent changes. (1) Global slippage + tiered maker/taker fees in the paper fill engine (`paper.rs`) — fixes zero-slippage taker exits for *every* engine. (2) A `PerpPriceSource` trait + Gate.io USDT-perp impl, injected into `TrendStrategy` via a `with_perp` builder; trend's open shorts are marked/triggered/exited against the perp mark instead of the spot mid. (3) Funding accrual on open shorts every 8h, journaled as a `log_unified` row. No leverage/liquidation (1×). No `Connector` trait change. Defaults preserve current behavior.

**Tech Stack:** Rust, `async-trait`, `reqwest`, `rusqlite` (existing), `serde`. Tests via `cargo test`.

**Spec:** `docs/superpowers/specs/2026-07-01-realistic-paper-shorts-design.md`

## Global Constraints

- **Paper only, 1× (no leverage).** No liquidation/margin/isolation modeling.
- **Defaults preserve current behavior:** `slippage_bps = 0`, `taker_fee_bps = maker_fee_bps = 10` (0.1%). Existing `paper.rs` tests must stay green unchanged.
- **No `Connector` trait changes.** The perp mark lives behind a new `PerpPriceSource` trait held by `TrendStrategy` only.
- **Longs and all non-trend engines are untouched** by the perp/funding changes (they never ask for a perp mark).
- **Perp feed outage must not halt trading:** on fetch failure, fall back to the spot mid for that tick and log a warning.
- **Funding sign:** positive funding rate → short pays (negative PnL); negative rate → short receives.
- **Funding logged as its own row:** `log_unified("trend", pair, Some("SELL"), None, None, Some(0.0), funding_pnl, Some("funding"), None)`.
- **All `cargo test` green** after each task. Commit per task.

---

## Task 1: Slippage + tiered maker/taker fees in the paper fill engine

Self-contained and independently shippable. This is the biggest honesty win and touches only `paper.rs`, `config.rs`, and one `main.rs` line.

**Files:**
- Modify: `trading-engine-core/src/connector/paper.rs` (engine fields, `try_fill_at_price`, builder, tests)
- Modify: `trading-engine-core/src/config.rs` (new `PaperConfig` struct + field on `AppConfig` + `default_10`)
- Modify: `trading-engine-core/src/main.rs:53-59` (wire realism into the connector build)
- Test: inline `mod tests` in `paper.rs`

**Interfaces:**
- Produces: `PaperTradeEngine::set_realism(&mut self, slippage_bps: f64, taker_fee_bps: f64, maker_fee_bps: f64)`; `PaperTradeConnector::with_realism(self, slippage_bps: f64, taker_fee_bps: f64, maker_fee_bps: f64) -> Self`; `config::PaperConfig { slippage_bps, taker_fee_bps, maker_fee_bps }`.

- [ ] **Step 1: Add realism fields + setter to `PaperTradeEngine` (`paper.rs`)**

In `PaperTradeEngine` struct (around line 37-46), add three fields after `last_fill_ms`:

```rust
    /// Adverse slippage in bps applied to TAKER fills only (Market, StopMarket).
    /// Maker limits fill at their resting price. 0 = off (preserve old behavior).
    slippage_bps: f64,
    taker_fee_bps: f64,
    maker_fee_bps: f64,
```

In `PaperTradeEngine::new` (around line 49-58), initialize them to behavior-preserving defaults:

```rust
            fill_cooldown_ms: 0,
            last_fill_ms: HashMap::new(),
            slippage_bps: 0.0,
            taker_fee_bps: 10.0, // 0.1%
            maker_fee_bps: 10.0, // 0.1%
```

Add a setter next to `set_fill_cooldown` (around line 63):

```rust
    /// Configure slippage (taker-only) and tiered fees. Defaults (0 / 10 / 10)
    /// reproduce the original flat-0.1%-fee, zero-slippage behavior.
    pub fn set_realism(&mut self, slippage_bps: f64, taker_fee_bps: f64, maker_fee_bps: f64) {
        self.slippage_bps = slippage_bps.max(0.0);
        self.taker_fee_bps = taker_fee_bps.max(0.0);
        self.maker_fee_bps = maker_fee_bps.max(0.0);
    }
```

- [ ] **Step 2: Write failing tests for slippage + tiered fees**

Add to the `mod tests` block in `paper.rs` (the `engine()` helper already seeds 1 BTC + 10_000 USDT):

```rust
    fn market_buy(qty: f64) -> OrderRequest {
        OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::Market,
            price: None,
            quantity: qty,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }
    }

    #[test]
    fn taker_buy_fills_above_mark_with_slippage() {
        let mut e = engine();
        e.set_realism(10.0, 5.0, 2.0); // 10 bps slippage
        e.place_order(&market_buy(0.1)).unwrap();
        let fills = e.try_fill_at_price("BTCUSDT", 50_000.0);
        assert_eq!(fills.len(), 1);
        // Buy slippage adverse (higher): 50000 * (1 + 10/10000) = 50050
        assert!((fills[0].price - 50_050.0).abs() < 1e-6, "got {}", fills[0].price);
    }

    #[test]
    fn taker_sell_fills_below_mark_with_slippage() {
        let mut e = engine();
        e.set_realism(10.0, 5.0, 2.0);
        e.place_order(&sell_stop(50_000.0, 0.5)).unwrap(); // StopMarket Sell
        let fills = e.try_fill_at_price("BTCUSDT", 49_900.0);
        assert_eq!(fills.len(), 1);
        // Sell slippage adverse (lower): 49900 * (1 - 10/10000) = 49850.1
        assert!((fills[0].price - 49_850.1).abs() < 1e-3, "got {}", fills[0].price);
    }

    #[test]
    fn maker_limit_unaffected_by_slippage() {
        let mut e = engine();
        e.set_realism(10.0, 5.0, 2.0);
        e.place_order(&OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::LimitMaker,
            price: Some(50_000.0),
            quantity: 0.1,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }).unwrap();
        let fills = e.try_fill_at_price("BTCUSDT", 50_000.0);
        // Maker fills at its resting limit, no slippage.
        assert!((fills[0].price - 50_000.0).abs() < 1e-9);
    }

    #[test]
    fn tiered_fees_maker_vs_taker() {
        let mut e = engine();
        e.set_realism(0.0, 5.0, 2.0); // taker 5bps, maker 2bps
        e.place_order(&market_buy(1.0)).unwrap();
        let taker_fee = e.try_fill_at_price("BTCUSDT", 50_000.0)[0].fee;
        // 50000 * 1.0 * 5/10000 = 25.0
        assert!((taker_fee - 25.0).abs() < 1e-6, "taker fee {}", taker_fee);

        let mut e2 = engine();
        e2.set_realism(0.0, 5.0, 2.0);
        e2.place_order(&OrderRequest {
            symbol: "BTCUSDT".to_string(),
            side: OrderSide::Buy,
            order_type: OrderTypeReq::Limit,
            price: Some(50_000.0),
            quantity: 1.0,
            time_in_force: None,
            client_order_id: None,
            reduce_only: false,
        }).unwrap();
        let maker_fee = e2.try_fill_at_price("BTCUSDT", 50_000.0)[0].fee;
        // 50000 * 1.0 * 2/10000 = 10.0
        assert!((maker_fee - 10.0).abs() < 1e-6, "maker fee {}", maker_fee);
    }
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cargo test -p trading-engine-core --lib connector::paper::tests`
Expected: the four new tests FAIL (price/fee still use old flat logic). The pre-existing tests still PASS (defaults unchanged).

- [ ] **Step 4: Apply slippage + tiered fees in `try_fill_at_price`**

Replace the fill-price + fee computation in `try_fill_at_price`. Find these lines (around 168-170):

```rust
                let fill_price = order.price.unwrap_or(market_price);
                let fill_qty = order.quantity;
                let fee = fill_price * fill_qty * FEE_RATE;
```

Replace with:

```rust
                let fill_qty = order.quantity;
                // Maker limits fill at their resting price (no slippage);
                // taker orders (Market, StopMarket) fill at the mark minus
                // adverse slippage (buys higher, sells lower).
                let is_maker = matches!(order.order_type, OrderTypeReq::Limit | OrderTypeReq::LimitMaker);
                let fill_price = if is_maker {
                    order.price.unwrap_or(market_price)
                } else {
                    let adverse = match order.side {
                        OrderSide::Buy => 1.0,
                        OrderSide::Sell => -1.0,
                    };
                    market_price * (1.0 + adverse * self.slippage_bps / 1e4)
                };
                let fee_bps = if is_maker { self.maker_fee_bps } else { self.taker_fee_bps };
                let fee = fill_price * fill_qty * (fee_bps / 1e4);
```

Leave the `FEE_RATE` const in place (now unused by fills, but referenced by the old comment/tests if any; remove later if clippy complains — keep to minimize churn).

- [ ] **Step 5: Run tests to verify they pass**

Run: `cargo test -p trading-engine-core --lib connector::paper::tests`
Expected: ALL tests PASS, including the pre-existing ones (`sell_stop_triggers_when_price_falls_through` etc. still pass because `engine()` defaults to slippage 0 / fees 10-10, reproducing old behavior).

- [ ] **Step 6: Add the `with_realism` connector builder**

In `impl PaperTradeConnector` (next to `with_fill_cooldown`, around line 251), add:

```rust
    /// Configure slippage + tiered fees. Call after constructing.
    pub fn with_realism(self, slippage_bps: f64, taker_fee_bps: f64, maker_fee_bps: f64) -> Self {
        if let Ok(mut engine) = self.engine.lock() {
            engine.set_realism(slippage_bps, taker_fee_bps, maker_fee_bps);
        }
        self
    }
```

- [ ] **Step 7: Add `PaperConfig` to `config.rs`**

Add a `default_10` helper near the other `default_*` fns (e.g., next to `default_10k`):

```rust
fn default_10() -> f64 { 10.0 }
```

Add the struct (e.g., just before `pub struct AppConfig` at line 6, or beside `CapitalConfig`):

```rust
#[derive(Debug, Clone, Deserialize)]
pub struct PaperConfig {
    /// Adverse slippage (bps) on taker fills only. 0 = off (old behavior).
    #[serde(default)]
    pub slippage_bps: f64,
    #[serde(default = "default_10")]
    pub taker_fee_bps: f64,
    #[serde(default = "default_10")]
    pub maker_fee_bps: f64,
}

impl Default for PaperConfig {
    fn default() -> Self {
        Self { slippage_bps: 0.0, taker_fee_bps: 10.0, maker_fee_bps: 10.0 }
    }
}
```

Add the field to `AppConfig` (line 6+):

```rust
    #[serde(default)]
    pub paper: PaperConfig,
```

- [ ] **Step 8: Wire realism into the connector build (`main.rs:53-59`)**

The current construction is:

```rust
            trading_engine_core::connector::paper::PaperTradeConnector::with_market_data(
                ...
            )
            .with_fill_cooldown(fill_cooldown_ms),
```

Change to chain `.with_realism(...)` before the closing:

```rust
            trading_engine_core::connector::paper::PaperTradeConnector::with_market_data(
                ...
            )
            .with_fill_cooldown(fill_cooldown_ms)
            .with_realism(
                config.paper.slippage_bps,
                config.paper.taker_fee_bps,
                config.paper.maker_fee_bps,
            ),
```

- [ ] **Step 9: Add a `paper` block to `config/strategy.yaml`**

Add under the top level (defaults shown; enable later by setting `slippage_bps`):

```yaml
paper:
  slippage_bps: 0       # 0 preserves old behavior; ~8 for realistic
  taker_fee_bps: 10     # 0.1% (current). Realistic USDT-M taker ~5
  maker_fee_bps: 10     # 0.1% (current). Realistic USDT-M maker ~2
```

- [ ] **Step 10: Build + full test + commit**

Run: `cargo test -p trading-engine-core` then `cargo run -p trading-engine-core --bin validate_config -- config/strategy.yaml` (if that bin exists; else `cargo build -p trading-engine-core`).
Expected: full suite green; config validates.

```bash
git add trading-engine-core/src/connector/paper.rs trading-engine-core/src/config.rs trading-engine-core/src/main.rs config/strategy.yaml
git commit -m "feat(paper): slippage + tiered maker/taker fees on taker fills"
```

---

## Task 2: Perp mark price for trend shorts

Adds a `PerpPriceSource` trait + Gate.io USDT-perp impl and makes trend mark open shorts against it.

**Files:**
- Create: `trading-engine-core/src/connector/perp_price.rs`
- Modify: `trading-engine-core/src/connector/mod.rs` (export the module)
- Modify: `trading-engine-core/src/config.rs` (`TrendConfig`: `perp_mark_source`, `funding_accrual`)
- Modify: `trading-engine-core/src/strategy/trend.rs` (struct field, `with_perp` builder, `on_tick` override)
- Modify: `trading-engine-core/src/main.rs:107` (build perp source, attach via `.with_perp(...)`)
- Test: inline `mod tests` in `perp_price.rs`; trend short-mark test in `trend.rs` tests

**Interfaces:**
- Produces: `connector::perp_price::PerpPriceSource` trait (`async fn mark(&self, symbol: &str) -> Option<f64>`; `async fn funding_rate(&self, symbol: &str) -> Option<f64>`); `GateioPerpSource::new() -> Self`; `TrendStrategy::with_perp(self, Arc<dyn PerpPriceSource>) -> Self`.
- Consumes: Task 1's config pattern (serde defaults).

- [ ] **Step 1: Write failing tests for the perp parsing + trait seam (`perp_price.rs`)**

Create the file with the trait + a parser test + a fake impl. The parser is a pure function over a JSON body so it is unit-testable without network.

```rust
use anyhow::Result;
use async_trait::async_trait;
use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::Mutex;

/// Perpetual mark + funding source. Behind a trait so tests inject a fake.
#[async_trait]
pub trait PerpPriceSource: Send + Sync {
    /// Current perp mark price for the symbol, or None if unavailable.
    async fn mark(&self, symbol: &str) -> Option<f64>;
    /// Current funding rate (e.g. 0.0001 = 0.01%), or None if unavailable.
    async fn funding_rate(&self, symbol: &str) -> Option<f64>;
}

/// Parse Gate.io `/futures/usdt/tickers` JSON for one contract's last + funding.
/// Body is an array: [{"contract":"BTC_USDT","last":"50000","funding_rate":"0.0001", ...}]
/// Returns (last, funding_rate) for `contract`, or None.
pub fn parse_gate_ticker(body: &str, contract: &str) -> Option<(f64, f64)> {
    let v: Vec<serde_json::Value> = serde_json::from_str(body).ok()?;
    for obj in v {
        if obj.get("contract").and_then(|c| c.as_str()) == Some(contract) {
            let last = obj.get("last").and_then(|l| l.as_str()).and_then(|s| s.parse::<f64>().ok())?;
            let fr = obj.get("funding_rate")
                .and_then(|f| f.as_str())
                .and_then(|s| s.parse::<f64>().ok())
                .unwrap_or(0.0);
            return Some((last, fr));
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_last_and_funding_for_contract() {
        let body = r#"[
            {"contract":"ETH_USDT","last":"3000","funding_rate":"0.00005"},
            {"contract":"BTC_USDT","last":"50000","funding_rate":"0.0001"}
        ]"#;
        let (last, fr) = parse_gate_ticker(body, "BTC_USDT").unwrap();
        assert!((last - 50_000.0).abs() < 1e-9);
        assert!((fr - 0.0001).abs() < 1e-9);
    }

    #[test]
    fn returns_none_for_missing_contract() {
        let body = r#"[{"contract":"BTC_USDT","last":"50000","funding_rate":"0.0001"}]"#;
        assert!(parse_gate_ticker(body, "DOGE_USDT").is_none());
    }

    #[test]
    fn missing_funding_defaults_to_zero() {
        let body = r#"[{"contract":"BTC_USDT","last":"50000"}]"#;
        let (last, fr) = parse_gate_ticker(body, "BTC_USDT").unwrap();
        assert!((last - 50_000.0).abs() < 1e-9);
        assert!((fr - 0.0).abs() < 1e-9);
    }

    #[tokio::test]
    async fn fake_source_returns_configured_values() {
        let f = super::FakePerp { mark: 1234.0, funding: 0.0002 };
        assert_eq!(f.mark("BTC-USDT").await, Some(1234.0));
        assert_eq!(f.funding_rate("BTC-USDT").await, Some(0.0002));
    }
}

/// Test double shared with strategy (trend) tests. `cfg(test)` + `pub(crate)`
/// so trend's test module can `use crate::connector::perp_price::FakePerp;`.
#[cfg(test)]
pub(crate) struct FakePerp {
    pub mark: f64,
    pub funding: f64,
}
#[cfg(test)]
#[async_trait]
impl PerpPriceSource for FakePerp {
    async fn mark(&self, _symbol: &str) -> Option<f64> { Some(self.mark) }
    async fn funding_rate(&self, _symbol: &str) -> Option<f64> { Some(self.funding) }
}
```

- [ ] **Step 2: Run tests to verify they fail (module not yet exported)**

Run: `cargo test -p trading-engine-core --lib connector::perp_price`
Expected: FAIL — module not declared (or compile error). After Step 3 wires the module, these pass.

- [ ] **Step 3: Add the `GateioPerpSource` live impl + export the module**

Append the live impl to `perp_price.rs` (above `#[cfg(test)]`):

```rust
/// Gate.io USDT-perpetual source. Caches per-symbol (mark, funding) for `ttl`.
pub struct GateioPerpSource {
    client: reqwest::Client,
    base: String,
    ttl: Duration,
    cache: Mutex<HashMap<String, (f64, f64, Instant)>>, // (mark, funding, fetched_at)
}

impl GateioPerpSource {
    pub fn new() -> Self {
        Self {
            client: reqwest::Client::new(),
            base: "https://api.gateio.ws/api/v4".to_string(),
            ttl: Duration::from_secs(5),
            cache: Mutex::new(HashMap::new()),
        }
    }

    fn contract(symbol: &str) -> String {
        // "BTC-USDT" / "BTCUSDT" -> "BTC_USDT"
        let s = symbol.replace('-', "");
        if let Some(pos) = s.find("USDT") {
            format!("{}_USDT", &s[..pos])
        } else {
            s
        }
    }

    async fn fetch(&self, symbol: &str) -> Option<(f64, f64)> {
        let contract = Self::contract(symbol);
        // Cache hit?
        {
            let c = self.cache.lock().await;
            if let Some(&(m, f, t)) = c.get(&contract) {
                if t.elapsed() < self.ttl { return Some((m, f)); }
            }
        }
        let url = format!("{}/futures/usdt/tickers?contract={}", self.base, contract);
        match self.client.get(&url).send().await {
            Ok(resp) => match resp.text().await {
                Ok(body) => {
                    if let Some((m, f)) = parse_gate_ticker(&body, &contract) {
                        self.cache.lock().await.insert(contract.clone(), (m, f, Instant::now()));
                        Some((m, f))
                    } else { None }
                }
                Err(_) => None,
            },
            Err(_) => None,
        }
    }
}

#[async_trait]
impl PerpPriceSource for GateioPerpSource {
    async fn mark(&self, symbol: &str) -> Option<f64> { self.fetch(symbol).await.map(|(m, _)| m) }
    async fn funding_rate(&self, symbol: &str) -> Option<f64> { self.fetch(symbol).await.map(|(_, f)| f) }
}
```

Export the module in `connector/mod.rs` (add next to the other `pub mod` lines):

```rust
pub mod perp_price;
```

Deps already present in `trading-engine-core/Cargo.toml` — no `Cargo.toml` change: `tokio` (features `full`), `async-trait`, `reqwest` (`json`, `rustls-tls`), `serde_json`.

- [ ] **Step 4: Run perp_price tests to verify they pass**

Run: `cargo test -p trading-engine-core --lib connector::perp_price`
Expected: PASS (4 tests).

- [ ] **Step 5: Add trend config fields (`config.rs` `TrendConfig`)**

Add to `TrendConfig` (after `trade_shorts`, around line 247):

```rust
    /// If set (e.g. "gateio_usdt_perp"), open SHORT positions are marked /
    /// triggered / exited against the perpetual mark instead of the spot mid.
    /// Longs always use spot. None disables (old behavior).
    #[serde(default)]
    pub perp_mark_source: Option<String>,
    /// Accrue funding on open shorts every 8h (requires perp_mark_source).
    #[serde(default)]
    pub funding_accrual: bool,
```

**Also update `base_test_config()`** in `trend.rs` tests (line ~983) — it builds a `TrendConfig { ... }` literal that must list every field or it won't compile. Add the two new fields to it:

```rust
            rsi_short_min: 35.0, atr_trailing_mult: 3.0, trade_shorts: false,
            perp_mark_source: None, funding_accrual: false,
        }
```

- [ ] **Step 6: Add the `perp` field + `with_perp` builder to `TrendStrategy` (`trend.rs`)**

Add the import near the top:

```rust
use crate::connector::perp_price::PerpPriceSource;
use std::sync::Arc;
```

Add a field to `TrendStrategy` (after `last_price` / `telegram`):

```rust
    perp: Option<Arc<dyn PerpPriceSource>>,
```

Initialize it in `new()` (in the struct literal, after `telegram,`):

```rust
            telegram,
            perp: None,
```

Add the builder next to the constructor:

```rust
    /// Attach a perp price source so open shorts are marked against the
    /// perpetual instead of the spot mid. Longs are unaffected.
    pub fn with_perp(mut self, perp: Arc<dyn PerpPriceSource>) -> Self {
        self.perp = Some(perp);
        self
    }
```

- [ ] **Step 7: Override `current_price` for open shorts in `on_tick`**

In `on_tick`, find (line ~428-430):

```rust
        let current_price = ctx.order_book.mid_price().unwrap_or(0.0);
        if current_price <= 0.0 { return Ok(orders); }
        self.last_price = current_price;
```

Replace with a short-aware override:

```rust
        let mut current_price = ctx.order_book.mid_price().unwrap_or(0.0);
        if current_price <= 0.0 { return Ok(orders); }
        // Open SHORT positions are marked against the perp feed (configurable),
        // so short MTM/triggers/exits reflect the perpetual, not spot. Longs and
        // no-position ticks keep the spot mid. On perp fetch failure, fall back
        // to spot and warn.
        if let Some(p) = &self.perp {
            let is_short = self.position.as_ref().map_or(false, |pos| pos.side == OrderSide::Sell);
            if is_short {
                match p.mark(&self.pair).await {
                    Some(mark) if mark > 0.0 => { current_price = mark; }
                    _ => warn!("perp mark unavailable for {}; using spot mid", self.pair),
                }
            }
        }
        self.last_price = current_price;
```

All downstream logic (TP/stop/trailing/pnl at lines 451-581) now uses the perp mark for shorts automatically because they read `current_price`.

- [ ] **Step 8: Wire the perp source into trend construction (`main.rs:107`)**

Currently:

```rust
        let trend = trading_engine_core::strategy::trend::TrendStrategy::new(
            symbol,
            &trend_cfg,
            telegram.clone_for_signal(),
        );
        engine.add_strategy(Box::new(trend));
```

Build the perp source ONCE before the pair loop (e.g., near the connector build) and clone the `Arc` per pair. Add before the `for (symbol, pc) in &pair_configs` loop:

```rust
    let perp_source: Option<Arc<dyn trading_engine_core::connector::perp_price::PerpPriceSource>> =
        if trend_cfg.perp_mark_source.as_deref() == Some("gateio_usdt_perp") {
            Some(Arc::new(trading_engine_core::connector::perp_price::GateioPerpSource::new()))
        } else {
            None
        };
```

Then attach it in the loop:

```rust
        let mut trend = trading_engine_core::strategy::trend::TrendStrategy::new(
            symbol,
            &trend_cfg,
            telegram.clone_for_signal(),
        );
        if let Some(p) = &perp_source {
            trend = trend.with_perp(p.clone());
        }
        engine.add_strategy(Box::new(trend));
```

- [ ] **Step 9: Write a failing test that a short uses the perp mark (`trend.rs` tests)**

Add to the `trend.rs` test module — it already has the helpers this test needs: `warmed_strategy(bool)`, `tick_at(price)`, `run_tick(&mut s, ctx)` (a sync `block_on` wrapper, so on_tick's `.await` is fine), and direct access to `s.realized_pnl` / `s.position`. `status()` is **sync** (`fn status(&self)`), so no `.await`. Uses the shared `FakePerp` from Task 2 Step 1 (`use crate::connector::perp_price::FakePerp;`):

```rust
    #[test]
    fn short_unrealized_uses_perp_mark_not_spot() {
        use crate::connector::perp_price::FakePerp;
        // warmed_strategy(true) builds a short-enabled strategy around price ~100.
        // Attach a FakePerp returning mark 50; inject a SHORT at entry 100.
        let mut s = warmed_strategy(true)
            .with_perp(std::sync::Arc::new(FakePerp { mark: 50.0, funding: 0.0 }));
        s.position = Some(TrendPosition {
            side: OrderSide::Sell, entry_price: 100.0, stop_loss: 110.0,
            quantity: 1.0, remaining_qty: 1.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false,
        });
        // Spot mid is 100 here; the perp override must force current_price to 50.
        let _ = run_tick(&mut s, tick_at(100.0));
        let st = s.status();
        // Unrealized at perp mark 50: (entry 100 - 50) * 1 = +50. Spot (100) would be 0.
        assert!(st.details.contains("Unrealized: $50.00"),
            "short must be marked at perp 50, not spot 100: {}", st.details);
    }
```

- [ ] **Step 10: Run tests; build; commit**

Run: `cargo test -p trading-engine-core`
Expected: green (existing trend tests unaffected — they don't set `perp`).

```bash
git add trading-engine-core/src/connector/perp_price.rs trading-engine-core/src/connector/mod.rs trading-engine-core/src/config.rs trading-engine-core/src/strategy/trend.rs trading-engine-core/src/main.rs
git commit -m "feat(trend): mark open shorts against Gate.io perp feed"
```

---

## Task 3: Funding accrual on open shorts

Reuses the `PerpPriceSource::funding_rate` from Task 2. Accrues every 8h on open shorts and journals it.

**Files:**
- Modify: `trading-engine-core/src/strategy/trend.rs` (`TrendPosition`: `last_funding_time`; `on_tick` accrual block)

**Interfaces:**
- Consumes: `PerpPriceSource::funding_rate` (Task 2), `trade_journal::log_unified` free function, `TrendConfig::funding_accrual` (Task 2).

- [ ] **Step 1: Add `last_funding_time` to `TrendPosition`**

In `trend.rs`, `TrendPosition` (around line 38-58), add after `restored`:

```rust
    /// Unix-seconds of the last funding accrual for this short. serde default 0
    /// so old state files load; the first accrual fires after one full interval.
    #[serde(default)]
    pub last_funding_time: i64,
```

Every `TrendPosition { ... }` literal in the file (entry construction) must add `last_funding_time: 0,` — find them via `grep -n "TrendPosition {" trading-engine-core/src/strategy/trend.rs` and add the field to each.

- [ ] **Step 2: Write failing test for funding accrual sign + interval**

Add to `trend.rs` tests. `tick_at()` stamps `timestamp: 1_700_000_001_000` (**milliseconds** — matching `entry_time` and `duration_minutes`), so a `last_funding_time: 0` short is ≥ one 8h interval old on the first tick:

```rust
    #[test]
    fn funding_accrues_negatively_for_short_on_positive_rate() {
        use crate::connector::perp_price::FakePerp;
        let mut s = warmed_strategy(true)
            .with_perp(std::sync::Arc::new(FakePerp { mark: 100.0, funding: 0.0001 }));
        s.config.funding_accrual = true; // test module can mutate private fields
        s.position = Some(TrendPosition {
            side: OrderSide::Sell, entry_price: 100.0, stop_loss: 110.0,
            quantity: 1.0, remaining_qty: 1.0, trailing_stop: None,
            highest_since_entry: 100.0, lowest_since_entry: 100.0, tp_levels: vec![],
            entry_time: 1_700_000_000_000, restored: false, last_funding_time: 0,
        });
        let before = s.realized_pnl;
        let _ = run_tick(&mut s, tick_at(100.0));
        // Positive funding 0.0001 on notional 100*1 = -0.01 to a short.
        assert!((s.realized_pnl - before - (-0.01)).abs() < 1e-9,
            "realized_pnl should drop by 0.01: before={} after={}", before, s.realized_pnl);
        // last_funding_time advanced to the tick's ms timestamp; won't re-fire next tick.
        assert_eq!(s.position.as_ref().unwrap().last_funding_time, 1_700_000_001_000);
    }
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cargo test -p trading-engine-core --lib strategy::trend`
Expected: FAIL (no accrual yet; `realized_pnl` unchanged).

- [ ] **Step 4: Add the funding accrual block in `on_tick`**

Inside the position-management block of `on_tick`, right after `current_price` is finalized (after the override added in Task 2 Step 7) and before the TP/stop checks, add (operating on `let pos = self.position.as_mut().unwrap()` — restructure the existing block to borrow mutably where needed; the existing code already mutates `pos`):

```rust
        // Funding accrual on open shorts, once per 8h (28_800_000 ms — ctx.timestamp
        // and entry_time are both milliseconds), using the perp funding rate.
        if self.config.funding_accrual {
            if let Some(p) = &self.perp {
                let mut accrue: Option<f64> = None;
                if let Some(pos) = self.position.as_ref() {
                    if pos.side == OrderSide::Sell && ctx.timestamp - pos.last_funding_time >= 28_800_000 {
                        accrue = p.funding_rate(&self.pair).await;
                    }
                }
                if let Some(rate) = accrue {
                    if let Some(pos) = self.position.as_mut() {
                        let notional = pos.entry_price * pos.remaining_qty;
                        let funding_pnl = -rate * notional; // positive rate -> short pays
                        self.realized_pnl += funding_pnl;
                        pos.last_funding_time = ctx.timestamp;
                        crate::strategy::trade_journal::log_unified(
                            "trend", &self.pair, Some("SELL"),
                            None, None, Some(0.0), funding_pnl, Some("funding"), None,
                        );
                        debug!("funding accrued on {}: rate={} pnl={}", self.pair, rate, funding_pnl);
                    }
                }
            }
        }
```

Note the two-phase borrow (first `as_ref` to read side/time, then `as_mut` to mutate) avoids holding a borrow across the `.await`. Adjust to fit the surrounding borrow structure. Tests read `s.realized_pnl` directly (the test module is in the same module, so private fields are visible) — no accessor needed.

- [ ] **Step 5: Run tests to verify they pass**

Run: `cargo test -p trading-engine-core --lib strategy::trend`
Expected: PASS. Also re-run the full suite: `cargo test -p trading-engine-core`.

- [ ] **Step 6: Enable funding in `config/strategy.yaml`**

Under the existing `trend:` block:

```yaml
  perp_mark_source: gateio_usdt_perp
  funding_accrual: true
```

- [ ] **Step 7: Build + commit**

Run: `cargo build -p trading-engine-core` then `cargo test -p trading-engine-core`.

```bash
git add trading-engine-core/src/strategy/trend.rs config/strategy.yaml
git commit -m "feat(trend): accrue funding on open shorts every 8h"
```

---

## Deployment note (after all tasks)

- Capture an all-time P&L snapshot first (`SELECT engine, SUM(pnl) FROM trades WHERE is_backfilled=0 GROUP BY engine`) — slippage will lower future reported P&L, so mark the pre/post boundary.
- Flip `paper.slippage_bps` from 0 to ~8 when ready to start counting realistic numbers.
- Watch logs for `perp mark unavailable` warnings on first deploy (confirms Gate.io reachability from the EC2 host).
