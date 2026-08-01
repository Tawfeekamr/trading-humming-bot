# ML/RL Evaluation, Attribution, and Shadow-Routing Design

**Date:** 2026-08-01  
**Status:** Approved design; implementation plan pending user review

## Goal

Make the existing regime-ML and PPO/RL systems measurable and safe to improve. The regime classifier remains live inference; PPO remains shadow-only until reproducible out-of-sample and paper evidence satisfies explicit promotion gates.

## Current context

- Python `src/ml/regime_pusher.py` loads per-pair models, computes 14-feature predictions, and pushes them to Rust.
- Rust `RegimeCache` feeds grid/trend entry gates.
- Python `src/rl/walk_forward.py` already compares PPO and supervised RF on chronological OOS slices.
- Python `src/rl/live_router.py` exists, but the deployed instance currently has no live router process and no `routing_cache.json`.
- The trade journal does not consistently persist regime/model attribution for trend trades.

## Scope and non-goals

### In scope

1. Reproducible model metadata and retraining/evaluation artifacts.
2. Walk-forward comparison of TA, ML-gated, clean RF, and PPO policies.
3. Per-trade regime, model, gate, and shadow-router attribution.
4. Live regime drift and stale-cache monitoring.
5. PPO paper/shadow routing with strict validation and no live-capital effect.
6. Promotion reports and explicit gates for any future live activation.

### Out of scope

- Automatic live PPO activation.
- Uncontrolled online learning from live trades.
- Replacing the existing Rust strategy execution logic.
- Optimizing thresholds against the same OOS window used for reporting.

## Architecture

Reuse the existing Python/Rust pipeline rather than adding a separate analytics service.

### Offline pipeline

1. Download/cache Binance 1h bars through `src.rl.data`.
2. Train per-pair regime models with chronological splits and embargo gaps.
3. Record model metadata: pair, timeframe, training interval, feature-contract hash, label parameters, class distribution, metrics, source commit, and artifact checksum.
4. Run walk-forward evaluations for TA baseline, ML gate, clean RF, and PPO.
5. Produce a machine-readable report and a human-readable summary.

### Live regime inference

- `regime_pusher` remains the inference source.
- Each pushed update includes model version, artifact checksum, feature-contract hash, and prediction timestamp.
- Rust stores the metadata with the current regime cache and uses the existing TA fallback when data is missing or stale.
- New entries record the regime/gate decision; open-position management is unaffected by stale ML input.

### Shadow PPO routing

- `live_router.py` may run in shadow mode and compute the same action it would have used for live routing.
- Shadow output is written to `data/shadow_routing_cache.json` and a shadow decision journal; it is never written to the active `data/routing_cache.json`.
- Every shadow decision carries action, decoded engine, size multiplier, model version, observation timestamp, and freshness status.
- The active `RoutingCache` remains empty/unchanged during this phase.

### Runtime contracts

- Extend `RegimeUpdate` and its API payload with optional metadata fields so old pusher payloads remain valid.
- Extend `RoutingEntry` only for validated active decisions; shadow records use a separate schema and file.
- Persist attribution in the existing trade `context_json`/audit context so old rows remain readable and no destructive migration is required.

## Attribution schema

Extend the trade/audit context without deleting existing fields:

- `regime_at_entry`: Ranging, Trending, Danger, or null.
- `regime_confidence`.
- `regime_model_version` and artifact checksum.
- `ml_gate_decision`: `allowed`, `blocked`, `ta_fallback`, `ml_unavailable`, or `not_applicable`.
- `router_action`, `router_engine`, and `router_size_mult` when a shadow decision exists.
- `router_mode`: `none`, `shadow`, or `live`.
- `decision_timestamp` and cache/model age.

The original strategy, pair, entry, exit, PnL, and audit context remain authoritative.

## Retraining and drift policy

- Scheduled retraining: every two weeks.
- Retrain immediately for a new pair, confidence below 0.55 for 24 hours, or OOS accuracy down more than 10% from baseline.
- Minimum training data: 2,160 one-hour candles / 90 days; target 4,000+ candles.
- Drift alerts:
  - any regime-class distribution changes by more than 20% from training;
  - Danger frequency exceeds 3x baseline;
  - stale or missing cache/model metadata;
  - feature-contract mismatch.

## Evaluation and promotion gates

Each report must include trade count, return, net PnL, profit factor, max drawdown, exposure/time-in-market, fees/slippage, and confidence intervals across walk-forward windows.

The initial evidence target is at least 100 independent trades per strategy/regime and coverage of multiple market regimes. Below that threshold, the result is explicitly inconclusive.

PPO can leave shadow-only mode only after:

1. Multiple chronological OOS windows complete.
2. PPO matches or exceeds clean RF on risk-adjusted metrics, not just raw return.
3. No unacceptable drawdown, exposure, or fee increase is observed.
4. Paper/shadow decisions are fresh, valid, and error-free.
5. A human reviews the report and explicitly enables live routing in a separate change.

## Runtime safety

- Invalid actions, unknown engines, out-of-range size multipliers, stale timestamps, missing model metadata, or stale cache entries produce no active routing decision.
- Shadow failures never block the trading loop and never create live orders.
- Regime inference failures fall back to TA and are attributed as `ml_unavailable`.
- Artifacts and reports are reproducible from cached bars, config, model checksum, and source commit.

## Verification

Tests must cover:

- attribution serialization and round trips;
- model metadata and feature-contract validation;
- chronological split and embargo boundaries;
- drift thresholds;
- stale/missing regime behavior;
- shadow/live isolation;
- invalid action and routing TTL rejection;
- promotion-gate failures;
- report reproducibility.

No live PPO routing test may place an order or modify active routing state.
