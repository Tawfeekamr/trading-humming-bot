# Binance Futures Signal-Copy Bot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A second bot that copies the same Telegram/DeepSeek signals onto Binance USDT-M perpetual futures (testnet), 3x isolated, **long + short**, alongside the existing spot paper engine.

**Architecture:** Reuse the Python `SignalEngine`; add a Binance fapi connector backend and a direction (`side`) dimension across parser → validator → position → risk → engine. Spot path is untouched (`side` defaults `"long"`, shorts rejected unless `allow_shorts`); futures execution branches on `self._futures_mode`. Deployed as a second container `trading-signal-futures`.

**Tech Stack:** Python 3.13, stdlib `urllib`+`hmac` (fapi REST, no new deps), pytest, existing `src/signals/*` modules.

**Spec:** `docs/superpowers/specs/2026-06-24-binance-futures-signal-bot-design.md`

## Global Constraints

- Binance USDT-M **fapi**, **testnet** base `https://testnet.binancefuture.com` (do not hit live).
- **3x max leverage, ISOLATED margin**, set per-symbol on open.
- **SL always set** and validated to sit inside the liquidation price (mandatory; v1 rejects rather than trims).
- **Spot engine must not regress** — every change is additive; `side` defaults `"long"`; `allow_shorts` defaults `False`; existing long-path tests stay green.
- **Shorts are first-class**: validator is direction-aware (short SL above entry, TPs below, R:R inverted); position PnL inverts; management uses short-aware TP/SL comparisons; closes are reduce-only.
- Errors **notify** (never silent), mirroring the spot rejection-notify discipline.
- Connector errors surface as `RuntimeError` with the fapi body so the engine can notify.
- Testnet only until a separate go-live decision; no live keys in code.

## File Structure

- **Create** `src/signals/binance_futures_connector.py` — fapi REST client. Owns all exchange I/O.
- **Create** `src/signals/futures_math.py` — pure helpers: estimated liquidation, side-aware PnL, SL-before-liquidation gate.
- **Modify** `src/signals/signal_parser.py` — `SignalAction.OPEN_SHORT` + relaxed short rule.
- **Modify** `src/signals/signal_validator.py` — `allow_shorts` gate + direction-aware SL/TP/R:R.
- **Modify** `src/signals/signal_position.py` — `side` field + short PnL in `partial_close`/`close_position`.
- **Modify** `src/signals/signal_risk.py` — leverage-aware sizing + liquidation-buffer reject.
- **Modify** `src/signals/signal_engine.py` — `futures_mode` + direction-aware open/manage/close branch.
- **Modify** `src/run_signal_listener.py` — boot futures-mode engine when `SIGNAL_MODE=futures`.
- **Modify** `config/strategy.yaml` — `signals_futures` block.
- **Modify** `docker-compose.rust.yml` — second service `trading-signal-futures`.
- **Modify** `src/notifications/telegram_commands.py` — `/futures_status`, `/futures_pnl`.
- **Tests:** `tests/signals/test_binance_futures_connector.py`, `tests/signals/test_futures_math.py`, plus additions to existing `tests/test_signal_*` files.

---

### Task 1: futures_math — estimated liquidation + side-aware PnL + SL gate

Pure helpers, tested first.

**Files:**
- Create: `src/signals/futures_math.py`
- Test: `tests/signals/test_futures_math.py`

**Interfaces:**
- Produces: `estimate_liquidation(entry, leverage, side, maint_rate=0.004) -> float`, `pnl(side, entry, exit, qty) -> float`, `sl_triggers_before_liquidation(side, entry, sl, leverage) -> bool`.

- [ ] **Step 1: Write failing tests**

```python
# tests/signals/test_futures_math.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from src.signals.futures_math import estimate_liquidation, pnl, sl_triggers_before_liquidation

def test_liquidation_long_below_entry():
    liq = estimate_liquidation(100.0, 3, "long")
    assert 66.0 < liq < 68.0

def test_liquidation_short_above_entry():
    liq = estimate_liquidation(100.0, 3, "short")
    assert 132.0 < liq < 134.0

def test_pnl_long_and_short_inverted():
    assert pnl("long", 100.0, 110.0, 1.0) == 10.0
    assert pnl("short", 100.0, 110.0, 1.0) == -10.0
    assert pnl("short", 100.0, 90.0, 2.0) == 20.0

def test_sl_must_trigger_before_liquidation():
    assert sl_triggers_before_liquidation("long", 100.0, 80.0, 3) is True
    assert sl_triggers_before_liquidation("long", 100.0, 60.0, 3) is False
    assert sl_triggers_before_liquidation("short", 100.0, 120.0, 3) is True
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/signals/test_futures_math.py -v` → FAIL (import error).

- [ ] **Step 3: Implement**

```python
# src/signals/futures_math.py
"""Pure futures math: estimated liquidation price, side-aware PnL, SL-before-
liquidation gate. Pure functions so they're trivially unit-testable.

Liquidation is an ESTIMATE (isolated margin, maintenance rate default 0.4%).
The exchange's actual liquidation_price (from get_position) is authoritative
and should be logged after open; this estimate is the pre-open gate.
"""
from typing import Literal

Side = Literal["long", "short"]


def estimate_liquidation(entry: float, leverage: float, side: Side, maint_rate: float = 0.004) -> float:
    if leverage <= 0:
        return entry
    if side == "long":
        return entry * (1 - 1.0 / leverage + maint_rate)
    return entry * (1 + 1.0 / leverage - maint_rate)


def pnl(side: Side, entry: float, exit_price: float, qty: float) -> float:
    if side == "long":
        return (exit_price - entry) * qty
    return (entry - exit_price) * qty


def sl_triggers_before_liquidation(side: Side, entry: float, sl: float, leverage: float) -> bool:
    liq = estimate_liquidation(entry, leverage, side)
    if side == "long":
        return sl > liq
    return sl < liq
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/signals/test_futures_math.py -v` → 4 passed.

- [ ] **Step 5: Commit**
```bash
git add src/signals/futures_math.py tests/signals/test_futures_math.py
git commit -m "feat(futures): pure liquidation/PnL/SL-gate math helpers"
```

---

### Task 2: BinanceFuturesConnector — signing + leverage/margin

**Files:**
- Create: `src/signals/binance_futures_connector.py`
- Test: `tests/signals/test_binance_futures_connector.py`

**Interfaces:**
- Produces: `BinanceFuturesConnector(api_key, api_secret, testnet=True, default_leverage=3)` with `.set_leverage`, `.set_margin_type`. Internal `_post`/`_get` used by Task 3.

- [ ] **Step 1: Write failing tests (mock urllib)**

```python
# tests/signals/test_binance_futures_connector.py
import sys, pathlib, json, urllib.request, urllib.error, io
from unittest.mock import patch, MagicMock
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from src.signals.binance_futures_connector import BinanceFuturesConnector


def _mock_resp(payload):
    m = MagicMock(); m.__enter__.return_value = m
    m.read.return_value = json.dumps(payload).encode(); return m


def test_set_leverage_posts_signed_request():
    conn = BinanceFuturesConnector("key", "secret", testnet=True)
    captured = {}
    def fake(req, timeout=None):
        captured["url"] = req.full_url; captured["headers"] = req.headers
        return _mock_resp({"leverage": 3})
    with patch("urllib.request.urlopen", side_effect=fake):
        out = conn.set_leverage("BTCUSDT", 3)
    assert out["leverage"] == 3
    assert "/fapi/v1/leverage" in captured["url"]
    assert "symbol=BTCUSDT" in captured["url"] and "leverage=3" in captured["url"]
    assert "signature=" in captured["url"]
    assert captured["headers"].get("X-Mbx-Apikey") == "key"


def test_set_margin_type_swallows_no_change_error():
    conn = BinanceFuturesConnector("key", "secret")
    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {},
            io.BytesIO(b'{"code":-4046,"msg":"No need to change margin type."}'))
    with patch("urllib.request.urlopen", side_effect=fake):
        out = conn.set_margin_type("BTCUSDT", "ISOLATED")
    assert out["msg"] == "no change needed"


def test_other_errors_surface():
    import pytest
    conn = BinanceFuturesConnector("key", "secret")
    def fake(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 400, "Bad", {},
            io.BytesIO(b'{"code":-1021,"msg":"timestamp"}'))
    with patch("urllib.request.urlopen", side_effect=fake):
        with pytest.raises(RuntimeError):
            conn.set_leverage("BTCUSDT", 3)
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/signals/test_binance_futures_connector.py -v` → FAIL (import error).

- [ ] **Step 3: Implement**

```python
# src/signals/binance_futures_connector.py
"""Binance USDT-M futures (fapi) REST connector. HMAC-signed, testnet by default.
Errors raise RuntimeError with the fapi body so the engine can notify.
"""
import hashlib
import hmac
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

TESTNET_BASE = "https://testnet.binancefuture.com"
LIVE_BASE = "https://fapi.binance.com"


class BinanceFuturesConnector:
    def __init__(self, api_key, api_secret, testnet=True, default_leverage=3):
        self._api_key = api_key
        self._api_secret = api_secret
        self._base = TESTNET_BASE if testnet else LIVE_BASE
        self._default_leverage = default_leverage

    def _sign(self, params):
        query = urllib.parse.urlencode(params)
        sig = hmac.new(self._api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return f"{query}&signature={sig}"

    def _request(self, method, path, params):
        params = dict(params)
        params.setdefault("timestamp", int(time.time() * 1000))
        params.setdefault("recvWindow", 5000)
        url = f"{self._base}{path}?{self._sign(params)}"
        req = urllib.request.Request(url, method=method,
                                     headers={"X-MBX-APIKEY": self._api_key})
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"fapi {method} {path} HTTP {e.code}: {body}")

    def _post(self, path, params): return self._request("POST", path, params)
    def _get(self, path, params):  return self._request("GET", path, params)

    def set_leverage(self, symbol, leverage):
        return self._post("/fapi/v1/leverage", {"symbol": symbol, "leverage": leverage})

    def set_margin_type(self, symbol, margin_type="ISOLATED"):
        try:
            return self._post("/fapi/v1/marginType", {"symbol": symbol, "marginType": margin_type})
        except RuntimeError as e:
            if "No need to change margin type" in str(e) or "-4046" in str(e):
                return {"msg": "no change needed"}
            raise
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/signals/test_binance_futures_connector.py -v` → 3 passed.

- [ ] **Step 5: Commit**
```bash
git add src/signals/binance_futures_connector.py tests/signals/test_binance_futures_connector.py
git commit -m "feat(futures): fapi connector signing + leverage/margin type"
```

---

### Task 3: Connector — open / close / get_position / get_price

**Files:**
- Modify: `src/signals/binance_futures_connector.py` (append)
- Test: `tests/signals/test_binance_futures_connector.py` (append)

**Interfaces:**
- Produces: `.open(symbol, side, qty, order_type="MARKET", price=None)`, `.close(symbol, side, qty)` (reduce-only opposite), `.get_position(symbol) -> dict|None` (`{qty, entry_price, side, liquidation_price, unrealized_pnl}`), `.get_price(symbol) -> float`.

- [ ] **Step 1: Write failing tests**

```python
# append to tests/signals/test_binance_futures_connector.py
def test_open_long_uses_buy():
    conn = BinanceFuturesConnector("k", "s"); cap = {}
    def fake(req, timeout=None): cap["url"] = req.full_url; return _mock_resp({"orderId": "11"})
    with patch("urllib.request.urlopen", side_effect=fake):
        conn.open("BTCUSDT", "long", 0.01)
    assert "side=BUY" in cap["url"] and "quantity=0.01" in cap["url"]

def test_open_short_uses_sell():
    conn = BinanceFuturesConnector("k", "s"); cap = {}
    def fake(req, timeout=None): cap["url"] = req.full_url; return _mock_resp({"orderId": "12"})
    with patch("urllib.request.urlopen", side_effect=fake):
        conn.open("ETHUSDT", "short", 1.0)
    assert "side=SELL" in cap["url"]

def test_close_is_reduce_only_opposite():
    conn = BinanceFuturesConnector("k", "s"); cap = {}
    def fake(req, timeout=None): cap["url"] = req.full_url; return _mock_resp({"orderId": "13"})
    with patch("urllib.request.urlopen", side_effect=fake):
        conn.close("BTCUSDT", "long", 0.01)
    assert "side=SELL" in cap["url"] and "reduceOnly=true" in cap["url"]

def test_get_position_parses_and_flat_none():
    conn = BinanceFuturesConnector("k", "s")
    with patch("urllib.request.urlopen",
               side_effect=lambda req, timeout=None: _mock_resp([{"symbol":"BTCUSDT",
               "positionAmt":"0.5","entryPrice":"100","liquidationPrice":"67",
               "unRealizedProfit":"5","positionSide":"BOTH"}])):
        pos = conn.get_position("BTCUSDT")
    assert pos["qty"] == 0.5 and pos["liquidation_price"] == 67.0 and pos["side"] == "long"
    with patch("urllib.request.urlopen",
               side_effect=lambda req, timeout=None: _mock_resp([{"symbol":"BTCUSDT",
               "positionAmt":"0","entryPrice":"0.0","liquidationPrice":"0",
               "unRealizedProfit":"0","positionSide":"BOTH"}])):
        assert conn.get_position("BTCUSDT") is None

def test_get_price_mark():
    conn = BinanceFuturesConnector("k", "s")
    with patch("urllib.request.urlopen",
               side_effect=lambda req, timeout=None: _mock_resp({"markPrice": "101.5"})):
        assert conn.get_price("BTCUSDT") == 101.5
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/signals/test_binance_futures_connector.py -v` → FAIL (methods missing).

- [ ] **Step 3: Implement**

```python
# append to src/signals/binance_futures_connector.py
    def open(self, symbol, side, qty, order_type="MARKET", price=None):
        params = {"symbol": symbol, "side": "BUY" if side == "long" else "SELL",
                  "type": order_type, "quantity": qty}
        if order_type == "LIMIT" and price is not None:
            params["timeInForce"] = "GTC"; params["price"] = price
        return self._post("/fapi/v1/order", params)

    def close(self, symbol, side, qty):
        opposite = "SELL" if side == "long" else "BUY"
        return self._post("/fapi/v1/order", {"symbol": symbol, "side": opposite,
            "type": "MARKET", "quantity": qty, "reduceOnly": "true"})

    def get_position(self, symbol):
        rows = self._get("/fapi/v2/positionRisk", {"symbol": symbol})
        for r in rows:
            amt = float(r.get("positionAmt", 0))
            if abs(amt) > 0:
                return {"qty": amt, "entry_price": float(r.get("entryPrice", 0)),
                        "side": "long" if amt > 0 else "short",
                        "liquidation_price": float(r.get("liquidationPrice", 0) or 0),
                        "unrealized_pnl": float(r.get("unRealizedProfit", 0))}
        return None

    def get_price(self, symbol):
        return float(self._get("/fapi/v1/premiumIndex", {"symbol": symbol}).get("markPrice", 0))
```

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/signals/test_binance_futures_connector.py -v` → 8 passed.

- [ ] **Step 5: Commit**
```bash
git add src/signals/binance_futures_connector.py tests/signals/test_binance_futures_connector.py
git commit -m "feat(futures): open/close/position/price fapi methods"
```

---

### Task 4: Parser OPEN_SHORT + validator direction-aware (short SL/TP/R:R + allow_shorts gate)

This task adds `OPEN_SHORT` to the parser AND makes the validator direction-aware so a valid short passes (and the spot engine still rejects shorts via `allow_shorts=False`).

**Files:**
- Modify: `src/signals/signal_parser.py` (enum + SYSTEM_PROMPT rule 2)
- Modify: `src/signals/signal_validator.py` (`allow_shorts` + direction-aware validate)
- Test: `tests/signals/test_parser_short.py`, `tests/test_signal_validator.py` (append)

**Interfaces:**
- Produces: `SignalAction.OPEN_SHORT`; `SignalValidator(config)` reads `allow_shorts` (default `False`). `validate()` accepts valid shorts when `allow_shorts=True`, rejects shorts when `False`, long path unchanged.

- [ ] **Step 1: Write failing tests**

```python
# tests/signals/test_parser_short.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent.parent))
from src.signals.signal_parser import SignalParser, SignalAction

def test_parses_short_signal():
    resp = {"action": "OPEN_SHORT", "pair": "ETH-USDT", "entry_low": 3000,
            "entry_high": 3050, "stop_loss": 3150, "take_profits": [2900, 2800],
            "confidence": "high", "quality_score": 7, "reasoning": "short"}
    p = SignalParser(api_key="fake"); p._call_glm = lambda prompt: resp
    sig = p.parse("SHORT ETH/USDT entry 3000-3050 SL 3150")
    assert sig.action == SignalAction.OPEN_SHORT
    assert sig.pair == "ETH-USDT"
```

```python
# append to tests/test_signal_validator.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence
from src.signals.signal_validator import SignalValidator

def _short():
    return ParsedSignal(action=SignalAction.OPEN_SHORT, pair="ETH-USDT",
        entry_low=3000.0, entry_high=3000.0, stop_loss=3150.0,
        take_profits=[2900.0, 2800.0], confidence=SignalConfidence.HIGH, quality_score=8)

def _cfg(allow_shorts=False):
    return {"min_rr_ratio": 1.0, "max_sl_distance_pct": 5.0,
            "max_entry_zone_pct": 3.0, "min_quality_score": 5,
            "allow_shorts": allow_shorts}

def test_spot_rejects_short_by_default():
    valid, reason = SignalValidator(_cfg()).validate(_short())
    assert valid is False and "short" in reason.lower()

def test_futures_accepts_valid_short_when_allowed():
    valid, reason = SignalValidator(_cfg(allow_shorts=True)).validate(_short())
    assert valid is True, reason   # entry 3000, SL 3150, TP 2800 → R:R = 200/150 = 1.33 ≥ 1.0

def test_short_rejected_when_sl_below_entry():
    sig = _short(); sig.stop_loss = 2900.0  # SL below entry — invalid for a short
    valid, reason = SignalValidator(_cfg(allow_shorts=True)).validate(sig)
    assert valid is False and "SL" in reason

def test_long_path_unchanged():
    sig = ParsedSignal(action=SignalAction.OPEN_LONG, pair="X-USDT", entry_low=100.0,
        entry_high=100.0, stop_loss=80.0, take_profits=[130.0],
        confidence=SignalConfidence.HIGH, quality_score=8)
    valid, _ = SignalValidator(_cfg()).validate(sig)
    assert valid is True
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/signals/test_parser_short.py tests/test_signal_validator.py -v` → FAIL.

- [ ] **Step 3: Parser — add enum + relax prompt rule**

In `src/signals/signal_parser.py`:
```python
class SignalAction(Enum):
    OPEN_LONG = "OPEN_LONG"
    OPEN_SHORT = "OPEN_SHORT"
    CLOSE = "CLOSE"
    UPDATE_SL = "UPDATE_SL"
    UPDATE_TP = "UPDATE_TP"
    NOT_A_SIGNAL = "NOT_A_SIGNAL"
```
Change SYSTEM_PROMPT rule 2 to extract shorts:
```
2. We trade SPOT longs AND futures shorts. If the signal is an explicit SHORT/SELL opening position, extract it with action "OPEN_SHORT" (for a short, SL is ABOVE entry and TPs BELOW). Ignore leverage mentions ("2-5x","10x").
```
Add `"OPEN_SHORT"` to the OUTPUT FORMAT action enum line.

- [ ] **Step 4: Validator — allow_shorts + direction-aware**

In `src/signals/signal_validator.py`, add to `__init__`:
```python
        self._allow_shorts = config.get("allow_shorts", False)
```
Replace the SL/TP/R:R block (the long-only section from the `SL >= entry` check through the R:R check) with a direction-aware version:
```python
        is_short = signal.action == SignalAction.OPEN_SHORT
        if is_short and not self._allow_shorts:
            return False, "Short signals not enabled (spot)"

        # Direction-aware stop-loss + risk:reward.
        if is_short:
            if signal.stop_loss <= entry:
                return False, f"SL {signal.stop_loss} <= entry {entry} (short SL must be above entry)"
            sl_distance = (signal.stop_loss - entry) / entry * 100
            tp_index = 0  # farthest TP for a short = lowest price
            tp_label = f"TP{tp_index + 1}"
            reward = entry - signal.take_profits[tp_index]
            if reward <= 0:
                return False, f"{tp_label} {signal.take_profits[tp_index]} >= entry {entry} (short TP must be below entry)"
            risk = signal.stop_loss - entry
        else:
            if signal.stop_loss >= entry:
                return False, f"SL {signal.stop_loss} >= entry {entry}"
            sl_distance = (entry - signal.stop_loss) / entry * 100
            tp_index = min(2, len(signal.take_profits) - 1)  # farthest TP for a long = highest
            tp_label = f"TP{tp_index + 1}"
            reward = signal.take_profits[tp_index] - entry
            if reward <= 0:
                return False, f"{tp_label} {signal.take_profits[tp_index]} <= entry {entry}"
            risk = entry - signal.stop_loss
        rr = reward / risk
        if rr < self._min_rr_ratio:
            return False, f"R:R {rr:.2f} (vs {tp_label}) < min {self._min_rr_ratio}"
```
Make the SL auto-tighten block direction-aware (the `if sl_distance > self._max_sl_distance_pct` block):
```python
        if sl_distance > self._max_sl_distance_pct and not getattr(signal, "entry_tuned", False):
            if is_short:
                new_sl = round(entry * (1 + self._max_sl_distance_pct / 100), 6)
            else:
                new_sl = round(entry * (1 - self._max_sl_distance_pct / 100), 6)
            logger.warning(f"SL auto-tightened for {signal.pair}: {signal.stop_loss} "
                           f"({sl_distance:.1f}%) → {new_sl} ({self._max_sl_distance_pct}%)")
            signal.stop_loss = new_sl
        elif sl_distance > self._max_sl_distance_pct:
            logger.info(f"SL kept wide for tuned {('short' if is_short else 'long')} {signal.pair}")
```
Note: the R:R above uses the pre-tighten SL; since tightening moves SL *toward* entry (smaller risk), R:R only improves, so the pre-tighten R:R check is the conservative gate — keep it before tighten as the original did. (Original order: SL-distance tighten, then R:R — but tighten only ever reduces risk, so checking R:R against the original SL is the strict case; either order is safe. Keep tighten-then-RR only if you also recompute risk after tighten. Simplest correct choice: compute R:R with the final SL.)

**To keep it correct and simple:** move the R:R block to AFTER the tighten block, and recompute `risk` from the (possibly tightened) `signal.stop_loss`:
```python
        # tighten first (mutates signal.stop_loss), then R:R against final SL
        risk = (signal.stop_loss - entry) if is_short else (entry - signal.stop_loss)
        rr = reward / risk
        if rr < self._min_rr_ratio:
            return False, f"R:R {rr:.2f} (vs {tp_label}) < min {self._min_rr_ratio}"
```
(Implementer: place the tighten block, then this final risk/rr block. Drop the earlier `risk =`/`rr =` lines to avoid computing twice.)

- [ ] **Step 5: Run to verify pass + regression** — `python -m pytest tests/signals/test_parser_short.py tests/test_signal_validator.py tests/test_signal_tuning.py tests/test_coverage_push2.py -v` → all pass (long unchanged, short accepted/rejected correctly).

- [ ] **Step 6: Commit**
```bash
git add src/signals/signal_parser.py src/signals/signal_validator.py tests/signals/test_parser_short.py tests/test_signal_validator.py
git commit -m "feat(parser+validator): OPEN_SHORT + direction-aware short validation"
```

---

### Task 5: Position — side field + short PnL

**Files:**
- Modify: `src/signals/signal_position.py`
- Test: `tests/test_signal_position_side.py`

**Interfaces:**
- Produces: `SignalPosition.side` (default `"long"`); `open_position(..., side="long")`; `partial_close`/`close_position` use `futures_math.pnl(side, ...)`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_signal_position_side.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from src.signals.signal_position import SignalPositionManager

def _mgr(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return SignalPositionManager({"max_positions": 2, "tp1_close_pct": 33, "tp2_close_pct": 50})

def test_short_close_pnl_inverted(tmp_path, monkeypatch):
    m = _mgr(tmp_path, monkeypatch)
    m.open_position("ETHUSDT", 3000.0, 2.0, 3150.0, [2900], "high", "x", "c", side="short")
    assert abs(m.close_position("ETHUSDT", 2850.0, "tp") - 300.0) < 1e-6  # (3000-2850)*2

def test_long_close_pnl_unchanged(tmp_path, monkeypatch):
    m = _mgr(tmp_path, monkeypatch)
    m.open_position("BTCUSDT", 100.0, 1.0, 90.0, [110], "high", "x", "c")
    assert m.close_position("BTCUSDT", 110.0, "tp") == 10.0

def test_short_partial_close(tmp_path, monkeypatch):
    m = _mgr(tmp_path, monkeypatch)
    m.open_position("ETHUSDT", 3000.0, 2.0, 3150.0, [2900], "high", "x", "c", side="short")
    amt, pnl = m.partial_close("ETHUSDT", 0.5, 2940.0, "tp1")  # (3000-2940)*1.0 = 60
    assert abs(pnl - 60.0) < 1e-6 and abs(amt - 1.0) < 1e-6
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_signal_position_side.py -v` → FAIL.

- [ ] **Step 3: Implement**

In `src/signals/signal_position.py` add at top: `from .futures_math import pnl as _side_pnl`.

Add field to `SignalPosition` (after `order_id: str = ""`): `side: str = "long"`.

`open_position` signature gains `side="long"` and passes it into the `SignalPosition(...)` construction (add `side=side`).

In `partial_close`, replace `pnl = (price - pos.entry_price) * close_amount` with:
```python
            pnl = _side_pnl(pos.side, pos.entry_price, price, close_amount)
```
In `close_position`, replace `pnl = (price - pos.entry_price) * remaining` with:
```python
            pnl = _side_pnl(pos.side, pos.entry_price, price, remaining)
```

- [ ] **Step 4: Run to verify pass + regression** — `python -m pytest tests/test_signal_position_side.py tests/test_signal_rejection_notify.py -v` → all pass (default `side="long"` keeps spot behavior).

- [ ] **Step 5: Commit**
```bash
git add src/signals/signal_position.py tests/test_signal_position_side.py
git commit -m "feat(position): side field + short PnL via futures_math"
```

---

### Task 6: Risk — leverage-aware sizing + liquidation-buffer reject

**Files:**
- Modify: `src/signals/signal_risk.py`
- Test: `tests/test_signal_risk.py` (append)

**Interfaces:**
- Produces: `get_budget_for_trade(signal, total_equity, leverage=None) -> float`. With `leverage`, raises `LiquidationBufferError` (a `RuntimeError` subclass) if SL beyond liquidation. No-`leverage` callers (spot) unchanged.

- [ ] **Step 1: Write failing tests**

```python
# append to tests/test_signal_risk.py
import pytest
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence
from src.signals.signal_risk import SignalRiskGuard, LiquidationBufferError

def _sig(entry=100.0, sl=80.0):
    return ParsedSignal(action=SignalAction.OPEN_LONG, pair="X-USDT",
        entry_low=entry, entry_high=entry, stop_loss=sl,
        take_profits=[130.0], confidence=SignalConfidence.HIGH, quality_score=8)

def test_leverage_sizing_returns_notional():
    g = SignalRiskGuard({"capital_pct":100,"max_capital_usdt":1000,"per_trade_risk_pct":1.0,"max_position_pct":100})
    assert g.get_budget_for_trade(_sig(100.0, 80.0), 1000.0, leverage=3) > 0

def test_rejects_when_sl_beyond_liquidation():
    g = SignalRiskGuard({"capital_pct":100,"max_capital_usdt":1000,"per_trade_risk_pct":1.0,"max_position_pct":100})
    with pytest.raises(LiquidationBufferError):
        g.get_budget_for_trade(_sig(100.0, 60.0), 1000.0, leverage=3)  # SL below liq ~67

def test_no_leverage_keeps_legacy_behavior():
    g = SignalRiskGuard({"capital_pct":10,"max_capital_usdt":1000,"per_trade_risk_pct":3.0,"max_position_pct":25})
    assert g.get_budget_for_trade(_sig(100.0, 80.0), 1000.0) > 0  # no leverage arg
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_signal_risk.py -v` → FAIL.

- [ ] **Step 3: Implement**

Top of `src/signals/signal_risk.py` add:
```python
from .futures_math import sl_triggers_before_liquidation


class LiquidationBufferError(RuntimeError):
    """Stop-loss would be hit only after liquidation — refuse the trade."""
```
Change signature to `def get_budget_for_trade(self, signal, total_equity, leverage=None):` and insert the gate before the existing sizing logic:
```python
        if leverage is not None and signal.stop_loss and signal.entry_high:
            side = "short" if signal.action and signal.action.value == "OPEN_SHORT" else "long"
            if not sl_triggers_before_liquidation(side, signal.entry_high, signal.stop_loss, leverage):
                raise LiquidationBufferError(
                    f"SL {signal.stop_loss} beyond est. liquidation for {leverage}x {side}")
```
Leave the rest of the method (the sizing math + returns) unchanged.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_signal_risk.py tests/test_signal_rejection_notify.py -v` → all pass.

- [ ] **Step 5: Commit**
```bash
git add src/signals/signal_risk.py tests/test_signal_risk.py
git commit -m "feat(risk): leverage-aware sizing + liquidation-buffer reject"
```

---

### Task 7: Engine — futures-mode long + short open/manage/close

Largest task. Spot `_execute_entry`/`_manage_positions` untouched; futures adds a parallel branch on `self._futures_mode`, handling BOTH directions: open via connector (with leverage/margin set), manage TP1/2/3 scale + SL→BE with short-aware comparisons, reduce-only close.

**Files:**
- Modify: `src/signals/signal_engine.py`
- Test: `tests/test_signal_futures_engine.py`

**Interfaces:**
- Consumes: `BinanceFuturesConnector` (Tasks 2-3), `SignalValidator` with `allow_shorts=True` (Task 4), `futures_math.pnl`.
- Produces: `SignalEngine(..., futures_mode=False, futures_connector=None, leverage=3)`; futures branch `_execute_futures_entry` + `_manage_futures_positions`; spot path byte-for-byte unchanged.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_signal_futures_engine.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
import src.signals.signal_engine as se_mod
from src.signals.signal_engine import SignalEngine
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence


def _short():
    return ParsedSignal(action=SignalAction.OPEN_SHORT, pair="ETH-USDT",
        entry_low=3000.0, entry_high=3000.0, stop_loss=3150.0,
        take_profits=[2900.0, 2800.0], confidence=SignalConfidence.HIGH, quality_score=8)


class FakeConn:
    def __init__(self): self.calls = []
    def set_leverage(self, s, l): self.calls.append(("lev", s, l))
    def set_margin_type(self, s, m="ISOLATED"): self.calls.append(("margin", s))
    def open(self, s, side, qty, **k): self.calls.append(("open", s, side, qty)); return {"orderId": "1"}
    def close(self, s, side, qty): self.calls.append(("close", s, side, qty)); return {"orderId": "9"}
    def get_price(self, s): return 3000.0
    def get_position(self, s): return None


def _futures_engine(monkeypatch, tmp_path, connector=None):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    conn = connector or FakeConn()
    sent = []
    eng = SignalEngine(config={"enabled": True, "audit_mode": False, "allow_shorts": True},
                       btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
                       telegram_send_fn=sent.append, buy_fn=lambda **k: "x",
                       get_price_fn=conn.get_price,
                       futures_mode=True, futures_connector=conn, leverage=3)
    eng._get_equity = lambda c: 10000.0
    eng._get_current_price = lambda c, s: 3000.0
    eng._log_audit_trade = lambda *a, **k: None
    eng._journal.log_raw_message = lambda *a, **k: None
    eng._risk.get_budget_for_trade = lambda sig, eq, leverage=3: 600.0
    eng._risk.record_trade_opened = lambda: None
    eng._position_mgr.has_open_position = lambda p: False
    eng._position_mgr.get_open_positions = lambda: []
    eng._position_mgr.open_position = lambda **k: None
    eng._seen_signal_ids = set(); eng._seen_signal_ids_path = str(tmp_path / "seen.json")
    return eng, conn, sent


def test_futures_opens_short_via_connector(monkeypatch, tmp_path):
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    eng._execute_entry(_short(), "chan", eng._futures_connector)
    assert any(c[0] == "open" and c[2] == "short" for c in conn.calls)
    assert any(c[0] == "lev" and c[2] == 3 for c in conn.calls)
    assert any("FUTURES" in m and "SHORT" in m for m in sent)


def test_futures_long_uses_buy(monkeypatch, tmp_path):
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    sig = ParsedSignal(action=SignalAction.OPEN_LONG, pair="BTC-USDT", entry_low=100.0,
        entry_high=100.0, stop_loss=80.0, take_profits=[130.0],
        confidence=SignalConfidence.HIGH, quality_score=8)
    eng._execute_entry(sig, "chan", eng._futures_connector)
    assert any(c[0] == "open" and c[2] == "long" for c in conn.calls)


def test_futures_manage_closes_short_on_tp_via_reduce_only(monkeypatch, tmp_path):
    # Open a short, then manage with price fallen to TP1 → reduce-only close.
    eng, conn, sent = _futures_engine(monkeypatch, tmp_path)
    from src.signals.signal_position import SignalPosition
    pos = SignalPosition("ETHUSDT", 3000.0, 2.0, 3150.0, [2900.0, 2800.0],
                         "high", "x", "chan", entry_timestamp=0, side="short")
    eng._position_mgr.get_open_positions = lambda: [pos]
    eng._position_mgr.partial_close = lambda sym, pct, price, reason: (2.0 * pct, 0.0)
    eng._position_mgr.update_stop_loss = lambda sym, sl: None
    eng._get_current_price = lambda c, s: 2900.0  # == TP1 for the short
    eng._manage_positions(eng._futures_connector)
    assert any(c[0] == "close" and c[2] == "short" for c in conn.calls)


def test_spot_mode_unchanged(monkeypatch, tmp_path):
    monkeypatch.setattr(SignalEngine, "_refresh_available_pairs", lambda self: None)
    buys = []
    eng = SignalEngine(config={"enabled": True, "audit_mode": False},
                       btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
                       telegram_send_fn=lambda m: None,
                       buy_fn=lambda **k: (buys.append(k), "oid")[1],
                       get_price_fn=lambda s: 0.11)
    eng._get_current_price = lambda c, s: 0.11; eng._get_equity = lambda c: 10000.0
    eng._log_audit_trade = lambda *a, **k: None; eng._journal.log_raw_message = lambda *a, **k: None
    eng._risk.get_budget_for_trade = lambda sig, eq, leverage=None: 500.0
    eng._risk.record_trade_opened = lambda: None
    eng._position_mgr.has_open_position = lambda p: False
    eng._position_mgr.get_open_positions = lambda: []
    eng._position_mgr.open_position = lambda **k: None
    assert eng._futures_mode is False
    sig = ParsedSignal(action=SignalAction.OPEN_LONG, pair="DOGE-USDT", entry_low=0.10,
        entry_high=0.12, stop_loss=0.09, take_profits=[0.14],
        confidence=SignalConfidence.HIGH, quality_score=8)
    eng._execute_entry(sig, "chan", None)
    assert len(buys) == 1
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_signal_futures_engine.py -v` → FAIL (`futures_mode` kwargs unknown).

- [ ] **Step 3: Implement**

In `SignalEngine.__init__` accept and store:
```python
    def __init__(self, config, btc_regime_fn=None, telegram_send_fn=None,
                 buy_fn=None, get_price_fn=None,
                 futures_mode=False, futures_connector=None, leverage=3):
        # ... existing body ...
        self._futures_mode = futures_mode
        self._futures_connector = futures_connector
        self._leverage = leverage
```

At the top of `_execute_entry`, branch:
```python
        if self._futures_mode:
            return self._execute_futures_entry(signal, channel_name)
```

Add:
```python
    def _execute_futures_entry(self, signal, channel_name):
        from .futures_math import sl_triggers_before_liquidation
        if not self._futures_connector:
            self._notify_dedupe("no_futures_conn", "🚫 Futures bot has no connector")
            return
        side = "short" if signal.action.value == "OPEN_SHORT" else "long"
        sym = signal.pair or ""
        entry = signal.entry_high or signal.entry_low
        if not entry:
            self._notify_dedupe(f"no_entry:{sym}", f"🚫 Futures reject (no entry): {sym}\n"
                                + _signal_detail(signal, channel_name)); return
        try:
            usdt_notional = self._risk.get_budget_for_trade(
                signal, self._get_equity(self._current_connector), leverage=self._leverage)
        except Exception as e:  # LiquidationBufferError etc.
            self._notify_dedupe(f"liq_block:{sym}",
                f"🚫 Futures reject (liquidation buffer): {sym} — {e}\n"
                + _signal_detail(signal, channel_name))
            self._log_audit_trade(signal, channel_name, "rejected_liquidation", 0, str(e)); return
        qty = (usdt_notional / entry) if entry else 0.0
        if usdt_notional <= 0 or qty <= 0:
            self._notify_dedupe(f"no_budget:{sym}", f"🚫 Futures skip (no budget): {sym}"); return
        try:
            self._futures_connector.set_leverage(sym, self._leverage)
            self._futures_connector.set_margin_type(sym, "ISOLATED")
            ack = self._futures_connector.open(sym, side, qty)
        except Exception as e:
            self._notify_dedupe(f"futures_open_err:{sym}",
                f"🚫 Futures open FAILED: {sym} — {e}\n" + _signal_detail(signal, channel_name)); return
        self._position_mgr.open_position(sym, entry, qty, signal.stop_loss,
            signal.take_profits, signal.confidence.value, signal.raw_message,
            channel_name, side=side)
        self._risk.record_trade_opened()
        self._notify(f"[FUTURES {self._leverage}x] Opened {side.upper()} {sym} "
                     f"~${usdt_notional:.0f} (orderId {ack.get('orderId','?')})")
```

At the top of `_manage_positions`, branch:
```python
        if self._futures_mode:
            return self._manage_futures_positions(connector)
```
Add (mirrors the spot manage logic with short-aware comparisons + reduce-only close via the connector):
```python
    def _manage_futures_positions(self, connector):
        """Direction-aware TP scale + SL→breakeven for futures, closing via the
        connector's reduce-only close. Long: price>=TP hits, price<=SL stops.
        Short: price<=TP hits, price>=SL stops."""
        for pos in self._position_mgr.get_open_positions():
            price = self._get_current_price(connector, pos.symbol)
            if price <= 0:
                continue
            side = getattr(pos, "side", "long")
            tps = pos.take_profits
            def close_partial(pct, reason):
                amt, _ = self._position_mgr.partial_close(pos.symbol, pct, price, reason)
                if amt > 0:
                    self._futures_connector.close(pos.symbol, side, amt)
            if side == "long":
                if not pos.tp1_hit and len(tps) >= 1 and price >= tps[0]:
                    close_partial(pos.tp1_close_pct, "tp1")
                    pos.tp1_hit = True; self._position_mgr.update_stop_loss(pos.symbol, pos.entry_price)
                elif not pos.tp2_hit and len(tps) >= 2 and price >= tps[1]:
                    close_partial(pos.tp2_close_pct, "tp2"); pos.tp2_hit = True
                elif not pos.tp3_hit and len(tps) >= 3 and price >= tps[2]:
                    close_partial(1.0, "tp3"); pos.tp3_hit = True
                if price <= pos.stop_loss:
                    pnl = self._position_mgr.close_position(pos.symbol, price, "stop_loss")
                    self._futures_connector.close(pos.symbol, side, pos.remaining_amount or pos.amount)
            else:  # short — inverted comparisons
                if not pos.tp1_hit and len(tps) >= 1 and price <= tps[0]:
                    close_partial(pos.tp1_close_pct, "tp1")
                    pos.tp1_hit = True; self._position_mgr.update_stop_loss(pos.symbol, pos.entry_price)
                elif not pos.tp2_hit and len(tps) >= 2 and price <= tps[1]:
                    close_partial(pos.tp2_close_pct, "tp2"); pos.tp2_hit = True
                elif not pos.tp3_hit and len(tps) >= 3 and price <= tps[2]:
                    close_partial(1.0, "tp3"); pos.tp3_hit = True
                if price >= pos.stop_loss:
                    pnl = self._position_mgr.close_position(pos.symbol, price, "stop_loss")
                    self._futures_connector.close(pos.symbol, side, pos.remaining_amount or pos.amount)
```
(Implementer: after `close_position`, `remaining_amount` is ~0; capture the qty to close BEFORE calling `close_position`. Fix: read `qty_to_close = pos.remaining_amount` first, then `close_position`, then `connector.close(symbol, side, qty_to_close)`.)

- [ ] **Step 4: Run to verify pass + full regression** — `python -m pytest tests/test_signal_futures_engine.py tests/test_signal_rejection_notify.py tests/test_signal_tuning.py tests/test_signal_validator.py -v` → all pass.

- [ ] **Step 5: Commit**
```bash
git add src/signals/signal_engine.py tests/test_signal_futures_engine.py
git commit -m "feat(engine): futures-mode long+short open/manage/close branch"
```

---

### Task 8: Config + boot path (SIGNAL_MODE=futures) + validate_config

**Files:**
- Modify: `config/strategy.yaml`, `src/run_signal_listener.py`, and the Rust config validator (search `trading-engine-core/src/` for the allow-list of top-level keys; add `signals_futures`).
- Test: `tests/test_futures_boot.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_futures_boot.py
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
def test_futures_config_block_present():
    import yaml
    cfg = yaml.safe_load(pathlib.Path("config/strategy.yaml").read_text())
    f = cfg.get("signals_futures", {})
    assert f.get("testnet") is True and f.get("leverage") == 3
    assert f.get("margin_type") == "isolated" and f.get("allow_shorts") is True
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_futures_boot.py -v` → FAIL.

- [ ] **Step 3: Add config + boot wiring**

Append to `config/strategy.yaml`:
```yaml
signals_futures:
  enabled: false
  testnet: true
  leverage: 3
  margin_type: isolated
  allow_shorts: true
  risk_pct: 1.0
  max_positions: 2
  max_capital_usdt: 1000
  pairs: []
  enabled_pairs: []
```

In `src/run_signal_listener.py`, add a branch (preserve existing spot construction; replace `...` with the current file's real lambdas/values):
```python
import os
from src.signals.binance_futures_connector import BinanceFuturesConnector

mode = os.environ.get("SIGNAL_MODE", "spot")
if mode == "futures":
    fc = cfg.get("signals_futures", {})
    connector = BinanceFuturesConnector(
        os.environ["BINANCE_FUTURES_KEY"], os.environ["BINANCE_FUTURES_SECRET"],
        testnet=fc.get("testnet", True), default_leverage=fc.get("leverage", 3))
    engine = SignalEngine(
        config={**cfg.get("signals", {}), **fc},
        btc_regime_fn=..., telegram_send_fn=...,
        buy_fn=lambda **k: None, get_price_fn=connector.get_price,
        futures_mode=True, futures_connector=connector, leverage=fc.get("leverage", 3))
else:
    engine = SignalEngine(...)  # existing spot construction unchanged
```
If the Rust `validate_config` enumerates allowed top-level keys, add `signals_futures` there.

- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_futures_boot.py -v` → PASS; run the repo's config validator (e.g. `cargo run -p trading-engine-core --bin validate_config`) → exit 0.

- [ ] **Step 5: Commit**
```bash
git add config/strategy.yaml src/run_signal_listener.py <validator path if changed>
git commit -m "feat(config): signals_futures block + SIGNAL_MODE=futures boot"
```

---

### Task 9: Second container in docker-compose

**Files:**
- Modify: `docker-compose.rust.yml`

- [ ] **Step 1: Add the service** mirroring `trading-signal-listener`:
```yaml
  trading-signal-futures:
    image: ghcr.io/tawfeekamr/trading-signal-listener:latest
    container_name: trading-signal-futures
    restart: unless-stopped
    env_file: .env.futures
    environment:
      - SIGNAL_MODE=futures
    volumes:
      - ./config/strategy.yaml:/app/config/strategy.yaml:ro
      - ./data:/app/data
```
- [ ] **Step 2: Validate compose syntax** — `docker compose -f docker-compose.rust.yml config -q` → exit 0. Note: `.env.futures` (chmod 600) must hold testnet futures keys + `SIGNAL_MODE=futures` + TG creds.
- [ ] **Step 3: Commit**
```bash
git add docker-compose.rust.yml
git commit -m "feat(deploy): trading-signal-futures container"
```

---

### Task 10: Telegram /futures_status + /futures_pnl

**Files:**
- Modify: `src/notifications/telegram_commands.py`; Test: `tests/test_telegram_commands.py` (append)

- [ ] **Step 1: Write failing test**
```python
# append to tests/test_telegram_commands.py
class TestFuturesCommands:
    def test_futures_status_replies(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        u = _mock_update()
        _handler(tmp_path)._cmd_futures_status(u, None)
        assert u.message.reply_text.called
```
- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/test_telegram_commands.py::TestFuturesCommands -v` → FAIL.
- [ ] **Step 3: Implement** `_cmd_futures_status` + `_cmd_futures_pnl` mirroring `_cmd_signal_status`/`_cmd_signal_pnl`, relabeling the title "FUTURES" (the futures container's `signal_positions.json` IS its state). Register `"futures_status"` and `"futures_pnl"` in the command map.
- [ ] **Step 4: Run to verify pass** — `python -m pytest tests/test_telegram_commands.py -v` → all pass.
- [ ] **Step 5: Commit**
```bash
git add src/notifications/telegram_commands.py tests/test_telegram_commands.py
git commit -m "feat(telegram): /futures_status and /futures_pnl"
```

---

### Task 11: fapi testnet smoke validation (manual gate)

- [ ] **Step 1:** Operator obtains Binance Futures TESTNET keys at https://testnet.binancefuture.com.
- [ ] **Step 2:** Smoke test: `python -c "from src.signals.binance_futures_connector import BinanceFuturesConnector as C; c=C('KEY','SECRET',testnet=True); print(c.get_price('BTCUSDT')); print(c.set_leverage('BTCUSDT',3))"`. Expected: a real mark price + leverage ack. If HTTP 404/451, fix the base URL (spot testnet had path issues — verify fapi testnet specifically).
- [ ] **Step 3:** Record testnet-reachable y/n + endpoints used in the PR. Do not merge to live until the smoke passes.

---

## Self-Review (completed during planning)

**Spec coverage:** liquidation buffer → Tasks 1+6; connector → 2+3; OPEN_SHORT + direction-aware validator → 4; short PnL → 5; leverage sizing → 6; futures long+short open/manage/close → 7; config/boot/container → 8+9; telegram → 10; testnet gate → 11. All spec sections mapped.

**Placeholders:** none — every step has real code or an exact command.

**Type consistency:** `side` is `"long"|"short"` everywhere (parser OPEN_SHORT, validator, position.side, futures_math, engine, connector `open(symbol, side, qty)`). `get_budget_for_trade(signal, equity, leverage=None)` consistent across risk (Task 6) + engine (Task 7). `allow_shorts` flag flows config (Task 8) → validator (Task 4). Connector constructor args consistent across Tasks 2/7/8.

**Known limitation (Task 1/spec):** liquidation price is an estimate pre-open; the exchange's actual `liquidation_price` from `get_position` is authoritative and should be logged after open.
