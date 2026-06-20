docs/audit/race_conditions_report.md# Race Conditions & Concurrency Audit Report
**Date**: June 20, 2026
**Target**: `trading-engine-core` (Rust) & `src/signals/` (Python)

## Executive Summary
This audit reviewed the concurrency and synchronization patterns within both the core Rust trading engine and the new Python signal processing modules. While the codebase uses appropriate primitives (`tokio::sync::Mutex` in Rust, `threading.Lock` in Python), several critical anti-patterns were discovered that lead to race conditions, thread starvation, and lock contention. 

The most pressing issues stem from mixing blocking operations (synchronous HTTP/Disk I/O and synchronous SQLite transactions) inside asynchronous or high-frequency event loops across both codebases.

## Key Findings & Vulnerabilities

### 1. Main Thread Starvation via Synchronous API/ML Execution (Python)
**Location**: `src/signals/signal_engine.py` (`_capture_decision_state`, `_fetch_klines`, `_compute_features`)
**Severity**: Critical

**Description**:
In the latest Phase 2 changes for offline RL, `_capture_decision_state` was added. This function calls `_fetch_klines`, which executes a strictly synchronous HTTP request using `urllib.request.urlopen` with a `10s` timeout. It then runs synchronous Pandas/ML computations via `_compute_features`. Because this is hooked into `_log_audit_trade`, it runs synchronously inside the main `tick()` loop whenever a signal decision is made.
**Risk**: If the Gate.io API is slow or hangs, the entire Python signal engine will freeze for up to 10–20 seconds. During this freeze, the engine cannot process new Telegram messages, monitor open positions, or trigger stop-losses.
**Recommendation**: Offload the offline RL state capture to a background thread using `concurrent.futures.ThreadPoolExecutor`, or rewrite the Kline fetching and ML computation using `aiohttp` and `asyncio.create_task()` to prevent blocking the main event loop.

### 2. SQLite Database Initialization Race Condition (Rust)
**Location**: `src/strategy/trade_journal.rs` (`log_unified` and `UnifiedTradeJournal::new()`)
**Severity**: High

**Description**:
The `log_unified` function instantiates a new `UnifiedTradeJournal` on every call, creating a new SQLite connection and attempting to run database migrations (`migrations().to_latest`).
**Risk**: If multiple async tasks or strategies log a trade concurrently, multiple threads will attempt to run database schema migrations simultaneously on different connections. This triggers `SQLITE_BUSY` (database locked) errors or risks database corruption.
**Recommendation**: Implement the Singleton pattern (e.g., using `lazy_static` or `tokio::sync::OnceCell`) to initialize the `UnifiedTradeJournal` and run migrations exactly once during startup.

### 3. Lock Contention & Starvation via `await` in Lock Guard (Rust)
**Location**: `src/signal/engine.rs` (`manage_positions`)
**Severity**: High

**Description**:
In `manage_positions`, a `tokio::sync::MutexGuard` is held across `.await` points while sequentially fetching current prices via network calls.
**Risk**: Holding a Mutex across an await point causes massive lock contention. Because this occurs inside the main engine loop (`Engine::run`), it stalls the entire websocket event loop. Incoming order book updates will be delayed or dropped while waiting for HTTP responses.
**Recommendation**: Clone the necessary position data, release the lock, fetch all prices concurrently (e.g., `futures::future::join_all`), and then briefly re-acquire the lock to update states.

### 4. Thread Starvation via Synchronous Database I/O (Rust & Python)
**Location**: `src/signal/journal.rs` (Rust) and `src/signals/signal_journal.py` (Python)
**Severity**: Medium

**Description**:
Both the Rust and Python signal journals use standard blocking operations for SQLite inserts (`std::sync::Mutex` in Rust, `threading.Lock` in Python) directly within their main execution paths.
**Risk**: Executing blocking database transactions on the main Tokio worker thread or the main Python event loop degrades high-frequency message processing.
**Recommendation**: Offload synchronous SQLite operations to a dedicated blocking thread pool (`tokio::task::spawn_blocking` in Rust) or a separate queue/worker thread (Python).

### 5. Tokio Thread Starvation via Synchronous File I/O (Rust)
**Location**: `src/strategy/regime_cache.rs` and `src/risk/mod.rs`
**Severity**: Medium

**Description**:
- `RegimeCache::maybe_reload_from_file` uses `std::fs::metadata` and `std::fs::read_to_string` inside an async function polled on every single strategy tick.
- `feed_breaker` continuously calls `save_state` (which uses `std::fs::write` and `std::fs::rename`) on *every single orderbook update*.
**Risk**: Doing synchronous disk I/O repeatedly on the hot path blocks the Tokio worker thread for milliseconds at a time, severely crippling the engine's ability to process high-frequency websocket messages.
**Recommendation**: Replace `std::fs` operations with their asynchronous equivalents from `tokio::fs`, or debounce the circuit-breaker writes so they only persist periodically instead of every micro-tick.
