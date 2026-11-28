# Scout Bot — Pre-registration v3: funding-rate / open-interest crowding test

**Committed:** 2026-11-28, before any funding-rate or open-interest data was downloaded
or looked at. (File-format checks on 4 sample files only: column names, availability dates.)
**Parent:** `PREREGISTRATION.md` (v1). Everything in v1 applies **except** the changes listed here.

## 1. Hypothesis

When one side of the futures market is **crowded** (extreme funding rate, paying the other
side heavily), price tends to move against the crowd once momentum stalls. This is a
contrarian idea, separate from the TA breakout family that v1/v2 closed.

Two detectors are tested. Because there are two, each must pass the null at **p ≤ 0.025**
(Bonferroni correction for 2 tests) instead of 0.05.

## 2. Data

- **Funding:** `data.binance.vision` `futures/um/monthly/fundingRate`. Columns: `calc_time`
  (ms), `funding_interval_hours`, `last_funding_rate`. Rate normalised to an 8h basis:
  `f8 = rate × 8 / interval_hours`.
- **Open interest:** `futures/um/daily/metrics` (5-minute `sum_open_interest`). Available for
  most coins from about **Dec 2021**, so detector D is tested on a shorter period. Daily files
  are fetched **only for the coin-days where a detector-C candidate fires** (plus the day before).
  Fetching about 1M files for every coin-day isn't feasible. This doesn't leak, because OI
  is only read at the candidate's own signal time.
- **Prices / universe:** as v1. 1d 2019-09 → 2026-09, 1h 2020-05 → 2026-09 (the v1 and v2
  caches merged).

**Honesty note:** the price data for 2021–2026 was already used in v1/v2. The funding and OI
data was not. The detector rules below come from the general crowding idea, set before
looking at any funding or OI values. Two guards against "we know 2022 crashed": the rule is
symmetric (long and short), and criterion 8 (§6) requires profit in both halves of the test period.

## 3. Features (at the close of 1h bar t)

- `f8` = the latest funding print with `calc_time` ≤ bar close, on an 8h basis.
- **Print hour:** a row is a *print row* for a coin if a funding print happened during that bar
  (`calc_time` in (bar open, bar close]). Detectors only fire on print rows.
- `f_rank` = cross-sectional percentile rank of `f8` within that hour's universe (0 = most
  negative, 1 = most positive), using each coin's latest print.
- `oi_chg24` = OI at the last 5-min snapshot ≤ bar close ÷ OI at the last snapshot ≤ (bar close − 24h) − 1.
- From v1: `ret24`, `atr1h`, `high_prev`, `low_prev`, `high_max6`, `low_min6`.

## 4. Detector C — funding fade (print rows only)

**Short** when all hold: `f8 ≥ F`, `f_rank ≥ 0.90`, `ret24 > 0` (price ran up), and
`close < low_prev` (bearish rejection bar).
Stop = max(`high_max6` + 0.1·ATR1h, close + `min_stop_atr`·ATR1h).

**Long** (mirror): `f8 ≤ −F`, `f_rank ≤ 0.10`, `ret24 < 0`, `close > high_prev`.
Stop = min(`low_min6` − 0.1·ATR1h, close − `min_stop_atr`·ATR1h).

Entry zone close ± 0.25·ATR1h. Targets 1R/2R/3R from the close. Stop distance 0.5–10%.
Score = 100 × `f_rank` for shorts, 100 × (1 − `f_rank`) for longs.

Grid: `F ∈ {0.03%, 0.05%, 0.08%}` × `min_stop_atr ∈ {1.0, 1.5, 2.0}`. Default (0.05%, 1.5).

## 5. Detector D — funding fade + OI build-up

All of C, **plus** `oi_chg24 ≥ +10%` (the crowd is still adding positions).
If OI is missing for that coin/time, the candidate is dropped. Same grid and default as C.
OI threshold fixed at 10% (not gridded).

## 6. Market state, sizing, periods, GO

- **Market state:** only the `NO_TRADE` gate (low volatility) applies. The direction gate
  (RISK_ON = longs only, etc.) is **not** applied, because a contrarian signal naturally
  goes against the trend. Size multiplier 1.0 whenever the state isn't `NO_TRADE`.
- The random-entry null is matched the same way: same side, any hour that isn't `NO_TRADE`.
- **Periods:**

| | OOS | Folds | First train window | Halves for criterion 8 |
|---|---|---|---|---|
| C | 2021-01-01 → 2026-09-30 | 23 | 2020-07 → 2020-12 | 2021-01→2023-06 / 2023-07→2026-09 |
| D | 2022-07-01 → 2026-09-30 | 17 | 2022-01 → 2022-06 | 2022-07→2024-06 / 2024-07→2026-09 |

- All other v1 rules apply: per-fold cell selection (≥ 30 train trades else default), costs,
  exit ladder, stop-first, sizing, portfolio rules, seed 20261128, 1,000 null runs.

**GO criteria, all required, per detector:**

| # | Metric | Threshold |
|---|---|---|
| 1 | Trades | ≥ 150 |
| 2 | Expectancy | > 0.10R |
| 3 | Profit factor | ≥ 1.25 |
| 4 | vs random-entry null | **p ≤ 0.025** |
| 5 | Positive folds (with ≥ 1 trade) | ≥ 60% |
| 6 | Max drawdown | ≤ 15% of $10k |
| 7 | Total R without best coin | > 0 |
| 8 | **Expectancy > 0 in each half** of the OOS period | both halves |

## 7. Decision rule

A detector passing all 8 → Phase 2 (shadow) for that detector on paper futures.
Both fail → the scout project stops. No relaxed re-tests. Results are written up as found.

## 8. Output

`docs/scout/FUNDING_RESULT.md` + `.json`, conclusion in `FUNDING_CONCLUSION.md`.
