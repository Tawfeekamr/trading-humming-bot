# Gate.io Paper Futures Fallback — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the futures signal engine trade every signal coin (ICP/FET/INJ + majors) as a pure paper simulator priced off Gate.io USDT-perpetual data, instead of crashing on Binance testnet's `fapi -1121 Invalid symbol`.

**Architecture:** Add a `PaperFuturesConnector` (same method surface as `BinanceFuturesConnector`) that uses Gate.io perp `mark_price` for pricing and synthetic order ids for open/close. Wire it into `run_signal_listener` in place of the Binance connector (no API keys needed). Add a one-line guard so a futures price failure doesn't silently fall back to the Gate *spot* ticker. The engine's existing leverage/liquidation/P&L math is reused unchanged.

**Tech Stack:** Python 3, stdlib only (`urllib.request`, `json`, `itertools`), pytest, Gate.io public REST (no auth).

## Global Constraints

- **Paper only.** No real money, no real exchange orders, no API keys, anywhere in this change.
- **Stdlib only** for the connector — no new pip dependencies (mirror `binance_futures_connector.py`, which uses `urllib`).
- **Symbol mapping:** `"ICP-USDT"` → Gate contract `"ICP_USDT"` via `symbol.replace("-", "_")`.
- **Gate.io endpoint:** `https://api.gateio.ws/api/v4/futures/usdt/tickers?contract=<CONTRACT>` (public, no auth); read `mark_price`, fall back to `last`, return `0.0` on any error/empty.
- **TDD:** write the failing test, watch it fail, implement, watch it pass, commit — for every task.
- **Branch:** `fix/futures-gate-io-paper-fallback` (already created; spec committed there).
- **The spot engine must not change behavior** — every futures-only path is gated on `self._futures_mode`.

---

## File Structure

| File | Action | Responsibility |
|---|---|---|
| `src/signals/paper_futures_connector.py` | **Create** | Paper futures connector: Gate.io perp pricing + synthetic no-op orders. Holds no position state. |
| `tests/signals/test_paper_futures_connector.py` | **Create** | Unit tests for the connector. |
| `src/signals/signal_engine.py` | **Modify** (`_get_current_price`, ~L778) | One-line guard: in futures mode, skip the Gate *spot* price fallback. |
| `tests/signals/test_futures_price_guard.py` | **Create** | Tests pinning the futures/spot fallback behavior. |
| `config/strategy.yaml` | **Modify** (`signals_futures`, ~L231) | Add `exchange: gate_io_paper_futures`; flip `testnet: false`. |
| `tests/test_futures_boot.py` | **Modify** | Update assertions to the new paper contract. |
| `src/run_signal_listener.py` | **Modify** (futures block, ~L222) | Extract `_build_futures_engine`; swap to `PaperFuturesConnector`; drop the Binance-key gate. |
| `tests/test_futures_wiring.py` | **Create** | Unit test: `_build_futures_engine` returns a paper-connected engine with no keys. |
| `tests/signals/test_futures_paper_execution.py` | **Create** | Regression test: non-Binance ICP + major BTC open paper positions via the real connector (the `-1121` guard). |

---

### Task 1: `PaperFuturesConnector`

**Files:**
- Create: `src/signals/paper_futures_connector.py`
- Test: `tests/signals/test_paper_futures_connector.py`

**Interfaces:**
- Produces: `PaperFuturesConnector(default_leverage=3)` with methods `get_price(symbol)->float`, `set_leverage(symbol, leverage)->dict`, `set_margin_type(symbol, margin_type="ISOLATED")->dict`, `open(symbol, side, qty, order_type="MARKET", price=None)->dict`, `close(symbol, side, qty)->dict`, `get_position(symbol)->None`. This is the same surface the engine already calls on `BinanceFuturesConnector` / `FakeConn` (see `tests/test_signal_futures_engine.py:15-22`).

- [ ] **Step 1: Write the failing tests**

Create `tests/signals/test_paper_futures_connector.py`:

```python
# tests/signals/test_paper_futures_connector.py
import sys
import pathlib
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from src.signals.paper_futures_connector import PaperFuturesConnector


def _mock_resp(payload):
    m = MagicMock()
    m.__enter__.return_value = m
    m.read.return_value = json.dumps(payload).encode()
    return m


def test_get_price_maps_symbol_and_reads_mark():
    conn = PaperFuturesConnector()
    captured = {}

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_resp([{"contract": "ICP_USDT", "mark_price": "2.17", "last": "2.18"}])

    with patch("urllib.request.urlopen", side_effect=fake):
        assert conn.get_price("ICP-USDT") == 2.17
    assert "contract=ICP_USDT" in captured["url"]
    assert "futures/usdt/tickers" in captured["url"]


def test_get_price_falls_back_to_last_when_no_mark():
    conn = PaperFuturesConnector()
    with patch("urllib.request.urlopen",
               side_effect=lambda req, timeout=None:
                   _mock_resp([{"contract": "FET_USDT", "last": "0.18"}])):
        assert conn.get_price("FET-USDT") == 0.18


def test_get_price_maps_btc_dash_to_underscore():
    conn = PaperFuturesConnector()
    captured = {}

    def fake(req, timeout=None):
        captured["url"] = req.full_url
        return _mock_resp([{"contract": "BTC_USDT", "mark_price": "60000", "last": "60001"}])

    with patch("urllib.request.urlopen", side_effect=fake):
        assert conn.get_price("BTC-USDT") == 60000.0
    assert "contract=BTC_USDT" in captured["url"]


def test_get_price_returns_zero_on_empty_or_transport_error():
    conn = PaperFuturesConnector()
    # Empty payload (unknown contract) -> 0.0, no raise.
    with patch("urllib.request.urlopen",
               side_effect=lambda req, timeout=None: _mock_resp([])):
        assert conn.get_price("NOPE-USDT") == 0.0
    # Transport failure -> 0.0, no raise.
    def boom(req, timeout=None):
        raise OSError("network down")
    with patch("urllib.request.urlopen", side_effect=boom):
        assert conn.get_price("ICP-USDT") == 0.0


def test_set_leverage_and_margin_type_are_noops():
    conn = PaperFuturesConnector()
    assert conn.set_leverage("BTC-USDT", 3) == {"msg": "paper"}
    assert conn.set_margin_type("BTC-USDT", "ISOLATED") == {"msg": "paper"}


def test_open_returns_unique_synthetic_order_ids():
    conn = PaperFuturesConnector()
    a = conn.open("ICP-USDT", "long", 100.0)
    b = conn.open("FET-USDT", "short", 50.0)
    assert a["orderId"].startswith("paper_fut_") and a["status"] == "FILLED"
    assert b["orderId"].startswith("paper_fut_") and a["orderId"] != b["orderId"]


def test_close_returns_synthetic_order_id():
    conn = PaperFuturesConnector()
    out = conn.close("ICP-USDT", "long", 100.0)
    assert out["orderId"].startswith("paper_fut_") and out["status"] == "FILLED"


def test_get_position_returns_none():
    # Positions live in the engine's position_mgr; the connector holds no state.
    assert PaperFuturesConnector().get_position("BTC-USDT") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/signals/test_paper_futures_connector.py -v`
Expected: FAIL / collection error — `ModuleNotFoundError: No module named 'src.signals.paper_futures_connector'`.

- [ ] **Step 3: Write the implementation**

Create `src/signals/paper_futures_connector.py`:

```python
# src/signals/paper_futures_connector.py
"""Paper (simulated) USDT-perpetual futures connector, priced off Gate.io.

Replaces BinanceFuturesConnector for the futures signal engine. Binance's
*testnet* trading endpoints reject every coin outside a ~30-symbol set
(`fapi POST /fapi/v1/leverage HTTP 400: -1121 Invalid symbol`), so the futures
engine traded nothing. This connector makes it a pure paper simulator:

  * Gate.io USDT-perp ``mark_price`` for pricing (807 contracts, incl. ICP/FET/INJ)
  * synthetic ``paper_fut_*`` order ids for open/close (no real exchange order)
  * no API keys, no auth, no real money

The engine already owns leveraged position state + P&L (futures_math,
signal_position, signal_risk), so this connector holds NO state — positions live
in the engine's position_mgr. It implements the same surface the engine calls on
BinanceFuturesConnector / FakeConn: get_price / set_leverage / set_margin_type /
open / close / get_position.
"""
import itertools
import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

GATE_PERP_TICKERS = "https://api.gateio.ws/api/v4/futures/usdt/tickers"


class PaperFuturesConnector:
    def __init__(self, default_leverage: int = 3):
        # default_leverage is accepted for interface parity with
        # BinanceFuturesConnector but is UNUSED — leverage lives in the engine's
        # risk math (signal_risk.get_budget_for_trade(leverage=...)), not here.
        self._default_leverage = default_leverage
        self._counter = itertools.count(1)

    @staticmethod
    def _contract(symbol: str) -> str:
        # "ICP-USDT" -> "ICP_USDT"  (Gate USDT-perp contract naming).
        return symbol.replace("-", "_")

    def get_price(self, symbol: str) -> float:
        """Gate.io USDT-perp mark_price (fallback last); 0.0 on any failure."""
        url = f"{GATE_PERP_TICKERS}?contract={self._contract(symbol)}"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                data = json.loads(resp.read().decode())
            if data:
                row = data[0]
                mark = row.get("mark_price")
                if mark:
                    return float(mark)
                last = row.get("last")
                if last:
                    return float(last)
        except Exception as e:
            logger.warning(f"Paper futures price fetch failed for {symbol}: {e}")
        return 0.0

    def set_leverage(self, symbol, leverage):
        return {"msg": "paper"}

    def set_margin_type(self, symbol, margin_type="ISOLATED"):
        return {"msg": "paper"}

    def _next_id(self) -> str:
        return f"paper_fut_{next(self._counter)}"

    def open(self, symbol, side, qty, order_type="MARKET", price=None):
        oid = self._next_id()
        logger.info(f"[PAPER FUTURES] open {side} {symbol} qty={qty} -> {oid}")
        return {"orderId": oid, "status": "FILLED"}

    def close(self, symbol, side, qty):
        oid = self._next_id()
        logger.info(f"[PAPER FUTURES] close {side} {symbol} qty={qty} -> {oid}")
        return {"orderId": oid, "status": "FILLED"}

    def get_position(self, symbol):
        return None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/signals/test_paper_futures_connector.py -v`
Expected: PASS (8 tests).

- [ ] **Step 5: Commit**

```bash
git add src/signals/paper_futures_connector.py tests/signals/test_paper_futures_connector.py
git commit -m "feat(signals): add PaperFuturesConnector (Gate.io perp paper simulator)

Replaces the broken Binance testnet path. Gate.io perp mark_price for pricing,
synthetic order ids for open/close, no real money."
```

---

### Task 2: `_get_current_price` futures-mode guard

**Files:**
- Modify: `src/signals/signal_engine.py` (`_get_current_price`, insert after L778)
- Test: `tests/signals/test_futures_price_guard.py`

**Interfaces:**
- Consumes: `SignalEngine(...)` constructor (`src/signals/signal_engine.py:75`); existing `self._futures_mode` flag (set at L98).
- Produces: behavior change only — no new public API.

**Why:** `_get_current_price` falls through to the Gate.io **spot** ticker when the primary price fn returns ≤0. In futures mode that would silently mis-price a leveraged sim with the spot price. A perp-price failure must surface as a clean `0.0` (the caller's `<=0` skip path + Telegram notify) instead.

- [ ] **Step 1: Write the failing tests**

Create `tests/signals/test_futures_price_guard.py`:

```python
# tests/signals/test_futures_price_guard.py
import sys
import pathlib
import json

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

import urllib.request
from src.signals.signal_engine import SignalEngine


def _engine(monkeypatch, futures_mode):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    return SignalEngine(
        config={"enabled": True, "audit_mode": False, "allow_shorts": True},
        btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
        telegram_send_fn=lambda m: None,
        get_price_fn=lambda s: 0.0,           # primary (perp) feed "fails"
        futures_mode=futures_mode,
        futures_connector=object() if futures_mode else None,
        leverage=3,
    )


def test_futures_mode_does_not_fall_through_to_spot(monkeypatch):
    """A failed perp price must NOT be replaced by the Gate SPOT ticker in futures
    mode — that would mis-price a leveraged sim. It must return 0.0."""
    eng = _engine(monkeypatch, futures_mode=True)
    spot_called = []

    def boom(*args, **kwargs):
        spot_called.append(1)
        raise AssertionError("spot fallback must not fire in futures mode")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert eng._get_current_price(None, "ICP-USDT") == 0
    assert spot_called == []


def test_spot_mode_still_uses_spot_fallback(monkeypatch):
    """Spot mode must keep falling back to Gate spot when the price fn returns 0
    (regression guard for the existing spot behavior)."""
    eng = _engine(monkeypatch, futures_mode=False)

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        def read(self):
            return json.dumps([{"last": "2.50"}]).encode()

    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: Resp())
    assert eng._get_current_price(None, "ICP-USDT") == 2.50
```

- [ ] **Step 2: Run the tests to verify one fails**

Run: `pytest tests/signals/test_futures_price_guard.py -v`
Expected: `test_futures_mode_does_not_fall_through_to_spot` FAILS (today the spot fallback fires, raising the `AssertionError` / returning a spot price). `test_spot_mode_still_uses_spot_fallback` PASSES.

- [ ] **Step 3: Implement the guard**

In `src/signals/signal_engine.py`, in `_get_current_price`, insert a futures guard between the `except Exception: pass` and the `# Fallback: fetch from Gate.io REST API` comment:

```python
        except Exception:
            pass
        # Futures mode: the price fn is the Gate.io PERP feed. A perp failure must
        # NOT silently fall through to the Gate SPOT ticker (it would mis-price a
        # leveraged sim). Return 0 so the caller's <=0 skip path fires cleanly.
        if self._futures_mode:
            return 0
        # Fallback: fetch from Gate.io REST API for any unregistered pair
        try:
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/signals/test_futures_price_guard.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full signals suite to confirm no regression**

Run: `pytest tests/signals/ tests/test_signal_futures_engine.py tests/test_futures_boot.py -v`
Expected: PASS (pre-existing tests unaffected — spot path unchanged).

- [ ] **Step 6: Commit**

```bash
git add src/signals/signal_engine.py tests/signals/test_futures_price_guard.py
git commit -m "fix(signals): skip Gate spot fallback in futures mode

A failed perp price must surface as 0.0 (clean skip + notify), not be silently
replaced by the Gate spot ticker, which would mis-price a leveraged sim."
```

---

### Task 3: Config + boot contract

**Files:**
- Modify: `config/strategy.yaml` (`signals_futures`, ~L231)
- Test: `tests/test_futures_boot.py`

**Interfaces:**
- Produces: `signals_futures.exchange == "gate_io_paper_futures"` and `signals_futures.testnet == false` in `config/strategy.yaml`.

- [ ] **Step 1: Update the boot test to the new contract (it will fail)**

Replace the body of `tests/test_futures_boot.py`:

```python
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))


def test_futures_config_block_present():
    import yaml

    cfg = yaml.safe_load(pathlib.Path("config/strategy.yaml").read_text())
    f = cfg.get("signals_futures", {})
    assert f.get("enabled") is True
    # Paper simulator priced off Gate.io USDT-perp (Binance testnet retired —
    # fapi -1121 Invalid symbol on non-major coins).
    assert f.get("exchange") == "gate_io_paper_futures"
    assert f.get("leverage") == 3
    assert f.get("margin_type") == "isolated" and f.get("allow_shorts") is True
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/test_futures_boot.py -v`
Expected: FAIL — `assert f.get("exchange") == "gate_io_paper_futures"` (key absent today).

- [ ] **Step 3: Update the config**

In `config/strategy.yaml`, replace the `signals_futures` block:

```yaml
signals_futures:
  enabled: true
  exchange: gate_io_paper_futures   # paper sim priced off Gate.io USDT-perp (binance testnet retired — fapi -1121)
  testnet: false                     # paper has no testnet; field kept for compat reads
  leverage: 3
  margin_type: isolated
  allow_shorts: true
  per_trade_risk_pct: 1.0   # % of budget risked per trade (guard reads per_trade_risk_pct, NOT risk_pct)
  max_positions: 2
  max_capital_usdt: 10000    # 10K paper budget (matches spot signal engine)
  pairs: []
  enabled_pairs: []
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_futures_boot.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add config/strategy.yaml tests/test_futures_boot.py
git commit -m "config: switch signals_futures to gate_io_paper_futures (paper)

Drop the Binance testnet execution model. exchange=gate_io_paper_futures,
testnet=false (paper has no testnet). Update boot test to the new contract."
```

---

### Task 4: Wire `PaperFuturesConnector` into `run_signal_listener`

**Files:**
- Modify: `src/run_signal_listener.py` (extract `_build_futures_engine` from `main`, ~L222-259)
- Test: `tests/test_futures_wiring.py`

**Interfaces:**
- Consumes: `PaperFuturesConnector` (Task 1); `SignalEngine` constructor.
- Produces: module-level `_build_futures_engine(signal_cfg: dict, fc: dict) -> Optional[SignalEngine]` in `src/run_signal_listener.py`. `main()` calls it and logs based on whether the result is `None`.

- [ ] **Step 1: Write the failing wiring tests**

Create `tests/test_futures_wiring.py`:

```python
# tests/test_futures_wiring.py
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import src.run_signal_listener as rsl
from src.signals.signal_engine import SignalEngine
from src.signals.paper_futures_connector import PaperFuturesConnector


def test_build_futures_engine_uses_paper_connector_without_keys(monkeypatch):
    """The futures engine builds from the config flag alone — no Binance keys.
    Before the fix this path required BINANCE_FUTURES_KEY/SECRET and constructed
    BinanceFuturesConnector."""
    monkeypatch.delenv("SIGNAL_MODE", raising=False)
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    eng = rsl._build_futures_engine(
        signal_cfg={"enabled": True}, fc={"enabled": True, "leverage": 3}
    )
    assert eng is not None
    assert isinstance(eng._futures_connector, PaperFuturesConnector)
    assert eng._futures_mode is True
    assert eng._leverage == 3


def test_build_futures_engine_disabled_returns_none(monkeypatch):
    monkeypatch.delenv("SIGNAL_MODE", raising=False)
    assert rsl._build_futures_engine(
        signal_cfg={"enabled": True}, fc={"enabled": False}
    ) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests/test_futures_wiring.py -v`
Expected: FAIL — `AttributeError: module 'src.run_signal_listener' has no attribute '_build_futures_engine'`.

- [ ] **Step 3: Extract `_build_futures_engine` and swap the connector**

In `src/run_signal_listener.py`, first **add** the new helper function just **above** `async def main():` (it closes over the module-level `_signal_order`, `_telegram_send`, `_get_equity`):

```python
def _build_futures_engine(signal_cfg: dict, fc: dict):
    """Build the headless PAPER futures engine, or None if disabled.

    Extracted from main() so the wiring (PaperFuturesConnector, no Binance keys)
    is unit-testable without starting the Telethon listener. Paper-only: Gate.io
    perp pricing, synthetic orders, no real money.
    """
    futures_enabled = (
        os.environ.get("SIGNAL_MODE") == "futures" or fc.get("enabled", False)
    )
    if not futures_enabled:
        return None
    from src.signals.paper_futures_connector import PaperFuturesConnector

    futures_connector = PaperFuturesConnector(default_leverage=fc.get("leverage", 3))
    return SignalEngine(
        config={**signal_cfg, **fc, "allow_shorts": True},
        btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
        telegram_send_fn=_telegram_send,
        buy_fn=lambda symbol, amount, price, order_type="MARKET": _signal_order("BUY", symbol, amount, price),
        sell_fn=lambda symbol, amount, price, order_type="MARKET": _signal_order("SELL", symbol, amount, price),
        get_price_fn=futures_connector.get_price,
        get_equity_fn=_get_equity,
        own_listener=False,
        state_suffix="_futures",
        futures_mode=True,
        futures_connector=futures_connector,
        leverage=fc.get("leverage", 3),
    )
```

Then **replace** the existing futures block inside `main()` (the `futures_engine = None` … `logger.info("Futures Signal Engine disabled …")` block) with:

```python
        fc = config.get("signals_futures", {})
        futures_engine = _build_futures_engine(signal_cfg, fc)
        if futures_engine is not None:
            logger.info("Futures Signal Engine built (paper, Gate.io perp, state_suffix=_futures)")
        else:
            logger.info("Futures Signal Engine disabled (futures_enabled=%s) — spot-only",
                        os.environ.get("SIGNAL_MODE") == "futures" or fc.get("enabled", False))
```

- [ ] **Step 4: Run the wiring tests to verify they pass**

Run: `pytest tests/test_futures_wiring.py -v`
Expected: PASS (both tests).

- [ ] **Step 5: Run the full futures + signal suite**

Run: `pytest tests/test_futures_wiring.py tests/test_futures_boot.py tests/test_signal_futures_engine.py tests/signals/ -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/run_signal_listener.py tests/test_futures_wiring.py
git commit -m "feat(signals): wire PaperFuturesConnector into the listener

Extract _build_futures_engine(signal_cfg, fc); build the paper connector from the
config flag alone (no Binance keys). Retires the broken Binance testnet path."
```

---

### Task 5: End-to-end regression — non-Binance coin opens a paper position

**Files:**
- Test: `tests/signals/test_futures_paper_execution.py`

**Interfaces:**
- Consumes: `PaperFuturesConnector` (Task 1); `SignalEngine._execute_entry` → `_execute_futures_entry` (the path that previously called `BinanceFuturesConnector.set_leverage` and crashed with `-1121`).

**Why:** This is the `-1121` regression guard. It proves a coin that Binance testnet rejects (ICP) opens a paper position through the **real** `PaperFuturesConnector` (Gate.io perp pricing) with no exception, and that a major (BTC) takes the same path. It uses the real `SignalRiskGuard` (leverage sizing + SL-before-liquidation) so the math is exercised.

- [ ] **Step 1: Write the regression test**

Create `tests/signals/test_futures_paper_execution.py`:

```python
# tests/signals/test_futures_paper_execution.py
"""Regression: the fapi -1121 'Invalid symbol' failure.

Non-Binance coins (ICP/FET) are not tradable on Binance's testnet, so every
futures entry crashed at set_leverage(). They now open PAPER positions via
PaperFuturesConnector (Gate.io perp pricing) with no exception. This wires the
REAL connector into the engine (not the FakeConn used in test_signal_futures_engine)
and exercises the real risk guard (leverage sizing + SL-before-liquidation).
"""
import sys
import pathlib
import json
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))

from src.signals.signal_engine import SignalEngine
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence
from src.signals.paper_futures_connector import PaperFuturesConnector


def _mock_gate(payload):
    m = MagicMock()
    m.__enter__.return_value = m
    m.read.return_value = json.dumps(payload).encode()
    return m


def _engine_with_paper_connector(monkeypatch, tmp_path):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    conn = PaperFuturesConnector()
    eng = SignalEngine(
        config={
            "enabled": True, "audit_mode": False, "allow_shorts": True,
            "per_trade_risk_pct": 1.0, "max_capital_usdt": 10000,
            "capital_pct": 100.0, "max_position_pct": 25.0,
            "max_positions": 2,
        },
        btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
        telegram_send_fn=lambda m: None,
        buy_fn=lambda **k: "x",
        sell_fn=lambda **k: "x",
        get_price_fn=conn.get_price,
        get_equity_fn=lambda: 10000.0,
        futures_mode=True, futures_connector=conn, leverage=3,
    )
    eng._get_equity = lambda c: 10000.0
    eng._log_audit_trade = lambda *a, **k: None
    eng._journal.log_raw_message = lambda *a, **k: None
    eng._risk.record_trade_opened = lambda: None
    eng._position_mgr.has_open_position = lambda p: False
    eng._position_mgr.get_open_positions = lambda: []
    opened = []
    eng._position_mgr.open_position = lambda **k: opened.append(k)
    eng._seen_signal_ids = set()
    eng._seen_signal_ids_path = str(tmp_path / "seen.json")
    return eng, conn, opened


def test_non_binance_icp_opens_paper_position(monkeypatch, tmp_path):
    """ICP (Binance testnet rejects it with -1121) opens a paper long via Gate
    perp pricing. Before the fix this raised at set_leverage."""
    eng, conn, opened = _engine_with_paper_connector(monkeypatch, tmp_path)
    sig = ParsedSignal(
        action=SignalAction.OPEN_LONG, pair="ICP-USDT",
        entry_low=2.17, entry_high=2.18, stop_loss=1.985,
        take_profits=[2.285, 2.385, 2.5],
        confidence=SignalConfidence.HIGH, quality_score=9,
    )
    gate = _mock_gate([{"contract": "ICP_USDT", "mark_price": "2.18", "last": "2.18"}])
    with patch("urllib.request.urlopen", side_effect=lambda *a, **k: gate):
        eng._execute_entry(sig, "Binance Killers VIP Signals", conn)  # must NOT raise
    assert len(opened) == 1
    assert opened[0]["symbol"] == "ICP-USDT"
    assert opened[0]["side"] == "long"


def test_major_btc_also_opens_via_gate(monkeypatch, tmp_path):
    """A major (BTC) takes the same paper path — Gate.io prices everything."""
    eng, conn, opened = _engine_with_paper_connector(monkeypatch, tmp_path)
    sig = ParsedSignal(
        action=SignalAction.OPEN_LONG, pair="BTC-USDT",
        entry_low=60000, entry_high=60500, stop_loss=58000,
        take_profits=[62000, 64000, 66000],
        confidence=SignalConfidence.HIGH, quality_score=8,
    )
    gate = _mock_gate([{"contract": "BTC_USDT", "mark_price": "60500", "last": "60500"}])
    with patch("urllib.request.urlopen", side_effect=lambda *a, **k: gate):
        eng._execute_entry(sig, "chan", conn)  # must NOT raise
    assert len(opened) == 1
    assert opened[0]["symbol"] == "BTC-USDT"


def test_non_binance_short_opens_paper_position(monkeypatch, tmp_path):
    """A SHORT on a non-Binance coin (FET) opens via Gate perp pricing with
    side-aware sizing. (Called via _execute_entry directly, like the existing
    test_futures_opens_short_via_connector — _process_message would otherwise
    return early on non-OPEN_LONG actions.)"""
    eng, conn, opened = _engine_with_paper_connector(monkeypatch, tmp_path)
    sig = ParsedSignal(
        action=SignalAction.OPEN_SHORT, pair="FET-USDT",
        entry_low=0.173, entry_high=0.175, stop_loss=0.20,
        take_profits=[0.16, 0.15, 0.14],
        confidence=SignalConfidence.HIGH, quality_score=8,
    )
    gate = _mock_gate([{"contract": "FET_USDT", "mark_price": "0.175", "last": "0.175"}])
    with patch("urllib.request.urlopen", side_effect=lambda *a, **k: gate):
        eng._execute_entry(sig, "chan", conn)  # must NOT raise
    assert len(opened) == 1
    assert opened[0]["symbol"] == "FET-USDT"
    assert opened[0]["side"] == "short"
```

- [ ] **Step 2: Run the regression test to verify it passes**

Run: `pytest tests/signals/test_futures_paper_execution.py -v`
Expected: PASS (all three tests). This is a guard over already-implemented code (Tasks 1 & 4); if it fails, the integration is broken and must be fixed before deploy.

- [ ] **Step 3: Run the entire test suite**

Run: `pytest tests/signals/ tests/test_signal_futures_engine.py tests/test_futures_boot.py tests/test_futures_wiring.py -v`
Expected: PASS — full green.

- [ ] **Step 4: Commit**

```bash
git add tests/signals/test_futures_paper_execution.py
git commit -m "test(signals): add -1121 regression guard (ICP/BTC paper execution)

Proves non-Binance coins open paper positions via the real PaperFuturesConnector
+ Gate.io perp pricing, with no exception — the path that crashed at Binance
set_leverage before the fix."
```

---

## Rollout (after all tasks green)

- Push the branch, open a PR. The GH Actions `test` job (pytest) gates deploy.
- On merge → `build-signal` image → SSM `pull`/`up` on the `trading-signal-listener` container.
- **No data migration** — the futures engine has never opened a position.
- The already-seen ICP/FET/INJ ids in `data/seen_signal_ids_futures.json` will NOT auto-retrade. Verify live via `/signal_inject` (Telegram) or the next fresh signal.
- `BINANCE_FUTURES_KEY/SECRET` in `.env.docker` are now inert (no longer read) — safe to leave or remove.
- Confirm health post-deploy: `docker logs trading-signal-listener` should show `Futures Signal Engine built (paper, Gate.io perp …)`, and the next non-major signal should open a paper position + write a row to `data/signal_journal_futures.db`.

## Out of scope

- Real-money execution (deferred past the August go-live review).
- Funding-rate modeling beyond logging.
- Any change to the spot, grid, trend, swing, or mean-reversion engines.
