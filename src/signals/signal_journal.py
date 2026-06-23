"""
signal_journal.py — SQLite journal for signal copy trading.
Logs every raw message (audit) and every executed trade with P&L.
"""

import sqlite3
import logging
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

DB_PATH = Path("data/signal_journal.db")


@dataclass
class SignalTrade:
    timestamp: str
    symbol: str
    channel_name: str
    action: str              # "OPEN_LONG" | "CLOSE" | etc
    entry_price: float
    current_price: float
    quantity: float
    realized_pnl: float
    exit_reason: str         # "stop_loss" | "tp1" | "tp2" | "tp3" | "manual" | "btc_danger"
    signal_confidence: str
    stop_loss: float
    take_profits: str        # JSON string
    tp1_hit: int
    tp2_hit: int
    tp3_hit: int
    raw_message: str
    parse_reasoning: str
    is_audit: int            # 1 = paper trade (audit mode), 0 = live


class SignalJournal:
    def __init__(self, db_path: Optional[Path] = None, state_suffix: str = ""):
        if db_path is None:
            db_path = Path(f"data/signal_journal{state_suffix}.db")
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init_db()

    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS raw_messages (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp     TEXT NOT NULL,
                    channel_id    INTEGER,
                    channel_name  TEXT,
                    message_id    INTEGER,
                    text          TEXT,
                    parsed_action TEXT,
                    parsed_pair   TEXT,
                    parse_reasoning TEXT,
                    quality_score INTEGER DEFAULT 0,
                    quality_reason TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_trades (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp       TEXT NOT NULL,
                    symbol          TEXT NOT NULL,
                    channel_name    TEXT,
                    action          TEXT,
                    entry_price     REAL,
                    current_price   REAL,
                    quantity        REAL,
                    realized_pnl    REAL,
                    exit_reason     TEXT,
                    signal_confidence TEXT,
                    stop_loss       REAL,
                    take_profits    TEXT,
                    tp1_hit         INTEGER DEFAULT 0,
                    tp2_hit         INTEGER DEFAULT 0,
                    tp3_hit         INTEGER DEFAULT 0,
                    raw_message     TEXT,
                    parse_reasoning TEXT,
                    is_audit        INTEGER DEFAULT 1
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_st_timestamp ON signal_trades(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_st_channel ON signal_trades(channel_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_rm_timestamp ON raw_messages(timestamp)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS signal_decision_states (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp      TEXT NOT NULL,
                    symbol         TEXT,
                    channel_name   TEXT,
                    decision       TEXT,
                    equity         REAL,
                    open_positions INTEGER,
                    open_notional  REAL,
                    drawdown_pct   REAL,
                    pair_features  TEXT,
                    btc_features   TEXT,
                    btc_regime     TEXT
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sds_ts ON signal_decision_states(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sds_symbol ON signal_decision_states(symbol)")
            # Migration: persist the DeepSeek quality score so /signal_history
            # can show it. CREATE TABLE above only adds these on a brand-new DB;
            # this ALTERs the existing EC2 database idempotently (SQLite has no
            # "ADD COLUMN IF NOT EXISTS", so guard with PRAGMA table_info).
            self._ensure_column(conn, "raw_messages", "quality_score", "INTEGER DEFAULT 0")
            self._ensure_column(conn, "raw_messages", "quality_reason", "TEXT DEFAULT ''")

    @staticmethod
    def _ensure_column(conn, table: str, column: str, definition: str):
        """Idempotently add a column to an existing table (no-op if present)."""
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

    def log_raw_message(self, channel_id: int, channel_name: str,
                        message_id: int, text: str,
                        parsed_action: str, parsed_pair: str,
                        parse_reasoning: str = "",
                        quality_score: int = 0, quality_reason: str = ""):
        with self._lock:
            try:
                with self._conn() as conn:
                    conn.execute(
                        "INSERT INTO raw_messages (timestamp, channel_id, channel_name, message_id, text, parsed_action, parsed_pair, parse_reasoning, quality_score, quality_reason) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (datetime.now(timezone.utc).isoformat(), channel_id, channel_name,
                         message_id, text, parsed_action, parsed_pair, parse_reasoning,
                         quality_score, quality_reason),
                    )
            except Exception as e:
                logger.error(f"Signal journal write failed: {e}")

    def log_trade(self, trade: SignalTrade):
        with self._lock:
            try:
                with self._conn() as conn:
                    conn.execute(
                        "INSERT INTO signal_trades "
                        "(timestamp, symbol, channel_name, action, entry_price, current_price, quantity, "
                        "realized_pnl, exit_reason, signal_confidence, stop_loss, take_profits, "
                        "tp1_hit, tp2_hit, tp3_hit, raw_message, parse_reasoning, is_audit) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (trade.timestamp, trade.symbol, trade.channel_name, trade.action,
                         trade.entry_price, trade.current_price, trade.quantity,
                         trade.realized_pnl, trade.exit_reason, trade.signal_confidence,
                         trade.stop_loss, trade.take_profits,
                         trade.tp1_hit, trade.tp2_hit, trade.tp3_hit,
                         trade.raw_message, trade.parse_reasoning, trade.is_audit),
                    )
            except Exception as e:
                logger.error(f"Signal trade journal write failed: {e}")

    def log_decision_state(self, timestamp: str, symbol: str, channel_name: str,
                           decision: str, equity: float, open_positions: int,
                           open_notional: float, drawdown_pct: float,
                           pair_features, btc_features, btc_regime: str):
        """Persist market + portfolio state at a signal decision (Phase 2 RL data).

        pair_features / btc_features are JSON strings of the 14 regime features,
        or None when feature computation was unavailable.
        """
        with self._lock:
            try:
                with self._conn() as conn:
                    conn.execute(
                        "INSERT INTO signal_decision_states "
                        "(timestamp, symbol, channel_name, decision, equity, open_positions, "
                        "open_notional, drawdown_pct, pair_features, btc_features, btc_regime) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (timestamp, symbol, channel_name, decision, equity, open_positions,
                         open_notional, drawdown_pct, pair_features, btc_features, btc_regime),
                    )
            except Exception as e:
                logger.error(f"Decision state journal write failed: {e}")

    def decision_states(self) -> list:
        """All decision-state rows (for the RL dataset export)."""
        try:
            with self._conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("SELECT * FROM signal_decision_states").fetchall()
                return [dict(r) for r in rows]
        except Exception:
            return []

    def summary(self, days: int = 0) -> dict:
        """Get P&L summary. days=0 means today, -1 means all time."""
        if days == 0:
            cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            where = f"timestamp >= '{cutoff}'"
        elif days > 0:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            where = f"timestamp >= '{cutoff}'"
        else:
            where = "1=1"

        try:
            with self._conn() as conn:
                row = conn.execute(
                    f"SELECT COUNT(*), SUM(realized_pnl), AVG(realized_pnl), "
                    f"SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) "
                    f"FROM signal_trades WHERE {where}"
                ).fetchone()
                total, total_pnl, avg_pnl, wins = row
                wins = wins or 0
                total = total or 0
                win_rate = (wins / total * 100) if total > 0 else 0
                return {
                    "total_trades": total,
                    "total_pnl": total_pnl or 0,
                    "avg_pnl": avg_pnl or 0,
                    "win_rate": round(win_rate, 1),
                }
        except Exception:
            return {"total_trades": 0, "total_pnl": 0, "avg_pnl": 0, "win_rate": 0}

    def summary_by_channel(self, days: int = 7) -> dict:
        """Get P&L breakdown by channel."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    f"SELECT channel_name, COUNT(*), SUM(realized_pnl), "
                    f"SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) "
                    f"FROM signal_trades WHERE timestamp >= ? GROUP BY channel_name",
                    (cutoff,),
                ).fetchall()
                result = {}
                for name, total, pnl, wins in rows:
                    wins = wins or 0
                    result[name] = {
                        "trades": total,
                        "pnl": pnl or 0,
                        "win_rate": round(wins / total * 100, 1) if total > 0 else 0,
                    }
                return result
        except Exception:
            return {}

    def channel_stats(self) -> list[dict]:
        """Get per-channel message and trade stats."""
        try:
            with self._conn() as conn:
                # Message counts by channel
                msg_rows = conn.execute(
                    "SELECT channel_name, COUNT(*), "
                    "SUM(CASE WHEN parsed_action = 'NOT_A_SIGNAL' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN parsed_action = 'OPEN_LONG' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN parsed_action = 'CLOSE' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN parsed_action = 'UPDATE_SL' THEN 1 ELSE 0 END) "
                    "FROM raw_messages GROUP BY channel_name ORDER BY COUNT(*) DESC"
                ).fetchall()

                # Trade counts by channel (approved/executed)
                trade_rows = conn.execute(
                    "SELECT channel_name, COUNT(*), "
                    "SUM(CASE WHEN action = 'rejected' OR action LIKE 'blocked%' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN action = 'OPEN_LONG' OR action = 'audit_entry' OR action = 'live_entry' THEN 1 ELSE 0 END), "
                    "SUM(realized_pnl) "
                    "FROM signal_trades GROUP BY channel_name"
                ).fetchall()
                trade_map = {r[0]: r for r in trade_rows}

                result = []
                for name, total_msgs, not_signal, open_long, close_cnt, update_sl in msg_rows:
                    trades = trade_map.get(name)
                    result.append({
                        "channel": name,
                        "messages": total_msgs,
                        "not_signal": not_signal or 0,
                        "signals": open_long or 0,
                        "closes": close_cnt or 0,
                        "updates": update_sl or 0,
                        "trades_total": trades[1] if trades else 0,
                        "trades_rejected": trades[2] if trades else 0,
                        "trades_approved": trades[3] if trades else 0,
                        "trades_pnl": trades[4] if trades else 0,
                    })
                return result
        except Exception:
            return []

    def recent_signals(self, limit: int = 10) -> list[dict]:
        """Get recent raw messages with parse results."""
        try:
            with self._conn() as conn:
                rows = conn.execute(
                    "SELECT timestamp, channel_name, parsed_action, parsed_pair, text, quality_score, quality_reason "
                    "FROM raw_messages ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
                return [
                    {"timestamp": r[0], "channel": r[1], "action": r[2],
                     "pair": r[3], "text": r[4][:100] if r[4] else "",
                     "quality_score": r[5] or 0, "quality_reason": r[6] or ""}
                    for r in rows
                ]
        except Exception:
            return []
