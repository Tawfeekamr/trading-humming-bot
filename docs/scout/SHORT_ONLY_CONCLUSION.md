# Scout Bot — v2 short-only conclusion (2026-11-28)

**Verdict: NO-GO** under `PREREGISTRATION_SHORT.md` (commit 1804a23).
Primary result: `SHORT_ONLY_RESULT.md` (code cb66e8c). Information-only seen-period run:
`SHORT_ONLY_SEEN_PERIOD.md`.

## Primary test — unseen period 2021-01 → 2023-06

| # | Criterion | Value | Threshold | Pass |
|---|---|---|---|---|
| 1 | Trades | 138 | ≥ 150 | ❌ |
| 2 | Expectancy | +0.147R | > 0.10R | ✅ |
| 3 | Profit factor | 1.45 | ≥ 1.25 | ✅ |
| 4 | vs random shorts | p = 0.083 | ≤ 0.05 | ❌ |
| 5 | Positive folds | 60% | ≥ 60% | ✅ |
| 6 | Max drawdown | 6.1% | ≤ 15% | ✅ |
| 7 | R without best coin | +15.3R | > 0 | ✅ |

+$1,515 on the $10k budget (+15%) over 2.5 years.

## What it means, in plain words

1. **It made money, but we can't tell it apart from luck.** Random shorts with the same
   exits also made money in this period (+0.02R on average). Breakdown shorts did better
   (+0.15R), but 8% of random runs did that well too. The bar was 5%.
2. **Too few trades to be sure.** 138 is below the 150 fixed in advance. As pre-registered,
   that counts as not enough evidence. The threshold is not lowered after the fact.
3. **The profit is mostly the 2022 bear market.** 84% of the R (17.0 of 20.3) came from the
   four 2022 quarters. 2021 added +3.4R and 2023 H1 was flat (−0.1R). The edge looks like
   "shorting worked in a crash year", not a repeatable timing skill.
4. **The seen period (info only) agrees.** Short-only on 2023-07 → 2026-09 lost
   −0.05R per trade over 293 trades, no better than random (p = 0.62).

## Decision (as pre-registered)

NO-GO → the scout's TA-breakout family is **closed**. No Phase 2. A further attempt needs a
**different signal family** (e.g. funding-rate / open-interest crowding) with its own
pre-registration. Re-testing breakdown shorts with relaxed thresholds would be p-hacking.
