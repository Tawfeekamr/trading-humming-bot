"""
trade_journal.py
────────────────
Logs every grid trade to SQLite with full indicator snapshots.
Provides P&L queries by hour / day / week / month / all-time.
"""

import sqlite3
import json
from datetime import datetime, timedelta
from pathlib import Path
from dataclasses import dataclass, asdict
from typing import Optional


DB_PATH = Path("data/trade_journal.db")


@dataclass
class Trade:
    timestamp: str            # ISO format: "2026-04-04 14:00:00"
    pair: str                 # "BTC/USDT"
    side: str                 # "BUY" | "SELL"
    entry_price: float
    exit_price: float
    quantity: float
    gross_pnl: float
    fee: float
    net_pnl: float
    grid_level: int
    duration_min: int
    rsi: float
    bb_upper: float
    bb_lower: float
    ema_200: float
    atr: float
    grid_state: str           # "ACTIVE" | "PAUSED" | "REACTIVATING"


class TradeJournal:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    # ── Setup ──────────────────────────────────────────────────────

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT NOT NULL,
                    pair          TEXT NOT NULL,
                    side          TEXT NOT NULL,
                    entry_price   REAL NOT NULL,
                    exit_price    REAL NOT NULL,
                    quantity      REAL NOT NULL,
                    gross_pnl     REAL NOT NULL,
                    fee           REAL NOT NULL,
                    net_pnl       REAL NOT NULL,
                    grid_level    INTEGER,
                    duration_min  INTEGER,
                    rsi           REAL,
                    bb_upper      REAL,
                    bb_lower      REAL,
                    ema_200       REAL,
                    atr           REAL,
                    grid_state    TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_timestamp ON trades(timestamp)
            """)

    def _conn(self):
        return sqlite3.connect(self.db_path)

    # ── Write ──────────────────────────────────────────────────────

    def log_trade(self, trade: Trade) -> int:
        """Insert a trade. Returns the new row ID."""
        with self._conn() as conn:
            cursor = conn.execute("""
                INSERT INTO trades (
                    timestamp, pair, side, entry_price, exit_price,
                    quantity, gross_pnl, fee, net_pnl, grid_level,
                    duration_min, rsi, bb_upper, bb_lower, ema_200,
                    atr, grid_state
                ) VALUES (
                    :timestamp, :pair, :side, :entry_price, :exit_price,
                    :quantity, :gross_pnl, :fee, :net_pnl, :grid_level,
                    :duration_min, :rsi, :bb_upper, :bb_lower, :ema_200,
                    :atr, :grid_state
                )
            """, asdict(trade))
            return cursor.lastrowid

    # ── Read ───────────────────────────────────────────────────────

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._conn() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()
            return [dict(r) for r in rows]

    def get_trades(self, since: Optional[str] = None, until: Optional[str] = None) -> list[dict]:
        """Return trades in a time range. Dates are ISO strings."""
        conditions, params = [], []
        if since:
            conditions.append("timestamp >= ?")
            params.append(since)
        if until:
            conditions.append("timestamp <= ?")
            params.append(until)
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        return self._query(f"SELECT * FROM trades {where} ORDER BY timestamp DESC", tuple(params))

    # ── P&L Summary ────────────────────────────────────────────────

    def _summary(self, since: str) -> dict:
        rows = self._query("""
            SELECT
                COUNT(*)                         AS total_trades,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) AS winning,
                SUM(CASE WHEN net_pnl < 0 THEN 1 ELSE 0 END) AS losing,
                SUM(gross_pnl)                   AS gross_pnl,
                SUM(fee)                         AS total_fees,
                SUM(net_pnl)                     AS net_pnl,
                MAX(net_pnl)                     AS best_trade,
                MIN(net_pnl)                     AS worst_trade,
                AVG(net_pnl)                     AS avg_pnl
            FROM trades
            WHERE timestamp >= ?
        """, (since,))
        s = rows[0]
        
        # Handle empty query results where SUM/MAX/MIN return NULL
        keys_to_zero = ["winning", "losing", "gross_pnl", "total_fees", "net_pnl", "best_trade", "worst_trade", "avg_pnl"]
        for k in keys_to_zero:
            if s.get(k) is None:
                s[k] = 0
                
        total = s["total_trades"] or 0
        s["win_rate"] = round((s["winning"] / total * 100), 1) if total > 0 else 0
        return s

    def summary_this_hour(self) -> dict:
        since = (datetime.utcnow() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        return self._summary(since)

    def summary_today(self) -> dict:
        since = datetime.utcnow().strftime("%Y-%m-%d 00:00:00")
        return self._summary(since)

    def summary_this_week(self) -> dict:
        since = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        return self._summary(since)

    def summary_this_month(self) -> dict:
        since = datetime.utcnow().strftime("%Y-%m-01 00:00:00")
        return self._summary(since)

    def summary_all_time(self) -> dict:
        return self._summary("2000-01-01 00:00:00")

    def equity_curve(self, days: int = 30) -> list[dict]:
        """Daily cumulative net PnL for equity curve chart."""
        since = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%d")
        return self._query("""
            SELECT
                DATE(timestamp)     AS date,
                SUM(net_pnl)        AS daily_pnl,
                SUM(SUM(net_pnl)) OVER (ORDER BY DATE(timestamp)) AS cumulative_pnl
            FROM trades
            WHERE timestamp >= ?
            GROUP BY DATE(timestamp)
            ORDER BY date ASC
        """, (since,))

    def best_worst_trades(self, limit: int = 5) -> dict:
        best  = self._query("SELECT * FROM trades ORDER BY net_pnl DESC LIMIT ?", (limit,))
        worst = self._query("SELECT * FROM trades ORDER BY net_pnl ASC  LIMIT ?", (limit,))
        return {"best": best, "worst": worst}
