# Trading Humming Bot — Race Conditions & Enhancements Audit

**Date:** 2026-07-20
**Scope:** Full codebase audit excluding security and DevOps
**Author:** Automated deep audit

---

## Architecture Summary

| Layer | Stack | Concurrency Model |
|---|---|---|
| **Rust Engine** | Tokio async, single-threaded event loop | Sequential tick-per-WebSocket-event |
| **Python Signal Engine** | asyncio + Telethon background thread + `ThreadPoolExecutor(1)` | Single async loop, offloaded ML capture |
| **Integration** | HTTP API (`/api/v1/*`) + shared JSON files | File-based IPC, no distributed lock |

---

## Race Conditions

### RC1 — Dual-Write to `signal_positions.json` (No Real Synchronization) — **CRITICAL**

**Files:** `src/signals/signal_position.py:159-200` (Python), `trading-engine-core/src/signal/position.rs:164-215` (Rust)

The Python side uses `fcntl.flock(LOCK_EX)` + atomic `tmp → rename`. The Rust side does a plain `std::fs::write` — **no advisory lock, no atomic rename**. The `disk_more_advanced` / `_disk_more_advanced` heuristic in both codebases is bug-for-bug mirrored detection, not synchronization. Either side can write while the other is mid-write, producing a truncated or stale file. The heuristic tries to prevent one from overwriting a more-advanced copy but does so *after* reading the file — a TOCTOU gap where both could read, both decide "disk not more advanced", and both write, with the last writer winning.

**Fix:** Rust `save_state` MUST participate in the existing `flock` protocol on `signal_positions.lock`, or migrate to a single-writer model where only one side owns the file.

---

### RC2 — `signal_status.json` Non-Atomic Write + No Lock — **HIGH**

**File:** `src/signals/signal_engine.py:280-281`

Python writes `signal_status.json` with a bare `json.dump(f)` — no temp file, no `os.replace`. If the Rust engine reads this file mid-write (e.g., for Telegram commands), it gets truncated or unparseable JSON. The Rust side catches parse errors silently (`except Exception: pass`), but the damage is already done — status goes stale until the next successful write.

**Fix:** Temp-write + rename.

---

### RC3 — `maybe_reload_from_file` in RegimeCache / RoutingCache: `last_mtime` Not Updated by `persist()` — **LOW**

**Files:** `trading-engine-core/src/strategy/regime_cache.rs:151-157`, `trading-engine-core/src/strategy/routing_cache.rs:146-153`

`RegimeCache::persist()` and `RoutingCache::persist()` write the current in-memory state to disk but never update `last_mtime`. After a `persist()`, the file's OS mtime changes. The next `maybe_reload_from_file()` call sees `file_mtime > last_mtime` and re-reads the file — producing a redundant parse of data that's already in-memory. Not a correctness bug (the data is identical), but wasteful on every tick after an HTTP push.

**Fix:** `persist()` should update `last_mtime` to the new file's mtime immediately after the `rename`.

---

### RC4 — Sync Disk I/O Blocking Tokio Runtime — **HIGH**

**Files:** `trading-engine-core/src/engine.rs:407-416` (`save_bar_buffers`), `:419-443` (`load_bar_buffers`)

`save_bar_buffers` calls `std::fs::write` directly inside an async context on every closed 1h bar. `load_bar_buffers` calls `std::fs::read_to_string`. These are synchronous blocking calls that starve the Tokio worker thread. For the save path, this runs every 1h (~50-200ms latency for a large bar cache) — the engine effectively pauses for that duration. If multiple pairs close bars in close succession, all stack.

**Fix:** Use `tokio::fs::write` / `tokio::fs::read_to_string`, or `spawn_blocking`.

---

### RC5 — `_peak_equity` Shared Between Main Thread and Capture Worker — **LOW**

**File:** `src/signals/signal_engine.py:120, 965-966`

`_peak_equity` is read and written in `_capture_decision_state` (offloaded to `_CAPTURE_POOL` thread). The `ThreadPoolExecutor(max_workers=1)` serializes captures, so there's no concurrent write-write race. However, `_peak_equity` is initialized to `0.0` in `__init__` and written only from the worker thread. If the main thread ever needs to read it (e.g., `get_status`), there's a write-read race without synchronization. Currently nothing reads it from the main thread, but this is a landmine for future code.

**Fix:** Document that `_peak_equity` is worker-only, or wrap in a `threading.Lock`.

---

### RC6 — Python `manual_close` Mutates Position Without Going Through Position Manager — **LOW**

**File:** `src/signals/signal_engine.py:300-307`

```python
def manual_close(self, symbol: str):
    pos = self._position_mgr.get_position(symbol)
    if not pos:
        return None
    pos.is_closed = True      # Direct mutation!
    pos.exit_reason = "manual"
    return "manual"
```

This bypasses `SignalPositionManager.close_position()` — no `realized_pnl` calculation, no `_save_state()`, no journal entry. The position is marked closed in-memory but the disk file still shows it open. If the caller doesn't separately call `_execute_close`, the Rust engine will see a stale open position.

**Fix:** Route through `self._position_mgr.close_position()`.

---

### RC7 — Python `_manage_positions` Mutates Position In-Place (Bypasses Manager) — **HIGH**

**File:** `src/signals/signal_engine.py:659, 671, 683`

```python
pos.tp1_hit = True  # Line 659 — direct mutation on the snapshot object
```

The `pos` variable is from `get_open_positions()`, which returns the live objects (Python list of references). Setting `pos.tp1_hit = True` mutates the position manager's internal state **outside** the manager's lock. `partial_close` is called through the manager (locked), but the `tp_hit` flag is set without synchronization. If the Rust side reads `signal_positions.json` between the flag mutation and the `partial_close` → `_save_state`, it sees inconsistent state (tp1_hit=True but amount unchanged).

**Fix:** Set TP flags inside the position manager's methods within the lock.

---

### RC8 — `_save_seen_signal_ids` Non-Atomic Truncation — **LOW**

**File:** `src/signals/signal_engine.py:1132-1140`

```python
if len(self._seen_signal_ids) > self._seen_signal_ids_max:
    self._seen_signal_ids = set(list(self._seen_signal_ids)[-self._seen_signal_ids_max:])
with open(self._seen_signal_ids_path, "w") as f:
    json.dump(sorted(self._seen_signal_ids), f)
```

The truncation mutates the set in-place, then writes. If the process crashes between truncation and write, the in-memory set is truncated but disk still has the full set. On restart, the full set is loaded (correct). But if it crashes between write start and write completion, disk is corrupted. Also no temp+rename atomicity.

**Fix:** Temp-write + rename.

---

### RC9 — Rust `manage_positions` Snapshot/Re-lock Gap — **LOW**

**File:** `trading-engine-core/src/signal/engine.rs:87-91`

Positions are cloned under lock at line 87, then the lock is released. Prices are fetched without the lock (correct — avoids holding lock across HTTP). Then the lock is re-acquired at line 105. Between release and re-acquire, a new position could be opened (by Python). The `reload_state()` at line 89 picks this up, but the `positions` snapshot already taken doesn't include it — the new position is unmanaged for this tick. Since ticks run every WebSocket event (sub-second), this is a 1-tick delay, not a correctness issue.

---

### RC10 — Rust `placed_orders` Not Persisted Across Restarts — **MEDIUM**

**File:** `trading-engine-core/src/engine.rs:42, 378-384`

The `placed_orders: HashMap<String, (String, String)>` mapping client_order_id → (symbol, exchange_order_id) is in-memory only. On restart, strategies lose the ability to cancel their own resting orders (e.g., swing's resting TP1 / hard stop). The orders still exist on the exchange but can't be canceled by the engine.

**Fix:** Persist to a JSON file on placement and cancel, load on startup.

---

## Enhancements & Functional Bugs

### E1 — Python `_manage_positions` TP Logic: Close Percentage Base Mismatch — **LOW**

**File:** `src/signals/signal_engine.py:658-687`

TP checks are independent `if` blocks — correct. But the Rust equivalent (lines 118-156 of `trading-engine-core/src/signal/engine.rs`) has a subtle difference: after TP1 partial close, the position snapshot's `remaining_amount` is stale for the TP2 calculation. TP2 uses `pos.tp2_close_pct` (0.50) but `partial_close` reads the LIVE remaining from the manager, which was already reduced by TP1. This means total closed = 33% + 50%×67% = 66.5%. **This is by design** (each TP closes a % of remaining), but verify this is intentional and documented.

---

### E2 — Signal `stale` Detection Runs BEFORE Audit Logging — **LOW**

**File:** `src/signals/signal_engine.py:353-361`

The `signal.stale` check at line 353 returns early **before** audit logging at line 364. The raw message is never logged for stale signals — so there's no record of how many signals were skipped due to staleness. Audit trail is incomplete.

**Fix:** Move `_journal.log_raw_message(...)` before the stale check, or add a separate stale audit log.

---

### E3 — Python Signal Engine: BTC Regime Check Only for DANGER — **LOW**

**File:** `src/signals/signal_engine.py:407-412`

Only `btc_regime == "DANGER"` blocks trades. A `RANGING` or `TRENDING` BTC regime is ignored. The Rust strategies DO use regime (ranging/trending/danger) for their own decisions, but the signal engine doesn't — it trades signals regardless of whether BTC is ranging or trending. If the signal provider performs poorly in ranging markets, there's no filter.

**Fix:** Consider gating signals by BTC regime, or at minimum log the regime for post-hoc analysis.

---

### E4 — `dispatch_cycle` Blocks Position Management on Slow DeepSeek Calls — **HIGH**

**File:** `src/run_signal_listener.py:142-161`

The loop drains ALL queued messages via `process_one(msg, connector)` before calling `manage(connector)`. Each `process_one` makes a synchronous HTTP call to DeepSeek. If 5 messages are queued and DeepSeek takes 3 seconds each, position management is delayed by 15 seconds. During a fast market move, this means SL/TP checks won't fire.

**Fix:** Interleave message processing and position management, or run them in parallel.

---

### E5 — Gate.io REST API Rate Limiting — **LOW**

**Files:** `src/signals/signal_engine.py:785-793` (price fallback), `:856-882` (ATR fetch), `:912-938` (kline fetch)

Each call goes directly to `api.gateio.ws` with no rate limiting. The price fallback fires when `_get_price_fn` returns 0 (connector failure), ATR fires on every signal with a missing SL, and klines fire for decision-state capture. In a burst of signals, Gate.io's rate limits (200 req/10s for public endpoints) could be hit, causing all operations to fail silently.

**Fix:** Add an in-process rate limiter or request coalescing.

---

### E6 — Rust Signal Engine Only Manages Long Positions — **HIGH**

**File:** `trading-engine-core/src/signal/engine.rs:110-156`

The SL/TP logic at lines 110-156 only checks `current_price <= pos.stop_loss` (long stop) and `current_price >= pos.take_profits[...]` (long TP). There's no short-side management even though `SignalPosition` has a `side` field.

**Fix:** Mirror the directional logic from `_manage_futures_positions` in Python (lines 729-748 of signal_engine.py).

---

### E7 — No Order Timeout for Stuck Paper/Exchange Orders — **LOW**

**File:** `trading-engine-core/src/engine.rs:363-390`

Orders are placed via `submit_orders` and tracked in `placed_orders`. If an order is never filled and never canceled (exchange glitch, connector bug), it stays in `placed_orders` forever and its entry in `placed_orders` prevents the strategy from ever re-placing a similar order (the cancel-by-client-id path). There's no TTL or cleanup sweep.

**Fix:** Add an order-age sweep in `tick_strategies` that auto-cancels orders older than N minutes.

---

### E8 — `signal_status.json` Written Every Tick Regardless of Change — **LOW**

**File:** `src/signals/signal_engine.py:207, 234-283`

`_write_status()` is called on every `tick()` regardless of whether any state changed. At 1-second tick intervals, this is 86,400 writes/day. The file is only ~300 bytes, but the write involves `_sync_closed_from_rust()` (reads `signal_positions.json` from disk) plus the `json.dump` — unnecessary I/O churn.

**Fix:** Cache the last written status hash; skip write if unchanged.

---

### E9 — Configuration Hot-Reload Not Supported — **MEDIUM**

**Files:** `config/strategy.yaml`, `trading-engine-core/src/main.rs`, `src/run_signal_listener.py`

Both Python and Rust load config once at startup. Changes to `max_positions`, `capital_pct`, `cooldown_seconds`, etc. require a full restart. For a live trading system, this is an operational risk — a restart during an open position loses state and re-triggers startup replay.

**Fix:** Add a file-watch / SIGHUP handler that reloads config without restarting the engine loop.

---

### E10 — `_fill_default_sl` Called Before Validation (Wasted Work) — **LOW**

**File:** `src/signals/signal_engine.py:394-396`

```python
if signal.stop_loss is None:
    self._fill_default_sl(signal, connector)  # Fetches ATR from Gate.io (HTTP call!)

# Validate
valid, reason = self._validator.validate(signal)
```

If validation fails (R:R too low, pair blacklisted, quality score too low), the ATR fetch was wasted. Move default SL fill after validation or make it conditional on validation passing.

---

### E11 — Models Directory: Stale Artifacts Mixed with Production Models — **LOW**

**File:** `models/`

The directory contains `.pkl` files (`regime_rf_v3.pkl` — 4.8MB), `.onnx` files (various versions), `_clean.pkl` files, and a `_pre_retrain_backup_20260719/` directory. Old model versions aren't cleaned up, the backup directory grows on each retrain, and `.pkl` models (scikit-learn) co-exist with `.onnx` (ONNX runtime). The model loader paths should be explicit and stale models removed.

**Fix:** Document which models are active, add a cleanup script for old versions.

---

### E12 — `capital` `set_size_mult` Signature Uses `&mut self` But Field Is `BTreeMap` on `self` — **LOW**

**File:** `trading-engine-core/src/capital/mod.rs:117-119`

```rust
pub fn set_size_mult(&mut self, name: &str, mult: f64) {
    self.size_mults.insert(name.to_string(), mult.max(0.0));
}
```

`CapitalManager` is `Clone` (via `Arc`), and `set_size_mult` takes `&mut self`. But `size_mults` is a `BTreeMap` on the outer struct, not inside the `Arc<RwLock<CapitalState>>`. This means `set_size_mult` can't be called through a shared reference. If the API handler ever needed to call it, it wouldn't compile. The field should live inside the `Arc<RwLock<...>>` for consistency.

---

### E13 — `Strategy::deployed_capital()` Relies on In-Memory State That Differs From Exchange Reality — **LOW**

The `deployed_capital` per strategy is calculated by the strategy's own tracking of cost basis. If a fill is missed (WebSocket disconnect, paper fill bug), the strategy's internal state diverges from actual exchange balances. The `sync_equity` call uses real balances, but `deployed` uses strategy self-reporting — these can drift apart.

**Fix:** Cross-validate `deployed` against `equity - USDT` (the locked-in-positions amount) and alert on divergence > threshold.

---

### E14 — Python `_execute_close` Sends MARKET Sell but Doesn't Verify Fill — **MEDIUM**

**File:** `src/signals/signal_engine.py:993-1023`

The MARKET sell order is placed and assumed to fill. There's no confirmation. If the order is rejected (insufficient balance, exchange error), the position tracker believes it's closed but the exchange position is still open. The Rust sync path (`_sync_closed_from_rust`) catches this on the next tick, but there's a window where the position manager shows "no open positions" while a position exists on the exchange.

**Fix:** Wait for fill confirmation before updating the position manager, or at minimum log-and-alert on placement failure.

---

### E15 — No Trade Cooldown Enforcement Between Signal and Execution — **MEDIUM**

**File:** `src/signals/signal_risk.py:40-59`

The `can_trade()` / `block_reason()` check happens at validation time (signal_engine.py:415). By the time execution happens (potentially seconds later due to DeepSeek API latency or queue depth), the risk state could have changed (daily limit hit, cooldown expired). The check should happen immediately before order placement, not just at validation time.

---

### E16 — `_available_pairs` Refresh Interval Is Hardcoded to 1 Hour — **LOW**

**File:** `src/signals/signal_engine.py:191`

```python
if time.time() - self._last_pair_refresh > 3600:
```

If a new trading pair is listed on Gate.io, the engine won't recognize it for up to an hour. Make the refresh interval configurable.

---

## Summary Table

| ID | Severity | Category | Component |
|---|---|---|---|
| RC1 | **Critical** | Data corruption | `signal_positions.json` dual-write |
| RC2 | **High** | Data corruption | `signal_status.json` non-atomic |
| RC4 | **High** | Performance | Sync I/O blocks Tokio |
| RC7 | **High** | State inconsistency | TP flag mutation outside lock |
| RC10 | **Medium** | Operational | `placed_orders` not persisted |
| E4 | **High** | Liveness | Slow DeepSeek blocks position mgmt |
| E6 | **High** | Missing feature | Rust signal engine: shorts unmanaged |
| E9 | **Medium** | Operational | No config hot-reload |
| E14 | **Medium** | Correctness | MARKET sell not verified |
| E15 | **Medium** | Correctness | Risk check/execution TOCTOU |
| RC3 | **Low** | Waste | `last_mtime` not updated by persist |
| RC5 | **Low** | Landmine | `_peak_equity` thread safety |
| RC6 | **Low** | Correctness | `manual_close` bypasses manager |
| RC8 | **Low** | Resilience | Non-atomic truncation |
| RC9 | **Low** | Latency | Snapshot/re-lock gap |
| E1 | **Low** | Verification | TP close-pct base mismatch |
| E2 | **Low** | Audit | Stale signals not logged |
| E3 | **Low** | Feature gap | BTC regime not used by signal engine |
| E5 | **Low** | Resilience | No Gate.io rate limiting |
| E7 | **Low** | Resilience | No order timeout |
| E8 | **Low** | Waste | Status written every tick |
| E10 | **Low** | Waste | ATR fetch before validation |
| E11 | **Low** | Hygiene | Stale model artifacts |
| E12 | **Low** | Design | `size_mults` outside Arc |
| E13 | **Low** | Monitoring | Deployed/equity divergence |
| E16 | **Low** | Config | Hardcoded pair refresh interval |

---

## Review & Pre-Deploy Fixes (Claude, 2026-07-20)

Multi-angle review (Rust concurrency, Python signal-engine, feature-contract
migration) of the working-tree implementation of RC1–RC4, RC10, the engine
command bus, the feature-contract module, and the signal-engine hardening.
**Verdict: mostly sound; one critical regression found and fixed before deploy.**

### Fixed before deploy (this commit)

- **TP-flag crash-recovery regression** (in the RC7 implementation,
  `signal_engine.py` / `signal_position.py`): RC7 correctly moved the tp-flag
  mutation into a locked `mark_tp_hit()`, but `mark_tp_hit` saved in its own
  `_save_state`, separate from the following `partial_close`/`close_position`
  save. A crash between them persisted `tp1_hit=True, amount_closed=0` → on
  restart TP1 was skipped and TP2/TP3 over-sold against a stale full amount on
  the exchange. (Spot ordered flag-then-close; futures close-then-flag — both
  created the window.) **Fix:** fold the tp-flag set into `partial_close` /
  `close_position` so flag + amount/is_closed are written in one atomic
  `_save_state`; removed the now-redundant `mark_tp_hit` calls from all 9 TP
  branches. Also resolves the futures-TP3 case where the post-close
  `mark_tp_hit(3)` early-returned on the `is_closed` guard (tp3_hit was never
  persisted on a successful TP3).

### Confirmed correct (no action)

- **Command bus** (`order_command.rs` + engine wiring): no `&mut` race
  (commands processed by the task owning `&mut self`), no deadlock (mpsc cap 64,
  `SendError`/`RecvError` handled, `let _ = respond_to.send` panic-safe).
- **RC1** (position file): already fixed in prior commit `cfc686a`
  (`fs2::FileExt::try_lock_exclusive` on `signal_positions.lock`, held across
  read+merge+temp+rename; Python uses the same path with `fcntl.flock`).
- **RC2** (atomic status write), **RC4** (`tokio::fs` for bar buffers),
  **RC10** (atomic `placed_orders` persist + tolerant load), **RC3**
  (`last_mtime` refresh), **E6** (short-side TP/SL), **E15** (pre-order risk
  re-check), `_execute_close` return-gating, and the **feature-contract**
  migration (column-for-column identical, behavior-preserving, enforced at
  load/inference).

### Logged as follow-ups (not blockers; shipped as-is)

- **manual_close (RC6):** routes through the manager now but uses `entry_price`
  → realized_pnl ≈ $0 and never places the exchange sell. Use real price +
  `_execute_close`.
- **E4 (dispatch):** one-message-per-cycle drain helps ≥2 queued msgs but a
  single slow DeepSeek call still blocks `manage()`; move `manage()` to its own
  task / offload DeepSeek.
- **E14:** `_execute_close` returns True on submission, not fill — async
  rejections still cause phantom closes.
- **Rust `process_cancels`** (`engine.rs`): retains the placed_orders entry on
  ANY cancel error (no retryable/permanent classification) → potential
  cancel-spam + `placed_orders.json` growth under a missed-fill.
- **Rust `cancel_all_api_orders`** (`engine.rs`): removes all entries on `Ok`
  even when the returned Vec is empty; `PaperTradeConnector` returns `Ok([])` →
  paper mode silently loses tracking. Gate on actual cancel results.
- **RC3 sync I/O** (`regime_cache.rs`, `routing_cache.rs`): `persist()` uses
  sync `std::fs::write` + `metadata` in an async fn (RC4 converted bar buffers
  to `tokio::fs` for this reason) → Tokio-worker starvation under a regime-push
  burst. Convert to `tokio::fs`.
- **`save_placed_orders`** (`engine.rs`): called once per order inside the
  submit loop instead of once after → N× latency on multi-order ticks. Batch.
- **Feature contract:** `_declared_feature_contract_ok` skips legacy (v4)
  pickles, so drift-detection only activates after the next retrain
  (intentional backward-compat).
