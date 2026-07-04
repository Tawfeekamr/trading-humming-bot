# Unified Rust Replay Backtest + OOS-Gated Auto-Apply

**Date:** 2026-07-04
**Status:** Design — pending implementation plan
**Supersedes:** the simplified `backtest/vectorbt_sweep.py` + `.github/workflows/sweep.yml` auto-apply loop (grid+trend only).

---

## 1. Why

Two problems this design solves:

1. **The existing trend+grid backtest is unfaithful and lies.** `backtest/vectorbt_sweep.py` is a simplified vectorbt signal model (EMA-cross + RSI gate, long-only, no shorts / 3-TP / trailing / regime gate / inventory accounting). It does not match the deployed Rust engines. The 2026-06-01 verdict it produced ("only BNB trend has edge, rest is noise") was about a strategy that **isn't what's running live**. Decisions made from it are unsafe.

2. **The existing auto-apply has no out-of-sample gate.** `.github/workflows/sweep.yml` runs weekly, picks the param with the highest **in-sample** delta-Sharpe via `backtest/apply_sweep.py`, writes it to `config/strategy.yaml`, and pushes → triggers a live redeploy. No OOS check, no "must beat current config" check, no min-trade-count. That is the classic overfit-to-production failure mode.

The August 2026 paper→live go-live decision needs trustworthy evidence. This design produces it by replaying the **actual Rust engine code** over history, splitting in-sample/out-of-sample, and applying a strict gate before anything touches live config.

**What this design explicitly does NOT do:** fix the mean-reversion (MR) strategy. MR is disabled (2026-07-02, PRs #51/#52) and validated negative-EV (−$170 / 41 trades; avg loss 2.8× avg win). Two prior fix attempts (PR #44 param tuning, ML entry-gate pilot) both confirmed no-edge. A backtest cannot manufacture edge where the strategy thesis doesn't hold; tuning a no-edge strategy is overfitting. MR revival is a separate future project (see §11).

---

## 2. Goals & Non-Goals

**Goals**
- Backtest **all 4 Rust engines** (grid, trend, swing, MR) faithfully — same code path as live, zero drift.
- Produce honest in-sample / out-of-sample edge evidence per engine per pair.
- Run **automatically** on a schedule, with results pushed to Telegram.
- **Auto-apply** param improvements to `config/strategy.yaml` **only when they survive a strict OOS gate** — and only for engines where that is safe.
- Retire the simplified `sweep.yml` loop so there is one faithful source of truth.

**Non-Goals**
- Fix or revive MR (separate project).
- Backtest the Signal Copy engine (different paradigm — Telegram/DeepSeek event-driven, not bar-replay; separate project).
- Tick-level / sub-1h fidelity (1h bars suffice; tick-replay is a future fidelity tier, §11).
- Replace the live trading loop or change the `Strategy` trait.

---

## 3. Approach — Unified Rust Replay (Approach A)

All 4 Rust engines implement the same `Strategy` trait:

```rust
async fn on_tick(&mut self, ctx: &TickContext) -> Result<Vec<OrderRequest>>;
async fn on_fill(&mut self, fill: &Fill) -> Result<Vec<OrderRequest>>;
// + on_start, on_stop, status, realized_pnl, current_capital, deployed_capital
```

`TickContext` already carries a `replay: bool` flag (used in production to suppress entries during indicator warmup). We reuse it: warmup bars run with `replay = true` (no entries), then the evaluation window runs with `replay = false` (live-equivalent).

**One new Rust binary** (`backtest_replay`) loads 1h bars, instantiates each engine with its **live config** parsed from `strategy.yaml`, and replays bars through the real `on_tick` → simulate-fills → `on_fill` cycle. Because the harness speaks the `Strategy` trait, **every engine runs through the same loop and any future engine plugs in for free.** Zero reimplementation, zero drift.

This was chosen over a Python reimplementation (Approach B) because the scope is all 4 engines: one Rust harness covers all of them, whereas Approach B would require hand-porting each engine plus parity tests (= the drift risk we are trying to eliminate).

---

## 4. Architecture

```
            ┌─────────────────────────────────────────────────────────┐
            │                   backtest_replay (Rust)                 │
            │                                                         │
            │  bars (1h spot + 1h perp)  ──►  bar loader / cache       │
            │                                     │                   │
            │  strategy.yaml ──► config ──►  engine construction      │
            │   (grid, trend, swing, MR)         │                   │
            │                                     ▼                   │
            │  ┌─────────────── per engine × per pair × IS / OOS ───┐ │
            │  │  warmup (replay=true) → live window (replay=false) │ │
            │  │     loop: on_tick → fill_sim → on_fill → portfolio │ │
            │  │     (sweep configs on IS; validate best on OOS)    │ │
            │  └────────────────────────────────────────────────────┘ │
            │                         │                               │
            │                         ▼                               │
            │            results.json (IS+OOS metrics per config)     │
            └─────────────────────────┬───────────────────────────────┘
                                      │
          ┌───────────────────────────▼────────────────────────────┐
          │  apply_sweep.py (Python, extended)                      │
          │   consume results.json → OOS apply-gate (§6)            │
          │   → write gated changes to strategy.yaml (comment-keep) │
          │   → emit changes manifest                               │
          └───────────────────────────┬────────────────────────────┘
                                      │
          ┌───────────────────────────▼────────────────────────────┐
          │  .github/workflows/backtest-rust.yml                    │
          │   weekly cron + workflow_dispatch                       │
          │   compile → run sweep+validate → gated apply            │
          │   → commit if changed → Telegram verdict → artifact     │
          └──────────────────────────────────────────────────────────┘
```

---

## 5. Components

### 5.1 Bar loader (Rust)
- Download **1h spot klines** per pair from `data.binance.vision` (daily/monthly kline files) or Binance REST; parquet cache per pair/range.
- Download **1h perp klines** for pairs where trend opens shorts (trend marks shorts against the perp and accrues funding). Source: Binance USDT-M perp (or Gate.io perp to match the live `perp_mark_source: gateio_usdt_perp`).
- Data volume is tiny at 1h (≈13k bars/pair × 18 mo) — fits in memory, no aggTrade-scale download.
- Skip missing/corrupt days with a warning (mirror `backtest/mean_reversion/data.py` resilience).

### 5.2 TelegramBot stub (Rust)
- The engines call `notify_entry` / `notify_exit` / `notify_*` on a `TelegramBot`. The harness constructs each engine with a **no-op notifier** that silently drops messages. No network, no test-channel spam.

### 5.3 Historical `PerpPriceSource` (Rust)
- Implement the same `PerpPriceSource` trait the production `GateioPerpSource` implements, but backed by the cached 1h perp bars, indexed by timestamp. Used by `TrendStrategy::with_perp(...)` for short MTM, TP/stop triggers, and 8h funding accrual.

### 5.4 Fill simulator (Rust) — the crux, written ONCE
Interprets each `OrderRequest` returned by `on_tick` and produces zero or more `Fill`s on the appropriate bar. Semantics (must match the live `paper` fill model + `paper.*` config):
- **Market / taker (StopMarket reduce-only exits, Market entries/exits)** → fill at **next bar open ± slippage** (`paper.slippage_bps`).
- **Limit / LIMIT_MAKER (resting entries, Limit TP exits)** → fill on the **next bar whose range crosses the price**; fill at the **resting price** (maker, no slippage).
- **Stop triggers** → trigger when a bar's range crosses the stop; **gap handling**: if the bar opens beyond the stop, fill at the open (worse), matching real gap risk.
- **Fees** → `paper.taker_fee_bps` for taker fills, `paper.maker_fee_bps` for maker fills.
- **reduce_only** → respected (closing fills never flip position direction).
- One resting-order book per engine; cancel via `pending_cancels()`.
- This component gets the heaviest unit-testing (§9).

### 5.5 Portfolio tracker (Rust)
- Per-engine **budget cap** from `capital.budgets` (grid 40k / trend 25k / swing 15k / MR 20k).
- Equity, realized P&L, MTM, per-trade journal, drawdown — per engine, isolated.
- `CapitalManager` is passed into `TickContext.capital` as `Some(...)` so engines size against their budget exactly as in production (when `None`, engines size from their own config uncapped — we want the capped path).

### 5.6 Replay driver (Rust)
- For each (engine, pair, slice): construct engine → feed warmup bars (`replay=true`) → feed evaluation bars (`replay=false`) → record metrics.
- **IS/OOS split:** 2/3 in-sample, 1/3 out-of-sample. Indicators warmed independently per slice (conservative isolation, matching MR's `BUG-8` design choice).
- For the **validation run**: the engine's *current live config* is run on full / IS / OOS — this is the baseline the sweep must beat.
- For the **sweep run**: per-engine param grid (§7) swept on IS; best-by-IS-Sharpe config validated on OOS; apply-gate (§6) decides.

### 5.7 Metrics & reporting (Rust → JSON + report.md)
Per (engine, pair, config): total return %, Sharpe, max drawdown %, win rate, profit factor, total trades, HODL benchmark, and the **IS→OOS Sharpe-gap** overfit flag. Emit:
- `results.json` — structured (consumed by the apply step + future tooling).
- `report.md` — human-readable per-pair/per-engine summary.

### 5.8 Param sweep grids (Rust, per engine)
Each engine declares its tunable params + value ranges. **Conservative by design** — few params, wide steps (de-overfit), bounded config count (the MR backtest lesson: call-/config-count is the bottleneck, not bar count). Initial grids (to be finalized in planning):
- **Trend:** `ema_fast`, `ema_slow`, `rr_ratio`, `trailing_stop_atr_mult`, `entry_score_threshold` (long+short path exercised).
- **Grid:** `atr.spacing_multiplier`, `adx_range_max`, `chop_range_min`, `natri` band.
- **Swing:** `band_atr_mult`, `atr_stop_mult`, `min_score`, `adx_range_entry`.
- **MR:** not swept (report-only).

### 5.9 Extended apply step (Python — extend `backtest/apply_sweep.py`)
- Consume `results.json` (now carrying **IS + OOS metrics + trade counts** per config, not just delta-Sharpe).
- Run the **OOS apply-gate** (§6) per engine.
- Write **only gated** changes to `config/strategy.yaml`, **comment-preserving** (extend the existing line-based editor; broaden `PARAM_MAP` to the full swept set; fall back to `ruamel.yaml` if comment preservation proves brittle).
- Emit a **changes manifest** (what changed, from → to, with the OOS evidence) for the Telegram step + commit message.

---

## 6. The OOS Apply Gate (the safety fix)

A candidate param set is written to live config for a given engine **only if ALL hold**:

1. **Beat current:** candidate's OOS Sharpe > current live config's OOS Sharpe **+ 0.3** (margin).
2. **Positive OOS:** candidate's OOS Sharpe **> 0**.
3. **Statistical sanity:** OOS trade count **≥ 15** (reject 1-lucky-trade winners).
4. **Risk-bounded:** OOS max drawdown within a sane bound (per-engine, e.g. ≤ the live config's DD + small tolerance).
5. **Param sanity:** all swept values within predeclared safe ranges (clamp; reject pathological winners).

If any check fails → **keep current config**, decision = `KEEP`, recorded with the reason. This is the difference between "auto-apply" and "auto-apply only what survives out-of-sample."

The gate is evaluated **per engine independently**, so a good trend config applies even if grid's gate fails (or vice-versa).

---

## 7. Per-Engine Policy

| Engine | Backtest (validate) | Sweep params | Auto-apply |
|---|---|---|---|
| **Trend** | ✅ (long+short, perp, funding) | ✅ | ✅ if gate passes |
| **Grid** | ✅ (regime gate, inventory) | ✅ | ✅ if gate passes |
| **Swing** | ✅ (ETH-only per config) | ✅ | ✅ if gate passes (marginal → likely KEEP) |
| **MR** | ✅ (confirms no-edge) | ❌ report-only | ❌ **never** (disabled + −EV) |

MR is backtested (to re-confirm the disable with real-engine evidence) but **excluded from sweep and auto-apply**. Auto-tuning a losing strategy makes it lose faster; the gate would (correctly) block it anyway, but we exclude it explicitly to remove any path by which an overfit MR config reaches live.

---

## 8. Automation

**Workflow:** `.github/workflows/backtest-rust.yml`
- **Triggers:** weekly cron (offset from the existing Sunday sweep so runs don't collide) **+** `workflow_dispatch` (manual override, with inputs: engines, pairs, months, IS/OOS split, `--dry-run`).
- **Steps:**
  1. Checkout + setup Rust (cache `target/` + cargo registry).
  2. Compile `backtest_replay` (+ extended `apply_sweep.py` deps).
  3. Run sweep+validate → `results.json` + `report.md`.
  4. Run `apply_sweep.py` (gated) against a checked-out `config/strategy.yaml`.
  5. **Commit only if something changed** (one commit, per-engine lines in the message).
  6. **Telegram:** per-engine verdict — `APPLY` (with from→to + OOS evidence) or `KEEP` (with reason), + artifact link.
  7. Upload `report.md` + `results.json` + changes manifest as artifact.
- **Dry-run mode:** run + report + propose changes to Telegram, but **do not commit**. Used for the first runs and for verification.
- **Retires `sweep.yml`** — the simplified auto-apply is deleted once the faithful pipeline is green.

**Run location:**
- **Develop + debug locally** (1h bars are tiny — this is *not* the MR 5s tick-replay that swap-thrashed the Mac).
- **Official scheduled run on GitHub Actions runner** (isolated, no prod impact, artifact).
- **Never on the EC2 prod box** — it runs the live bot; no backtest load there.

---

## 9. Testing Strategy

- **Fill simulator unit tests (highest priority):** maker fill on range cross, taker fill at open ± slippage, stop trigger with and without gap, reduce_only never flips direction, fee tiers applied correctly. This component is the single biggest source of fidelity risk.
- **Smoke run per engine:** tiny synthetic bar sequence → assert entries/exits/P&L are sane (not the live engine's exact trades, but directional correctness).
- **Known-trade cross-check (if available):** replay a recent window where live trades are journaled in `data/trades.db` (`is_backfilled=0` rows per the PnL source-of-truth memory) and compare. Exact match is not expected (paper vs simulated fills), but order-of-magnitude and side agreement is a strong signal.
- **Engine constructor wiring tests:** each engine builds from `strategy.yaml` with the stub notifier / perp source / tick+step sizes without panicking.
- **Apply-gate unit tests:** feed synthetic `results.json` cases (beats current / fails each gate check / pathological winner) → assert correct apply/keep decision + correct YAML edit.
- **Existing MR backtest tests still pass** (we are not touching that package).

---

## 10. Fidelity Gaps (stamped on every report)

- **ML regime gate:** the live grid blocks on an ML Trending/Danger model + confidence. The harness passes `regime = None`, `regime_confidence = 0.0` → grid falls through to its TA gates (`evaluate_state_with_ml` already handles `None`). **Optimistic** (no ML block). Flagged in the report; future work could feed a historical ML regime series.
- **Funding rate:** trend accrues perp funding every 8h. Requires a historical funding-rate source; if unavailable, a documented flat assumption is used and flagged.
- **Order book:** synthesized from bar OHLC. Strategies predominantly use bar prices, so impact is minor.
- **Sub-bar timing:** 1h bars cannot resolve the grid's 60s per-level cooldown or intrabar order sequencing. Rarely binds at 1h; flagged.
- **Paper-restart artifacts:** production restarts wipe grid inventory / reset realized P&L display (known issues). The backtest is a single continuous run, so it does not reproduce those production-only artifacts. This makes the backtest *more* representative of true strategy economics, not less.

---

## 11. Future / Out of Scope

- **MR revival** — a new mean-reversion thesis (or new instruments) as a separate project; this harness becomes the validation tool for it.
- **Signal Copy engine backtest** — replay of logged Telegram/DeepSeek signal history, if a log exists; separate paradigm.
- **Tick / sub-1h fidelity tier** — for slippage/queue realism; would move official runs off local (the MR RAM constraint returns).
- **Historical ML regime series** — to close the grid ML-gate fidelity gap.

---

## 12. Scope Summary

- **Engines:** grid, trend, swing, MR (all 4 replayed; grid/trend/swing sweep+gated-apply; MR report-only).
- **Pairs:** DOGE, ETH, BNB, XRP (BTC disabled; swing ETH-only per config).
- **Window:** Jan 2025 → today (~18 mo), **2/3 IS / 1/3 OOS**.
- **Run:** develop locally; official weekly run on Actions; never on EC2 prod.
- **Apply:** OOS-gated, per-engine, comment-preserving, one revertible commit; retires `sweep.yml`.
- **Not included:** MR fix, Signal engine, tick fidelity.

---

## 13. Open Questions for Planning

1. Final per-engine sweep param grids + value ranges (§5.8) — start conservative, widen only if compute allows.
2. Historical funding-rate source for trend shorts (Binance USDT-M funding-rate history is downloadable; confirm coverage for the window).
3. Perp kline source: Binance USDT-M perp vs Gate.io perp (match live `gateio_usdt_perp`). Decide which for fidelity vs data availability.
4. Weekly cron day/time (avoid colliding with the existing Sunday sweep until `sweep.yml` is retired).
5. Whether the first production auto-apply runs should be `--dry-run` for N weeks before flipping to live apply.
