# Signal Salvage (DeepSeek Tuning) + Display/Rejection Fixes

**Date:** 2026-06-23
**Status:** Approved

## Problem

1. **"All signals rejected"** — misdiagnosed as validator rejection. In 48h the
   engine processed one signal; it *passed* validation (SL auto-tightened) but
   was **skipped at the entry-zone gate** because price had already moved +7%
   above the zone. The entry-zone skip is where signals die.
2. **Recent Trades display is misleading** — `_cmd_trades` orders by `id DESC`;
   backfill re-inserts old trades with fresh high IDs every restart, so old
   backfilled trades appear as "most recent." Also shows only `HH:MM` (no date)
   and includes `qty=0, pnl=0` MR artifacts.
3. **Score missing from some rejections** — `_signal_detail()` (which prints
   `Score: x/10`) is appended to most skip/reject alerts but not the two
   entry-zone skip paths.

## Design

### Task 1 — Recent Trades display (`src/notifications/telegram_commands.py::_cmd_trades`)
- `ORDER BY id DESC LIMIT 15` → **`ORDER BY timestamp DESC LIMIT 15`**.
- Render date + time (`MM-DD HH:MM`) instead of `HH:MM`.
- Filter `qty=0, pnl=0` artifacts: `WHERE NOT (pnl=0 AND COALESCE(quantity,0)=0)`.
- Make the DB path injectable so tests can point at a temp `trades.db`.

### Task 2 — DeepSeek tuning (single call, entry-only, original SL preserved)
Scope: **entry-zone skip only** (the actual failure point).

Flow (in the engine, replacing the parse → zone-skip dead-end):
1. Regex **pre-scan** the message for the ticker (`AERO/USDT`, `$HYPE`).
2. If found, fetch its **Gate.io live price** (one REST call).
3. Single DeepSeek call: `parser.parse(message, live_price, live_pair)`.
4. `SYSTEM_PROMPT` gains a rule: when `live_price` for the signal's pair is
   given and is outside the entry zone, judge the setup at that price:
   - still valid → set `entry_low = entry_high = live_price`, **keep original SL
     + TPs**, `entry_tuned=true`, `stale=false`.
   - move already happened (price ≥ TP1, or poor R:R at tuned entry) →
     `entry_tuned=false`, `stale=true`.
   - price in zone / no live price → normal.
5. New `ParsedSignal` fields: `entry_tuned: bool = False`, `stale: bool = False`.
6. Engine handling: `stale` → skip with "skipped (stale — price past entry)" +
   score; `entry_tuned` → proceed (zone check passes by construction); else
   normal (existing zone-skip still applies as fallback).
7. Fallback: no ticker found or Gate.io has no price → parse without
   `live_price` → today's behavior.

**Risk safety:** SL stays at the provider's level → $-risk-per-trade unchanged
(position sizing shrinks for the larger entry→SL distance). Because the SL sits
far below a *raised* entry, tuned signals **skip the validator's SL
auto-tighten** (DeepSeek already vetted validity; R:R still re-checked as a
safety net).

### Task 3 — Score in every reject/skip alert
Append `_signal_detail(signal, channel_name)` to the entry-zone skip alerts
(the only paths currently missing it). Applies to whatever skip notification
remains after Task 2 merges the zone path.

## Testing
- **Display**: temp `trades.db` seeded with old high-id backfilled row + newer
  low-id live row → assert timestamp ordering + date shown + artifacts filtered.
- **Parser**: mock DeepSeek → `entry_tuned` when price above-zone-but-valid;
  `stale` when price ≥ TP1; normal when price in zone; unchanged when no price.
- **Engine**: tuned signal proceeds; stale skips with notification; fallback
  (no price) → existing behavior.
- **Pre-scan regex**: extracts tickers from sample channel messages.

## Out of scope
- Validator-side tuning (R:R / quality / missing-field rejects) — explicitly
  deferred (scope choice: entry-zone only).
- The unified-DB dup bug (fixed separately, 2026-06-23).
