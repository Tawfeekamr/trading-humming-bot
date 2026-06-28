# Stop-Loss Alerts (running totals) + /trades Pagination — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make trend/MR loss alerts loud (`🛑 LOSS`) with the engine's running realized P&L, and add `/trades [page]` pagination — both Telegram UX only.

**Architecture:** Enhance the existing in-engine Telegram messages (no new watcher). Pure message-builder functions for unit-testability. Pagination reads the page from `update.message.text` (the dispatcher passes full text; `context` is `None`).

**Tech Stack:** Rust (strategies + message fns, `cargo test`), Python (Telegram command, `pytest`).

## Global Constraints

- **Paper only** — no order/execution/risk logic changes anywhere.
- **Losses only** get the `🛑 LOSS` marker; wins keep `📈`.
- **Running total** comes from each engine's existing cumulative `self.realized_pnl` (already updated before the alert fires).
- **Page size = 15** (unchanged); pagination via `LIMIT 15 OFFSET (page-1)*15`.
- **Message builders are pure functions** (no Telegram I/O) so they unit-test without network.
- **TDD** every task; **frequent commits**.
- **Branch:** `feat/stop-loss-alerts-and-trades-pagination` (already created; spec committed).

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `trading-engine-core/src/strategy/trend.rs` | Modify | Add pure `trend_exit_message(...)`; `notify_exit` calls it with running P&L. Inline tests. |
| `trading-engine-core/src/strategy/mean_reversion.rs` | Modify | Add `pub fn mr_sl_message(...)`; SL send uses it with running P&L. |
| `trading-engine-core/tests/test_mean_reversion.rs` | Modify | Unit test for `mr_sl_message`. |
| `src/notifications/telegram_commands.py` | Modify | `_cmd_trades` parses `/trades [page]`, paginates, page-aware footer. |
| `tests/test_telegram_commands.py` | Modify | `TestTradesPagination` tests. |

---

### Task 1: Trend loss-aware exit alerts with running total

**Files:**
- Modify: `trading-engine-core/src/strategy/trend.rs` — add `trend_exit_message` after `trend_entry_message` (~L714); rewrite `notify_exit` (~L354-362); add tests in the existing inline `#[cfg(test)] mod tests`.

**Interfaces:**
- Produces: private free fn `trend_exit_message(pair: &str, reason: &str, exit_price: f64, pnl: f64, running_pnl: f64) -> String`. Consumed only inside `trend.rs`.

- [ ] **Step 1: Write the failing tests**

Inside the existing `#[cfg(test)] mod tests { use super::*; ... }` block in `trend.rs`, add:

```rust
    #[test]
    fn trend_exit_message_loss_is_loud_with_running_total() {
        let msg = trend_exit_message("ETH-USDT", "stop_loss", 1800.0, -259.25, -472.20);
        assert!(msg.starts_with("🛑 LOSS Trend ETH-USDT stop_loss"), "got: {msg}");
        assert!(msg.contains("$-259.25"), "should show this trade pnl: {msg}");
        assert!(msg.contains("Trend running: $-472.20"), "should show running total: {msg}");
    }

    #[test]
    fn trend_exit_message_win_is_rocket_no_loss_marker() {
        let msg = trend_exit_message("BNB-USDT", "tp1", 614.5, 30.02, 93.49);
        assert!(msg.starts_with("📈 Trend BNB-USDT tp1"), "got: {msg}");
        assert!(!msg.contains("LOSS"), "wins must not be marked LOSS: {msg}");
        assert!(msg.contains("Trend running: $+93.49"), "should show running total: {msg}");
    }
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cargo test --lib trend::tests::trend_exit_message trend::tests::trend_exit_message_win` (from `trading-engine-core/`)
Expected: FAIL — `trend_exit_message` not defined (cannot find function).

- [ ] **Step 3: Add the `trend_exit_message` function**

In `trend.rs`, immediately after the `trend_entry_message` function (around L714), add:

```rust
fn trend_exit_message(pair: &str, reason: &str, exit_price: f64, pnl: f64, running_pnl: f64) -> String {
    let marker = if pnl < 0.0 { "🛑 LOSS Trend" } else { "📈 Trend" };
    format!(
        "{} {} {} @ ${:.2} | this: ${:+.2} | Trend running: ${:+.2}",
        marker, pair, reason, exit_price, pnl, running_pnl
    )
}
```

- [ ] **Step 4: Wire `notify_exit` to use it**

Replace the body of `notify_exit` (the `let emoji = …; let msg = format!(…)` lines, ~L354-358) with:

```rust
    fn notify_exit(&self, exit_price: f64, pnl: f64, reason: &str) {
        let msg = trend_exit_message(&self.pair, reason, exit_price, pnl, self.realized_pnl);
        // Fire-and-forget: never block the tick loop on Telegram latency.
        let tg = self.telegram.clone_for_signal();
        tokio::spawn(async move { let _ = tg.send(&msg).await; });
    }
```

(`self.realized_pnl` is the cumulative field, already updated before `notify_exit` is called at trend.rs:451→456, 476, 520→525, 544→549.)

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cargo test --lib trend::tests -v` (from `trading-engine-core/`)
Expected: PASS (both new tests + existing trend unit tests).

- [ ] **Step 6: Commit**

```bash
git add trading-engine-core/src/strategy/trend.rs
git commit -m "feat(trend): loud 🛑 LOSS exit alerts with running realized P&L"
```

---

### Task 2: MR stop-loss alert with running total

**Files:**
- Modify: `trading-engine-core/src/strategy/mean_reversion.rs` — add `pub fn mr_sl_message`; rewrite the SL `tg.send` block (~L299-301).
- Modify: `trading-engine-core/tests/test_mean_reversion.rs` — add a unit test.

**Interfaces:**
- Produces: `pub fn mr_sl_message(pair: &str, price: f64, pnl: f64, running_pnl: f64) -> String` in `mean_reversion.rs` (exported so the integration test can call it).

- [ ] **Step 1: Write the failing test**

Append to `trading-engine-core/tests/test_mean_reversion.rs`:

```rust
#[test]
fn mr_sl_message_is_loud_with_running_total() {
    use trading_engine_core::strategy::mean_reversion::mr_sl_message;
    let msg = mr_sl_message("ETH-USDT", 90.0, -115.89, -149.05);
    assert!(msg.starts_with("🛑 LOSS MR ETH-USDT SL"), "got: {msg}");
    assert!(msg.contains("$-115.89"), "should show this trade pnl: {msg}");
    assert!(msg.contains("MR running: $-149.05"), "should show running total: {msg}");
}
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cargo test --test test_mean_reversion mr_sl_message` (from `trading-engine-core/`)
Expected: FAIL — `mr_sl_message` not found / not exported.

- [ ] **Step 3: Add the `mr_sl_message` function**

In `mean_reversion.rs`, near the top of the file (after the `ReversionSignal`/`Verdict` definitions, before `struct TickData` is fine — i.e. as a free `pub fn` in the module), add:

```rust
/// Stop-loss alert message for MR closes — loud (🛑 LOSS) with the engine's
/// running realized P&L so each alert is self-explanatory.
pub fn mr_sl_message(pair: &str, price: f64, pnl: f64, running_pnl: f64) -> String {
    format!(
        "🛑 LOSS MR {} SL @ ${:.2} | this: ${:+.2} | MR running: ${:+.2}",
        pair, price, pnl, running_pnl
    )
}
```

- [ ] **Step 4: Wire the SL send to use it**

In `mean_reversion.rs`, replace the SL notification block (~L299-301):

```rust
                let pair = self.pair.clone();
                let tg = self.telegram.clone_for_signal();
                tokio::spawn(async move {
                    let _ = tg.send(&format!("⚠️ MR {} SL @ ${:.2} | PnL: ${:+.2}", pair, mid, pnl)).await;
                });
```

with:

```rust
                let pair = self.pair.clone();
                let running = self.realized_pnl;  // cumulative; already includes this SL (updated above)
                let tg = self.telegram.clone_for_signal();
                tokio::spawn(async move {
                    let _ = tg.send(&mr_sl_message(&pair, mid, pnl, running)).await;
                });
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cargo test --test test_mean_reversion -v` (from `trading-engine-core/`)
Expected: PASS (new test + existing MR tests).

- [ ] **Step 6: Commit**

```bash
git add trading-engine-core/src/strategy/mean_reversion.rs trading-engine-core/tests/test_mean_reversion.rs
git commit -m "feat(mr): loud 🛑 LOSS SL alerts with running realized P&L"
```

---

### Task 3: `/trades` pagination

**Files:**
- Modify: `src/notifications/telegram_commands.py::_cmd_trades` (~L1911-1942).
- Modify: `tests/test_telegram_commands.py` — add `TestTradesPagination`.

**Interfaces:**
- Consumes: the dispatcher passes full command text as `update.message.text` (context is `None`).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_telegram_commands.py`:

```python
class TestTradesPagination:
    def _seed(self, tmp_path, n=20):
        import sqlite3
        d = tmp_path / "data"
        d.mkdir(exist_ok=True)
        c = sqlite3.connect(d / "trades.db")
        c.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT, "
                  "engine TEXT, pair TEXT, side TEXT, entry_price REAL, exit_price REAL, "
                  "quantity REAL, pnl REAL, exit_reason TEXT, duration_mins INTEGER, "
                  "is_backfilled INTEGER DEFAULT 0)")
        for i in range(n):
            c.execute("INSERT INTO trades (timestamp,engine,pair,pnl,exit_reason,quantity) "
                      "VALUES (?,?,?,?,?,?)",
                      (f"2026-06-{(i % 27) + 1:02d}T00:00:00Z", "trend", "ETH-USDT",
                       float(i), "signal_exit", 1.0))
        c.commit(); c.close()

    def _u(self, text):
        u = _mock_update()
        u.message.text = text
        return u

    def test_default_is_page_one_with_next_hint(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        monkeypatch.chdir(tmp_path)
        out = _replied(_handler(tmp_path)._cmd_trades(self._u("/trades"), None))
        assert "Page 1" in out and "/trades 2 for older" in out, out

    def test_page_two_footer_advances(self, tmp_path, monkeypatch):
        self._seed(tmp_path, 20)
        monkeypatch.chdir(tmp_path)
        out = _replied(_handler(tmp_path)._cmd_trades(self._u("/trades 2"), None))
        assert "Page 2" in out and "/trades 3 for older" in out, out

    def test_page_beyond_data_suggests_previous(self, tmp_path, monkeypatch):
        self._seed(tmp_path, 5)
        monkeypatch.chdir(tmp_path)
        out = _replied(_handler(tmp_path)._cmd_trades(self._u("/trades 99"), None))
        assert "page 99" in out and "/trades 98" in out, out

    def test_invalid_page_arg_shows_usage(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out = _replied(_handler(tmp_path)._cmd_trades(self._u("/trades abc"), None))
        assert "Usage" in out, out

    def test_no_trades_page_one(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        out = _replied(_handler(tmp_path)._cmd_trades(self._u("/trades"), None))
        assert "No trades yet" in out, out
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_telegram_commands.py::TestTradesPagination -v`
Expected: FAIL — footer lacks "Page N" / "/trades N for older" (current footer is "{n} most recent"); beyond-data returns "No trades yet." not the page-99 message.

- [ ] **Step 3: Rewrite `_cmd_trades` for pagination**

Replace the entire body of `_cmd_trades` in `src/notifications/telegram_commands.py` (the `try:` block, ~L1912-1942) with:

```python
    def _cmd_trades(self, update, context):
        """Show recent individual trades across all bots from trades.db.

        Pagination: /trades (page 1) or /trades N (page N).
        """
        try:
            import sqlite3
            logger.info("Telegram /trades received")
            # Parse optional page number from the message text (e.g. "/trades 2").
            parts = (update.message.text or "").split()
            page = 1
            if len(parts) > 1:
                try:
                    page = int(parts[1])
                except ValueError:
                    update.message.reply_text("Usage: /trades [page]  (e.g. /trades 2)")
                    return
            if page < 1:
                update.message.reply_text("Usage: /trades [page]  (e.g. /trades 2)")
                return
            page_size = 15
            offset = (page - 1) * page_size
            conn = sqlite3.connect("data/trades.db")
            # ORDER BY timestamp (trade time), NOT id — backfill re-inserts old
            # trades with fresh high IDs every restart. Filter qty=0/pnl=0 rows
            # (paper-engine artifacts, not real trades).
            rows = conn.execute(
                "SELECT timestamp, engine, pair, pnl, exit_reason FROM trades "
                "WHERE NOT (pnl = 0 AND COALESCE(quantity, 0) = 0) "
                "ORDER BY timestamp DESC LIMIT ? OFFSET ?",
                (page_size, offset),
            ).fetchall()
            conn.close()
            if not rows:
                if page == 1:
                    update.message.reply_text("No trades yet.")
                else:
                    update.message.reply_text(f"No trades on page {page}. Try /trades {page - 1}.")
                return
            lines = ["📜 <b>Recent Trades</b> (all engines)", "•••"]
            for ts, engine, pair, pnl, reason in rows:
                when = f"{ts[5:10]} {ts[11:16]}" if len(ts) >= 16 else ts
                sign = "+" if pnl >= 0 else ""
                emoji = "🟢" if pnl >= 0 else "🔴"
                p = pair.replace("-USDT", "").replace("-USD", "")
                lines.append(f"{when}  {engine:<6} {p:<8} {reason:<12} {emoji} {sign}${pnl:.2f}")
            lines.append("•••")
            lines.append(f"Page {page} · /trades {page + 1} for older")
            update.message.reply_text("\n".join(lines), parse_mode="HTML")
        except Exception as e:
            update.message.reply_text(f"⚠️ Error: {e}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_telegram_commands.py -v`
Expected: PASS (5 new pagination tests + all existing telegram tests).

- [ ] **Step 5: Commit**

```bash
git add src/notifications/telegram_commands.py tests/test_telegram_commands.py
git commit -m "feat(telegram): paginate /trades via /trades [page]"
```

---

## Rollout (after all tasks green)

- Run the full Rust suite (`cargo test`) and the Python suite (`pytest tests/ --ignore=tests/test_telegram_bot.py --ignore=tests/test_feature_engineering.py`) — both green. (The two ignored files need local deps not installed on this machine; CI has them.)
- Push the branch, open a PR. The GH Actions `test` job (cargo test + pytest) gates deploy.
- On merge → build → SSM deploy.
- **Verify post-deploy:**
  - Send `/trades` then `/trades 2` from Telegram — page 2 shows older trades.
  - Wait for (or force via market) a trend/MR stop-loss — the alert reads `🛑 LOSS … | this: $-X | <engine> running: $Y`.
- Spot engine, grid, swing, and order logic untouched.

## Out of scope
- New alerting watcher / external process.
- Inline-keyboard pagination buttons.
- Pagination on `/trend_history`, `/signal_history`.
- Enhancing MR TP or trend entry messages.
