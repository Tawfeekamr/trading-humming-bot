# Multi-Pair Strategy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable the trading bot to run multiple pairs simultaneously with shared capital and consolidated risk management.

**Architecture:** Single strategy instance managing a dictionary of PairEngine objects, one per enabled pair. Each PairEngine owns its own indicators, grid state, and trend state. Shared capital pool with first-come-first-served allocation and consolidated risk limits across all pairs.

**Tech Stack:** Python 3.10, Hummingbot V2 Strategy, pydantic dataclass, asyncio, SQLite, Streamlit, python-telegram-bot

**Spec:** `docs/superpowers/specs/2026-05-20-multi-pair-strategy-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `config/strategy.yaml` | Modify | Replace `pair`/`step_size` with `pairs` list |
| `hummingbot_files/scripts/pair_engine.py` | Create | Per-pair state container: indicators, grid, trend |
| `hummingbot_files/scripts/capital_manager.py` | Create | Shared capital allocation and tracking |
| `hummingbot_files/scripts/ta_grid_trend.py` | Modify | Multi-pair init, tick loop, order placement |
| `src/notifications/telegram_commands.py` | Modify | Multi-pair status, trend status, daily report |
| `src/notifications/pnl_reporter.py` | Modify | Consolidated multi-pair P&L reporting |
| `app.py` | Modify | Pair selector, per-pair and portfolio views |
| `tests/test_pair_engine.py` | Create | PairEngine unit tests |
| `tests/test_capital_manager.py` | Create | CapitalManager unit tests |

---

### Task 1: Config — Replace single pair with pairs list

**Files:**
- Modify: `config/strategy.yaml`

- [ ] **Step 1: Update strategy.yaml with pairs list**

Replace the `pair` field and grid `step_size` with a `pairs` list. Keep all other settings global.

```yaml
# ════════════════════════════════════════════════════════════════════
#  TA-Enhanced Multi-Pair Grid Bot — Strategy Configuration
#  All non-secret settings live here. Loaded by the strategy script.
# ════════════════════════════════════════════════════════════════════

# ── Pairs & Exchange ─────────────────────────────────────────────
pairs:
  - symbol: "DOGE-USDT"
    step_size: 1
    enabled: true
  - symbol: "ETH-USDT"
    step_size: 0.001
    enabled: true
  - symbol: "BTC-USDT"
    step_size: 0.00001
    enabled: false
  - symbol: "BNB-USDT"
    step_size: 0.01
    enabled: true
  - symbol: "XRP-USDT"
    step_size: 0.1
    enabled: true

exchange: "binance"
timeframe: "1h"

# ── Grid Parameters ───────────────────────────────────────────────
grid:
  levels: 2
  capital_usdt: 5000
  min_usdt_reserve: 100
  order_refresh_time: 60

# ── Indicator Settings ────────────────────────────────────────────
indicators:
  bollinger:
    period: 20
    std_dev: 2.0

  rsi:
    period: 14
    oversold: 35
    overbought: 70

  ema:
    period: 200

  atr:
    period: 14
    spacing_multiplier: 1.5

# ── Grid State Rules ──────────────────────────────────────────────
rules:
  activate_conditions:
    - "price > ema_200"
    - "rsi < 65"
  pause_conditions:
    - "price < ema_200"
    - "rsi > 70"
  reactivate_conditions:
    - "rsi < 35"
    - "price near lower_bb"

# ── Risk Management ───────────────────────────────────────────────
risk:
  max_drawdown_pct: 10
  daily_loss_limit_pct: 5
  max_base_exposure_pct: 80

# ── Dashboard ─────────────────────────────────────────────────────
dashboard:
  port: 8501

# ── Trend Engine ──────────────────────────────────────────────
trend:
  enabled: true
  capital: 5000
  ema_fast: 20
  ema_slow: 50
  ema_trend: 200
  rsi_period: 14
  rsi_min: 40
  rsi_max: 70
  min_signal_score: 3
  confirmation_ticks: 1
  risk_per_trade_pct: 2.0
  max_position_pct: 25.0
  max_positions: 2
  trailing_stop_pct: 1.5
  trailing_activation_pct: 1.5
  rr_ratio: 2.0
  sl_buffer_pct: 0.2
  max_drawdown_pct: 10.0
  daily_loss_limit_pct: 5.0
  timeframe: "1h"
```

- [ ] **Step 2: Commit**

```bash
git add config/strategy.yaml
git commit -m "chore: replace single pair config with multi-pair list"
```

---

### Task 2: Create CapitalManager — shared capital pool

**Files:**
- Create: `hummingbot_files/scripts/capital_manager.py`
- Create: `tests/test_capital_manager.py`

- [ ] **Step 1: Write failing tests for CapitalManager**

```python
# tests/test_capital_manager.py
import json
import pytest
from pathlib import Path
from unittest.mock import patch
from hummingbot_files.scripts.capital_manager import CapitalManager


class TestCapitalManager:
    def test_initial_state(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        assert cm.available == 5000.0
        assert cm.total_capital == 5000.0

    def test_allocate_grid_success(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        assert cm.allocate("DOGE-USDT", "grid", 1000.0) is True
        assert cm.available == 4000.0
        assert cm.allocated("DOGE-USDT", "grid") == 1000.0

    def test_allocate_insufficient_capital(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        assert cm.allocate("DOGE-USDT", "grid", 6000.0) is False
        assert cm.available == 5000.0

    def test_release(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        cm.allocate("DOGE-USDT", "grid", 1000.0)
        cm.release("DOGE-USDT", "grid")
        assert cm.available == 5000.0

    def test_max_per_pair_enforced(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, max_per_pair=0.25, state_dir=tmp_path)
        # 25% of 5000 = 1250 max per pair
        assert cm.allocate("DOGE-USDT", "grid", 1250.0) is True
        assert cm.allocate("DOGE-USDT", "trend", 1.0) is False

    def test_multiple_pairs(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        cm.allocate("DOGE-USDT", "grid", 1000.0)
        cm.allocate("ETH-USDT", "grid", 2000.0)
        assert cm.available == 2000.0

    def test_save_and_load(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        cm.allocate("DOGE-USDT", "grid", 1000.0)
        cm.save()

        cm2 = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        cm2.load()
        assert cm2.allocated("DOGE-USDT", "grid") == 1000.0
        assert cm2.available == 4000.0

    def test_release_nonexistent_is_noop(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        cm.release("DOGE-USDT", "grid")  # should not raise
        assert cm.available == 5000.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_capital_manager.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'hummingbot_files.scripts.capital_manager'`

- [ ] **Step 3: Implement CapitalManager**

```python
# hummingbot_files/scripts/capital_manager.py
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_capital_manager.py -v`
Expected: All 9 tests PASS

- [ ] **Step 5: Commit**

```bash
git add hummingbot_files/scripts/capital_manager.py tests/test_capital_manager.py
git commit -m "feat: add CapitalManager for shared multi-pair capital allocation"
```

---

### Task 3: Create PairEngine — per-pair state container

**Files:**
- Create: `hummingbot_files/scripts/pair_engine.py`
- Create: `tests/test_pair_engine.py`

- [ ] **Step 1: Write failing tests for PairEngine**

```python
# tests/test_pair_engine.py
import pytest
from hummingbot_files.scripts.pair_engine import PairEngine, PairConfig


class TestPairEngine:
    def test_config_derives_helpers(self):
        cfg = PairConfig(symbol="DOGE-USDT", step_size=1, enabled=True)
        assert cfg.base_asset == "DOGE"
        assert cfg.binance_symbol == "DOGEUSDT"
        assert cfg.display_pair == "DOGE/USDT"

    def test_config_disabled(self):
        cfg = PairConfig(symbol="BTC-USDT", step_size=0.00001, enabled=False)
        assert not cfg.enabled

    def test_engine_creates_indicators(self):
        cfg = PairConfig(symbol="DOGE-USDT", step_size=1, enabled=True)
        engine = PairEngine(cfg)
        assert engine.bb is not None
        assert engine.rsi is not None
        assert engine.ema is not None
        assert engine.atr is not None

    def test_engine_state_files(self, tmp_path):
        cfg = PairConfig(symbol="DOGE-USDT", step_size=1, enabled=True)
        engine = PairEngine(cfg, state_dir=tmp_path)
        assert engine.grid_state_path == tmp_path / "grid_state_DOGE.json"
        assert engine.trend_state_path == tmp_path / "trend_state_DOGE.json"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_pair_engine.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement PairEngine**

```python
# hummingbot_files/scripts/pair_engine.py
from dataclasses import dataclass, field
from pathlib import Path

from hummingbot_files.scripts.indicators import BollingerBands, RSI, EMA, ATRExponential


@dataclass
class PairConfig:
    symbol: str
    step_size: float
    enabled: bool = True

    @property
    def base_asset(self) -> str:
        return self.symbol.split("-")[0]

    @property
    def binance_symbol(self) -> str:
        return self.symbol.replace("-", "")

    @property
    def display_pair(self) -> str:
        return self.symbol.replace("-", "/")


class PairEngine:
    """Holds all per-pair state: indicators, grid, trend."""

    def __init__(self, config: PairConfig, state_dir: Path = Path("data")):
        self.config = config
        self.symbol = config.symbol
        self.base_asset = config.base_asset
        self.binance_symbol = config.binance_symbol
        self.display_pair = config.display_pair
        self.step_size = config.step_size
        self._state_dir = state_dir

        # Indicators — fresh instance per pair
        self.bb = BollingerBands(20, 2.0)
        self.rsi = RSI(14)
        self.ema = EMA(200)
        self.atr = ATRExponential(14)

        # Grid state
        self.grid_state = None  # populated by strategy
        self.grid_orders = []

        # Trend state
        self.trend_positions = {}
        self.trend_signals = None

        # Last known price for this pair
        self.last_price = 0.0

    @property
    def grid_state_path(self) -> Path:
        return self._state_dir / f"grid_state_{self.base_asset}.json"

    @property
    def trend_state_path(self) -> Path:
        return self._state_dir / f"trend_state_{self.base_asset}.json"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/test_pair_engine.py -v`
Expected: All 4 tests PASS

- [ ] **Step 5: Verify indicator import paths are correct**

Check that `BollingerBands`, `RSI`, `EMA`, `ATRExponential` are the actual class names used in the strategy. Run:
```bash
grep -n 'from.*import.*BollingerBands\|from.*import.*RSI\|from.*import.*EMA\|from.*import.*ATR' hummingbot_files/scripts/ta_grid_trend.py
```
If class names differ, update `pair_engine.py` to match.

- [ ] **Step 6: Commit**

```bash
git add hummingbot_files/scripts/pair_engine.py tests/test_pair_engine.py
git commit -m "feat: add PairEngine for per-pair indicator and state management"
```

---

### Task 4: Refactor ta_grid_trend.py — multi-pair init and tick loop

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py`

This is the largest task. The strategy script must be refactored from single-pair to multi-pair. The changes are:

1. **Config class**: Keep `trading_pair` and `step_size` as legacy defaults, add `pairs` list parsing
2. **`__init__`**: Create a `Dict[str, PairEngine]` instead of single indicators; create `CapitalManager`; register all enabled pairs in `markets`
3. **`on_tick`**: Loop over all enabled PairEngines
4. **Grid methods**: Accept `PairEngine` parameter instead of using `self.trading_pair`
5. **Trend methods**: Accept `PairEngine` parameter instead of using `self.trading_pair`
6. **State persistence**: Use per-pair state files from `PairEngine`
7. **Skip ML**: Disable regime classifier for multi-pair (indicator signals only)

- [ ] **Step 1: Update Config class to support pairs list**

In the `TAGridTrendConfig` dataclass, the `trading_pair` and `step_size` fields remain as Hummingbot config defaults. The YAML `pairs` list is read in `__init__` and overrides them.

No changes needed to the Config class itself — the multi-pair config is loaded from YAML in `__init__`.

- [ ] **Step 2: Update `__init__` to build PairEngine dict**

In `__init__`, after `cfg = self._load_config()`, parse the `pairs` list and create engines:

```python
# After cfg = self._load_config() ...
pairs_cfg = cfg.get("pairs", [])
if not pairs_cfg:
    # Legacy single-pair fallback
    pair = cfg.get("pair", config.trading_pair)
    step = grid_cfg.get("step_size", config.step_size)
    pairs_cfg = [{"symbol": pair, "step_size": step, "enabled": True}]

self.pairs: Dict[str, PairEngine] = {}
for p in pairs_cfg:
    pc = PairConfig(
        symbol=p["symbol"],
        step_size=p["step_size"],
        enabled=p.get("enabled", True),
    )
    if pc.enabled:
        self.pairs[pc.symbol] = PairEngine(pc, state_dir=Path("data"))

# Backward compat: first pair is the "primary"
self.trading_pair = list(self.pairs.keys())[0] if self.pairs else config.trading_pair
```

Update `markets` registration:

```python
markets[config.exchange] = {
    engine.symbol: {} for engine in self.pairs.values()
}
```

Create `CapitalManager`:

```python
from hummingbot_files.scripts.capital_manager import CapitalManager

total_cap = float(grid_cfg.get("capital_usdt", config.capital_usdt)) + float(trend_cfg.get("capital", 5000))
max_per_pair = float(trend_cfg.get("max_position_pct", 25.0)) / 100.0
self._capital_mgr = CapitalManager(
    total_capital=total_cap,
    state_dir=Path("data"),
    max_per_pair=max_per_pair,
)
self._capital_mgr.load()
```

- [ ] **Step 3: Update `on_tick` to loop over pairs**

```python
def on_tick(self):
    try:
        self._tick_count += 1
        connector = self.connectors.get(self.exchange)
        if not connector:
            return

        for symbol, engine in self.pairs.items():
            mid_price = connector.get_mid_price(symbol)
            if not mid_price:
                continue
            engine.last_price = float(mid_price)

            # Process grid for this pair
            self._grid_tick(engine)

            # Process trend for this pair
            self._trend_tick(engine)

        # Health + Telegram poll (unchanged, use primary pair)
        primary = list(self.pairs.values())[0] if self.pairs else None
        if primary:
            update_health(
                grid_state=self.state_machine.state.value,
                trend_healthy=not self._trend_breaker.halted,
                trend_positions=sum(len(e.trend_positions) for e in self.pairs.values()),
                last_signal_score=self._last_trend_score.total if self._last_trend_score else 0,
            )
    except Exception as e:
        self._safe_telegram_crash("on_tick", str(e), traceback.format_exc())
```

- [ ] **Step 4: Refactor grid methods to accept PairEngine**

Update `_grid_tick(self)` → `_grid_tick(self, engine: PairEngine)`. Inside, replace all `self.trading_pair` references with `engine.symbol`, `self.display_pair` with `engine.display_pair`, `self._bb` with `engine.bb`, etc.

Key substitutions inside grid methods:
- `self.trading_pair` → `engine.symbol`
- `self.display_pair` → `engine.display_pair`
- `self.base_asset` → `engine.base_asset`
- `self._bb` / `self.bb` → `engine.bb`
- `self._rsi` / `self.rsi` → `engine.rsi`
- `self._ema200` → `engine.ema`
- `self._atr` → `engine.atr`
- `self.step_size` → `engine.step_size`
- State file reads/writes → use `engine.grid_state_path`

- [ ] **Step 5: Refactor trend methods to accept PairEngine**

Same pattern as grid. Update:
- `_trend_tick(self)` → `_trend_tick(self, engine: PairEngine)`
- `_evaluate_trend_signals(self)` → `_evaluate_trend_signals(self, engine: PairEngine)`
- `_check_trend_exits(self)` → `_check_trend_exits(self, engine: PairEngine)`
- `_open_trend_position(self, ...)` → `_open_trend_position(self, engine: PairEngine, ...)`
- `_execute_trend_exit(self, ...)` → `_execute_trend_exit(self, engine: PairEngine, ...)`
- State persistence → use `engine.trend_state_path`
- Capital checks → use `self._capital_mgr`

- [ ] **Step 6: Disable ML for multi-pair**

At the ML initialization block, skip when multiple pairs are active:

```python
# Only init ML for single-pair mode
if len(self.pairs) <= 1 and ML_AVAILABLE:
    # ... existing ML init code ...
else:
    self._ml_classifier = None
    self._ml_regime = -1
```

- [ ] **Step 7: Run existing tests to check for regressions**

Run: `cd /Users/amro/WebstormProjects/trading-humming-bot && python -m pytest tests/ -v --tb=short`
Expected: All existing tests pass (they test components, not the full strategy)

- [ ] **Step 8: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py
git commit -m "feat: refactor ta_grid_trend for multi-pair init and tick loop"
```

---

### Task 5: Update Telegram notifications for multi-pair

**Files:**
- Modify: `src/notifications/telegram_commands.py`
- Modify: `src/notifications/pnl_reporter.py`

- [ ] **Step 1: Update `/status` command to show all pairs**

In `_cmd_status` (around line 241), iterate over `self.strategy.pairs`:

```python
def _cmd_status(self, update, context):
    strategy = self.strategy
    uptime_s = int(time.time() - self._started_at)
    hours, rem = divmod(uptime_s, 3600)
    minutes, secs = divmod(rem, 60)

    mode = os.environ.get("ENV", "paper").upper()
    cb_status = "🛑 HALTED" if strategy.circuit_breaker.halted else "✅ OK"

    lines = [
        "📊 <b>Bot Status</b>",
        "•••",
        f"Mode: {mode} | CB: {cb_status}",
        f"⏱ <b>Up:</b> {hours}h {minutes}m {secs}s",
        "•••",
    ]

    for symbol, engine in strategy.pairs.items():
        state = strategy.state_machine.state.value
        pending = strategy.order_tracker.total_pending
        lines.append(f"<b>{engine.display_pair}</b> | Grid: {state} | Pending: {pending}")

    lines.append("•••")
    total_capital = strategy._capital_mgr.total_capital
    available = strategy._capital_mgr.available
    lines.append(f"💰 Capital: ${total_capital:,.0f} | Available: ${available:,.0f}")

    update.message.reply_text("\n".join(lines), parse_mode="HTML")
```

- [ ] **Step 2: Update `/trend_status` command for all pairs**

In `_cmd_trend_status` (around line 691):

```python
def _cmd_trend_status(self, update, context):
    strategy = self.strategy
    lines = ["🤖 <b>TREND ENGINE</b>", "•••"]

    total_open = 0
    total_max = 0
    for symbol, engine in strategy.pairs.items():
        positions = list(engine.trend_positions.values())
        if not positions:
            lines.append(f"<b>{engine.display_pair}</b> — No positions")
            continue

        total_open += len(positions)
        total_max += getattr(strategy._position_manager, '_max_positions', 2)

        lines.append(f"<b>{engine.display_pair}</b> ({len(positions)} open)")
        for pos in positions:
            pnl_pct = (engine.last_price - pos.entry_price) / pos.entry_price * 100
            lines.append(
                f"  {pos.amount:.2f} {engine.base_asset} @ ${pos.entry_price:.2f} "
                f"| SL ${pos.stop_loss:.2f} TP ${pos.take_profit:.2f}"
            )
            lines.append(f"  P&L: {pnl_pct:+.1f}% | Trail: ${pos.trailing_stop:.2f}")

    lines.insert(2, f"Open positions: {total_open}/{total_max}")

    available = strategy._capital_mgr.available
    lines.append(f"Capital: ${strategy._capital_mgr.total_capital:,.2f} | Available: ${available:,.2f}")

    update.message.reply_text("\n".join(lines), parse_mode="HTML")
```

- [ ] **Step 3: Update `/daily_report` in PnLReporter for consolidated P&L**

In `pnl_reporter.py`, update `report_daily()` to include pair breakdown:

```python
def report_daily(self, pairs: dict = None) -> str:
    s = self.journal.summary_today()
    sw = self.journal.summary_this_week()
    sm = self.journal.summary_this_month()
    ts = self._trend_journal.summary_today()

    fmt = lambda v: f"${v:+,.2f}" if v else "$0.00"

    # Pair breakdown if available
    pair_lines = ""
    if pairs:
        pair_lines = "\n•••\n📊 <b>PER PAIR</b>\n"
        for symbol, engine in pairs.items():
            pair_trades = self.journal.summary_today(pair=symbol)
            pair_trend = self._trend_journal.summary_today(pair=symbol)
            pair_net = pair_trades.get("net_pnl", 0) + pair_trend.get("net_pnl", 0)
            if pair_net != 0 or pair_trades["total_trades"] > 0 or pair_trend["total_trades"] > 0:
                pair_lines += f"  {engine.display_pair}: {fmt(pair_net)}\n"

    msg = (
        f"📅 <b>Daily Report — {pd.Timestamp.now(tz='UTC').strftime('%b %d, %Y')}</b>\n"
        f"•••\n"
        f"🤖 <b>GRID BOT</b>\n"
        f"📊 Trades: {s['total_trades']} (✅{s['winning']} / ❌{s['losing']}) Win: {s['win_rate']}%\n"
        f"💰 Gross: {fmt(s['gross_pnl'])}  |  💸 Fees: {fmt(-s['total_fees'])}\n"
        f"📈 Net Today: {fmt(s['net_pnl'])}\n"
        f"•••\n"
        f"📈 <b>TREND BOT</b>\n"
        f"📊 Trades: {ts['total_trades']} (✅{ts['winning']} / ❌{ts['losing']}) Win: {ts['win_rate']:.1f}%\n"
        f"💰 Gross: {fmt(ts['gross_pnl'])}  |  💸 Fees: {fmt(-ts['total_fees'])}\n"
        f"📈 Net Today: {fmt(ts['net_pnl'])}\n"
        f"{pair_lines}"
        f"•••\n"
        f"🏆 <b>COMBINED PNL</b>\n"
        combined_today = s['net_pnl'] + ts['net_pnl']
        combined_week = sw['net_pnl'] + self._trend_journal.summary_this_week()['net_pnl']
        combined_month = sm['net_pnl'] + self._trend_journal.summary_this_month()['net_pnl']
        f"📈 Net Today: {fmt(combined_today)}\n"
        f"📆 Net Week:  {fmt(combined_week)}\n"
        f"🗓 Net Month: {fmt(combined_month)}\n"
    )
    return msg
```

- [ ] **Step 4: Update trade notification messages to include pair**

In `ta_grid_trend.py`, verify all notification messages already use `engine.display_pair` (or `self.display_pair`). Key locations to check:
- `_notify_state_change()` — grid state change notifications
- Buy/sell fill notifications in `_grid_fill()`
- Trend entry/exit notifications

These already use `self.display_pair`, which now resolves from `engine.display_pair` via the PairEngine parameter.

- [ ] **Step 5: Update `/start` command to list active pairs**

In `_cmd_start` (or equivalent help handler):

```python
def _cmd_start(self, update, context):
    strategy = self.strategy
    pairs_list = "\n".join(
        f"  • {e.display_pair}" for e in strategy.pairs.values()
    )
    msg = (
        f"🤖 <b>Multi-Pair Trading Bot</b>\n"
        f"•••\n"
        f"📊 Active pairs:\n{pairs_list}\n"
        f"•••\n"
        f"Commands: /status /trend_status /pnl /balance\n"
        f"/trades /pending /pause /resume"
    )
    update.message.reply_text(msg, parse_mode="HTML")
```

- [ ] **Step 6: Commit**

```bash
git add src/notifications/telegram_commands.py src/notifications/pnl_reporter.py hummingbot_files/scripts/ta_grid_trend.py
git commit -m "feat: update Telegram notifications for multi-pair status and reports"
```

---

### Task 6: Update dashboard for multi-pair display

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Add pair selector to Streamlit sidebar**

At the top of the dashboard, after imports, add:

```python
# Pair selector
active_pairs = list(strategy.pairs.keys()) if hasattr(strategy, 'pairs') else [strategy.trading_pair]
selected_pair = st.sidebar.selectbox(
    "Trading Pair",
    options=active_pairs,
    format_func=lambda p: strategy.pairs[p].display_pair if hasattr(strategy, 'pairs') else p,
)
engine = strategy.pairs.get(selected_pair) if hasattr(strategy, 'pairs') else None
```

- [ ] **Step 2: Update trade history table to filter by pair**

Where trades are displayed, add pair filter:

```python
# Filter trades by selected pair
if engine:
    df = df[df["pair"] == selected_pair]
```

- [ ] **Step 3: Add portfolio overview section**

Add a summary section showing all pairs:

```python
st.sidebar.markdown("### Portfolio")
if hasattr(strategy, 'pairs'):
    for symbol, eng in strategy.pairs.items():
        st.sidebar.metric(
            eng.display_pair,
            f"${eng.last_price:.4f}",
            delta=f"{eng.last_price % 1:.2f}" if eng.last_price else None,
        )
    st.sidebar.metric("Available Capital", f"${strategy._capital_mgr.available:,.0f}")
```

- [ ] **Step 4: Commit**

```bash
git add app.py
git commit -m "feat: update Streamlit dashboard with multi-pair selector and portfolio view"
```

---

### Task 7: Update grid-only script for multi-pair

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_btcusdt.py`

Apply the same pattern as Task 4 to the grid-only strategy. This is a lighter version since there's no trend engine.

- [ ] **Step 1: Update `__init__` to parse pairs and create engines**

In `__init__`, after `cfg = self._load_config()`, add the same pairs parsing block as Task 4 Step 2:

```python
pairs_cfg = cfg.get("pairs", [])
if not pairs_cfg:
    pair = cfg.get("pair", config.trading_pair)
    step = grid_cfg.get("step_size", config.step_size)
    pairs_cfg = [{"symbol": pair, "step_size": step, "enabled": True}]

self.pairs: Dict[str, PairEngine] = {}
for p in pairs_cfg:
    pc = PairConfig(symbol=p["symbol"], step_size=p["step_size"], enabled=p.get("enabled", True))
    if pc.enabled:
        self.pairs[pc.symbol] = PairEngine(pc, state_dir=Path("data"))

self.trading_pair = list(self.pairs.keys())[0] if self.pairs else config.trading_pair
```

Update markets registration:
```python
markets[config.exchange] = {engine.symbol: {} for engine in self.pairs.values()}
```

- [ ] **Step 2: Update `on_tick` to loop over pairs**

```python
def on_tick(self):
    try:
        self._tick_count += 1
        connector = self.connectors.get(self.exchange)
        if not connector:
            return

        for symbol, engine in self.pairs.items():
            mid_price = connector.get_mid_price(symbol)
            if not mid_price:
                continue
            engine.last_price = float(mid_price)
            self._grid_tick(engine)

        update_health(
            grid_state=self.state_machine.state.value,
            trend_healthy=True,
            trend_positions=0,
            last_signal_score=0,
        )
    except Exception as e:
        self._safe_telegram_crash("on_tick", str(e), traceback.format_exc())
```

- [ ] **Step 3: Refactor grid methods to accept PairEngine**

Replace all `self.trading_pair` with `engine.symbol`, `self.display_pair` with `engine.display_pair`, `self.base_asset` with `engine.base_asset`, indicator references (`self._bb` etc.) with `engine.bb` etc., and `self.step_size` with `engine.step_size`.

- [ ] **Step 4: Disable ML for multi-pair**

```python
if len(self.pairs) <= 1 and ML_AVAILABLE:
    # ... existing ML init code ...
else:
    self._ml_classifier = None
    self._ml_regime = -1
```

- [ ] **Step 5: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_btcusdt.py
git commit -m "feat: update grid-only strategy for multi-pair support"
```

---

### Task 8: Clean up old single-pair state files on server

**Files:**
- None (server operation)

- [ ] **Step 1: Clear stale state files on EC2**

Before deploying, remove old single-pair state files:

```bash
aws ssm send-command --instance-ids "i-0eafde6592d97eab2" --document-name "AWS-RunShellScript" \
  --parameters 'commands=["cd /home/ec2-user/trading-humming-bot && rm -f data/grid_state.json data/trend_state.json data/capital_state.json && ls -la data/"]'
```

This ensures the bot starts fresh with per-pair state files on first run.

---

### Task 9: Deploy and verify

- [ ] **Step 1: Push all changes to main**

```bash
git push origin main
```

- [ ] **Step 2: Watch CI pipeline**

```bash
gh run watch --exit-status
```

Expected: Tests pass, deploy succeeds, containers running.

- [ ] **Step 3: Verify via Telegram**

Send `/start` → should list all 4 enabled pairs (DOGE, ETH, BNB, XRP; BTC disabled).
Send `/status` → should show per-pair grid state.
Send `/trend_status` → should show no open positions (fresh start).

- [ ] **Step 4: Monitor logs for multi-pair activity**

```bash
aws ssm send-command --instance-ids "i-0eafde6592d97eab2" --document-name "AWS-RunShellScript" \
  --parameters 'commands=["cd /home/ec2-user/trading-humming-bot && docker compose logs --tail=50 bot 2>&1 | grep -i pair"]'
```

Expected: Log lines showing multiple pairs being processed in each tick.
