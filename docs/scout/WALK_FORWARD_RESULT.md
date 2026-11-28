# Scout Bot — Walk-forward result (Phase 1)

Rules: `docs/scout/PREREGISTRATION.md`. Commit `6af6816`. OOS 2023-07-01 → 2026-09-30, 524 symbols. Runtime 103.0s.

Market state share of OOS hours: NO_TRADE 0%, RISK_ON 28%, RISK_OFF 24%, NEUTRAL 47%

## Detector breakout: **NO-GO**

| # | Criterion | Value | Threshold | Pass |
|---|---|---|---|---|
| 1 | trades | 632 | ≥ 150 | ✅ |
| 2 | expectancy_r | -0.090 | > 0.10R | ❌ |
| 3 | profit_factor | 0.811 | ≥ 1.25 | ❌ |
| 4 | null_p | 0.402 | ≤ 0.05 | ❌ |
| 5 | folds_positive | 0.231 | ≥ 60% | ❌ |
| 6 | max_dd | 0.333 | ≤ 15% | ❌ |
| 7 | ex_best_coin_r | -61.989 | > 0 | ❌ |

OOS: 632 trades, total -56.918R, win rate 0.479, P&L $-3,314 on $10k (-33.1%).
Null: mean -0.099R, 95th pct -0.030R, p = 0.402 (1000 runs).
Exits: {'stop': 282, 'time': 159, 'breakeven': 136, 'tp3': 54, 'data_end': 1}. Sides: {'long': 443, 'short': 189}. States: {'RISK_ON': 252, 'NEUTRAL': 310, 'RISK_OFF': 70}.
Best coin INJUSDT (5.071R).

Per fold (selected cell, train mean R → OOS total R):

| Fold | Cell | Train mean R | OOS total R |
|---|---|---|---|
| 2023Q3 | {'rvol_min': 2.0, 'stop_atr': 2.0} | -0.047 | -3.610 |
| 2023Q4 | {'rvol_min': 1.5, 'stop_atr': 2.0} | -0.045 | -16.991 |
| 2024Q1 | {'rvol_min': 2.0, 'stop_atr': 2.0} | -0.080 | 3.434 |
| 2024Q2 | {'rvol_min': 2.5, 'stop_atr': 2.0} | -0.014 | -8.848 |
| 2024Q3 | {'rvol_min': 1.5, 'stop_atr': 2.0} | -0.073 | 9.703 |
| 2024Q4 | {'rvol_min': 1.5, 'stop_atr': 2.0} | 0.016 | 1.030 |
| 2025Q1 | {'rvol_min': 1.5, 'stop_atr': 2.0} | 0.092 | -5.364 |
| 2025Q2 | {'rvol_min': 1.5, 'stop_atr': 2.0} | -0.051 | -7.569 |
| 2025Q3 | {'rvol_min': 2.5, 'stop_atr': 1.5} | 0.091 | -2.412 |
| 2025Q4 | {'rvol_min': 1.5, 'stop_atr': 1.5} | 0.041 | -7.146 |
| 2026Q1 | {'rvol_min': 2.5, 'stop_atr': 2.0} | 0.189 | -5.274 |
| 2026Q2 | {'rvol_min': 2.0, 'stop_atr': 1.5} | -0.032 | -1.038 |
| 2026Q3 | {'rvol_min': 1.5, 'stop_atr': 1.0} | -0.048 | -12.834 |

Default cell, OOS (info): 714 trades, mean -0.103R, PF 0.785.
Default-cell skips: {'already_open': 145, 'chased': 1037, 'capacity': 314, 'cooldown': 28, 'correlated': 44}.

## Detector pullback: **NO-GO**

| # | Criterion | Value | Threshold | Pass |
|---|---|---|---|---|
| 1 | trades | 2730 | ≥ 150 | ✅ |
| 2 | expectancy_r | -0.125 | > 0.10R | ❌ |
| 3 | profit_factor | 0.741 | ≥ 1.25 | ❌ |
| 4 | null_p | 0.080 | ≤ 0.05 | ❌ |
| 5 | folds_positive | 0.154 | ≥ 60% | ❌ |
| 6 | max_dd | 1.937 | ≤ 15% | ❌ |
| 7 | ex_best_coin_r | -350.860 | > 0 | ❌ |

OOS: 2730 trades, total -342.402R, win rate 0.506, P&L $-19,274 on $10k (-192.7%).
Null: mean -0.153R, 95th pct -0.121R, p = 0.080 (1000 runs).
Exits: {'stop': 1306, 'breakeven': 909, 'time': 208, 'tp3': 304, 'data_end': 3}. Sides: {'long': 1622, 'short': 1108}. States: {'RISK_ON': 641, 'NEUTRAL': 1605, 'RISK_OFF': 484}.
Best coin TONUSDT (8.458R).

Per fold (selected cell, train mean R → OOS total R):

| Fold | Cell | Train mean R | OOS total R |
|---|---|---|---|
| 2023Q3 | {'rank_min': 0.6, 'min_stop_atr': 1.5} | -0.242 | -22.983 |
| 2023Q4 | {'rank_min': 0.6, 'min_stop_atr': 1.5} | -0.187 | -33.310 |
| 2024Q1 | {'rank_min': 0.6, 'min_stop_atr': 1.5} | -0.109 | -52.033 |
| 2024Q2 | {'rank_min': 0.8, 'min_stop_atr': 1.5} | -0.112 | -16.562 |
| 2024Q3 | {'rank_min': 0.8, 'min_stop_atr': 1.0} | -0.105 | -37.248 |
| 2024Q4 | {'rank_min': 0.6, 'min_stop_atr': 1.5} | -0.087 | -25.664 |
| 2025Q1 | {'rank_min': 0.6, 'min_stop_atr': 1.5} | -0.109 | 1.251 |
| 2025Q2 | {'rank_min': 0.8, 'min_stop_atr': 1.5} | -0.041 | -26.735 |
| 2025Q3 | {'rank_min': 0.8, 'min_stop_atr': 1.5} | -0.083 | -22.491 |
| 2025Q4 | {'rank_min': 0.7, 'min_stop_atr': 1.0} | -0.129 | 13.778 |
| 2026Q1 | {'rank_min': 0.7, 'min_stop_atr': 1.5} | -0.015 | -34.852 |
| 2026Q2 | {'rank_min': 0.6, 'min_stop_atr': 1.5} | -0.032 | -10.861 |
| 2026Q3 | {'rank_min': 0.6, 'min_stop_atr': 1.5} | -0.074 | -74.692 |

Default cell, OOS (info): 2785 trades, mean -0.136R, PF 0.726.
Default-cell skips: {'already_open': 843, 'capacity': 7510, 'cooldown': 742, 'correlated': 292, 'daily_loss': 121, 'chased': 2}.

## For information only (not GO criteria)

- BTC buy-and-hold over OOS: 174.4%; equal-weight universe: -79.7%.
- Combined A+B (default cells): 2831 trades, mean -0.144R, return on budget -214.8%.
