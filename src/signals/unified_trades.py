"""Write completed signal trades to the unified trades table (data/trades.db).

Until 1 Oct 2026 the Rust signal mirror wrote each close to trades.db. After it
became read-only, only the Python journals recorded signal closes, so the
unified table silently stopped at 26 Sep (Nov 2026 audit). This writes ONE row
per completed trade (never per partial take-profit), with the cumulative P&L,
in the same schema the Rust journal uses (is_backfilled = 0).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB = Path("data/trades.db")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL, engine TEXT NOT NULL, pair TEXT NOT NULL,
    side TEXT, entry_price REAL, exit_price REAL, quantity REAL, pnl REAL NOT NULL,
    exit_reason TEXT, duration_mins INTEGER, is_backfilled INTEGER DEFAULT 0,
    sl_price REAL, tp_price REAL, signal_score REAL, regime_at_entry TEXT,
    entry_reason TEXT, fees REAL, r_multiple REAL, context_json TEXT
)
"""


def estimate_fees(side: str, entry_price: float, amount: float, gross_pnl: float, fee_bps: float) -> float:
    """Taker fee on the entry notional plus the total exit notional.

    Exit notional over all slices = entry notional + gross P&L for a long
    (minus for a short), so no per-slice prices are needed.
    """
    entry_notional = abs(entry_price * amount)
    exit_notional = entry_notional + (gross_pnl if side == "long" else -gross_pnl)
    return (entry_notional + max(exit_notional, 0.0)) * fee_bps / 1e4


def write_trade(*, engine: str, pair: str, side: str, entry_price: float, exit_price: float,
                quantity: float, gross_pnl: float, fees: float, exit_reason: str,
                duration_mins: Optional[int], sl_price: Optional[float], tp_price: Optional[float],
                r_multiple: Optional[float], context: dict, db_path: Path = DEFAULT_DB,
                timestamp: Optional[str] = None) -> bool:
    """Insert one completed trade. Returns False (and logs) on any failure —
    journaling must never break trading."""
    ts = timestamp or datetime.now(timezone.utc).isoformat()
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(db_path, timeout=10) as conn:
            conn.execute(_SCHEMA)
            conn.execute(
                "INSERT INTO trades (timestamp, engine, pair, side, entry_price, exit_price, quantity, pnl, "
                "exit_reason, duration_mins, is_backfilled, sl_price, tp_price, fees, r_multiple, context_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)",
                (ts, engine, pair, side.upper(), entry_price, exit_price, quantity, gross_pnl - fees,
                 exit_reason, duration_mins, sl_price, tp_price, fees, r_multiple, json.dumps(context)),
            )
        return True
    except Exception as e:  # pragma: no cover - logged, never raised
        logger.error(f"unified trade write failed for {pair}: {e}")
        return False
