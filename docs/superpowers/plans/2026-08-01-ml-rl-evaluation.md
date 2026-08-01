# ML/RL Evaluation, Attribution, and Shadow Routing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make regime ML and PPO measurable through reproducible evaluation, per-trade attribution, drift checks, and paper-only shadow routing without enabling PPO to control live capital.

**Architecture:** Extend the existing Python/Rust pipeline. Python owns model metadata, retraining, walk-forward reports, drift checks, and shadow PPO decisions. Rust accepts optional regime metadata, persists attribution in existing audit context, and keeps active routing isolated from shadow output. Promotion is a report-and-human-review gate, not an automatic code path.

**Tech Stack:** Python, pandas, scikit-learn/XGBoost, Stable-Baselines3 PPO, Rust, serde, Tokio, SQLite, existing Binance kline cache and Rust HTTP API.

## Global Constraints

- PPO remains shadow-only; it MUST NOT write `data/routing_cache.json` or modify active engine routing in this plan.
- Missing, stale, invalid, or metadata-incompatible ML/RL input MUST fail safe to TA/no active routing.
- Walk-forward evaluation MUST keep training strictly before test data and preserve the existing embargo gap.
- Reports MUST include PnL, return, profit factor, max drawdown, exposure/time-in-market, fees/slippage, trade count, and confidence intervals.
- Results with fewer than 100 independent trades per strategy/regime MUST be labeled inconclusive.
- Retraining requires at least 2,160 one-hour candles / 90 days; 4,000+ candles is the target.
- Existing trade fields and old rows MUST remain readable; attribution is additive through existing audit/context storage.
- No automatic online learning from live trades.
- Every task MUST add deterministic tests for its observable contract and skip project-wide validation until the final task.

---

### Task 1: Regime metadata and trade attribution contracts

**Files:**
- Modify: `trading-engine-core/src/strategy/regime_cache.rs`
- Modify: `trading-engine-core/src/api/handlers.rs`
- Modify: `trading-engine-core/src/strategy/trade_journal.rs`
- Modify: `trading-engine-core/src/strategy/trend.rs`
- Modify: `trading-engine-core/src/strategy/grid.rs`
- Modify: `src/ml/regime_pusher.py`
- Test: `tests/test_ml_attribution.py` (create)
- Test: Rust inline tests in the modified modules

**Interfaces:**
- `RegimeEntry` gains optional `model_version: Option<String>`, `artifact_sha256: Option<String>`, and `feature_contract_hash: Option<String>`; serde defaults keep old cache files valid.
- `RegimeUpdate` gains the same optional metadata fields plus an explicit `timestamp: Option<i64>`; Rust uses the supplied timestamp only when positive, otherwise current time.
- `RegimeCache::get_entry(&self, pair: &str) -> Option<RegimeEntry>` returns a TTL-validated clone; existing `get()` continues returning `(i32, f64)` for old callers.
- `src.ml.regime_pusher.compute_regime(...)` continues returning `(regime, confidence)`; `collect_regime(...)` adds metadata to each update payload from the loaded classifier artifact.
- `TradeContext.context_json` becomes a JSON object merged with existing engine-specific keys. Required keys are `regime_at_entry`, `regime_confidence`, `regime_model_version`, `regime_artifact_sha256`, `ml_gate_decision`, `router_mode`, `router_action`, `router_engine`, `router_size_mult`, `decision_timestamp`, and `ml_age_ms` when known.

- [ ] **Step 1: Write failing Rust cache tests**

```rust
#[tokio::test]
async fn regime_metadata_round_trips_and_old_payload_defaults() {
    let cache = RegimeCache::new("target/test-regime-cache.json", 60_000);
    cache.update(&[RegimeUpdate {
        pair: "BNB-USDT".into(), regime: 0, confidence: 0.81,
        timestamp: Some(chrono::Utc::now().timestamp_millis()),
        model_version: Some("rf-bnb-20260801".into()),
        artifact_sha256: Some("abc".into()),
        feature_contract_hash: Some("features-v1".into()),
    }]).await;
    let entry = cache.get_entry("BNB-USDT").await.unwrap();
    assert_eq!(entry.model_version.as_deref(), Some("rf-bnb-20260801"));
    let old: RegimeUpdate = serde_json::from_str(r#"{"pair":"BNB-USDT","regime":0,"confidence":0.5}"#).unwrap();
    assert!(old.model_version.is_none());
}
```

- [ ] **Step 2: Run the focused Rust test and verify it fails**

Run: `cd trading-engine-core && cargo test --lib strategy::regime_cache::regime_metadata_round_trips_and_old_payload_defaults --no-fail-fast`

Expected: FAIL because metadata fields and `get_entry` do not exist.

- [ ] **Step 3: Implement additive cache metadata and API deserialization**

Add `#[serde(default)]` optional fields, preserve existing `get()`, validate positive supplied timestamps, and expose `get_entry()` for attribution. Do not change TTL behavior. Update the API handler to pass the full `RegimeUpdate` through unchanged.

- [ ] **Step 4: Add Python model metadata helpers and payload tests**

Implement deterministic helpers in `src/ml/regime_pusher.py`:

```python
def model_metadata(clf: RegimeClassifier, path: str) -> dict[str, str | None]:
    """Return version, artifact SHA-256, and feature-contract hash."""

def build_regime_update(pair: str, regime: int, confidence: float,
                        metadata: dict[str, str | None], timestamp_ms: int) -> dict:
    """Build the API payload without changing the existing prediction semantics."""
```

Test that the payload contains the required metadata, rejects a missing artifact hash only when the artifact path is missing, and remains compatible with the old `(regime, confidence)` result.

- [ ] **Step 5: Persist entry-time attribution in trend/grid audit context**

At the existing entry-intent points, capture the current regime entry and confidence in the position/order state. On close, merge that snapshot into `TradeContext.context_json` without replacing existing keys. Use `ml_gate_decision="allowed"` for an ML-approved entry, `ta_fallback` when no fresh regime exists, and `ml_unavailable` when an expected cache lookup is stale or malformed.

- [ ] **Step 6: Run focused attribution tests**

Run: `pytest -q tests/test_ml_attribution.py` and `cd trading-engine-core && cargo test --lib strategy::regime_cache strategy::trade_journal --no-fail-fast`

Expected: all new metadata, backward-compatibility, and context round-trip tests pass.

- [ ] **Step 7: Commit**

```bash
git add trading-engine-core/src/strategy/regime_cache.rs trading-engine-core/src/api/handlers.rs trading-engine-core/src/strategy/trade_journal.rs trading-engine-core/src/strategy/trend.rs trading-engine-core/src/strategy/grid.rs src/ml/regime_pusher.py tests/test_ml_attribution.py
git commit -m "feat(ml): persist regime metadata and trade attribution"
```

---

### Task 2: Model artifacts and regime drift monitoring

**Files:**
- Modify: `src/ml/regime_classifier.py`
- Modify: `src/ml/regime_pusher.py`
- Create: `src/ml/model_metadata.py`
- Create: `src/ml/drift.py`
- Create: `tests/test_ml_metadata.py`
- Create: `tests/test_ml_drift.py`
- Modify: `docs/ml-retraining-guide.md`

**Interfaces:**
- `model_metadata.write_metadata(path: str, metadata: dict) -> str` writes adjacent immutable JSON metadata and returns its SHA-256.
- `model_metadata.read_metadata(path: str) -> dict` validates required keys: pair, timeframe, train_start, train_end, feature_contract_hash, label_params, class_distribution, metrics, source_commit, artifact_sha256.
- `drift.compare_distribution(training: dict[int, float], live: dict[int, float]) -> dict` returns per-class deltas and `max_abs_delta`.
- `drift.evaluate_drift(training, live, confidence_24h, age_ms, ttl_ms) -> list[str]` emits stable reason codes: `class_distribution_shift`, `danger_frequency_spike`, `low_confidence`, `stale_cache`, `feature_contract_mismatch`.

- [ ] **Step 1: Write failing metadata/drift tests**

```python
def test_distribution_drift_and_danger_spike():
    result = compare_distribution({0: .6, 1: .3, 2: .1}, {0: .2, 1: .3, 2: .5})
    assert result["max_abs_delta"] == .4
    reasons = evaluate_drift({0: .6, 1: .3, 2: .1}, {0: .2, 1: .3, 2: .5},
                             confidence_24h=.7, age_ms=1_000, ttl_ms=180_000)
    assert "class_distribution_shift" in reasons
    assert "danger_frequency_spike" in reasons
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `pytest -q tests/test_ml_metadata.py tests/test_ml_drift.py`

Expected: FAIL because the metadata and drift modules do not exist.

- [ ] **Step 3: Implement immutable artifact metadata**

Add metadata generation to the existing training save path. The metadata file MUST include source commit when available, otherwise the literal value `unknown` plus a warning; it MUST never claim a fabricated commit. Verify the artifact hash after writing and reject mismatches during `load_models()`.

- [ ] **Step 4: Implement drift reason codes and pusher integration**

Maintain a bounded in-memory 24-hour prediction window per pair in the pusher. Compare the live class distribution to metadata, log structured warnings, and expose a deterministic `collect_drift_report()` for tests and operators. Drift warnings MUST NOT silently disable the model; they trigger retraining/reporting while the existing confidence/TA fallback remains active.

- [ ] **Step 5: Run focused tests**

Run: `pytest -q tests/test_ml_metadata.py tests/test_ml_drift.py tests/test_ml_hot_reload.py`

Expected: all pass, including stale-cache and feature-contract mismatch paths.

- [ ] **Step 6: Commit**

```bash
git add src/ml/model_metadata.py src/ml/drift.py src/ml/regime_classifier.py src/ml/regime_pusher.py tests/test_ml_metadata.py tests/test_ml_drift.py docs/ml-retraining-guide.md
git commit -m "feat(ml): add model manifests and regime drift checks"
```

---

### Task 3: Walk-forward evaluation and promotion gates

**Files:**
- Modify: `src/rl/walk_forward.py`
- Modify: `src/rl/evaluate.py`
- Create: `src/ml/evaluation_report.py`
- Create: `src/ml/promotion_gate.py`
- Create: `tests/test_ml_evaluation_report.py`
- Create: `tests/test_ml_promotion_gate.py`
- Modify: `docs/rl_walk_forward_results.md`

**Interfaces:**
- `evaluation_report.summarize_returns(returns: Sequence[float], exposure: Sequence[float], fees: float) -> dict` returns `trade_count`, `net_pnl`, `total_return`, `profit_factor`, `max_drawdown`, `time_in_market`, and 95% bootstrap confidence intervals.
- `evaluation_report.write_report(path: str, metadata: dict, metrics: dict, slices: list[dict]) -> None` writes deterministic JSON sorted by keys.
- `promotion_gate.evaluate(metrics: dict, min_trades: int = 100) -> dict` returns `{eligible: bool, reasons: list[str]}` and marks insufficient samples `inconclusive`.
- `walk_forward.run_walk_forward(...)` retains existing return compatibility and adds TA and ML-gate result series plus report output.

- [ ] **Step 1: Write failing metric and gate tests**

```python
def test_small_sample_is_inconclusive():
    result = evaluate({"trade_count": 12, "profit_factor": 2.0,
                       "ppo_max_drawdown": .05, "rf_max_drawdown": .10})
    assert result["eligible"] is False
    assert "inconclusive_sample" in result["reasons"]

def test_drawdown_parity_can_qualify_ppo():
    result = evaluate({"trade_count": 140, "ppo_profit_factor": 1.2,
                       "rf_profit_factor": 1.2, "ppo_max_drawdown": .05,
                       "rf_max_drawdown": .10, "ppo_exposure": .52,
                       "rf_exposure": .74})
    assert result["eligible"] is True
```

- [ ] **Step 2: Run focused tests and verify failure**

Run: `pytest -q tests/test_ml_evaluation_report.py tests/test_ml_promotion_gate.py`

Expected: FAIL because the report and gate modules do not exist.

- [ ] **Step 3: Implement metrics and deterministic reports**

Use the existing walk-forward slices and returns. Compute net PnL after configured fees/slippage, profit factor with a zero-loss guard, max drawdown from cumulative equity, exposure/time-in-market, and bootstrap confidence intervals with a fixed seed. Keep per-window rows so a single favorable slice cannot hide failures.

- [ ] **Step 4: Implement promotion rules**

Require at least 100 independent trades per strategy/regime, multiple windows, no unacceptable drawdown/exposure increase, and either improved risk-adjusted performance or return parity with materially lower drawdown/exposure. Include a `human_review_required` reason whenever the candidate is otherwise eligible.

- [ ] **Step 5: Add TA-vs-ML gate and PPO-vs-RF outputs to walk-forward CLI**

The CLI MUST emit JSON suitable for CI and a concise text summary suitable for operators. It MUST include source commit, model checksums, date windows, feature hash, fees/slippage, and the promotion result.

- [ ] **Step 6: Run focused evaluator tests**

Run: `pytest -q tests/test_ml_evaluation_report.py tests/test_ml_promotion_gate.py tests/test_rl_walk_forward.py tests/test_rl_evaluate.py`

Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add src/rl/walk_forward.py src/rl/evaluate.py src/ml/evaluation_report.py src/ml/promotion_gate.py tests/test_ml_evaluation_report.py tests/test_ml_promotion_gate.py docs/rl_walk_forward_results.md
git commit -m "feat(ml): add walk-forward reports and promotion gates"
```

---

### Task 4: Shadow PPO router and isolated decision journal

**Files:**
- Modify: `src/rl/live_router.py`
- Modify: `src/rl/router.py`
- Create: `src/rl/shadow_journal.py`
- Create: `src/rl/shadow_schema.py`
- Create: `tests/test_rl_shadow_router.py`
- Modify: `config/strategy.yaml`
- Modify: `docker-compose.yml`

**Interfaces:**
- `shadow_schema.ShadowRoutingDecision` is a dataclass with `timestamp_ms`, `pair`, `action`, `engine`, `size_mult`, `model_version`, `model_sha256`, `observation_age_ms`, and `mode="shadow"`.
- `shadow_schema.validate_decision(decision: dict, now_ms: int, max_age_ms: int) -> tuple[bool, str]` rejects unknown engines, actions outside `0..9`, size multipliers outside the action map, stale observations, and non-shadow modes.
- `shadow_journal.log_decision(path: str, decision: ShadowRoutingDecision) -> None` appends JSONL atomically to `data/shadow_routing.jsonl`.
- `live_router.run_loop(..., shadow: bool = True)` computes actions as before but calls the shadow journal instead of the active routing POST when `shadow=True`.

- [ ] **Step 1: Write failing shadow isolation tests**

```python
def test_shadow_router_never_posts_active_routing(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(requests, "post", lambda *a, **k: calls.append(a[0]))
    decision = decode_action(0)
    assert decision["engine"] in {"grid", "trend", "swing", "mean_reversion"}
    log_decision(tmp_path / "shadow.jsonl", ShadowRoutingDecision.from_action("ETH-USDT", 0, decision, "m"))
    assert calls == []
```

- [ ] **Step 2: Run the focused test and verify failure**

Run: `pytest -q tests/test_rl_shadow_router.py`

Expected: FAIL because the shadow schema/journal and isolated loop do not exist.

- [ ] **Step 3: Implement validated shadow schema and journal**

Use the existing `ACTION_TO_ENGINE_SIZE` map as the only source of valid action/size values. Write one JSON object per line using a temp file plus `os.replace` for snapshots, and never touch `data/routing_cache.json` in shadow mode.

- [ ] **Step 4: Add shadow mode to `live_router.py`**

Preserve the existing live-post function behind `shadow=False`; default CLI behavior MUST be `--shadow`. Include model checksum/version and observation age in every record. A request or model failure logs a shadow error and continues without active routing.

- [ ] **Step 5: Add explicit configuration and compose service**

Add:

```yaml
routing:
  enabled: true
  mode: shadow
  shadow_path: data/shadow_routing.jsonl
  ppo_model: models/rl/ppo_ETHUSDT_2026-07-05_clean-oos-24m.zip
```

Add a `trading-rl-shadow` compose service using the existing Python image/environment, `restart: unless-stopped`, and read/write mounts for `data` and `models`. The service MUST not receive a credential or endpoint that enables active routing; it may read the Rust API for klines/equity.

- [ ] **Step 6: Run focused shadow tests**

Run: `pytest -q tests/test_rl_shadow_router.py tests/test_rl_live_router.py tests/test_rl_features.py`

Expected: all pass and no test creates/updates active routing state.

- [ ] **Step 7: Commit**

```bash
git add src/rl/live_router.py src/rl/router.py src/rl/shadow_journal.py src/rl/shadow_schema.py tests/test_rl_shadow_router.py config/strategy.yaml docker-compose.yml
git commit -m "feat(rl): add isolated PPO shadow routing"
```

---

### Task 5: Runtime attribution and operator reports

**Files:**
- Modify: `trading-engine-core/src/engine.rs`
- Modify: `trading-engine-core/src/strategy/trade_journal.rs`
- Modify: `trading-engine-core/src/strategy/trend.rs`
- Modify: `trading-engine-core/src/strategy/grid.rs`
- Modify: `src/notifications/telegram_commands.py`
- Create: `scripts/ml_report.py`
- Create: `tests/test_ml_report_cli.py`
- Modify: `docs/ml-retraining-guide.md`

**Interfaces:**
- Engine obtains `RegimeEntry` through `RegimeCache::get_entry()` and passes a serializable attribution snapshot into strategy entry context.
- `scripts/ml_report.py --db PATH --since ISO --out PATH` reads the unified journal and shadow JSONL, produces the same report schema as Task 3, and exits nonzero when the report is malformed—not merely when it is inconclusive.
- Telegram `/readiness` or the existing ML status command reports cache age, model version, drift reasons, shadow decision age, and whether PPO is active (`false` in this phase).

- [ ] **Step 1: Write failing runtime report tests**

```python
def test_report_marks_missing_attribution_and_shadow_only(tmp_path):
    report = build_report(db_path=tmp_path / "trades.db", shadow_path=tmp_path / "shadow.jsonl")
    assert report["ppo_active"] is False
    assert report["attribution_missing_count"] == 0
    assert report["shadow_decisions"] == 0
```

- [ ] **Step 2: Implement CLI aggregation**

Join trade context by pair and entry/decision timestamp, count missing metadata explicitly, and calculate per-engine/per-regime metrics. Never infer a regime from a later cache value when entry attribution is absent; record it as missing.

- [ ] **Step 3: Add operator status output**

Expose cache/model/shadow health through the existing command surface. The output MUST distinguish `live`, `shadow`, `stale`, `missing`, and `inconclusive`; it MUST not call PPO active merely because a model file exists.

- [ ] **Step 4: Run focused report tests**

Run: `pytest -q tests/test_ml_report_cli.py tests/test_telegram_bot.py`

Expected: all new report tests pass; existing Telegram tests may require the repository's documented optional dependency setup.

- [ ] **Step 5: Commit**

```bash
git add trading-engine-core/src/engine.rs trading-engine-core/src/strategy/trade_journal.rs trading-engine-core/src/strategy/trend.rs trading-engine-core/src/strategy/grid.rs src/notifications/telegram_commands.py scripts/ml_report.py tests/test_ml_report_cli.py docs/ml-retraining-guide.md
git commit -m "feat(ml): add runtime attribution and operator reports"
```

---

### Task 6: End-to-end verification and paper rollout

**Files:**
- Modify: `docs/ml-retraining-guide.md`
- Modify: `docs/rl_walk_forward_results.md`
- Create: `scripts/verify_ml_rl_rollout.py`
- Test: `tests/test_ml_rl_rollout.py`

**Interfaces:**
- `verify_ml_rl_rollout(repo_root: str, report_path: str, shadow_path: str) -> dict` returns `{ok, failures, warnings, ppo_active, model_checksums, attribution_coverage}`.
- The verifier MUST fail if PPO is active, active routing cache changes during shadow mode, metadata checksums mismatch, or shadow records are stale/invalid.

- [ ] **Step 1: Write failing rollout verifier tests**

```python
def test_rollout_verifier_rejects_active_ppo(tmp_path):
    result = verify_ml_rl_rollout(str(tmp_path), str(tmp_path / "report.json"), str(tmp_path / "shadow.jsonl"))
    assert result["ok"] is False
    assert "ppo_active" in result["failures"]
```

- [ ] **Step 2: Implement deterministic rollout verification**

Verify model manifests, feature hashes, report gates, shadow-only mode, cache freshness, and attribution coverage. Emit JSON and a concise exit-status summary.

- [ ] **Step 3: Run the full relevant suites**

Run:

```bash
pytest -q tests/test_ml_attribution.py tests/test_ml_metadata.py tests/test_ml_drift.py tests/test_ml_evaluation_report.py tests/test_ml_promotion_gate.py tests/test_rl_shadow_router.py tests/test_rl_walk_forward.py tests/test_rl_evaluate.py tests/test_ml_rl_rollout.py
cd trading-engine-core && cargo test --workspace --no-fail-fast
```

Expected: all focused Python tests and all Rust tests pass. Existing missing optional Python dependencies are reported separately and do not get masked.

- [ ] **Step 4: Run offline walk-forward evaluation**

Run the evaluator on cached data for every enabled model pair. Store report JSON with model checksums, source commit, date windows, and promotion result. Do not modify active routing.

- [ ] **Step 5: Start the shadow service only**

Run the compose shadow service with `mode=shadow`. Verify it writes `data/shadow_routing.jsonl`, leaves `data/routing_cache.json` absent/unchanged, and produces fresh valid decisions for at least one full bar interval.

- [ ] **Step 6: Review the report and rollout verifier output**

Record whether the current evidence is `eligible`, `inconclusive`, or `rejected`. PPO remains shadow-only unless a separate human-approved change explicitly promotes it.

- [ ] **Step 7: Commit verification documentation**

```bash
git add docs/ml-retraining-guide.md docs/rl_walk_forward_results.md scripts/verify_ml_rl_rollout.py tests/test_ml_rl_rollout.py
git commit -m "chore(ml): verify shadow rollout and promotion gates"
```
