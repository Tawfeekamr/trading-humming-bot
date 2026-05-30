import threading
from enum import Enum


class GridState(Enum):
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    REACTIVATING = "REACTIVATING"
    DANGER = "DANGER"


class GridStateMachine:
    def __init__(self):
        self._state = GridState.PAUSED
        self._lock = threading.Lock()

    # Thresholds: aligned with actual RF model output range (0.35-0.80)
    ML_INFLUENCE_THRESHOLD = 0.55   # Confidence needed for ML to relax technicals
    ML_DANGER_MIN_CONFIDENCE = 0.55  # DANGER must be confident to block grid

    def evaluate(self, price: float, rsi: float, ema_200: float,
                 bb_lower: float, bb_upper: float,
                 rsi_overbought: float = 70.0, rsi_oversold: float = 35.0,
                 ml_regime: int = 0, ml_confidence: float = 0.0) -> GridState:
        with self._lock:
            # ML danger regime — only block if model is confident enough.
            # Low-confidence DANGER (e.g. 0.43) is unreliable and was keeping
            # the grid PAUSED 100% of the time since May 28.
            if ml_regime == 2 and ml_confidence >= self.ML_DANGER_MIN_CONFIDENCE:
                self._state = GridState.DANGER
                return self._state

            # ML trending (>threshold) relaxes overbought by +5, letting grid stay active
            # in confirmed uptrends despite slightly elevated RSI.
            ml_rsi_buffer = 5.0 if ml_regime == 1 and ml_confidence > self.ML_INFLUENCE_THRESHOLD else 0.0
            effective_overbought = rsi_overbought + ml_rsi_buffer

            if rsi > effective_overbought or price < ema_200:
                self._state = GridState.PAUSED
                return self._state

            # ML ranging (>threshold confidence) widens BB threshold, re-entering grid
            # sooner in confirmed ranging markets.
            bb_threshold = 1.05 if ml_regime == 0 and ml_confidence > self.ML_INFLUENCE_THRESHOLD else 1.02
            if rsi < rsi_oversold and price <= bb_lower * bb_threshold:
                self._state = GridState.REACTIVATING
                return self._state

            if price > ema_200 and rsi < effective_overbought:
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
            return self._state in (GridState.PAUSED, GridState.DANGER)
