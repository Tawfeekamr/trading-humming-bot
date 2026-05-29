"""Position guard — enforces position limits across all strategies."""


class PositionGuard:
    def __init__(
        self,
        max_positions_per_pair: int = 2,
        max_total_positions: int = 3,
        max_exposure_pct: float = 80.0,
    ):
        self.max_positions_per_pair = max_positions_per_pair
        self.max_total_positions = max_total_positions
        self.max_exposure_pct = max_exposure_pct
        self._positions: dict[str, dict] = {}  # symbol → position info

    def can_open(
        self,
        symbol: str,
        strategy_id: str,
        proposed_cost: float,
        available_capital: float,
    ) -> tuple[bool, str]:
        """Check if a new position is allowed."""
        # Total count
        if len(self._positions) >= self.max_total_positions:
            return False, f"Max total positions reached ({self.max_total_positions})"

        # Per-pair count
        pair_count = sum(1 for p in self._positions.values() if p["symbol"] == symbol)
        if pair_count >= self.max_positions_per_pair:
            return False, f"Max per-pair positions reached for {symbol} ({self.max_positions_per_pair})"

        # Exposure
        total_exposure = sum(p["cost"] for p in self._positions.values())
        new_exposure_pct = (total_exposure + proposed_cost) / available_capital * 100
        if available_capital > 0 and new_exposure_pct > self.max_exposure_pct:
            return False, f"Max exposure reached ({new_exposure_pct:.1f}%)"

        # Duplicate check (same symbol + strategy)
        for p in self._positions.values():
            if p["symbol"] == symbol and p["strategy_id"] == strategy_id:
                return False, f"Duplicate position: {symbol} for {strategy_id}"

        return True, ""

    def register(self, key: str, symbol: str, strategy_id: str, cost: float):
        """Register an opened position."""
        self._positions[key] = {"symbol": symbol, "strategy_id": strategy_id, "cost": cost}

    def close(self, key: str):
        """Remove a closed position."""
        self._positions.pop(key, None)

    @property
    def open_count(self) -> int:
        return len(self._positions)
