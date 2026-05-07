from enum import Enum


class GridState(Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REACTIVATING = "REACTIVATING"


class GridStateMachine:
    def __init__(self):
        self.state = GridState.PAUSED

    def evaluate(self, price: float, rsi: float, ema_200: float,
                 bb_lower: float, bb_upper: float,
                 rsi_overbought: float = 70.0, rsi_oversold: float = 35.0) -> GridState:
        if rsi > rsi_overbought or price < ema_200:
            self.state = GridState.PAUSED
            return self.state
        if rsi < rsi_oversold and price <= bb_lower * 1.02:
            self.state = GridState.REACTIVATING
            return self.state
        if price > ema_200 and rsi < rsi_overbought:
            self.state = GridState.ACTIVE
            return self.state
        return self.state

    @property
    def is_active(self) -> bool:
        return self.state in (GridState.ACTIVE, GridState.REACTIVATING)

    @property
    def is_paused(self) -> bool:
        return self.state == GridState.PAUSED
