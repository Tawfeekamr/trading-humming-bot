#!/usr/bin/env python3
"""Corrected false-DANGER measures (batch 3 task 2).

Replaces the multiply-counted summed figure (748 bars x overlapping 24-bar
forward windows -> 2,243 'pp' on a window where B&H returned 44.8%) with:

  1. mean forward 24-bar return per false-DANGER signal (per-bar, no sum)
  2. de-overlapped block analysis: partition the evaluation into
     non-overlapping 24-bar blocks; classify each block by its ENTRY bar's
     predicted regime; report mean/total return of DANGER-flagged vs
     non-flagged blocks
  3. the realised portfolio-level cost: gated-strategy return minus
     buy-and-hold over the same span (already computed in the walk-forward)
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

HORIZON = 24


def main() -> int:
    end = date(2026, 7, 5)
    start = end - timedelta(days=30 * 24)
    out = {}

    for pair in ("ETHUSDT", "BNBUSDT"):
        df = load_klines(pair, start, end)
        slices = walk_forward_slices(len(df), 4320, 720, 2160, embargo_bars=DEFAULT_EMBARGO_BARS)
        preds, closes = [], []
        for i, (ts, te, vs, ve) in enumerate(slices):
            rf_model = f"models/rl/_wf_{pair}_fold_rf_{ts}_{te}.pkl"
            test_df = df.iloc[vs:ve]
            tmp = Path(f"reports/returns/_b3_{pair}_{i}.csv")
            test_df.to_csv(tmp)
            script = (
                "import sys, json; sys.path.insert(0, '.')\n"
                "import pandas as pd, numpy as np\n"
                "from src.data.feature_engineering import calculate_technical_features\n"
                "from src.data.feature_contract import MARKET_FEATURE_COLS\n"
                "from src.ml.regime_classifier import RegimeClassifier\n"
                f"df = pd.read_csv({str(tmp)!r}, index_col=0, parse_dates=True)\n"
                "X = calculate_technical_features(df)[MARKET_FEATURE_COLS]\n"
                f"clf = RegimeClassifier(model_path={rf_model!r}, model_type='random_forest')\n"
                "clf.load_model()\n"
                "probs = [clf.predict_proba_full(X.iloc[[k]]) for k in range(len(X))]\n"
                f"open({str(tmp.with_suffix('.pred'))!r}, 'w').write(json.dumps([int(max(p, key=p.get)) for p in probs]))\n"
            )
            proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
            if proc.returncode != 0:
                print(f"{pair} fold{i} FAILED", file=sys.stderr)
                continue
            p = np.array(json.loads(tmp.with_suffix(".pred").read_text()))
            c = test_df["close"].to_numpy()
            k = min(len(p), len(c))
            preds.append(p[:k]); closes.append(c[:k])
            tmp.unlink(missing_ok=True); tmp.with_suffix(".pred").unlink(missing_ok=True)

        pred = np.concatenate(preds); close = np.concatenate(closes)
        n = len(close)

        # 1. per-signal mean forward return (no summing)
        fwd = np.full(n, np.nan)
        for i in range(n - HORIZON):
            fwd[i] = close[i + HORIZON] / close[i] - 1.0
        false_danger = (pred == 2) & (fwd > 0)
        fd_mean = float(np.mean(fwd[false_danger]) * 100) if false_danger.any() else 0.0
        danger_mean = float(np.nanmean(fwd[pred == 2]) * 100)

        # 2. non-overlapping 24-bar blocks, classified by ENTRY bar regime
        blocks = []
        for b0 in range(0, n - HORIZON, HORIZON):
            b1 = b0 + HORIZON
            blocks.append({
                "entry_regime": int(pred[b0]),
                "ret": close[b1] / close[b0] - 1.0,
            })
        danger_blocks = [b["ret"] for b in blocks if b["entry_regime"] == 2]
        other_blocks = [b["ret"] for b in blocks if b["entry_regime"] != 2]

        # 3. portfolio-level cost from the main run
        wf = json.loads(Path(f"reports/rl_walk_forward_{pair}.json").read_text())
        rf_ret = wf["metrics"]["rf"]["total_return"]
        bh_ret = wf["metrics"]["ta"]["total_return"]

        out[pair] = {
            "n_bars": n,
            "per_signal": {
                "false_danger_bars": int(false_danger.sum()),
                "mean_fwd_return_per_false_danger_signal_pct": fd_mean,
                "mean_fwd_return_on_danger_bars_pct": danger_mean,
            },
            "de_overlapped_blocks_24bar": {
                "n_blocks": len(blocks),
                "danger_flagged": {
                    "n": len(danger_blocks),
                    "mean_return_pct": float(np.mean(danger_blocks) * 100),
                    "total_compounded_return_pct": float((np.prod([1 + r for r in danger_blocks]) - 1) * 100),
                },
                "not_flagged": {
                    "n": len(other_blocks),
                    "mean_return_pct": float(np.mean(other_blocks) * 100),
                    "total_compounded_return_pct": float((np.prod([1 + r for r in other_blocks]) - 1) * 100),
                },
                "mean_return_gap_pp_per_block": float((np.mean(other_blocks) - np.mean(danger_blocks)) * 100),
            },
            "portfolio_level_cost": {
                "gated_strategy_return_pct": rf_ret * 100,
                "buy_hold_return_pct": bh_ret * 100,
                "gap_pp": (rf_ret - bh_ret) * 100,
                "note": "RF-gated strategy minus buy-and-hold over identical span",
            },
        }
        print(f"\n=== {pair} ===")
        print(json.dumps(out[pair], indent=2))

    Path("reports/false_danger_corrected.json").write_text(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
