# Hybrid trend-exit implementation

Implemented the agreed hybrid trend exit model.

- New fills create exactly two targets: TP1 at 1R closing 33% of current remaining quantity, and TP2 at 1.5R closing 50% of current remaining quantity.
- Remaining quantity is managed by the existing breakeven-at-+1R and Chandelier trailing logic; no fixed TP3 is generated or logged.
- Persisted positions with an empty `tp_levels` ladder are backfilled on load. New state persists the original per-unit risk so targets remain correct after breakeven promotion. Legacy states use their original stop distance, then a non-zero trail/fallback estimate when that history is unavailable.
- Every restored ladder is normalized to exactly the two hybrid targets; only TP1/TP2 filled flags are retained, and any legacy TP3 is discarded. Restore reconciliation marks already-reached targets filled and skips exit-order emission for that first tick.
- Reactive exit quantities are persisted in the trend state. Matching reduce-only fills consume the pending booked quantity instead of deducting it a second time, including across restart; unbooked external/manual reduce fills still reconcile normally.
- Corrected trend YAML comments and removed the stale `runner_pct` setting.

Focused verification:

- `cargo test --test test_trend_exits` — 10 passed
- `cargo test --test test_trend_strategy` — 4 passed
- `cargo test trend::tests` — 38 passed

Concern: `risk_reward_ratio` and the historical `calculate_tp_levels` arguments remain for config/API compatibility but are intentionally ignored by the fixed hybrid ladder.
