# src/ml/run_eth_pilot.py
"""ETH Phase-1 pilot: build labeled flush dataset, train RandomForest walk-forward,
and produce the GO/NO-GO for the ML entry gate.

PRIMARY signal = walk-forward OUT-OF-SAMPLE metrics (does the model predict
winners on unseen flushes?). The full-period run_single_ml vs run_single P&L
comparison is INFORMATIONAL only — it trains on all data then predicts on the
same data, so it is in-sample-biased (optimistic). Trust the OOS metrics.

Run: python -m src.ml.run_eth_pilot
Reuses the backtest's cached ETH bars (run the backtest first, or it downloads).
"""
from datetime import date, timedelta
import pandas as pd

from backtest.mean_reversion.data import load_bars
from backtest.mean_reversion.features import compute_features
from backtest.mean_reversion.labels import label_flushes
from backtest.mean_reversion.backtest import run_single, run_single_ml, LIVE_CONFIG
from src.ml.flush_reversion_model import (
    build_dataset, FlushReversionClassifier, walk_forward_evaluate,
)


def main():
    import argparse
    parser = argparse.ArgumentParser(description="ML flush-reversion pilot (per-pair GO/NO-GO)")
    parser.add_argument("--symbol", default="ETHUSDT")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--bar", default="5s")
    parser.add_argument("--drop_thr", type=float, default=0.05)
    parser.add_argument("--tp", type=float, default=0.02)
    parser.add_argument("--stop", type=float, default=0.04)
    parser.add_argument("--ml_threshold", type=float, default=0.5)
    args = parser.parse_args()

    end = date.today()
    start = end - timedelta(days=30 * args.months)
    bars = load_bars(args.symbol, start, end, args.bar)
    feats = compute_features(bars, args.bar)
    labels = label_flushes(
        bars, feats,
        drop_thr=args.drop_thr, tp=args.tp,
        stop=args.stop, max_hold=180,
    )
    if labels.empty:
        print("No flush events in the period — cannot train. VERDICT: NO-GO (no data).")
        return

    ds = build_dataset(bars, feats, labels)
    n_winners = int(ds["label"].sum())
    print(f"flush events: {len(ds)}  (winners: {n_winners}, "
          f"base win rate: {100.0 * n_winners / len(ds):.1f}%)")

    metrics = walk_forward_evaluate(ds, test_frac=1 / 3)
    print("OOS metrics (PRIMARY):", metrics)

    # Informational full-period comparison (in-sample-biased).
    clf = FlushReversionClassifier()
    clf.fit(ds, ds["label"])
    clf.save(f"models/flush_reversion_{args.symbol}.pkl")
    proba = pd.Series(clf.predict_proba(ds).tolist(), index=ds["ts"])
    cfg = {"drop_thr": args.drop_thr, "tp": args.tp, "stop": args.stop, "base_size": 100}
    no_ml = run_single(bars, feats, bar=args.bar, **cfg)
    ml = run_single_ml(bars, feats, bar=args.bar, proba=proba, ml_threshold=args.ml_threshold, **cfg)
    print("no-ML (info, full period):", no_ml)
    print("ML-gated (info, IN-SAMPLE-biased):", ml)

    # PRIMARY GO/NO-GO from OOS: meaningful predictive power on unseen flushes.
    auc = metrics.get("oos_auc", 0.0)
    prec = metrics.get("oos_precision", 0.0)
    go = auc > 0.55 and prec > 0.52
    print(f"\nOOS AUC={auc:.3f}  OOS precision={prec:.3f}")
    print(f"=== VERDICT: {'GO (OOS signal present -> wire up Phase 2)' if go else 'NO-GO (no meaningful OOS edge)'} ===")


if __name__ == "__main__":
    main()
