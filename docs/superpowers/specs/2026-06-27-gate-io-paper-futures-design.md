# Gate.io Paper Futures Fallback — Design

**Date:** 2026-06-27
**Status:** Approved (Approach #1 — new `PaperFuturesConnector`)
**Author:** brainstorm session 2026-06-27
**Supersedes the execution model in:** `2026-06-24-binance-futures-signal-bot-design.md`

## Goal

The futures signal engine has executed **zero trades** since its 2026-06-24
deploy because every entry for any coin outside the Binance **testnet** trading
set fails with `fapi … HTTP 400: {"code":-1121,"msg":"Invalid symbol."}`. The
Binance testnet *market-data* endpoints proxy all of mainnet (so `premiumIndex`
returns a price for ICP/FET/INJ), but the *trading* endpoints (`/leverage`,
`/order`) accept only ~30 majors. Signals like ICP-USDT, FET-USDT, INJ-USDT die
at `set_leverage()` and never trade.

Fix the futures engine by making it a **pure paper simulator** priced off
**Gate.io USDT-perpetual** data (807 contracts, incl. all majors + ICP/FET/INJ),
consistent with how the spot engine already paper-trades. No real money, ever.

## Locked decisions

| Decision | Choice |
|---|---|
| Execution mode | **Paper only** (no real money, no real exchange orders) |
| Price source | **Gate.io USDT-perp** (`/api/v4/futures/usdt/tickers`) for **all** coins |
| Binance testnet | **Retired** — unwired; no `BINANCE_FUTURES_KEY/SECRET` required |
| Connector shape | **New `PaperFuturesConnector`** (same interface as `BinanceFuturesConnector`) |
| Leverage / margin | 3×, isolated (unchanged — simulated via `futures_math.py`) |
| Direction | Long + Short (unchanged) |
| Spot engine | **Untouched** — still paper-trades + profitable (+$1,123.53) |

## Background: two engines, one listener

The `trading-signal-listener` container runs **two** `SignalEngine` instances
fed by a single Telethon listener (`run_signal_listener.dispatch_cycle` sends
every message to both `process_one`):

- **Spot** engine — paper-trades via the Rust engine API; prices/volume from
  Gate.io *spot*. Profitable (+$1,123.53, 52 trades). **Not changed by this work.**
- **Futures** engine — `futures_mode=True`, headless (`own_listener=False`,
  `state_suffix="_futures"`). Currently wired to `BinanceFuturesConnector`
  (testnet) → broken for ~all incoming signals.

This design changes only which connector the **futures** engine holds.

## Why not "Binance for majors, Gate for the rest"

In paper mode there is no execution difference between exchanges — only a price
source. Maintaining two price sources + the `-1121` fallback adds complexity and
splits P&L accounting (real-testnet vs simulated rows) for no benefit. Gate.io's
807-contract perp book covers every coin the signal channel trades, so one
source is simpler, more reliable, and matches the spot engine's single-source
pattern. The `BinanceFuturesConnector` file is left dormant in the tree for
reference but is no longer constructed.

## Architecture (Approach #1)

```
run_signal_listener.main()
└── signals_futures.enabled?  ──yes──►  PaperFuturesConnector(gate perp)
                                         │
                                         ▼
        SignalEngine(futures_mode=True, futures_connector=PaperFuturesConnector,
                     get_price_fn=PaperFuturesConnector.get_price,
                     state_suffix="_futures")
                                         │
        process_one → _get_current_price → connector.get_price (Gate perp mark)
        _execute_futures_entry → set_leverage/set_margin_type (no-op) → open (synthetic)
        _manage_futures_positions → direction-aware TP1/2/3 + SL→BE → close (no-op)
                                         │
                                         ▼
        signal_journal_futures.db  (entries/closes + realized PnL)
        signal_positions_futures.json (open leveraged positions)
```

The engine's parse → validate → risk → execute → manage pipeline is unchanged.
The leverage sizing, liquidation-buffer gate, and side-aware P&L already live in
`futures_math.py` + `signal_risk.py` + `signal_position.py` and are reused as-is.

## Components

### 1. NEW `src/signals/paper_futures_connector.py`

Implements the same surface `BinanceFuturesConnector` exposes (the engine calls
only these methods):

| Method | Behavior |
|---|---|
| `get_price(symbol)` | `symbol` `ICP-USDT` → contract `ICP_USDT`; GET `https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=<c>`; return `float(mark_price)` (fallback `last`). Return `0.0` on any error / unknown contract. Pure-public, **no auth, no keys**. |
| `set_leverage(symbol, lev)` | No-op; return `{"msg": "paper"}`. |
| `set_margin_type(symbol, type)` | No-op; return `{"msg": "paper"}`. |
| `open(symbol, side, qty, …)` | Return `{"orderId": "paper_fut_< monotonic counter >", "status": "FILLED"}`. No state held (the engine's `position_mgr` is authoritative). |
| `close(symbol, side, qty)` | No-op; return `{"orderId": "paper_fut_<counter>", "status": "FILLED"}`. |
| `get_position(symbol)` | Return `None` (engine tracks via `position_mgr`; never relied on for paper). |

Notes:
- Constructor: `PaperFuturesConnector()` — no credentials. (An optional
  `default_leverage` kwarg is accepted for interface parity with
  `BinanceFuturesConnector` but is unused — leverage lives in the engine's risk
  math, not the connector.)
- A monotonic in-process counter for order ids (not wall-clock — see test
  determinism). Counter starts at 1 per instance; fine because ids only need to
  be unique within one process lifetime.
- `get_price` is the only network call; hard-timeout 5s, log on failure.

### 2. `src/run_signal_listener.py`

Replace the `BinanceFuturesConnector` construction block: when
`signals_futures.enabled` is true, build `PaperFuturesConnector` and wire it as
`futures_connector` + `get_price_fn`. **Remove the `BINANCE_FUTURES_KEY/SECRET`
gate** — futures now builds unconditionally on the config flag (today the
missing-key path silently downgrades to spot-only, which masked nothing here but
would block a keys-less redeploy). Keep the `SIGNAL_MODE=futures` legacy env as
an alternate enable. Log `"Futures Signal Engine built (paper, Gate.io perp)"`.

### 3. `config/strategy.yaml` — `signals_futures`

```yaml
signals_futures:
  enabled: true
  exchange: gate_io_paper_futures   # NEW; retires binance testnet
  testnet: false                     # paper has no testnet; kept for compat reads
  leverage: 3
  margin_type: isolated
  allow_shorts: true
  per_trade_risk_pct: 1.0
  max_positions: 2
  max_capital_usdt: 10000
  pairs: []
  enabled_pairs: []
```

### 4. `src/signals/signal_engine.py` — one tweak in `_get_current_price`

Today `_get_current_price` falls through to a **Gate.io spot** REST fetch if the
primary price fn returns ≤0. For a leveraged futures sim, silently substituting
the *spot* price would mis-price entries/PnL. Add: in `futures_mode`, when
`get_price_fn` returns ≤0, **return 0 immediately** (skip the spot fallback) so
the existing `current_price <= 0` skip path + Telegram notify fires cleanly.
(One-line guard; no behavior change for the spot engine.)

## Data flow — ICP-USDT OPEN_LONG (the previously-broken case)

1. Signal → both engines' `process_one`. Futures `_seen_signal_ids_futures`
   dedup (msg not seen) → proceeds.
2. Pre-scan `_get_current_price(connector, "ICP-USDT")` →
   `PaperFuturesConnector.get_price` → Gate perp `mark_price` ≈ $2.17. ✅
3. DeepSeek parse → validate → risk: `get_budget_for_trade(leverage=3)` sizes
   notional by `risk_amount / sl_distance_pct` (capped at `max_position_pct`),
   and `sl_triggers_before_liquidation` rejects if SL is beyond the estimated
   3× liquidation (raises `LiquidationBufferError` → notify + skip).
4. `_execute_futures_entry`: `set_leverage`/`set_margin_type` no-ops →
   `open(ICP, long, qty)` returns synthetic id → `position_mgr.open_position`
   records the leveraged long (entry, SL, TPs, side).
5. Manage ticks: `_manage_futures_positions` checks Gate price each cycle →
   direction-aware TP1 (33%) / TP2 (50%) / TP3 (rest) + SL→breakeven → each
   `close()` is a no-op → P&L recorded into `signal_journal_futures.db`.

## Error handling

- **Gate perp price unavailable** (Gate down / unknown contract / timeout):
  `get_price` returns `0.0` → engine hits `current_price <= 0` → signal skipped
  with a Telegram notify (existing path). No silent spot substitution (the
  `_get_current_price` futures-mode guard, Components #4).
- **`open`/`close`** are paper → cannot fail at an exchange; the only failure
  mode is the price feed, handled above.
- **SL beyond liquidation** → existing `LiquidationBufferError` → notify + skip.
- A futures-engine exception is already isolated from spot by the
  `try/except` wrappers in `dispatch_cycle` (spot never dies because of futures).

## Testing (TDD)

Unit (`tests/signals/test_paper_futures_connector.py`):
- Symbol mapping `ICP-USDT` → `ICP_USDT`; `BTC-USDT` → `BTC_USDT`.
- `get_price` parses `mark_price` from a mocked Gate ticker; falls back to
  `last`; returns `0.0` on HTTP error / empty / unknown contract.
- `open`/`close` return unique `paper_fut_*` ids; `set_leverage`/`set_margin_type`
  are no-ops returning success.

Integration (`tests/signals/test_futures_paper_execution.py`):
- Feed a **non-Binance** OPEN_LONG (ICP) through a futures `SignalEngine` wired
  to a mocked `PaperFuturesConnector` → assert a paper position opens, a journal
  row is written, and **no exception** (the `-1121` regression test).
- Feed an OPEN_SHORT → assert short position + side-aware P&L.
- Feed BTC-USDT (major) → opens via Gate prices (same path).

Existing `futures_math` tests cover the liquidation/PnL math; verify coverage of
`estimate_liquidation` at 3× long and short.

## Rollout

- **Zero data migration.** The futures engine has never opened a position
  (`signal_positions_futures.json` does not exist; `signal_trades` has 0 rows).
  The 9 rows in `raw_messages` are retained as audit history.
- `seen_signal_ids_futures.json` keeps its 9 ids — the already-seen ICP/FET/INJ
  will **not** auto-retrade (they're likely stale). Verify the fix with
  `/signal_inject <json>` or the next fresh signal.
- Ship via the existing GH Actions `test → build-signal → SSM pull/up` pipeline.
  The `test` job (pytest + the new tests) gates deploy.

## Out of scope

- Real-money execution (deferred past the August go-live review by user choice).
- Re-wiring shorts into the spot engine.
- Funding-rate modeling beyond logging (unchanged from 2026-06-24 spec).
- Any change to the spot engine, grid, trend, swing, or mean-reversion engines.
