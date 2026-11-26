"""Single-fee ablation: does PPO still abstain when fees are counted only once?

The frozen evaluation's reward counts transaction costs twice: equity is net of
fees, and the reward subtracts ``fee_rate * turnover`` again. This script
retrains PPO on exactly the same walk-forward folds, data window, seed (42),
timesteps (1M) and hyper-parameters, with ONE change: the explicit fee term is
removed (``EnvConfig.reward_fee_term=False``).

Nothing frozen is touched:
  * models  -> models/rl/ablation_single_fee/
  * results -> reports/ablation_single_fee/
The baseline numbers are read from the committed traces in
reports/exposure_traces/, produced by the same evaluation worker
(scripts/diagnose_exposure.py), so the comparison is like-for-like.

Run (about one hour on an Apple-silicon laptop; resumable):
    .mlvenv/bin/python scripts/ablation_single_fee.py
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.rl.data import load_klines  # noqa: E402
from src.rl.walk_forward import (  # noqa: E402
    DEFAULT_EMBARGO_BARS,
    strict_training_end_date,
    walk_forward_slices,
)

PAIRS = ("ETHUSDT", "BNBUSDT")
DATA_END = date(2026, 7, 5)                 # pinned, as in the frozen run
HISTORY_START = DATA_END - timedelta(days=30 * 24)
TRAIN, TEST, STEP = 4320, 720, 2160
TIMESTEPS = 1_000_000
SEED = 42
WARMUP = 100
MODEL_DIR = Path("models/rl/ablation_single_fee")
OUT_DIR = Path("reports/ablation_single_fee")
TRACE_DIR = OUT_DIR / "traces"
LABELS = ["grid_0.5", "grid_1", "grid_1.5", "trend_0.5", "trend_1", "trend_1.5",
          "swing_0.5", "swing_1", "swing_1.5", "FLAT"]

WORKER = r'''
import sys, json
sys.path.insert(0, ".")
import numpy as np, pandas as pd
from src.rl.env import EnvConfig, TradingEnv
from src.rl.router import PPORouter

test_df = pd.read_csv({csv!r}, index_col=0, parse_dates=True)
cfg = EnvConfig(window_length=len(test_df), warmup_bars={warmup}, reward_fee_term=False)
env = TradingEnv(test_df, cfg)
router = PPORouter({model!r})

rows, rewards = [], []
obs, info = env.reset(seed=42)
done = False
while not done:
    action = int(router.predict(obs))
    prev_eq, prev_dd = info["equity"], env._prev_dd
    obs, r, term, trunc, info = env.step(action)
    state = env._engine_state
    crossed = 0
    if info["engine"] == "grid" and state.get("deployed"):
        bi = int(info["bar_idx"])
        lo, hi = float(env._lows[bi]), float(env._highs[bi])
        crossed = sum(lo <= float(l) <= hi for l in state.get("buy_levels", []))
    eq = float(info["equity"])
    dd_step = max(0.0, (env._peak_equity - eq) / max(env._peak_equity, 1e-8) - prev_dd)
    rows.append({{
        "timestamp": test_df.index[int(info["bar_idx"])].isoformat(),
        "action_index": action,
        "position_value": float(info["position_value"]),
        "equity": eq,
        "position_value/equity": float(info["position_value"]) / max(eq, 1e-9),
        "turnover_this_bar": float(info.get("turnover", 0.0)),
        "grid_levels_crossed_this_bar": int(crossed),
        "reward": float(r),
        "reward_dd_term": float(cfg.lambda_dd * dd_step),
    }})
    done = term or trunc

flat = []
env.reset(seed=42)
done = False
while not done:
    _, r, term, trunc, _ = env.step(9)
    flat.append(r)
    done = term or trunc
print(json.dumps({{"rows": rows, "reward_flat": float(np.sum(flat))}}))
'''


def train(pair: str, train_end, i: int) -> Path:
    model = MODEL_DIR / f"_wf_{pair}_slice{i}.zip"
    sidecar = model.with_suffix(".json")
    if model.exists() and sidecar.exists():
        meta = json.loads(sidecar.read_text())
        if (meta.get("hyperparams", {}).get("reward_fee_term") is False
                and str(meta.get("data_end", "")).startswith(str(train_end))
                and int(meta.get("timesteps", 0)) == TIMESTEPS):
            print(f"  reuse {model}", flush=True)
            return model
    months = max(1, -(-TRAIN // 720))       # same rounding as walk_forward
    cmd = [sys.executable, "-m", "src.rl.agents.ppo_trainer", "--pair", pair,
           "--train-end", str(train_end), "--months", str(months),
           "--timesteps", str(TIMESTEPS), "--seed", str(SEED),
           "--single-fee", "--output", str(model)]
    print("  train:", " ".join(cmd), flush=True)
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL)
    return model


def evaluate(df: pd.DataFrame, vs: int, ve: int, model: Path, pair: str, i: int) -> dict:
    tmp = OUT_DIR / "_slice.csv"
    df.iloc[max(0, vs - WARMUP):ve].to_csv(tmp)
    code = WORKER.format(csv=str(tmp), model=str(model), warmup=WARMUP)
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    tmp.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"{pair} fold {i} evaluation failed: {proc.stderr[-600:]}")
    res = json.loads(proc.stdout.strip().splitlines()[-1])
    trace = pd.DataFrame(res["rows"])
    trace.insert(1, "action_label", [LABELS[a] for a in trace.action_index])
    trace.to_csv(TRACE_DIR / f"{pair}_single_fee_fold{i}.csv", index=False)
    return {"trace": trace, "reward_flat": res["reward_flat"]}


def summarise(traces: list[pd.DataFrame], label_col: str = "action_label") -> dict:
    """Same quantities as the thesis: exposure, action mix, return, drawdown."""
    all_t = pd.concat(traces, ignore_index=True)
    fam = all_t[label_col].str.split("_").str[0].replace({"trend": "immediate", "swing": "immediate"})
    share = (fam.value_counts(normalize=True) * 100).round(2).to_dict()
    grid = all_t[all_t[label_col].str.startswith("grid")]
    total, mdds = 1.0, []
    for t in traces:
        eq = np.concatenate([[10_000.0], t["equity"].to_numpy()])
        r = eq[1:] / eq[:-1] - 1
        total *= np.prod(1 + r)
        e = np.cumprod(1 + r)
        mdds.append(float((1 - e / np.maximum.accumulate(e)).max()))
    return {
        "capital_weighted_exposure_pct": round(float(all_t["position_value/equity"].abs().mean() * 100), 2),
        "time_in_market_active_bars_pct": round(float((all_t[label_col] != "FLAT").mean() * 100), 2),
        "flat_share_pct": share.get("FLAT", 0.0),
        "grid_share_pct": share.get("grid", 0.0),
        "trend_swing_share_pct": share.get("immediate", 0.0),
        "grid_fill_rate_pct": round(float((grid.grid_levels_crossed_this_bar > 0).mean() * 100), 2) if len(grid) else None,
        "total_return_pct": round(float(total - 1) * 100, 2),
        "median_fold_max_drawdown": round(float(np.median(mdds)), 3),
        "fold_max_drawdowns": [round(m, 3) for m in mdds],
    }


def main() -> int:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    TRACE_DIR.mkdir(parents=True, exist_ok=True)
    out = {"setup": {
        "change": "explicit fee term removed from the reward (fees counted once, via net equity)",
        "unchanged": "folds, data end 2026-07-05, seed 42, 1M timesteps, PPO hyper-parameters, lambda_dd 0.5, fee_rate 0.001",
    }}
    for pair in PAIRS:
        df = load_klines(pair, HISTORY_START, DATA_END)
        slices = walk_forward_slices(len(df), TRAIN, TEST, STEP, embargo_bars=DEFAULT_EMBARGO_BARS)
        print(f"[{pair}] {len(slices)} folds", flush=True)
        traces, flat_vs = [], []
        for i, (ts, te, vs, ve) in enumerate(slices):
            model = train(pair, strict_training_end_date(df.index, te), i)
            res = evaluate(df, vs, ve, model, pair, i)
            traces.append(res["trace"])
            flat_vs.append({"fold": i + 1, "trained_reward": round(float(res["trace"].reward.sum()), 4),
                            "flat_reward": round(res["reward_flat"], 4)})
            print(f"  fold {i + 1}: exposure {res['trace']['position_value/equity'].abs().mean() * 100:.2f}%"
                  f", FLAT {(res['trace'].action_label == 'FLAT').mean() * 100:.1f}%", flush=True)
        base = [pd.read_csv(f"reports/exposure_traces/{pair}_trained_fold{i}.csv") for i in range(len(slices))]
        out[pair] = {
            "single_fee": summarise(traces),
            "frozen_double_fee": summarise(base),
            "single_fee_vs_do_nothing_by_fold": flat_vs,
            "do_nothing_beats_single_fee_in_folds": sum(f["flat_reward"] > f["trained_reward"] for f in flat_vs),
        }
        (OUT_DIR / "summary.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
