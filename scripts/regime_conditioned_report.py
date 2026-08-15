#!/usr/bin/env python3
"""Regime-conditioned performance and false-DANGER cost (batch 2 task 5).

For each pair, over the corrected walk-forward test windows, using the
FOLD-SPECIFIC RF regime classifier (the same baseline the PPO competes
against):

1. Per-bar regime-conditioned table (RANGING/TRENDING/DANGER as predicted):
   bar count, mean forward return, mean forward drawdown.
2. The diagnostic cross-tab: predicted regime vs realised forward outcome
   over the label horizon (24 bars):
   - predicted DANGER but forward return positive  -> opportunity cost
   - predicted RANGING/TRENDING but forward max drawdown <= -3% -> missed danger
3. Total opportunity cost of false DANGER signals in percentage points of
   foregone return (bars where the classifier said DANGER, market went up,
   cost = foregone return).
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

HORIZON = 24  # label horizon in bars
RET_THR = 0.02
DD_THR = -0.03


def forward_outcomes(df: pd.DataFrame) -> pd.DataFrame:
    """Realised forward 24-bar return and forward max drawdown per bar."""
    close = df["close"].to_numpy(dtype=np.float64)
    n = len(close)
    fwd_ret = np.full(n, np.nan)
    fwd_dd = np.full(n, np.nan)
    for i in range(n - HORIZON):
        window = close[i : i + HORIZON + 1]
        fwd_ret[i] = window[-1] / window[0] - 1.0
        running_peak = np.maximum.accumulate(window)
        fwd_dd[i] = float(np.min(window / running_peak - 1.0))
    return pd.DataFrame(
        {"fwd_return": fwd_ret, "fwd_maxdd": fwd_dd}, index=df.index
    )


def main() -> int:
    end = date(2026, 7, 5)
    start = end - timedelta(days=30 * 24)
    warmup = 100
    out = {}

    for pair in ("ETHUSDT", "BNBUSDT"):
        df = load_klines(pair, start, end)
        slices = walk_forward_slices(len(df), 4320, 720, 2160, embargo_bars=DEFAULT_EMBARGO_BARS)
        preds, frs, fdds = [], [], []
        for i, (ts, te, vs, ve) in enumerate(slices):
            rf_model = f"models/rl/_wf_{pair}_fold_rf_{ts}_{te}.pkl"
            test_df = df.iloc[vs:ve]  # pure test bars (no warmup for outcomes)
            tmp = Path(f"reports/returns/_reg_{pair}_{i}.csv")
            test_df.to_csv(tmp)
            script = (
                "import sys, json; sys.path.insert(0, '.')\n"
                "import pandas as pd, numpy as np\n"
                "from src.data.feature_engineering import calculate_technical_features\n"
                "from src.data.feature_contract import MARKET_FEATURE_COLS\n"
                "from src.ml.regime_classifier import RegimeClassifier\n"
                f"df = pd.read_csv({str(tmp)!r}, index_col=0, parse_dates=True)\n"
                "feats = calculate_technical_features(df)\n"
                "X = feats[MARKET_FEATURE_COLS]\n"
                f"clf = RegimeClassifier(model_path={rf_model!r}, model_type='random_forest')\n"
                "clf.load_model()\n"
                "probs = [clf.predict_proba_full(X.iloc[[k]]) for k in range(len(X))]\n"
                "preds = [max(p, key=p.get) for p in probs]\n"
                f"open({str(tmp.with_suffix('.pred'))!r}, 'w').write(json.dumps([int(x) for x in preds]))\n"
            )
            proc = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
            if proc.returncode != 0:
                print(f"{pair} fold{i} predict FAILED: {proc.stderr[-300:]}", file=sys.stderr)
                continue
            p = np.array(json.loads(tmp.with_suffix(".pred").read_text()))
            # predictions valid after feature warmup (~50 bars); outcomes
            # valid until HORIZON bars before end — align on the intersection
            fo = forward_outcomes(test_df)
            k = min(len(p), len(fo))
            valid = ~np.isnan(fo["fwd_return"].to_numpy()[:k])
            preds.append(p[:k][valid])
            frs.append(fo["fwd_return"].to_numpy()[:k][valid])
            fdds.append(fo["fwd_maxdd"].to_numpy()[:k][valid])
            tmp.unlink(missing_ok=True)
            tmp.with_suffix(".pred").unlink(missing_ok=True)

        pred = np.concatenate(preds); fr = np.concatenate(frs); fdd = np.concatenate(fdds)
        names = {0: "RANGING", 1: "TRENDING", 2: "DANGER"}
        table = {}
        for cls in (0, 1, 2):
            m = pred == cls
            table[names[cls]] = {
                "bars": int(m.sum()),
                "share": float(m.mean()),
                "mean_fwd_return_pct": float(np.mean(fr[m]) * 100),
                "mean_fwd_maxdd_pct": float(np.mean(fdd[m]) * 100),
                "fwd_return_positive_rate": float(np.mean(fr[m] > 0)),
                "fwd_dd_beyond_3pct_rate": float(np.mean(fdd[m] <= DD_THR)),
            }
        false_danger = (pred == 2) & (fr > 0)
        missed_danger = (pred != 2) & (fdd <= DD_THR)
        cost_pp = float(np.sum(fr[false_danger]) * 100)  # foregone pp summed over bars
        cost_per_danger_bar_pp = float(np.mean(fr[pred == 2]) * 100) if (pred == 2).any() else 0.0
        out[pair] = {
            "n_bars": int(len(pred)),
            "by_predicted_regime": table,
            "false_danger": {
                "bars": int(false_danger.sum()),
                "rate_within_danger": float(false_danger.sum() / max((pred == 2).sum(), 1)),
                "total_foregone_return_pp": cost_pp,
                "mean_foregone_per_bar_pp": float(np.mean(fr[false_danger]) * 100) if false_danger.any() else 0.0,
            },
            "missed_danger": {
                "bars": int(missed_danger.sum()),
                "rate_within_nondanger": float(missed_danger.sum() / max((pred != 2).sum(), 1)),
                "mean_fwd_return_when_missed_pct": float(np.mean(fr[missed_danger]) * 100) if missed_danger.any() else 0.0,
            },
            "danger_share": float((pred == 2).mean()),
        }
        print(f"\n=== {pair} ===")
        print(json.dumps(table, indent=2))
        print(f"false DANGER: {false_danger.sum()} bars "
              f"({false_danger.sum()/max((pred==2).sum(),1)*100:.0f}% of DANGER calls), "
              f"total foregone {cost_pp:.1f} pp")
        print(f"missed DANGER: {missed_danger.sum()} bars "
              f"({missed_danger.sum()/max((pred!=2).sum(),1)*100:.1f}% of non-DANGER)")

    Path("reports/regime_conditioned.json").write_text(json.dumps(out, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
