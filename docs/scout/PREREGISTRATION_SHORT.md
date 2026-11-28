# Scout Bot — Pre-registration v2: short-only breakdown test

**Committed:** 2026-11-28, before any short-only run and before any data from the
test period below was downloaded or looked at.
**Parent:** `PREREGISTRATION.md` (v1). Everything in v1 applies **except** the changes listed here.
**Why this test exists:** in the v1 result, breakout *shorts* lost far less than longs
(≈ −0.01R vs −0.15R per trade). That observation came from looking at v1's out-of-sample
data, so re-testing on that period would be circular. The primary test therefore uses an
**earlier, unseen period**.

---

## 1. Hypothesis

Breakdowns (the v1 breakout detector, short side only) earn a positive return after costs
and beat random short entries using the same exits.

## 2. What changes vs v1

| Item | v1 | v2 |
|---|---|---|
| Detectors | A breakout + B pullback | **A breakout only** |
| Sides | long + short | **short only** (long branch dropped before portfolio rules) |
| Primary test period (OOS) | 2023-07-01 → 2026-09-30 | **2021-01-01 → 2023-06-30** (10 quarterly folds) |
| Data range | 1h 2022-11 → 2026-09 | 1d 2019-09 → 2023-06; 1h 2020-05 → 2023-06 |
| First train window | 2023-01 → 2023-06 | 2020-07 → 2020-12 |

Unchanged from v1: universe rules (top 60, ≥ $20M avg volume, ≥ 30 days), indicators,
market state (shorts allowed in `RISK_OFF` at full size and `NEUTRAL` at half size), the
breakout short rule, 3×3 grid (`rvol_min` × `stop_atr`, default (2.0, 1.5)), per-fold cell
selection, entry-zone rule, costs (5 + 8 bps per fill, funding 0.01%/8h), exit ladder,
stop-first bar rules, sizing, portfolio rules, random-entry null (1,000 runs, same side),
seed 20261128.

## 3. GO criteria (all required, primary period only)

Same seven as v1 §11:

| # | Metric | Threshold |
|---|---|---|
| 1 | Trades | ≥ 150 |
| 2 | Expectancy | > 0.10R |
| 3 | Profit factor | ≥ 1.25 |
| 4 | vs random-entry null | p ≤ 0.05 |
| 5 | Positive folds (with ≥ 1 trade) | ≥ 60% |
| 6 | Max drawdown | ≤ 15% of $10k |
| 7 | Total R without best coin | > 0 |

If the early universe is too thin to reach 150 trades, the result is **NO-GO** (not enough
evidence). The threshold is not lowered.

## 4. Known biases, stated up front

- **2022 was a deep bear market.** That favours shorts. The 2021 bull run is in the
  same window, and the market-state gate is meant to handle direction. A pass that comes
  entirely from 2022 folds will be reported as such.
- **Thinner early universe.** Fewer coins had ≥ $20M daily futures volume in 2020–21.
- **Hypothesis chosen after seeing v1.** That's why the primary period is unseen. The seen
  period (2023-07 → 2026-09) is re-run short-only **for information only**, labelled
  "contaminated", and it can't count toward GO.

## 5. Decision rule

GO → Phase 2 (shadow) for **breakdown shorts on paper futures only**.
NO-GO → the scout's TA-breakout family is closed. Any further idea needs a different
signal family and a new pre-registration.

## 6. Output

`docs/scout/SHORT_ONLY_RESULT.md` + `.json` (primary) and `SHORT_ONLY_SEEN_PERIOD.md`/`.json` (info only).
