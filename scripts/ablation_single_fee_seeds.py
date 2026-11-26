"""Multi-seed extension of the single-fee ablation (fees counted once).

The first ablation (scripts/ablation_single_fee.py) used seed 42 only. This
runs the same experiment for seeds 7, 123, 2024 and 999 on folds 1 and 4
(source folds 0 and 3) -- the same seeds and folds as the frozen seed sweep
(reports/seed_sensitivity.json, reports/flat_vs_trained_by_seed.json) -- so
the two can be compared cell by cell. Seed 42 is read from the first run.

Nothing frozen is touched:
  models  -> models/rl/ablation_single_fee/seed<seed>/
  results -> reports/ablation_single_fee/seeds/

Run (about 1.5 hours; resumable):
    .mlvenv/bin/python scripts/ablation_single_fee_seeds.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ablation_single_fee as A  # noqa: E402

NEW_SEEDS = (7, 123, 2024, 999)
SOURCE_FOLDS = (0, 3)
OUT = Path("reports/ablation_single_fee/seeds")


def cell_stats(trace: pd.DataFrame, flat_reward: float) -> dict:
    eq = np.concatenate([[10_000.0], trace["equity"].to_numpy()])
    r = eq[1:] / eq[:-1] - 1
    e = np.cumprod(1 + r)
    return {
        "capital_weighted_exposure_pct": round(float(trace["position_value/equity"].abs().mean() * 100), 2),
        "flat_share_pct": round(float((trace["action_label"] == "FLAT").mean() * 100), 2),
        "total_return_pct": round(float(np.prod(1 + r) - 1) * 100, 2),
        "max_drawdown": round(float((1 - e / np.maximum.accumulate(e)).max()), 4),
        "trained_reward": round(float(trace["reward"].sum()), 4),
        "flat_reward": round(float(flat_reward), 4),
    }


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    cells = []
    for pair in A.PAIRS:
        df = A.load_klines(pair, A.HISTORY_START, A.DATA_END)
        slices = A.walk_forward_slices(len(df), A.TRAIN, A.TEST, A.STEP, embargo_bars=A.DEFAULT_EMBARGO_BARS)
        for seed in (42,) + NEW_SEEDS:
            for i in SOURCE_FOLDS:
                ts, te, vs, ve = slices[i]
                if seed == 42:  # from the first ablation run
                    trace = pd.read_csv(f"reports/ablation_single_fee/traces/{pair}_single_fee_fold{i}.csv")
                    flat = json.loads(Path("reports/ablation_single_fee/summary.json").read_text())[pair][
                        "single_fee_vs_do_nothing_by_fold"][i]["flat_reward"]
                else:
                    A.SEED = seed
                    A.MODEL_DIR = Path(f"models/rl/ablation_single_fee/seed{seed}")
                    A.TRACE_DIR = OUT / f"seed{seed}"
                    A.MODEL_DIR.mkdir(parents=True, exist_ok=True)
                    A.TRACE_DIR.mkdir(parents=True, exist_ok=True)
                    model = A.train(pair, A.strict_training_end_date(df.index, te), i)
                    res = A.evaluate(df, vs, ve, model, pair, i)
                    trace, flat = res["trace"], res["reward_flat"]
                stats = cell_stats(trace, flat)
                cells.append({"pair": pair, "fold": i + 1, "seed": seed, **stats})
                print(f"[{pair}] fold {i + 1} seed {seed}: exposure {stats['capital_weighted_exposure_pct']}%, "
                      f"flat beats trained: {stats['flat_reward'] > stats['trained_reward']}", flush=True)
                (OUT / "cells.json").write_text(json.dumps(cells, indent=2))

    frame = pd.DataFrame(cells)
    summary = {
        "design": "fees counted once; seeds 42, 7, 123, 2024, 999; folds 1 and 4; both pairs (20 cells)",
        "do_nothing_beats_trained_cells": int((frame.flat_reward > frame.trained_reward).sum()),
        "cells": len(frame),
        "exposure_pct_by_pair": frame.groupby("pair").capital_weighted_exposure_pct.agg(["mean", "min", "max"]).round(2).to_dict("index"),
        "flat_share_pct_by_pair": frame.groupby("pair").flat_share_pct.mean().round(1).to_dict(),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
