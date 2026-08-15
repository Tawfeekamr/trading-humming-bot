#!/usr/bin/env python3
"""Measure BOTH exposure definitions for every strategy/pair (batch 2 task 2).

time_in_market        — fraction of bars with engine != "flat"
capital_weighted_exposure — mean(|position notional| / equity) per bar

Re-runs the cached PPO/fold-RF models over each fold (models reused; this
only re-collects per-bar info including the new position_value field) and
writes reports/exposure_definitions_<PAIR>.json.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.rl.data import load_klines  # noqa: E402
from src.rl.walk_forward import (  # noqa: E402
    DEFAULT_EMBARGO_BARS,
    walk_forward_slices,
)


WORKER = r'''
import sys, json
sys.path.insert(0, ".")
import numpy as np, pandas as pd
from src.rl.env import EnvConfig, TradingEnv
from src.rl.evaluate import _run_model
from src.rl.router import PPORouter, SupervisedRegimeRouter

test_df = pd.read_csv(sys.argv[1], index_col=0, parse_dates=True)
model, kind, warmup = sys.argv[2], sys.argv[3], int(sys.argv[4])
cfg = EnvConfig(window_length=len(test_df), warmup_bars=warmup)
env = TradingEnv(test_df, cfg)
router = PPORouter(model) if kind == "ppo" else SupervisedRegimeRouter(model)
out = _run_model(env, router)
ret = np.asarray(out["returns_array"])
equity = np.asarray(out["equity_curve"])[1:]
# position_value is collected inside _run_model's info; recompute exposure
# from the summary fields instead
res = {
    "time_in_market": float(out["time_in_market"]),
    "capital_weighted_exposure": float(out["capital_weighted_exposure"]),
    "n": int(len(ret)),
}
print(json.dumps(res))
'''


def main() -> int:
    from datetime import date, timedelta

    end = date(2026, 7, 5)
    start = end - timedelta(days=30 * 24)
    warmup = 100

    results = {}
    for pair in ("ETHUSDT", "BNBUSDT"):
        df = load_klines(pair, start, end)
        slices = walk_forward_slices(len(df), 4320, 720, 2160, embargo_bars=DEFAULT_EMBARGO_BARS)
        pair_res = {"ppo": [], "rf": [], "ta": []}
        for i, (ts, te, vs, ve) in enumerate(slices):
            test_df = df.iloc[max(0, vs - warmup):ve]
            tmp = Path(f"reports/returns/_exp_{pair}_{i}.csv")
            tmp.parent.mkdir(parents=True, exist_ok=True)
            test_df.to_csv(tmp)
            ppo_model = f"models/rl/_wf_{pair}_slice{i}.zip"
            rf_model = f"models/rl/_wf_{pair}_fold_rf_{ts}_{te}.pkl"
            for kind, model in (("ppo", ppo_model), ("rf", rf_model)):
                proc = subprocess.run(
                    [sys.executable, "-c", WORKER, str(tmp), model, kind, str(warmup)],
                    capture_output=True, text=True,
                )
                if proc.returncode != 0:
                    print(f"{pair} fold{i} {kind} FAILED: {proc.stderr[-300:]}", file=sys.stderr)
                    continue
                pair_res[kind].append(json.loads(proc.stdout.strip().splitlines()[-1]))
            tmp.unlink(missing_ok=True)
        # TA: always fully invested
        n_bars = sum(r["n"] for r in pair_res["ppo"])
        agg = {}
        for kind in ("ppo", "rf"):
            if pair_res[kind]:
                agg[kind] = {
                    "time_in_market": float(np.mean([r["time_in_market"] for r in pair_res[kind]])),
                    "capital_weighted_exposure": float(np.mean([r["capital_weighted_exposure"] for r in pair_res[kind]])),
                }
        agg["ta"] = {"time_in_market": 1.0, "capital_weighted_exposure": 1.0}
        results[pair] = agg
        print(f"{pair}: {json.dumps(agg, indent=2)}")

    Path("reports/exposure_definitions.json").write_text(json.dumps(results, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
