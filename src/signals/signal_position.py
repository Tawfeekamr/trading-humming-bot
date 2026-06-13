"""
signal_position.py — Position manager for signal copy trading with TP scaling.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

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

    @property
    def remaining_amount(self) -> float:
        return self.amount - self.amount_closed

    @property
    def hold_minutes(self) -> int:
        return int((time.time() - self.entry_timestamp) / 60) if self.entry_timestamp else 0


class SignalPositionManager:
    def __init__(self, config: dict):
        self._max_positions = config.get("max_positions", 3)
        self._tp1_close_pct = config.get("tp1_close_pct", 33) / 100
        self._tp2_close_pct = config.get("tp2_close_pct", 50) / 100
        self._data_dir = Path("data")
        self._data_dir.mkdir(parents=True, exist_ok=True)
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
                      channel_name: str) -> Optional[SignalPosition]:
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
            )
            self._positions[symbol] = pos
            self._save_state()
            return pos

    def partial_close(self, symbol: str, close_pct: float, price: float, reason: str) -> tuple:
        """Close a fraction of position. Returns (amount_closed, realized_pnl)."""
        with self._lock:
            pos = self._positions.get(symbol)
            if not pos or pos.is_closed:
                return (0.0, 0.0)

            close_amount = pos.remaining_amount * close_pct
            pnl = (price - pos.entry_price) * close_amount
            pos.amount_closed += close_amount
            pos.realized_pnl += pnl

            logger.info(f"Signal partial close {symbol}: {close_pct:.0%} @ ${price:,.2f} "
                         f"({reason}, PnL: ${pnl:.2f})")
            self._save_state()
            return (close_amount, pnl)

    def close_position(self, symbol: str, price: float, reason: str) -> Optional[float]:
        """Fully close position. Returns net PnL."""
        with self._lock:
            pos = self._positions.get(symbol)
            if not pos or pos.is_closed:
                return None

            remaining = pos.remaining_amount
            pnl = (price - pos.entry_price) * remaining
            pos.amount_closed = pos.amount
            pos.realized_pnl += pnl
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

    def _save_state(self):
        state = {}
        for symbol, pos in self._positions.items():
            if not pos.is_closed or (time.time() - pos.entry_timestamp) < 86400:
                state[symbol] = asdict(pos)
        path = self._data_dir / "signal_positions.json"
        path.write_text(json.dumps(state, indent=2, default=str))

    def _load_state(self):
        path = self._data_dir / "signal_positions.json"
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
