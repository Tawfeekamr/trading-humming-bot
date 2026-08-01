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


def walk_forward_slices(
    series_len: int,
    train_bars: int,
    test_bars: int,
    step_bars: int,
    embargo_bars: int = 0,
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


def _evaluate_slice(test_df, ppo_model_path: str, rf_model_path: str):
    """Run PPO + RF through the OOS slice; return (ppo_returns, rf_returns)."""
    from src.rl.env import EnvConfig, TradingEnv
    from src.rl.evaluate import _run_model
    from src.rl.router import PPORouter, SupervisedRegimeRouter

    config = EnvConfig(window_length=len(test_df))
    env = TradingEnv(test_df, config)
    ppo = _run_model(env, PPORouter(ppo_model_path))
    rf = _run_model(env, SupervisedRegimeRouter(rf_model_path))
    return ppo["returns_array"], rf["returns_array"], ppo, rf


def _technical_analysis_returns(test_df, warmup: int) -> np.ndarray:
    """Passive TA comparator: close-to-close returns after indicator warmup."""
    closes = np.asarray(test_df["close"], dtype=np.float64)
    start = min(max(0, warmup), len(closes))
    closes = closes[start:]
    if len(closes) < 2:
        return np.array([], dtype=np.float64)
    return np.diff(closes) / closes[:-1]


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
    embargo_bars: int = 0,
    report_path: str | None = None,
    feature_hash: str | None = None,
    fees: float = 0.0,
    slippage: float = 0.0,
) -> dict:
    """Full chronological walk-forward with PPO/RF and TA/ML comparisons."""
    from src.rl.data import load_klines

    print(f"[{pair}] loading history {history_start} -> {history_end}")
    df = load_klines(pair, history_start, history_end)
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
    for i, (ts, te, vs, ve) in enumerate(slices):
        try:
            train_end_date = strict_training_end_date(df.index, te)
            model_path = f"models/rl/_wf_{pair}_slice{i}.zip"
            _train_slice_subprocess(pair, train_end_date, train_bars, timesteps, model_path)
            model_paths.append(model_path)
            test_df = df.iloc[max(0, vs - warmup):ve]
            ppo_ret, rf_ret, ppo_sum, rf_sum = _evaluate_slice(
                test_df, model_path, rf_model
            )
            ppo_ret = np.asarray(ppo_ret, dtype=np.float64)
            rf_ret = np.asarray(rf_ret, dtype=np.float64)
            ta_ret = _technical_analysis_returns(test_df, warmup)
            n = min(len(ppo_ret), len(rf_ret), len(ta_ret))
            ppo_ret, rf_ret, ta_ret = ppo_ret[:n], rf_ret[:n], ta_ret[:n]
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
        )
        write_report(report_path, metadata, report, report_slices)
    return {
        "pair": pair, "slices": len(slices), "ok": len(per_slice),
        "failed": failed, "per_slice": per_slice,
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
    parser.add_argument("--embargo-bars", type=int, default=0)
    parser.add_argument("--timesteps", type=int, default=1_000_000)
    parser.add_argument("--months", type=int, default=24, help="Total history to load.")
    parser.add_argument("--report-dir", default="reports")
    parser.add_argument("--feature-hash", default=None)
    parser.add_argument("--fees", type=float, default=0.0)
    parser.add_argument("--slippage", type=float, default=0.0)
    args = parser.parse_args()

    history_end = date.today()
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
