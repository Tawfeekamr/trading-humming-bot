# 23 — Scout Bot: Design & Implementation Plan

**Status:** Phase 0 done (rules fixed in `docs/scout/PREREGISTRATION.md` — that file wins where it differs from this one) · Phase 1 in progress · **Date:** 2026-11-28
**One line:** a bot that scans many coins, reads the overall market, writes its own
trade signals, and sends them through the trade executor we already have.

---

## 1. What the Scout Bot is (and is not)

Today the bot trades in two ways:

| Today | Where ideas come from | Weakness |
|---|---|---|
| Grid / Trend / Swing (Rust) | Fixed rules on 4 fixed pairs | Only sees ETH/BNB/DOGE/XRP; sits idle when those 4 are quiet |
| Signal Copy (Python) | Other people's Telegram signals | We don't control quality, timing or how often signals come |

The Scout Bot fills the gap: **it makes its own signals by looking at the whole market**,
then hands them to the same signal executor that already trades Telegram signals.

It is **not** a new execution engine. Order placement, the take-profit ladder, stop
management, journals, Telegram alerts and paper futures already exist and are proven
(the signal path is the main source of realised profit: spot signal +$1,377, futures +$253).
Building a second executor would bring back the dual-manager race fixed in 4efac99.

### Lessons from this repo that shape the design

| Past lesson | Design rule |
|---|---|
| Most strategies here showed **no edge** once tested properly (MR, swing, PPO, trend-ETH) | **Research gate first.** No live paper trading until the walk-forward test passes pre-set criteria (§7) |
| Paper P&L was inflated by free, instant fills | Backtest with 5 bps taker fee + 8 bps slippage on **both** sides |
| Spot shorts were fiction (naked −68 ETH) | Shorts only on the **paper futures** venue |
| Bad ticks caused phantom losses | Check price against a second source before every entry |
| Config keys that nothing read (3 times) | Strict config: unknown or unused keys fail at startup |
| Backtest code ≠ live code | Setup detectors are **pure functions of bars**, used unchanged by backtest and live |
| Silence was "designed abstention" | Scout logs **every candidate it rejects, and why**, so silence can be explained |

---

## 2. Architecture

```
                     ┌──────────────────────── scout-bot container (Python) ───────────────────────┐
 Gate.io / Binance   │                                                                              │
 REST klines,  ───►  │ 1 Universe  ─► 2 Market State ─► 3 Features ─► 4 Setup     ─► 5 Ranker ─►   │
 tickers, funding    │   (hourly)      (risk-on/off)     (per coin)    detectors      & portfolio  │
                     │                      ▲                          (pure fns)     filter       │
 Rust engine  ───────┼──► ML regime cache ──┘                                            │         │
 /api/v1/regime      │                                                                   ▼         │
                     │                                                    6 Emitter: ScoutSignal  │
                     │                                                       → ParsedSignal       │
                     │                                                                   │         │
                     │   ┌───────────────── existing SignalEngine (own instance) ────────▼──────┐  │
                     │   │ validator → risk guard → price check → entry → TP ladder / SL / trail│  │
                     │   └───────────────┬───────────────────────────────┬───────────────────────┘  │
                     └───────────────────┼───────────────────────────────┼──────────────────────────┘
                                         ▼                               ▼
                         paper futures (Gate.io perp prices)   scout_journal.db + trades.db
                                                               (strategy='scout') + Telegram 🔭
```

It runs as **its own container** with **its own `SignalEngine` instance** (like the futures
signal container today). That gives it its own budget, position limit, journal and kill
switch, and clean P&L attribution. It never touches the Telegram signal positions.

### Timing

- Runs on each **closed 1h bar** (+15 s delay so the exchange has published the bar).
- Universe refresh: hourly. Market state: each run. 15m bars are fetched **only** for
  coins that already passed a 1h/4h setup (to time the entry), which keeps API calls low.
- Position management stays on the SignalEngine's existing tick loop.

---

## 3. The six layers

### 3.1 Universe — "which coins are worth looking at?"

Source: Gate.io USDT perpetuals (the paper futures venue), cross-listed on Binance.

Filters (all configurable):

| Filter | Default | Why |
|---|---|---|
| 24h quote volume | ≥ $20M | Enough liquidity that our size doesn't move price |
| Bid-ask spread | ≤ 10 bps | Spread is a hidden fee |
| Listing age | ≥ 30 days | New listings have no history and wild moves |
| Also on Binance | required | Second price source for the sanity check |
| Exclude | stablecoins, leveraged tokens, `blacklisted_pairs` | Not real trends |
| Cap | top 60 by volume | Bounds API calls and multiple-testing risk |

Output: `data/scout_universe.json` (timestamped) — saved so the backtest can rebuild
**the universe as it was** on each past date (avoids survivorship bias).

### 3.2 Market State — "is now a good time to trade at all, and which way?"

Top-down view, computed once per run:

| Input | Source |
|---|---|
| BTC regime + confidence | Existing ML regime now-cast (`/api/v1/regime`) — reuse, don't retrain |
| Breadth | % of universe with close > EMA50 (4h) |
| BTC trend | BTC close vs EMA200 (1d), ADX(4h) |
| Market volatility | Median NATR(1h) across universe |
| Crowding | Median funding rate across universe |

Output: one of `RISK_ON`, `NEUTRAL`, `RISK_OFF`, `NO_TRADE`, plus a gross-exposure
multiplier (1.0 / 0.5 / 0.5 / 0.0).

- `RISK_ON` → longs only.  `RISK_OFF` → shorts only (futures).  `NEUTRAL` → both, half size.
- `NO_TRADE` when market volatility is very low (fees eat the move) or the regime model
  is in "danger". This is **intended** silence and is logged with the reason.

### 3.3 Features — "what is each coin doing?"

Per coin, on 1h and 4h bars:

- **Relative strength** vs BTC over 24h and 7d, as a **cross-sectional rank** (0–1) within the universe
- Trend: EMA20/50/200 stack, ADX(14) and its slope
- Breakout: distance to Donchian(20) high/low, in ATRs
- Volume: RVOL = volume ÷ 20-bar average
- Volatility: ATR(14), NATR
- Futures crowding: funding rate, 24h change in open interest
- Structure: nearest support/resistance (reuse logic of `indicators/support_resistance.rs`)

The Python feature code must match the Rust indicators. A **parity test** checks both
give the same values on a fixed bar file (same lesson as the regime feature contract,
`src/data/feature_contract.py`).

### 3.4 Setup detectors — "is there a trade here?"

Each detector is a **pure function**: `detect(bars_1h, bars_4h, features, market_state) -> Optional[Setup]`.
No I/O, no clocks, no globals — so the backtest runs the exact same code.

Start with **two** well-known setups. More detectors = more chances to fool ourselves.

**A. Momentum breakout (trend continuation)**
- Coin in top 20% of relative strength (long) or bottom 20% (short)
- 4h close breaks the Donchian(20) high (low for shorts)
- RVOL ≥ 2.0 on the breakout bar, ADX(4h) ≥ 20 and rising
- Entry zone: breakout level to +0.5 ATR (don't chase further — reuses `entry_chase_reason`)
- Stop: 1.5 × ATR(4h) below the breakout level
- Targets: 1R / 2R / 3R

**B. Pullback in trend**
- Coin in top 30% relative strength, EMA20 > EMA50 > EMA200 on 4h
- Price pulls back to the EMA20(4h) zone, RSI(1h) dips to 40–50 then closes back up
- Funding not extreme (|funding| < 0.05% / 8h — avoids crowded trades)
- Stop: below the pullback swing low, at least 1 ATR(1h)
- Targets: prior high, then 2R / 3R

A `Setup` holds: pair, side, entry zone, stop, targets, detector name, the feature
snapshot, and a 0–100 rule score.

**Later (Phase 4, only if A/B pass):** funding-squeeze reversal; LLM news/sentiment as a
*filter* (never as the source of trades — it can't be backtested reliably).

### 3.5 Ranker & portfolio filter — "which of these do we actually take?"

1. **Score.** Phase 1: the rule score. Phase 3: a **meta-label model** — a classifier
   trained on past setups to predict "did this hit target before stop?" (triple-barrier
   labels). It only decides *whether to take* a setup, not the direction. This is the
   right place for ML here.
2. **Correlation cap.** Group coins by 30-day return correlation; max 1 open position per
   group, and max 2 with BTC-beta > 1.2. Five altcoin longs are one bet, not five.
3. **Capacity.** Fill free slots (max 3 positions) with the highest scores. No re-entry on the
   same coin for 12h after a stop-out.

### 3.6 Emitter — "hand it to the executor"

`ScoutSignal` → `ParsedSignal` adapter:

```python
ParsedSignal(
    action=SignalAction.LONG / SHORT,
    pair="SOL-USDT",
    entry_low=..., entry_high=...,
    stop_loss=..., take_profits=[tp1, tp2, tp3],
    quality_score=round(score / 10),     # maps onto min_quality_score gate
    raw_message=json.dumps(setup_snapshot),
    parse_reasoning=f"scout:{detector}",
)
```

New method on `SignalEngine`: **`submit_signal(signal: ParsedSignal, source: str)`**.
It enters `_process_message` *after* the DeepSeek parse step (today `inject_signal`
re-parses text through DeepSeek, which costs money and can change our numbers).
Validator, risk guard, entry-chase check, sizing, TP ladder, journals and Telegram are all
reused unchanged. Small refactor: split `_process_message` into `parse` + `_act_on_signal`.

---

## 4. Risk & sizing

| Control | Default | Enforced by |
|---|---|---|
| Scout budget | $10,000 paper (`capital.budgets.scout`) | own SignalEngine `max_capital_usdt` |
| Risk per trade | 0.75% of budget (stop distance sets size) | existing `SignalRiskGuard` |
| Max open positions | 3 | existing guard |
| Max per correlation group | 1 | scout ranker (new) |
| Leverage | 3x isolated (futures paper) | existing futures connector |
| Daily loss limit | 3% of budget → halt until UTC midnight | existing guard |
| Price sanity | Gate.io price within 1% of Binance before entry, else skip | new check in emitter |
| Max hold | 72h, then exit at market | new rule in position manager (config flag) |
| **Kill switch** | After ≥ 30 trades, rolling 30-trade expectancy < 0 **or** drawdown > 12% → auto-pause + Telegram | new `scout/guard.py` |
| Global breaker | Respects the Rust circuit breaker halt state | read `risk_state.json` |

---

## 5. Data, storage, monitoring

- **`data/scout_journal.db`** — every run: market state; every candidate (taken **or
  rejected**, with reason and full feature snapshot). This is also the training set for the
  Phase 3 meta-label model.
- **`trades.db`** — closed trades with `strategy='scout'`, written once (the
  `is_backfilled=0` path), to avoid the old double-count bug.
- **Telegram** — `🔭 SCOUT` prefix on entries/exits; daily digest (market state,
  candidates seen, taken, top reject reasons). Commands: `/scout`, `/scout_pause`,
  `/scout_resume`, `/scout_candidates`.
- **Frontend** — a Scout tab: market state, live candidates, open positions, equity curve.
- **Health** — container healthcheck = "last successful run < 70 min ago".

---

## 6. Code layout

```
src/scout/
  __init__.py
  config.py          # pydantic model for `scout:` block, extra="forbid" (strict)
  data.py            # klines/tickers/funding fetch + on-disk cache (Gate.io, Binance)
  universe.py        # 3.1
  market_state.py    # 3.2
  features.py        # 3.3 (pure)
  setups/
    base.py          # Setup dataclass, Detector protocol
    breakout.py      # 3.4 A (pure)
    pullback.py      # 3.4 B (pure)
  ranker.py          # 3.5 (score, correlation groups, capacity)
  emitter.py         # 3.6 + price sanity
  guard.py           # kill switch
  journal.py         # scout_journal.db
  runner.py          # bar-close scheduler, wires everything, owns the SignalEngine instance
backtest/scout/
  replay.py          # walks history bar by bar, calls the SAME universe/features/setups/ranker
  fills.py           # fee + slippage + intrabar stop/target order (stop first if both hit)
  baselines.py       # random-entry null, BTC hold, equal-weight universe
  report.py          # walk-forward report (markdown + json)
tests/scout/
  test_features_parity.py   # Python vs Rust indicators on fixed bars
  test_setups.py            # hand-built bar fixtures → expected setup / no setup
  test_ranker.py            # correlation cap, capacity, cooldown
  test_emitter.py           # ParsedSignal mapping, price-sanity reject
  test_no_lookahead.py      # features at bar t unchanged when bars > t are altered
  test_config_strict.py     # unknown key → startup error
```

Changes to existing code (small):
- `src/signals/signal_engine.py`: split `_process_message`; add `submit_signal`; optional `max_hold_hours`.
- `config/strategy.yaml`: new `scout:` block + `capital.budgets.scout`.
- `docker-compose.rust.yml`: `scout-bot` service (uses `entrypoint:`, not `command:` — regime-pusher lesson).
- `src/notifications/telegram_commands.py`: `/scout*` commands.

---

## 7. Research gate (the most important part)

**Write these criteria down and commit them before running the test.** If the result
fails, the answer is "no edge" — we do not retune until it passes.

**Data:** 1h bars, Binance + Gate.io, 2023-01 → 2026-10, universe rebuilt per date
(include coins later delisted). Fees 5 bps + slippage 8 bps per side. If a bar hits both
stop and target, assume the **stop** filled first.

**Method:** walk-forward, 6-month train / 3-month test, rolling. Thresholds are fixed in
the train window only. Only one config is tested per detector (plus at most a 3×3 grid,
all results reported).

**Baselines** (the random-entry null is pass/fail; the holds are reported for information only — see pre-registration §11):
1. **Random-entry null** — same coins, same times-of-day, same stop/target/hold rules,
   random direction-consistent entries. 1,000 runs. *This is the real test:* it shows
   whether the setup adds anything beyond the exit rules.
2. BTC buy-and-hold, and equal-weight universe hold (same exposure time).

**GO criteria (all required, on out-of-sample test windows only):**

| Metric | Threshold |
|---|---|
| Trades | ≥ 150 |
| Expectancy after costs | > 0.10R per trade |
| Profit factor | ≥ 1.25 |
| vs random-entry null | better than 95% of the null runs |
| Folds positive | ≥ 60% |
| Max drawdown | ≤ 15% of budget |
| Not one-coin luck | Still positive with the best coin removed |

Each detector is judged **separately**. A detector that fails is switched off; the scout
can ship with just one.

---

## 8. Rollout phases

| Phase | What | Exit condition | Est. effort |
|---|---|---|---|
| **0. Pre-register** | Commit §7 criteria + data plan | Merged | 0.5 day |
| **1. Research** | `data`, `universe`, `features`, `setups`, `ranker`, `backtest/scout`, tests | Walk-forward report; GO/NO-GO per detector | 5–7 days |
| **2. Shadow** | `runner`, `journal`, `emitter` in **log-only** mode on the server; Telegram digest | 3 weeks; live candidates match what replay produces on the same bars (fidelity ≥ 95%) | 3 days + 3 weeks wait |
| **3. Paper** | `submit_signal`, own SignalEngine on paper futures, kill switch, `/scout` commands, frontend tab | 6 weeks / ≥ 40 trades; live expectancy within the backtest's 90% range | 3–4 days + 6 weeks |
| **4. Improve** | Meta-label ranker (trained on journal + backtest setups); extra detectors — each must pass §7 on its own | Ranker improves OOS expectancy vs rule score | 4–5 days |
| **5. Real money** | Only after Phase 3 passes; 10% of paper size; separate decision | Explicit user approval | — |

Stop at any phase that fails. "No edge found" is a valid, useful result.

### Phase 1 task list (first PR series)

1. `scout/config.py` + strict-config test
2. `scout/data.py` — klines cache (parquet under `backtest/data_cache/scout/`), Gate.io + Binance, unit tests with recorded responses
3. `scout/universe.py` + historical universe rebuild (volume ranks per day)
4. `scout/features.py` + parity test vs Rust + no-lookahead test
5. `scout/market_state.py` (historical regime: run the regime model offline on past bars)
6. `scout/setups/breakout.py`, `pullback.py` + fixture tests
7. `scout/ranker.py` + tests
8. `backtest/scout/fills.py`, `replay.py`, `baselines.py`, `report.py`
9. Run walk-forward on GitHub Actions (local RAM is too small — see backtest perf note); publish report to `reports/scout_walk_forward.md`

---

## 9. Main risks

| Risk | Mitigation |
|---|---|
| No edge (most likely outcome, based on this repo's history) | Pre-registered gate; cheap to stop after Phase 1 |
| Overfitting via many detectors/params | Two detectors, tiny grid, all runs reported, random-entry null |
| Survivorship bias | Historical universe with delisted coins |
| Altcoin crashes are correlated | Correlation cap, market-state gate, 3 positions max |
| Gate.io paper fills ≠ real fills | Fee + slippage model; real money only at small size after paper |
| Thin liquidity coins | Volume + spread filters; size ≤ 0.5% of 1h volume |
| API rate limits | Fetch 15m only for shortlisted coins; local cache |
| Time vs dissertation (due 12 Jan 2027) | Phase 1 is self-contained; can pause after it without leaving half-wired live code |

---

## 10. Open decisions

1. Venue: paper futures only (long + short), or also spot longs? **Recommended: futures only** — one venue, real shorts.
2. Budget: new $10k slice, or taken from an idle engine (e.g. mean-reversion's $20k, which is disabled)? **Recommended: reuse MR's slice.**
3. Start now, or after the dissertation is submitted? **Recommended: Phase 0–1 only before submission.**
