# Appendix E: Supplementary Signal Tests

This appendix gives the details behind Section 4.8. The tests were run in November 2026, after the main evaluation was frozen. They use a separate rule-based signal scanner that shares no code with the PPO environment. They answer a narrower question than the main study: do simple, published-style trading signals show an edge after costs on a wide set of cryptocurrency futures?

**E.1 Design common to all tests**

*Data.* Hourly and daily price bars for Binance USDT-margined perpetual futures, and funding-rate and open-interest records, were taken from Binance's public historical archive (Binance, 2026). The archive keeps delisted contracts, so the coin list on any past day could be rebuilt as it was then.

*Daily coin list.* Each day, using data up to the previous day only, the scanner kept USDT perpetuals (excluding stablecoin pairs, dated contracts and index contracts) with at least 30 days of history and an average daily traded value of at least $20 million over the previous 30 days. It then took the top 60 by that average. 524 coins appeared on the list at some point between 2023 and 2026, and 179 between 2020 and 2023.

*Timing.* Signals used only closed bars. A value at an hourly bar used data up to that bar's close, and four-hour values became available only once the four-hour bar had closed. A test that altered future bars confirmed that earlier values did not change.

*Market filter.* Each hour the market was classed as risk-on, risk-off, neutral or no-trade. The inputs were the share of listed coins above their 50-period four-hour average, whether Bitcoin was above its 200-day average, and the median hourly volatility. Trend signals could go long in risk-on, short in risk-off, and either way at half size in neutral. The contrarian funding signals ignored direction and were blocked only in no-trade conditions.

*Entries and exits.* A signal at the close of an hour entered at the next hour's open, but only if that price was still inside a set entry zone. Otherwise the signal was skipped. Exits copied the live system's futures signal path: a third of the position closed at a profit of 1R and the stop moved to the entry price, half the remainder closed at 2R, and the rest closed at 3R or after 72 hours. Where one hourly bar touched both the stop and a target, the stop was assumed to fill first. A gap through the stop filled at the bar's open.

*Costs.* Every fill paid a 5 basis-point fee and 8 basis points of adverse slippage. Funding was charged at 0.01% of the position value per eight hours held, whichever side was held. These costs are more realistic than the main study's 0.1% fee per side with no slippage (Section 5.4, limitation 8).

*Sizing and portfolio rules.* A fixed $10,000 budget risked 0.75% per trade (scaled by the market filter), with the position value capped at $7,500. At most three positions were open at once, one per coin. No coin was re-entered for 12 hours after a stop. A new position was skipped if its hourly returns over the previous 30 days correlated above 0.75 with any open position. New entries stopped for the day once realised losses reached 3% of the budget.

*Walk-forward testing.* Each test used quarterly out-of-sample windows. Each signal had a small grid of nine settings (two settings, three values each). In each quarter, the setting with the best average result over the previous six months was used, provided it had at least 30 trades there; otherwise a pre-set default was used. A small grid with every result reported limits the risk of backtest overfitting (Bailey et al., 2017).

*Random-entry comparison.* For each real trade, a random trade was drawn in the same quarter: a random coin from that day's list, a random hour in which the same side was allowed, and a stop at the same distance in volatility units. It used the same exits and costs. Repeating this 1,000 times gave a distribution of average results, and the p-value is the share of random runs that did at least as well as the real signal (White, 2000).

*Pass criteria.* All of the following were required, measured on out-of-sample trades only: at least 150 trades; an average above +0.10R per trade; a profit factor of at least 1.25; a random-entry p-value of at most 0.05; profit in at least 60% of quarters; a maximum drawdown of at most 15% of the budget; and a positive total after removing the best coin. For the two funding signals, which were tested together, the p-value limit was halved to 0.025, and both halves of the test period had to be profitable.

**E.2 The five tests**

*Test 1, breakout (July 2023 to September 2026).* Trades followed a four-hour close beyond the previous 20 four-hour bars' range, on above-average volume, with a rising trend-strength reading (ADX). Longs were limited to the strongest 20% of coins by return relative to Bitcoin, and shorts to the weakest 20%. Cryptocurrency returns show momentum (Liu and Tsyvinski, 2021), which motivated this test.

*Test 2, pullback in trend (same period).* Trades followed a dip towards the four-hour 20-period average within an established trend, confirmed by a turn in the hourly RSI and a close beyond the previous bar.

*Test 3, short-only breakdowns (January 2021 to June 2023).* This was the short side of Test 1 only. The idea came from Test 1's result, where shorts lost much less than longs. So that the test was not circular, it was run on an earlier period that no part of the project had examined.

*Test 4, funding-rate fade (January 2021 to September 2026).* When a coin's funding rate was extreme (at least 0.05% per eight hours in the default setting) and among the most extreme 10% of the list, the scanner traded against the crowded side once the price bar turned, and only after a price move in the crowd's favour over the previous 24 hours.

*Test 5, funding fade with rising open interest (July 2022 to September 2026).* This was Test 4, plus a requirement that open interest had risen by at least 10% over 24 hours, meaning the crowd was still adding positions. Open-interest records for most coins start around December 2021, so the period is shorter. Because downloading every coin-day would have meant about a million files, open interest was fetched only for the coin-days where Test 4 fired: 7,063 files, of which one was missing.

**E.3 Results against each criterion**

Table E.1: Out-of-sample results against the pass criteria (✓ = passed)

| Criterion | Breakout | Pullback | Breakdown, short only | Funding fade | Funding fade with rising open interest |
|---|---:|---:|---:|---:|---:|
| Trades (≥ 150) | 632 ✓ | 2,730 ✓ | 138 | 1,597 ✓ | 275 ✓ |
| Mean R per trade (> 0.10) | −0.09 | −0.13 | +0.15 ✓ | −0.20 | −0.18 |
| Profit factor (≥ 1.25) | 0.81 | 0.74 | 1.45 ✓ | 0.64 | 0.66 |
| Random-entry mean R | −0.10 | −0.15 | +0.02 | −0.19 | −0.21 |
| p-value (≤ 0.05; ≤ 0.025 for funding) | 0.40 | 0.08 | 0.08 | 0.67 | 0.33 |
| Quarters with a profit (≥ 60%) | 23% | 15% | 60% ✓ | 13% | 35% |
| Maximum drawdown (≤ 15%) | 33% | 194% | 6% ✓ | 247% | 51% |
| Total R without best coin (> 0) | −62.0 | −350.9 | +15.3 ✓ | −335.5 | −53.9 |
| Mean R in each half (> 0, funding only) | – | – | – | −0.08 / −0.24 | −0.12 / −0.22 |
| Criteria passed | 1 of 7 | 1 of 7 | 5 of 7 | 1 of 8 | 1 of 8 |

Drawdowns above 100% arise because the budget was held fixed and not stopped when exhausted. They show the size of the losses, not a reachable account balance.

Further checks support the results. With costs removed, Tests 1, 2 and 4 still lost about 0.07R, 0.06R and 0.14R per trade. Average results by exit type matched the exit rules: stops lost slightly more than 1R once costs were included, and full exits at the third target made about 1.9R. Every one of the nine settings lost money for Tests 4 and 5. In Test 3, 84% of the total profit came from the four quarters of 2022, and random shorts were also profitable in that period.

**E.4 Limitations**

The scanner is a rule-based system, not a learning agent, so these tests say nothing direct about the PPO reward. Prices come from Binance, while the live system's paper futures account is priced from Gate.io; the two track each other closely on liquid contracts. Bars are hourly, so the order of events within an hour is unknown, and the stop-first rule makes results conservative. The bid-ask spread filter used live could not be applied historically. Finally, the tests cover a period in which Bitcoin rose 174% (July 2023 to September 2026) while an equal-weighted basket of listed coins fell about 80%, a difficult backdrop for altcoin trading.
