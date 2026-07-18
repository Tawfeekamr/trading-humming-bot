"""Backfill ML regime labels for a pair over a historical window.

Reuses the LIVE regime-pusher pipeline verbatim
(``calculate_technical_features`` + ``RegimeClassifier``) so the labels match
what ``regime-pusher`` would have produced — zero train/serve skew. Output is
the JSON timeline consumed by ``backtest_replay --regime-file``.

One label per CLOSED 1h bar whose open time falls in [start, end). Each label
is computed from bars up to and including that bar (no lookahead), matching how
the live pusher predicts on the latest closed bar.

Run from the repo root with the conda-base interpreter (has pandas/sklearn/
pandas_ta)::

    /opt/anaconda3/bin/python3 -m src.ml.regime_labels_backfill \\
        --pair ETH-USDT --symbol ETHUSDT \\
        --start 2026-07-04 --end 2026-07-15 \\
        --out backtest/results/eth_regime_jul4-14.json
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

import pandas as pd
import requests

from src.ml.regime_pusher import compute_regime, load_models

BINANCE_KLINES = "https://api.binance.com/api/v3/klines"


def fetch_history(symbol: str, interval: str, start_ms: int, end_ms: int) -> pd.DataFrame:
    """Paginated Binance public klines -> OHLCV DataFrame indexed by timestamp.

    Column shape mirrors ``regime_pusher.fetch_klines`` output
    (``[open, high, low, close, volume]`` with a tz-aware timestamp index) so
    ``compute_regime`` consumes it unchanged.
    """
    frames: list[list] = []
    cur = start_ms
    while cur < end_ms:
        params = {"symbol": symbol, "interval": interval,
                  "startTime": cur, "endTime": end_ms, "limit": 1000}
        resp = requests.get(BINANCE_KLINES, params=params, timeout=15)
        resp.raise_for_status()
        rows = resp.json()
        if not rows:
            break
        frames.append(rows)
        cur = rows[-1][0] + 1  # advance past the last open_time (ms)
        if len(rows) < 1000:
            break
    raw = [r for f in frames for r in f]
    cols = ["open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tbav", "tqav", "ignore"]
    df = pd.DataFrame(raw, columns=cols)
    df["timestamp"] = pd.to_datetime(df["open_time"].astype("int64"), unit="ms", utc=True)
    df = df.set_index("timestamp").sort_index()
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = df[c].astype(float)
    return df[["open", "high", "low", "close", "volume"]]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pair", default="ETH-USDT", help="regime-pusher pair key, e.g. ETH-USDT")
    ap.add_argument("--symbol", default="ETHUSDT", help="Binance symbol, e.g. ETHUSDT")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive, bar open time)")
    ap.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    ap.add_argument("--model-dir", default="models")
    ap.add_argument("--out", required=True)
    ap.add_argument("--warmup", type=int, default=500,
                    help="extra bars fetched before --start so the first in-window label has full feature context")
    args = ap.parse_args()

    window_start = pd.Timestamp(args.start, tz="UTC")
    window_end = pd.Timestamp(args.end, tz="UTC")
    start_ms = int(window_start.timestamp() * 1000)
    end_ms = int(window_end.timestamp() * 1000)
    fetch_start = start_ms - args.warmup * 3_600_000  # warmup hours in ms

    print(f"Fetching {args.symbol} {args.interval} bars {args.start} -> {args.end} "
          f"(warmup {args.warmup} bars before window)...")
    df = fetch_history(args.symbol, args.interval, fetch_start, end_ms)
    print(f"  {len(df)} bars fetched")

    models = load_models([args.pair], args.model_dir)
    if args.pair not in models:
        raise SystemExit(f"No clean model for {args.pair} in {args.model_dir}")
    clf = models[args.pair]

    # One label per in-window bar, computed from bars up to and including it
    # (iloc[:i+1]) -> no lookahead. compute_regime predicts the last row.
    timeline = []
    confs = []
    for i in range(len(df)):
        ts = df.index[i]
        if ts < window_start or ts >= window_end:
            continue
        res = compute_regime(df.iloc[: i + 1], clf)
        if res is None:
            continue
        regime, conf = res
        timeline.append({"ts": int(ts.timestamp() * 1000),
                         "regime": int(regime), "confidence": float(conf)})
        confs.append(conf)

    out = {args.pair: timeline}
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(out))
    print(f"Wrote {len(timeline)} labels to {args.out}")
    if confs:
        s = pd.Series(confs)
        print(f"Confidence: min={s.min():.2f} p25={s.quantile(.25):.2f} "
              f"median={s.median():.2f} p75={s.quantile(.75):.2f} max={s.max():.2f}")
        c = Counter(t["regime"] for t in timeline)
        print(f"Regime counts (0=Ranging, 1=Trending, 2=Danger): {dict(sorted(c.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
