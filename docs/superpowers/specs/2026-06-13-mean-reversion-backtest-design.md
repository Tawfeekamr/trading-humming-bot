# Mean-Reversion Tick-Replay Backtest — Design

**Date:** 2026-06-13
**Status:** Approved (pending implementation plan)
**Owner:** tawfeekamr

## Context

The Rust mean-reversion strategy (`trading-engine-core/src/strategy/mean_reversion.rs`) was recently fixed: a latent "Layer-1 protective backstop" (a `LIMIT SELL` ~7% below entry that would have filled instantly and booked ~-7% per trade) was removed. The strategy now relies cleanly on its Layer-2 `on_tick` logic: enter on a sharp intraday flush, exit at +2% take-profit or -4% stop.

Before committing capital, we need to know whether this edge is real. The existing backtest harness (`backtest/`, Python + vectorbt, 1h klines) cannot evaluate the strategy as written: the entry is a sub-minute microstructure signal (">5% drop in 30s" plus `bid_refill_ratio` from live orderbook depth), invisible on 1h candles, and Binance offers no historical L2 depth.

This spec defines a **faithful tick-replay backtest** that reconstructs the strategy on real second-resolution trade history.

## Goal

Decide whether "buy sharp dumps, exit +TP/-stop" has a profitable edge, and find the best configuration, using 6 months of real tick history across BNBUSDT, DOGEUSDT, ETHUSDT, XRPUSDT. The result must be **provably faithful** to the deployed logic.

## Non-Goals

- Backtesting the live Rust code path literally (the async `Strategy` trait is impractical to drive over tens of millions of events; see Rejected Alternatives).
- High-frequency / market-making analysis. This is a directional dump-buyer.
- Production deployment tooling, live sizing, or config auto-application (existing `apply_sweep.py` already does the last; out of scope here).

## Approach (chosen)

Faithful **Python port** of the strategy logic, using **vectorbt** as the sweep engine (path-dependent SL/TP exits and parameter broadcast — the same machinery the grid/trend harness uses), with the port **cross-validated against the Rust strategy's unit tests as the correctness oracle**.

## Components

### 1. Data pipeline — `backtest/mean_reversion/data.py`
- **Source:** daily aggTrade ZIP files from `data.binance.vision`
  (`https://data.binance.vision/data/spot/monthly/aggTrades/{SYMBOL}/{YYYY}-{MM}/{SYMBOL}-aggTrades-{YYYY}-{MM}-{DD}.zip`).
  ~720 files for 4 pairs × 6 months.
- **Raw cache:** `backtest/data_cache/aggtrades/{SYMBOL}/{YYYY-MM-DD}.parquet`. Idempotent download (skip if present). Schema: `ts_ms, price, qty, is_buyer_maker`.
- **Resample to 1-second bars:** per second, OHLC from trades plus `buy_vol = Σqty where not is_buyer_maker` and `sell_vol = Σqty where is_buyer_maker`. Cache as `backtest/data_cache/1s/{SYMBOL}.parquet`. This bar stream is the replay input.
- **Smoke test:** for any resampled day, `buy_vol + sell_vol == total_vol`, and bar count == seconds between first/last trade.

### 2. Trade-flow features — `backtest/mean_reversion/features.py`
Over a trailing 30s window (30 one-second bars), computed as vectorized pandas rolling arrays:
- `drop_frac = (close_30s_ago - close_now) / close_30s_ago` — faithful to the live `oldest.price` calculation.
- `bid_refill_ratio = smoothed_buy_vol_now / (smoothed_buy_vol_30s_ago + ε)` — real buy-pressure restoration proxy for the live `bid_depth / oldest_bid_depth`. Both numerator and denominator use per-second `buy_vol` smoothed over a 5s window to tame single-second noise; a ratio >1 means buy-side pressure has recovered since 30s ago.
- `sell_flow_decay = sell_vol_recent_10s / sell_vol_window` — dump exhaustion.
- `liq_cascade_score = peak_per_second_sell_vol / avg_per_second_sell_vol` — liquidation-cascade spike.
- `cross_market_corr = 0` — no per-second cross-market data; live uses a 0.2 constant, so impact is minor.

These replace the four constants the live strategy currently hardcodes into its classifier, making the backtest *more* rigorous than the live code on those dimensions.

### 3. Strategy port — `backtest/mean_reversion/strategy.py`
Pure-Python reimplementation of `MeanReversionStrategy` decision logic, structured to mirror the Rust `on_tick`:
- **Classifier score:** `w_retrace·drop_frac + w_refill·bid_refill_ratio + w_exhaust·sell_flow_decay + w_liq·liq_cascade_score - w_corr·cross_market_corr`, using live `ClassifierCfg` defaults (`w_retrace=1, w_refill=1, w_exhaust=1, w_liq=0.5, w_corr=1.5, enter_threshold=2.0, full_size_margin=1.5`). `size_mult = clamp((score - enter_threshold)/full_size_margin, 0, 1)`.
- **Entry (flat):** `drop_frac > drop_thr` AND `score >= enter_threshold`. vectorbt's `Portfolio.from_signals` enforces one position at a time, so no separate in-position mask is required.
- **Sizing:** `qty = base_size_usdt * size_mult / close`. `base_size_usdt` is a sweep axis (see §4). Note: because the strategy holds one position at a time, percentage returns are size-invariant — sizing scales dollar P&L and dollar drawdown (directly relevant to the "how much capital" question) but does not change the edge or add overfitting risk.
- **Exits (in position, per bar):** `close >= entry·(1+tp)` → take-profit; `close <= entry·(1-stop)` → stop-loss. Fill at bar close. Fees 0.1%/side (matches paper `FEE_RATE`); slippage 5bps via `reporting.add_slippage`.
- **No protective backstop** (consistent with the fix). **No re-entry cooldown by default** (faithful to current behavior); a cooldown axis may be added in a follow-up if the first pass shows churn.

### 4. Sweep engine — `backtest/mean_reversion/backtest.py`
- **Grid:** `drop_thr ∈ {0.03, 0.04, 0.05, 0.06, 0.08}` × `tp ∈ {0.01, 0.015, 0.02, 0.03, 0.04}` × `stop ∈ {0.02, 0.03, 0.04, 0.05, 0.06}` × `base_size_usdt ∈ {50, 100, 200, 500}` = **500 configs/pair × 4 pairs = 2000**. Includes positive risk-reward variants, to test the current -4%/+2% (negative-RR) configuration directly against better-structured alternatives. Sizing scales dollar metrics only (does not affect entry/exit or percentage edge).
- vectorbt broadcasts the grid in one vectorized pass per pair (`from_signals(..., sl_stop=..., tp_stop=...)`).
- **Entry point:** `python -m backtest.mean_reversion.backtest --pairs BNBUSDT,DOGEUSDT,ETHUSDT,XRPUSDT --months 6`.

### 5. Evaluation & anti-overfit
- **Walk-forward IS/OOS split** (reuse `walk_forward.py` pattern): months 1–4 in-sample (sweep), months 5–6 out-of-sample (validate the top configs). Report the IS-vs-OOS gap; flag any config whose OOS Sharpe collapses (overfit signal).
- **Metrics** (reuse `backtest/reporting.py` `BacktestResult`): total return %, Sharpe, max drawdown %, win rate, trade count, profit factor, total fees — each compared to HODL over the same window. Reported per-pair and aggregated.
- **Monte Carlo** (`reporting.monte_carlo_simulation`): trade-order reshuffle for return-confidence intervals.

### 6. Fidelity & testing — the "faithful" guarantee
- **Oracle tests** (`tests/test_mr_port_vs_rust.py`): reconstruct the exact scenarios from the three Rust unit tests (`on_fill_entry_does_not_place_protective_backstop`, `position_holds_then_exits_at_take_profit_via_on_tick`, `position_exits_at_layer2_stop_loss`) as 1s-bar series; feed them through the Python port; assert the entry bar, exit bars, and sides match the Rust behavior. If the port agrees on these, the decision logic is faithful.
- **Feature unit tests** (`tests/test_mr_features.py`): hand-crafted series for `drop_frac`, `bid_refill_ratio`, `sell_flow_decay`, `liq_cascade_score`.
- **Data smoke test** (`tests/test_mr_data.py`): one-day download → resample → assert `buy_vol+sell_vol == total_vol` and correct bar count.

### 7. Output — `backtest/results/mean_reversion/`
- `{SYMBOL}_sweep.json` — full grid + metrics per pair.
- `summary.json` — best IS/OOS config per pair, the deployed-config result, aggregated stats, vs HODL.
- `report.md` — human verdict: does the edge exist? best configuration, risk-reward analysis, overfit flags, explicit go/no-go recommendation for capital.

## File Layout
```
backtest/mean_reversion/
  __init__.py
  data.py          # aggTrade download + 1s resample + cache
  features.py      # trade-flow feature derivation
  strategy.py      # faithful port: classifier + entry + exits
  backtest.py      # vectorbt sweep engine, IS/OOS split, metrics, report
backtest/results/mean_reversion/
  {SYMBOL}_sweep.json
  summary.json
  report.md
tests/
  test_mr_port_vs_rust.py
  test_mr_features.py
  test_mr_data.py
```

## Stated Fidelity Gaps
1. **1s vs sub-second:** brief flash spikes inside a second are invisible. Captures the 30s drop signal and ±2/4% exit crosses well.
2. **`cross_market_corr = 0`:** no per-second cross-market data; live uses a 0.2 constant.
3. **Regime gate dropped:** live uses an ML regime (`regime != Trending`); no historical per-second equivalent. Approximation deferred (could add an EMA trend filter later).
4. **`bid_refill_ratio` is a trade-flow proxy**, not real L2 depth.
5. **Fees/slippage:** flat 0.1%/side + 5bps slippage (matches paper assumptions).

The oracle tests guarantee the **decision logic** matches Rust despite these data approximations.

## Rejected Alternatives
- **Replay through the literal Rust strategy:** maximum fidelity, but the async/tokio `Strategy` trait is built for live ticks; driving ~62M one-second events per config across a 500-config grid is impractically slow, and sweeping would require reconstructing the strategy per config.
- **Pure vectorbt signals, no port:** fastest, but vectorbt cannot express the rolling-window trade-flow classifier features; a port is required regardless.

## Open Questions for Implementation
- Whether to add a re-entry `cooldown_s` sweep axis (default: no, faithful).
- Whether to add a simple EMA-based regime approximation (default: no, drop the gate).
- Whether to extend the feature sweep to classifier weights (default: no — fix at live defaults to limit overfitting).
