#!/usr/bin/env python3
"""OOS evaluation for the regime classifier on a held-out window.

Computes the TRUE now-cast label on the OOS window (deterministic from past
data) and reports accuracy, calibrated confidence, class mix, per-class recall,
and an OLD-vs-NEW comparison. Run under conda (needs pandas_ta)::

    /opt/anaconda3/bin/python scripts/eval_regime_oos.py [PAIRUSDT]
"""
from __future__ import annotations
import sys
import numpy as np
import pandas as pd

sys.path.insert(0, ".")
from datetime import date
from collections import Counter
from src.rl.data import load_klines
from src.data.feature_engineering import calculate_technical_features
from src.data.label_generation import generate_regime_labels_nowcast
from src.rl.features import MARKET_FEATURE_COLS
from src.ml.regime_classifier import RegimeClassifier
from sklearn.metrics import accuracy_score

NAMES = {0: "ranging", 1: "trending", 2: "danger"}
OOS_START = pd.Timestamp("2026-05-15", tz="UTC")
OOS_END = pd.Timestamp("2026-07-17", tz="UTC")


def load_xy(pair):
    bars = load_klines(pair, date(2026, 4, 1), date(2026, 7, 17))
    feats = calculate_technical_features(bars.copy())
    lab = generate_regime_labels_nowcast(feats).loc[OOS_START:OOS_END]
    X = lab[MARKET_FEATURE_COLS]
    y = lab["regime_label"]
    m = X.notna().all(axis=1).to_numpy()
    return X[m], y[m]


def evaluate(path, label, X, y):
    c = RegimeClassifier(model_path=path, model_type="random_forest")
    c.load_model()
    probs = [c.predict_proba_full(X.iloc[[i]]) for i in range(len(X))]
    regs = np.array([max(p, key=p.get) for p in probs])
    confs = np.array([p[r] for p, r in zip(probs, regs)])
    yv = y.to_numpy()
    ym = yv >= 0
    acc = accuracy_score(yv[ym], regs[ym]) if ym.sum() else float("nan")
    if len(confs) and ym.sum():
        hi = confs >= np.percentile(confs, 75)
        sub = ym & hi
        hi_acc = accuracy_score(yv[sub], regs[sub]) if sub.sum() else float("nan")
    else:
        hi_acc = float("nan")
    rec = {}
    for k in (1, 2):
        sel = (yv == k) & ym
        rec[k] = (regs[sel] == k).mean() if sel.sum() else float("nan")
    print(f"\n=== {label} ===\n  {path}")
    print(f"  calibrated: {c.calibrated_model is not None}")
    print(f"  accuracy: {acc:.3f}  (majority baseline ~0.55)")
    print(f"  confidence: median={np.median(confs):.3f} mean={confs.mean():.3f} | "
          f"top-quartile-conf accuracy={hi_acc:.3f}")
    print(f"  predicted mix: " + ", ".join(
        f"{NAMES[int(k)]}={v}({v / len(regs) * 100:.0f}%)"
        for k, v in sorted(Counter(regs).items())))
    print(f"  recall: trending={rec[1]:.2f} danger={rec[2]:.2f}")


def main():
    pair = sys.argv[1] if len(sys.argv) > 1 else "ETHUSDT"
    slug = pair.replace("USDT", "-USDT")
    X, y = load_xy(pair)
    yl = y[y >= 0]
    print(f"OOS {pair} {OOS_START.date()}->{OOS_END.date()}: {len(X)} bars | TRUE mix: "
          + ", ".join(f"{NAMES[int(k)]}={v}" for k, v in sorted(Counter(yl.to_numpy()).items())))
    evaluate(f"models/_pre_retrain_backup_20260719/regime_{slug}_clean.pkl",
             "OLD (forward label, depth-5)", X, y)
    evaluate(f"models/regime_{slug}_clean.pkl",
             "NEW (now-cast, deeper+calibrated)", X, y)


if __name__ == "__main__":
    main()
