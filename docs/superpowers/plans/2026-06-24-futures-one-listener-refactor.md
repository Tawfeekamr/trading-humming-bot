# Futures One-Listener Refactor + Deploy Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Rework the futures bot so ONE Telethon listener (the authenticated spot one) feeds BOTH engines (spot + futures) in a single container, because a second listener can't auth (no session) and two clients on one session is unreliable. Then deploy to testnet.

**Why:** At deploy time we discovered the separate `trading-signal-futures` container's Telethon listener can't start (no session in `data_futures/`, needs interactive login), and copying the session risks breaking the spot feed. User chose "one listener → both engines."

**Architecture:** One `trading-signal-listener` container runs one Telethon listener + two `SignalEngine` instances: spot (Gate.io, owns the listener) and futures (Binance connector, headless — no own listener). Each received message dispatches to both engines' `_process_message`; each tick manages both engines' positions. State files are namespaced (`signal_positions_futures.json`, etc.) so the two engines in one process don't collide. The separate `trading-signal-futures` container + `data_futures` are removed; futures keys move into the listener container's env.

**Tech Stack:** Python 3.13, pytest, existing `src/signals/*`.

**Spec:** supersedes the "new bot alongside (separate container)" decision in `docs/superpowers/specs/2026-06-24-binance-futures-signal-bot-design.md`.

## Global Constraints

- Spot engine behavior + state files (`signal_positions.json`, `signal_journal.db`, `seen_signal_ids.json`) must NOT change (namespace defaults to `""`).
- One Telethon listener only; futures engine is headless (never creates/starts a listener).
- Futures state namespaced by suffix `_futures` to avoid collision in the shared `data/` dir.
- Futures keys reach the futures engine via the listener container's env (not a separate container).
- Errors notify, never silent. Existing tests stay green.

## File Structure

- **Modify** `src/signals/signal_position.py` — accept a `state_suffix` (filename namespace).
- **Modify** `src/signals/signal_journal.py` — accept a `state_suffix` (db filename).
- **Modify** `src/signals/signal_engine.py` — `own_listener` flag (skip listener when False); `state_suffix` threaded to position mgr/journal/seen_ids; expose `process_one(msg, connector)` + `manage(connector)` for external driving.
- **Modify** `src/run_signal_listener.py` — build spot engine (own_listener, suffix "") + futures engine (headless, suffix "_futures", futures_mode, connector); shared tick drains the listener and dispatches to both + manages both.
- **Modify** `docker-compose.rust.yml` — remove the `trading-signal-futures` service; add `BINANCE_FUTURES_KEY/SECRET` to the listener container's env (via `.env.docker` or `environment`).
- **Modify** `.github/workflows/deploy.yml` — write futures keys into the listener's env file (not `.env.futures`), from repo secrets, with an empty-guard.
- **Modify** `src/notifications/telegram_commands.py` — `/futures_status` + `/futures_pnl` read the namespaced futures state file (`signal_positions_futures.json`).
- **Tests:** extend `tests/test_signal_*` for namespacing + headless + dispatch.

---

### Task 1: State namespacing (position mgr + journal + engine seen_ids)

**Files:** `src/signals/signal_position.py`, `src/signals/signal_journal.py`, `src/signals/signal_engine.py`
**Interfaces:** `SignalPositionManager(config, state_suffix="")` → writes `data/signal_positions{suffix}.json` (+ lock); `SignalJournal(state_suffix="")` → `data/signal_journal{suffix}.db`; engine `seen_signal_ids` path → `data/seen_signal_ids{suffix}.json`. Default `""` = byte-identical to today (spot regression).

- [ ] RED: test that a position mgr with `state_suffix="_futures"` writes `signal_positions_futures.json` and does NOT touch `signal_positions.json`; default suffix writes the legacy name.
- [ ] Implement: thread `state_suffix` through the three components. Engine `__init__` accepts `state_suffix=""` and passes it down + uses it for seen_ids path.
- [ ] GREEN: new test passes; existing `tests/test_signal_position_side.py` + `tests/test_signal_rejection_notify.py` unchanged (spot default).
- [ ] Commit.

### Task 2: Headless engine mode (no own listener)

**Files:** `src/signals/signal_engine.py`, `src/run_signal_listener.py`
**Interfaces:** `SignalEngine(..., own_listener=True)`; when `False`, skip `ChannelListener` creation and `start_listener`/`stop_listener` are no-ops. Expose `process_one(msg, connector)` (calls `_process_message`) and `manage(connector)` (calls `_manage_positions`) so a coordinator can drive a headless engine.

- [ ] RED: a headless engine (`own_listener=False`) does NOT create a ChannelListener (assert no `_listener`/no Telethon); `process_one` + `manage` work; default `own_listener=True` still starts the listener (spot unchanged).
- [ ] Implement the flag + public `process_one`/`manage` methods (thin wrappers over existing `_process_message`/`_manage_positions`).
- [ ] GREEN: spot regression intact.
- [ ] Commit.

### Task 3: run_signal_listener — one listener, two engines, dispatch

**Files:** `src/run_signal_listener.py`
**Interfaces:** builds spot engine (own_listener=True, suffix "") + futures engine (own_listener=False, suffix "_futures", futures_mode=True, futures_connector=BinanceFuturesConnector). Shared loop: drain the spot listener's queue → dispatch each msg to BOTH engines' `process_one` → then `manage(connector)` on both. Only the spot engine starts/stops the listener.

- [ ] RED: a test that a message from the shared listener is processed by BOTH engines (both `process_one` called); futures engine is headless.
- [ ] Implement the coordinator loop. Read existing run_signal_listener to reuse its listener wiring.
- [ ] GREEN.
- [ ] Commit.

### Task 4: Compose + env + deploy.yml (futures keys into the listener)

**Files:** `docker-compose.rust.yml`, `.github/workflows/deploy.yml`, possibly `src/notifications/telegram_commands.py`
- Remove the `trading-signal-futures` service + `data_futures` from docker-compose (both engines live in `signal-listener` now; back to `./data:/app/data`). Remove `.env.futures`/`SIGNAL_MODE` futures references.
- `/futures_status` + `/futures_pnl` read `data/signal_positions_futures.json` (the namespaced file).
- deploy.yml: add a step (before `docker compose up`) that writes `BINANCE_FUTURES_KEY`/`BINANCE_FUTURES_SECRET` into the listener container's env source (`.env.docker`, appended idempotently) from repo secrets, with an empty-guard that fails the deploy if a secret is unset.
- [ ] RED/verify: compose parses; futures-telegram test reads the namespaced file; deploy.yml step logic reviewed.
- [ ] Commit.

### Task 5: Verify + deploy

- Run the full Python + Rust suite → green.
- Confirm `.env.futures` cleanup on EC2 is not needed (harmless) or remove it.
- Merge `feat/binance-futures-bot` → main → CI deploys → watch `signal-listener` come up with BOTH engines; confirm futures engine logs "listening" without a second Telethon auth, and `/futures_status` responds.

---

## Self-Review
Coverage: session conflict → R2/R3 (headless + one listener); state collision → R1 (namespace); futures keys → R4 (listener env); spot regression → R1/R2 defaults. No placeholders.
