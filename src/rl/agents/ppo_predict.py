#!/usr/bin/env python3
"""PPO inference helper — load a trained model + run it on a data frame.

Used by Task 6 walk-forward evaluation to roll a trained PPO policy through an
out-of-sample frame and collect per-bar actions + the equity curve.

Lazy SB3 import: ``predict()`` only requires stable-baselines3 at call time,
so this module can be imported (and unit-tested) without the RL deps.

Quick CLI::

    python -m src.rl.agents.ppo_predict \\
        --model models/rl/ppo_ETHUSDT_2026-07-04.zip \\
        --pair ETHUSDT --months 3

Returns nothing on stdout except the summary; the function ``predict()`` is
the importable entry point used by ``src/rl/evaluate.py``.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from typing import Optional

import numpy as np
import pandas as pd


def predict(
    model_path: str,
    df: pd.DataFrame,
    config: Optional[object] = None,
    seed: int = 42,
    deterministic: bool = True,
) -> tuple[list[np.ndarray], list[float], list[float]]:
    """Load a trained PPO model and run it through ``df``.

    Args:
        model_path: Path to a saved SB3 PPO ``.zip``.
        df: OHLCV frame indexed by UTC datetime (same format as
            ``src.rl.data.load_klines``).
        config: optional ``EnvConfig``. Defaults to ``EnvConfig()``.
        seed: Used for the ``env.reset(seed=...)`` call — picking the window.
            Note: the default ``EnvConfig.window_length`` is 4_300 bars, so for
            full-frame evaluation pass a config with ``window_length >= len(df)``
            or call ``predict`` per-window from the evaluator.
        deterministic: Passed to ``model.predict`` (default True — standard for
            OOS evaluation).

    Returns:
        ``(actions, rewards, equity)`` — three equal-length lists, one entry
        per executed bar. ``actions[i]`` is the raw ``np.ndarray`` action
        (shape ``(1,)`` for the Discrete space), ``rewards[i]`` is the float
        reward, ``equity[i]`` is the account equity after step ``i`` (taken
        from ``info["equity"]``).
    """
    from stable_baselines3 import PPO

    from src.rl.env import EnvConfig, TradingEnv

    env = TradingEnv(df, config=config or EnvConfig())
    obs, _ = env.reset(seed=seed)

    model = PPO.load(model_path)
    actions: list[np.ndarray] = []
    rewards: list[float] = []
    equity: list[float] = []

    done = False
    while not done:
        action, _ = model.predict(obs, deterministic=deterministic)
        obs, reward, terminated, truncated, info = env.step(action)
        actions.append(action)
        rewards.append(float(reward))
        equity.append(float(info["equity"]))
        done = bool(terminated or truncated)

    return actions, rewards, equity


# ---------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------


def _cli_main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src.rl.agents.ppo_predict",
        description="Run a trained PPO model on a recent window of klines and "
                    "print summary stats (eval helper).",
    )
    parser.add_argument("--model", required=True, help="Path to PPO .zip.")
    parser.add_argument("--pair", default="ETHUSDT")
    parser.add_argument("--months", type=int, default=3,
                        help="How many months of recent klines to evaluate on (default: 3).")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args(argv)

    try:
        from src.rl.data import load_klines  # noqa: F401  (provenance + lazy)
    except ImportError as e:
        print(f"ERROR: missing local module: {e}", file=sys.stderr)
        return 2

    end = date.today()
    start = end - timedelta(days=30 * args.months)
    df = load_klines(args.pair, start, end)
    if df.empty:
        print(f"ERROR: no data for {args.pair} [{start}, {end}].", file=sys.stderr)
        return 1

    try:
        actions, rewards, equity = predict(args.model, df, seed=args.seed)
    except ImportError:
        print(
            "ERROR: stable-baselines3 is not installed.\n"
            "       pip install -r requirements-rl.txt",
            file=sys.stderr,
        )
        return 2

    eq = np.asarray(equity, dtype=np.float64)
    final = eq[-1] if len(eq) else float("nan")
    ret = (eq[-1] / eq[0] - 1.0) if len(eq) >= 2 else 0.0
    peak = np.maximum.accumulate(eq) if len(eq) else eq
    max_dd = float(np.max((peak - eq) / np.maximum(peak, 1e-8))) if len(eq) else 0.0
    print(f"{args.pair}: {len(actions)} steps | "
          f"final equity=${final:,.2f} | return={ret*100:+.2f}% | "
          f"max DD={max_dd*100:.2f}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli_main())
