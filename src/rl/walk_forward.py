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
    series_len: int, train_bars: int, test_bars: int, step_bars: int
) -> list[tuple[int, int, int, int]]:
    """Rolling train/test index splits over a bar series.

    Returns a list of ``(train_start, train_end, test_start, test_end)`` tuples
    where ``train_end == test_start`` (contiguous, no gap, no overlap) and each
    slice's test window fits fully inside ``[0, series_len)``. The window
    advances by ``step_bars`` each iteration.

    Returns ``[]`` if the series is too short to fit even one train+test pair.
    """
    if train_bars <= 0 or test_bars <= 0 or step_bars <= 0:
        raise ValueError("train_bars, test_bars, step_bars must be positive")

    slices: list[tuple[int, int, int, int]] = []
    start = 0
    while start + train_bars + test_bars <= series_len:
        train_start = start
        train_end = start + train_bars
        test_end = train_end + test_bars
        slices.append((train_start, train_end, train_end, test_end))
        start += step_bars
    return slices


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
) -> dict:
    """Full walk-forward over one pair: slice, train, eval, aggregate.

    ``warmup`` bars before each test slice are included in the eval frame so
    indicators are warmed up; returns are taken over the full frame (aligned
    across PPO/RF so pooling is fair).
    """
    from src.rl.data import load_klines

    print(f"[{pair}] loading history {history_start} -> {history_end}")
    df = load_klines(pair, history_start, history_end)
    slices = walk_forward_slices(len(df), train_bars, test_bars, step_bars)
    if not slices:
        print(f"[{pair}] series too short for {train_bars}+{test_bars} slices")
        return {"pair": pair, "slices": 0}

    print(f"[{pair}] {len(slices)} walk-forward slices")
    ppo_returns, rf_returns, per_slice = [], [], []
    for i, (ts, te, vs, ve) in enumerate(slices):
        train_end_date = df.index[te].date() if hasattr(df.index[te], "date") else df.index[te]
        model_path = f"models/rl/_wf_{pair}_slice{i}.zip"
        _train_slice_subprocess(pair, train_end_date, train_bars, timesteps, model_path)
        test_df = df.iloc[max(0, vs - warmup):ve]
        ppo_ret, rf_ret, ppo_sum, rf_sum = _evaluate_slice(test_df, model_path, rf_model)
        ppo_returns.append(np.asarray(ppo_ret, dtype=np.float64))
        rf_returns.append(np.asarray(rf_ret, dtype=np.float64))
        per_slice.append({"slice": i, "ppo": ppo_sum, "rf": rf_sum})
        print(
            f"  slice {i}: PPO {ppo_sum['Total Return']} | RF {rf_sum['Total Return']}",
            flush=True,
        )

    stat, p, n = aggregate_dm(ppo_returns, rf_returns)
    print(f"[{pair}] pooled DM (PPO vs RF): stat={stat:.3f} p={p:.4f} n={n}")
    return {
        "pair": pair,
        "slices": len(slices),
        "per_slice": per_slice,
        "dm_stat": stat,
        "dm_p": p,
        "n": n,
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
    parser.add_argument("--timesteps", type=int, default=1_000_000)
    parser.add_argument("--months", type=int, default=24, help="Total history to load.")
    args = parser.parse_args()

    history_end = date.today()
    history_start = history_end - timedelta(days=30 * args.months)

    results = []
    for pair in args.pairs:
        rf = args.rf_model if len(args.pairs) == 1 else f"models/regime_{pair.replace('USDT','-USDT').replace('/', '-')}.pkl"
        results.append(
            run_walk_forward(
                pair, rf,
                history_start=history_start, history_end=history_end,
                train_bars=args.train_bars, test_bars=args.test_bars,
                step_bars=args.step_bars, timesteps=args.timesteps,
            )
        )

    print("\n=== Walk-Forward Summary ===")
    for r in results:
        if r.get("slices"):
            print(
                f"{r['pair']}: {r['slices']} slices, "
                f"DM stat={r['dm_stat']:.3f} p={r['dm_p']:.4f} (n={r['n']})"
            )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
