# Scout Bot — Short-only breakdown test — PRIMARY (unseen 2021-01 → 2023-06)

Rules: `docs/scout/PREREGISTRATION_SHORT.md`. Commit `cb66e8c`. OOS 2021-01-01 → 2023-06-30, 179 symbols. Runtime 9.8s.

Market state share of OOS hours: NO_TRADE 0%, RISK_ON 26%, RISK_OFF 34%, NEUTRAL 40%

Universe size per hour (OOS): min 32, mean 59.6, max 60. Side filter: short only.

## Detector breakout: **NO-GO**

| # | Criterion | Value | Threshold | Pass |
|---|---|---|---|---|
| 1 | trades | 138 | ≥ 150 | ❌ |
| 2 | expectancy_r | 0.147 | > 0.10R | ✅ |
| 3 | profit_factor | 1.446 | ≥ 1.25 | ✅ |
| 4 | null_p | 0.083 | ≤ 0.05 | ❌ |
| 5 | folds_positive | 0.600 | ≥ 60% | ✅ |
| 6 | max_dd | 0.061 | ≤ 15% | ✅ |
| 7 | ex_best_coin_r | 15.309 | > 0 | ✅ |

OOS: 138 trades, total 20.262R, win rate 0.580, P&L $1,515 on $10k (15.2%).
Null: mean 0.023R, 95th pct 0.170R, p = 0.083 (1000 runs).
Exits: {'breakeven': 33, 'stop': 52, 'tp3': 22, 'time': 30, 'data_end': 1}. Sides: {'short': 138}. States: {'NEUTRAL': 46, 'RISK_OFF': 92}.
Best coin AXSUSDT (4.952R).

Per fold (selected cell, train mean R → OOS total R):

| Fold | Cell | Train mean R | OOS total R |
|---|---|---|---|
| 2021Q1 | {'rvol_min': 1.5, 'stop_atr': 1.0} | 0.279 | -0.646 |
| 2021Q2 | {'rvol_min': 1.5, 'stop_atr': 1.0} | 0.180 | 8.359 |
| 2021Q3 | {'rvol_min': 2.0, 'stop_atr': 1.5} (default) | None | -1.227 |
| 2021Q4 | {'rvol_min': 2.0, 'stop_atr': 1.5} (default) | None | -3.119 |
| 2022Q1 | {'rvol_min': 2.0, 'stop_atr': 1.5} (default) | None | 0.147 |
| 2022Q2 | {'rvol_min': 1.5, 'stop_atr': 1.5} | 0.129 | 7.373 |
| 2022Q3 | {'rvol_min': 1.5, 'stop_atr': 1.5} | 0.287 | 3.461 |
| 2022Q4 | {'rvol_min': 2.0, 'stop_atr': 1.0} | 0.249 | 6.015 |
| 2023Q1 | {'rvol_min': 2.0, 'stop_atr': 1.0} | 0.197 | -1.917 |
| 2023Q2 | {'rvol_min': 2.0, 'stop_atr': 1.5} | 0.315 | 1.816 |

Default cell, OOS (info): 87 trades, mean 0.100R, PF 1.306.
Default-cell skips: {'chased': 148, 'capacity': 18, 'correlated': 4, 'already_open': 8}.

## For information only (not GO criteria)

- BTC buy-and-hold over OOS: 5.0%; equal-weight universe: 17.2%.
- Combined A+B (default cells): 87 trades, mean 0.100R, return on budget 7.1%.
