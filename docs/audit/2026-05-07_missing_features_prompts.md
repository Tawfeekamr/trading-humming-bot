# 🚀 Missing Features — Implementation Prompts

**Date**: 7 May 2026  
**Status**: Ready to execute — run prompts 1 → 2 → 3 in order  
**Related**: [Full Implementation Audit](./2026-05-07_full_implementation_audit.md)

---

## Feature Summary

| # | Feature | Priority | Effort | Prompt |
|---|---------|----------|--------|--------|
| 1 | Dashboard Auth + CSV Export + Cleanup | 🔴 High | 1h | Prompt 1 |
| 2 | Telegram Bot Commands | 🔴 High | 3h | Prompt 2 |
| 3 | Auto Daily Reset + Fee Detection | 🟡 Medium | 1h | Prompt 3 |

**Estimated total effort**: ~5 hours

---

## Prompt 1 — Dashboard Auth + CSV Export + Cleanup

```
Implement these 3 changes:

## 1. Dashboard Authentication
Add password auth to app.py using streamlit-authenticator:
- Add streamlit-authenticator to requirements.txt
- Read DASHBOARD_USERNAME and DASHBOARD_PASSWORD_HASH from environment variables
- Block the entire dashboard behind a login form
- Use a 30-day cookie so the user stays logged in
- Add a logout button in the sidebar
- If env vars are missing, show a warning and block access (don't fall back to no auth)

## 2. CSV Export Button
Add a "📥 Download CSV" button to the Trade History section of app.py:
- Export all trades as a CSV file with all columns
- Use st.download_button with the CSV data
- Filename format: trades_export_2026-05-07.csv (with today's date)
- Place it next to the trade count caption

## 3. Delete Duplicate Root Files
- Delete /trade_journal.py (root level) — canonical copy is at src/journal/trade_journal.py
- Delete /sheets_sync.py (root level) — canonical copy is at src/journal/sheets_sync.py
- Move /pnl_reporter.py to src/notifications/pnl_reporter.py
- Update any imports that reference the root-level files
- Verify nothing breaks by running: python -c "from src.journal.trade_journal import TradeJournal; from src.notifications.telegram_bot import TelegramBot; print('OK')"
```

---

## Prompt 2 — Telegram Bot Commands

```
Add interactive Telegram command handling to the trading bot so the user can control and query the bot from their phone.

## Create src/notifications/telegram_commands.py

Build a TelegramCommandHandler class that:
- Uses python-telegram-bot's Application (async) to listen for commands
- Runs in a background thread so it doesn't block Hummingbot's event loop
- Takes references to: TradeJournal, GridStateMachine, CircuitBreaker, PositionGuard, EventLogger
- Reads TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID from env
- Only responds to messages from the configured TELEGRAM_CHAT_ID (ignore strangers)

## Implement these commands:

### /status
Reply with:
- Grid state (ACTIVE / PAUSED / REACTIVATING)
- Current mode (paper/live)
- Circuit breaker status (OK / HALTED)
- Uptime since bot started
- Number of pending orders

### /pnl
Reply with:
- Today's net PnL, trade count, win rate
- This week's net PnL
- This month's net PnL
- All-time net PnL
Use the TradeJournal.summary_today(), summary_this_week(), etc.

### /pause
- Set a flag that the strategy script checks on each tick
- When flag is set, cancel all orders and hold USDT
- Reply: "⏸️ Grid manually paused. Use /resume to restart."

### /resume
- Clear the pause flag
- Reply: "🟢 Grid resumed. Will activate on next valid signal."

### /reset
- Clear the circuit breaker halted flag (set self.circuit_breaker.halted = False)
- Reset peak equity to current equity
- Reply: "🔄 Circuit breaker reset. Bot will resume on next tick."

### /trades
- Show last 5 trades with side, price, net PnL, time ago
- Use TradeJournal.get_trades() with limit

### /help
- List all available commands with descriptions

## Integration

In ta_grid_btcusdt.py __init__:
- Create the TelegramCommandHandler and start it
- Pass the necessary references (journal, state_machine, circuit_breaker, etc.)
- Add a _manual_pause flag that on_tick() checks alongside state_machine.is_paused

In on_stop():
- Stop the command handler gracefully
```

---

## Prompt 3 — Auto Daily Reset + Fee Detection

```
Implement two small but important fixes:

## 1. Auto Start-of-Day Equity Reset

In ta_grid_btcusdt.py:
- Add a _last_sod_reset: Optional[str] field initialized to None
- At the start of on_tick(), check if today's date (UTC) differs from _last_sod_reset
- If it's a new day:
  - Call self.circuit_breaker.set_start_of_day_equity(current_equity)
  - Set _last_sod_reset to today's date string
  - Log an event: event_type="daily_reset", equity=current_equity
  - Log: logger.info(f"Start-of-day equity reset: ${equity:.2f}")
- This ensures the daily_loss_limit_pct circuit breaker actually resets each day

## 2. Fee Tier Auto-Detection

In ta_grid_btcusdt.py __init__:
- Add a self._fee_rate: float = 0.00075 (default: 0.075% standard tier)
- After the CandleFeed is initialized, try to fetch the account's fee tier:
  - Use self.candle_feed.client.get_trade_fee(symbol="BTCUSDT")
  - Extract the maker fee from the response
  - If BNB discount is active, Binance returns 0.0005 (0.05%) instead of 0.00075
  - Wrap in try/except — if it fails, keep the default 0.00075
  - Log the detected fee rate: logger.info(f"Fee rate: {self._fee_rate*100:.4f}%")
- Replace all hardcoded 0.00075 in did_fill_order with self._fee_rate
- Log an event: event_type="fee_detected", rate=self._fee_rate
```

---

## Future Roadmap (Not Yet Prompted)

These features are planned for later phases:

| Feature | Phase | Notes |
|---------|-------|-------|
| ETH/USDT second grid | Phase 5 | Requires multi-pair architecture |
| ML signal layer (FreqAI) | Phase 5 | Needs 3+ months of trade data first |
| OKX failover exchange | Phase 5 | Requires exchange abstraction layer |
| Multi-timeframe confirmation (4h + 1h) | Phase 4 | Would reduce false signals |
| Slippage tracking | Phase 4 | Compare order price vs actual fill |
| Walk-forward auto-reoptimize | Phase 4 | Re-run param sweep monthly |
