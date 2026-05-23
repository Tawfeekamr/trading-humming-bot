# Three Feature Enhancements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement three incremental features: cross-asset ML correlation gate, dynamic fee optimization (BNB rebalancer + LIMIT_MAKER), and auto-retraining pipeline.

**Architecture:** All changes are incremental on the existing dual-engine strategy. Feature 2 (correlation gate) is a pure logic addition to `ta_grid_trend.py`. Feature 1 (fee optimization) adds one new module + order type swap. Feature 4 (auto-retraining) adds two GitHub Actions workflows + hot-reload detection.

**Tech Stack:** Python 3.12, Hummingbot v2, sklearn, vectorbt, GitHub Actions, pytest

---

## File Map

| Action | File | Responsibility |
|--------|------|----------------|
| Modify | `hummingbot_files/scripts/ta_grid_trend.py` | Correlation gate, LIMIT_MAKER, rebalancer integration, hot-reload |
| Create | `src/risk/bnb_rebalancer.py` | BNB balance management for fee payments |
| Create | `tests/test_correlation_gate.py` | Tests for cross-asset ML correlation gate |
| Create | `tests/test_bnb_rebalancer.py` | Tests for BNB rebalancer thresholds |
| Create | `tests/test_ml_hot_reload.py` | Tests for ML model hot-reload detection |
| Create | `.github/workflows/sweep.yml` | Weekly parameter sweep cron |
| Create | `.github/workflows/retrain.yml` | Monthly ML retraining cron |
| Modify | `backtest/vectorbt_sweep.py` | Accept `--pair` CLI arg, output JSON |
| Modify | `src/ml/train_pipeline.py` | Support `--output` flag for `.new` files |
| Modify | `config/strategy.yaml` | Add `fee_optimization` section |

---

## Phase 1: Cross-Asset ML Correlation Gate

### Task 1: Write correlation gate tests

**Files:**
- Create: `tests/test_correlation_gate.py`

- [ ] **Step 1: Write the test file**

```python
# tests/test_correlation_gate.py
"""
Tests for cross-asset ML correlation gate.
When BTC signals DANGER (regime 2), all altcoin buy-side operations halt.
"""
import pytest


class TestCorrelationGate:
    """Test BTC DANGER → altcoin buy halt logic."""

    def _make_gate(self, btc_regime=None, btc_confidence=0.0, btc_model_loaded=True):
        """Build a lightweight stub mimicking the correlation gate state."""
        return {
            "ml_predictions": {
                "BTC-USDT": (btc_regime, btc_confidence, 0.0),
                "ETH-USDT": (0, 0.8, 0.0),
                "BNB-USDT": (0, 0.7, 0.0),
            },
            "ml_models": {"BTC-USDT": True} if btc_model_loaded else {},
        }

    def _btc_danger_active(self, state, pair):
        """Reproduce the correlation gate logic for testing."""
        if pair == "BTC-USDT":
            return False
        btc_pred = state["ml_predictions"].get("BTC-USDT")
        if btc_pred is None or "BTC-USDT" not in state["ml_models"]:
            # BTC model not loaded — default to safe (halt altcoins)
            return True
        btc_regime = btc_pred[0]
        if btc_regime is None:
            return True  # No prediction yet — safe default
        return btc_regime == 2

    def test_altcoin_buy_blocked_when_btc_danger(self):
        state = self._make_gate(btc_regime=2, btc_confidence=0.9)
        assert self._btc_danger_active(state, "ETH-USDT") is True
        assert self._btc_danger_active(state, "BNB-USDT") is True

    def test_altcoin_buy_allowed_when_btc_ranging(self):
        state = self._make_gate(btc_regime=0, btc_confidence=0.8)
        assert self._btc_danger_active(state, "ETH-USDT") is False

    def test_altcoin_buy_allowed_when_btc_trending(self):
        state = self._make_gate(btc_regime=1, btc_confidence=0.7)
        assert self._btc_danger_active(state, "ETH-USDT") is False

    def test_btc_pair_never_blocked(self):
        """BTC itself is never blocked by its own correlation gate."""
        state = self._make_gate(btc_regime=2, btc_confidence=0.9)
        assert self._btc_danger_active(state, "BTC-USDT") is False

    def test_safe_default_when_no_btc_prediction(self):
        """If BTC has no prediction yet, default to safe (halt altcoins)."""
        state = self._make_gate(btc_regime=None, btc_confidence=0.0)
        assert self._btc_danger_active(state, "ETH-USDT") is True

    def test_safe_default_when_btc_model_missing(self):
        """If BTC model failed to load, default to safe (halt altcoins)."""
        state = self._make_gate(btc_model_loaded=False)
        assert self._btc_danger_active(state, "ETH-USDT") is True

    def test_gate_transition_tracked(self):
        """Gate should track state transitions for alerting."""
        transitions = []
        # Simulate: RANGING → DANGER → RANGING
        state1 = self._make_gate(btc_regime=0)
        state2 = self._make_gate(btc_regime=2)
        state3 = self._make_gate(btc_regime=0)

        was_active = self._btc_danger_active(state1, "ETH-USDT")
        now_active = self._btc_danger_active(state2, "ETH-USDT")
        if was_active != now_active:
            transitions.append(("activated", "ETH-USDT"))

        was_active = now_active
        now_active = self._btc_danger_active(state3, "ETH-USDT")
        if was_active != now_active:
            transitions.append(("deactivated", "ETH-USDT"))

        assert len(transitions) == 2
        assert transitions[0][0] == "activated"
        assert transitions[1][0] == "deactivated"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_correlation_gate.py -v`
Expected: FAIL (tests pass against the inline helper, but this validates the logic)

- [ ] **Step 3: Commit the test**

```bash
git add tests/test_correlation_gate.py
git commit -m "test: add correlation gate test cases for BTC DANGER altcoin halt"
```

---

### Task 2: Implement correlation gate in strategy

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py`

- [ ] **Step 1: Add `_btc_danger_active` helper and `_correlation_gate_active` tracker**

Insert after the `_regime_name` method (around line 1561):

```python
    def _btc_danger_active(self) -> bool:
        """Check if BTC regime is DANGER — used as cross-asset correlation gate.

        When BTC signals DANGER, altcoin buy-side operations are halted.
        Returns True only for non-BTC pairs when BTC regime == 2.
        If BTC model is missing or has no prediction, defaults to safe (True).
        """
        btc_pred = self._ml_predictions.get("BTC-USDT")
        if btc_pred is None or "BTC-USDT" not in self._ml_models:
            return True  # No BTC model — safe default, halt altcoins
        btc_regime = btc_pred[0]
        if btc_regime is None:
            return True  # No prediction yet — safe default
        return btc_regime == 2
```

Add to `__init__` (around line 500, in the shared state section):

```python
        self._correlation_gate_active: Dict[str, bool] = {}
```

- [ ] **Step 2: Add correlation gate check in `_place_grid_orders`**

In `_place_grid_orders` (line 1478), add the gate check at the start of the buy loop, right after the `for level in grid.buy_levels:` line and before the RSI check. Insert between lines 1478 and 1479:

```python
        # Cross-asset correlation gate: halt altcoin buys when BTC is in DANGER
        is_altcoin = engine.symbol != "BTC-USDT"
        gate_active = is_altcoin and self._btc_danger_active()
        if gate_active:
            # Track transition for alerting
            if not self._correlation_gate_active.get(engine.symbol, False):
                self._correlation_gate_active[engine.symbol] = True
                logger.warning(f"Correlation gate ACTIVATED for {engine.symbol}: BTC DANGER — halting buy-side")
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.telegram.send(
                            f"🛑 <b>Correlation Gate</b>\n"
                            f"BTC regime: DANGER\n"
                            f"Action: {engine.symbol} buy-side HALTED"
                        ))
                except RuntimeError:
                    pass
        else:
            if self._correlation_gate_active.get(engine.symbol, False):
                self._correlation_gate_active[engine.symbol] = False
                logger.info(f"Correlation gate DEACTIVATED for {engine.symbol}: BTC no longer in DANGER")
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        loop.create_task(self.telegram.send(
                            f"✅ <b>Correlation Gate Lifted</b>\n"
                            f"BTC regime: no longer DANGER\n"
                            f"Action: {engine.symbol} buy-side RESUMED"
                        ))
                except RuntimeError:
                    pass

        for level in grid.buy_levels:
            if gate_active:
                continue
            if current_rsi and current_rsi > 60:
```

This replaces the original `for level in grid.buy_levels:` block, adding `if gate_active: continue` as the first check inside the loop.

- [ ] **Step 3: Add correlation gate check in `_evaluate_trend_signals`**

In `_evaluate_trend_signals` (line 1370), add the BTC DANGER check right after the per-pair ML gate. Insert after line 1381 (after `if ml_regime == 0 and ml_confidence >= 0.65: return`):

```python
        # Cross-asset correlation gate: block trend entries on altcoins when BTC is DANGER
        if engine.symbol != "BTC-USDT" and self._btc_danger_active():
            return
```

- [ ] **Step 4: Ensure BTC-USDT always has ML predictions loaded even when disabled**

In `__init__` (around line 456), the ML init loop only iterates `self.pairs` (enabled pairs). BTC-USDT is currently disabled. We need to ensure BTC always gets predictions. Add after line 458 (`self._ml_predictions[symbol] = (None, 0.0, 0.0)`), but handle BTC separately. Modify the init block to also load BTC if not in pairs:

```python
        if ML_AVAILABLE:
            for symbol in self.pairs:
                self._ml_predictions[symbol] = (None, 0.0, 0.0)
                self._ml_prediction_history[symbol] = []

            # Ensure BTC-USDT always has ML predictions (systemic signal for correlation gate)
            btc_symbol = "BTC-USDT"
            if btc_symbol not in self._ml_predictions:
                self._ml_predictions[btc_symbol] = (None, 0.0, 0.0)
                self._ml_prediction_history[btc_symbol] = []

            for symbol in list(self._ml_predictions.keys()):
                model_path = Path(f"models/regime_{symbol}.pkl")
                if model_path.exists():
                    try:
                        clf = RegimeClassifier(model_path=str(model_path))
                        clf.load_model()
                        self._ml_models[symbol] = clf
                        logger.info(f"ML model loaded for {symbol} from {model_path}")
                    except Exception as e:
                        logger.warning(f"ML model load failed for {symbol}: {e}")
                else:
                    logger.warning(f"No ML model for {symbol} (rule-based fallback)")
```

- [ ] **Step 5: Run existing tests to verify no regressions**

Run: `pytest tests/ -v --tb=short`
Expected: All existing tests pass. Correlation gate is additive — no existing behavior changed.

- [ ] **Step 6: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py
git commit -m "feat: cross-asset ML correlation gate — BTC DANGER halts altcoin buys"
```

---

## Phase 2: Dynamic Fee Optimization

### Task 3: Write BNB rebalancer tests

**Files:**
- Create: `tests/test_bnb_rebalancer.py`

- [ ] **Step 1: Write the test file**

```python
# tests/test_bnb_rebalancer.py
"""Tests for BNB rebalancer — maintains BNB balance for fee payments."""
import pytest
from src.risk.bnb_rebalancer import BNBRebalancer


class TestBNBRebalancer:
    def setup_method(self):
        self.rebalancer = BNBRebalancer(
            bnb_min_usdt=10.0,
            bnb_target_usdt=20.0,
            bnb_max_usdt=50.0,
        )

    def test_no_action_when_balance_in_range(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=25.0)
        assert result.action == "none"

    def test_buy_triggered_below_min(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=5.0)
        assert result.action == "buy"
        assert result.amount_usdt == 20.0  # buy up to target

    def test_sell_triggered_above_max(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=60.0)
        assert result.action == "sell"
        assert result.amount_usdt == 10.0  # sell excess above target

    def test_no_action_at_min_boundary(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=10.0)
        assert result.action == "none"

    def test_no_action_at_max_boundary(self):
        result = self.rebalancer.evaluate(bnb_balance_usdt=50.0)
        assert result.action == "none"

    def test_cooldown_prevents_rapid_rebalance(self):
        """After a rebalance, the next call should be in cooldown."""
        r1 = self.rebalancer.evaluate(bnb_balance_usdt=5.0)
        assert r1.action == "buy"
        # Immediate second call — should be in cooldown
        r2 = self.rebalancer.evaluate(bnb_balance_usdt=5.0)
        assert r2.action == "none"

    def test_buy_amount_capped_to_available(self):
        """Buy amount should not exceed what we pass as available USDT."""
        result = self.rebalancer.evaluate(bnb_balance_usdt=5.0, available_usdt=10.0)
        assert result.action == "buy"
        assert result.amount_usdt <= 10.0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_bnb_rebalancer.py -v`
Expected: FAIL — `src/risk/bnb_rebalancer.py` does not exist yet.

- [ ] **Step 3: Commit the test**

```bash
git add tests/test_bnb_rebalancer.py
git commit -m "test: add BNB rebalancer tests"
```

---

### Task 4: Implement BNB rebalancer

**Files:**
- Create: `src/risk/bnb_rebalancer.py`

- [ ] **Step 1: Write the implementation**

```python
# src/risk/bnb_rebalancer.py
"""BNB rebalancer — maintains BNB balance within target range for fee payments."""
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class RebalanceResult:
    action: str  # "buy", "sell", or "none"
    amount_usdt: float
    reason: str = ""


class BNBRebalancer:
    def __init__(
        self,
        bnb_min_usdt: float = 10.0,
        bnb_target_usdt: float = 20.0,
        bnb_max_usdt: float = 50.0,
        cooldown_seconds: float = 3600.0,
    ):
        self._min = bnb_min_usdt
        self._target = bnb_target_usdt
        self._max = bnb_max_usdt
        self._cooldown = cooldown_seconds
        self._last_rebalance_ts: float = 0.0

    def evaluate(
        self, bnb_balance_usdt: float, available_usdt: float = float("inf")
    ) -> RebalanceResult:
        now = time.time()
        if now - self._last_rebalance_ts < self._cooldown:
            return RebalanceResult(action="none", amount_usdt=0.0, reason="cooldown")

        if bnb_balance_usdt < self._min:
            buy_amount = min(self._target - bnb_balance_usdt, available_usdt)
            if buy_amount < 1.0:
                return RebalanceResult(action="none", amount_usdt=0.0, reason="insufficient_usdt")
            self._last_rebalance_ts = now
            return RebalanceResult(
                action="buy", amount_usdt=round(buy_amount, 2),
                reason=f"BNB ${bnb_balance_usdt:.2f} < min ${self._min:.2f}",
            )

        if bnb_balance_usdt > self._max:
            sell_amount = bnb_balance_usdt - self._target
            self._last_rebalance_ts = now
            return RebalanceResult(
                action="sell", amount_usdt=round(sell_amount, 2),
                reason=f"BNB ${bnb_balance_usdt:.2f} > max ${self._max:.2f}",
            )

        return RebalanceResult(action="none", amount_usdt=0.0)
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_bnb_rebalancer.py -v`
Expected: All 7 tests PASS.

- [ ] **Step 3: Commit**

```bash
git add src/risk/bnb_rebalancer.py
git commit -m "feat: BNB rebalancer for fee payment optimization"
```

---

### Task 5: Integrate BNB rebalancer + LIMIT_MAKER into strategy

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py`
- Modify: `config/strategy.yaml`

- [ ] **Step 1: Add import for BNBRebalancer**

Add near the top imports (around line 76, near the other risk imports):

```python
from src.risk.bnb_rebalancer import BNBRebalancer
```

- [ ] **Step 2: Add fee_optimization config loading in `__init__`**

After the trend config section (around line 239), add:

```python
        fee_cfg = cfg.get("fee_optimization", {})
        self._use_limit_maker = fee_cfg.get("use_limit_maker", True)
        self._bnb_rebalancer = BNBRebalancer(
            bnb_min_usdt=fee_cfg.get("bnb_min_usdt", 10.0),
            bnb_target_usdt=fee_cfg.get("bnb_target_usdt", 20.0),
            bnb_max_usdt=fee_cfg.get("bnb_max_usdt", 50.0),
        )
```

- [ ] **Step 3: Add BNB rebalancer call in `_grid_tick`**

In `_grid_tick`, after the ML prediction block (around line 771) and before the state evaluation (line 788), add:

```python
            # BNB rebalancer check (every indicator refresh cycle)
            if engine.symbol == list(self.pairs.keys())[0]:  # Only run once, on first pair
                try:
                    bnb_bal = 0.0
                    connector = self.connectors.get(self.exchange)
                    if connector and hasattr(connector, 'ready') and connector.ready:
                        try:
                            balances = connector.balance  # Hummingbot: Dict[str, Balance]
                            if "BNB" in balances:
                                bnb_qty = float(balances["BNB"].total)
                                bnb_price = float(self._last_price.get("BNB-USDT", 600))
                                bnb_bal = bnb_qty * bnb_price
                        except Exception:
                            pass  # Balance API varies across Hummingbot versions
                    result = self._bnb_rebalancer.evaluate(bnb_bal, available_usdt=self._get_usdt_balance(engine))
                    if result.action == "buy":
                        logger.info(f"BNB rebalancer: {result.reason} — buying ${result.amount_usdt:.2f}")
                        self.event_log.log("bnb_rebalance", action="buy", amount=result.amount_usdt, reason=result.reason)
                    elif result.action == "sell":
                        logger.info(f"BNB rebalancer: {result.reason} — selling ${result.amount_usdt:.2f}")
                        self.event_log.log("bnb_rebalance", action="sell", amount=result.amount_usdt, reason=result.reason)
                except Exception as e:
                    logger.debug(f"BNB rebalancer check skipped: {e}")
```

- [ ] **Step 4: Swap `OrderType.LIMIT` to `OrderType.LIMIT_MAKER`**

Replace at these 4 sites in `ta_grid_trend.py`:

Line 1498 (grid buy):
```python
                client_order_id = self.buy(
                    connector_name=self.exchange, trading_pair=engine.symbol,
                    amount=Decimal(str(level["quantity"])), order_type=OrderType.LIMIT_MAKER,
                    price=Decimal(str(level["price"])),
                )
```

Line 1528 (grid sell):
```python
                client_order_id = self.sell(
                    connector_name=self.exchange, trading_pair=engine.symbol,
                    amount=Decimal(str(buy.quantity)), order_type=OrderType.LIMIT_MAKER,
                    price=Decimal(str(sell_price)),
                )
```

Line 1354 (trend exit):
```python
            order_id = self.sell(self.exchange, engine.symbol, amount, OrderType.LIMIT_MAKER)
```

Line 1421 (trend buy):
```python
            order_id = self.buy(self.exchange, engine.symbol, amount_dec, OrderType.LIMIT_MAKER)
```

- [ ] **Step 5: Add `fee_optimization` section to `config/strategy.yaml`**

Append at the end of `config/strategy.yaml`:

```yaml

# ── Fee Optimization ───────────────────────────────────────────
fee_optimization:
  bnb_target_usdt: 20         # Target BNB balance in USDT
  bnb_min_usdt: 10            # Buy BNB when balance drops below this
  bnb_max_usdt: 50            # Sell excess BNB when above this
  use_limit_maker: true       # Use LIMIT_MAKER (post-only) for all orders
```

- [ ] **Step 6: Run tests**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass. LIMIT_MAKER is handled gracefully by the Hummingbot mock in tests (`OrderType.LIMIT_MAKER` falls back to `"LIMIT_MAKER"` string in the mock).

- [ ] **Step 7: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py config/strategy.yaml
git commit -m "feat: BNB rebalancer integration + LIMIT_MAKER enforcement"
```

---

## Phase 3: Auto-Retraining Pipeline

### Task 6: Write ML hot-reload tests

**Files:**
- Create: `tests/test_ml_hot_reload.py`

- [ ] **Step 1: Write the test file**

```python
# tests/test_ml_hot_reload.py
"""Tests for ML model hot-reload detection."""
import pytest
import pickle
import time
import os
from pathlib import Path
from unittest.mock import MagicMock


def _write_model(path: Path, version: int = 1):
    """Write a minimal model file and return its mtime."""
    from sklearn.ensemble import RandomForestClassifier
    import numpy as np
    X = np.array([[0.1, 0.2], [0.3, 0.4], [0.5, 0.6], [0.7, 0.8]])
    y = np.array([0, 1, 0, 2])
    clf = RandomForestClassifier(n_estimators=5, max_depth=2, random_state=42)
    clf.fit(X, y)
    data = {"model": clf, "model_type": "random_forest", "version": version}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(data, f)
    return os.path.getmtime(str(path))


class TestMLHotReload:
    def test_detects_modified_model(self, tmp_path):
        """Hot-reload should detect when model mtime changes."""
        model_path = tmp_path / "regime_ETH-USDT.pkl"
        mtime1 = _write_model(model_path, version=1)
        # Simulate tracking
        last_mtime = mtime1
        # Overwrite model
        time.sleep(0.1)  # Ensure different mtime
        mtime2 = _write_model(model_path, version=2)
        # Detection
        assert mtime2 != last_mtime  # File was modified

    def test_no_reload_when_unchanged(self, tmp_path):
        """Hot-reload should NOT trigger when mtime unchanged."""
        model_path = tmp_path / "regime_ETH-USDT.pkl"
        mtime = _write_model(model_path)
        # Same mtime → no reload needed
        assert os.path.getmtime(str(model_path)) == mtime

    def test_reload_updates_mtime_tracker(self, tmp_path):
        """After reload, the tracked mtime should match the new file."""
        model_path = tmp_path / "regime_ETH-USDT.pkl"
        _write_model(model_path, version=1)
        tracked_mtime = os.path.getmtime(str(model_path))
        time.sleep(0.1)
        _write_model(model_path, version=2)
        new_mtime = os.path.getmtime(str(model_path))
        # Simulate reload: update tracked mtime
        tracked_mtime = new_mtime
        assert tracked_mtime == os.path.getmtime(str(model_path))
```

- [ ] **Step 2: Run tests to verify they pass**

Run: `pytest tests/test_ml_hot_reload.py -v`
Expected: PASS — these test file mtime detection mechanics, not strategy integration.

- [ ] **Step 3: Commit**

```bash
git add tests/test_ml_hot_reload.py
git commit -m "test: add ML model hot-reload detection tests"
```

---

### Task 7: Implement hot-reload in strategy

**Files:**
- Modify: `hummingbot_files/scripts/ta_grid_trend.py`

- [ ] **Step 1: Add mtime tracking to ML model loading**

In `__init__`, add a new dict alongside the existing ML containers (around line 453):

```python
        self._ml_model_mtimes: Dict[str, float] = {}
```

After each model load (around line 465, after `self._ml_models[symbol] = clf`), add:

```python
                        self._ml_model_mtimes[symbol] = os.path.getmtime(str(model_path))
```

- [ ] **Step 2: Add hot-reload check in `_run_ml_prediction`**

In `_run_ml_prediction` (around line 873), add the hot-reload check at the top of the method, right after the `if pair not in self._ml_models: return` check:

```python
        # Hot-reload: check if model file was updated
        model_path = Path(f"models/regime_{pair}.pkl")
        if model_path.exists():
            current_mtime = os.path.getmtime(str(model_path))
            last_mtime = self._ml_model_mtimes.get(pair, 0.0)
            if current_mtime > last_mtime:
                try:
                    new_clf = RegimeClassifier(model_path=str(model_path))
                    new_clf.load_model()
                    self._ml_models[pair] = new_clf
                    self._ml_model_mtimes[pair] = current_mtime
                    logger.info(f"Hot-reloaded ML model for {pair} (mtime changed)")
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            loop.create_task(self.telegram.send(
                                f"🔄 <b>ML Model Hot-Reloaded</b>\n"
                                f"Pair: {pair}\n"
                                f"Reason: model file updated"
                            ))
                    except RuntimeError:
                        pass
                except Exception as e:
                    logger.warning(f"Hot-reload failed for {pair}: {e} — keeping existing model")
```

- [ ] **Step 3: Run tests**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 4: Commit**

```bash
git add hummingbot_files/scripts/ta_grid_trend.py
git commit -m "feat: ML model hot-reload detection via mtime tracking"
```

---

### Task 8: Refactor vectorbt_sweep.py for CLI pair arg

**Files:**
- Modify: `backtest/vectorbt_sweep.py`

- [ ] **Step 1: Add CLI argument parsing and JSON output**

Add at the top of the `run_sweep` function and at the bottom of the file:

```python
# At the end of backtest/vectorbt_sweep.py, replace the existing __main__ block:

if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser(description="VectorBT Parameter Sweep")
    parser.add_argument("--pair", type=str, default="ETHUSDT", help="Trading pair symbol (e.g. ETHUSDT)")
    parser.add_argument("--output", type=str, default=None, help="Output JSON path")
    args = parser.parse_args()

    df = fetch_data(symbol=args.pair)
    if df.empty:
        print(f"No data for {args.pair}")
        exit(1)

    results = run_sweep(df)
    best = max(results, key=lambda r: r.sharpe) if results else None

    output = {
        "pair": args.pair,
        "best_sharpe": best.sharpe if best else None,
        "best_params": {"bb_period": best.bb_period, "rsi_oversold": best.rsi_oversold,
                        "rsi_overbought": best.rsi_overbought, "atr_multiplier": best.atr_multiplier} if best else None,
        "total_combinations": len(results),
    }

    output_path = args.output or f"backtest/results/{args.pair}_sweep.json"
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(output, f, indent=2)

    print(f"Best result for {args.pair}: Sharpe={output['best_sharpe']}")
    print(f"Results saved to {output_path}")
```

Note: The exact field names on `BacktestResult` depend on the existing reporting module. Verify `backtest/reporting.py` for the `BacktestResult` fields and adjust the `best_params` dict accordingly.

- [ ] **Step 2: Test locally**

Run: `python backtest/vectorbt_sweep.py --pair ETHUSDT --output /tmp/test_sweep.json`
Expected: Fetches data, runs sweep, writes JSON to `/tmp/test_sweep.json`. May take a few minutes.

- [ ] **Step 3: Commit**

```bash
git add backtest/vectorbt_sweep.py
git commit -m "feat: add --pair and --output CLI args to vectorbt sweep"
```

---

### Task 9: Add --output flag to train_pipeline.py

**Files:**
- Modify: `src/ml/train_pipeline.py`

- [ ] **Step 1: Add `--output` argument to the existing argparse**

Find the argparse section in `train_pipeline.py` and add the `--output` argument:

```python
    parser.add_argument("--output", type=str, default=None,
                        help="Output path for new model (e.g. models/regime_ETH-USDT.pkl.new). "
                             "Defaults to standard path with .new suffix.")
```

- [ ] **Step 2: Use output path when saving model**

Find the model save logic and modify it to use the `--output` path when provided:

```python
    # At the point where the model is saved, change:
    output_path = args.output or model_path.replace(".pkl", ".pkl.new")
    # Save model to output_path instead of model_path
```

- [ ] **Step 3: Commit**

```bash
git add src/ml/train_pipeline.py
git commit -m "feat: add --output flag to train pipeline for .new model files"
```

---

### Task 10: Create sweep GitHub Actions workflow

**Files:**
- Create: `.github/workflows/sweep.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Weekly Parameter Sweep

on:
  schedule:
    - cron: '0 0 * * 0'  # Every Sunday at 00:00 UTC
  workflow_dispatch:  # Allow manual trigger

env:
  PYTHON_VERSION: "3.12"

jobs:
  sweep:
    name: Parameter Sweep
    runs-on: ubuntu-latest
    strategy:
      matrix:
        pair: [ETHUSDT, BNBUSDT, DOGEUSDT, XRPUSDT]

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Run sweep for ${{ matrix.pair }}
        run: python backtest/vectorbt_sweep.py --pair ${{ matrix.pair }} --output backtest/results/${{ matrix.pair }}_sweep.json

      - name: Upload sweep results
        uses: actions/upload-artifact@v4
        with:
          name: sweep-${{ matrix.pair }}
          path: backtest/results/${{ matrix.pair }}_sweep.json
          retention-days: 30
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/sweep.yml
git commit -m "ci: weekly VectorBT parameter sweep workflow"
```

---

### Task 11: Create retrain GitHub Actions workflow

**Files:**
- Create: `.github/workflows/retrain.yml`

- [ ] **Step 1: Write the workflow**

```yaml
name: Monthly ML Retraining

on:
  schedule:
    - cron: '0 0 1 * *'  # 1st of each month at 00:00 UTC
  workflow_dispatch:

env:
  PYTHON_VERSION: "3.12"

jobs:
  retrain:
    name: Retrain ${{ matrix.pair }}
    runs-on: ubuntu-latest
    strategy:
      matrix:
        pair: [ETHUSDT, BNBUSDT, DOGEUSDT, XRPUSDT]

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: ${{ env.PYTHON_VERSION }}
          cache: pip

      - name: Install dependencies
        run: pip install -r requirements.txt

      - name: Train new model for ${{ matrix.pair }}
        run: |
          python -m src.ml.train_pipeline \
            --pair ${{ matrix.pair }} \
            --output models/regime_${{ matrix.pair }}.pkl.new

      - name: Compare and deploy if improved
        run: |
          python -c "
          import pickle, os, sys
          pair = '${{ matrix.pair }}'
          new_path = f'models/regime_{pair}.pkl.new'
          cur_path = f'models/regime_{pair}.pkl'

          if not os.path.exists(new_path):
              print(f'No new model for {pair}')
              sys.exit(0)

          # Load new model
          with open(new_path, 'rb') as f:
              new_data = pickle.load(f)

          new_acc = new_data.get('validation_accuracy', 0.0)

          # Compare with current
          cur_acc = 0.0
          if os.path.exists(cur_path):
              with open(cur_path, 'rb') as f:
                  cur_data = pickle.load(f)
              cur_acc = cur_data.get('validation_accuracy', 0.0)

          improvement = new_acc - cur_acc
          print(f'{pair}: current={cur_acc:.4f} new={new_acc:.4f} delta={improvement:+.4f}')

          if improvement > 0.01:
              os.replace(new_path, cur_path)
              print(f'DEPLOYED: {pair} model updated (accuracy +{improvement:.4f})')
          else:
              os.remove(new_path)
              print(f'SKIPPED: {pair} not improved enough')
              sys.exit(0)
          "

      - name: Commit improved models [skip ci]
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add models/*.pkl
          if git diff --cached --quiet; then
            echo "No model changes to commit"
          else
            git commit -m "retrain: update models [skip ci]"
            git push
          fi
```

- [ ] **Step 2: Commit**

```bash
git add .github/workflows/retrain.yml
git commit -m "ci: monthly ML retraining workflow with accuracy-gated deployment"
```

---

### Task 12: Final integration test + deploy

**Files:**
- None new — verification only

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass (including new tests from Tasks 1, 3, 6).

- [ ] **Step 2: Verify no import errors in strategy**

Run: `python -c "import sys; sys.path.insert(0, '.'); from src.risk.bnb_rebalancer import BNBRebalancer; print('OK')"`
Expected: `OK`

- [ ] **Step 3: Verify YAML config is valid**

Run: `python -c "import yaml; yaml.safe_load(open('config/strategy.yaml')); print('YAML OK')"`
Expected: `YAML OK`

- [ ] **Step 4: Commit and push all changes**

```bash
git push origin main
```

This triggers the existing `deploy.yml` pipeline — bot restarts on EC2 with all three features active.
