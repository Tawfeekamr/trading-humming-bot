import json
from pathlib import Path
from typing import Dict, Literal


class CapitalManager:
    """Tracks capital allocation across multiple pairs."""

    EngineType = Literal["grid", "trend"]

    def __init__(self, total_capital: float, state_dir: Path, max_per_pair: float = 0.25):
        self._total = total_capital
        self._max_pct = max_per_pair
        self._state_dir = state_dir
        # {"DOGE-USDT": {"grid": 500.0, "trend": 250.0}, ...}
        self._allocations: Dict[str, Dict[str, float]] = {}

    @property
    def total_capital(self) -> float:
        return self._total

    @property
    def available(self) -> float:
        used = sum(
            amt for pair_allocs in self._allocations.values()
            for amt in pair_allocs.values()
        )
        return self._total - used

    def allocated(self, pair: str, engine: str) -> float:
        return self._allocations.get(pair, {}).get(engine, 0.0)

    def allocate(self, pair: str, engine: str, amount: float) -> bool:
        if amount <= 0:
            return False
        if amount > self.available:
            return False
        # Check per-pair limit
        pair_total = sum(self._allocations.get(pair, {}).values())
        limit = self._total * self._max_pct
        if pair_total + amount > limit:
            return False
        self._allocations.setdefault(pair, {})
        self._allocations[pair][engine] = self._allocations[pair].get(engine, 0.0) + amount
        return True

    def release(self, pair: str, engine: str):
        if pair in self._allocations and engine in self._allocations[pair]:
            del self._allocations[pair][engine]
            if not self._allocations[pair]:
                del self._allocations[pair]

    def save(self):
        self._state_dir.mkdir(parents=True, exist_ok=True)
        data = {
            "total_capital": self._total,
            "allocations": self._allocations,
        }
        path = self._state_dir / "capital_state.json"
        tmp = self._state_dir / "capital_state.json.tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        tmp.replace(path)

    def load(self):
        path = self._state_dir / "capital_state.json"
        if not path.exists():
            return
        with open(path) as f:
            data = json.load(f)
        self._total = data.get("total_capital", self._total)
        self._allocations = data.get("allocations", {})
