"""Promote a freshly trained regime model only if it beats the live one on unseen data.

Used by the monthly retraining workflow (.github/workflows/retrain.yml).
The candidate is trained with ``--train-end`` set ``--holdout-days`` before
today, so the most recent days are unseen by it. Both the candidate and the
current live model are scored on exactly those days, with the same now-cast
labels the trainer uses. The candidate replaces the live model (``.pkl`` and
its ``.metadata.json``) only if its hold-out accuracy is at least as high.

    python scripts/retrain_regime_gate.py --pair ETHUSDT \
        --candidate candidates/regime_ETH-USDT_clean.pkl \
        --current models/regime_ETH-USDT_clean.pkl --holdout-days 30
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def holdout_accuracy(model_path: str, X, y) -> float:
    from src.ml.regime_classifier import RegimeClassifier

    clf = RegimeClassifier(model_path=model_path, model_type="random_forest")
    clf.load_model()
    pred = clf._active_model.predict(X)
    return float((pred == y.to_numpy()).mean())


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pair", required=True)
    p.add_argument("--candidate", required=True)
    p.add_argument("--current", required=True)
    p.add_argument("--holdout-days", type=int, default=30)
    args = p.parse_args(argv)

    from src.data.feature_contract import MARKET_FEATURE_COLS
    from src.data.feature_engineering import calculate_technical_features
    from src.data.label_generation import generate_regime_labels_nowcast
    from src.rl.data import load_klines

    today = date.today()
    holdout_start = today - timedelta(days=args.holdout_days)
    # 10 extra days so indicators and the 24-bar label window are warmed up.
    bars = load_klines(args.pair, holdout_start - timedelta(days=10), today)
    labeled = generate_regime_labels_nowcast(calculate_technical_features(bars))
    labeled = labeled[labeled["regime_label"] >= 0].dropna(subset=MARKET_FEATURE_COLS)
    hold = labeled[labeled.index.date > holdout_start]
    if len(hold) < 24 * 7:
        print(f"{args.pair}: only {len(hold)} hold-out rows — keeping the current model")
        return 0
    X, y = hold[MARKET_FEATURE_COLS], hold["regime_label"].astype(int)

    new_acc = holdout_accuracy(args.candidate, X, y)
    cur_acc = holdout_accuracy(args.current, X, y) if Path(args.current).exists() else 0.0
    decision = "PROMOTE" if new_acc >= cur_acc else "KEEP"
    print(json.dumps({"pair": args.pair, "holdout_rows": len(hold), "holdout_start": str(holdout_start),
                      "candidate_accuracy": round(new_acc, 4), "current_accuracy": round(cur_acc, 4),
                      "decision": decision}))
    if decision == "PROMOTE":
        shutil.copyfile(args.candidate, args.current)
        meta = Path(args.candidate + ".metadata.json")
        if meta.exists():
            shutil.copyfile(meta, args.current + ".metadata.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
