# Trades and P&L Audit — 26 November 2026

Paper trading only (no real money). Read-only audit of the live server's records:
`data/trades.db` (130 rows), `data/signal_journal.db` (199 rows),
`data/signal_journal_futures.db` (137 rows), the signal position files and the engine API.

## 1. True realised P&L

| Engine | Trades | Realised P&L | Source |
|---|---:|---:|---|
| Grid | 13 | **+$728.08** | trades.db (live rows) |
| Trend | 20 | **−$76.26** | trades.db (live rows) |
| Signal, spot | 54 | **+$1,376.52** | journal 3–16 Jun (+$999.00) + trades.db 17 Jun–30 Sep (+$754.16) + journal Oct–Nov (−$376.64) |
| Signal, futures (paper) | 40 | **+$253.07** | futures journal (plus ≈ +$39–50 already taken on the one open position, NEAR) |
| **Total** | **127** | **+$2,281.41** | |

Signal results by period: strong in June (+$1,123), weaker from August, and **negative since
October**: spot −$376.64 and futures −$581.12 (−$437.42 Oct, −$143.70 Nov), so **−$957.76 in
October–November combined**.

Trend: fees ($89.44) are larger than the loss. Before fees, trend made +$13.18, so the
strategy roughly broke even and the fees made it a loss.

## 2. How the numbers were checked

- **Grid and trend:** every trade's stored P&L was recomputed from entry, exit, quantity and
  fees. All trend trades match. The large grid trades match within their fees.
- **Signal trades** close in stages (33% at take-profit 1, then 50% of the rest at
  take-profit 2), and the row stores the *remaining* quantity and the *cumulative* P&L.
  Rebuilding the stages reproduces 30 of 35 trades exactly; 2 differ by exactly one fee
  ($3.35, fee field empty); 3 early trades have no stored details.
- **No duplicate trades** were found.

## 3. Problems found

| # | Problem | Effect | Severity |
|---|---|---|---|
| 1 | **The main trade table stopped recording signal trades on 26 Sep 2026.** Since the 1 Oct fix that made the Rust signal manager read-only, closes are only written to the Python journal (and copied into trades.db as `is_backfilled=1` event rows). | Any report that reads only the live rows of trades.db **misses all Oct–Nov signal trades** (−$376.64 spot) and **all futures trades** (+$253.07). | High (reporting) |
| 2 | **Back-filled rows are events, not trades.** Take-profit rows hold one slice's P&L; the final row holds the running total. | Summing every row gives **$2,975.67**, overstating the true figure by about $700. | High (reporting) |
| 3 | **Before 1 Oct, two managers handled signal positions** (Rust and Python). Trades closed by Rust were never closed in the Python journal. | The journal shows 16 "open" positions that are actually closed (the position state confirms 0 open on spot). The journal alone **under-states June–September** signal P&L. | Medium (historical; fixed 1 Oct) |
| 4 | **Signal entries chased the price.** 9 of 32 spot signal entries filled more than 3% above the signal's entry range; in 6 the first take-profit was **at or below** the entry, so "take-profits" were booked at a loss. Worst: UNI, 21% above range, −$161.76 (−9.2R). | Avoidable losses; a `skipped_above_zone` guard exists (15 skips) but these entries passed it, apparently via the entry-tuning path. | High (strategy) |
| 5 | **Exit labels are misleading.** 23 profitable signal exits are labelled `stop_loss`; they are trailing or break-even stops after take-profits. | Win/loss reporting by exit reason is wrong. | Low |
| 6 | **Missing fields.** `duration_mins` missing on 48/68 live rows; `fees` missing on 18. | Duration and fee analysis incomplete. | Low |
| 7 | **Feed period.** 23 of 33 grid/trend trades (+$669.88) were made while the live feed was connected to the test network (31 May–18 Sep), so their fill prices are not fully reliable. | Grid's +$728 rests mostly on this period. | Medium (data quality) |
| 8 | **Grid fee-only rows.** 7 ETH grid "sells" on 21–22 Jun at exactly the buy price, P&L = −fee only (−$1.70 total). | Small paper-churn artefact. | Low |

The phantom BNB tick (July) is recorded as one trend row with P&L $0, so it no longer
distorts the totals.

## 4. Recommended fixes

1. One P&L report that uses the correct source per period (as in section 1), instead of
   summing trades.db.
2. Write signal closes (spot and futures) as one trade row each in trades.db, with the
   cumulative P&L, so the main table is complete again.
3. Reject a tuned entry if the first take-profit is at or below it (or recompute the targets).
4. Label exits by what happened: `trailing_stop` / `breakeven_stop` after a take-profit.
5. Always fill `duration_mins` and `fees`.

## 5. Fixes applied (26 Nov 2026, commits ef12c05, 41d9c57, 7642dbb)

| Problem | Fix |
|---|---|
| 1. Signal trades missing from trades.db | Each completed signal trade (spot as `signal`, futures as `signal_futures`) is written once to trades.db when it closes, with net P&L, fees and duration. Past journal trades are copied in on start-up, including the futures journal. |
| 2. Event rows double-counted | Start-up copy takes only each trade's final row (not the take-profit slices). The journal's own summary does the same. |
| 4. Entries chased the price | An entry is skipped if the price is already at or past TP1, or more than 3% beyond the entry range (tuned spot entries and all futures entries). |
| 5. Misleading exit labels | A stop hit after a take-profit is now `breakeven_stop` (or `trailing_stop` after TP2 on spot). Older rows keep their old label. |
| 6. Missing duration and fees | Filled on every new signal row. |

**Checked on the server after deploy:** summing every row of trades.db now gives
**+$2,281.41**, the same as the true total in section 1 (spot signal 754.16 + 622.36 =
1,376.52; futures 253.07; grid 728.08; trend −76.26). No errors in the logs.
