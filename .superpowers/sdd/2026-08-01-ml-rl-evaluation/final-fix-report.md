# Final ML/RL review fix report

## Scope

Implemented all eight findings from the final review package while keeping PPO shadow-only by default and avoiding AWS, live exchange, and compose operations.

## Changed files

- `trading-engine-core/src/config.rs` — added backward-compatible `routing.mode` configuration (default `shadow`) with an explicit `live` predicate.
- `trading-engine-core/src/engine.rs` — loads and consumes `routing_cache.json` only in explicit live mode; shadow mode ignores sentinel/stale active cache state; added regression test.
- `src/rl/walk_forward.py` — emits `ppo_model_paths` paired with `ppo_model_sha256`; aggregates realized per-slice PPO/RF fees without subtracting already-net returns twice; publishes gate metrics and regime counts, failing closed when attribution is unavailable.
- `scripts/verify_ml_rl_rollout.py` — recognizes `ppo_model_paths` while resolving artifacts for report verification.
- `scripts/ml_report.py` — validates every shadow record through canonical `shadow_schema.validate_decision`; reports independent-trade counts for each engine/regime slice and marks evidence inconclusive below 100.
- `src/ml/promotion_gate.py` — requires regime-level sample evidence; missing/empty regime attribution is inconclusive.
- `src/ml/regime_pusher.py` — drift reports iterate the union of observed windows and loaded model metadata; validated artifact metadata/checksum is captured at load and reused until explicit reload.
- `tests/test_ml_report_cli.py` — canonical action-map rejection and per-slice evidence tests; corrected legacy fixtures to canonical action mappings.
- `tests/test_ml_drift.py` — no-observation model drift and immutable validated metadata tests.
- `tests/test_ml_promotion_gate.py` — missing regime counts are explicitly inconclusive.
- `tests/test_rl_walk_forward.py` — PPO path/checksum round-trip metadata, realized fee aggregation, and regime attribution gate tests.

## Focused verification

- `python3 -m pytest -q tests/test_ml_report_cli.py tests/test_ml_drift.py tests/test_ml_promotion_gate.py tests/test_rl_walk_forward.py tests/test_ml_rl_rollout.py`
  - Result: **41 passed**.
- `cargo test --lib engine::tests --no-fail-fast`
  - Result: **15 passed**.
- `cargo test --test test_config --no-fail-fast`
  - Result: **4 passed**.
- `python3 -m py_compile src/rl/walk_forward.py scripts/ml_report.py src/ml/promotion_gate.py src/ml/regime_pusher.py scripts/verify_ml_rl_rollout.py`
  - Result: passed with no output.

## Concerns

- Walk-forward PPO/RF return arrays are already net of evaluator execution fees; aggregate metrics therefore expose realized fee totals while passing zero additional fee deduction, avoiding double-counted net returns.
- Legacy shadow journals remain readable for stale-evidence reporting, but every record must satisfy the canonical action/engine/size schema; freshness is reported separately by runtime status.
- A loaded pusher classifier intentionally continues reporting its validated artifact checksum after an on-disk replacement; calling `load_models` is the explicit reload boundary.
- Promotion remains report-only and shadow-only; no automatic routing activation was added.
