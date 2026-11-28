"""Scout walk-forward + random-entry null + GO verdict (pre-registration §9–§12).

    python3 -m backtest.scout.walk_forward            # full run → docs/scout/WALK_FORWARD_RESULT.{md,json}
"""
from __future__ import annotations

import itertools
import json
import logging
import math
import subprocess
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

from src.scout import market_state as ms
from src.scout.config import BreakoutParams, CommonParams, CostParams, PortfolioParams, PullbackParams
from src.scout.setups import detect_breakout, detect_pullback

from .panel import BTC, Panel, build_panel, find_setups
from .portfolio import Trade, run_portfolio
from .sim import simulate_trade

logger = logging.getLogger(__name__)

DATA_START, DATA_END = "2022-11-01", "2026-09-30"
OOS_START = "2023-07-01"
N_FOLDS = 13
TRAIN_MONTHS = 6
MIN_TRAIN_TRADES = 30
NULL_RUNS = 1000
SEED = 20261128
OUT_DIR = Path("docs/scout")

GRIDS = {
    "breakout": {
        "fn": detect_breakout,
        "default": (2.0, 1.5),
        "cells": {(rv, sa): BreakoutParams(rvol_min=rv, stop_atr=sa)
                  for rv, sa in itertools.product((1.5, 2.0, 2.5), (1.0, 1.5, 2.0))},
        "axes": ("rvol_min", "stop_atr"),
    },
    "pullback": {
        "fn": detect_pullback,
        "default": (0.7, 1.0),
        "cells": {(rk, ms_): PullbackParams(rank_min=rk, min_stop_atr=ms_)
                  for rk, ms_ in itertools.product((0.6, 0.7, 0.8), (0.75, 1.0, 1.5))},
        "axes": ("rank_min", "min_stop_atr"),
    },
}

GO_THRESHOLDS = {"trades": 150, "expectancy_r": 0.10, "profit_factor": 1.25,
                 "null_p": 0.05, "folds_positive": 0.60, "max_dd": 0.15, "ex_best_coin_r": 0.0}


def folds(index: pd.DatetimeIndex) -> list[dict]:
    out = []
    start = pd.Timestamp(OOS_START, tz="UTC")
    for k in range(N_FOLDS):
        ts = start + pd.DateOffset(months=3 * k)
        te = ts + pd.DateOffset(months=3)
        tr = ts - pd.DateOffset(months=TRAIN_MONTHS)
        out.append({
            "name": f"{ts.year}Q{(ts.month - 1) // 3 + 1}",
            "train": (int(index.searchsorted(tr)), int(index.searchsorted(ts))),
            "test": (int(index.searchsorted(ts)), int(index.searchsorted(te))),
        })
    return out


def in_window(trades, w) -> list[Trade]:
    a, b = w
    return [t for t in trades if a <= t.entry_idx < b]


def mean_r(trades) -> float:
    return float(np.mean([t.r for t in trades])) if trades else float("nan")


def max_drawdown(trades, budget: float) -> float:
    legs = sorted(leg for t in trades for leg in t.legs)
    if not legs:
        return 0.0
    eq = np.cumsum([p for _, p in legs])
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    return float(np.max(peak - eq) / budget)


def metrics(trades: list[Trade], fold_defs, budget: float) -> dict:
    if not trades:
        return {"trades": 0}
    pnl = np.array([t.pnl for t in trades])
    wins, losses = pnl[pnl > 0].sum(), -pnl[pnl < 0].sum()
    fold_r = {}
    for f in fold_defs:
        ft = in_window(trades, f["test"])
        if ft:
            fold_r[f["name"]] = float(sum(t.r for t in ft))
    by_coin = defaultdict(float)
    for t in trades:
        by_coin[t.sym] += t.r
    best = max(by_coin, key=by_coin.get)
    return {
        "trades": len(trades),
        "expectancy_r": mean_r(trades),
        "total_r": float(sum(t.r for t in trades)),
        "profit_factor": float(wins / losses) if losses > 0 else float("inf"),
        "win_rate": float(np.mean(pnl > 0)),
        "total_pnl_usd": float(pnl.sum()),
        "return_on_budget": float(pnl.sum() / budget),
        "max_dd": max_drawdown(trades, budget),
        "fold_total_r": fold_r,
        "folds_positive": float(np.mean([v > 0 for v in fold_r.values()])) if fold_r else 0.0,
        "best_coin": best,
        "best_coin_r": float(by_coin[best]),
        "ex_best_coin_r": float(sum(t.r for t in trades) - by_coin[best]),
        "coins_traded": len(by_coin),
        "exit_reasons": dict(Counter(t.reason for t in trades)),
        "sides": dict(Counter("long" if t.side > 0 else "short" for t in trades)),
        "states": dict(Counter(ms.NAMES[t.state] for t in trades)),
        "avg_hold_h": float(np.mean([t.exit_idx - t.entry_idx + 1 for t in trades])),
    }


def random_entry_null(p: Panel, real: list[Trade], fold_defs, costs: CostParams,
                      pp: PortfolioParams, common: CommonParams, runs: int, seed: int) -> np.ndarray:
    """Mean R of `runs` random-entry replicas of the real OOS trade list."""
    rng = np.random.default_rng(seed)
    fold_of = []
    for t in real:
        for k, f in enumerate(fold_defs):
            if f["test"][0] <= t.entry_idx < f["test"][1]:
                fold_of.append(k)
                break
    member = p.member.to_numpy()
    sym_arr = np.array(p.syms)
    eligible = {}
    for k, f in enumerate(fold_defs):
        a, b = f["test"]
        hours = np.arange(max(a - 1, 0), min(b - 1, p.n - 1))   # signal bar t, entry t+1 in window
        for side in (1, -1):
            ok = ms.allows(p.state[hours], side) & (p.state[hours] != ms.NO_TRADE)
            eligible[(k, side)] = hours[np.asarray(ok, dtype=bool)]
    out = np.full(runs, np.nan)
    for run in range(runs):
        rs = []
        for t, k in zip(real, fold_of):
            hours = eligible[(k, t.side)]
            if len(hours) == 0:
                continue
            for _ in range(50):
                h = int(hours[rng.integers(len(hours))])
                cands = np.flatnonzero(member[h])
                if len(cands) == 0:
                    continue
                s = sym_arr[cands[rng.integers(len(cands))]]
                O, H, L, C = p.ohlc[s]
                c, a1 = C[h], p.atr1h[s][h]
                if math.isnan(c) or math.isnan(a1) or math.isnan(O[h + 1]):
                    continue
                risk = t.stop_dist_atr1h * a1
                if not (common.min_stop_pct <= risk / c <= common.max_stop_pct):
                    continue
                stop = c - t.side * risk
                if (t.side > 0 and O[h + 1] <= stop) or (t.side < 0 and O[h + 1] >= stop):
                    continue
                tps = (c + t.side * risk, c + 2 * t.side * risk, c + 3 * t.side * risk)
                res = simulate_trade(O, H, L, C, h + 1, t.side, stop, tps, 1.0, costs, pp.max_hold_bars)
                if res is not None and res.risk > 0:
                    rs.append(res.r)
                    break
        out[run] = np.mean(rs) if rs else np.nan
        if (run + 1) % 100 == 0:
            logger.info("null %d/%d", run + 1, runs)
    return out


def verdict(m: dict, null_p: float) -> dict:
    g = GO_THRESHOLDS
    checks = {
        "1_trades": (m.get("trades", 0), m.get("trades", 0) >= g["trades"]),
        "2_expectancy_r": (m.get("expectancy_r"), (m.get("expectancy_r") or -1) > g["expectancy_r"]),
        "3_profit_factor": (m.get("profit_factor"), (m.get("profit_factor") or 0) >= g["profit_factor"]),
        "4_null_p": (null_p, null_p <= g["null_p"]),
        "5_folds_positive": (m.get("folds_positive"), (m.get("folds_positive") or 0) >= g["folds_positive"]),
        "6_max_dd": (m.get("max_dd"), m.get("max_dd", 1) <= g["max_dd"]),
        "7_ex_best_coin_r": (m.get("ex_best_coin_r"), (m.get("ex_best_coin_r") or -1) > g["ex_best_coin_r"]),
    }
    return {"checks": {k: {"value": v, "pass": bool(ok)} for k, (v, ok) in checks.items()},
            "go": all(ok for _, ok in checks.values())}


def benchmarks(p: Panel, a: int, b: int) -> dict:
    C = p.ohlc[BTC][3]
    btc = float(C[b - 1] / C[a] - 1.0)
    closes = pd.DataFrame({s: p.ohlc[s][3] for s in p.syms}, index=p.index).iloc[a:b]
    rets = closes.pct_change()
    mem = p.member.iloc[a:b].shift(1, fill_value=False)
    ew = rets.where(mem).mean(axis=1).fillna(0.0)
    return {"btc_hold": btc, "equal_weight_universe": float((1.0 + ew).prod() - 1.0)}


def git_commit() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except Exception:  # pragma: no cover
        return "unknown"


def run(null_runs: int = NULL_RUNS) -> dict:
    t0 = time.time()
    pp, costs, common = PortfolioParams(), CostParams(), CommonParams()
    logger.info("building panel …")
    p = build_panel(DATA_START, DATA_END)
    fd = folds(p.index)
    oos = (fd[0]["test"][0], fd[-1]["test"][1])
    result = {"commit": git_commit(), "data": [DATA_START, DATA_END], "oos": [OOS_START, DATA_END],
              "symbols_in_panel": len(p.syms),
              "state_share_oos": {ms.NAMES[k]: float(np.mean(p.state[oos[0]:oos[1]] == k)) for k in ms.NAMES},
              "benchmarks_oos": benchmarks(p, *oos), "detectors": {}}
    default_cands = []
    for name, g in GRIDS.items():
        logger.info("%s: finding setups for %d cells …", name, len(g["cells"]))
        cands = find_setups(p, g["fn"], g["cells"])
        cell_trades, cell_skips = {}, {}
        for key, c in cands.items():
            cell_trades[key], cell_skips[key] = run_portfolio(c, p, pp, costs)
        chosen, stitched = [], []
        for f in fd:
            scores = {k: mean_r(in_window(tr, f["train"])) for k, tr in cell_trades.items()
                      if len(in_window(tr, f["train"])) >= MIN_TRAIN_TRADES}
            pick = max(scores, key=scores.get) if scores else g["default"]
            chosen.append({"fold": f["name"], "cell": dict(zip(g["axes"], pick)),
                           "train_mean_r": scores.get(pick), "from_default": not scores})
            stitched += in_window(cell_trades[pick], f["test"])
        m = metrics(stitched, fd, pp.budget)
        logger.info("%s: %d OOS trades, mean R %.3f — running null …", name, m.get("trades", 0),
                    m.get("expectancy_r", float("nan")))
        null = random_entry_null(p, stitched, fd, costs, pp, common, null_runs, SEED) if stitched \
            else np.array([np.nan])
        valid = null[~np.isnan(null)]
        null_p = float(np.mean(valid >= m["expectancy_r"])) if stitched and len(valid) else 1.0
        default_oos = in_window(cell_trades[g["default"]], oos)
        result["detectors"][name] = {
            "oos_metrics": m,
            "null": {"runs": int(len(valid)), "mean": float(np.mean(valid)) if len(valid) else None,
                     "p95": float(np.percentile(valid, 95)) if len(valid) else None, "p_value": null_p},
            "verdict": verdict(m, null_p),
            "selected_cells": chosen,
            "default_cell_oos_metrics": metrics(default_oos, fd, pp.budget),
            "candidates_per_cell": {str(k): int(len(v)) for k, v in cands.items()},
            "skips_default_cell": cell_skips[g["default"]],
            "full_period_mean_r_per_cell": {str(k): mean_r(v) for k, v in cell_trades.items()},
        }
        if not cands[g["default"]].empty:
            default_cands.append(cands[g["default"]])
    combo = pd.concat(default_cands) if default_cands else pd.DataFrame()
    combo_trades, _ = run_portfolio(combo, p, pp, costs)
    result["combined_default_cells_oos"] = metrics(in_window(combo_trades, oos), fd, pp.budget)
    result["runtime_s"] = round(time.time() - t0, 1)
    return result


def _fmt(v):
    if isinstance(v, float):
        return "∞" if math.isinf(v) else f"{v:.3f}"
    return str(v)


def write_report(res: dict, out_dir: Path = OUT_DIR) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "WALK_FORWARD_RESULT.json").write_text(json.dumps(res, indent=2, default=str))
    L = ["# Scout Bot — Walk-forward result (Phase 1)", "",
         f"Rules: `docs/scout/PREREGISTRATION.md`. Commit `{res['commit']}`. "
         f"OOS {res['oos'][0]} → {res['oos'][1]}, {res['symbols_in_panel']} symbols. "
         f"Runtime {res['runtime_s']}s.", "",
         "Market state share of OOS hours: " + ", ".join(f"{k} {v:.0%}" for k, v in res["state_share_oos"].items()), ""]
    for name, d in res["detectors"].items():
        v = d["verdict"]
        L += [f"## Detector {name}: **{'GO' if v['go'] else 'NO-GO'}**", "",
              "| # | Criterion | Value | Threshold | Pass |", "|---|---|---|---|---|"]
        th = {"1_trades": "≥ 150", "2_expectancy_r": "> 0.10R", "3_profit_factor": "≥ 1.25",
              "4_null_p": "≤ 0.05", "5_folds_positive": "≥ 60%", "6_max_dd": "≤ 15%", "7_ex_best_coin_r": "> 0"}
        for k, c in v["checks"].items():
            L.append(f"| {k[0]} | {k[2:]} | {_fmt(c['value'])} | {th[k]} | {'✅' if c['pass'] else '❌'} |")
        m = d["oos_metrics"]
        L += ["", f"OOS: {m.get('trades', 0)} trades, total {_fmt(m.get('total_r', 0.0))}R, "
                  f"win rate {_fmt(m.get('win_rate', 0.0))}, P&L ${m.get('total_pnl_usd', 0):,.0f} "
                  f"on $10k ({m.get('return_on_budget', 0):.1%}).",
              f"Null: mean {_fmt(d['null']['mean'])}R, 95th pct {_fmt(d['null']['p95'])}R, "
              f"p = {_fmt(d['null']['p_value'])} ({d['null']['runs']} runs).",
              f"Exits: {m.get('exit_reasons')}. Sides: {m.get('sides')}. States: {m.get('states')}.",
              f"Best coin {m.get('best_coin')} ({_fmt(m.get('best_coin_r', 0.0))}R).", "",
              "Per fold (selected cell, train mean R → OOS total R):", "",
              "| Fold | Cell | Train mean R | OOS total R |", "|---|---|---|---|"]
        fr = m.get("fold_total_r", {})
        for c in d["selected_cells"]:
            L.append(f"| {c['fold']} | {c['cell']}{' (default)' if c['from_default'] else ''} | "
                     f"{_fmt(c['train_mean_r'])} | {_fmt(fr.get(c['fold'], 0.0)) if c['fold'] in fr else '—'} |")
        dm = d["default_cell_oos_metrics"]
        L += ["", f"Default cell, OOS (info): {dm.get('trades', 0)} trades, mean "
                  f"{_fmt(dm.get('expectancy_r', float('nan')))}R, PF {_fmt(dm.get('profit_factor', float('nan')))}.",
              f"Default-cell skips: {d['skips_default_cell']}.", ""]
    b = res["benchmarks_oos"]
    cm = res["combined_default_cells_oos"]
    L += ["## For information only (not GO criteria)", "",
          f"- BTC buy-and-hold over OOS: {b['btc_hold']:.1%}; equal-weight universe: {b['equal_weight_universe']:.1%}.",
          f"- Combined A+B (default cells): {cm.get('trades', 0)} trades, mean "
          f"{_fmt(cm.get('expectancy_r', float('nan')))}R, return on budget {cm.get('return_on_budget', 0):.1%}.", ""]
    (out_dir / "WALK_FORWARD_RESULT.md").write_text("\n".join(L))


def main(argv) -> int:  # pragma: no cover - CLI
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    runs = int(argv[1]) if len(argv) > 1 else NULL_RUNS
    res = run(runs)
    write_report(res)
    print(json.dumps({k: v["verdict"]["go"] for k, v in res["detectors"].items()}))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
