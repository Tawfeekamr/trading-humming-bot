import logging
import sqlite3
import threading
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

DEFAULT_DB_PATH = Path("data/trend_journal.db")


class TrendJournal:
    def __init__(self, db_path: Path = DEFAULT_DB_PATH) -> None:
        self._db_path = db_path
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self) -> None:
        with self._lock:
            conn = self._conn()
            conn.execute("""
                CREATE TABLE IF NOT EXISTS trend_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    side TEXT NOT NULL,
                    entry_price REAL NOT NULL,
                    exit_price REAL NOT NULL,
                    amount REAL NOT NULL,
                    fee REAL DEFAULT 0,
                    pnl REAL NOT NULL,
                    pnl_pct REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    take_profit REAL NOT NULL,
                    exit_reason TEXT NOT NULL,
                    signal_score INTEGER DEFAULT 0,
                    duration_minutes INTEGER DEFAULT 0
                )
            """)
            conn.commit()
            conn.close()

    def log_trade(self, side: str, entry_price: float, exit_price: float,
                  amount: float, fee: float, pnl: float, pnl_pct: float,
                  stop_loss: float, take_profit: float, exit_reason: str,
                  signal_score: int = 0, duration_minutes: int = 0) -> int:
        ts = datetime.now(timezone.utc).isoformat()
        with self._lock:
            conn = self._conn()
            cursor = conn.execute(
                """INSERT INTO trend_trades
                   (timestamp, side, entry_price, exit_price, amount, fee,
                    pnl, pnl_pct, stop_loss, take_profit, exit_reason,
                    signal_score, duration_minutes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (ts, side, entry_price, exit_price, amount, fee,
                 pnl, pnl_pct, stop_loss, take_profit, exit_reason,
                 signal_score, duration_minutes),
            )
            trade_id = cursor.lastrowid
            conn.commit()
            conn.close()
        return trade_id

    def get_trades(self, since: Optional[str] = None, limit: int = 100) -> list[dict]:
        conn = self._conn()
        if since:
            rows = conn.execute("SELECT * FROM trend_trades WHERE timestamp >= ? ORDER BY id DESC LIMIT ?", (since, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM trend_trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def recent_trades(self, limit: int = 10) -> list[dict]:
        return self.get_trades(limit=limit)

    def summary(self, since: Optional[str] = None) -> dict:
        conn = self._conn()
        query = "SELECT * FROM trend_trades"
        params = []
        if since:
            query += " WHERE timestamp >= ?"
            params.append(since)
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        conn.close()
        if not rows:
            return {"total_trades": 0, "winning": 0, "losing": 0, "win_rate": 0.0, "net_pnl": 0.0, "gross_pnl": 0.0, "total_fees": 0.0, "avg_pnl": 0.0, "best_trade": 0.0, "worst_trade": 0.0}
        wins = [t for t in rows if t["pnl"] > 0]
        losses = [t for t in rows if t["pnl"] <= 0]
        total_pnl = sum(t["pnl"] for t in rows)
        gross_pnl = sum(t["pnl"] + t["fee"] for t in rows)
        total_fees = sum(t["fee"] for t in rows)
        best = max((t["pnl"] for t in rows), default=0.0)
        worst = min((t["pnl"] for t in rows), default=0.0)
        return {
            "total_trades": len(rows), "winning": len(wins), "losing": len(losses),
            "win_rate": round(len(wins) / len(rows) * 100, 1),
            "net_pnl": round(total_pnl, 2),
            "gross_pnl": round(gross_pnl, 2),
            "total_fees": round(total_fees, 2),
            "avg_pnl": round(total_pnl / len(rows), 2) if rows else 0.0,
            "best_trade": round(best, 2),
            "worst_trade": round(worst, 2)
        }

    def summary_this_hour(self) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M:%S")
        return self.summary(since)

    def summary_today(self) -> dict:
        since = datetime.now(timezone.utc).strftime("%Y-%m-%d 00:00:00")
        return self.summary(since)

    def summary_this_week(self) -> dict:
        since = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        return self.summary(since)

    def summary_this_month(self) -> dict:
        since = datetime.now(timezone.utc).strftime("%Y-%m-01 00:00:00")
        return self.summary(since)

    def summary_all_time(self) -> dict:
        return self.summary("2000-01-01 00:00:00")

    def performance(self, since: Optional[str] = None) -> dict:
        conn = self._conn()
        query = "SELECT * FROM trend_trades"
        params = []
        if since:
            query += " WHERE timestamp >= ?"
            params.append(since)
        rows = [dict(r) for r in conn.execute(query, params).fetchall()]
        conn.close()
        if not rows:
            return {"profit_factor": 0, "avg_win": 0, "avg_loss": 0, "largest_win": 0, "largest_loss": 0, "avg_duration": 0}
        wins = [t["pnl"] for t in rows if t["pnl"] > 0]
        losses = [t["pnl"] for t in rows if t["pnl"] <= 0]
        gross_wins = sum(wins) if wins else 0
        gross_losses = abs(sum(losses)) if losses else 0.001
        return {
            "profit_factor": round(gross_wins / gross_losses, 2),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else 0,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else 0,
            "largest_win": round(max(wins), 2) if wins else 0,
            "largest_loss": round(min(losses), 2) if losses else 0,
            "avg_duration": round(sum(t["duration_minutes"] for t in rows) / len(rows), 0),
        }
