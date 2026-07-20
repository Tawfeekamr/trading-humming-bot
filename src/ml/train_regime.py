#!/usr/bin/env python3
# src/ml/train_regime.py
"""Train a reproducible Random-Forest regime classifier for one pair.

Closes the reproducibility gap left by the legacy ``regime_*.pkl`` models,
which were trained by code that was never committed (so their label definition
is unrecoverable). This trainer uses the documented, lookahead-free-per-bar
labeling in :mod:`src.data.label_generation`.

It saves to ``models/regime_{PAIR}_clean.pkl`` — it does **not** overwrite the
legacy ``regime_{PAIR}.pkl``, so anything still pointing at the legacy model
keeps working.

Usage::

    python -m src.ml.train_regime --pair ETHUSDT --train-end 2026-05-31 --months 24
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta


def _pair_to_slug(pair: str) -> str:
    # ETHUSDT -> ETH-USDT (matches the legacy regime_*.pkl naming).
    return pair.replace("USDT", "-USDT").replace("/", "-")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m src.ml.train_regime",
        description="Train a reproducible RF regime classifier (forward-looking labels).",
    )
    p.add_argument("--pair", default="ETHUSDT")
    p.add_argument(
        "--train-end",
        default=None,
        help="Last training day (YYYY-MM-DD). Default: today. Use the OOS "
        "start to keep the RF strictly out-of-sample.",
    )
    p.add_argument("--months", type=int, default=24)
    p.add_argument(
        "--output",
        default=None,
        help="Output .pkl path. Default: models/regime_{PAIR}_clean.pkl.",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    # Heavy imports (pandas_ta via feature_engineering) — lazy so --help is fast.
    from src.data.feature_engineering import calculate_technical_features
    from src.data.label_generation import generate_regime_labels_nowcast
    from src.ml.regime_classifier import RegimeClassifier
    from src.rl.data import load_klines
    from src.rl.features import MARKET_FEATURE_COLS

    end = date.fromisoformat(args.train_end) if args.train_end else date.today()
    start = end - timedelta(days=30 * args.months)
    print(f"Loading {args.pair} klines {start} -> {end} (~{args.months} months)")
    bars = load_klines(args.pair, start, end)
    if bars.empty:
        print(f"ERROR: no kline data for {args.pair} in [{start}, {end}]")
        return 1
    print(f"  {len(bars):,} bars")

    feats = calculate_technical_features(bars)
    labeled = generate_regime_labels_nowcast(feats)
    labeled = labeled[labeled["regime_label"] >= 0]  # drop no-history tail (first window-1)
    labeled = labeled.dropna(subset=MARKET_FEATURE_COLS)  # drop warmup NaNs
    if labeled.empty:
        print("ERROR: no labeled rows after warmup/truncation; cannot train.")
        return 1

    # Temporal split: fit on the first 85% by time, calibrate on the held-out
    # tail (never seen by the fit) so emitted probabilities are honest.
    labeled = labeled.sort_index()
    split = int(len(labeled) * 0.85)
    train_df, cal_df = labeled.iloc[:split], labeled.iloc[split:]
    X_tr, y_tr = train_df[MARKET_FEATURE_COLS], train_df["regime_label"]
    X_cal, y_cal = cal_df[MARKET_FEATURE_COLS], cal_df["regime_label"]
    counts = {int(k): int(v) for k, v in y_tr.value_counts().items()}
    print(
        f"  fit: {len(train_df):,} rows (class counts {counts}); "
        f"calibrate: {len(cal_df):,} held-out rows"
    )

    out = args.output or f"models/regime_{_pair_to_slug(args.pair)}_clean.pkl"
    clf = RegimeClassifier(model_path=out, model_type="random_forest")
    # Deeper, regularized forest — the depth-5 default underfits; now that the
    # label is learnable, full depth + min_samples_leaf fits real structure, and
    # isotonic calibration makes the confidences honest.
    from sklearn.ensemble import RandomForestClassifier

    clf.model = RandomForestClassifier(
        n_estimators=200,
        max_depth=None,
        min_samples_leaf=5,
        max_features="sqrt",
        class_weight="balanced_subsample",
        n_jobs=-1,
        random_state=42,
    )
    clf.train(X_tr, y_tr)
    clf.calibrate(X_cal, y_cal)
    clf.save_model()
    print(
        f"Saved -> {out}\n"
        f"  Labeling: now-cast (trailing window, no lookahead), 3-class "
        f"(0=ranging, 1=trending, 2=danger), window=24 bars, "
        f"|ret|>=2% / max-drawdown<=-3%. "
        f"Forest: depth-full, min_samples_leaf=5, isotonic-calibrated."
    )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
