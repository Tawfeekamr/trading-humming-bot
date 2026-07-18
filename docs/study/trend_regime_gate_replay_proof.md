# Trend Regime Gate — Replay Proof & Findings

**Date:** 2026-07-19
**Branch:** `fix/trend-regime-gate`
**Question:** Would wiring the ML regime gate into the trend engine have prevented the Jul 4–14 ETH whipsaw loss (−$1,099) before going to real money?

## TL;DR (honest verdict)

**The gate is correctly built and mechanically works** (it blocks trend entries in Ranging/Danger periods, cutting the trade count, verified by unit tests + replay). **But it does not improve profitability at any threshold** with the current ETH regime model — it slightly *hurts* returns while reducing max drawdown. The root issue is upstream of the gate: **the ETH regime classifier's `predict_proba` is near-uniform** (all classes ~0.34–0.37, barely above the 0.33 random baseline), so its labels neither carry usable confidence nor predict where trend actually loses money.

**Enabling real money today would still lose on trend/ETH.** The fix that was wired is necessary infrastructure but not sufficient — the regime model itself needs to improve before the gate protects P&L.

## What was built

1. **Gate (`trend.rs` + `config.rs` + `strategy.yaml`)** — `regime_gate` + `min_regime_confidence` config fields; trend entry is blocked when `regime ∈ {Ranging, Danger}` at confidence ≥ threshold. Entries only — open positions keep being managed. 7 TDD unit tests.
2. **Regime-aware replay (`replay.rs` + `backtest_replay.rs`)** — `RegimeTimeline` injects per-bar regime labels via `--regime-file`; CLI gains `--start`/`--end`. Closes the known "regime=None optimistic" replay gap. 3 unit tests.
3. **Label backfill (`src/ml/regime_labels_backfill.py`)** — reuses the live `regime_pusher` pipeline verbatim (zero train/serve skew) to produce per-bar regime labels from Binance 1h bars.

## Two bugs found and fixed during the proof

- **Replay state leakage (pre-existing):** `TrendStrategy` persists its position to `data/{pair}_trend_position.json` (CWD-relative), which is **not** isolated by the harness `TempDir` (unlike grid). Consecutive replay runs contaminated each other (44 → 85 phantom trades). *Workaround applied:* `rm data/ETHUSDT_trend_position.json` before each run. Proper fix (give trend a `state_dir` like grid) is out of scope here — flagged for follow-up.
- **μs/ms timestamp mismatch:** Binance's 2026+ `data.binance.vision` daily kline CSVs emit `open_time` in **microseconds**, while the REST API (used by the label backfill) emits **milliseconds**. The replay's `RegimeTimeline::get` compared them directly, so every bar resolved to the single most-recent label. *Fixed:* `to_ms()` normalizes both sides (with a unit test). Note: the **live** engine is unaffected — its `RegimeCache::get(pair)` is keyed by pair (latest fresh label), not by bar timestamp.

## Labels: the classifier knows it's Ranging, but isn't confident

Backfilled ETH-USDT regime labels:

| Window | Ranging | Trending | Danger | Confidence (median / max) |
|---|---|---|---|---|
| Jul 4–14 (the live loss window) | 240 (91%) | 20 (8%) | 4 (1%) | 0.37 / 0.52 |
| May 15–Jul 15 (replay trade window) | 1048 (72%) | 139 (9%) | 277 (19%) | 0.38 / 0.61 |

The classifier correctly identifies the market as overwhelmingly Ranging — so the gate's *direction* is right. But the confidence distributions of all three classes overlap almost completely (Ranging 0.34–0.52, Trending 0.35–0.37, Danger 0.34–0.35). **Confidence cannot discriminate Ranging from Trending.** At a 0.55 threshold the gate blocks 0% of bars; only at ~0.0 (trust the argmax label, ignore confidence) does it fire.

## Replay proof — threshold sweep (ETH, May 15–Jul 15, 1h bars)

This is the window where the replay trend engine actually produces trades. Both gated and ungated runs are identical clean sims; the delta isolates the gate.

| Config | trades | return % | Sharpe | max DD % | win % |
|---|:---:|:---:|:---:|:---:|:---:|
| **ungated (baseline)** | 44 | **−0.36** | −1.02 | 1.31 | 34.1 |
| gate thr=0.55 | 45 | −0.44 | −1.26 | 1.31 | 33.3 |
| gate thr=0.40 | 40 | −0.59 | −1.78 | 1.35 | 30.0 |
| gate thr=0.35 | 26 | −0.73 | −3.03 | 1.03 | 26.9 |
| gate thr=0.0 | 25 | −0.64 | −2.66 | **0.94** | 28.0 |

(HODL for the window was −16.3%.)

**Reading:** Gating never beats ungated on return. The more aggressively the gate blocks "Ranging" entries, the *worse* the return — because some of the entries it removes were profitable. The one thing gating improves is **max drawdown** (1.31% → 0.94%, −28% at thr=0.0): it is risk control, not edge.

## Why the exact live loss window couldn't be tested directly

Over **Jul 4–14 itself, the replay trend engine produces 0 trades** (whereas live made 22). The replay starts cold and its 220-bar warmup + EMA-200 trend filter + ADX-25 entry gate don't reproduce the entries live took in that choppy window. This is a replay-fidelity gap, not a gate problem. The May 15–Jul 15 window above is the closest window in which the gate could be exercised against real replay trades.

## Conclusion & recommendation

- **The gate infrastructure is sound and stays in the codebase** (gate + regime-aware replay + backfill script, all tested). It's ready to protect trend the moment the regime model is good enough.
- **The current ETH regime model is not good enough.** Its near-uniform confidence means the gate can't be selective, and gating on the raw label hurts returns. Improving the regime model (better features, calibration, or a confidence threshold that actually means something) is the prerequisite to the gate helping P&L.
- **For real money:** the trend engine on ETH is still negative-edge ungated, and the gate doesn't fix that. Do not enable real money on trend/ETH on the basis of this gate. The gate at a low threshold can serve as **drawdown control** if that trade-off (lower DD, slightly worse return) is acceptable — but that is a risk-preference decision, not a profitability one.
- **Config shipped state:** `regime_gate: true`, `min_regime_confidence: 0.55`. At 0.55 the gate is currently near-inert (model confidences < 0.55), so it is effectively a no-op safety rail until either the threshold is deliberately lowered (drawdown control) or the model is improved.

## Follow-ups

1. Improve the ETH regime classifier (features/calibration) so confidence is informative.
2. Give `TrendStrategy` a `state_dir` (like grid) so the replay stops leaking `data/{pair}_trend_position.json`.
3. Decide RL's fate separately (not deployed; no proven edge).
4. Decide whether trend SHORT entries should exist on the spot engine (separate paper-fiction issue).
