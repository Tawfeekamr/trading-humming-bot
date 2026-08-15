#!/usr/bin/env python3
"""PPO seed sensitivity (batch 2 task 4).

RESTRICTION (documented): full 6-fold x 2-pair retraining x 5 seeds = 60
trainings was infeasible in one session (~5 CPU-minutes each). We run
5 seeds x 2 folds x 2 pairs = 20 trainings. Folds 0 and 3 chosen to span
the market-direction split found in batch 2 task 7 (fold 0 ETH: B&H
-14.4%; fold 3 ETH: B&H -7.1%; both BNB folds mixed).

Per seed: total return, MaxDD (canonical), Sharpe (both variants),
time-in-market, capital-weighted exposure, paired mean-diff stat vs the
fold-specific RF baseline.
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, ".")

from src.rl.data import load_klines
from src.rl.evaluate import newey_west_lag
from src.rl.risk_stats import invested_bars_sharpe, max_drawdown, sharpe_annualized
from src.rl.walk_forward import DEFAULT_EMBARGO_BARS, walk_forward_slices

SEEDS = [42, 7, 123, 2024, 999]
FOLDS = [0, 3]


def hac_var(d, lag):
    dm = np.mean(d); n = len(d)
    v = np.var(d, ddof=0)
    for k in range(1, lag + 1):
        if n > k:
            v += 2 * (1 - k / (lag + 1)) * np.sum((d[:-k]-dm)*(d[k:]-dm)) / n
    return max(v, np.var(d, ddof=0))


def main() -> int:
    from datetime import timezone

    end = date(2026, 7, 5)
    start = end - timedelta(days=30 * 24)
    warmup = 100
    out = {}

    for pair in ("ETHUSDT", "BNBUSDT"):
        df = load_klines(pair, start, end)
        slices = walk_forward_slices(len(df), 4320, 720, 2160, embargo_bars=DEFAULT_EMBARGO_BARS)
        pair_out = []
        for fold_i in FOLDS:
            ts, te, vs, ve = slices[fold_i]
            test_df = df.iloc[max(0, vs - warmup):ve]
            tmp_csv = Path(f"reports/returns/_seedtmp.csv")
            tmp_csv.parent.mkdir(parents=True, exist_ok=True)
            test_df.to_csv(tmp_csv)
            rf_model = f"models/rl/_wf_{pair}_fold_rf_{ts}_{te}.pkl"

            # RF baseline once per fold
            rf_csv = Path(f"reports/returns/_seedres_rf_{pair}_{fold_i}.csv")
            if not rf_csv.exists():
                run_worker(tmp_csv, rf_model, "rf", warmup, tag=f"rf_{pair}_{fold_i}")

            for seed in SEEDS:
                model = f"models/rl/_seed_{pair}_f{fold_i}_s{seed}.zip"
                sidecar = Path(model).with_suffix(".json")
                if not (Path(model).exists() and sidecar.exists()):
                    train_end = str(date.fromisoformat(str(df.index[te].date())) - timedelta(days=1))
                    subprocess.run(
                        [sys.executable, "-m", "src.rl.agents.ppo_trainer",
                         "--pair", pair, "--train-end", train_end,
                         "--months", "6", "--timesteps", "1000000",
                         "--seed", str(seed), "--output", model],
                        check=True, capture_output=True, text=True,
                    )
                ppo_csv = Path(f"reports/returns/_seedres_ppo_{pair}_{fold_i}_{seed}.csv")
                run_worker(tmp_csv, model, "ppo", warmup, tag=f"ppo_{pair}_{fold_i}_{seed}")
                ppo_r = pd.read_csv(ppo_csv, index_col=0)["return"].to_numpy()
                rf_r = pd.read_csv(rf_csv, index_col=0)["return"].to_numpy()
                n = min(len(ppo_r), len(rf_r))
                d = ppo_r[:n] - rf_r[:n]
                lag = newey_west_lag(n)
                hv = hac_var(d, lag)
                stat = float(np.mean(d) / np.sqrt(hv / n)) if hv > 0 else 0.0

                # exposure fields from the worker summary
                summary = json.loads(ppo_csv.with_suffix(".json").read_text()) if ppo_csv.with_suffix(".json").exists() else {}
                pair_out.append({
                    "fold": fold_i, "seed": seed,
                    "total_return_pct": round(float((np.cumprod(1+ppo_r)[-1]-1)*100), 2),
                    "max_drawdown": round(float(max_drawdown(ppo_r)), 4),
                    "sharpe_all_bars": round(float(sharpe_annualized(ppo_r)), 3),
                    "sharpe_invested": round(invested_bars_sharpe(ppo_r)[0], 3),
                    "time_in_market": summary.get("time_in_market"),
                    "capital_weighted_exposure": summary.get("capital_weighted_exposure"),
                    "paired_stat_vs_rf": round(stat, 3),
                })
                print(f"{pair} fold{fold_i} seed{seed}: "
                      f"ret={pair_out[-1]['total_return_pct']}% dd={pair_out[-1]['max_drawdown']} "
                      f"stat={stat:.3f}", flush=True)

        out[pair] = pair_out

    # across-seed distribution per pair
    summary = {}
    for pair, rows in out.items():
        m = {}
        for key in ("total_return_pct", "max_drawdown", "sharpe_all_bars",
                    "paired_stat_vs_rf", "time_in_market"):
            vals = [r[key] for r in rows if r[key] is not None]
            m[key] = {"mean": float(np.mean(vals)), "sd": float(np.std(vals)),
                      "min": float(np.min(vals)), "max": float(np.max(vals))}
        summary[pair] = m
    Path("reports/seed_sensitivity.json").write_text(
        json.dumps({"per_seed": out, "across_seed": summary}, indent=2, sort_keys=True))
    print(json.dumps(summary, indent=2))
    return 0


WORKER_TMPL = r'''
import sys, json
sys.path.insert(0, ".")
import pandas as pd, numpy as np
from src.rl.env import EnvConfig, TradingEnv
from src.rl.evaluate import _run_model
from src.rl.router import PPORouter, SupervisedRegimeRouter

test_df = pd.read_csv({csv!r}, index_col=0, parse_dates=True)
cfg = EnvConfig(window_length=len(test_df), warmup_bars={warmup})
env = TradingEnv(test_df, cfg)
router = PPORouter({model!r}) if {is_ppo} else SupervisedRegimeRouter({model!r})
out = _run_model(env, router)
pd.Series(np.asarray(out["returns_array"]), name="return",
          index=out["timestamps"] if out.get("timestamps") is not None else None
).to_csv({out_csv!r}, index_label="timestamp")
open({sum_json!r}, "w").write(json.dumps({{
    "time_in_market": float(out["time_in_market"]),
    "capital_weighted_exposure": float(out["capital_weighted_exposure"]),
}}))
'''


def run_worker(csv, model, kind, warmup, tag):
    out_csv = Path(f"reports/returns/_seedres_{tag}.csv")
    script = WORKER_TMPL.format(csv=str(csv), model=model, warmup=warmup,
                                is_ppo="True" if kind == "ppo" else "False",
                                out_csv=str(out_csv),
                                sum_json=str(out_csv.with_suffix(".json")))
    subprocess.run([sys.executable, "-c", script], check=True, capture_output=True, text=True)


if __name__ == "__main__":
    sys.exit(main())
