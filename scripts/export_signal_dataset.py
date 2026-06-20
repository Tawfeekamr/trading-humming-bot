#!/usr/bin/env python3
"""Export signal-trade data to pandas-ready CSVs for ML / RL.

Reads the signal engine's stores and writes two CSVs:
  - <out>/signal_positions_export.csv  — one row per position (entry features +
    outcome: realized PnL, exit reason, hold time). The labeled-example unit.
  - <out>/signal_decisions_export.csv  — one row per signal_trades row (every
    decision: entries, rejects, blocks, closes) with all columns.

Stdlib only — runs anywhere (EC2 or laptop) against a copy of data/.

Usage:
  python3 scripts/export_signal_dataset.py [--data-dir data] [--out-dir data]
"""
import argparse
import csv
import json
import os
import sqlite3
from datetime import datetime, timezone

POSITIONS_PATH = "signal_positions.json"
JOURNAL_PATH = "signal_journal.db"

POSITION_COLUMNS = [
    "timestamp_entry", "symbol", "channel", "confidence", "entry_price",
    "stop_loss", "take_profits", "tp1_hit", "tp2_hit", "tp3_hit",
    "amount", "amount_closed", "realized_pnl", "is_closed", "exit_reason",
    "hold_seconds", "timestamp_exit", "raw_message",
]


def _parse_ts(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def _load_positions(data_dir):
    path = os.path.join(data_dir, POSITIONS_PATH)
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _load_decisions(data_dir):
    path = os.path.join(data_dir, JOURNAL_PATH)
    rows = []
    if not os.path.exists(path):
        return rows
    try:
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM signal_trades")
        for r in cur:
            rows.append({k: r[k] for k in r.keys()})
        conn.close()
    except sqlite3.Error:
        pass
    return rows


def _find_exit(decisions, symbol, entry_ts):
    """Earliest CLOSE-* decision for symbol at/after entry_ts → (exit_ts, reason)."""
    best = None
    for d in decisions:
        if d.get("symbol") != symbol:
            continue
        action = (d.get("action") or "")
        if not action.upper().startswith("CLOSE"):
            continue
        ts = _parse_ts(d.get("timestamp"))
        if ts is None or ts < entry_ts:
            continue
        if best is None or ts < best[0]:
            best = (ts, d.get("exit_reason") or action)
    return best


def export(data_dir, out_dir):
    """Write positions + decisions CSVs. Returns (positions_csv, decisions_csv)."""
    os.makedirs(out_dir, exist_ok=True)
    positions = _load_positions(data_dir)
    decisions = _load_decisions(data_dir)

    pos_path = os.path.join(out_dir, "signal_positions_export.csv")
    with open(pos_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=POSITION_COLUMNS)
        w.writeheader()
        for p in positions.values():
            entry_ts = p.get("entry_timestamp") or 0
            entry_iso = datetime.fromtimestamp(entry_ts, tz=timezone.utc).isoformat() if entry_ts else ""
            exit_iso, hold, exit_reason = "", "", p.get("exit_reason") or ""
            if p.get("is_closed"):
                ex = _find_exit(decisions, p.get("symbol"), entry_ts)
                if ex:
                    exit_ts = ex[0]
                    hold = int(exit_ts - entry_ts) if exit_ts and entry_ts else ""
                    exit_iso = datetime.fromtimestamp(exit_ts, tz=timezone.utc).isoformat()
                    if not exit_reason:
                        exit_reason = ex[1]
            w.writerow({
                "timestamp_entry": entry_iso,
                "symbol": p.get("symbol", ""),
                "channel": p.get("channel_name", ""),
                "confidence": p.get("signal_confidence", ""),
                "entry_price": p.get("entry_price", ""),
                "stop_loss": p.get("stop_loss", ""),
                "take_profits": json.dumps(p.get("take_profits", [])),
                "tp1_hit": int(bool(p.get("tp1_hit"))),
                "tp2_hit": int(bool(p.get("tp2_hit"))),
                "tp3_hit": int(bool(p.get("tp3_hit"))),
                "amount": p.get("amount", ""),
                "amount_closed": p.get("amount_closed", ""),
                "realized_pnl": p.get("realized_pnl", ""),
                "is_closed": int(bool(p.get("is_closed"))),
                "exit_reason": exit_reason,
                "hold_seconds": hold,
                "timestamp_exit": exit_iso,
                "raw_message": (p.get("raw_message") or "")[:500],
            })

    dec_path = os.path.join(out_dir, "signal_decisions_export.csv")
    all_cols = []
    for d in decisions:
        for k in d.keys():
            if k not in all_cols:
                all_cols.append(k)
    with open(dec_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=all_cols or ["id"])
        w.writeheader()
        for d in decisions:
            w.writerow({k: d.get(k, "") for k in all_cols})

    closed = [p for p in positions.values() if p.get("is_closed")]
    realized = sum((p.get("realized_pnl") or 0) for p in closed)
    wins = sum(1 for p in closed if (p.get("realized_pnl") or 0) > 0)
    print(f"positions: {len(positions)} total / {len(closed)} closed")
    print(f"decisions: {len(decisions)} rows")
    if closed:
        print(f"closed realized PnL: ${realized:+.2f} | win rate: {wins}/{len(closed)} ({wins/len(closed)*100:.0f}%)")
    print(f"wrote: {pos_path}")
    print(f"wrote: {dec_path}")
    return pos_path, dec_path


def main():
    ap = argparse.ArgumentParser(description="Export signal trade data to CSV.")
    ap.add_argument("--data-dir", default="data", help="dir with signal_positions.json + signal_journal.db")
    ap.add_argument("--out-dir", default="data", help="where to write CSVs")
    args = ap.parse_args()
    export(args.data_dir, args.out_dir)


if __name__ == "__main__":
    main()
