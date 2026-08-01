# ML Regime Model Retraining Guide

## When to Retrain

| Trigger | Action |
|---------|--------|
| **Scheduled** | Retrain every 2 weeks (crypto regimes drift fast) |
| **Regime shift detected** | If model confidence drops below 0.55 for 24h+, retrain with latest data |
| **New pair added** | Train a new per-pair model before enabling the pair |
| **Accuracy degradation** | If out-of-sample accuracy drops >10% from baseline, retrain |

## Training Commands

```bash
# Standard retrain (1h, at least 90 days / 2,160 candles)
python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 1h --candles 2160

# Recommended retrain (1h, 4,000+ candles)
python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 1h --candles 4000

# Quick validation (must still use the 90-day minimum)
python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 1h --candles 2160
```

## Data Requirements

| Timeframe | Min Candles | Min Days | Recommended Candles |
|-----------|-------------|----------|---------------------|
| 15m       | 5,760       | 60       | 8,000+              |
| 1h        | 2,160       | 90       | 4,000+              |
| 4h        | 720         | 120      | 2,000+              |
| 1d        | 180         | 180      | 500+                |

## Drift Detection

The pusher retains a bounded 24-hour prediction window per pair and compares
the live class distribution with the immutable `*.pkl.metadata.json` manifest.
It emits deterministic reason codes for operator/retraining reports:

- `class_distribution_shift` — a class moved by more than 20 percentage points
- `danger_frequency_spike` — DANGER frequency exceeded 3x its training rate
- `low_confidence` — 24-hour mean confidence is below 0.55
- `stale_cache` — the regime cache is older than its 180-second TTL
- `feature_contract_mismatch` — live and manifest feature hashes differ

Warnings request retraining/reporting; they do not disable model inference.
Existing confidence and TA fallback behavior remains active.

## Deployment Checklist

- [ ] Train model and review accuracy, confusion matrix, DANGER recall
- [ ] Verify at least 2,160 one-hour candles (90 days); prefer 4,000+
- [ ] Verify embargo gap is active (check "Embargo gap: N samples" in training output)
- [ ] Review feature importances — if all flat (0.04-0.10), model may be weak
- [ ] Confirm the adjacent `.pkl.metadata.json` manifest has the real source commit
- [ ] Verify the manifest `artifact_sha256` matches the `.pkl` before deployment
- [ ] Copy `.pkl.new` → `.pkl` together with its immutable manifest
- [ ] Restart Python container to pick up new model
- [ ] Monitor `/system` Telegram command for regime changes for 30 minutes

## Runtime Attribution Report

Generate a deterministic report from the unified trade journal and the isolated
PPO shadow decision journal:

```bash
python scripts/ml_report.py \
  --db data/trades.db \
  --shadow data/shadow_routing.jsonl \
  --since 2026-08-01T00:00:00Z \
  --out data/ml_runtime_report.json
```

The report keeps the Task 3 `metadata`/`metrics`/`slices` shape and adds runtime
coverage fields. `attribution_missing_count` counts old trades with no
entry-time attribution; a later cache value is never used to fill that gap.
`ppo_active` is always `false` while routing is shadow-only. Malformed SQLite
rows, attribution objects, or shadow JSONL cause a non-zero exit; an
`inconclusive` evidence state is a valid report and is not an input error.

The Telegram `/readiness` response includes cache age, model version, drift
reason codes, shadow decision age, evidence state, and the explicit PPO active
flag. `/ml_status` returns the same operator block. Runtime state is reported
as `live`, `shadow`, `stale`, `missing`, or `inconclusive`; model files alone
never imply active PPO routing.


## Paper-only rollout verification

PPO is a paper/shadow observer in this phase. A model file, an eligible
walk-forward report, or a running sidecar never activates PPO. Do not provide
the shadow container with exchange credentials or an active-routing endpoint.
Run the following checks offline against cached artifacts and journals:

```bash
# Run the cached walk-forward evaluator for every enabled pair. This writes
# reports/rl_walk_forward_<PAIR>.json and never writes data/routing_cache.json.
python -m src.rl.walk_forward --pairs ETHUSDT BNBUSDT

# Verify manifests, feature-contract hashes, report gates, cache freshness,
# attribution coverage, and every shadow decision.
python scripts/verify_ml_rl_rollout.py \
  --repo-root . \
  --report reports/rl_walk_forward_ETHUSDT.json \
  --shadow data/shadow_routing.jsonl \
  --out reports/ml_rl_rollout_verification.json
```

The verifier emits machine-readable JSON and a concise exit summary. It
rejects active PPO, a non-`shadow` routing mode, an active routing-cache
change, missing or mismatched model-manifest checksums, feature-contract hash
mismatches, stale cache/shadow observations, invalid shadow schema records,
and malformed or rejected promotion gates. An inconclusive sample-size gate
is reported as a warning and remains ineligible for promotion.

Before and after a shadow run, record the SHA-256 of
`data/routing_cache.json` when that file exists; pass the baseline checksum in
the report metadata as `routing_cache_sha256`. The shadow service may append
only to `data/shadow_routing.jsonl`. Confirm the active cache is absent or
byte-for-byte unchanged and that the journal contains fresh, schema-valid
records for at least one complete bar interval. No AWS, exchange, or live
routing operation is part of verification.

Review `promotion.eligible`, all reasons, model checksums, date windows,
source commit, feature hash, and attribution coverage. Record the evidence as
`eligible` only when the report gate and verifier pass; use `inconclusive` for
insufficient independent trades or missing paper evidence, and `rejected` for
any verifier failure. PPO remains shadow-only until a separate,
human-approved configuration and code change explicitly promotes it.