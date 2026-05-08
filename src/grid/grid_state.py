import threading
from enum import Enum


class GridState(Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REACTIVATING = "REACTIVATING"


class GridStateMachine:
    def __init__(self):
        self._state = GridState.PAUSED
        self._lock = threading.Lock()

    def evaluate(self, price: float, rsi: float, ema_200: float,
                 bb_lower: float, bb_upper: float,
                 rsi_overbought: float = 70.0, rsi_oversold: float = 35.0) -> GridState:
        with self._lock:
            if rsi > rsi_overbought or price < ema_200:
                self._state = GridState.PAUSED
                return self._state
            if rsi < rsi_oversold and price <= bb_lower * 1.02:
                self._state = GridState.REACTIVATING
                return self._state
            if price > ema_200 and rsi < rsi_overbought:
                self._state = GridState.ACTIVE
                return self._state
            return self._state

    @property
    def state(self) -> GridState:
        with self._lock:
            return self._state

    @state.setter
    def state(self, value: GridState):
        with self._lock:
            self._state = value

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._state in (GridState.ACTIVE, GridState.REACTIVATING)

    @property
    def is_paused(self) -> bool:
        with self._lock:
            return self._state == GridState.PAUSED
