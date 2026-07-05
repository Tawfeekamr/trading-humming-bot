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
    from src.data.label_generation import generate_regime_labels
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
    labeled = generate_regime_labels(feats)
    labeled = labeled[labeled["regime_label"] >= 0]  # drop no-future tail
    labeled = labeled.dropna(subset=MARKET_FEATURE_COLS)  # drop warmup NaNs
    if labeled.empty:
        print("ERROR: no labeled rows after warmup/truncation; cannot train.")
        return 1

    X = labeled[MARKET_FEATURE_COLS]
    y = labeled["regime_label"]
    counts = {int(k): int(v) for k, v in y.value_counts().items()}
    print(f"  labeled: {len(labeled):,} rows, class counts {counts}")

    out = args.output or f"models/regime_{_pair_to_slug(args.pair)}_clean.pkl"
    clf = RegimeClassifier(model_path=out, model_type="random_forest")
    clf.train(X, y)
    clf.save_model()
    print(
        f"Saved -> {out}\n"
        f"  Labeling: forward-looking 3-class (0=ranging, 1=trending, 2=danger), "
        f"horizon=24 bars, |ret|>=2% / drawdown<=-3%. Defined, not recovered."
    )
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
