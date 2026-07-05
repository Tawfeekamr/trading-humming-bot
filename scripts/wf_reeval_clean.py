#!/usr/bin/env python3
"""Re-evaluate cached walk-forward PPO slice models against a different RF baseline.

Reuses ``models/rl/_wf_{pair}_slice{i}.zip`` produced by ``src.rl.walk_forward``
— no PPO retraining. Pools per-bar returns across slices for a HAC-robust DM
test. Use this to compare the SAME trained PPO policies against the clean RF
baseline (``models/regime_{PAIR}_clean.pkl``) instead of the legacy opaque one.

Usage:
    python scripts/wf_reeval_clean.py ETHUSDT
    python scripts/wf_reeval_clean.py ETHUSDT models/regime_ETH-USDT_clean.pkl
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

import numpy as np

# Make `src` importable when run as a script file (python scripts/wf_reeval_clean.py).
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


def main() -> int:
    pair = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
    rf = (
        sys.argv[2]
        if len(sys.argv) > 2
        else f"models/regime_{pair.replace('USDT', '-USDT')}_clean.pkl"
    )
    # Must match the sweep that produced the cached slice models.
    train_bars, test_bars, step_bars, months, warmup = 4320, 720, 2160, 24, 100

    from src.rl.data import load_klines
    from src.rl.walk_forward import _evaluate_slice, aggregate_dm, walk_forward_slices

    end = date.today()
    start = end - timedelta(days=30 * months)
    df = load_klines(pair, start, end)
    slices = walk_forward_slices(len(df), train_bars, test_bars, step_bars)
    print(f"[{pair}] {len(slices)} slices, RF baseline = {rf}")

    ppo_ret, rf_ret = [], []
    for i, (_ts, _te, vs, ve) in enumerate(slices):
        model = f"models/rl/_wf_{pair}_slice{i}.zip"
        try:
            test_df = df.iloc[max(0, vs - warmup):ve]
            p, r, ps, rs = _evaluate_slice(test_df, model, rf)
            ppo_ret.append(np.asarray(p, dtype=np.float64))
            rf_ret.append(np.asarray(r, dtype=np.float64))
            print(
                f"  slice {i}: PPO {ps['Total Return']} | "
                f"RF {rs['Total Return']}",
                flush=True,
            )
        except Exception as e:  # noqa: BLE001
            print(f"  slice {i}: FAILED ({e}); skipping", flush=True)

    stat, p, n = aggregate_dm(ppo_ret, rf_ret)
    print(f"[{pair}] pooled DM (PPO vs RF): stat={stat:.3f} p={p:.4f} n={n}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
