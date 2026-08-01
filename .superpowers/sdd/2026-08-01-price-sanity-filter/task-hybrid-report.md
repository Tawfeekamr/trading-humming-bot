# Hybrid trend-exit implementation

Implemented the agreed hybrid trend exit model.

- New fills create exactly two targets: TP1 at 1R closing 33% of current remaining quantity, and TP2 at 1.5R closing 50% of current remaining quantity.
- Remaining quantity is managed by the existing breakeven-at-+1R and Chandelier trailing logic; no fixed TP3 is generated or logged.
- Persisted positions with an empty `tp_levels` ladder are backfilled on load. Restore reconciliation marks already-reached targets filled and skips exit-order emission for that first tick.
- Corrected trend YAML comments and removed the stale `runner_pct` setting.

Focused verification:

- `cargo test --test test_trend_exits` — 8 passed
- `cargo test --test test_trend_strategy` — 4 passed
- `cargo test trend::tests` — 38 passed

Concern: `risk_reward_ratio` and the historical `calculate_tp_levels` arguments remain for configuration/API compatibility, but are intentionally ignored by the fixed hybrid target ladder.
