# Rust Replay Backtest — Phase 3 Implementation Plan (IS/OOS validation)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add in-sample / out-of-sample validation to the harness: run each 1h engine's *current live config* on the full window, the IS slice, and the OOS slice, and emit an honest report with the IS→OOS Sharpe-gap overfit flag — the baseline evidence the Phase-4 sweep must beat.

**Architecture:** A new `backtest/validation.rs` module splits the bar series 2/3 IS / 1/3 OOS, runs the existing `run_engine_on_bars` on each slice (warmup is already handled per-run), computes `Metrics` per slice, and packages a `ValidationReport` (full/IS/OOS metrics + Sharpe gap + overfit flag + fidelity-gap stamps). `report.rs` gains a `write_validation_report` markdown emitter. The CLI gains a `--validate` flag. No engine changes.

**Tech Stack:** Rust (existing `trading-engine-core` crate); reuses Phase 1-2 `bars`, `replay::{run_engine_on_bars, ReplayConfig, RunResult, EngineKind}`, `report::{compute, Metrics}`. No new dependencies.

## Scope refinement (important — read first)

**MR is excluded from this harness's validation.** MR's `on_tick` (mean_reversion.rs:178-188) maintains a **30-second** `tick_history` window and detects a "-5% drop in 30s" flash crash — it is a *tick-resolution* strategy. On 1h bars the signal is structurally undetectable (every bar ages out instantly), so an MR run on this harness is meaningless regardless of the wall-clock gate. MR's faithful backtest is the **existing** tick-replay at `backtest/mean_reversion/` (already validated no-edge; MR is disabled in production). Phase 3 validates only the **three 1h engines: grid, trend, swing**. (Trend runs long+short; the perp-as-spot-proxy + flat-funding gaps from Phase 2 still apply and are stamped on every report.)

## Global Constraints

(Carried from the spec + Phase 1-2. Every task inherits these.)

- **Engines run verbatim.** No engine/trait/portfolio/fills changes. Only `backtest/validation.rs` (new), `backtest/report.rs`, the bin, and tests change.
- **No lookahead** — each slice is a self-contained `run_engine_on_bars` call; the existing per-run warmup (first `warmup_bars` of the slice, `replay=true`) applies. Indicators warm independently per slice (conservative isolation — a slice's live window never sees the other slice's bars).
- **IS/OOS split:** 2/3 in-sample, 1/3 out-of-sample (the spec's default). The split is a clean contiguous partition (no shared bars).
- **Overfit flag:** IS→OOS Sharpe gap > 1.0 → "⚠️ overfit?" (mirrors the existing `backtest/mean_reversion/` convention).
- **Fidelity gaps stamped on every validation report:** regime=None (grid/trend ML gate off — optimistic), perp-as-spot-proxy + flat funding (trend shorts approximate), MR excluded (tick-resolution).
- **State + journal isolation** unchanged (each `run_engine_on_bars` call owns its TempDir + TRADES_JOURNAL_PATH).
- Run cargo with `--manifest-path trading-engine-core/Cargo.toml`. Frequent commits, one per task. Branch: `feat/rust-replay-backtest`.

---

## File Structure (Phase 3)

| File | Phase 3 change |
|---|---|
| `backtest/validation.rs` (new) | `split_is_oos`, `ValidationReport`, `run_validation` |
| `backtest/mod.rs` | `pub mod validation;` |
| `backtest/report.rs` | `write_validation_report` (markdown IS/OOS table + overfit flag + gap stamps) |
| `bin/backtest_replay.rs` | `--validate` flag → calls `run_validation`, writes the validation report |
| `tests/backtest_validation.rs` (new) | split + run_validation unit/smoke tests |

---

## Task 1: `split_is_oos` bar partition

**Files:**
- Create: `trading-engine-core/src/backtest/validation.rs`
- Modify: `trading-engine-core/src/backtest/mod.rs` (add `pub mod validation;`)
- Test: `trading-engine-core/tests/backtest_validation.rs`

**Interfaces:**
- Consumes: `Bar` (`trading_engine_core::models::bar::Bar`).
- Produces: `pub fn split_is_oos(bars: &[Bar], oos_frac: f64) -> (Vec<Bar>, Vec<Bar>)` — contiguous partition; IS = first `1 - oos_frac`, OOS = remainder. No shared bars. Empty input → two empty vecs.

- [ ] **Step 1: Write the failing test**

`tests/backtest_validation.rs`:
```rust
use trading_engine_core::backtest::validation::split_is_oos;
use trading_engine_core::models::bar::Bar;

fn bars(n: usize) -> Vec<Bar> {
    (0..n).map(|i| Bar::new(100.0, 101.0, 99.0, 100.0, 1.0, i as i64 * 3_600_000)).collect()
}

#[test]
fn split_is_two_thirds_one_third_contiguous_no_overlap() {
    let b = bars(300);
    let (is_b, oos_b) = split_is_oos(&b, 1.0 / 3.0);
    assert_eq!(is_b.len(), 200);
    assert_eq!(oos_b.len(), 100);
    // contiguous: IS ends where OOS begins
    assert_eq!(is_b.last().unwrap().timestamp, 199 * 3_600_000);
    assert_eq!(oos_b.first().unwrap().timestamp, 200 * 3_600_000);
    assert!(oos_b.last().unwrap().timestamp > is_b.last().unwrap().timestamp);
}

#[test]
fn split_empty_input_returns_two_empty_vecs() {
    let (is_b, oos_b) = split_is_oos(&[], 1.0 / 3.0);
    assert!(is_b.is_empty() && oos_b.is_empty());
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_validation`
Expected: FAIL — `cannot find function split_is_oos` / module not found.

- [ ] **Step 3: Implement `split_is_oos` + module**

`backtest/validation.rs`:
```rust
//! In-sample / out-of-sample validation for the 1h engines.
use crate::models::bar::Bar;

/// Contiguous IS/OOS split. IS = first (1 - oos_frac) of bars, OOS = the rest.
/// Empty input → two empty vecs. No shared bars.
pub fn split_is_oos(bars: &[Bar], oos_frac: f64) -> (Vec<Bar>, Vec<Bar>) {
    if bars.is_empty() {
        return (Vec::new(), Vec::new());
    }
    let split = (bars.len() as f64 * (1.0 - oos_frac.clamp(0.0, 1.0))).round() as usize;
    let split = split.min(bars.len());
    let (is_b, oos_b) = bars.split_at(split);
    (is_b.to_vec(), oos_b.to_vec())
}
```
Add `pub mod validation;` to `backtest/mod.rs`.

- [ ] **Step 4: Run test → GREEN**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_validation`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/backtest/validation.rs trading-engine-core/src/backtest/mod.rs \
        trading-engine-core/tests/backtest_validation.rs
git commit -m "feat(backtest): split_is_oos bar partition (Phase 3 Task 1)"
```

---

## Task 2: `run_validation` — full/IS/OOS runner

**Files:**
- Modify: `trading-engine-core/src/backtest/validation.rs`
- Modify: `trading-engine-core/tests/backtest_validation.rs`

**Interfaces:**
- Consumes: `run_engine_on_bars(kind, rc, bars) -> Result<RunResult>` (Phase 2), `report::compute(run, risk_free, bar_hours) -> Metrics`, `ReplayConfig`, `EngineKind`.
- Produces:
  - `pub struct ValidationReport { pub full: Metrics, pub is_metrics: Metrics, pub oos: Metrics, pub is_oos_sharpe_gap: f64, pub overfit_suspect: bool }`
  - `pub async fn run_validation(kind: EngineKind, rc: &ReplayConfig, bars: Vec<Bar>, oos_frac: f64, bar_hours: f64) -> anyhow::Result<ValidationReport>` — runs the engine on full / IS / OOS (three `run_engine_on_bars` calls), computes Metrics each, derives the Sharpe gap + overfit flag.

- [ ] **Step 1: Write the failing test**

Append to `tests/backtest_validation.rs`:
```rust
use trading_engine_core::backtest::validation::{run_validation, ValidationReport};
use trading_engine_core::backtest::replay::{ReplayConfig, EngineKind};
use trading_engine_core::config::AppConfig;

fn rc_for(grid_cfg: trading_engine_core::config::GridConfig) -> ReplayConfig {
    ReplayConfig {
        symbol: "ETHUSDT".into(), init_cash: 100_000.0, warmup_bars: 50, bar_hours: 1.0,
        engine: EngineKind::Grid, grid: grid_cfg,
        trend: trading_engine_core::config::TrendConfig::default(),
        swing: None,
        mean_reversion: trading_engine_core::config::MeanReversionConfig::default(),
        perp_bars: None, funding_rate: None,
        tick_size: 0.01, step_size: 0.0001,
        taker_fee_bps: 10.0, maker_fee_bps: 10.0, slippage_bps: 0.0,
    }
}

#[tokio::test]
async fn run_validation_produces_three_metric_sets_and_gap() {
    let cfg = AppConfig::load(format!("{}/../config/strategy.yaml", env!("CARGO_MANIFEST_DIR"))).unwrap();
    let rc = rc_for(cfg.grid.clone());
    // 300-bar ranging sawtooth — same generator the grid smoke uses (trades on hospitable data).
    let bars: Vec<_> = (0..300).map(|i| {
        let p = 100.0 + ((i % 8) as f64 / 2.0);
        Bar::new(p, p + 1.0, p - 1.0, p, 10.0, (i as i64) * 3_600_000)
    }).collect();
    let rep: ValidationReport = run_validation(EngineKind::Grid, &rc, bars, 1.0/3.0, 1.0).await.unwrap();
    // all three slices produce a Metrics (full/IS/OOS); gap is IS_sharpe - OOS_sharpe
    assert!((rep.is_oos_sharpe_gap - (rep.is_metrics.sharpe - rep.oos.sharpe)).abs() < 1e-9);
    // overfit flag is the gap > 1.0 test
    assert_eq!(rep.overfit_suspect, rep.is_oos_sharpe_gap > 1.0);
}
```

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_validation`
Expected: FAIL — `cannot find run_validation / ValidationReport`.

- [ ] **Step 3: Implement `ValidationReport` + `run_validation`**

Append to `backtest/validation.rs`:
```rust
use anyhow::Result;
use crate::backtest::replay::{run_engine_on_bars, ReplayConfig, EngineKind, RunResult};
use crate::backtest::report::{compute, Metrics};

#[derive(Debug, Clone)]
pub struct ValidationReport {
    pub full: Metrics,
    pub is_metrics: Metrics,
    pub oos: Metrics,
    pub is_oos_sharpe_gap: f64,
    /// IS→OOS Sharpe gap > 1.0 → suspected overfit (matches the MR tick-replay convention).
    pub overfit_suspect: bool,
}

/// Run the engine's current live config on full / IS / OOS slices and return
/// per-slice Metrics + the IS→OOS Sharpe gap. Each slice is a self-contained
/// run_engine_on_bars call (per-slice warmup; no cross-slice bar leakage).
pub async fn run_validation(
    kind: EngineKind, rc: &ReplayConfig, bars: Vec<Bar>, oos_frac: f64, bar_hours: f64,
) -> Result<ValidationReport> {
    let (is_b, oos_b) = split_is_oos(&bars, oos_frac);
    let full_run = run_engine_on_bars(kind, rc, bars).await?;
    let is_run = run_engine_on_bars(kind, rc, is_b).await?;
    let oos_run = run_engine_on_bars(kind, rc, oos_b).await?;
    let full = compute(&full_run, 0.0, bar_hours);
    let is_metrics = compute(&is_run, 0.0, bar_hours);
    let oos = compute(&oos_run, 0.0, bar_hours);
    let gap = is_metrics.sharpe - oos.sharpe;
    Ok(ValidationReport {
        full, is_metrics, oos, is_oos_sharpe_gap: gap, overfit_suspect: gap > 1.0,
    })
}
```

- [ ] **Step 4: Run test → GREEN**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_validation`
Expected: PASS (3 tests). Run `cargo build --bin backtest_replay` → clean.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/backtest/validation.rs trading-engine-core/tests/backtest_validation.rs
git commit -m "feat(backtest): run_validation — full/IS/OOS metrics + overfit flag (Phase 3 Task 2)"
```

---

## Task 3: `write_validation_report` (markdown IS/OOS table + gap stamps)

**Files:**
- Modify: `trading-engine-core/src/backtest/report.rs`
- Modify: `trading-engine-core/tests/backtest_validation.rs`

**Interfaces:**
- Consumes: `ValidationReport` (Task 2), `Metrics` (existing).
- Produces: `pub fn write_validation_report(dir: &Path, symbol: &str, kind: EngineKind, rep: &ValidationReport) -> Result<()>` — writes `<dir>/<SYMBOL>_<engine>_validation.json` (the ValidationReport serialized) + `<dir>/validation_report.md` (human-readable IS/OOS table + overfit flag + fidelity-gap stamps).

- [ ] **Step 1: Write the failing test**

Append to `tests/backtest_validation.rs`:
```rust
use trading_engine_core::backtest::report::{write_validation_report, Metrics};
use tempfile::TempDir;

#[test]
fn validation_report_markdown_has_is_oos_table_overfit_flag_and_gap_stamps() {
    let mk = |sharpe: f64, trades: usize| Metrics {
        total_return_pct: 10.0, sharpe, max_drawdown_pct: 5.0, win_rate_pct: 60.0,
        total_trades: trades, profit_factor: 1.5, hodl_return_pct: 0.0,
    };
    // IS sharpe 2.0, OOS sharpe 0.5 → gap 1.5 → overfit
    let rep = ValidationReport {
        full: mk(1.2, 30), is_metrics: mk(2.0, 20), oos: mk(0.5, 10),
        is_oos_sharpe_gap: 1.5, overfit_suspect: true,
    };
    let tmp = TempDir::new().unwrap();
    write_validation_report(tmp.path(), "ETHUSDT", EngineKind::Trend, &rep).unwrap();
    let md = std::fs::read_to_string(tmp.path().join("validation_report.md")).unwrap();
    assert!(md.contains("IS")); assert!(md.contains("OOS"));
    assert!(md.contains("Sharpe")); assert!(md.contains("1.5"));      // the gap
    assert!(md.contains("overfit") || md.contains("Overfit"));        // the flag
    assert!(md.contains("regime") || md.contains("fidelity") || md.contains("Fidelity")); // gap stamps
    // JSON artifact present
    assert!(tmp.path().join("ETHUSDT_trend_validation.json").exists());
}
```
(`tempfile` is already a non-dev dependency from Phase 1.)

- [ ] **Step 2: Run to verify it fails**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_validation`
Expected: FAIL — `cannot find write_validation_report`.

- [ ] **Step 3: Implement `write_validation_report`**

Append to `backtest/report.rs` (add `use crate::backtest::validation::ValidationReport;` and `use crate::backtest::replay::EngineKind;` at top, and `use std::path::Path;` is already imported):
```rust
pub fn write_validation_report(
    dir: &Path, symbol: &str, kind: crate::backtest::replay::EngineKind, rep: &ValidationReport,
) -> anyhow::Result<()> {
    std::fs::create_dir_all(dir)?;
    let json = serde_json::to_string_pretty(rep)?;
    std::fs::write(dir.join(format!("{}_{}_validation.json", symbol, kind.budget_key())), json)?;

    let row = |label: &str, m: &Metrics| format!(
        "| {} | {:.2}% | {:.2} | {:.2}% | {:.0}% | {} |",
        label, m.total_return_pct, m.sharpe, m.max_drawdown_pct, m.win_rate_pct, m.total_trades);
    let flag = if rep.overfit_suspect { "⚠️ OVERFIT SUSPECTED" } else { "✅ no overfit flag" };
    let md = format!(
        "# Validation: {} {} (IS/OOS)\n\n\
         | Slice | Return | Sharpe | MaxDD | Win | Trades |\n|---|---|---|---|---|---|\n{}\n{}\n{}\n\n\
         - IS→OOS Sharpe gap: **{:.2}** → {}\n\n\
         ## Fidelity gaps (apply to every metric above)\n\
         - **regime=None** — grid/trend ML regime gate is OFF (optimistic; live uses ML regime).\n\
         - **perp-as-spot-proxy + flat funding** — trend-short MTM uses spot≈perp, funding accrual = 0 (trend shorts approximate).\n\
         - **MR excluded** — MR is tick-resolution (30s flush window); its faithful backtest is the separate `backtest/mean_reversion/` tick-replay, not this 1h harness.\n",
        symbol, kind.budget_key(),
        row("Full", &rep.full), row("IS", &rep.is_metrics), row("OOS", &rep.oos),
        rep.is_oos_sharpe_gap, flag);
    std::fs::write(dir.join("validation_report.md"), md)?;
    Ok(())
}
```
(If `ValidationReport` does not derive `Serialize`, add `#[derive(Debug, Clone, Serialize)]` to it in `validation.rs` — needed for the JSON artifact. Update the Task-2 struct accordingly.)

- [ ] **Step 4: Run test → GREEN**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_validation`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/backtest/report.rs trading-engine-core/src/backtest/validation.rs \
        trading-engine-core/tests/backtest_validation.rs
git commit -m "feat(backtest): write_validation_report — IS/OOS table + overfit flag + gap stamps (Phase 3 Task 3)"
```

---

## Task 4: CLI `--validate` flag

**Files:**
- Modify: `trading-engine-core/src/bin/backtest_replay.rs`

**Interfaces:**
- Produces: a `--validate` CLI flag that, for the selected `--engine`, fetches bars, calls `run_validation`, writes the validation report (JSON + markdown), and prints the headline (full/IS/OOS Sharpe + overfit flag).

- [ ] **Step 1: Add the `--validate` flag + wiring**

In `bin/backtest_replay.rs`:
- Add a `validate: bool` arg (parse `--validate` in the same `args` loop that parses `--engine`/`--config`).
- After building the bars + `ReplayConfig` (existing logic), branch:
```rust
if validate {
    use trading_engine_core::backtest::validation::run_validation;
    let rep = run_validation(kind, &rc, bars, 1.0/3.0, bar_hours).await?;
    println!("validate engine={:?} full_sharpe={:.2} is_sharpe={:.2} oos_sharpe={:.2} gap={:.2} overfit={}",
        kind, rep.full.sharpe, rep.is_metrics.sharpe, rep.oos.sharpe, rep.is_oos_sharpe_gap, rep.overfit_suspect);
    trading_engine_core::backtest::report::write_validation_report(
        std::path::Path::new("backtest/results/replay"), &pair, kind, &rep)?;
} else {
    // existing single-run path (unchanged)
}
```

- [ ] **Step 2: Build**

Run: `cargo build --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay`
Expected: clean build.

- [ ] **Step 3: Commit**

```bash
git add trading-engine-core/src/bin/backtest_replay.rs
git commit -m "feat(backtest): --validate CLI flag (IS/OOS for the 3 1h engines) — Phase 3 Task 4"
```

---

## Task 5: Real-data validation smoke (grid/trend/swing)

**Files:** none (manual smoke + report)

- [ ] **Step 1: Run validation for each 1h engine on a meaningful window**

From the repo ROOT, run (12 months to give each slice enough bars after warmup; IS ≈ 8mo, OOS ≈ 4mo):
```
cargo run --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay -- ETHUSDT 12 grid --validate
cargo run --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay -- ETHUSDT 12 trend --validate
cargo run --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay -- ETHUSDT 12 swing --validate
```
Each prints the headline (full/IS/OOS Sharpe + gap + overfit flag) and writes `backtest/results/replay/{ETHUSDT_<engine>_validation.json, validation_report.md}`.

- [ ] **Step 2: Record the results in the task report**

Write the actual per-engine full/IS/OOS Sharpe + return + trades + the overfit verdict to `/Users/amro/WebstormProjects/trading-humming-bot/.superpowers/sdd/task-5-report.md` (Phase 3). This is the baseline evidence the Phase-4 sweep must beat. Note: 0 trades on a slice means the engine's gate didn't fire on that slice — report it honestly (it's the deployed config's behavior on that regime, not a harness bug; the integration tests prove trades generate on hospitable data).

- [ ] **Step 3: Commit the report reference (if any code touched) + final**

If no code changed in this task, the deliverable is the recorded metrics in the report file + a final Phase-3-complete marker commit (e.g. an empty commit or a docs note). Prefer: if the smoke revealed a harness bug, fix + commit; otherwise no commit needed — Phase 3 exit is the recorded validation metrics.

---

## Phase 3 Exit Criteria

- All `cargo test --test backtest_*` pass (split + run_validation + write_validation_report + the Phase 1-2 suite).
- `cargo run --bin backtest_replay -- ETHUSDT 12 <engine> --validate` runs clean for grid, trend, swing and writes the validation report.
- The validation report shows full/IS/OOS metrics + the IS→OOS Sharpe-gap overfit flag + the three fidelity-gap stamps.
- No engine modified; MR excluded (tick-resolution — documented on every report).

Once Phase 3 is green, write the Phase 4 plan (param sweep + OOS apply-gate) using these validation baselines.

---

## Self-Review

1. **Spec coverage:** §3 IS/OOS split → Task 1; live-config validation (full/IS/OOS) → Task 2; IS→OOS Sharpe-gap overfit flag → Tasks 2-3; fidelity-gap stamps (regime=None, perp-as-spot-proxy, flat funding, MR-excluded) → Task 3; baseline-for-sweep → Task 5. MR exclusion justified (tick-resolution). ✅
2. **Placeholder scan:** Task 5 Step 3 is deliberately conditional (no-code smoke) — it specifies the exact deliverable (recorded metrics + a marker commit only if a bug surfaces), not a vague "TODO." No bare TODOs.
3. **Type consistency:** `split_is_oos(&[Bar], f64) -> (Vec<Bar>, Vec<Bar>)` (Task 1) consumed by `run_validation` (Task 2). `ValidationReport { full, is_metrics, oos, is_oos_sharpe_gap, overfit_suspect }` (Task 2) consumed by `write_validation_report` (Task 3) + the CLI (Task 4). `Metrics` fields used in the markdown row match the Phase-1 `Metrics` struct. `kind.budget_key()` used in Task 3's filename matches the Phase-2 `EngineKind::budget_key()` (returns `"grid"`/`"trend"`/`"swing"`/`"mean_reversion"`). `ValidationReport` needs `Serialize` for the JSON artifact — flagged in Task 3 Step 3.
