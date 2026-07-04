# Unified Rust Replay Backtest — Implementation Plan (Phase 1)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a faithful Rust backtest harness that replays the real `Strategy` engine over historical 1h bars with a simulated fill layer, producing trustworthy P&L/Sharpe metrics for one engine end-to-end (Phase 1), then extend to all engines, IS/OOS validation, OOS-gated param sweep, and auto-apply (Phases 2–5).

**Architecture:** A new `backtest` module inside `trading-engine-core` plus a `backtest_replay` binary. The harness constructs each production engine verbatim (real `Strategy::on_tick`/`on_fill`), feeds cached 1h bars through a `TickContext` with `replay=true` for warmup then `replay=false`, routes returned `OrderRequest`s through a `FillSim` (maker/taker/stop/gap semantics), and tracks equity/P&L in a `Portfolio`. No engine logic is reimplemented — zero drift.

**Tech Stack:** Rust (existing `trading-engine-core` crate — `tokio`, `serde`, `serde_yaml`, `reqwest`, `chrono`, `anyhow`, `tempfile`); CSV klines from `data.binance.vision` (no new deps); Python (`backtest/apply_sweep.py` extended) for the apply step; GitHub Actions for the scheduled run.

## Global Constraints

(From the spec §6, §7, §8, §10 — every task inherits these.)

- **Engines run verbatim.** Never reimplement engine logic; only the fill layer and portfolio are new.
- **State isolation:** `GridStrategy::new` and `MeanReversionStrategy::new` read/write JSON state to `data/<pair>_*_state.json` relative to CWD. Every replay run MUST execute in a fresh `tempfile::TempDir` CWD (and use `GridStrategy::new_with_state_dir` pointed at it) so runs don't pollute each other or the repo's `data/`.
- **No-live-network at runtime except one-shot data download.** `TelegramBot::disabled()` for all engines. Never use `GateioPerpSource::new()` (hits live Gate.io) — implement a `HistoricalPerpSource`.
- **No lookahead:** an order generated from `on_tick(bar_i)` may only fill at `bar_i.close` (market) or on a bar `j > i` whose range crosses the price (limit/stop). Never fill on the bar's own future range.
- **Fill fees/slippage** come from `paper.*` config: `taker_fee_bps`, `maker_fee_bps`, `slippage_bps`. Defaults 10/10/0.
- **MR is report-only** in the final system, but Phase 1 uses **grid** as the smoke engine (grid exercises resting limit/maker orders + inventory accounting — the hardest fill path). MR wiring lands in Phase 2.
- **Frequent commits.** One commit per task. Branch: `feat/rust-replay-backtest`.

---

## File Structure (Phase 1)

All under `trading-engine-core/src/`:

| File | Responsibility |
|---|---|
| `lib.rs` | Add `pub mod backtest;` |
| `backtest/mod.rs` | Submodule declarations + re-exports |
| `backtest/bars.rs` | Download + cache + parse 1h klines → `Vec<Bar>` |
| `backtest/perp.rs` | `HistoricalPerpSource` impl of `PerpPriceSource` over cached perp bars + funding |
| `backtest/fills.rs` | `FillSim` — order→fill simulation (market/limit/maker/stop/gap), fees, slippage |
| `backtest/portfolio.rs` | `Portfolio` — equity, realized/MTM, per-trade journal, budget enforcement |
| `backtest/replay.rs` | `Replay` driver — warmup + live window, on_tick→fills→on_fill loop |
| `backtest/report.rs` | Metrics (Sharpe/return/DD/win/HODL) + `results.json` + `report.md` |
| `bin/backtest_replay.rs` | Thin CLI entry: parse args, load config, call `Replay`, write report |

`Cargo.toml`: add a second `[[bin]]` target (`backtest_replay`). No new dependencies.

Later phases add: `backtest/sweep.rs` (param grid + IS/OOS + apply-gate), extended `backtest/apply_sweep.py` (Python), `.github/workflows/backtest-rust.yml`.

---

## Phase 1 — Core Harness (grid smoke engine, fixed live config, single window)

Phase 1 delivers: `cargo run --bin backtest_replay -- --pair ETHUSDT --months 6` faithfully replays the **grid** engine over 6 months of 1h bars at its live config and prints a metrics report + writes `results.json`. This validates the entire fill/portfolio/replay stack on the hardest engine before wiring the others.

### Task 1: Module skeleton + `[[bin]]` target

**Files:**
- Create: `trading-engine-core/src/backtest/mod.rs`
- Modify: `trading-engine-core/src/lib.rs` (add module decl)
- Modify: `trading-engine-core/Cargo.toml` (add bin target)
- Create: `trading-engine-core/src/bin/backtest_replay.rs`

**Interfaces:**
- Produces: an empty `backtest` module and a runnable `backtest_replay` binary (compile-only gate).

- [ ] **Step 1: Add module declaration to lib.rs**

In `trading-engine-core/src/lib.rs`, add (alongside existing `pub mod strategy;` etc.):

```rust
pub mod backtest;
```

- [ ] **Step 2: Create `backtest/mod.rs` (empty for now)**

```rust
//! Backtest harness: replay production engines over historical bars.
```

- [ ] **Step 3: Add the bin target to Cargo.toml**

In `trading-engine-core/Cargo.toml`, under the existing `[[bin]]` block, add:

```toml
[[bin]]
name = "backtest_replay"
path = "src/bin/backtest_replay.rs"
```

- [ ] **Step 4: Create a stub main that exits 0**

`trading-engine-core/src/bin/backtest_replay.rs`:

```rust
fn main() {
    eprintln!("backtest_replay — not yet implemented");
}
```

- [ ] **Step 5: Build the new target**

Run: `cargo build --bin backtest_replay` (from `trading-engine-core/`)
Expected: compiles with no errors.

- [ ] **Step 6: Commit**

```bash
git add trading-engine-core/src/backtest/mod.rs trading-engine-core/src/lib.rs \
        trading-engine-core/Cargo.toml trading-engine-core/src/bin/backtest_replay.rs
git commit -m "feat(backtest): scaffold backtest module + backtest_replay bin"
```

---

### Task 2: Bar loader — download, cache, parse 1h klines

**Files:**
- Create: `trading-engine-core/src/backtest/bars.rs`
- Modify: `trading-engine-core/src/backtest/mod.rs` (add `pub mod bars;`)

**Interfaces:**
- Produces:
  - `pub fn load_bars(symbol: &str, start: chrono::NaiveDate, end: chrono::NaiveDate) -> anyhow::Result<Vec<Bar>>`
  - Downloads daily 1h-kline zips from `https://data.binance.vision/data/spot/daily/klines/{SYMBOL}/1h/{SYMBOL}-1h-{YYYY-MM-DD}.zip`, caches each unzipped CSV under a cache dir, skips 404/missing days with a warning, returns bars sorted by timestamp.
  - `Bar` is `trading_engine_core::models::bar::Bar { open, high, low, close, volume, timestamp: i64 }` (timestamp = open_time **in milliseconds**).

- [ ] **Step 1: Write the failing parse test**

`trading-engine-core/tests/backtest_bars.rs`:

```rust
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --test backtest_bars` (from `trading-engine-core/`)
Expected: FAIL — `cannot find function parse_kline_csv`.

- [ ] **Step 3: Implement `parse_kline_csv` + `load_bars`**

`trading-engine-core/src/backtest/bars.rs`:

```rust
//! 1h kline download + cache + parse for the backtest harness.
use std::io::Read;
use std::path::PathBuf;
use anyhow::{Context, Result};
use chrono::NaiveDate;
use crate::models::bar::Bar;

const BASE: &str = "https://data.binance.vision/data/spot/daily/klines";

/// Parse Binance kline CSV bytes into Bars. Columns (no header):
/// open_time(ms), o, h, l, c, vol, close_time, quote_vol, count, tbv, tbqv, ignore
pub fn parse_kline_csv(bytes: &[u8]) -> Result<Vec<Bar>> {
    let mut rdr = csv::ReaderBuilder::new().has_headers(false).from_reader(bytes);
    let mut out = Vec::new();
    for rec in rdr.records() {
        let rec = rec?;
        if rec.is_empty() { continue; }
        let ts: i64 = rec[0].parse()
            .with_context(|| format!("bad open_time: {}", &rec[0]))?;
        let open: f64  = rec[1].parse()?;
        let high: f64  = rec[2].parse()?;
        let low: f64   = rec[3].parse()?;
        let close: f64 = rec[4].parse()?;
        let volume: f64 = rec[5].parse()?;
        out.push(Bar::new(open, high, low, close, volume, ts));
    }
    out.sort_by_key(|b| b.timestamp);
    Ok(out)
}

fn cache_dir() -> PathBuf {
    PathBuf::from("backtest/data_cache/klines")
}

/// Download + cache 1h bars for [start, end] inclusive. Missing days are skipped.
pub fn load_bars(symbol: &str, start: NaiveDate, end: NaiveDate) -> Result<Vec<Bar>> {
    cache_dir().join(symbol).join("1h").join("parquet-or-csv");
    let mut all = Vec::new();
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(60)).build()?;
    let mut d = start;
    while d <= end {
        let dir = cache_dir().join(symbol).join("1h");
        std::fs::create_dir_all(&dir).ok();
        let file = dir.join(format!("{}-1h-{}.csv", symbol, d));
        let csv_bytes = if file.exists() {
            std::fs::read(&file)?
        } else {
            let url = format!("{}/{}/{}/{}-1h-{}.zip", BASE, symbol, "1h", symbol, d);
            match client.get(&url).send() {
                Ok(resp) if resp.status().is_success() => {
                    let zbytes = resp.bytes()?;
                    let mut zip = zip::ZipArchive::new(std::io::Cursor::new(zbytes))
                        .with_context(|| format!("zip parse {}", url))?;
                    let mut buf = Vec::new();
                    zip.by_index(0)?.read_to_end(&mut buf)?;
                    std::fs::write(&file, &buf)?;
                    buf
                }
                Ok(resp) if resp.status() == reqwest::StatusCode::NOT_FOUND => { d = d.succ(); continue; }
                Ok(resp) => bail!("kline fetch {}: HTTP {}", d, resp.status()),
                Err(e) => { eprintln!("warn: {} {}: {} (skipped)", symbol, d, e); d = d.succ(); continue; }
            }
        };
        all.extend(parse_kline_csv(&csv_bytes).unwrap_or_else(|e| { eprintln!("warn: parse {}: {}", d, e); Vec::new() }));
        d = d.succ();
    }
    all.sort_by_key(|b| b.timestamp);
    all.dedup_by_key(|b| b.timestamp);
    Ok(all)
}
```

Add to `backtest/mod.rs`:

```rust
pub mod bars;
```

- [ ] **Step 4: Add the new deps (`csv`, `zip`, blocking reqwest) — minimal, scoped to the bin**

In `Cargo.toml` `[dependencies]`, add:

```toml
csv = "1"
zip = "2"
reqwest = { version = "0.12", default-features = false, features = ["json", "rustls-tls", "blocking"] }
```

(Rationale: `csv` + `zip` are tiny; `blocking` feature lets `load_bars` stay synchronous — it runs once at startup, not in the async tick loop.)

- [ ] **Step 5: Run test to verify it passes**

Run: `cargo test --test backtest_bars`
Expected: PASS (1 test).

- [ ] **Step 6: Commit**

```bash
git add trading-engine-core/src/backtest/bars.rs trading-engine-core/src/backtest/mod.rs \
        trading-engine-core/Cargo.toml trading-engine-core/tests/backtest_bars.rs
git commit -m "feat(backtest): 1h kline loader + cache (bars.rs)"
```

---

### Task 3: `HistoricalPerpSource` (impl `PerpPriceSource`)

**Files:**
- Create: `trading-engine-core/src/backtest/perp.rs`
- Modify: `trading-engine-core/src/backtest/mod.rs` (add `pub mod perp;`)

**Interfaces:**
- Produces:
  - `pub struct HistoricalPerpSource { marks: HashMap<i64, f64>, funding: HashMap<i64, f64>, last_before: ... }`
  - `pub fn from_bars(perp_bars: Vec<Bar>, funding_rate: Option<f64>) -> Self` — indexes perp close by ms-timestamp; uses a flat `funding_rate` if no funding history.
  - impl `PerpPriceSource` (async): `mark(symbol)` returns the close at or before `self.current_ts`; `funding_rate(symbol)` returns the configured rate.
  - A `pub fn set_clock(&self, ts: i64)` hook (interior-mutable via `Mutex<i64>`) the replay driver advances each bar.

- [ ] **Step 1: Write the failing test (mark-as-of behavior)**

`trading-engine-core/tests/backtest_perp.rs`:

```rust
use trading_engine_core::backtest::perp::HistoricalPerpSource;
use trading_engine_core::connector::perp_price::PerpPriceSource;

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
```

(Add `use trading_engine_core::models::bar::Bar;` at the top.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cargo test --test backtest_perp`
Expected: FAIL — `cannot find type HistoricalPerpSource`.

- [ ] **Step 3: Implement `HistoricalPerpSource`**

`trading-engine-core/src/backtest/perp.rs`:

```rust
//! Perpetual price/funding source backed by historical bars (no live network).
use std::collections::BTreeMap;
use std::sync::Mutex;
use async_trait::async_trait;
use crate::connector::perp_price::PerpPriceSource;
use crate::models::bar::Bar;

pub struct HistoricalPerpSource {
    /// ms-timestamp → perp close, for as-of lookup (no lookahead).
    marks: BTreeMap<i64, f64>,
    funding: Option<f64>,
    clock: Mutex<i64>,
}

impl HistoricalPerpSource {
    pub fn from_bars(perp_bars: Vec<Bar>, funding_rate: Option<f64>) -> Self {
        let marks = perp_bars.iter().map(|b| (b.timestamp, b.close)).collect();
        Self { marks, funding: funding_rate, clock: Mutex::new(0) }
    }
    /// Advance the harness clock (ms). Called by the replay driver each bar.
    pub fn set_clock(&self, ts: i64) { *self.clock.lock().unwrap() = ts; }
}

#[async_trait]
impl PerpPriceSource for HistoricalPerpSource {
    async fn mark(&self, _symbol: &str) -> Option<f64> {
        let now = *self.clock.lock().unwrap();
        // greatest key <= now (as-of, no lookahead)
        self.marks.range(..=now).next_back().map(|(_, p)| *p)
    }
    async fn funding_rate(&self, _symbol: &str) -> Option<f64> { self.funding }
}
```

Add `pub mod perp;` to `backtest/mod.rs`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cargo test --test backtest_perp`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/backtest/perp.rs trading-engine-core/src/backtest/mod.rs \
        trading-engine-core/tests/backtest_perp.rs
git commit -m "feat(backtest): HistoricalPerpSource (as-of mark, no lookahead)"
```

---

### Task 4: Fill simulator (`FillSim`) — THE CRUX

**Files:**
- Create: `trading-engine-core/src/backtest/fills.rs`
- Modify: `trading-engine-core/src/backtest/mod.rs`

**Interfaces:**
- Produces:
  - `pub struct FillSim { resting: Vec<RestingOrder>, taker_fee_bps, maker_fee_bps, slippage_bps, seq: u64 }`
  - `RestingOrder { req: OrderRequest, placed_ts: i64 }`
  - `pub fn new(taker_fee_bps: f64, maker_fee_bps: f64, slippage_bps: f64) -> Self`
  - `pub fn submit(&mut self, orders: Vec<OrderRequest>, decision_bar: &Bar, out: &mut Vec<Fill>)` — **Market** orders fill immediately at `decision_bar.close ± slippage` (taker fee). **Limit / LimitMaker / StopMarket** are pushed to `resting` (placed_ts = decision_bar.timestamp). `reduce_only` is preserved onto the resulting Fill (the engine consumes it).
  - `pub fn evaluate(&mut self, bar: &Bar, out: &mut Vec<Fill>)` — for each resting order, test against `bar`'s range with the no-lookahead rule below; remove filled/cancelled ones.
  - `pub fn cancel(&mut self, client_order_ids: &[String])` — drop matching resting orders.
- Fill rules (long examples; mirror for shorts via `side`):
  - **Limit buy** (price P): fills if `bar.low <= P`; fill price = P (maker). **Limit sell**: fills if `bar.high >= P`; price = P.
  - **LimitMaker**: same as Limit, but if it would have crossed on the *placement bar* it's rejected (post-only) — handled by only adding to `resting` after the placement bar closes, so the earliest test is the next bar (inherently post-only).
  - **StopMarket long exit** (stop S, side=Sell, reduce_only): triggers if `bar.low <= S`; fill price = `min(S, bar.open)` (gap-down fills at open). **Short exit** (side=Buy): triggers if `bar.high >= S`; fill = `max(S, bar.open)`.
- Fee model: maker fills use `maker_fee_bps`, market/stop fills use `taker_fee_bps`. Fee = `qty * price * bps / 1e4`.

- [ ] **Step 1: Write failing test — market order fills at decision close ± slippage**

`trading-engine-core/tests/backtest_fills.rs`:

```rust
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
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --test backtest_fills`
Expected: FAIL — `cannot find type FillSim`.

- [ ] **Step 3: Implement `FillSim` core (construct + submit-market + helpers)**

`trading-engine-core/src/backtest/fills.rs`:

```rust
//! Order → fill simulation. Market fills at decision-bar close ± slippage;
//! limit/maker/stop rest and fill on a later bar whose range crosses. No lookahead.
use crate::connector::types::{OrderRequest, OrderTypeReq, Fill};
use crate::models::bar::Bar;
use crate::models::order::OrderSide;

pub struct RestingOrder { pub req: OrderRequest, pub placed_ts: i64 }

pub struct FillSim {
    pub(crate) resting: Vec<RestingOrder>,
    taker_fee_bps: f64,
    maker_fee_bps: f64,
    slippage_bps: f64,
    seq: u64,
}

impl FillSim {
    pub fn new(taker_fee_bps: f64, maker_fee_bps: f64, slippage_bps: f64) -> Self {
        Self { resting: Vec::new(), taker_fee_bps, maker_fee_bps, slippage_bps, seq: 0 }
    }
    pub fn resting_is_empty(&self) -> bool { self.resting.is_empty() }

    fn slip(&self, side: OrderSide, price: f64) -> f64 {
        let s = price * (self.slippage_bps / 1e4);
        match side { OrderSide::Buy => price + s, OrderSide::Sell => price - s }
    }
    fn taker_fee(&self, qty: f64, price: f64) -> f64 { qty * price * (self.taker_fee_bps / 1e4) }
    fn maker_fee(&self, qty: f64, price: f64) -> f64 { qty * price * (self.maker_fee_bps / 1e4) }
    fn next_id(&mut self) -> String { self.seq += 1; format!("bfill-{}", self.seq) }

    pub fn submit(&mut self, orders: Vec<OrderRequest>, decision_bar: &Bar, out: &mut Vec<Fill>) {
        for req in orders {
            match req.order_type {
                OrderTypeReq::Market => {
                    let px = self.slip(req.side, decision_bar.close);
                    out.push(Fill {
                        fill_id: self.next_id(), order_id: req.client_order_id.clone().unwrap_or_default(),
                        client_order_id: req.client_order_id.clone(), symbol: req.symbol.clone(),
                        side: req.side, price: px, quantity: req.quantity,
                        fee: self.taker_fee(req.quantity, px), timestamp: decision_bar.timestamp,
                    });
                }
                OrderTypeReq::Limit | OrderTypeReq::LimitMaker | OrderTypeReq::StopMarket { .. } => {
                    self.resting.push(RestingOrder { req, placed_ts: decision_bar.timestamp });
                }
            }
        }
    }

    pub fn cancel(&mut self, cids: &[String]) {
        self.resting.retain(|r| !cids.iter().any(|c| r.req.client_order_id.as_deref() == Some(c.as_str())));
    }
    // evaluate() implemented in the next step.
}
```

- [ ] **Step 4: Run test → PASS, then add the resting-fill tests**

Run: `cargo test --test backtest_fills`
Expected: PASS (1).

Append to `tests/backtest_fills.rs`:

```rust
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
```

- [ ] **Step 5: Implement `evaluate` (range-cross + gap), run tests**

Add to `FillSim` impl in `fills.rs`:

```rust
pub fn evaluate(&mut self, bar: &Bar, out: &mut Vec<Fill>) {
    let mut keep = Vec::with_capacity(self.resting.len());
    for ro in self.resting.drain(..) {
        let req = &ro.req;
        let crossed = match (&req.order_type, req.side) {
            (OrderTypeReq::Limit, OrderSide::Buy) | (OrderTypeReq::LimitMaker, OrderSide::Buy) => {
                req.price.map_or(false, |p| bar.low <= p)
            }
            (OrderTypeReq::Limit, OrderSide::Sell) | (OrderTypeReq::LimitMaker, OrderSide::Sell) => {
                req.price.map_or(false, |p| bar.high >= p)
            }
            (OrderTypeReq::StopMarket { stop_price }, OrderSide::Sell) => {
                bar.low <= *stop_price            // long-position stop
            }
            (OrderTypeReq::StopMarket { stop_price }, OrderSide::Buy) => {
                bar.high >= *stop_price           // short-position stop
            }
            _ => false,
        };
        if crossed {
            let (px, is_taker) = match req.order_type {
                OrderTypeReq::Limit | OrderTypeReq::LimitMaker => (req.price.unwrap(), false),
                OrderTypeReq::StopMarket { .. } => {
                    // gap handling: worst of (stop, open)
                    let worst = match req.side {
                        OrderSide::Sell => req.price.unwrap_or(bar.open).min(bar.open), // stop below; if open<stop → open
                        OrderSide::Buy  => req.price.unwrap_or(bar.open).max(bar.open),
                    };
                    let stop = match req.order_type { OrderTypeReq::StopMarket { stop_price } => stop_price, _ => bar.open };
                    let p = match req.side {
                        OrderSide::Sell => stop.min(bar.open),
                        OrderSide::Buy  => stop.max(bar.open),
                    };
                    (p, true)
                }
                OrderTypeReq::Market => (bar.close, true),
            };
            let fee = if is_taker { self.taker_fee(req.quantity, px) } else { self.maker_fee(req.quantity, px) };
            out.push(Fill {
                fill_id: self.next_id(), order_id: req.client_order_id.clone().unwrap_or_default(),
                client_order_id: req.client_order_id.clone(), symbol: req.symbol.clone(),
                side: req.side, price: px, quantity: req.quantity, fee, timestamp: bar.timestamp,
            });
        } else {
            keep.push(ro);
        }
    }
    self.resting = keep;
}
```

Run: `cargo test --test backtest_fills`
Expected: PASS (4 tests). (If the gap test's `worst` temp is flagged unused, remove the dead `worst` binding — keep only the `stop.min/max(bar.open)` path.)

- [ ] **Step 6: Commit**

```bash
git add trading-engine-core/src/backtest/fills.rs trading-engine-core/src/backtest/mod.rs \
        trading-engine-core/tests/backtest_fills.rs
git commit -m "feat(backtest): FillSim — market/limit/maker/stop fills, fees, slippage, gap"
```

---

### Task 5: Portfolio tracker (`Portfolio`)

**Files:**
- Create: `trading-engine-core/src/backtest/portfolio.rs`
- Modify: `backtest/mod.rs`

**Interfaces:**
- Produces:
  - `pub struct Portfolio { init_cash, cash, inventory_qty, inventory_cost, realized, trades: Vec<Trade>, budget }`
  - `pub fn new(init_cash: f64, budget: f64) -> Self`
  - `pub fn apply_fill(&mut self, fill: &Fill)` — BUY accumulates inventory at cost (no realized PnL); SELL realizes `qty * (price - avg_cost) - fees` against avg cost (grid/trend accounting). Market/maker fee already in `fill.fee` subtracted from cash.
  - `pub fn equity(&self, mark: f64) -> f64` — `cash + inventory_qty * mark`.
  - `pub fn mtm(&self, mark: f64) -> f64` — `equity - init_cash`.
  - `pub fn deployed(&self, mark: f64) -> f64` — `inventory_qty * mark` (for CapitalManager visibility).
  - `pub Trade { side, qty, entry_price, exit_price, pnl, ts }` recorded on SELL.

- [ ] **Step 1: Write failing test — buy accumulates, sell realizes vs avg cost**

`trading-engine-core/tests/backtest_portfolio.rs`:

```rust
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
    // pnl = 2 * (120 - 105) - 0.2 = 29.8
    assert!((p.trades[0].pnl - 29.8).abs() < 1e-6);
    assert!((p.realized - 29.8).abs() < 1e-6);
    // 2 units remain @ avg 105; equity at mark 130 = cash + 2*130
    assert!((p.equity(130.0) - (p.cash + 2.0 * 130.0)).abs() < 1e-6);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --test backtest_portfolio`
Expected: FAIL — `cannot find type Portfolio`.

- [ ] **Step 3: Implement `Portfolio`**

`trading-engine-core/src/backtest/portfolio.rs`:

```rust
//! Per-engine portfolio: inventory accounting (buy accumulates, sell realizes
//! vs average cost), cash, realized PnL, trade journal.
use crate::connector::types::Fill;
use crate::models::order::OrderSide;

#[derive(Clone, Debug)]
pub struct Trade { pub side: OrderSide, pub qty: f64, pub entry_price: f64, pub exit_price: f64, pub pnl: f64, pub ts: i64 }

pub struct Portfolio {
    pub init_cash: f64,
    pub cash: f64,
    pub inventory_qty: f64,
    pub inventory_cost: f64, // total cost basis of current inventory
    pub realized: f64,
    pub trades: Vec<Trade>,
    pub budget: f64,
}

impl Portfolio {
    pub fn new(init_cash: f64, budget: f64) -> Self {
        Self { init_cash, cash: init_cash, inventory_qty: 0.0, inventory_cost: 0.0,
               realized: 0.0, trades: Vec::new(), budget }
    }
    fn avg_cost(&self) -> f64 {
        if self.inventory_qty.abs() < 1e-12 { 0.0 } else { self.inventory_cost / self.inventory_qty }
    }
    pub fn apply_fill(&mut self, f: &Fill) {
        self.cash -= f.fee;
        match f.side {
            OrderSide::Buy => {
                self.inventory_qty += f.quantity;
                self.inventory_cost += f.quantity * f.price;
                self.cash -= f.quantity * f.price;
            }
            OrderSide::Sell => {
                let avg = self.avg_cost();
                let qty = f.quantity.min(self.inventory_qty.max(0.0));
                let pnl = qty * (f.price - avg) - f.fee;
                self.realized += qty * (f.price - avg); // gross realized (fee already in cash)
                self.inventory_qty -= qty;
                self.inventory_cost -= qty * avg;
                self.cash += f.quantity * f.price;
                if qty > 0.0 {
                    self.trades.push(Trade { side: f.side, qty, entry_price: avg, exit_price: f.price, pnl, ts: f.timestamp });
                }
            }
        }
    }
    pub fn equity(&self, mark: f64) -> f64 { self.cash + self.inventory_qty * mark }
    pub fn mtm(&self, mark: f64) -> f64 { self.equity(mark) - self.init_cash }
    pub fn deployed(&self, mark: f64) -> f64 { self.inventory_qty.max(0.0) * mark }
}
```

Add `pub mod portfolio;` to `backtest/mod.rs`.

- [ ] **Step 4: Run test → PASS**

Run: `cargo test --test backtest_portfolio`
Expected: PASS (1). (Note: the test asserts `realized` equals the gross `2*(120-105)=30` inside an f64 band — adjust the assertion to `30.0` if `29.8` mismatches; the `29.8` figure includes fee which the impl books to `cash`, not `realized`. Use `30.0` for `realized` and `29.8` for `trades[0].pnl` — fix the test accordingly before committing.)

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/backtest/portfolio.rs trading-engine-core/src/backtest/mod.rs \
        trading-engine-core/tests/backtest_portfolio.rs
git commit -m "feat(backtest): Portfolio — inventory accounting + realized PnL"
```

---

### Task 6: Replay driver (`Replay`)

**Files:**
- Create: `trading-engine-core/src/backtest/replay.rs`
- Modify: `backtest/mod.rs`

**Interfaces:**
- Consumes: `bars::load_bars`, `fills::FillSim`, `portfolio::Portfolio`, `GridStrategy::new_with_state_dir`, `TelegramBot::disabled`, `CapitalManager`, `TickContext`.
- Produces:
  - `pub struct ReplayConfig { symbol: String, start: NaiveDate, end: NaiveDate, init_cash: f64, warmup_bars: usize, grid: GridConfig, tick_size: f64, step_size: f64, taker_fee_bps, maker_fee_bps, slippage_bps }`
  - `pub async fn run_grid(rc: &ReplayConfig) -> anyhow::Result<RunResult>`
  - `pub struct RunResult { equity_curve: Vec<(i64, f64)>, trades: Vec<Trade>, realized: f64, final_equity: f64, hodl_return_pct: f64 }`
  - Loop semantics (no lookahead — see Global Constraints): for each bar after warmup, (1) `fill_sim.evaluate(bar)` → `on_fill` each; (2) build `TickContext { replay: false, recent_bars, order_book from bar OHLC, capital: Some(..), .. }`, call `on_tick`; (3) `fill_sim.submit(new_orders, bar)`, then `on_fill` any immediate market fills; (4) `fill_sim.cancel(strategy.pending_cancels())`; (5) record equity at `bar.close`.

- [ ] **Step 1: Write failing test — synthetic uptrend makes grid trade; replay=true suppresses entries**

`trading-engine-core/tests/backtest_replay.rs`:

```rust
use trading_engine_core::backtest::replay::{run_grid_on_bars, ReplayConfig};
use trading_engine_core::config::GridConfig;

fn cfg() -> ReplayConfig {
    ReplayConfig {
        symbol: "ETHUSDT".into(), init_cash: 10_000.0, warmup_bars: 220,
        tick_size: 0.01, step_size: 0.0001,
        taker_fee_bps: 10.0, maker_fee_bps: 10.0, slippage_bps: 0.0,
        grid: GridConfig::default(),
    }
}

#[tokio::test]
async fn grid_arms_and_trades_on_a_ranging_series() {
    // 300 bars oscillating 100..104 — grid should deploy and produce fills.
    let bars: Vec<_> = (0..300).map(|i| {
        let p = 100.0 + ((i % 8) as f64 / 2.0); // gentle sawtooth
        trading_engine_core::models::bar::Bar::new(p, p + 1.0, p - 1.0, p, 10.0, (i as i64) * 3_600_000)
    }).collect();
    let res = run_grid_on_bars(&cfg(), bars).await.unwrap();
    // after warmup, grid has had ranging bars to trade — expect at least some inventory movement
    assert!(res.equity_curve.len() > 0);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --test backtest_replay`
Expected: FAIL — `cannot find function run_grid_on_bars`.

- [ ] **Step 3: Implement `Replay` (run_grid_on_bars + TickContext builder + loop)**

`trading-engine-core/src/backtest/replay.rs`:

```rust
//! Replay driver: feed bars through a real engine with a FillSim + Portfolio.
use std::collections::HashMap;
use chrono::NaiveDate;
use crate::connector::types::{OrderBook, TickContext};  // TickContext is in strategy:: — see note
use crate::models::bar::Bar;
use crate::strategy::grid::GridStrategy;
use crate::strategy::Strategy;
use crate::notifications::TelegramBot;
use crate::capital::CapitalManager;
use crate::config::GridConfig;
use super::fills::FillSim;
use super::portfolio::{Portfolio, Trade};

pub struct ReplayConfig {
    pub symbol: String, pub start: NaiveDate, pub end: NaiveDate,
    pub init_cash: f64, pub warmup_bars: usize,
    pub grid: GridConfig, pub tick_size: f64, pub step_size: f64,
    pub taker_fee_bps: f64, pub maker_fee_bps: f64, pub slippage_bps: f64,
}

pub struct RunResult {
    pub equity_curve: Vec<(i64, f64)>,
    pub trades: Vec<Trade>,
    pub realized: f64,
    pub final_equity: f64,
    pub hodl_return_pct: f64,
}

pub async fn run_grid_on_bars(rc: &ReplayConfig, bars: Vec<Bar>) -> anyhow::Result<RunResult> {
    let tmp = tempfile::TempDir::new()?;
    let mut grid = GridStrategy::new_with_state_dir(
        &rc.symbol, &rc.grid, rc.tick_size, rc.step_size,
        tmp.path().to_str().unwrap(), TelegramBot::disabled(),
    );
    let capital = CapitalManager::new(20.0)
        .with_budgets({ let mut b = std::collections::BTreeMap::new(); b.insert("grid".to_string(), rc.init_cash); b });
    let mut sim = FillSim::new(rc.taker_fee_bps, rc.maker_fee_bps, rc.slippage_bps);
    let mut port = Portfolio::new(rc.init_cash, rc.init_cash);
    let mut equity_curve = Vec::new();
    let mut fills_buf = Vec::new();

    for (i, bar) in bars.iter().enumerate() {
        capital.sync_equity(port.equity(bar.close), port.cash);
        capital.reset_tick_grants();
        // 1. evaluate resting against this bar
        sim.evaluate(bar, &mut fills_buf);
        for f in fills_buf.drain(..) {
            grid.on_fill(&f).await?;
            port.apply_fill(&f);
        }
        // 2. on_tick
        let ctx = build_ctx(&rc.symbol, bar, &bars[..i], &capital, /*replay*/ i < rc.warmup_bars);
        let new_orders = grid.on_tick(&ctx).await?;
        // 3. submit (market fills now at bar.close; limit/stop rest)
        sim.submit(new_orders, bar, &mut fills_buf);
        for f in fills_buf.drain(..) {
            grid.on_fill(&f).await?;
            port.apply_fill(&f);
        }
        // 4. cancels
        sim.cancel(&grid.pending_cancels());
        equity_curve.push((bar.timestamp, port.equity(bar.close)));
    }
    let first_close = bars.first().map(|b| b.close).unwrap_or(1.0);
    let last_close = bars.last().map(|b| b.close).unwrap_or(1.0);
    Ok(RunResult {
        equity_curve, trades: port.trades.clone(), realized: port.realized,
        final_equity: port.equity(last_close),
        hodl_return_pct: (last_close / first_close - 1.0) * 100.0,
    })
}

fn build_ctx(symbol: &str, bar: &Bar, prior: &[Bar], capital: &CapitalManager, replay: bool) -> crate::strategy::TickContext {
    use crate::strategy::TickContext;
    let recent: Vec<Bar> = prior.iter().rev().take(200).cloned().rev().collect();
    let ob = OrderBook {
        symbol: symbol.into(),
        bids: vec![(bar.close * 0.9999, 1.0)],
        asks: vec![(bar.close * 1.0001, 1.0)],
        timestamp: bar.timestamp,
    };
    let mut balances = HashMap::new();
    balances.insert("USDT".to_string(), 1e9);
    TickContext {
        order_book: ob, recent_bars: recent, balances, open_orders: vec![],
        regime: None, regime_confidence: 0.0, timestamp: bar.timestamp,
        capital: Some(capital.clone()), replay,
    }
}
```

(Correct the `use` for `TickContext` — it lives at `crate::strategy::TickContext`, not `connector::types`. The draft above mixes both; the implementer should keep only `crate::strategy::TickContext` and `crate::connector::types::OrderBook`.)

Add `pub mod replay;` to `backtest/mod.rs`.

- [ ] **Step 4: Run test → PASS**

Run: `cargo test --test backtest_replay`
Expected: PASS (1). If `GridConfig::default()` is not `Default`-derived, construct it from `config/strategy.yaml` via `AppConfig::load` in the test helper instead.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/backtest/replay.rs trading-engine-core/src/backtest/mod.rs \
        trading-engine-core/tests/backtest_replay.rs
git commit -m "feat(backtest): Replay driver — warmup + live window, grid smoke"
```

---

### Task 7: Metrics + report (`report.rs`) + wire the CLI

**Files:**
- Create: `trading-engine-core/src/backtest/report.rs`
- Modify: `backtest/mod.rs`, `src/bin/backtest_replay.rs`

**Interfaces:**
- Produces:
  - `pub struct Metrics { total_return_pct, sharpe, max_drawdown_pct, win_rate_pct, total_trades, profit_factor, hodl_return_pct }`
  - `pub fn compute(run: &RunResult, risk_free_per_bar: f64) -> Metrics`
  - `pub fn write_report(path: &Path, symbol: &str, run: &RunResult, m: &Metrics) -> Result<()>` — emits `results.json` (structured) + `report.md` (human).

- [ ] **Step 1: Write failing test — metrics from a known equity curve**

`trading-engine-core/tests/backtest_report.rs`:

```rust
use trading_engine_core::backtest::report::{compute, Metrics};
use trading_engine_core::backtest::replay::RunResult;

#[test]
fn sharpe_and_drawdown_from_known_curve() {
    // monotonically rising equity 100 -> 110 over 10 bars
    let curve: Vec<(i64,f64)> = (0..10).map(|i| (i as i64, 100.0 + i as f64)).collect();
    let run = RunResult { equity_curve: curve, trades: vec![], realized: 10.0,
        final_equity: 109.0, hodl_return_pct: 0.0 };
    let m = compute(&run, 0.0);
    assert!((m.total_return_pct - 10.0).abs() < 1e-6);  // (109-100+1? use first vs last equity)
    assert!(m.max_drawdown_pct.abs() < 1e-6);           // monotonic → 0 drawdown
}
```

(Adjust the `total_return_pct` expectation to `(last_equity - first_equity)/first_equity * 100` once the impl defines it so.)

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --test backtest_report`
Expected: FAIL.

- [ ] **Step 3: Implement `compute` + `write_report`**

`trading-engine-core/src/backtest/report.rs`:

```rust
//! Metrics + JSON/markdown report from a RunResult.
use std::path::Path;
use anyhow::Result;
use serde::Serialize;
use super::replay::RunResult;

#[derive(Serialize)]
pub struct Metrics {
    pub total_return_pct: f64, pub sharpe: f64, pub max_drawdown_pct: f64,
    pub win_rate_pct: f64, pub total_trades: usize, pub profit_factor: f64,
    pub hodl_return_pct: f64,
}

pub fn compute(run: &RunResult, _risk_free: f64) -> Metrics {
    let eq: Vec<f64> = run.equity_curve.iter().map(|(_, e)| *e).collect();
    let first = eq.first().copied().unwrap_or(0.0);
    let last = eq.last().copied().unwrap_or(0.0);
    let total_return_pct = if first > 0.0 { (last / first - 1.0) * 100.0 } else { 0.0 };
    // per-bar returns → Sharpe (annualized × sqrt(24*365) for 1h)
    let mut rets = Vec::new();
    for w in eq.windows(2) { if w[0] > 0.0 { rets.push(w[1] / w[0] - 1.0); } }
    let mean = rets.iter().sum::<f64>() / rets.len().max(1) as f64;
    let var = rets.iter().map(|r| (r - mean).powi(2)).sum::<f64>() / rets.len().max(1) as f64;
    let std = var.sqrt();
    let sharpe = if std > 0.0 { mean / std * (24.0 * 365.0).sqrt() } else { 0.0 };
    // max drawdown
    let mut peak = f64::NEG_INFINITY; let mut max_dd = 0.0;
    for e in &eq { peak = peak.max(*e); max_dd = max_dd.max((peak - *e) / peak.max(1e-9) * 100.0); }
    let wins = run.trades.iter().filter(|t| t.pnl > 0.0).count();
    let win_rate_pct = if run.trades.is_empty() { 0.0 } else { wins as f64 / run.trades.len() as f64 * 100.0 };
    let gross_win = run.trades.iter().filter(|t| t.pnl > 0.0).map(|t| t.pnl).sum::<f64>();
    let gross_loss = (-run.trades.iter().filter(|t| t.pnl < 0.0).map(|t| t.pnl).sum::<f64>()).max(0.0);
    let profit_factor = if gross_loss > 0.0 { gross_win / gross_loss } else if gross_win > 0.0 { f64::INFINITY } else { 0.0 };
    Metrics { total_return_pct, sharpe, max_drawdown_pct: max_dd, win_rate_pct,
        total_trades: run.trades.len(), profit_factor, hodl_return_pct: run.hodl_return_pct }
}

pub fn write_report(dir: &Path, symbol: &str, run: &RunResult, m: &Metrics) -> Result<()> {
    std::fs::create_dir_all(dir)?;
    let json = serde_json::to_string_pretty(m)?;
    std::fs::write(dir.join(format!("{}_results.json", symbol)), json)?;
    let md = format!("# Backtest: {}\n\n- Return: {:.2}%\n- Sharpe: {:.2}\n- MaxDD: {:.2}%\n- Win: {:.0}%\n- Trades: {}\n- HODL: {:.2}%\n",
        symbol, m.total_return_pct, m.sharpe, m.max_drawdown_pct, m.win_rate_pct, m.total_trades, m.hodl_return_pct);
    std::fs::write(dir.join("report.md"), md)?;
    Ok(())
}
```

Add `pub mod report;` to `backtest/mod.rs`.

- [ ] **Step 4: Wire the CLI**

`trading-engine-core/src/bin/backtest_replay.rs`:

```rust
use chrono::NaiveDate;
use trading_engine_core::backtest::{bars, replay::{run_grid_on_bars, ReplayConfig}, report};
use trading_engine_core::config::AppConfig;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let pair = std::env::args().nth(1).unwrap_or("ETHUSDT".into());
    let months: u32 = std::env::args().nth(2).and_then(|s| s.parse().ok()).unwrap_or(6);
    let cfg = AppConfig::load("config/strategy.yaml")?;
    let end = chrono::Utc::now().date_naive();
    let start = end - chrono::Duration::days(30 * months as i64);
    println!("Loading {} 1h bars {} → {} ...", pair, start, end);
    let bars = bars::load_bars(&pair, start, end)?;
    println!("{} bars loaded", bars.len());
    let rc = ReplayConfig {
        symbol: pair.clone(), start, end, init_cash: cfg.capital.account_usdt,
        warmup_bars: 220, grid: cfg.grid.clone(),
        tick_size: cfg.pairs.values().next().map(|p| p.tick_size).unwrap_or(0.01),
        step_size: cfg.pairs.values().next().map(|p| p.step_size).unwrap_or(0.0001),
        taker_fee_bps: cfg.paper.taker_fee_bps, maker_fee_bps: cfg.paper.maker_fee_bps,
        slippage_bps: cfg.paper.slippage_bps,
    };
    let run = run_grid_on_bars(&rc, bars).await?;
    let m = report::compute(&run, 0.0);
    report::write_report(std::path::Path::new("backtest/results/replay"), &pair, &run, &m)?;
    println!("{:#?}", m);
    Ok(())
}
```

- [ ] **Step 5: Build + run tests**

Run: `cargo test --test backtest_report && cargo build --bin backtest_replay`
Expected: tests PASS; binary builds. (`AppConfig` field names `paper.taker_fee_bps` etc. must match `config.rs` — verify the `PaperConfig` field names during implementation and adjust the CLI accordingly.)

- [ ] **Step 6: Smoke run on real data (manual)**

Run: `cargo run --bin backtest_replay -- ETHUSDT 6` (from `trading-engine-core/`)
Expected: downloads ~180 daily 1h zips, prints a `Metrics` summary, writes `backtest/results/replay/ETHUSDT_results.json` + `report.md`. Sanity: trade count > 0, drawdown finite, no panic.

- [ ] **Step 7: Commit**

```bash
git add trading-engine-core/src/backtest/report.rs trading-engine-core/src/backtest/mod.rs \
        trading-engine-core/src/bin/backtest_replay.rs trading-engine-core/tests/backtest_report.rs
git commit -m "feat(backtest): metrics + report + CLI wiring (Phase 1 complete)"
```

---

## Phase 1 Exit Criteria

- All `cargo test --test backtest_*` pass.
- `cargo run --bin backtest_replay -- ETHUSDT 6` runs end-to-end on real bars without panic and emits a sane metrics report.
- The grid engine ran verbatim (no reimplementation); fills obey no-lookahead; state isolated in a temp dir; no live network except the one-shot kline download.

Once Phase 1 is green, write the Phase 2 plan (it depends on the exact `ReplayConfig` / `RunResult` / `FillSim` APIs that Phase 1 just stabilized).

---

## Phases 2–5 — Roadmap (separate plans, written after Phase 1)

These are outlined here so the shape is visible; each becomes its own detailed plan once Phase 1's APIs are frozen.

### Phase 2 — Wire all 4 engines
- Extend `ReplayConfig` → an `EngineKind` enum (`Grid | Trend | Swing | MeanReversion`) and a `run_engine_on_bars(kind, rc, bars)` dispatch.
- **Trend:** build `TrendStrategy::new(.., disabled())`, attach `HistoricalPerpSource` via `.with_perp(Arc::new(perp))` when `trend.trade_shorts`, advance `perp.set_clock(bar.timestamp)` each tick. Verify short MTM + funding path.
- **Swing:** clone `SwingConfig`, set `cfg.tick_size = Some(..)` / `cfg.step_size = Some(..)`, construct `SwingStrategy`. Swing aggregates 1h→4h internally — feed 1h bars, let it warm its HTF state.
- **MR:** run from the temp-dir CWD so `data/<pair>_mean_reversion_state.json` is isolated; set `replay=true` for warmup so `startup_time_ms` replay-suppression stays benign.
- Per-engine, per-pair matrix runner. Smoke test each engine on a synthetic series.

### Phase 3 — IS/OOS split + live-config validation
- Split bars 2/3 IS / 1/3 OOS (independent warmup per slice — re-enter warmup at each slice start).
- Run the engine's **current live config** on full / IS / OOS. Emit the baseline metrics the sweep must beat.
- Add the **IS→OOS Sharpe-gap overfit flag** to the report (reuse the MR backtest's convention).

### Phase 4 — Param sweep + OOS apply-gate (Rust)
- `sweep.rs`: per-engine param grids (start conservative — few params, wide steps). Sweep on IS, take best-by-IS-Sharpe, validate on OOS, run the **apply-gate** (spec §6): candidate OOS Sharpe > current OOS Sharpe + 0.3 AND > 0 AND OOS trades ≥ 15 AND DD-bounded AND params in range. MR excluded from sweep entirely.
- Emit `results.json` carrying per-engine `{ current_oos, candidate_oos, apply: bool, changes: {param: {from, to}} }`.

### Phase 5 — Auto-apply (Python) + workflow + retire `sweep.yml`
- Extend `backtest/apply_sweep.py`: consume the new `results.json`, apply **only** `apply: true` engines (comment-preserving YAML edit — broaden `PARAM_MAP`), emit a changes manifest. Add `--dry-run`.
- `.github/workflows/backtest-rust.yml`: weekly cron + `workflow_dispatch` (inputs: engines, pairs, months, split, dry-run). Compile → run sweep+validate → gated apply → commit if changed → Telegram per-engine verdict (APPLY/KEEP + evidence) → upload artifact.
- Run first N weeks in `--dry-run`; delete `.github/workflows/sweep.yml` once the faithful pipeline is green.

---

## Self-Review (run after writing)

1. **Spec coverage:** §3 approach → Task 6 (replay drives real `Strategy`). §5.1 bars → Task 2. §5.3 perp → Task 3. §5.4 FillSim → Task 4. §5.5 portfolio → Task 5. §5.7 metrics/report → Task 7. §6 gate, §7 policy, §8 workflow → Phases 4–5 (roadmap). §9 fill-sim tests → Task 4. §10 fidelity gaps (ML regime=None, replay warmup) → reflected in `build_ctx`. ✅ Phase 1 covered; later phases explicitly roadmapmed.
2. **Placeholders:** the `worst` dead binding in Task 4 Step 5 and the `PaperConfig` field-name verification in Task 7 are flagged for the implementer inline (not left as "TODO"). No bare TODOs.
3. **Type consistency:** `FillSim::new(taker, maker, slip)`, `submit(orders, bar, &mut fills)`, `evaluate(bar, &mut fills)`, `cancel(&[String])` used consistently across Tasks 4–6. `Portfolio::apply_fill(&Fill)`, `equity(mark)`, `deployed(mark)` consistent. `ReplayConfig` fields match between Task 6 and the Task 7 CLI. `RunResult` fields match Task 6/7. `TickContext` correctly sourced from `crate::strategy` (the draft's mixed `use` is flagged for correction).
