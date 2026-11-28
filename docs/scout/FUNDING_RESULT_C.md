# Scout Bot — Funding fade (detector C) — 2021-01 → 2026-09

Rules: `docs/scout/PREREGISTRATION_FUNDING.md`. Commit `6538b54`. OOS 2021-01-01 → 2026-09-30, 549 symbols. Runtime 58.5s.

Market state share of OOS hours: NO_TRADE 0%, RISK_ON 27%, RISK_OFF 29%, NEUTRAL 44%

Universe size per hour (OOS): min 32, mean 59.8, max 60. Side filter: long + short.

## Detector funding: **NO-GO**

| # | Criterion | Value | Threshold | Pass |
|---|---|---|---|---|
| 1 | trades | 1597 | ≥ 150 | ✅ |
| 2 | expectancy_r | -0.204 | > 0.10R | ❌ |
| 3 | profit_factor | 0.637 | ≥ 1.25 | ❌ |
| 4 | null_p | 0.665 | ≤ 0.025 | ❌ |
| 5 | folds_positive | 0.130 | ≥ 60% | ❌ |
| 6 | max_dd | 2.469 | ≤ 15% | ❌ |
| 7 | ex_best_coin_r | -335.506 | > 0 | ❌ |
| 8 | both_halves_positive | {'2021-01-01→2023-06-30': -0.0837660621204263, '2023-07-01→2026-09-30': -0.2399766722975108} | > 0R in each half | ❌ |

OOS: 1597 trades, total -326.538R, win rate 0.456, P&L $-24,457 on $10k (-244.6%).
Null: mean -0.193R, 95th pct -0.151R, p = 0.665 (1000 runs).
Exits: {'stop': 852, 'breakeven': 491, 'time': 85, 'tp3': 167, 'data_end': 2}. Sides: {'short': 426, 'long': 1171}. States: {'RISK_ON': 410, 'NEUTRAL': 594, 'RISK_OFF': 593}.
Best coin POPCATUSDT (8.968R).

Per fold (selected cell, train mean R → OOS total R):

| Fold | Cell | Train mean R | OOS total R |
|---|---|---|---|
| 2021Q1 | {'f_min': 0.0008, 'min_stop_atr': 2.0} | -0.097 | -16.220 |
| 2021Q2 | {'f_min': 0.0008, 'min_stop_atr': 2.0} | -0.096 | -1.195 |
| 2021Q3 | {'f_min': 0.0008, 'min_stop_atr': 1.0} | -0.072 | -1.500 |
| 2021Q4 | {'f_min': 0.0008, 'min_stop_atr': 1.5} | 0.052 | -1.730 |
| 2022Q1 | {'f_min': 0.0005, 'min_stop_atr': 2.0} | 0.131 | 2.140 |
| 2022Q2 | {'f_min': 0.0005, 'min_stop_atr': 1.0} | 0.155 | -7.170 |
| 2022Q3 | {'f_min': 0.0008, 'min_stop_atr': 1.0} | -0.005 | -4.309 |
| 2022Q4 | {'f_min': 0.0008, 'min_stop_atr': 1.0} | -0.120 | 1.216 |
| 2023Q1 | {'f_min': 0.0008, 'min_stop_atr': 1.0} | -0.052 | -0.974 |
| 2023Q2 | {'f_min': 0.0008, 'min_stop_atr': 2.0} | 0.121 | -0.666 |
| 2023Q3 | {'f_min': 0.0005, 'min_stop_atr': 2.0} | 0.095 | -11.294 |
| 2023Q4 | {'f_min': 0.0008, 'min_stop_atr': 2.0} | -0.106 | -4.562 |
| 2024Q1 | {'f_min': 0.0008, 'min_stop_atr': 2.0} | -0.138 | -0.447 |
| 2024Q2 | {'f_min': 0.0008, 'min_stop_atr': 2.0} | -0.060 | -0.077 |
| 2024Q3 | {'f_min': 0.0008, 'min_stop_atr': 2.0} | -0.006 | 2.208 |
| 2024Q4 | {'f_min': 0.0008, 'min_stop_atr': 2.0} | 0.043 | -2.873 |
| 2025Q1 | {'f_min': 0.0008, 'min_stop_atr': 2.0} | -0.010 | -13.633 |
| 2025Q2 | {'f_min': 0.0008, 'min_stop_atr': 1.5} | -0.076 | -26.087 |
| 2025Q3 | {'f_min': 0.0008, 'min_stop_atr': 1.0} | -0.151 | -42.271 |
| 2025Q4 | {'f_min': 0.0008, 'min_stop_atr': 1.5} | -0.195 | -38.943 |
| 2026Q1 | {'f_min': 0.0003, 'min_stop_atr': 2.0} | -0.225 | -64.410 |
| 2026Q2 | {'f_min': 0.0003, 'min_stop_atr': 2.0} | -0.283 | -63.533 |
| 2026Q3 | {'f_min': 0.0003, 'min_stop_atr': 2.0} | -0.345 | -30.208 |

Default cell, OOS (info): 2107 trades, mean -0.212R, PF 0.620.
Default-cell skips: {'correlated': 31, 'already_open': 387, 'cooldown': 489, 'capacity': 450, 'daily_loss': 10}.

## For information only (not GO criteria)

- BTC buy-and-hold over OOS: 188.0%; equal-weight universe: -76.2%.
- Combined A+B (default cells): 2107 trades, mean -0.212R, return on budget -333.8%.
