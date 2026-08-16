#!/usr/bin/env python3
"""Flat-policy vs trained-policy total reward across the B2.4 seed models (batch 5 task 1).

Uses ONLY the already-trained seed models (folds 0 and 3, seeds 42/7/123/2024/999,
both pairs). The flat policy is deterministic (action 9 every bar) so its reward is
one value per fold per pair, computed once and reused across seeds.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.rl.data import load_klines  # noqa: E402
from src.rl.walk_forward import DEFAULT_EMBARGO_BARS, walk_forward_slices  # noqa: E402

SEEDS = [42, 7, 123, 2024, 999]
FOLDS = [0, 3]

WORKER = r'''
import sys, json
sys.path.insert(0, ".")
import pandas as pd, numpy as np
from src.rl.env import EnvConfig, TradingEnv
from src.rl.evaluate import _run_model
from src.rl.router import PPORouter

test_df = pd.read_csv({csv!r}, index_col=0, parse_dates=True)
cfg = EnvConfig(window_length=len(test_df), warmup_bars=100)
env = TradingEnv(test_df, cfg)

# trained policy total reward (equity-based per-bar returns are not the
# reward; re-run the env loop collecting rewards directly)
router = PPORouter({model!r})
obs, info = env.reset(seed=42)
done, trained_rew = False, 0.0
while not done:
    a = router.predict(obs)
    obs, r, term, trunc, info = env.step(a)
    trained_rew += float(r)
    done = term or trunc

# flat policy: fresh env (same seed -> same window), action 9 every bar
obs, info = env.reset(seed=42)
done, flat_rew = False, 0.0
while not done:
    obs, r, term, trunc, info = env.step(9)
    flat_rew += float(r)
    done = term or trunc

print(json.dumps({{"trained": trained_rew, "flat": flat_rew}}))
'''


def main() -> int:
    end = date(2026, 7, 5)
    start = end - timedelta(days=30 * 24)
    rows = []
    for pair in ("ETHUSDT", "BNBUSDT"):
        df = load_klines(pair, start, end)
        slices = walk_forward_slices(len(df), 4320, 720, 2160, embargo_bars=DEFAULT_EMBARGO_BARS)
        for fold_i in FOLDS:
            ts, te, vs, ve = slices[fold_i]
            tmp = Path(f"reports/returns/_b5tmp.csv")
            df.iloc[max(0, vs - 100):ve].to_csv(tmp)
            # flat computed once per (pair, fold) via the seed-42 worker; it is
            # deterministic across seeds (same env, same window, action 9 only)
            flat_reward = None
            for seed in SEEDS:
                model = f"models/rl/_seed_{pair}_f{fold_i}_s{seed}.zip"
                if not Path(model).exists():
                    print(f"MISSING MODEL: {model} — stopping per constraint", file=sys.stderr)
                    return 1
                script = WORKER.format(csv=str(tmp), model=model)
                proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
                if proc.returncode != 0:
                    print(f"FAILED {pair} f{fold_i} s{seed}: {proc.stderr[-300:]}", file=sys.stderr)
                    return 1
                res = json.loads(proc.stdout.strip().splitlines()[-1])
                if flat_reward is None:
                    flat_reward = res["flat"]
                rows.append({
                    "pair": pair, "fold": fold_i, "seed": seed,
                    "trained_reward": round(res["trained"], 4),
                    "flat_reward": round(flat_reward, 4),
                    "diff_trained_minus_flat": round(res["trained"] - flat_reward, 4),
                    "flat_outscored": bool(res["trained"] < flat_reward),
                })
            tmp.unlink(missing_ok=True)

    # consistency: flat must be identical across seeds within a cell
    for pair in ("ETHUSDT", "BNBUSDT"):
        for fold_i in FOLDS:
            vals = {r["flat_reward"] for r in rows if r["pair"] == pair and r["fold"] == fold_i}
            assert len(vals) == 1, f"flat reward not deterministic in {pair} f{fold_i}: {vals}"

    n_flat = sum(1 for r in rows if r["flat_outscored"])
    print(f"\nflat outscored trained in {n_flat} of {len(rows)} cells")
    print(f"{'pair':8s} {'fold':>4s} {'seed':>5s} {'trained':>9s} {'flat':>9s} {'diff':>9s} flat_wins")
    for r in rows:
        mark = "  <-- seed42 (main run)" if r["seed"] == 42 else ""
        print(f"{r['pair']:8s} {r['fold']:>4d} {r['seed']:>5d} {r['trained_reward']:>9.4f} "
              f"{r['flat_reward']:>9.4f} {r['diff_trained_minus_flat']:>9.4f} {r['flat_outscored']}{mark}")

    Path("reports/flat_vs_trained_by_seed.json").write_text(json.dumps(rows, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
