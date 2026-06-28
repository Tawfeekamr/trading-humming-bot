# Stop-Loss Alerts (with running totals) + /trades Pagination — Design

**Date:** 2026-06-28
**Status:** Approved
**Author:** brainstorm session 2026-06-28

## Goal

Two small, independent Telegram UX improvements:

1. **Clearer stop-loss alerts.** Trend and MR already fire Telegram alerts on every close (and delivery works — `200 OK`). They're terse and lack context, so losses get buried. Make **loss** alerts loud (`🛑 LOSS`) and append the engine's **running realized P&L** so each alert is self-explanatory.
2. **`/trades` pagination.** The cross-engine history command shows a fixed 15 rows. Let the user page through older history with `/trades 2`, `/trades 3`, …

Both are paper-trading UX; neither changes order/execution logic.

## Locked decisions

| Decision | Choice |
|---|---|
| Stop-loss alert mechanism | **Enhance the existing in-engine messages** (no new watcher, no duplicate alerts) |
| Which exits get the loud treatment | **Losses only** (`pnl < 0`); wins keep 📈 |
| Running-total source | Each engine's existing cumulative `self.realized_pnl` (already updated before the alert fires) |
| Pagination UX | **Argument-based**: `/trades [page]` (the polling dispatcher passes full `message.text`; `context` is `None`, so no inline-button/callback machinery) |
| Page size | 15 (unchanged) |
| Scope | Trend + MR loss alerts; `/trades` only. MR TP, trend entries, and other history commands untouched |

## Feature 1 — Stop-loss alerts with running totals

### `trend.rs`
`notify_exit(exit_price, pnl, reason)` fires on **every** exit (`stop_loss`, `trailing_stop`, `signal_exit`). Today's message: `"{📈|⚠️} Trend {pair} {reason} @ ${exit_price} | PnL: ${pnl}"`.

Change: build the message via a new **pure, testable** free function `trend_exit_message(pair, reason, exit_price, pnl, running_pnl) -> String` (mirrors the existing `trend_entry_message`). `notify_exit` calls it with `self.realized_pnl()` (which already includes the just-closed trade, since `self.realized_pnl += pnl` runs before `notify_exit` at trend.rs:451→456 etc.).

Formats:
- Loss (`pnl < 0`): `🛑 LOSS Trend ETH-USDT stop_loss @ $1800.00 | this: $-259.25 | Trend running: $-472.20`
- Win (`pnl >= 0`): `📈 Trend ETH-USDT tp1 @ $614.50 | this: $+30.02 | Trend running: $+93.49`

### `mean_reversion.rs`
The SL notification (`mean_reversion.rs:299-301`) fires after `self.realized_pnl += pnl`. Capture `let running = self.realized_pnl;` **before** the `tokio::spawn` (the spawn moves captures), then build via a pure `mr_sl_message(pair, price, pnl, running) -> String`:

- `🛑 LOSS MR ETH-USDT SL @ $90.00 | this: $-115.89 | MR running: $-149.05`

MR's TP message (`📈 MR … TP`) is left unchanged (tight scope).

### Testing
Pure-function unit tests (no Telegram, no I/O):
- `trend_exit_message` with `pnl < 0` → starts with `🛑 LOSS`, contains the pnl and the running total.
- `trend_exit_message` with `pnl >= 0` → starts with `📈`, no `LOSS`.
- `mr_sl_message` → starts with `🛑 LOSS`, contains running total.

## Feature 2 — `/trades` pagination

### `telegram_commands.py::_cmd_trades`
Today: `LIMIT 15`, footer `"{n} most recent"`.

Change:
- Parse page from `update.message.text`: tokens after `/trades` (strip `@bot` suffix like the dispatcher does at telegram_commands.py:222). Default page 1. Non-integer or `<1` → reply `"Usage: /trades [page] (e.g. /trades 2)"` and return.
- Query: `… ORDER BY timestamp DESC LIMIT 15 OFFSET ?` with `(page-1)*15`.
- Footer: `"Page {page} · /trades {page+1} for older"`.
- Empty result on page > 1 → `"No trades on page {page}. Try /trades {page-1}."`. Empty on page 1 → existing `"No trades yet."`.

### Testing
Unit tests (build the handler, chdir to a tmp_path with a seeded `data/trades.db`, monkeypatch — matches the existing `test_telegram_commands.py` pattern):
- `/trades` → returns the 15 most recent rows, footer mentions `Page 1` and `/trades 2`.
- `/trades 2` → returns rows 16–30 (older), footer `Page 2`.
- `/trades 99` (beyond data) → "No trades on page 99 …".
- `/trades abc` → usage message.
- Page 1 with no trades → "No trades yet."

The handler reads the page from `update.message.text`; tests set `_mock_update`-style `message.text` accordingly (the existing `_mock_update()` helper builds a MagicMock whose `.text` can be set, or build a minimal stand-in).

## Out of scope
- New alerting watcher / external process.
- Inline-keyboard pagination buttons.
- Pagination on `/trend_history`, `/signal_history` (can add later on request).
- Enhancing MR TP or trend entry messages.
- Any change to order execution, risk logic, or the signal engine.
