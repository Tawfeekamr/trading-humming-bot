# Binance Futures Signal-Copy Bot — Design

**Date:** 2026-06-24
**Status:** Approved (Approach A)
**Author:** brainstorm session 2026-06-24

## Goal

A second trading bot that copies the same Telegram signals (DeepSeek-parsed) the
spot signal engine already trades, but executes them on **Binance USDT-M
perpetual futures** with modest leverage and **both long and short** direction.
The signal engine is the only profitable engine today (+$1,123 all-time, 7W/3L);
futures amplify that edge and unlock shorts the spot engine currently ignores.

## Locked decisions

| Decision | Choice |
|---|---|
| Strategy source | Signal-copy (reuse the Telegram/DeepSeek signal engine) |
| Coexistence | **New bot alongside** spot paper (separate instance + container) |
| Environment | **Binance Futures testnet** first |
| Direction | **Long + Short** |
| Leverage / margin | **3x max, isolated margin** |

Risk defaults (adjustable): ~1% risk per trade of the futures budget, max 2
concurrent positions, SL always set and validated inside the liquidation price.

## Architecture (Approach A)

A second bot instance — its own container `trading-signal-futures` (same image,
futures config) — runs alongside the existing spot signal-copy. It runs its own
Telethon listener against the same channels and drives a `SignalEngine` wired to
a **Binance USDT-M fapi connector** (testnet), 3x isolated, long + short. The
spot paper engine keeps running untouched for comparison.

Rationale for reuse: the `SignalEngine` already abstracts execution behind
`buy_fn`/`get_price_fn`, and we just hardened it (entry-tuning, score-in-reject,
dedup). Reusing it avoids a fork and keeps one code path. The engine's
direction-neutral parse → validate → risk → execute → manage pipeline stays; we
add a direction/side dimension and a futures execution backend.

## Components

### NEW `src/signals/binance_futures_connector.py`
HMAC-signed fapi REST client (mirrors the signing pattern in
`trading-engine-core/src/connector/binance_rest.rs`). Testnet base
`https://testnet.binancefuture.com`.
- `set_leverage(symbol, leverage)` → `POST /fapi/v1/leverage`
- `set_margin_type(symbol, "ISOLATED")` → `POST /fapi/v1/marginType` (swallow
  "no need to change" error)
- `open(symbol, side, qty, order_type="MARKET", price=None)` — `side` is
  `"BUY"` (long) / `"SELL"` (short) → `POST /fapi/v1/order`
- `close(symbol, side, qty)` — reduce-only opposite order
- `get_position(symbol)` → `{qty, entry_price, liquidation_price, unrealized_pnl}`
- `get_price(symbol)` → mark price (`/fapi/v1/premiumIndex`)
- Errors are caught and surfaced so the engine can notify (no silent failures —
  same rule as the spot rejection-notify work).

### EXTEND `src/signals/signal_parser.py`
- Add `SignalAction.OPEN_SHORT`.
- Relax SYSTEM_PROMPT rule 2 so DeepSeek **extracts** short signals (action
  `OPEN_SHORT`) instead of rejecting them. The **spot** engine still rejects
  shorts (its validator/config ignores `OPEN_SHORT`); only the futures engine
  acts on them.
- The entry-tuning rule already added (2026-06-23) is direction-neutral and
  applies to shorts by symmetry (a short whose price moved below its short
  entry zone tunes the same way; SL/TPs kept).

### EXTEND `src/signals/signal_position.py`
- Add `side: str` (`"long"` / `"short"`).
- Short PnL = `(entry_price − exit_price) × qty` (inverted vs long).
- Short SL is **above** entry; TPs are **below** entry. Scale-out (TP1/2/3) and
  SL→breakeven logic mirrors longs with inverted comparisons.
- Closes use reduce-only via the connector.

### EXTEND `src/signals/signal_risk.py`
- Position sizing keeps today's risk-distance method
  (`qty = $risk / |entry − SL|`), then:
  - `notional = qty × price`; `margin = notional / leverage`.
  - Reject if `margin` exceeds the available futures-budget slot.
  - **Liquidation buffer:** compute the liquidation price for the leverage/margin
    and **reject (skip + notify)** if the signal's SL would only be breached
    after liquidation — i.e. the SL must trigger first. v1 rejects rather than
    trims, for simplicity and safety. This is mandatory, not optional.

### EXTEND `src/signals/signal_engine.py`
- Direction-aware execution **for futures mode only**: a small executor interface
  (`open(signal, side, qty)`, `close(symbol, side, qty)`, `get_price`,
  `get_position`) implemented by `BinanceFuturesConnector`. The engine's
  open/close calls branch on `self._futures_mode`; **the spot path (`buy_fn`) is
  left untouched** so the working spot engine is not put at risk. One engine,
  mode-selected backends.
- Reuses the existing pipeline: parse (now long/short) → validate (long+short
  branches) → entry-tuning → risk (leverage sizing + liquidation buffer) →
  `set_leverage` + open → manage (scale TPs, SL→BE, reduce-only close) →
  journal + notify.

### NEW config + entrypoint
- Config block `signals_futures`: `enabled`, `testnet`, `leverage: 3`,
  `margin_type: isolated`, `risk_pct: 1.0`, `max_positions: 2`, `pairs`
  (watchlist / liquidity filter), `capital_usdt`.
- Boot path: `SIGNAL_MODE=futures` (or a second listener entrypoint) constructs
  the `SignalEngine` with the futures connector + config. Deployed as a second
  container reusing the existing image.
- Telegram: `/futures_status`, `/futures_pnl` mirroring the existing signal
  commands; reuses the notification + dedupe path.

## Risk model
- **3x isolated** — one position's loss is capped to its own margin; a bad trade
  can't cascade-liquidate the account.
- **~1% risk per trade** of the futures budget.
- **Max 2 concurrent positions.**
- **SL always set** and validated to sit inside the liquidation price.
- **Leverage set per-symbol on open** (fapi `set_leverage`).
- **Funding rate logged** per position, not used as a trade trigger in v1.

## Data flow
Telegram msg → DeepSeek parse (`OPEN_LONG`/`OPEN_SHORT` + entry-tuning) →
validate (direction branches) → risk (leverage sizing + liquidation buffer) →
connector `set_leverage` + `set_margin_type` + `open` → manage (TP1/2/3 scale,
SL→breakeven, reduce-only close on TP/SL/trader-close) → journal + notify.
Identical shape to the spot flow, futures-aware.

## Testing
- **Connector** unit tests with mocked fapi responses (order ack, position,
  liquidation price, error paths).
- **Short PnL math** — winning short and losing short compute inverted correctly
  vs long.
- **Leverage sizing + liquidation-buffer reject** — a signal whose SL would be
  beyond liquidation is rejected/trimmed.
- **Parser** extracts `OPEN_SHORT` from a short signal message.
- **Regression:** all existing long-path signal tests stay green; spot engine
  behavior unchanged.

## Phasing / rollout
1. Build long + short together on **testnet**.
2. Observe 2–4 weeks: win rate, liquidation near-misses, funding drag, fill
   slippage vs the spot copy, and whether shorts add or subtract edge.
3. Go live = flip the testnet flag + start at small size (mirrors the August
   spot go-live discipline).

## Risks & out of scope
- **No backtest** — signal-copy depends on live Telegram signals that can't be
  replayed. Testnet observation replaces backtesting.
- **Testnet endpoint quirks** — the spot testnet had WebSocket 404s. Must
  validate the **fapi testnet** (`testnet.binancefuture.com`) works (REST at
  minimum) before trusting any result.
- **Leverage bugs are costly** even on testnet — the liquidation-buffer check is
  mandatory; connector error paths must notify, never fail silently.
- **Funding** not traded in v1 (logged only).
- Out of scope: a Rust futures connector (Approach B), grid/market-making on
  futures, cross-margin.
