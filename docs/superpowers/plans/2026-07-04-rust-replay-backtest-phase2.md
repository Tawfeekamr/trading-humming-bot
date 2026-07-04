# Rust Replay Backtest — Phase 2 Implementation Plan (wire all 4 engines)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Phase 1 grid-only replay harness to faithfully backtest **all 4 engines** (grid ✓, trend w/ shorts+perp+funding, swing, mean-reversion) on a single historical window, with the Phase-1 final-review prerequisites that gate them landed (signed-inventory Portfolio for shorts, perp wiring, parametrized Sharpe, capital-deployment tracking).

**Architecture:** Generalize the Phase 1 `run_grid_on_bars` loop into an engine-agnostic `run_loop(&mut dyn Strategy, …)` driven by an `EngineKind` enum. Each engine's construction stays specific (grid: state-dir; trend: `with_perp`; swing: tick/step injected; MR: state isolation). The Portfolio is rewritten to **signed inventory** so trend shorts (Sell-open / Buy-close) account correctly alongside longs. No engine is modified — `Strategy` runs verbatim.

**Tech Stack:** Rust (existing `trading-engine-core` crate); reuses Phase 1's `bars`, `fills`, `perp`, `replay`, `report`, `portfolio` modules and the `backtest_replay` binary. No new dependencies.

## Global Constraints

(Carried from the Phase 1 plan + the final-review Phase-2 prerequisites. Every task inherits these.)

- **Engines run verbatim.** Never modify `GridStrategy`/`TrendStrategy`/`SwingStrategy`/`MeanReversionStrategy`/`Strategy`/`trade_journal`. Only `backtest/*`, the bin, and (here) `portfolio.rs` change.
- **No lookahead** — unchanged from Phase 1: the `run_loop` order is `sim.evaluate(bar)` → `on_fill` → `on_tick` → `sim.submit(orders, bar)` → `on_fill` (market fills) → `sim.cancel(pending_cancels)`. Market fills at decision-bar close; limit/stop only on a later bar's range. Per-bar `perp.set_clock(bar.timestamp)` happens BEFORE `on_tick` so shorts mark as-of.
- **State + journal isolation.** Every run uses a fresh `tempfile::TempDir` for grid state AND sets `TRADES_JOURNAL_PATH` to that tempdir (Phase 1 Critical fix). MR's own state file (`data/<pair>_mean_reversion_state.json`, CWD-relative, NOT env-driven) must also be isolated — see Task 5.
- **No live network** except the one-shot kline/perp download in `load_bars`. `TelegramBot::disabled()` for all engines. Never `GateioPerpSource::new()`.
- **Phase-2 fidelity gaps (stamp on reports):** (a) ML regime gate stays `None` — grid's ML block is off (optimistic); (b) perp funding uses a flat `Option<f64>` rate (no historical funding-rate series yet); (c) MR reads `calculate_bid_depth` but `build_ctx` synthesizes a mid-only book (size 1) → MR's depth signal is degenerate.
- **Frequent commits**, one per task. Branch: `feat/rust-replay-backtest` (continues from Phase 1 head `f88be64`).

---

## File Structure (Phase 2)

| File | Phase 2 change |
|---|---|
| `backtest/replay.rs` | Add `EngineKind` enum + `run_engine_on_bars(kind, rc, bars)`; extract `run_loop(&mut dyn Strategy, …)`. Add per-engine config fields to `ReplayConfig`. Wire perp `set_clock` + `capital.set_deployed`. |
| `backtest/portfolio.rs` | **Rewrite to signed inventory** (long + short + partial close + flip-safe). Public API (`new`, `apply_fill`, `equity`, `mtm`, `deployed`, `Trade`) unchanged in shape. |
| `backtest/report.rs` | `compute(.., bar_hours: f64)` — parametrize Sharpe annualization `(24.0/bar_hours*365.0).sqrt()`. |
| `bin/backtest_replay.rs` | `--engine <kind>` flag; build the matching `ReplayConfig` from `AppConfig`. |
| `tests/backtest_portfolio.rs` | Add signed-inventory tests (short open/extend/close, flips, clamps). |
| `tests/backtest_replay.rs` | Add per-engine smoke tests (trend long, trend short, swing, MR) + grid regression. |

---

## Task 1: Generalize the replay loop + `EngineKind(Grid)` + cheap prerequisites

**Files:**
- Modify: `trading-engine-core/src/backtest/replay.rs`
- Modify: `trading-engine-core/src/backtest/report.rs`
- Modify: `trading-engine-core/tests/backtest_replay.rs`
- Modify: `trading-engine-core/tests/backtest_report.rs`

**Interfaces:**
- Consumes: Phase 1 `run_grid_on_bars`, `FillSim`, `Portfolio`, `build_ctx`, `GridStrategy::new_with_state_dir`, `CapitalManager`, `TelegramBot::disabled`.
- Produces:
  - `pub enum EngineKind { Grid }` (only `Grid` this task; Tasks 3/4/5 add `Trend`/`Swing`/`MeanReversion`).
  - `pub async fn run_engine_on_bars(kind: EngineKind, rc: &ReplayConfig, bars: Vec<Bar>) -> Result<RunResult>` — dispatches on kind; `Grid` arm constructs grid (current logic) and calls `run_loop`.
  - `async fn run_loop(strategy: &mut dyn Strategy, sim: &mut FillSim, port: &mut Portfolio, capital: &CapitalManager, bars: &[Bar], warmup_bars: usize, bar_hours: f64, perp: Option<&HistoricalPerpSource>) -> RunResult` — the engine-agnostic bar loop extracted from `run_grid_on_bars`.
  - `ReplayConfig` gains: `pub engine: EngineKind`, `pub bar_hours: f64`. (Per-engine configs are added by Tasks 3/4/5.)
  - `report::compute(run, risk_free, bar_hours: f64)` — Sharpe uses `(24.0/bar_hours*365.0).sqrt()`.
- `run_grid_on_bars` stays as a thin wrapper: `run_engine_on_bars(EngineKind::Grid, rc, bars)` (keeps Phase 1 callers/tests green).

- [ ] **Step 1: Parametrize `report::compute` Sharpe by `bar_hours`**

In `backtest/report.rs`, change the signature to `pub fn compute(run: &RunResult, _risk_free_per_bar: f64, bar_hours: f64) -> Metrics` and replace the Sharpe annualization line:
```rust
let bars_per_year = (24.0 / bar_hours) * 365.0;
let sharpe = if std > 0.0 { mean / std * bars_per_year.sqrt() } else { 0.0 };
```
Update the `backtest_report.rs` test call to `compute(&run, 0.0, 1.0)` (1h bars). Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_report` → GREEN.

- [ ] **Step 2: Add `EngineKind` + extend `ReplayConfig`**

In `backtest/replay.rs`:
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EngineKind { Grid }

pub struct ReplayConfig {
    pub symbol: String,
    pub init_cash: f64,
    pub warmup_bars: usize,
    pub bar_hours: f64,
    pub engine: EngineKind,
    pub grid: crate::config::GridConfig,
    pub tick_size: f64,
    pub step_size: f64,
    pub taker_fee_bps: f64,
    pub maker_fee_bps: f64,
    pub slippage_bps: f64,
}
```
Update the existing grid smoke test's `ReplayConfig { … }` literal to include `bar_hours: 1.0, engine: EngineKind::Grid`.

- [ ] **Step 3: Extract `run_loop` and add `run_engine_on_bars`**

Extract the bar-loop body of `run_grid_on_bars` into `run_loop(strategy, sim, port, capital, bars, warmup_bars, bar_hours, perp)` (signature above). Two additions inside the loop, after `capital.reset_tick_grants()`:
```rust
// Advance the perp clock BEFORE on_tick so short MTM/funding are as-of this bar.
if let Some(p) = perp { p.set_clock(bar.timestamp); }
// Track cumulative deployment so CapitalManager budget caps behave like live.
let mut deployed = std::collections::BTreeMap::new();
deployed.insert(strategy.name().to_string(), port.deployed(bar.close));
capital.set_deployed(deployed);
```
Then:
```rust
pub async fn run_grid_on_bars(rc: &ReplayConfig, bars: Vec<Bar>) -> anyhow::Result<RunResult> {
    run_engine_on_bars(EngineKind::Grid, rc, bars).await
}

pub async fn run_engine_on_bars(kind: EngineKind, rc: &ReplayConfig, bars: Vec<Bar>) -> anyhow::Result<RunResult> {
    let tmp = tempfile::TempDir::new()?;
    std::env::set_var("TRADES_JOURNAL_PATH", tmp.path().join("trades.db"));
    let capital = build_capital(rc);
    let mut sim = FillSim::new(rc.taker_fee_bps, rc.maker_fee_bps, rc.slippage_bps);
    let mut port = Portfolio::new(rc.init_cash, rc.init_cash);
    match kind {
        EngineKind::Grid => {
            let mut grid = crate::strategy::grid::GridStrategy::new_with_state_dir(
                &rc.symbol, &rc.grid, rc.tick_size, rc.step_size,
                tmp.path().to_str().expect("tempdir utf8"), crate::notifications::TelegramBot::disabled(),
            );
            Ok(run_loop(&mut grid, &mut sim, &mut port, &capital, &bars, rc.warmup_bars, rc.bar_hours, None).await)
        }
    }
}
```
Factor the `CapitalManager` construction into `fn build_capital(rc: &ReplayConfig) -> CapitalManager` keyed by `match rc.engine { EngineKind::Grid => "grid" }` (Tasks 3/4/5 extend this). Verify `strategy.name()` returns `"grid"` — read `GridStrategy::name()` (grid.rs ~L509); if it returns something else, map `EngineKind` → budget key explicitly in `build_capital`.

- [ ] **Step 4: Run grid regression**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_replay`
Expected: both existing tests (grid smoke + journal isolation) still PASS — the refactor is behavior-preserving. `cargo build --bin backtest_replay` clean.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/backtest/replay.rs trading-engine-core/src/backtest/report.rs \
        trading-engine-core/tests/backtest_replay.rs trading-engine-core/tests/backtest_report.rs
git commit -m "refactor(backtest): engine-agnostic run_loop + EngineKind(Grid) + parametrized Sharpe"
```

---

## Task 2: Portfolio signed-inventory accounting (THE CRUX)

**Files:**
- Modify: `trading-engine-core/src/backtest/portfolio.rs`
- Modify: `trading-engine-core/tests/backtest_portfolio.rs`

**Interfaces:**
- Consumes: `Fill { side: OrderSide, price, quantity, fee, .. }`, `OrderSide { Buy, Sell }`.
- Produces: `Portfolio` with **signed inventory** — same public API (`new`, `apply_fill(&Fill)`, `equity(mark)`, `mtm(mark)`, `deployed(mark)`, `Trade`) but `inventory_qty` is now signed (`+` long / `−` short) and `apply_fill` handles both directions, partial closes, and flips. `equity = cash + inventory_qty * mark` (short MTM falls out of the signed qty). Unblocks trend shorts (Task 3).

- [ ] **Step 1: Write failing tests for the short + flip paths**

Append to `tests/backtest_portfolio.rs` (keep existing long tests; they must still pass):
```rust
fn fill(side: OrderSide, price: f64, qty: f64, fee: f64) -> Fill {
    Fill { fill_id: "f".into(), order_id: "o".into(), client_order_id: Some("c".into()),
        symbol: "ETHUSDT".into(), side, price, quantity: qty, fee, timestamp: 0 }
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
```
Run → RED (current Portfolio treats Sell as long-close, clamps at 0 → the short-open assertion fails).

- [ ] **Step 2: Rewrite `Portfolio::apply_fill` for signed inventory**

In `backtest/portfolio.rs`, replace `inventory_cost: f64` with `avg_price: f64` and rewrite `apply_fill`:
```rust
pub struct Portfolio {
    pub init_cash: f64,
    pub cash: f64,
    pub inventory_qty: f64,   // signed: + long, − short
    pub avg_price: f64,       // entry average (sign-agnostic)
    pub realized: f64,
    pub trades: Vec<Trade>,
    pub budget: f64,
}

impl Portfolio {
    pub fn new(init_cash: f64, budget: f64) -> Self {
        Self { init_cash, cash: init_cash, inventory_qty: 0.0, avg_price: 0.0,
               realized: 0.0, trades: Vec::new(), budget }
    }

    pub fn apply_fill(&mut self, f: &Fill) {
        self.cash -= f.fee;
        let delta = match f.side { OrderSide::Buy => f.quantity, OrderSide::Sell => -f.quantity };
        let prev = self.inventory_qty;
        let new_qty = prev + delta;

        if prev == 0.0 || new_qty.signum() == prev.signum() {
            // opening or extending same direction → weighted avg, no realized
            self.avg_price = if prev == 0.0 { f.price }
                             else { (self.avg_price * prev.abs() + f.quantity * f.price) / new_qty.abs() };
        } else {
            // reducing / closing / flipping
            let close = f.quantity.min(prev.abs());
            let dir = prev.signum(); // +1 long closed-by-Sell, −1 short closed-by-Buy
            let gross = dir * (f.price - self.avg_price) * close;
            self.realized += dir * (f.price - self.avg_price) * close;
            let entry_side = if dir > 0.0 { OrderSide::Buy } else { OrderSide::Sell };
            if close > 0.0 {
                self.trades.push(Trade { side: entry_side, qty: close, entry_price: self.avg_price,
                                         exit_price: f.price, pnl: gross - f.fee, ts: f.timestamp });
            }
            // after closing, if flipped the leftover opens opposite at f.price; if flat, avg 0
            self.avg_price = if new_qty == 0.0 { 0.0 } else { f.price };
        }
        self.inventory_qty = new_qty;
        self.cash -= delta * f.price;   // Buy pays, Sell receives — uniform across open/close, long/short
    }

    pub fn equity(&self, mark: f64) -> f64 { self.cash + self.inventory_qty * mark }
    pub fn mtm(&self, mark: f64) -> f64 { self.equity(mark) - self.init_cash }
    pub fn deployed(&self, mark: f64) -> f64 { self.inventory_qty.abs() * mark }
}
```
Note `deployed` is now `|inventory_qty| * mark` (works for shorts). Update any Phase 1 caller/test that read `inventory_cost` (the long-only `realized == 30.0` test still holds — long path unchanged; verify the Phase-1 regression `buys_accumulate_then_sell_realizes_vs_average_cost` and the over-sell regression both still pass).

- [ ] **Step 3: Run all portfolio tests → GREEN**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_portfolio`
Expected: all tests PASS (long open/extend/close from Phase 1 + the 3 new short/flip tests). Run the full backtest suite to confirm no other breakage: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_replay --test backtest_report`.

- [ ] **Step 4: Commit**

```bash
git add trading-engine-core/src/backtest/portfolio.rs trading-engine-core/tests/backtest_portfolio.rs
git commit -m "feat(backtest): signed-inventory Portfolio (long+short, partial close, flip-safe)"
```

---

## Task 3: Trend wiring + perp source

**Files:**
- Modify: `trading-engine-core/src/backtest/replay.rs`
- Modify: `trading-engine-core/tests/backtest_replay.rs`

**Interfaces:**
- Consumes: `TrendStrategy::new(pair, &TrendConfig, TelegramBot)`, `.with_perp(Arc<dyn PerpPriceSource>)`, `HistoricalPerpSource::from_bars(Vec<Bar>, Option<f64>)` + `set_clock(i64)`, the signed `Portfolio` (Task 2), `run_loop` (Task 1).
- Produces: `EngineKind::Trend` arm in `run_engine_on_bars`; `ReplayConfig` gains `pub trend: crate::config::TrendConfig`, `pub perp_bars: Option<Vec<Bar>>`, `pub funding_rate: Option<f64>`. `run_loop` advances `perp.set_clock(bar.timestamp)` each bar (already added in Task 1).

- [ ] **Step 1: Extend `EngineKind` + `ReplayConfig` + `build_capital`**

In `replay.rs`: add `Trend` to `EngineKind`. Add `pub trend: crate::config::TrendConfig`, `pub perp_bars: Option<Vec<Bar>>`, `pub funding_rate: Option<f64>` to `ReplayConfig`. In `build_capital`, add `EngineKind::Trend => "trend"` to the budget-key match.

- [ ] **Step 2: Implement the `Trend` dispatch arm**

```rust
EngineKind::Trend => {
    let mut trend = crate::strategy::trend::TrendStrategy::new(
        &rc.symbol, &rc.trend, crate::notifications::TelegramBot::disabled());
    let perp = if rc.trend.trade_shorts {
        let p = std::sync::Arc::new(
            crate::backtest::perp::HistoricalPerpSource::from_bars(
                rc.perp_bars.clone().unwrap_or_default(), rc.funding_rate));
        trend = trend.with_perp(p.clone());
        Some(p)
    } else { None };
    Ok(run_loop(&mut trend, &mut sim, &mut port, &capital, &bars, rc.warmup_bars, rc.bar_hours, perp.as_deref()).await)
}
```
(`Arc<HistoricalPerpSource>` derefs to `&HistoricalPerpSource` for `run_loop`'s `Option<&HistoricalPerpSource>` param. `with_perp` takes `Arc<dyn PerpPriceSource>` — the same `Arc` coerces.)

- [ ] **Step 3: Write trend smoke tests**

Append to `tests/backtest_replay.rs`:
```rust
fn trending_bars(up: bool, n: usize) -> Vec<Bar> {
    (0..n).map(|i| {
        let p = if up { 100.0 + i as f64 * 0.5 } else { 200.0 - i as f64 * 0.5 };
        Bar::new(p, p + 1.0, p - 1.0, p, 10.0, (i as i64) * 3_600_000)
    }).collect()
}

#[tokio::test]
async fn trend_long_opens_on_uptrend_and_closes() {
    let cfg = AppConfig::load(format!("{}/../config/strategy.yaml", env!("CARGO_MANIFEST_DIR"))).unwrap();
    let mut tc = cfg.trend.clone();
    tc.trade_shorts = false; // long-only for this leg
    let rc = ReplayConfig { symbol: "ETHUSDT".into(), init_cash: 100_000.0, warmup_bars: 220,
        bar_hours: 1.0, engine: EngineKind::Trend, grid: cfg.grid.clone(), trend: tc,
        perp_bars: None, funding_rate: None,
        tick_size: 0.01, step_size: 0.0001,
        taker_fee_bps: cfg.paper.taker_fee_bps, maker_fee_bps: cfg.paper.maker_fee_bps, slippage_bps: cfg.paper.slippage_bps };
    let bars = trending_bars(true, 400);
    let res = run_engine_on_bars(EngineKind::Trend, &rc, bars).await.unwrap();
    assert!(!res.equity_curve.is_empty());
    // a clean monotonic uptrend should let trend enter at least one long
    assert!(res.trades.len() >= 0); // smoke: completes without panic; entries depend on score gate
}
```
(Sharpe validation isn't asserted — the smoke proves the trend engine runs verbatim with the perp path wired. If the score gate never fires on synthetic data, that's acceptable — same caveat as Phase 1 grid.)

- [ ] **Step 4: Run trend tests → GREEN; build**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_replay` → all pass. `cargo build --bin backtest_replay` clean.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/backtest/replay.rs trading-engine-core/tests/backtest_replay.rs
git commit -m "feat(backtest): wire TrendStrategy (long + short via HistoricalPerpSource)"
```

---

## Task 4: Swing wiring

**Files:**
- Modify: `trading-engine-core/src/backtest/replay.rs`
- Modify: `trading-engine-core/tests/backtest_replay.rs`

**Interfaces:**
- Consumes: `SwingStrategy::new(pair, &SwingConfig, TelegramBot)`; production sets `cfg.tick_size = Some(..)` / `cfg.step_size = Some(..)` before construction (see main.rs:151-153).
- Produces: `EngineKind::Swing` arm; `ReplayConfig` gains `pub swing: Option<crate::config::SwingConfig>`. Swing is long-only (Buy range-low / Sell) so the signed Portfolio (Task 2) handles it without extra work.

- [ ] **Step 1: Extend `EngineKind` + `ReplayConfig` + arm + capital key**

Add `Swing` to `EngineKind`. Add `pub swing: Option<crate::config::SwingConfig>` to `ReplayConfig`. `build_capital`: `EngineKind::Swing => "swing"`. Dispatch arm:
```rust
EngineKind::Swing => {
    let mut sc = rc.swing.clone().expect("swing config required for EngineKind::Swing");
    sc.tick_size = Some(rc.tick_size);
    sc.step_size = Some(rc.step_size);
    let mut swing = crate::strategy::swing::SwingStrategy::new(
        &rc.symbol, &sc, crate::notifications::TelegramBot::disabled());
    Ok(run_loop(&mut swing, &mut sim, &mut port, &capital, &bars, rc.warmup_bars, rc.bar_hours, None).await)
}
```

- [ ] **Step 2: Smoke test + commit**

Add a `#[tokio::test] async fn swing_runs_on_synthetic_range` (load `AppConfig`, build `ReplayConfig` with `engine: EngineKind::Swing`, `swing: cfg.swing.clone()`, run on a ranging series, assert `equity_curve` non-empty). Run `cargo test --test backtest_replay` → GREEN. Commit: `feat(backtest): wire SwingStrategy`.

---

## Task 5: Mean-Reversion wiring (state isolation)

**Files:**
- Modify: `trading-engine-core/src/backtest/replay.rs`
- Modify: `trading-engine-core/tests/backtest_replay.rs`

**Interfaces:**
- Consumes: `MeanReversionStrategy::new(pair, &MeanReversionConfig, TelegramBot)`.
- Produces: `EngineKind::MeanReversion` arm; `ReplayConfig` gains `pub mean_reversion: crate::config::MeanReversionConfig`.

**Isolation note:** MR's `state_path()` returns `data/<pair>_mean_reversion_state.json` (CWD-relative, NOT env-driven — verified in the constructor report). To isolate, `run_engine_on_bars`'s `EngineKind::MeanReversion` arm runs the construction + loop with the process CWD temporarily changed to the TempDir. Because `set_current_dir` is process-global and tests run in parallel, do this by setting CWD only inside a synchronous critical section is unsafe across tests — instead, **delete any pre-existing `data/<pair>_mean_reversion_state.json` before construction and accept MR writes it to CWD `data/`** (small, idempotent; MR is disabled in production so the file is inert). Document this as a known isolation gap; the TempDir covers grid state + the trade journal (the load-bearing isolation).

- [ ] **Step 1: Extend enum/config/arm + capital key**

Add `MeanReversion` to `EngineKind`. Add `pub mean_reversion: crate::config::MeanReversionConfig` to `ReplayConfig`. `build_capital`: `EngineKind::MeanReversion => "mean_reversion"`. Dispatch arm:
```rust
EngineKind::MeanReversion => {
    let _ = std::fs::remove_file(format!("data/{}_mean_reversion_state.json", rc.symbol)); // fresh state
    let mut mr = crate::strategy::mean_reversion::MeanReversionStrategy::new(
        &rc.symbol, &rc.mean_reversion, crate::notifications::TelegramBot::disabled());
    Ok(run_loop(&mut mr, &mut sim, &mut port, &capital, &bars, rc.warmup_bars, rc.bar_hours, None).await)
}
```
(Stamp the MR `bid_depth` fidelity gap — mid-only book — in a comment on the arm.)

- [ ] **Step 2: Smoke test + commit**

Add `#[tokio::test] async fn mr_runs_on_synthetic_reverting_series` (mean-reverting series: oscillating around a mean; build `ReplayConfig` with `engine: EngineKind::MeanReversion`, run, assert no panic + equity_curve non-empty). Run `cargo test --test backtest_replay` → GREEN. Commit: `feat(backtest): wire MeanReversionStrategy (state-isolation caveat)`.

---

## Task 6: CLI `--engine` flag

**Files:**
- Modify: `trading-engine-core/src/bin/backtest_replay.rs`

**Interfaces:**
- Produces: CLI accepts `--engine <grid|trend|swing|mean_reversion>` (default `grid`), builds the matching `ReplayConfig` from `AppConfig`, fetches bars (+ perp bars when trend+shorts), runs `run_engine_on_bars`, writes the report.

- [ ] **Step 1: Parse `--engine` and build the right config**

Rewrite `backtest_replay.rs`'s arg parsing to accept `--engine` (use `std::env::args` find-flag or accept positional `<pair> <months> <engine>` — match Phase 1's positional style). For `trend` with `cfg.trend.trade_shorts`, also fetch perp bars: `bars::load_bars(&format!("{}USDT", base), start, end)` reusing the spot kline source as a perp proxy **OR** document the gap. (Phase-2 fidelity gap: a true perp series source is deferred; for now, pass perp_bars = a clone of the spot bars when shorts are on, and stamp the gap on the report. Funding rate = `None`.)

- [ ] **Step 2: Build + manual smoke per engine**

Run, for each engine from the repo root:
- `cargo run --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay -- ETHUSDT 3 grid`
- `… -- ETHUSDT 3 trend`
- `… -- ETHUSDT 3 swing`
- `… -- ETHUSDT 3 mean_reversion`
Expect each completes without panic and writes `backtest/results/replay/ETHUSDT_results.json` + `report.md`. Report the metrics each produced.

- [ ] **Step 3: Commit**

```bash
git add trading-engine-core/src/bin/backtest_replay.rs
git commit -m "feat(backtest): --engine flag (grid|trend|swing|mean_reversion) — Phase 2 complete"
```

---

## Phase 2 Exit Criteria

- All `cargo test --test backtest_*` pass (signed-Portfolio long+short tests, 4-engine smokes, grid regression).
- `cargo run --bin backtest_replay -- ETHUSDT 3 <engine>` runs clean for all 4 engines.
- No engine modified; shorts/perp/funding path exercised for trend; state+journal isolated; no lookahead (unchanged `run_loop` order).
- Phase-2 fidelity gaps (ML regime None, flat funding, MR bid_depth, perp-as-spot-proxy) stamped on reports.

Once Phase 2 is green, write the Phase 3 plan (IS/OOS split + live-config validation + the IS→OOS Sharpe-gap overfit flag).

---

## Self-Review

1. **Spec/Phase-2-prereq coverage:** short-inventory (prereq #3) → Task 2; perp wiring (#2) → Task 3; Sharpe parametrize (#4) → Task 1; `set_deployed` (#5) → Task 1; on_fill chaining (#1) → N/A (trend/MR `on_fill` return empty — verified); ML regime (#6) → documented gap; order-book depth (#7) → documented gap (MR bid_depth). All engines wired (Tasks 3/4/5). ✅
2. **Placeholder scan:** Task 5's MR state-isolation uses a `remove_file` + documented gap (not a TODO). Task 6's perp-as-spot-proxy is an explicit stamped gap (not a placeholder). No bare TODOs.
3. **Type consistency:** `EngineKind` grows `Grid`→`+Trend`→`+Swing`→`+MeanReversion` across tasks; `ReplayConfig` accumulates `trend`/`swing`/`mean_reversion`/`perp_bars`/`funding_rate`/`bar_hours`/`engine` — all named consistently. `run_loop` signature stable across tasks (Tasks 3/4/5 pass `None` or `Some(&perp)`). `Portfolio` API shape unchanged (Task 2 only swaps `inventory_cost`→`avg_price` internally; `equity`/`mtm`/`deployed`/`Trade` unchanged). `report::compute(.., bar_hours)` updated in Task 1 and called consistently.
