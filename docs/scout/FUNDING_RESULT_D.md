# Scout Bot — Funding fade + OI build-up (detector D) — 2022-07 → 2026-09

Rules: `docs/scout/PREREGISTRATION_FUNDING.md`. Commit `6538b54`. OOS 2022-07-01 → 2026-09-30, 542 symbols. Runtime 249.4s.

Market state share of OOS hours: NO_TRADE 0%, RISK_ON 26%, RISK_OFF 26%, NEUTRAL 48%

Universe size per hour (OOS): min 59, mean 60.0, max 60. Side filter: long + short.

## Detector funding_oi: **NO-GO**

| # | Criterion | Value | Threshold | Pass |
|---|---|---|---|---|
| 1 | trades | 275 | ≥ 150 | ✅ |
| 2 | expectancy_r | -0.184 | > 0.10R | ❌ |
| 3 | profit_factor | 0.660 | ≥ 1.25 | ❌ |
| 4 | null_p | 0.328 | ≤ 0.025 | ❌ |
| 5 | folds_positive | 0.353 | ≥ 60% | ❌ |
| 6 | max_dd | 0.505 | ≤ 15% | ❌ |
| 7 | ex_best_coin_r | -53.869 | > 0 | ❌ |
| 8 | both_halves_positive | {'2022-07-01→2024-06-30': -0.12410579378027498, '2024-07-01→2026-09-30': -0.22199399775202164} | > 0R in each half | ❌ |

OI fetch: {'candidates': 3958, 'files': 7063, 'missing_files': 1, 'oi_known': 3954, 'oi_ge_10pct': 454}

OOS: 275 trades, total -50.476R, win rate 0.473, P&L $-3,786 on $10k (-37.9%).
Null: mean -0.210R, 95th pct -0.115R, p = 0.328 (1000 runs).
Exits: {'time': 22, 'stop': 142, 'tp3': 28, 'breakeven': 83}. Sides: {'long': 184, 'short': 91}. States: {'RISK_OFF': 92, 'NEUTRAL': 108, 'RISK_ON': 75}.
Best coin ONDOUSDT (3.392R).

Per fold (selected cell, train mean R → OOS total R):

| Fold | Cell | Train mean R | OOS total R |
|---|---|---|---|
| 2022Q3 | {'f_min': 0.0005, 'min_stop_atr': 1.5} (default) | None | 1.150 |
| 2022Q4 | {'f_min': 0.0005, 'min_stop_atr': 1.5} (default) | None | 0.578 |
| 2023Q1 | {'f_min': 0.0003, 'min_stop_atr': 2.0} | 0.065 | 9.985 |
| 2023Q2 | {'f_min': 0.0003, 'min_stop_atr': 2.0} | 0.299 | 2.216 |
| 2023Q3 | {'f_min': 0.0005, 'min_stop_atr': 1.5} (default) | None | -6.907 |
| 2023Q4 | {'f_min': 0.0005, 'min_stop_atr': 1.5} (default) | None | -9.745 |
| 2024Q1 | {'f_min': 0.0003, 'min_stop_atr': 2.0} | -0.596 | -6.512 |
| 2024Q2 | {'f_min': 0.0005, 'min_stop_atr': 2.0} | -0.351 | -4.168 |
| 2024Q3 | {'f_min': 0.0005, 'min_stop_atr': 2.0} | -0.254 | -0.585 |
| 2024Q4 | {'f_min': 0.0005, 'min_stop_atr': 1.5} (default) | None | 1.806 |
| 2025Q1 | {'f_min': 0.0005, 'min_stop_atr': 1.5} (default) | None | -5.237 |
| 2025Q2 | {'f_min': 0.0003, 'min_stop_atr': 1.0} | -0.008 | 6.078 |
| 2025Q3 | {'f_min': 0.0003, 'min_stop_atr': 1.0} | 0.104 | -10.422 |
| 2025Q4 | {'f_min': 0.0003, 'min_stop_atr': 1.0} | -0.117 | -9.371 |
| 2026Q1 | {'f_min': 0.0008, 'min_stop_atr': 1.0} | -0.506 | -9.581 |
| 2026Q2 | {'f_min': 0.0008, 'min_stop_atr': 1.0} | -0.330 | -6.875 |
| 2026Q3 | {'f_min': 0.0003, 'min_stop_atr': 1.5} | -0.287 | -2.886 |

Default cell, OOS (info): 247 trades, mean -0.231R, PF 0.589.
Default-cell skips: {'correlated': 4, 'already_open': 56, 'cooldown': 36}.

## For information only (not GO criteria)

- BTC buy-and-hold over OOS: 311.9%; equal-weight universe: -83.9%.
- Combined A+B (default cells): 247 trades, mean -0.231R, return on budget -42.9%.
