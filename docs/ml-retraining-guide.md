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
