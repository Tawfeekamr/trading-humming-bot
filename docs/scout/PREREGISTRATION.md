# Scout Bot — Pre-registration (Phase 0)

**Committed:** 2026-11-28, before any scout backtest was run.
**Design doc:** `docs/23_scout_bot.md`.
**Rule:** everything below is fixed. If the result fails, the answer is "no edge
for this detector". We do not change a rule and re-run until it passes. Any later
change is a **new** pre-registration with a new date, and the old result stays reported.

---

## 1. Question

Do the two scout setups (A: momentum breakout, B: pullback in trend) earn a positive
return **after costs**, and is that return **better than random entries that use the
same exits**?

The second part matters most. Exit rules alone (1R/2R/3R ladder, breakeven stop)
can create a positive-looking curve in a trending market. The setup only adds value if it
beats random entries using the same exits.

## 2. Data

- **Prices:** Binance USDT-M perpetual futures, 1h klines, from `data.binance.vision`
  (monthly archives). This archive **includes delisted symbols**, so the universe can be
  rebuilt without survivorship bias.
- **Daily klines** (1d) for all USDT perpetuals, used to rank volume and find listing age.
- **Period:** 1h data 2022-11-01 → 2026-09-30 (first months = indicator warm-up).
  Out-of-sample test windows run 2023-07-01 → 2026-09-30 (13 quarterly folds).
- 4h bars are built from 1h bars, aligned to 00/04/08/12/16/20 UTC.
- Only **closed** bars are used. A value at bar *t* uses data up to the close of *t* only
  (enforced by a no-lookahead test that edits bars after *t* and checks nothing changes).

**Known gap vs live:** the live venue is Gate.io paper futures, but Gate.io has no
equivalent public archive with delisted coins. Binance futures prices are used as the
proxy. The two venues track each other closely on liquid coins.

## 3. Universe (rebuilt each UTC day, using data up to the previous day)

1. USDT-quoted perpetual (no `_` dated contracts, no index contracts such as `BTCDOMUSDT`, `DEFIUSDT`).
2. Not a stablecoin pair (USDC, BUSD, TUSD, FDUSD, USDP, DAI, EUR, …).
3. At least 30 days of daily history.
4. Rank by 30-day average daily quote volume; keep the **top 60** with average ≥ $20M.

The bid-ask spread filter (≤ 10 bps) is **live-only**. No historical spread data exists.

## 4. Indicators

Wilder smoothing (RMA, α = 1/n) for ATR(14), RSI(14) and ADX(14). EMAs use α = 2/(n+1),
seeded with the first value. Donchian(20) high/low = max high / min low of the **previous**
20 bars (current bar excluded). RVOL = bar volume ÷ mean volume of the previous 20 bars.

**Relative-strength rank:** rs24 = coin 24h return − BTC 24h return; rs7d = coin 168h
return − BTC 168h return. Rank = mean of the two cross-sectional percentile ranks within
the current universe (0 = weakest, 1 = strongest).

## 5. Market state (each hour)

- `breadth` = share of universe coins whose last 4h close > EMA50(4h)
- `btc_up` = BTC last completed daily close > EMA200(1d)
- `med_natr` = median over the universe of ATR14(1h) ÷ close

| State | Rule | Directions | Size × |
|---|---|---|---|
| `NO_TRADE` | `med_natr` < 0.0035 | none | 0 |
| `RISK_ON` | `btc_up` and `breadth` ≥ 0.55 | long | 1.0 |
| `RISK_OFF` | not `btc_up` and `breadth` ≤ 0.45 | short | 1.0 |
| `NEUTRAL` | otherwise | long + short | 0.5 |

The ML regime model is **not** used in v1. It is trained per pair (ETH/BNB/DOGE/XRP),
not on BTC, and has no faithful history across the whole universe. Using it live but
not in the backtest would break backtest/live parity.

## 6. Setups

All rules mirror for shorts. "4h" values are from the last **completed** 4h bar.

### A. Momentum breakout (evaluated only at hours when a 4h bar closes)

Long when all hold:
- rank ≥ 0.80
- 4h close > Donchian(20) high of 4h bars → level `L` = that high
- 4h RVOL ≥ **`rvol_min`**
- ADX(14, 4h) ≥ 20 and higher than the previous 4h bar's ADX

Entry zone `[L, L + 0.5·ATR4h]`. Stop = `L − `**`stop_atr`**`·ATR4h`.
Short: rank ≤ 0.20, close < Donchian low, zone `[L − 0.5·ATR4h, L]`, stop above.

Grid: `rvol_min ∈ {1.5, 2.0, 2.5}` × `stop_atr ∈ {1.0, 1.5, 2.0}`. Default cell (2.0, 1.5).

### B. Pullback in trend (evaluated every hour)

Long when all hold:
- rank ≥ **`rank_min`**
- 4h EMA20 > EMA50 > EMA200
- within the last 3 closed 1h bars, some low ≤ EMA20(4h) + 0.25·ATR14(4h)
- 1h close > EMA50(4h)
- min RSI14(1h) over the 3 bars before the current one is in [40, 50]
- current RSI > previous RSI, and current close > previous bar's high

Stop = (lowest low of last 6 1h bars) − 0.1·ATR1h, but at least **`min_stop_atr`**·ATR1h
below the close. Entry zone `close ± 0.25·ATR1h`.
Short: rank ≤ 1 − `rank_min`, EMA20 < EMA50 < EMA200, high ≥ EMA20 − 0.25·ATR4h,
max RSI of prior 3 bars in [50, 60], RSI falling, close < previous low.

Grid: `rank_min ∈ {0.6, 0.7, 0.8}` × `min_stop_atr ∈ {0.75, 1.0, 1.5}`. Default (0.7, 1.0).

### Common to both

- Side must be allowed by the market state.
- Stop distance must be between 0.5% and 10% of the signal close; otherwise skip.
- Targets from the signal close `C` and risk `R = |C − stop|`: TP1 = C ± 1R, TP2 = ±2R, TP3 = ±3R.
- Score = 100 × strength (rank for longs, 1 − rank for shorts). Higher score is taken first.

Design-doc deviations, fixed here: funding and open-interest filters are **removed from v1**
(moved to Phase 4). Pullback targets are R-multiples, not "prior high".

## 7. Execution model (copies the live futures signal path)

- Signal at close of 1h bar *t*. Entry at the **open of bar t+1, only if that open is inside
  the entry zone**; otherwise the setup is skipped ("chased"). (Live Phase 3 must set the
  scout's entry-chase tolerance to 0% to match.)
- Costs per fill: **5 bps fee + 8 bps adverse slippage**, on entry and on every exit.
  Funding: 0.01% of notional per 8h held, charged regardless of side.
- Ladder (as in `_manage_futures_positions`): close 33% at TP1 and move stop to entry;
  close 50% of remaining at TP2 (stop stays at entry); close the rest at TP3.
- Per bar, starting with the entry bar: the **stop is checked first**. If the bar touches both
  stop and a target, the stop fills. A gap through the stop fills at the bar open. If TP1
  fills and the same bar also reaches the new breakeven stop, the rest closes at breakeven.
- Max hold 72 hours, then exit at that bar's close.
- Sizing: budget fixed at **$10,000** (no compounding). Risk per trade = 0.75% × size multiplier.
  Quantity = risk $ ÷ |entry − stop|. Notional capped at $7,500 (25% of budget × 3x leverage).

## 8. Portfolio rules

Max 3 open positions; one per coin; no new entry on a coin for 12h after a stop-out on it.
Skip a candidate if the correlation of its 1h returns (previous 720 bars) with any open
position is > 0.75. Daily loss limit: once realised loss in a UTC day reaches 3% of
budget, no new entries that day.

## 9. Walk-forward

- 13 folds. Test = one calendar quarter, 2023Q3 → 2026Q3. Train = the 6 months before it.
- Each grid cell is run as one continuous portfolio simulation over the whole period.
- In each fold, pick the cell with the highest **train** mean R among cells with ≥ 30 train
  trades. If none qualifies, use the default cell.
- The out-of-sample (OOS) result = the selected cell's trades that **enter** inside the test quarter,
  stitched across the 13 folds.
- Each detector is judged **separately**. A combined A+B run is reported for information only.

## 10. Random-entry null

For each real OOS trade, draw a replacement in the same fold: a random coin from that
day's universe, a random hour where the market state allows the **same side**, and a stop
placed at the **same distance in ATR(1h) units** as the real trade. Same ladder, hold limit
and costs. Entry at next bar open, no zone check, no portfolio rules.
Repeat **1,000 times**, recording mean R each time. The real p-value = share of null runs with
mean R ≥ the real mean R.

## 11. GO criteria (all required, OOS only, per detector)

| # | Metric | Threshold |
|---|---|---|
| 1 | Trades | ≥ 150 |
| 2 | Expectancy (mean net R per trade) | > 0.10R |
| 3 | Profit factor (gross $ won ÷ gross $ lost) | ≥ 1.25 |
| 4 | vs random-entry null | p ≤ 0.05 |
| 5 | Folds with positive total R (among folds with ≥ 1 trade) | ≥ 60% |
| 6 | Max drawdown of realised equity (on $10k budget) | ≤ 15% |
| 7 | Total R with the single best coin's trades removed | > 0 |

A detector passing all 7 → **GO to Phase 2 (shadow)**. Failing any → **NO-GO**. The detector
stays off, and the result is written up as found.

**Reported for information, not a GO criterion:** return on the $10k budget vs BTC
buy-and-hold and vs an equal-weight universe hold over the same OOS period. A capped-risk
R strategy and a fully-invested hold aren't a fair head-to-head, so this comparison is not
pass/fail. (This replaces the design doc's "must beat" wording.)

## 12. Reproducibility

Random seed 20261128 for the null. The report records the git commit, data date range,
symbol count and the selected grid cell per fold. Output: `reports/scout_walk_forward.md` + `.json`.
