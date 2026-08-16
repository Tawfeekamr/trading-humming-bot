#!/usr/bin/env python3
"""Diagnostic: why is PPO's capital-weighted exposure 4-5%? (batch 3 task 1)

Five diagnostics over the corrected walk-forward folds (all six, both pairs):
  1. action distribution, trained vs randomly-initialised policy
  2. (ceiling audit is in the report — static code values)
  3. realised size / available max, and fraction of active steps >= 90% of max
  4. reward decomposition (PnL-vs-bench, fee, lambda*dd terms)
  5. trained total reward vs permanently-flat policy reward
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.rl.data import load_klines  # noqa: E402
from src.rl.walk_forward import DEFAULT_EMBARGO_BARS, walk_forward_slices  # noqa: E402

WORKER = r'''
import sys, json
sys.path.insert(0, ".")
import numpy as np, pandas as pd
from src.rl.env import EnvConfig, TradingEnv, ACTION_TO_ENGINE_SIZE
from src.rl.evaluate import _run_model
from src.rl.router import PPORouter

test_df = pd.read_csv({csv!r}, index_col=0, parse_dates=True)
warmup = {warmup}
cfg = EnvConfig(window_length=len(test_df), warmup_bars=warmup)
env = TradingEnv(test_df, cfg)

if {untrained}:
    # Randomly-initialised policy: untrained PPO net with the same arch.
    from stable_baselines3 import PPO
    import tempfile, os
    tmp = tempfile.mktemp(suffix=".zip")
    model = PPO("MlpPolicy", env, seed={seed}, verbose=0)
    model.save(tmp)
    router = PPORouter(tmp)
    os.unlink(tmp)
    for ext in (".json",):
        pass
else:
    router = PPORouter({model!r})

# --- run the router, collecting actions, sizes, rewards ---
obs, info = env.reset(seed=42)
actions = []
pos_vals = []
max_notionals = []
rew_pnl, rew_fee, rew_dd = [], [], []
done = False
import src.rl.env as envmod
while not done:
    a = router.predict(obs)
    actions.append(int(a))
    obs, r, term, trunc, info = env.step(a)
    pos_vals.append(float(info["position_value"]))
    # available max notional this bar: engine ceiling x equity
    engine, size_mult = ACTION_TO_ENGINE_SIZE[int(a)]
    if engine == "flat":
        max_notionals.append(0.0)
    elif engine == "grid":
        max_notionals.append(5 * env.config.grid_level_pct * size_mult * info["equity"])
    else:
        max_notionals.append(env.config.max_position_pct * size_mult * info["equity"])
    done = term or trunc

# reward decomposition via a re-run capturing components (same seed)
obs, info = env.reset(seed=42)
done = False
while not done:
    a = router.predict(obs)
    prev_eq = info["equity"]
    prev_dd = env._prev_dd
    obs, r, term, trunc, info = env.step(a)
    eq_ret = (info["equity"] - prev_eq) / max(prev_eq, 1e-8)
    # bench: buy&hold over the same bar
    bi = info["bar_idx"]
    bench = (env._closes[bi] - env._prev_close_prev if False else 0.0)
    # fee + dd terms from env internals of the step just executed
    turnover = info["turnover"]
    fee_term = env.config.fee_rate * (turnover / max(prev_eq, 1e-8))
    dd_step = max(0.0, (env._peak_equity - info["equity"]) / max(env._peak_equity, 1e-8) - prev_dd)
    rew_fee.append(fee_term)
    rew_dd.append(env.config.lambda_dd * dd_step)
    rew_pnl.append(r + fee_term + env.config.lambda_dd * dd_step)  # r = pnl - fee - dd
    done = term or trunc

# flat policy reward on identical timestamps
flat_rew = []
obs, info = env.reset(seed=42)
done = False
fr = 0.0
while not done:
    obs, r, term, trunc, info = env.step(9)  # GO_FLAT
    flat_rew.append(r)
    done = term or trunc

res = {{
    "actions": actions,
    "pos_frac_of_equity": [v / max(e, 1e-9) for v, e in zip(pos_vals, [{{"e": x}}["e"] for x in [info["equity"]] * len(pos_vals)])],
    "n": len(actions),
    "reward_pnl_total": float(np.sum(rew_pnl)),
    "reward_fee_total": float(np.sum(rew_fee)),
    "reward_dd_total": float(np.sum(rew_dd)),
    "reward_total_trained": float(np.sum(rew_pnl) - np.sum(rew_fee) - np.sum(rew_dd)),
    "reward_total_flat": float(np.sum(flat_rew)),
    "max_notionals": max_notionals,
    "pos_values": pos_vals,
}}
print(json.dumps(res))
'''


def main() -> int:
    end = date(2026, 7, 5)
    start = end - timedelta(days=30 * 24)
    warmup = 100
    out = {}

    for pair in ("ETHUSDT", "BNBUSDT"):
        df = load_klines(pair, start, end)
        slices = walk_forward_slices(len(df), 4320, 720, 2160, embargo_bars=DEFAULT_EMBARGO_BARS)
        agg = {
            "actions": [], "pos_over_max": [],
            "rew_pnl": 0.0, "rew_fee": 0.0, "rew_dd": 0.0,
            "rew_trained": 0.0, "rew_flat": 0.0,
            "untrained_actions": [],
        }
        for i, (ts, te, vs, ve) in enumerate(slices):
            model = f"models/rl/_wf_{pair}_slice{i}.zip"
            tmp = Path(f"reports/returns/_diag.csv")
            df.iloc[max(0, vs - warmup):ve].to_csv(tmp)
            for untrained in (False, True):
                script = WORKER.format(csv=str(tmp), model=model, warmup=warmup,
                                       untrained="True" if untrained else "False", seed=42)
                proc = subprocess.run([sys.executable, "-c", script],
                                      capture_output=True, text=True)
                if proc.returncode != 0:
                    print(f"{pair} fold{i} untrained={untrained} FAILED: {proc.stderr[-400:]}", file=sys.stderr)
                    continue
                res = json.loads(proc.stdout.strip().splitlines()[-1])
                if untrained:
                    agg["untrained_actions"].extend(res["actions"])
                    continue
                agg["actions"].extend(res["actions"])
                agg["rew_pnl"] += res["reward_pnl_total"]
                agg["rew_fee"] += res["reward_fee_total"]
                agg["rew_dd"] += res["reward_dd_total"]
                agg["rew_trained"] += res["reward_total_trained"]
                agg["rew_flat"] += res["reward_total_flat"]
                # binding test
                for pv, mx in zip(res["pos_values"], res["max_notionals"]):
                    if mx > 0:
                        agg["pos_over_max"].append(pv / mx)
            tmp.unlink(missing_ok=True)

        def dist(actions):
            counts = np.bincount(actions, minlength=10)
            names = [f"{e}_{s}" for e, s in
                     [("grid", .5), ("grid", 1), ("grid", 1.5), ("trend", .5), ("trend", 1),
                      ("trend", 1.5), ("swing", .5), ("swing", 1), ("swing", 1.5), ("FLAT", 0)]]
            return {n: float(c / len(actions)) for n, c in zip(names, counts) if c}

        pom = np.array(agg["pos_over_max"]) if agg["pos_over_max"] else np.zeros(0)
        active = pom[pom > 0]
        out[pair] = {
            "n_steps": len(agg["actions"]),
            "trained_action_dist": dist(agg["actions"]),
            "untrained_action_dist": dist(agg["untrained_actions"]),
            "flat_share_trained": float(np.mean([a == 9 for a in agg["actions"]])),
            "flat_share_untrained": float(np.mean([a == 9 for a in agg["untrained_actions"]])),
            "binding": {
                "mean_pos_over_max": float(np.mean(active)) if len(active) else None,
                "median_pos_over_max": float(np.median(active)) if len(active) else None,
                "frac_active_ge_90pct_of_max": float(np.mean(active >= 0.9)) if len(active) else None,
            },
            "reward": {
                "pnl_term_total": agg["rew_pnl"],
                "fee_term_total": agg["rew_fee"],
                "lambda_dd_term_total": agg["rew_dd"],
                "lambda_value": 0.5,
                "trained_total": agg["rew_trained"],
                "flat_total": agg["rew_flat"],
            },
        }
        print(f"\n=== {pair} ===")
        print("trained dist:", {k: round(v, 3) for k, v in out[pair]["trained_action_dist"].items()})
        print("untrained dist:", {k: round(v, 3) for k, v in out[pair]["untrained_action_dist"].items()})
        print("flat share: trained", round(out[pair]["flat_share_trained"], 3),
              "| untrained", round(out[pair]["flat_share_untrained"], 3))
        print("binding:", out[pair]["binding"])
        print("reward:", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in out[pair]["reward"].items()})

    Path("reports/exposure_diagnosis.json").write_text(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
