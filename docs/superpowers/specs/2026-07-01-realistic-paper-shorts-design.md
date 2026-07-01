# Realistic Paper Shorts — Design

**Date:** 2026-07-01
**Status:** Approved (brainstorm), pending implementation plan
**Scope:** `trading-engine-core` (Rust) — paper fill engine + trend strategy

## Goal

Make the Rust trend engine's paper **short** trades honest enough that, by the
September review, the paper track record is a fair test of whether the side-aware
trend strategy works on a shortable venue. Stay in **paper mode, 1× (no leverage),
no real money** until the September go-live decision.

September live target: real perpetual shorting at 1× notional (no leverage).

## Context / Problem

PR #48 (2026-06-30) made the trend engine side-aware: it can now open shorts on
`Direction::Down`. The trend engine trades through `PaperTradeConnector`, which
simulates shorts via naked negative base balance. Two problems make the resulting
P&L untrustworthy:

1. **Zero-slippage instant fills on taker orders.** In `connector/paper.rs`,
   `try_fill_at_price` fills Market and StopMarket orders at exactly the mark price
   (`fill_price = order.price.unwrap_or(market_price)`, line ~168) with a flat
   0.1% fee. Stop-loss and trailing-stop exits — the orders that matter most for
   risk realism — therefore fill with no adverse movement and no spread. This
   inflates P&L for **every** engine, not just shorts.
2. **Shorts are priced off spot.** A real short trades the perpetual, which
   diverges from spot (especially on alts) and accrues funding every 8h. Paper
   shorts today are marked and filled against the Binance spot feed.

Observed symptom: on 2026-07-01 the trend bot reported +$726 from BNB/DOGE shorts,
with multiple TP levels filling inside 0.01s. That number is not live-realizable.

## Non-Goals

- **No leverage, liquidation, or margin/isolation modeling.** Trend stays 1×. At
  1× a short cannot be liquidated; it simply loses 1:1. Leverage is a separate
  risk dial deferred to a future decision (would require liquidation modeling).
- **No `Connector` trait changes.** The perp mark lives inside the trend strategy,
  not in the shared connector interface, so grid/swing/MR are untouched by it.
- **No live trading.** Everything stays paper until September.
- **No size-dependent (market-impact) slippage in v1.** Flat bps, configurable.
  (Listed under Future Work.)

## Design

Three independent changes. (1) is global and benefits all paper engines; (2) and
(3) are confined to the trend strategy's short path.

### 1. Slippage + tiered fees in the paper fill engine (`connector/paper.rs`)

Modify `PaperTradeEngine::try_fill_at_price` so the fill price and fee depend on
order type:

- **Taker orders** (`OrderTypeReq::Market`, `StopMarket { .. }`) — covers stop-loss
  and trailing-stop exits, and market entries:
  - `fill_price = market_price * (1 + adverse_sign * slippage_bps / 1e4)`
    where `adverse_sign = +1` for `Buy`, `-1` for `Sell` (buys fill above mark,
    sells below).
  - Fee: `taker_fee_bps`.
- **Maker orders** (`OrderTypeReq::Limit`, `LimitMaker`) — covers TP fills:
  - `fill_price = order.price` (the resting limit) — **unchanged**, a resting TP
    does not slip.
  - Fee: `maker_fee_bps`.

Defaults preserve current behavior: `slippage_bps = 0`, `taker_fee_bps = maker_fee_bps = 10`
(0.1%). Realism is opt-in via config (suggested start: `slippage_bps = 8`; realistic
Binance USDT-M fees ~0.05% taker / 0.02% maker, configurable).

The `fill_cooldown_ms` anti-churn guard and `reduce_only` inventory enforcement
are unchanged.

**Why global:** zero-slippage taker fills inflate every engine's exits. Applying
slippage here is the single highest-ROI honesty fix and costs the non-trend
engines nothing but realism.

### 2. Perp mark price for trend shorts (new `trading-engine-core/src/perp_price.rs`)

A small helper that fetches and caches the Gate.io USDT-perpetual mark price for a
symbol, porting the pricing approach from the Python
`src/signals/paper_futures_connector.py` (endpoint family
`/futures/usdt/tickers`). Cache at bar cadence; tolerate fetch failure by falling
back to the last good mark (and logging).

The **trend strategy's short path** uses this perp mark instead of the spot bar
price for:

- unrealized PnL on open shorts (`trend.rs` short PnL, ~line 693),
- TP / SL / trailing-stop trigger comparisons (~lines 529-541),
- the fill price handed to the paper engine for short exits.

**Longs and all other engines continue to use spot bars.** No change to their
price source. The perp mark is requested only by trend and only for shorts, so
grid/swing/MR see no behavioral or rate-limit change.

### 3. Funding accrual on trend shorts (trend strategy)

While a trend short is open, accrue funding at each funding interval (every 8h,
aligned to the venue's funding timestamps):

- `funding_pnl = -funding_rate * position_notional` where `position_notional =
  |entry_price| * remaining_qty` and `funding_rate` comes from the Gate.io perp
  funding-rate field.
- Sign: positive funding rate → shorts pay (PnL negative); negative → shorts
  receive.
- Applied to the position's realized PnL and journaled as a dedicated row via
  `log_unified` with `engine="trend"`, `exit_reason="funding"`, `quantity=0`,
  `pnl=funding_pnl`, so the drag appears in `/trades` and the unified journal as a
  visible, filterable line (`WHERE exit_reason='funding'`).

Funding accrual stops when the short closes.

## Config Surface (`config/strategy.yaml`)

Additive, under a new `paper` block and the existing `trend` block:

```yaml
paper:
  slippage_bps: 0        # 0 preserves current behavior; ~8 for realistic
  taker_fee_bps: 10      # 0.1% (current). Realistic USDT-M taker ~0.05% (5)
  maker_fee_bps: 10      # 0.1% (current). Realistic USDT-M maker ~0.02% (2)

trend:
  # ...existing fields...
  perp_mark_source: gateio_usdt_perp   # short MTM/fills use Gate.io perp mark
  funding_accrual: true                # accrue funding on open shorts every 8h
```

Missing/zero values fall back to current behavior so existing configs do not
regress.

## Testing

Unit tests (`trading-engine-core`):

- **Slippage sign & tiering** (`paper.rs`):
  - taker Buy fills above mark, taker Sell below, by `slippage_bps`;
  - maker Limit/LimitMaker fill at the limit price;
  - fee uses `taker_fee_bps` vs `maker_fee_bps` correctly;
  - with `slippage_bps=0` and fees=10bps, output is byte-identical to today
    (regression guard over the existing `paper.rs` tests).
- **Perp mark routing** (`perp_price.rs` + trend): trend short MTM and trigger
  checks use the perp mark; a long on the same symbol uses spot; a fetch failure
  falls back to the last good mark without panicking.
- **Funding**: positive rate accrues negative PnL to a short; negative rate
  accrues positive; accrual stops on close; not applied to longs.
- Integration: a short open→TP1→TP2→TP3→trailing-stop scenario reports lower net
  PnL than today (slippage on the trailing taker fill) and includes a funding
  line if held across an interval.

## Risks / Heads-Up

- **Reported P&L across all engines will drop** once slippage is enabled, because
  taker exits (stop-loss, trailing-stop, market entries) across grid/swing/MR/trend
  all become realistically worse. This is intended (honesty) but is a visible
  step-change in the numbers when the change ships — call it out before deploy.
- **Gate.io perp REST dependency:** adds a network call path (cached at bar
  cadence). Rate-limit by caching; on failure, fall back to last mark + log. Do
  not let a perp-feed outage halt trading — degrade to spot mark with a warning.
- **Existing paper baselines shift**, so period-over-period comparisons before/after
  deploy are not like-for-like. Capture an all-time P&L snapshot before deploying
  so the "pre-realism" vs "post-realism" split is documented.
- Paper short accounting remains naked-negative-balance at 1×; this is sound as a
  1× sim but **not live-realizable on Binance spot**. Live realization requires a
  futures venue — that is the September decision, out of scope here.

## Future Work (explicitly deferred)

- Size-dependent slippage / market impact (walk the order book via `get_order_book`).
- Leverage + liquidation + isolated-margin modeling (gates on a decision to trade
  leveraged trend).
- Full Rust `PaperFuturesConnector` (only needed if leverage is added).
- Migration of the perp path to live Binance USDT-M futures at 1× (September gate).

## Open Questions — Resolved

- **Real money now vs realistic paper first?** → Realistic paper first; stay paper
  through September. (User decision 2026-07-01.)
- **Flat-bps vs size-dependent slippage for v1?** → Flat-bps, configurable. Size
  impact deferred to Future Work.
