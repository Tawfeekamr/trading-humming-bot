# Grid State Persistence + Circuit-Breaker Wiring — Design

**Date:** 2026-06-14
**Status:** Approved (design)
**Scope:** `trading-engine-core` (Rust)

## Problem

Three gaps in the grid strategy and one latent safety defect:

1. **Grid summary state is in-memory only.** `GridStrategy` holds `total_pnl`, `peak_equity`, `current_capital`, and `level_cooldowns` in memory with no persistence. On every restart they reset — cumulative grid growth and per-level cooldowns are lost.
2. **Grid fills are not journaled.** Unlike trend (`trend_journal.db`), grid has no per-fill history — no queryable performance record.
3. **Grid PnL is realized-cash only.** `on_fill` records cash flow (`Buy => -cash`, `Sell => +cash`) and never mark-to-markets the held inventory, so `/status` understates true value while holding coins.
4. **The portfolio `CircuitBreaker` is unwired.** `risk::CircuitBreaker` has `peak_equity` / `start_of_day_equity` thresholds for max-drawdown (10%) and daily-loss (5%), and `check_trading_allowed()` gates every order on `is_halted()` — but **nothing ever calls `record_pnl` / `update_peak` / `check_daily` / `set_start_of_day_equity`**. `halted` can never become true, so the bot's loss limits do not actually function. (`risk/mod.rs` even carries the TODO: `// Engine should call record_pnl with actual equity`.)

## Decisions

- **Accounting model: both.** The circuit breaker runs on **realized** equity (stable — won't trip on transient price drops of held inventory). `/status` additionally shows a **mark-to-market** figure (live, display-only) computed from connector balances × mid-price.
- **Storage: JSON state + SQLite journal**, mirroring the proven trend pattern. `data/{pair}_grid_state.json` for summary state; `data/grid_journal.db` (SQLite, WAL, `user_version` migrations) for per-fill rows.
- **Scope: grid gap + wire the portfolio breaker.** The portfolio breaker covers grid + trend (the strategies in `Engine::strategies`, ~20K paper capital). The signal engine keeps its own `SignalRiskGuard` (separate ~10K, own daily limit) — unchanged, to avoid double-counting.
- **Breaker feed cadence: every tick.** Realized equity changes only on fills/exits, so this is behaviorally identical to after-fills, and it handles the UTC-midnight daily rollover correctly even on quiet days.

## Components

### 1. Grid state persistence — `data/{pair}_grid_state.json`

Mirror `TrendStrategy::save_position` / `load_position`.

- **Shape:** `{ realized_pnl: f64, peak_equity: f64, level_cooldowns: HashMap<String,i64> }` (`level_cooldowns` keyed `"buy_2"`/`"sell_0"` → fill timestamp ms).
- **Load:** in `GridStrategy::new`, best-effort. Missing file → start fresh. Parse error → log `warn!`, start fresh (never panic).
- **Save:** after every fill in `on_fill` (after `record_pnl`), via a `save_state()` helper. Atomic write (temp + rename) to avoid torn reads.
- `current_capital` is derived (`initial_capital + realized_pnl`) rather than stored, so it stays consistent.

### 2. Grid fill journal — `data/grid_journal.db`

New module `strategy/grid_journal.rs`, struct `GridJournal` mirroring `TrendJournal`:
- SQLite, `PRAGMA journal_mode=WAL; busy_timeout=5000`.
- `user_version` migrations (start at v1).
- Table `grid_trades(id INTEGER PK, ts_ms INTEGER, pair TEXT, side TEXT, level TEXT, price REAL, quantity REAL, fee REAL, realized_pnl REAL, running_total REAL)`.
- Methods: `new()`, `open(path)`, `log_fill(...)`.
- Insert one row per fill in `on_fill` (after `record_pnl`, before/after `save_state`). `running_total` = `self.total_pnl` at insert time.

### 3. Mark-to-market display (no persistence)

In `GridStrategy::status()`, compute live each call:
```
unrealized_value = base_balance * mid_price + quote_balance
mtm_equity       = initial_capital + realized_pnl + unrealized_gain   // see note
```
where `base_balance`/`quote_balance` come from the connector balances (already passed into `on_tick` via `ctx.balances`) and `mid_price` from the tick's order book. `status()` already runs in the tick context, so MTM is computed from data on hand. Display string adds `| MTM $Y` alongside the existing realized growth. Nothing persisted.

> Note: to avoid double-counting, the **quote (USDT) balance already includes realized PnL** in paper mode (fills adjust balances). So `mtm_equity = base_balance * mid_price + quote_balance` directly — do not add `realized_pnl` again. The displayed MTM is the true account equity; the displayed "Realized" is the cash-flow accumulator for break-even visibility.

### 4. Circuit-breaker wiring (portfolio, grid + trend)

- **Trait accessor:** add `fn realized_pnl(&self) -> f64 { 0.0 }` (default) to `Strategy` in `strategy/mod.rs`. `GridStrategy` returns `self.total_pnl`; `TrendStrategy` returns `self.realized_pnl`; `MeanReversionStrategy` returns `0.0`.
- **Baseline:** `Engine` computes `baseline_capital = grid.capital_usdt + trend.capital` once at startup (sum of configured capital for the strategies in `self.strategies`). Signal capital excluded.
- **Per-tick feed:** in `Engine::tick_strategies` (after strategies have ticked and fills processed), compute:
  ```
  realized_equity = baseline_capital + Σ strategy.realized_pnl()
  breaker.update_peak(realized_equity)
  let tripped = breaker.check(realized_equity) || breaker.check_daily(realized_equity)
  ```

- **Halt semantics — block entries, allow exits (critical).** `check_trading_allowed()` currently gates *every* order in `submit_orders`. Trend uses **bot-side** stops (the SL/TP/trailing exit is a sell `OrderRequest` pushed from `on_tick`, not an exchange-side stop), so a naive full-freeze on halt would trap open trend positions and let losses run past their stops. Therefore:
  - Add `reduce_only: bool` (default `false`) to `OrderRequest`.
  - Exit order sites set `reduce_only = true`: trend SL/TP/trailing/signal_exit sells; grid sells (they reduce inventory). Entry orders (trend buy, grid buy) stay `false`.
  - `submit_orders`: when `check_trading_allowed()` fails (halted), **skip only non-reduce-only orders**; reduce-only exits still submit. So a halt stops new exposure but keeps managing/closing existing positions.
  - Assumption: long-only (`trade_shorts = false`, grid long-only). A future shorts feature would need to flip the reduce-only sense for short entries/exits.
- **Daily reset:** `Engine` tracks `last_reset_date` (UTC `YYYY-MM-DD`). When the date rolls, call `breaker.set_start_of_day_equity(realized_equity)` and update `last_reset_date`. Also set it on first run.

### 5. Circuit-breaker persistence — `data/risk_state.json`

- **Shape:** `{ peak_equity, start_of_day_equity, halted, halted_at_unix, last_reset_date }`.
- **Load** at startup: restore `peak_equity`/`start_of_day_equity`/`halted`; if `halted` and `now - halted_at_unix < cooldown_secs`, stay halted; recompute `last_reset_date` (if it's a new day, reset SOD). On first run (no file), initialize `peak_equity` and `start_of_day_equity` to current realized equity.
- **Save** on state changes: new peak, daily reset, halt trigger, (optionally) cooldown-expiry auto-unhalt.
- `CircuitBreaker` gains a `last_reset_date: String` field + accessors; load/save live in a small `risk::state` helper (or inline in `risk/mod.rs`) using atomic temp+rename, consistent with the signal-state pattern.

## Data flow

```
fill arrives (paper or live)
  → GridStrategy::on_fill
      → record_pnl (updates total_pnl, peak_equity)        [existing]
      → GridJournal::log_fill (insert row)                 [new]
      → save_state (grid_state.json, atomic)               [new]
  → next Engine tick
      → Σ realized_pnl → breaker.update_peak/check/check_daily   [new wiring]
      → on halt → check_trading_allowed fails → no new orders
      → state change → risk_state.json save                [new]
UTC midnight → set_start_of_day_equity → risk_state.json save    [new]
restart → load grid_state.json + risk_state.json → state restored [new]
```

## Error handling

- **Corrupt/missing `grid_state.json`:** warn + start fresh. Never panic.
- **`grid_journal.db` unavailable (open fails):** log error, continue without journaling (grid still trades; `GridJournal` is `Option<GridJournal>`). Mirrors trend's `new()` journal-disabled fallback.
- **`risk_state.json` corrupt/missing:** warn + initialize breaker from current realized equity (safe default: no false halt).
- All persistence uses atomic temp-write-rename so readers (status, reload) never see partial files.

## Testing

- `grid_state` save/load round-trip; corrupt-file recovery (starts fresh, no panic).
- `GridJournal` insert + query (mirror `tests/test_trend_journal.rs`); idempotent migrations on restart.
- Breaker wiring unit test: feed rising then falling realized equity → assert `halted` at 10% drawdown and at 5% daily loss; assert 30-min cooldown auto-unhalt; assert `risk_state.json` round-trip restores peak/SOD/halted.
- MTM: inject balances + order-book mid-price → assert displayed MTM = base×mid + quote (no double-count of realized).
- Integration: simulate a sequence of fills → drop/recreate strategy + engine → assert grid state, journal rows, and breaker peak/SOD all restored.

## Out of scope

- Signal engine accounting (keeps its own `SignalRiskGuard`).
- Live-only concerns (real-fill ingestion) — separate work.
- Migrating signal JSON to SQLite (already addressed by advisory-lock fix).
- Wiring mean-reversion into the breaker (returns 0.0; strategy is inert / validated NO-GO).

## Files

- `strategy/grid.rs` — load/save state, journal hook, MTM in `status()`, `realized_pnl()` accessor, `reduce_only` on sell orders.
- `strategy/grid_journal.rs` — **new**, mirrors `trend_journal.rs`.
- `strategy/mod.rs` — `Strategy::realized_pnl()` default.
- `strategy/trend.rs` — `realized_pnl()` accessor; `reduce_only = true` on SL/TP/trailing/signal_exit sells.
- `connector/types.rs` — `OrderRequest.reduce_only: bool` (default `false`).
- `engine.rs` — per-tick breaker feed, daily reset, baseline capital, `submit_orders` reduce-only bypass, load/save `risk_state.json` at startup.
- `risk/circuit_breaker.rs` — `last_reset_date` field + accessors.
- `risk/mod.rs` (or `risk/state.rs`) — `risk_state.json` load/save.
- Tests: `tests/test_grid_state.rs`, `tests/test_grid_journal.rs`, extend breaker tests, reduce-only bypass test.
