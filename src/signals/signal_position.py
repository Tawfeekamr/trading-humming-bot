"""
signal_position.py — Position manager for signal copy trading with TP scaling.
"""

import fcntl
import json
import logging
import os
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from .futures_math import pnl as _side_pnl

logger = logging.getLogger(__name__)


@dataclass
class SignalPosition:
    symbol: str
    entry_price: float
    amount: float
    stop_loss: float
    take_profits: list[float]
    signal_confidence: str
    raw_message: str
    channel_name: str
    entry_timestamp: float = 0.0
    tp1_hit: bool = False
    tp2_hit: bool = False
    tp3_hit: bool = False
    amount_closed: float = 0.0
    realized_pnl: float = 0.0
    is_closed: bool = False
    exit_reason: str = ""
    tp1_close_pct: float = 0.33
    tp2_close_pct: float = 0.50
    order_id: str = ""
    side: str = "long"

    @property
    def remaining_amount(self) -> float:
        return self.amount - self.amount_closed

    @property
    def hold_minutes(self) -> int:
        return int((time.time() - self.entry_timestamp) / 60) if self.entry_timestamp else 0


class SignalPositionManager:
    def __init__(self, config: dict, state_suffix: str = ""):
        self._max_positions = config.get("max_positions", 3)
        self._tp1_close_pct = config.get("tp1_close_pct", 33) / 100
        self._tp2_close_pct = config.get("tp2_close_pct", 50) / 100
        self._data_dir = Path("data")
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._suffix = state_suffix
        self._positions: dict[str, SignalPosition] = {}
        self._lock = threading.Lock()
        self._load_state()

    @property
    def max_positions(self) -> int:
        """Max concurrent signal positions (read accessor for pre-trade checks)."""
        return self._max_positions

    def has_open_position(self, symbol: str) -> bool:
        with self._lock:
            pos = self._positions.get(symbol)
            return pos is not None and not pos.is_closed

    def get_open_positions(self) -> list[SignalPosition]:
        with self._lock:
            return [p for p in self._positions.values() if not p.is_closed]

    def get_position(self, symbol: str) -> Optional[SignalPosition]:
        with self._lock:
            pos = self._positions.get(symbol)
            return pos if pos and not pos.is_closed else None

    def open_position(self, symbol: str, entry_price: float, amount: float,
                      stop_loss: float, take_profits: list[float],
                      signal_confidence: str, raw_message: str,
                      channel_name: str, side: str = "long") -> Optional[SignalPosition]:
        with self._lock:
            open_count = sum(1 for p in self._positions.values() if not p.is_closed)
            if open_count >= self._max_positions:
                logger.warning(f"Max signal positions ({self._max_positions}) reached")
                return None
            if symbol in self._positions and not self._positions[symbol].is_closed:
                logger.warning(f"Signal position already open for {symbol}")
                return None

            pos = SignalPosition(
                symbol=symbol,
                entry_price=entry_price,
                amount=amount,
                stop_loss=stop_loss,
                take_profits=take_profits,
                signal_confidence=signal_confidence,
                raw_message=raw_message,
                channel_name=channel_name,
                entry_timestamp=time.time(),
                tp1_close_pct=self._tp1_close_pct,
                tp2_close_pct=self._tp2_close_pct,
                side=side,
            )
            self._positions[symbol] = pos
            self._save_state()
            return pos

    def partial_close(self, symbol: str, close_pct: float, price: float, reason: str) -> tuple:
        """Close a fraction of position. Returns (amount_closed, realized_pnl).

        Sets the matching tp_hit flag in the SAME locked _save_state as the
        amount reduction. A separate mark_tp_hit() call saved the flag in its
        own _save_state, so a crash between the two left disk in a mixed state
        (tp_hit=True but amount unchanged) and the next TP over-sold against a
        stale full amount on restart. Folding the flag in here makes the
        flag+amount update atomic.
        """
        with self._lock:
            pos = self._positions.get(symbol)
            if not pos or pos.is_closed:
                return (0.0, 0.0)

            close_amount = pos.remaining_amount * close_pct
            pnl = _side_pnl(pos.side, pos.entry_price, price, close_amount)
            pos.amount_closed += close_amount
            pos.realized_pnl += pnl
            if reason == "tp1":
                pos.tp1_hit = True
            elif reason == "tp2":
                pos.tp2_hit = True
            elif reason == "tp3":
                pos.tp3_hit = True

            logger.info(f"Signal partial close {symbol}: {close_pct:.0%} @ ${price:,.2f} "
                         f"({reason}, PnL: ${pnl:.2f})")
            self._save_state()
            return (close_amount, pnl)

    def close_position(self, symbol: str, price: float, reason: str) -> Optional[float]:
        """Fully close position. Returns net PnL.

        Sets tp3_hit atomically with is_closed when reason is "tp3" — previously
        the post-close mark_tp_hit(3) early-returned on the is_closed guard, so
        the flag was never persisted on a successful TP3.
        """
        with self._lock:
            pos = self._positions.get(symbol)
            if not pos or pos.is_closed:
                return None

            remaining = pos.remaining_amount
            pnl = _side_pnl(pos.side, pos.entry_price, price, remaining)
            pos.amount_closed = pos.amount
            pos.realized_pnl += pnl
            if reason == "tp3":
                pos.tp3_hit = True
            pos.is_closed = True
            pos.exit_reason = reason

            total_pnl = pos.realized_pnl
            logger.info(f"Signal close {symbol}: {reason} @ ${price:,.2f} "
                         f"(total PnL: ${total_pnl:.2f})")
            self._save_state()
            return total_pnl

    def update_stop_loss(self, symbol: str, new_sl: float):
        with self._lock:
            pos = self._positions.get(symbol)
            if pos and not pos.is_closed:
                pos.stop_loss = new_sl
                self._save_state()

    def mark_tp_hit(self, symbol: str, tp_level: int):
        """Set TP hit flag under lock. tp_level in {1, 2, 3}."""
        with self._lock:
            pos = self._positions.get(symbol)
            if not pos or pos.is_closed:
                return
            if tp_level == 1:
                pos.tp1_hit = True
            elif tp_level == 2:
                pos.tp2_hit = True
            elif tp_level == 3:
                pos.tp3_hit = True
            self._save_state()

    @contextmanager
    def _state_lock(self):
        """Advisory file lock shared with the Rust engine (fcntl.flock) so
        concurrent writes don't clobber each other's state. Blocks until acquired
        (Python can wait briefly; the Rust side uses a non-blocking try-lock)."""
        lock_path = self._data_dir / f"signal_positions{self._suffix}.lock"
        f = open(lock_path, "w")
        try:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(f, fcntl.LOCK_UN)
            f.close()

    def _save_state(self):
        path = self._data_dir / f"signal_positions{self._suffix}.json"
        tmp_path = self._data_dir / f"signal_positions{self._suffix}.json.tmp"
        with self._state_lock():
            # Read current disk state and preserve entries we don't track (e.g.
            # positions the Rust engine is managing) so our write can't erase them.
            try:
                disk = json.loads(path.read_text()) if path.exists() else {}
            except Exception:
                disk = {}
            merged = dict(disk)
            for symbol, pos in self._positions.items():
                # Don't clobber a same-position disk entry that Rust closed or took
                # further than our (possibly stale) in-memory copy — that's the
                # duplicate-close bug: a reverted close re-opens the position and it
                # gets closed AGAIN next tick/restart. A new open (different
                # entry_timestamp) still overwrites.
                disk_pos = disk.get(symbol)
                if disk_pos is not None and self._disk_more_advanced(disk_pos, pos):
                    continue
                # Persist closed positions too (do NOT prune by entry age) — pruning
                # a just-closed position held >24h left Rust's open copy as the only
                # one on disk → re-close loops. Re-opens (different entry_timestamp)
                # still overwrite, bounding growth.
                merged[symbol] = asdict(pos)
            # Atomic publish: write temp, then replace.
            tmp_path.write_text(json.dumps(merged, indent=2, default=str))
            os.replace(tmp_path, path)

    @staticmethod
    def _disk_more_advanced(disk_pos: dict, pos: "SignalPosition") -> bool:
        """True if disk is the SAME position (same entry_timestamp) AND has
        progressed further (closed, or a TP hit our in-memory copy lacks). In that
        case disk is authoritative and we must not overwrite it."""
        if abs(float(disk_pos.get("entry_timestamp", 0)) - pos.entry_timestamp) > 1e-3:
            return False  # different position (a new open) — allow overwrite
        if disk_pos.get("is_closed") and not pos.is_closed:
            return True
        for key, py_hit in (("tp1_hit", pos.tp1_hit), ("tp2_hit", pos.tp2_hit), ("tp3_hit", pos.tp3_hit)):
            if disk_pos.get(key) and not py_hit:
                return True
        return False

    def _load_state(self):
        path = self._data_dir / f"signal_positions{self._suffix}.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            for symbol, pos_data in data.items():
                pos_data.pop("symbol", None)
                pos = SignalPosition(symbol=symbol, **pos_data)
                if not pos.is_closed:
                    self._positions[symbol] = pos
        except Exception as e:
            logger.error(f"Failed to load signal positions: {e}")
