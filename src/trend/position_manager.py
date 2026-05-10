import json
import logging
import threading
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TrendPosition:
    entry_order_id: str
    entry_price: float
    amount: float
    stop_loss: float
    take_profit: float
    entry_time: str
    trailing_stop: float = 0.0
    trailing_activated: bool = False
    highest_price: float = 0.0

    def __post_init__(self):
        if self.trailing_stop == 0.0:
            self.trailing_stop = self.stop_loss
        if self.highest_price == 0.0:
            self.highest_price = self.entry_price


class PositionManager:
    def __init__(self, capital: float = 2000.0, max_positions: int = 1,
                 risk_per_trade_pct: float = 2.0, max_position_pct: float = 75.0,
                 trailing_stop_pct: float = 1.5,
                 trailing_activation_pct: float = 1.5) -> None:
        self._capital = capital
        self._max_positions = max_positions
        self._risk_per_trade_pct = risk_per_trade_pct / 100.0
        self._max_position_pct = max_position_pct / 100.0
        self._trailing_stop_pct = trailing_stop_pct / 100.0
        self._trailing_activation_pct = trailing_activation_pct / 100.0
        self._positions: dict[str, TrendPosition] = {}
        self._lock = threading.Lock()

    @property
    def open_count(self) -> int:
        return len(self._positions)

    def can_open(self) -> bool:
        return len(self._positions) < self._max_positions

    def open_position(self, entry_order_id: str, entry_price: float,
                      amount: float, stop_loss: float, take_profit: float,
                      entry_time: str) -> Optional[TrendPosition]:
        with self._lock:
            if not self.can_open():
                return None
            pos = TrendPosition(
                entry_order_id=entry_order_id,
                entry_price=entry_price, amount=amount,
                stop_loss=stop_loss, take_profit=take_profit,
                entry_time=entry_time,
            )
            self._positions[entry_order_id] = pos
            return pos

    def close_position(self, order_id: str, exit_price: float,
                       exit_reason: str) -> Optional[dict]:
        with self._lock:
            pos = self._positions.pop(order_id, None)
            if pos is None:
                return None
            pnl = (exit_price - pos.entry_price) * pos.amount
            pnl_pct = (exit_price - pos.entry_price) / pos.entry_price * 100
            try:
                entry_dt = datetime.fromisoformat(pos.entry_time.replace("Z", "+00:00"))
                duration_min = int((datetime.now(entry_dt.tzinfo) - entry_dt).total_seconds() / 60)
            except (ValueError, TypeError):
                duration_min = 0
            return {
                "order_id": order_id,
                "entry_price": pos.entry_price,
                "exit_price": exit_price,
                "amount": pos.amount,
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "exit_reason": exit_reason,
                "duration_minutes": duration_min,
                "stop_loss": pos.stop_loss,
                "take_profit": pos.take_profit,
                "trailing_stop": pos.trailing_stop,
            }

    def get_position(self, order_id: str) -> Optional[TrendPosition]:
        return self._positions.get(order_id)

    def get_all_positions(self) -> list[TrendPosition]:
        return list(self._positions.values())

    def check_exits(self, current_price: float) -> list[dict]:
        exits = []
        with self._lock:
            for order_id, pos in list(self._positions.items()):
                if current_price >= pos.take_profit:
                    exits.append({"order_id": order_id, "reason": "take_profit", "exit_price": pos.take_profit})
                elif current_price <= pos.stop_loss:
                    exits.append({"order_id": order_id, "reason": "stop_loss", "exit_price": pos.stop_loss})
                elif pos.trailing_activated and current_price <= pos.trailing_stop:
                    exits.append({"order_id": order_id, "reason": "trailing_stop", "exit_price": pos.trailing_stop})
        return exits

    def update_trailing(self, pos: TrendPosition, current_price: float) -> None:
        if current_price > pos.highest_price:
            pos.highest_price = current_price
        if not pos.trailing_activated:
            activation_price = pos.entry_price * (1 + self._trailing_activation_pct)
            if current_price >= activation_price:
                pos.trailing_activated = True
        if pos.trailing_activated:
            new_trail = current_price * (1 - self._trailing_stop_pct)
            if new_trail > pos.trailing_stop:
                pos.trailing_stop = round(new_trail, 4)

    def calculate_position_size(self, entry_price: float, stop_loss_price: float) -> float:
        sl_distance = abs(entry_price - stop_loss_price)
        if sl_distance == 0:
            return 0.0
        risk_amount = self._capital * self._risk_per_trade_pct
        # Calculate size based on risk amount and stop loss distance
        size = risk_amount / sl_distance
        max_notional = self._capital * self._max_position_pct
        # Cap position size at max notional
        max_size = max_notional / entry_price
        size = min(size, max_size)
        return round(size, 4)

    def save_state(self, path: Path) -> None:
        data = {"capital": self._capital, "positions": {oid: asdict(pos) for oid, pos in self._positions.items()}}
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, indent=2))
        tmp_path.replace(path)

    def load_state(self, path: Path) -> None:
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
            self._positions.clear()
            for oid, pdata in data.get("positions", {}).items():
                self._positions[oid] = TrendPosition(**pdata)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"Failed to load trend state: {e}")
