# backtest/mean_reversion/backtest.py
"""vectorbt sweep engine: SL/TP exits, IS/OOS split, metrics, report, CLI."""
import json
from pathlib import Path

import pandas as pd

from .data import load_bars
from .features import compute_features
from .strategy import entry_signal

# Spec §4 grid.
DROP_THRS = [0.03, 0.04, 0.05, 0.06, 0.08]
TP_STOPS = [0.01, 0.015, 0.02, 0.03, 0.04]
STOP_STOPS = [0.02, 0.03, 0.04, 0.05, 0.06]
BASE_SIZES = [50, 100, 200, 500]

# Deployed live config (headline reference).
LIVE_CONFIG = {"drop_thr": 0.05, "tp": 0.02, "stop": 0.04, "base_size": 100}

INIT_CASH = 1000.0
FEES = 0.001        # 0.1% per side (matches paper FEE_RATE)
SLIPPAGE = 0.0005   # 5 bps (matches existing sweep)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "mean_reversion"


def run_single(bars: pd.DataFrame, features: pd.DataFrame, drop_thr: float,
               tp: float, stop: float, base_size: float, bar: str = "1s"):
    """Run one config. Returns a metrics dict, or None if no entries."""
    import vectorbt as vbt

    entries = entry_signal(features, drop_thr)
    if not entries.any():
        return None

    # Flat $ sizing per trade (avoids per-bar Series size; base_size sweep stays
    # meaningful as account-% risk vs INIT_CASH).
    pf = vbt.Portfolio.from_signals(
        close=bars["close"],
        entries=entries,
        sl_stop=stop,
        tp_stop=tp,
        size=base_size,
        size_type="value",
        init_cash=INIT_CASH,
        fees=FEES,
        slippage=SLIPPAGE,
        freq=bar,
    )
    stats = pf.stats()
    return {
        "drop_thr": drop_thr, "tp": tp, "stop": stop, "base_size": base_size,
        "total_trades": int(stats.get("Total Trades", 0)),
        "total_return_pct": float(stats.get("Total Return [%]", 0.0)),
        "sharpe_ratio": float(stats.get("Sharpe Ratio", 0.0)),
        "max_drawdown_pct": float(stats.get("Max Drawdown [%]", 0.0)),
        "win_rate": float(stats.get("Win Rate [%]", 0.0)),
    }


def run_sweep(bars: pd.DataFrame, features: pd.DataFrame, bar: str = "1s") -> pd.DataFrame:
    """Run the full grid. Configs with no entries are skipped."""
    rows = []
    for drop_thr in DROP_THRS:
        for tp in TP_STOPS:
            for stop in STOP_STOPS:
                for base_size in BASE_SIZES:
                    try:
                        r = run_single(bars, features, drop_thr, tp, stop, base_size, bar)
                    except Exception:
                        r = None
                    if r is not None:
                        rows.append(r)
    return pd.DataFrame(rows)


def walk_forward(bars: pd.DataFrame, features: pd.DataFrame, bar: str = "1s",
                 oos_frac: float = 1 / 3):
    """Sweep on the in-sample slice; re-evaluate the best (by Sharpe) on out-of-sample.

    Features are computed once on the full series and sliced by position. This is
    intentional and is NOT lookahead bias: every OOS decision at bar t uses only
    features[t], which depends solely on close[t-30..t] — all past bars, no future
    data. The rolling window legitimately carries history across the IS/OOS
    boundary because a deployed strategy always has its last 30 bars (this matches
    the existing grid/trend walk_forward.py, which also slices and evaluates on the
    test window). Recomputing features on the OOS slice alone would discard 30 real
    bars to a NaN warmup gap, making the test *less* faithful. At real scale
    (millions of OOS bars) the 30 boundary bars are also statistically negligible.
    """
    n = len(bars)
    split = int(n * (1 - oos_frac))
    is_bars, oos_bars = bars.iloc[:split], bars.iloc[split:]
    is_feat, oos_feat = features.iloc[:split], features.iloc[split:]

    is_results = run_sweep(is_bars, is_feat, bar)
    if is_results.empty:
        return None
    best = is_results.sort_values("sharpe_ratio", ascending=False).iloc[0]
    oos = run_single(
        oos_bars, oos_feat,
        drop_thr=best["drop_thr"], tp=best["tp"], stop=best["stop"],
        base_size=best["base_size"], bar=bar,
    )
    return {"is_best": best.to_dict(), "oos": oos}


def _to_jsonable(d):
    """Make a dict JSON-serializable (numpy/pandas scalars -> python)."""
    out = {}
    for k, v in (d or {}).items():
        if hasattr(v, "item"):
            v = v.item()
        out[k] = v
    return out


def _hodl_return(close: pd.Series) -> float:
    if close.empty:
        return 0.0
    return float((close.iloc[-1] / close.iloc[0] - 1.0) * 100.0)


def run_pair(symbol: str, bars: pd.DataFrame, bar: str = "1s",
             results_dir: Path = RESULTS_DIR) -> dict:
    """Full pipeline for one symbol: features, live-config, sweep, walk-forward, write JSON."""
    results_dir.mkdir(parents=True, exist_ok=True)
    features = compute_features(bars, bar)

    live = run_single(bars, features, bar=bar, **LIVE_CONFIG)
    sweep = run_sweep(bars, features, bar)
    wf = walk_forward(bars, features, bar)

    best = (sweep.sort_values("sharpe_ratio", ascending=False).iloc[0].to_dict()
            if not sweep.empty else None)

    per_symbol = {
        "symbol": symbol,
        "bar": bar,
        "hodl_return_pct": _hodl_return(bars["close"]),
        "live_config": _to_jsonable(live),
        "best": _to_jsonable(best),
        "walk_forward": {"is_best": _to_jsonable(wf["is_best"]),
                         "oos": _to_jsonable(wf["oos"])} if wf else None,
        "sweep": sweep.to_dict(orient="records"),
    }
    with open(results_dir / f"{symbol}_sweep.json", "w") as f:
        json.dump(per_symbol, f, indent=2, default=str)
    return per_symbol


def build_report(per_pair: list, summary_path: Path) -> str:
    lines = ["# Mean-Reversion Backtest Report", ""]
    for p in per_pair:
        live = p.get("live_config") or {}
        best = p.get("best") or {}
        wf = p.get("walk_forward") or {}
        oos = wf.get("oos") or {}
        lines.append(f"## {p['symbol']}")
        lines.append(f"- HODL: {p.get('hodl_return_pct', 0):.1f}%")
        lines.append(f"- Live (+2%/-4%): trades={live.get('total_trades',0)} "
                     f"return={live.get('total_return_pct',0):.1f}% "
                     f"sharpe={live.get('sharpe_ratio',0):.2f} "
                     f"maxDD={live.get('max_drawdown_pct',0):.1f}% "
                     f"win={live.get('win_rate',0):.0f}%")
        if best:
            lines.append(f"- Best IS: drop={best.get('drop_thr')} tp={best.get('tp')} "
                         f"stop={best.get('stop')} size={best.get('base_size')} "
                         f"sharpe={best.get('sharpe_ratio',0):.2f}")
        lines.append(f"- OOS (best cfg): trades={oos.get('total_trades',0)} "
                     f"return={oos.get('total_return_pct',0):.1f}% "
                     f"sharpe={oos.get('sharpe_ratio',0):.2f}")
        if best and oos:
            gap = float(best.get("sharpe_ratio", 0)) - float(oos.get("sharpe_ratio", 0))
            flag = " ⚠️ overfit?" if gap > 1.0 else ""
            lines.append(f"- IS→OOS Sharpe gap: {gap:.2f}{flag}")
        lines.append("")
    text = "\n".join(lines)
    with open(summary_path, "w") as f:
        f.write(text)
    return text


def main():
    import argparse
    from datetime import date, timedelta

    parser = argparse.ArgumentParser(description="Mean-reversion tick-replay backtest")
    parser.add_argument("--pairs", default="BNBUSDT,DOGEUSDT,ETHUSDT,XRPUSDT")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--bar", default="1s",
                        help="resample bar (1s=max fidelity/slowest; 5s≈5x faster)")
    args = parser.parse_args()

    end = date.today()
    start = end - timedelta(days=30 * args.months)
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    per_pair = []
    for symbol in pairs:
        print(f"=== {symbol} {start} -> {end} ({args.bar}) ===")
        bars = load_bars(symbol, start, end, args.bar)
        if bars.empty:
            print(f"  no data, skipping")
            continue
        print(f"  {len(bars)} bars; computing features + sweep...")
        per_pair.append(run_pair(symbol, bars, args.bar))

    summary = {
        "pairs": pairs, "bar": args.bar, "start": str(start), "end": str(end),
        "results": per_pair,
    }
    with open(RESULTS_DIR / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    build_report(per_pair, RESULTS_DIR / "report.md")
    print(f"\nDone -> {RESULTS_DIR}")


if __name__ == "__main__":
    main()
