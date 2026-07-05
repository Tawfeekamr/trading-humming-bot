#!/usr/bin/env python3
"""PPO training CLI for the RL execution-routing pipeline.

Loads 1h klines for a pair, wraps them in ``TradingEnv``, trains a PPO agent
via stable-baselines3 with Tensorboard logging, and saves the model under
``models/rl/`` with a timestamped filename plus a small JSON sidecar recording
git SHA + data hash for reproducibility.

Usage::

    python -m src.rl.agents.ppo_trainer \
        --pair ETHUSDT --months 12 --timesteps 500000 --lambda-dd 0.5

    tensorboard --logdir ./tb/

The script imports stable-baselines3 LAZILY inside ``main()`` so the module
loads (and unit tests can import it) without the heavy RL deps installed. If
SB3 is missing, a helpful install hint is printed and the process exits.

Reproducibility notes:
    * The same ``--seed`` is passed to ``PPO(seed=...)`` and to ``env.reset()``
      via a thin seeding wrapper, so an identical (data, seed, hyperparams)
      triple reproduces the same trajectory bit-for-bit on the same torch /
      CUDA build. Cross-device determinism is not guaranteed (GPU non-determinism).
    * Git SHA + a SHA256 of the close-price series are written next to the
      model so a trained artefact always carries its provenance.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

# Default output dir is repo-relative so it works regardless of CWD: the
# trainer is normally invoked as ``python -m src.rl.agents.ppo_trainer`` from
# the repo root, but the path is absolute to avoid surprises.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_OUTPUT_DIR = _REPO_ROOT / "models" / "rl"
_DEFAULT_TB_LOG = _REPO_ROOT / "tb"


def _git_sha() -> str:
    """Return the current commit SHA, or 'unknown' if not in a git repo."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=_REPO_ROOT,
            stderr=subprocess.DEVNULL,
        ).decode().strip()
        return out
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return "unknown"


def _data_hash(df) -> str:
    """Stable SHA256 of the OHLCV frame: index + close + volume.

    Index range + close/volume arrays uniquely identify the data the model
    trained on (open/high/low are redundant with close+volume for hashing
    purposes and we want this cheap for multi-year frames).
    """
    h = hashlib.sha256()
    h.update(str(df.index[0]).encode())
    h.update(str(df.index[-1]).encode())
    h.update(str(len(df)).encode())
    h.update(df["close"].to_numpy().tobytes())
    h.update(df["volume"].to_numpy().tobytes())
    return h.hexdigest()[:16]


def _format_int(n: int) -> str:
    """Comma-grouped integer for log lines."""
    return f"{n:,}"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI args. Factored out of ``main`` so it is unit-testable."""
    parser = argparse.ArgumentParser(
        prog="python -m src.rl.agents.ppo_trainer",
        description="Train a PPO agent on TradingEnv (RL execution-routing pipeline).",
    )
    parser.add_argument("--pair", default="ETHUSDT",
                        help="Trading pair (default: ETHUSDT).")
    parser.add_argument("--months", type=int, default=12,
                        help="Lookback in months of 1h klines to train on (default: 12).")
    parser.add_argument("--timesteps", type=int, default=500_000,
                        help="Total PPO training timesteps (default: 500_000).")
    parser.add_argument("--lambda-dd", type=float, default=0.5,
                        dest="lambda_dd",
                        help="Drawdown-step penalty weight in the env reward (default: 0.5).")
    parser.add_argument("--seed", type=int, default=42,
                        help="RNG seed for PPO + env.reset (default: 42).")
    parser.add_argument("--tb-log", default=str(_DEFAULT_TB_LOG),
                        help="Tensorboard log directory (default: ./tb/).")
    parser.add_argument("--output", default=None,
                        help="Model output path. Default: models/rl/ppo_{pair}_{ts}.zip.")
    parser.add_argument("--learning-rate", type=float, default=3e-4,
                        help="PPO learning rate (default: 3e-4).")
    parser.add_argument("--n-steps", type=int, default=2048,
                        help="Rollout horizon per PPO update, in env steps (default: 2048).")
    parser.add_argument("--batch-size", type=int, default=64,
                        help="PPO minibatch size (default: 64).")
    parser.add_argument("--gamma", type=float, default=0.99,
                        help="Discount factor (default: 0.99).")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point. Returns the process exit code."""
    args = parse_args(argv)

    # Lazy import — stable-baselines3 is ~2GB (torch). The module loads
    # without it; only ``main()`` requires it. Print a helpful hint on failure.
    try:
        from stable_baselines3 import PPO
        from stable_baselines3.common.utils import set_random_seed
    except ImportError:
        print(
            "ERROR: stable-baselines3 is not installed.\n"
            "       Install the RL training deps with:\n"
            "           pip install -r requirements-rl.txt",
            file=sys.stderr,
        )
        return 2

    # Local imports (cheap, always available).
    from src.rl.data import load_klines
    from src.rl.env import EnvConfig, TradingEnv

    # --- 1. Load data ----------------------------------------------------
    end = date.today()
    start = end - timedelta(days=30 * args.months)
    print(f"Loading {args.pair} klines {start} → {end} (~{args.months} months)...")
    df = load_klines(args.pair, start, end)
    if df.empty:
        print(f"ERROR: no kline data found for {args.pair} in [{start}, {end}].",
              file=sys.stderr)
        return 1
    print(f"Loaded {len(df):,} bars  "
          f"({df.index[0]} → {df.index[-1]})  "
          f"data_hash={_data_hash(df)}")

    # --- 2. Build env ----------------------------------------------------
    # TradingEnv does not take a seed in __init__; seed is applied to env.reset
    # via SB3's seeding (set_random_seed below + SB3's internal env seeding).
    config = EnvConfig(lambda_dd=args.lambda_dd)
    env = TradingEnv(df, config=config)

    # Seed torch + python + numpy globally. SB3's make_vec_env would propagate
    # this to env.reset() automatically; for a single env we also call
    # env.reset(seed=...) once to bake the window-start RNG deterministically.
    set_random_seed(args.seed)
    env.reset(seed=args.seed)

    # --- 3. Train PPO ----------------------------------------------------
    model = PPO(
        "MlpPolicy",
        env,
        learning_rate=args.learning_rate,
        n_steps=args.n_steps,
        batch_size=args.batch_size,
        gamma=args.gamma,
        verbose=1,
        tensorboard_log=args.tb_log,
        seed=args.seed,
    )
    print(
        f"Training PPO for {_format_int(args.timesteps)} timesteps "
        f"(lr={args.learning_rate}, n_steps={args.n_steps}, "
        f"batch_size={args.batch_size}, gamma={args.gamma}, "
        f"lambda_dd={args.lambda_dd}, seed={args.seed})..."
    )
    print(f"    Tensorboard: tensorboard --logdir {args.tb_log}")
    model.learn(total_timesteps=args.timesteps)

    # --- 4. Save model + provenance sidecar -----------------------------
    timestamp = date.today().isoformat()
    output = args.output or str(_DEFAULT_OUTPUT_DIR / f"ppo_{args.pair}_{timestamp}.zip")
    out_path = Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    model.save(out_path)
    print(f"Model saved to {out_path}")

    sidecar = out_path.with_suffix(".json")
    meta = {
        "pair": args.pair,
        "model_path": str(out_path),
        "git_sha": _git_sha(),
        "data_hash": _data_hash(df),
        "data_start": str(df.index[0]),
        "data_end": str(df.index[-1]),
        "n_bars": int(len(df)),
        "timesteps": int(args.timesteps),
        "hyperparams": {
            "learning_rate": args.learning_rate,
            "n_steps": args.n_steps,
            "batch_size": args.batch_size,
            "gamma": args.gamma,
            "lambda_dd": args.lambda_dd,
        },
        "seed": int(args.seed),
        "trained_at": timestamp,
    }
    sidecar.write_text(json.dumps(meta, indent=2))
    print(f"Provenance sidecar saved to {sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
