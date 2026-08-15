# src/rl/walk_forward.py
"""Walk-forward multi-window OOS harness for the RL execution pipeline.

The single-pair/single-window ``evaluate.py`` answers "does PPO beat the
baseline on *this* month?" — statistically thin (DM on one ~800-bar window).
This module rolls train/test splits across the full available history so the
OOS evidence spans many windows, then pools per-bar returns into a single
HAC-robust DM test with real statistical power.

Design:
    * **Pure helpers** (``walk_forward_slices``, ``pool_returns``,
      ``aggregate_dm``) are numpy-only and unit-tested without the RL stack.
    * **Orchestration** (``main``) trains a PPO model per train-window
      (subprocess to ``ppo_trainer``) and evaluates the matching OOS slice via
      ``evaluate._run_model``, then aggregates. Subprocess isolation keeps each
      training run in a fresh process (cleaner memory + reproducible).

Train strictly precedes test in every slice (the per-model OOS-boundary guard
in ``evaluate.py`` enforces this a second time at eval time).
"""
from __future__ import annotations

from typing import Sequence

import numpy as np


# Default train/test embargo, in bars. Sized to the maximum feature lookback
# in the canonical 14-feature contract (src/data/feature_contract.py) so no
# test-window feature can be computed from any bar inside the training window:
#   - largest explicit rolling windows: sma_50 / vwap(50) / OBV z-score(50) = 50 bars
#   - MACD(12,26,9) = 34; Aroon(25) = 25; fractal dimension = 30; ADX(14) ≈ 27
#   - RSI uses Wilder EWM (alpha=1/14, adjust=False): infinite support with
#     weight decaying at (13/14)^k — oldest-bar weight < 1% after ~64 bars.
# 70 covers every finite window with margin and bounds the EWM tail < 0.6%.
DEFAULT_EMBARGO_BARS = 70


def walk_forward_slices(
    series_len: int,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    embargo_bars: int = DEFAULT_EMBARGO_BARS,
) -> list[tuple[int, int, int, int]]:
    """Rolling chronological splits with an optional train/test embargo gap."""
    if train_bars <= 0 or test_bars <= 0 or step_bars <= 0 or embargo_bars < 0:
        raise ValueError("window sizes must be positive and embargo non-negative")

    slices: list[tuple[int, int, int, int]] = []
    start = 0
    while start + train_bars + embargo_bars + test_bars <= series_len:
        train_end = start + train_bars
        test_start = train_end + embargo_bars
        test_end = test_start + test_bars
        slices.append((start, train_end, test_start, test_end))
        start += step_bars
    return slices


def strict_training_end_date(index, boundary_index: int):
    """Return an inclusive trainer date strictly before a boundary timestamp."""
    from datetime import timedelta

    if boundary_index <= 0 or boundary_index >= len(index):
        raise ValueError("boundary_index must identify a non-initial timestamp")
    boundary = index[boundary_index]
    boundary_date = boundary.date() if hasattr(boundary, "date") else boundary
    return boundary_date - timedelta(days=1)


def pool_returns(
    slice_returns_a: Sequence[np.ndarray],
    slice_returns_b: Sequence[np.ndarray],
) -> tuple[np.ndarray, np.ndarray]:
    """Concatenate per-slice return arrays into two flat, length-matched arrays.

    Each input is a list of 1-D arrays (one per walk-forward slice). Outputs
    are truncated to the shorter length so the DM test gets aligned series.
    """
    pa = (
        np.concatenate(slice_returns_a)
        if len(slice_returns_a)
        else np.array([], dtype=np.float64)
    )
    pb = (
        np.concatenate(slice_returns_b)
        if len(slice_returns_b)
        else np.array([], dtype=np.float64)
    )
    n = min(len(pa), len(pb))
    return pa[:n], pb[:n]


def aggregate_dm(
    ppo_slice_returns: Sequence[np.ndarray],
    rf_slice_returns: Sequence[np.ndarray],
) -> tuple[float, float, int]:
    """Pooled HAC-robust DM test across all walk-forward slices.

    Returns ``(stat, p_value, n)`` where ``n`` is the pooled bar count. A
    positive stat means PPO outperforms RF on the pooled OOS returns.
    """
    from src.rl.evaluate import _diebold_mariano_test

    ppo_all, rf_all = pool_returns(ppo_slice_returns, rf_slice_returns)
    n = len(ppo_all)
    if n == 0:
        return 0.0, 1.0, 0
    stat, p = _diebold_mariano_test(ppo_all, rf_all)
    return stat, p, n


def train_fold_rf(
    pair: str,
    df,
    fold_train_start: int,
    fold_train_end: int,
    *,
    out_dir: str = "models/rl",
    seed: int = 42,
) -> dict:
    """Train one RF regime classifier on a single fold's training window.

    Fixes audit defect #3: previously ONE clean RF artifact (trained over a
    long window) was reused for every cached PPO slice, so early test windows
    were scored by a model that had seen later periods. This trains strictly
    on ``df.iloc[fold_train_start:fold_train_end]`` with the same recipe as
    ``src.ml.train_regime`` (RF n=200, depth-full, min_samples_leaf=5,
    isotonic calibration on the temporal tail).

    Emits a provenance manifest matching the PPO sidecar format
    (train window, data hash, feature-contract hash, source commit, seed,
    timestamp) next to the artifact.
    """
    import hashlib
    import json
    import subprocess
    from datetime import date, datetime, timezone
    from pathlib import Path

    from src.data.feature_contract import FEATURE_SCHEMA_VERSION, MARKET_FEATURE_COLS
    from src.data.feature_engineering import calculate_technical_features
    from src.data.label_generation import generate_regime_labels_nowcast
    from src.ml.regime_classifier import RegimeClassifier

    fold_df = df.iloc[fold_train_start:fold_train_end]
    feats = calculate_technical_features(fold_df.copy())
    labeled = generate_regime_labels_nowcast(feats)
    labeled = labeled[labeled["regime_label"] >= 0]
    labeled = labeled.dropna(subset=MARKET_FEATURE_COLS)
    if labeled.empty:
        raise ValueError(
            f"fold [{fold_train_start},{fold_train_end}) has no labeled rows"
        )

    labeled = labeled.sort_index()
    split = int(len(labeled) * 0.85)
    train_df, cal_df = labeled.iloc[:split], labeled.iloc[split:]
    X_tr, y_tr = train_df[MARKET_FEATURE_COLS], train_df["regime_label"]
    X_cal, y_cal = cal_df[MARKET_FEATURE_COLS], cal_df["regime_label"]

    from sklearn.ensemble import RandomForestClassifier

    out_dir_path = Path(out_dir)
    out_dir_path.mkdir(parents=True, exist_ok=True)
    model_name = f"_wf_{pair}_fold_rf_{fold_train_start}_{fold_train_end}.pkl"
    model_path = out_dir_path / model_name

    clf = RegimeClassifier(model_path=str(model_path), model_type="random_forest")
    clf.pair = pair
    clf.timeframe = "1h"
    clf.train_start = str(fold_df.index[0])
    clf.train_end = str(fold_df.index[-1])
    clf.label_params = {
        "window_bars": 24,
        "return_threshold": 0.02,
        "danger_drawdown_threshold": -0.03,
    }
    clf.model = RandomForestClassifier(
        n_estimators=200,
        max_depth=None,
        min_samples_leaf=5,
        max_features="sqrt",
        class_weight="balanced_subsample",
        n_jobs=-1,
        random_state=seed,
    )
    clf.train(X_tr, y_tr)
    clf.calibrate(X_cal, y_cal)
    clf.feature_columns = list(MARKET_FEATURE_COLS)
    clf.feature_schema_version = FEATURE_SCHEMA_VERSION
    clf.class_distribution = {
        int(k): int(v) for k, v in y_tr.value_counts().items()
    }
    clf.training_samples = int(len(train_df))
    clf.metrics = {
        "training_samples": int(len(train_df)),
        "calibration_samples": int(len(cal_df)),
    }
    clf.source_commit = _git_commit()
    clf.save_model()

    # Sidecar manifest in the PPO-sidecar format (models/rl/ppo_*.json).
    data_bytes = fold_df[["open", "high", "low", "close", "volume"]].to_csv().encode()
    data_hash = hashlib.sha256(data_bytes).hexdigest()
    contract_hash = hashlib.sha256(
        json.dumps(list(MARKET_FEATURE_COLS), separators=(",", ":")).encode()
    ).hexdigest()
    manifest = {
        "pair": pair,
        "model_path": str(model_path),
        "git_sha": clf.source_commit,
        "source_commit": clf.source_commit,
        "data_hash": data_hash,
        "train_start": str(fold_df.index[0]),
        "train_end": str(fold_df.index[-1]),
        "fold_bars": int(fold_train_end - fold_train_start),
        "training_samples": int(len(train_df)),
        "calibration_samples": int(len(cal_df)),
        "class_distribution": {
            str(k): int(v) for k, v in y_tr.value_counts().items()
        },
        "feature_contract_hash": contract_hash,
        "seed": seed,
        "trained_at": datetime.now(timezone.utc).date().isoformat(),
    }
    manifest_path = out_dir_path / f"{model_name}.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True))
    return {"model_path": str(model_path), "model_name": model_name,
            "manifest": manifest}


def _git_commit() -> str:
    import subprocess

    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, timeout=2,
            stderr=subprocess.DEVNULL,
        ).strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# ---------------------------------------------------------------------------
# Orchestration (heavy deps imported lazily; not unit-tested — validated via
# the tiny end-to-end smoke in main()).
# ---------------------------------------------------------------------------


def _train_slice_subprocess(
    pair: str, train_end_date, train_bars: int, timesteps: int, model_path: str
) -> str:
    """Train one PPO model for a slice via the trainer CLI (subprocess).

    Uses ``--train-end <test_start_date>`` so the model trains strictly before
    the OOS slice; ``--months`` is sized to cover ``train_bars`` (rounds up, so
    the model may see slightly more than the strict slice — the eval boundary
    is what matters and the guard enforces it).
    """
    import math
    import subprocess
    import sys

    months = max(1, math.ceil(train_bars / 720))  # ~720 bars / 30 days
    cmd = [
        sys.executable, "-m", "src.rl.agents.ppo_trainer",
        "--pair", pair,
        "--train-end", str(train_end_date),
        "--months", str(months),
        "--timesteps", str(timesteps),
        "--output", model_path,
    ]
    print(f"  training: {' '.join(cmd)}", flush=True)
    subprocess.run(cmd, check=True)
    return model_path


def _evaluate_slice(test_df, ppo_model_path: str, rf_model_path: str, warmup: int = 100):
    """Run PPO + RF through the OOS slice; return (ppo_returns, rf_returns, ppo, rf).

    ``warmup`` is the number of prepended warmup bars. It is passed to the
    env as ``warmup_bars`` so the first collected return lands exactly on the
    first bar AFTER the declared test boundary — previously the env's default
    of 50 let PPO/RF collect ~49 pre-boundary bars.
    """
    from src.rl.env import EnvConfig, TradingEnv
    from src.rl.evaluate import _run_model
    from src.rl.router import PPORouter, SupervisedRegimeRouter

    config = EnvConfig(window_length=len(test_df), warmup_bars=warmup)
    env = TradingEnv(test_df, config)
    ppo = _run_model(env, PPORouter(ppo_model_path))
    rf = _run_model(env, SupervisedRegimeRouter(rf_model_path))
    return ppo["returns_array"], rf["returns_array"], ppo, rf


def _evaluate_slice_aligned(test_df, ppo_model_path: str | None = None,
                            rf_model_path: str | None = None, warmup: int = 100):
    """Boundary-strict evaluation of one slice for all three comparators.

    Returns ``{"ppo": {"returns": Series, "summary": dict}, "rf": ..., "ta": ...}``
    where every ``returns`` Series is indexed by the timestamp of the bar that
    produced it, and all three share the IDENTICAL index (inner join).

    The first return's timestamp is the bar immediately after the warmup
    anchor, i.e. the first bar of the declared test window (the env executes
    the action chosen on bar ``warmup`` through bar ``warmup + 1``).
    """
    from src.rl.env import EnvConfig, TradingEnv
    from src.rl.evaluate import _run_model
    from src.rl.router import PPORouter, SupervisedRegimeRouter

    config = EnvConfig(window_length=len(test_df), warmup_bars=warmup)
    env = TradingEnv(test_df, config)

    summaries = {}
    if ppo_model_path:
        summaries["ppo"] = _strip_padding_returns(_run_model(env, PPORouter(ppo_model_path)))
    if rf_model_path:
        summaries["rf"] = _strip_padding_returns(_run_model(env, SupervisedRegimeRouter(rf_model_path)))

    result = {
        name: {"returns": _to_timestamped(summary, test_df), "summary": summary}
        for name, summary in summaries.items()
    }
    result["ta"] = {
        "returns": _technical_analysis_returns(test_df, warmup),
        "summary": {},
    }
    # Inner join on timestamps so every comparator shares the identical
    # index; alignment by position is forbidden downstream.
    keys = [k for k in ("ppo", "rf", "ta") if k in result]
    joined = align_on_timestamps(*(result[k]["returns"] for k in keys))
    for k, series in zip(keys, joined):
        result[k]["returns"] = series
    return result


def _to_timestamped(summary: dict, test_df) -> "pd.Series":
    """Convert a _run_model summary into a timestamp-indexed return series."""
    import pandas as pd

    ts = summary.get("timestamps")
    if ts is None:
        raise AssertionError(
            "_run_model did not expose timestamps; cannot align by timestamp"
        )
    return pd.Series(np.asarray(summary["returns_array"], dtype=np.float64), index=ts)


def _strip_padding_returns(summary: dict) -> dict:
    """Drop the terminal padding step the env emits after the last real bar.

    When the env runs out of bars it clamps ``bar_idx`` to the final index
    and returns ``truncated=True`` with a 0.0 reward — that step produced no
    bar, so its "return" is padding and its timestamp duplicates the last
    real bar's. Timestamped consumers drop it via the duplicate index here.
    """
    import pandas as pd

    ts = summary.get("timestamps")
    if ts is None or not len(ts):
        return summary
    keep = ~pd.Index(ts).duplicated(keep="first")
    if keep.all():
        return summary
    return {
        **summary,
        "returns_array": np.asarray(summary["returns_array"])[keep.to_numpy()]
        if hasattr(keep, "to_numpy") else np.asarray(summary["returns_array"])[keep],
        "timestamps": pd.Index(ts)[keep],
    }


def align_on_timestamps(*series) -> tuple:
    """Inner-join series on their DatetimeIndex; log when lengths change."""
    import logging

    log = logging.getLogger(__name__)
    names = [getattr(s, "name", None) or f"s{i}" for i, s in enumerate(series)]
    joined_index = series[0].index
    for s in series[1:]:
        joined_index = joined_index.intersection(s.index)
    out = tuple(s.loc[joined_index] for s in series)
    if any(len(s) != len(joined_index) for s in series):
        log.info(
            "timestamp alignment dropped rows: inputs %s -> aligned %d "
            "(joined index %s..%s)",
            {name: len(s) for name, s in zip(names, series)},
            len(joined_index),
            joined_index[0] if len(joined_index) else None,
            joined_index[-1] if len(joined_index) else None,
        )
    return out


def _technical_analysis_returns(test_df, warmup: int) -> "pd.Series":
    """Passive TA comparator: close-to-close returns after indicator warmup.

    Timestamp-indexed: the return of bar ``k`` is attributed to bar ``k``'s
    timestamp, matching the PPO/RF attribution (the action chosen on bar
    ``k-1`` executes through bar ``k``).
    """
    import pandas as pd

    closes = test_df["close"]
    start = min(max(0, warmup + 1), len(closes))
    closes = closes.iloc[start - 1 :]  # include prior close for the first diff
    if len(closes) < 2:
        return pd.Series(np.array([], dtype=np.float64), dtype=np.float64)
    rets = closes.pct_change().iloc[1:]
    return rets


def _report_metadata(pair: str, rf_model: str, ppo_models: list[str], **extra) -> dict:
    import hashlib
    import subprocess

    def checksum(path: str) -> str | None:
        try:
            digest = hashlib.sha256()
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(block)
            return digest.hexdigest()
        except OSError:
            return None

    try:
        source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        source_commit = "unknown"
    return {
        "source_commit": source_commit,
        "pair": pair,
        "rf_model": rf_model,
        "rf_model_sha256": checksum(rf_model),
        # Keep paths paired with the per-slice checksums so report verifiers
        # can resolve every generated PPO artifact deterministically.
        "ppo_model_paths": list(ppo_models),
        "ppo_model_sha256": [checksum(path) for path in ppo_models],
        **extra,
    }


def run_walk_forward(
    pair: str,
    rf_model: str,
    *,
    history_start,
    history_end,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    timesteps: int,
    warmup: int = 100,
    embargo_bars: int = DEFAULT_EMBARGO_BARS,
    report_path: str | None = None,
    feature_hash: str | None = None,
    fees: float = 0.0,
    slippage: float = 0.0,
    fold_specific_rf: bool = True,
) -> dict:
    """Full chronological walk-forward with PPO/RF and TA/ML comparisons.

    ``fold_specific_rf=True`` (default) trains one RF baseline per fold on
    that fold's training window only (audit fix #3), instead of reusing one
    long-window artifact across all folds. Pass the shared artifact path via
    ``rf_model`` when ``fold_specific_rf=False``.
    """
    from src.rl.data import load_klines

    print(f"[{pair}] loading history {history_start} -> {history_end}")
    df = load_klines(pair, history_start, history_end)
    # Data provenance: hash the loaded OHLCV frame so every emitted report is
    # tied to the exact evaluation data (audit fix: pinning the date alone
    # does not pin the data — cache contents can change).
    import hashlib as _hashlib

    _frame_csv = df[[
        c for c in ("open", "high", "low", "close", "volume") if c in df.columns
    ]].to_csv().encode()
    data_end_str = str(history_end)
    data_sha256 = _hashlib.sha256(_frame_csv).hexdigest()
    slices = walk_forward_slices(
        len(df), train_bars, test_bars, step_bars, embargo_bars=embargo_bars
    )
    if not slices:
        print(f"[{pair}] series too short for {train_bars}+{test_bars} slices")
        return {"pair": pair, "slices": 0}
    print(f"[{pair}] {len(slices)} walk-forward slices")
    ppo_returns, rf_returns, ta_returns = [], [], []
    ppo_exposure, rf_exposure, ta_exposure = [], [], []
    per_slice, failed, model_paths = [], [], []
    fold_rf_manifests: list[dict] = []
    boundary_report: list[dict] = []
    for i, (ts, te, vs, ve) in enumerate(slices):
        try:
            train_end_date = strict_training_end_date(df.index, te)
            model_path = f"models/rl/_wf_{pair}_slice{i}.zip"
            _train_slice_subprocess(pair, train_end_date, train_bars, timesteps, model_path)
            model_paths.append(model_path)
            slice_rf_model = rf_model
            if fold_specific_rf:
                fold = train_fold_rf(pair, df, ts, te, out_dir="models/rl")
                slice_rf_model = fold["model_path"]
                fold_rf_manifests.append(fold["manifest"])
                print(
                    f"  fold RF {i}: window {fold['manifest']['train_start']}"
                    f" -> {fold['manifest']['train_end']}, "
                    f"samples={fold['manifest']['training_samples']}, "
                    f"classes={fold['manifest']['class_distribution']}",
                    flush=True,
                )
            # Frame = [warmup prefix][test bars]. The declared test-slice
            # start is df.index[vs]; the first collected return must land on
            # the FIRST TEST BAR (vs+1 in frame coords) — the aligned
            # evaluator enforces this and timestamp-aligns all comparators.
            test_df = df.iloc[max(0, vs - warmup):ve]
            aligned = _evaluate_slice_aligned(test_df, model_path, slice_rf_model, warmup=warmup)

            declared_test_start = df.index[vs]
            for name in ("ppo", "rf", "ta"):
                series_ts = aligned[name]["returns"].index
                if len(series_ts) == 0:
                    raise AssertionError(
                        f"slice {i}: {name} produced zero aligned returns"
                    )
                # The first test bar is the bar after the warmup anchor.
                # Assert no comparator reports any timestamp at or before
                # the last warmup bar (which would mean pre-boundary bars
                # leaked into the pooled OOS series).
                last_warmup_ts = df.index[vs - 1] if vs >= 1 else None
                if last_warmup_ts is not None and series_ts[0] <= last_warmup_ts:
                    raise AssertionError(
                        f"slice {i}: {name} first timestamp {series_ts[0]} "
                        f"precedes declared test boundary {declared_test_start}"
                    )
            boundary_report.append({
                "slice": i,
                "declared_test_start": str(declared_test_start),
                **{
                    name: {
                        "first_ts": str(aligned[name]["returns"].index[0]),
                        "last_ts": str(aligned[name]["returns"].index[-1]),
                        "rows": len(aligned[name]["returns"]),
                    }
                    for name in ("ppo", "rf", "ta")
                },
            })

            ppo_s = aligned["ppo"]["returns"]
            rf_s = aligned["rf"]["returns"]
            ta_s = aligned["ta"]["returns"]
            ppo_sum = aligned["ppo"]["summary"]
            rf_sum = aligned["rf"]["summary"]
            ppo_ret = ppo_s.to_numpy(dtype=np.float64)
            rf_ret = rf_s.to_numpy(dtype=np.float64)
            ta_ret = ta_s.to_numpy(dtype=np.float64)
            n = len(ppo_ret)  # all three share the identical joined index
            ppo_returns.append(ppo_ret)
            rf_returns.append(rf_ret)
            ta_returns.append(ta_ret)
            ppo_exposure.append(np.asarray(ppo_sum.get("exposure_array", np.zeros(n)))[:n])
            rf_exposure.append(np.asarray(rf_sum.get("exposure_array", np.zeros(n)))[:n])
            ta_exposure.append(np.ones(n, dtype=np.float64))
            per_slice.append({
                "slice": i,
                "train_start": str(df.index[ts]),
                "train_end": str(df.index[te - 1]),
                "embargo_start": str(df.index[te]) if embargo_bars else None,
                "embargo_end": str(df.index[vs - 1]) if embargo_bars else None,
                "test_start": str(df.index[vs]),
                "test_end": str(df.index[ve - 1]),
                "ppo": ppo_sum,
                "rf": rf_sum,
                "ml": rf_sum,
                "ta": {"trade_count": 1, "returns_array": ta_ret, "time_in_market": 1.0},
            })
            print(
                f"  slice {i}: PPO {ppo_sum['Total Return']} | RF {rf_sum['Total Return']}",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001 - isolate slice failures
            failed.append(i)
            print(f"  slice {i}: FAILED ({e}); skipping", flush=True)

    stat, p, n = aggregate_dm(ppo_returns, rf_returns)
    ta_all, ml_all = pool_returns(ta_returns, rf_returns)
    ta_stat, ta_p, ta_n = aggregate_dm(ta_returns, rf_returns)
    print(
        f"[{pair}] pooled DM (PPO vs RF): stat={stat:.3f} p={p:.4f} n={n} "
        f"({len(failed)} slices failed)",
        flush=True,
    )

    from src.ml.evaluation_report import summarize_returns, write_report
    from src.ml.promotion_gate import evaluate

    def summarize(series, exposures, trade_count, realized_fees):
        values = pool_returns(series, exposures)[0]
        exposure_values = pool_returns(exposures, series)[0]
        has_realized_fees = realized_fees is not None
        applied_fees = 0.0 if has_realized_fees else fees + slippage
        result = summarize_returns(
            values, exposure_values, fees=applied_fees, trade_count=trade_count
        )
        # _run_model returns equity deltas after execution fees. Keep net
        # returns untouched while surfacing the realized fee total.
        result["fees"] = float(realized_fees if has_realized_fees else applied_fees)
        return result

    def realized_fees(strategy: str) -> float | None:
        fee_values = [
            float(row[strategy]["fees"])
            for row in per_slice
            if row[strategy].get("fees") is not None
        ]
        return float(sum(fee_values)) if fee_values else None

    ppo_trade_count = sum(int(row["ppo"].get("trade_count", 0)) for row in per_slice)
    rf_trade_count = sum(int(row["rf"].get("trade_count", 0)) for row in per_slice)
    ta_trade_count = len(per_slice)
    ppo_metrics = summarize(ppo_returns, ppo_exposure, ppo_trade_count, realized_fees("ppo"))
    rf_metrics = summarize(rf_returns, rf_exposure, rf_trade_count, realized_fees("rf"))
    ta_metrics = summarize(ta_returns, ta_exposure, ta_trade_count, None)

    # Preserve regime attribution when evaluators provide it. If unavailable,
    # the empty map is intentional: promotion_gate treats it as inconclusive.
    trade_counts_by_regime: dict[str, dict[str, int]] = {}
    for strategy in ("ppo", "rf", "ta"):
        aggregate: dict[str, int] = {}
        for row in per_slice:
            raw = row[strategy].get("trade_counts_by_regime")
            if not isinstance(raw, dict):
                continue
            for regime, count in raw.items():
                if isinstance(count, dict):
                    for nested_regime, nested_count in count.items():
                        aggregate[str(nested_regime)] = aggregate.get(str(nested_regime), 0) + int(nested_count)
                else:
                    aggregate[str(regime)] = aggregate.get(str(regime), 0) + int(count)
        if aggregate:
            trade_counts_by_regime[strategy] = aggregate

    gate_metrics = {
        "trade_count": ppo_trade_count,
        "trade_counts_by_strategy": {
            "ppo": ppo_trade_count,
            "rf": rf_trade_count,
            "ta": ta_trade_count,
        },
        "trade_counts_by_regime": trade_counts_by_regime,
        "window_count": len(per_slice),
        "ppo_profit_factor": ppo_metrics["profit_factor"],
        "rf_profit_factor": rf_metrics["profit_factor"],
        "ppo_max_drawdown": ppo_metrics["max_drawdown"],
        "rf_max_drawdown": rf_metrics["max_drawdown"],
        "ppo_exposure": ppo_metrics["time_in_market"],
        "rf_exposure": rf_metrics["time_in_market"],
        "ppo_total_return": ppo_metrics["total_return"],
        "rf_total_return": rf_metrics["total_return"],
    }
    promotion = evaluate(gate_metrics)

    def compact(values):
        return {
            key: value
            for key, value in values.items()
            if key not in {"returns_array", "equity_curve", "exposure_array"}
        }

    report_slices = [
        {
            "slice": row["slice"],
            "train_start": row["train_start"],
            "train_end": row["train_end"],
            "embargo_start": row["embargo_start"],
            "embargo_end": row["embargo_end"],
            "test_start": row["test_start"],
            "test_end": row["test_end"],
            "ppo": compact(row["ppo"]),
            "rf": compact(row["rf"]),
            "ml": compact(row["ml"]),
            "ta": compact(row["ta"]),
        }
        for row in per_slice
    ]
    report = {
        "ppo": ppo_metrics,
        "rf": rf_metrics,
        "ml": rf_metrics,
        "ta": ta_metrics,
        "gate_metrics": gate_metrics,
        "promotion": promotion,
        "ppo_vs_rf": {"dm_stat": stat, "dm_p": p, "n": n},
        "ta_vs_ml": {"dm_stat": ta_stat, "dm_p": ta_p, "n": len(ta_all)},
    }
    if report_path:
        metadata = _report_metadata(
            pair, rf_model, model_paths,
            history_start=str(history_start), history_end=str(history_end),
            feature_hash=feature_hash, fees=fees, slippage=slippage,
            embargo_bars=embargo_bars, train_bars=train_bars,
            test_bars=test_bars, step_bars=step_bars,
            fold_rf_manifests=fold_rf_manifests,
            fold_specific_rf=fold_specific_rf,
            data_end=data_end_str,
            data_sha256=data_sha256,
            data_bars=len(df),
        )
        write_report(report_path, metadata, report, report_slices)
    return {
        "pair": pair, "slices": len(slices), "ok": len(per_slice),
        "failed": failed, "per_slice": per_slice,
        "boundary_report": boundary_report,
        "fold_rf_manifests": fold_rf_manifests,
        "ppo_returns": ppo_returns, "rf_returns": rf_returns,
        "ml_returns": rf_returns, "ta_returns": ta_returns,
        "dm_stat": stat, "dm_p": p, "n": n,
        "ta_ml_dm_stat": ta_stat, "ta_ml_dm_p": ta_p,
        "promotion": promotion, "report": report,
    }


def main() -> int:
    import argparse
    from datetime import date, timedelta

    parser = argparse.ArgumentParser(
        prog="python -m src.rl.walk_forward",
        description="Walk-forward multi-window OOS harness for the RL pipeline.",
    )
    parser.add_argument("--pairs", nargs="+", default=["ETHUSDT"])
    parser.add_argument("--rf-model", default="models/regime_ETH-USDT.pkl")
    parser.add_argument("--train-bars", type=int, default=4320)   # ~6 months
    parser.add_argument("--test-bars", type=int, default=720)     # ~1 month
    parser.add_argument("--step-bars", type=int, default=2160)    # ~3 months
    parser.add_argument("--embargo-bars", type=int, default=DEFAULT_EMBARGO_BARS)
    parser.add_argument("--timesteps", type=int, default=1_000_000)
    parser.add_argument("--months", type=int, default=24, help="Total history to load.")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--feature-hash", default=None)
    parser.add_argument("--fees", type=float, default=0.0)
    parser.add_argument("--slippage", type=float, default=0.0)
    parser.add_argument(
        "--data-end",
        required=True,
        help="Pinned evaluation end date (YYYY-MM-DD). Required: the moving "
        "date.today() default made runs unreproducible against cached "
        "slice models.",
    )
    args = parser.parse_args()

    history_end = date.fromisoformat(args.data_end)
    history_start = history_end - timedelta(days=30 * args.months)

    results = []
    for pair in args.pairs:
        rf = (
            args.rf_model
            if len(args.pairs) == 1
            else f"models/regime_{pair.replace('USDT', '-USDT').replace('/', '-')}.pkl"
        )
        report_path = f"{args.report_dir}/rl_walk_forward_{pair}.json"
        results.append(
            run_walk_forward(
                pair, rf,
                history_start=history_start, history_end=history_end,
                train_bars=args.train_bars, test_bars=args.test_bars,
                step_bars=args.step_bars, timesteps=args.timesteps,
                embargo_bars=args.embargo_bars, report_path=report_path,
                feature_hash=args.feature_hash, fees=args.fees,
                slippage=args.slippage,
            )
        )

    import json
    ci_results = [
        {
            "pair": result["pair"],
            "slices": result.get("slices", 0),
            "ok": result.get("ok", 0),
            "failed": result.get("failed", []),
            "ppo_vs_rf": {
                "dm_stat": result.get("dm_stat", 0.0),
                "dm_p": result.get("dm_p", 1.0),
                "n": result.get("n", 0),
            },
            "ta_vs_ml": {
                "dm_stat": result.get("ta_ml_dm_stat", 0.0),
                "dm_p": result.get("ta_ml_dm_p", 1.0),
            },
            "promotion": result.get("promotion", {"eligible": False, "reasons": ["no_slices"]}),
        }
        for result in results
    ]
    print(json.dumps({"results": ci_results}, sort_keys=True))
    print("\n=== Walk-Forward Summary ===")
    for result in ci_results:
        print(
            f"{result['pair']}: {result['slices']} slices, "
            f"PPO-vs-RF p={result['ppo_vs_rf']['dm_p']:.4f}, "
            f"TA-vs-ML p={result['ta_vs_ml']['dm_p']:.4f}, "
            f"promotion={result['promotion']['eligible']}"
        )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
