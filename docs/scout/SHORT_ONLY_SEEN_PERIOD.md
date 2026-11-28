# Scout Bot — Short-only breakdown — SEEN period (CONTAMINATED, information only)

Rules: `docs/scout/PREREGISTRATION_SHORT.md`. Commit `cb66e8c`. OOS 2023-07-01 → 2026-09-30, 524 symbols. Runtime 29.7s.

Market state share of OOS hours: NO_TRADE 0%, RISK_ON 28%, RISK_OFF 24%, NEUTRAL 47%

Universe size per hour (OOS): min 59, mean 60.0, max 60. Side filter: short only.

## Detector breakout: **NO-GO**

| # | Criterion | Value | Threshold | Pass |
|---|---|---|---|---|
| 1 | trades | 293 | ≥ 150 | ✅ |
| 2 | expectancy_r | -0.047 | > 0.10R | ❌ |
| 3 | profit_factor | 0.836 | ≥ 1.25 | ❌ |
| 4 | null_p | 0.617 | ≤ 0.05 | ❌ |
| 5 | folds_positive | 0.385 | ≥ 60% | ❌ |
| 6 | max_dd | 0.124 | ≤ 15% | ✅ |
| 7 | ex_best_coin_r | -18.908 | > 0 | ❌ |

OOS: 293 trades, total -13.810R, win rate 0.536, P&L $-1,020 on $10k (-10.2%).
Null: mean -0.026R, 95th pct 0.070R, p = 0.617 (1000 runs).
Exits: {'stop': 112, 'time': 90, 'breakeven': 74, 'tp3': 17}. Sides: {'short': 293}. States: {'NEUTRAL': 199, 'RISK_OFF': 94}.
Best coin WLDUSDT (5.098R).

Per fold (selected cell, train mean R → OOS total R):

| Fold | Cell | Train mean R | OOS total R |
|---|---|---|---|
| 2023Q3 | {'rvol_min': 1.5, 'stop_atr': 2.0} | -0.066 | -1.797 |
| 2023Q4 | {'rvol_min': 2.0, 'stop_atr': 2.0} | 0.112 | -2.207 |
| 2024Q1 | {'rvol_min': 2.0, 'stop_atr': 1.5} | -0.158 | 0.995 |
| 2024Q2 | {'rvol_min': 1.5, 'stop_atr': 1.0} | -0.203 | -9.057 |
| 2024Q3 | {'rvol_min': 1.5, 'stop_atr': 2.0} | 0.092 | 6.739 |
| 2024Q4 | {'rvol_min': 2.5, 'stop_atr': 1.0} | 0.332 | 2.742 |
| 2025Q1 | {'rvol_min': 2.0, 'stop_atr': 1.0} | 0.447 | -9.781 |
| 2025Q2 | {'rvol_min': 1.5, 'stop_atr': 1.5} | 0.143 | 7.545 |
| 2025Q3 | {'rvol_min': 1.5, 'stop_atr': 1.5} | 0.238 | -4.790 |
| 2025Q4 | {'rvol_min': 1.5, 'stop_atr': 1.5} | 0.055 | -0.712 |
| 2026Q1 | {'rvol_min': 1.5, 'stop_atr': 2.0} | 0.250 | -4.885 |
| 2026Q2 | {'rvol_min': 2.0, 'stop_atr': 1.0} | 0.149 | 1.610 |
| 2026Q3 | {'rvol_min': 2.0, 'stop_atr': 1.0} | 0.149 | -0.212 |

Default cell, OOS (info): 211 trades, mean -0.029R, PF 0.944.
Default-cell skips: {'already_open': 40, 'chased': 254, 'capacity': 39, 'correlated': 11, 'cooldown': 3}.

## For information only (not GO criteria)

- BTC buy-and-hold over OOS: 174.4%; equal-weight universe: -79.7%.
- Combined A+B (default cells): 211 trades, mean -0.029R, return on budget -2.5%.
