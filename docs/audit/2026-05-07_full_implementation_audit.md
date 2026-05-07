# 🔍 Trading Bot — Full Implementation Audit

**Date**: 7 May 2026  
**Auditor**: Antigravity AI  
**Project**: TA-Enhanced BTC/USDT Grid Bot  

> Complete code review of every module across 30+ source files. Issues are ranked by **severity** (🔴 Critical → 🟡 Medium → 🟢 Low) and grouped by area.

---

## Executive Summary

| Area | Critical | Medium | Low |
|------|----------|--------|-----|
| **Strategy Script** | 2 | 2 | 1 |
| **Project Structure** | 1 | 1 | 1 |
| **Security** | 2 | 1 | 0 |
| **Error Handling & Resilience** | 1 | 3 | 0 |
| **Strategy Logic** | 1 | 2 | 0 |
| **Testing** | 0 | 3 | 1 |
| **Production Readiness** | 1 | 2 | 1 |
| **Total** | **8** | **14** | **4** |

---

## 1. 🔴 Strategy Script — Broken PnL Tracking

> [!CAUTION]
> The core `did_fill_order` handler logs every fill with **zero PnL, zero fees, zero grid level**. The entire trade journal is unusable.

### Issue in `ta_grid_btcusdt.py` (L265–L288)

```python
def did_fill_order(self, event):
    order = event.order
    trade = Trade(
        entry_price=float(order.price),
        exit_price=float(order.price),   # ← Same as entry — always $0 PnL
        gross_pnl=0.0,                   # ← Always zero
        fee=0.0,                         # ← Always zero
        net_pnl=0.0,                     # ← Always zero
        grid_level=0,                    # ← Always zero
        duration_min=0,                  # ← Always zero
        rsi=0.0,                         # ← No snapshot taken
        ...
    )
```

**Impact**: Every downstream system — Dashboard, Telegram alerts, Google Sheets — will show `$0.00` for every trade. The bot is functionally blind to its own performance.

**Fix**: Implement a proper buy/sell pairing engine:
1. On **BUY fill**: store the order in a `dict[grid_level, FillRecord]` with timestamp + price + current indicators snapshot.
2. On **SELL fill**: look up the matching BUY, compute `gross_pnl = (sell_price - buy_price) * qty`, calculate `fee = qty * price * 0.00075 * 2` (maker+taker), and `net_pnl = gross - fee`.
3. Store the grid level from the order tracker.

---

### 🔴 Candle Fetch on Every Tick

In `ta_grid_btcusdt.py` (L122):

```python
def on_tick(self):
    df = self.candle_feed.fetch_candles(limit=250)  # REST call every tick cycle!
```

**Impact**: Hummingbot's tick interval is ~1 second. This fires **250-candle REST requests every second** to Binance, which will:
- Exhaust your API rate limit (1200 req weight/min) within minutes
- Get your IP temporarily banned
- Waste CPU/network on redundant data (1h candles don't change every second)

**Fix**: Cache candles and only re-fetch on hourly boundaries:
```python
def on_tick(self):
    now = pd.Timestamp.now(tz="UTC")
    if self._last_candle_time and now - self._last_candle_time < pd.Timedelta(minutes=55):
        return  # Reuse cached indicators
    self._last_candle_time = now
    df = self.candle_feed.fetch_candles(limit=250)
```

---

### 🟡 Grid Orders Cancelled and Replaced Every Tick

```python
def _place_grid_orders(self, grid, current_price):
    self._cancel_all_orders()  # Cancel ALL orders first
    # ... then re-place them all
```

**Impact**: Even if indicators haven't changed, all orders are cancelled and re-placed every ~1 second. This creates a blizzard of cancel/place API calls, causes order ID churn, and introduces brief windows with **no orders on the book**.

**Fix**: Only recalculate/replace orders when a new candle closes or the state changes. Add a dirty-flag pattern:
```python
if not self._grid_dirty:
    return
self._grid_dirty = False
```

---

### 🟡 `sys.path.insert` Hack

`ta_grid_btcusdt.py` (L18):
```python
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
```

**Impact**: Fragile path resolution. Breaks if the script is moved, symlinked, or run from a different working directory (e.g., inside Docker).

**Fix**: Use a proper `setup.py` / `pyproject.toml` with `pip install -e .` so `src.*` is a real importable package.

---

## 2. 🔴 Project Structure — Duplicated Files

> [!WARNING]
> Three files exist in **two locations** with nearly identical content. Any edit to one copy silently diverges from the other.

| Root-level file | Canonical `src/` copy | Bytes differ? |
|---|---|---|
| `trade_journal.py` (7,455 B) | `src/journal/trade_journal.py` (7,431 B) | ✅ Yes — 24 byte diff |
| `sheets_sync.py` (7,717 B) | `src/journal/sheets_sync.py` (7,706 B) | ✅ Yes — 11 byte diff |
| `pnl_reporter.py` (root) | No `src/notifications/pnl_reporter.py` exists | ❌ Orphaned |

**Impact**: 
- Importing `from src.journal.trade_journal` gives different behavior than importing the root `trade_journal.py`
- `pnl_reporter.py` lives at root level but the project structure in the README says it should be at `src/notifications/pnl_reporter.py`
- Bugs fixed in one copy won't be fixed in the other

**Fix**: Delete the root-level duplicates. Move `pnl_reporter.py` into `src/notifications/`. Ensure all imports reference the canonical `src.*` path.

---

### 🟡 `config/strategy.yaml` Is Never Loaded

The YAML config exists but **no code anywhere reads it**. All configuration is hardcoded in class attributes inside `ta_grid_btcusdt.py` (L52–L67):

```python
class TAGridBTCUSDT(ScriptStrategyBase):
    levels = 8                # Hardcoded, ignores strategy.yaml
    bb_period = 20            # Hardcoded
    rsi_overbought = 70.0     # Hardcoded
```

Meanwhile `config/strategy.yaml` says `levels: 8`, `grid.capital_usdt: 200`, etc. — but nothing loads it.

**Fix**: Add a config loader in `__init__`:
```python
import yaml
with open("config/strategy.yaml") as f:
    cfg = yaml.safe_load(f)
self.levels = cfg["grid"]["levels"]
```

---

### 🟢 Config Values Drift Between Sources

| Parameter | `strategy.yaml` | `.env.example` | `README.md` | Script default |
|-----------|-----------------|----------------|-------------|----------------|
| `levels` | 8 | 10 | 10 | 8 |
| `capital_usdt` | 200 | 1000 | 1000 | 200 |
| `min_reserve` | 50 | 100 | 100 | 50 |
| `spacing_multiplier` | 0.8 | — | 0.5 | 0.8 |
| `order_refresh_time` | 60 | — | 30 | 60 |

Five different sources, five different values. Whichever one you thought you were configuring is likely not the one running.

---

## 3. 🔴 Security Vulnerabilities

### SSH Open to the World

`iac/aws-tokyo/main.tf` (L48–L53):
```hcl
ingress {
    from_port   = 22
    cidr_blocks = ["0.0.0.0/0"]  # WARNING: Open to entire internet
}
```

**Impact**: Every SSH scanner on the internet will hammer this instance. Combined with a default key pair name of `bot-key`, this is a brute-force target.

**Fix**: 
1. Add a `var.my_ip` variable and use `["${var.my_ip}/32"]`
2. Better yet, use AWS Systems Manager Session Manager instead of SSH — no inbound ports needed.

---

### 🔴 Dashboard Open to the World

```hcl
ingress {
    from_port   = 8501
    to_port     = 8501
    cidr_blocks = ["0.0.0.0/0"]  # Streamlit has no auth by default
}
```

**Impact**: Anyone on the internet can see your live PnL dashboard, trade history, and strategy performance. Streamlit has **no built-in authentication**.

**Fix**: Either restrict to your IP or add `streamlit-authenticator` package, or put it behind an ALB with Cognito.

---

### 🟡 Docker Port Conflict

`docker-compose.yml`:
```yaml
bot:
    ports:
      - "8501:8501"  # Bot also exposes 8501?
dashboard:
    ports:
      - "8502:8501"  # Dashboard on 8502
```

The bot service exposes port 8501 but the bot isn't a Streamlit app — it's a Hummingbot instance. This port mapping is incorrect and will cause confusion.

---

## 4. Error Handling & Resilience

### 🔴 No WebSocket Reconnection Logic

`ws_feed.py` (L26–L38):
```python
async def start(self) -> None:
    async with websockets.connect(stream) as ws:
        while self._running:
            msg = await ws.recv()  # If connection drops → exception → feed dies
```

**Impact**: Any network hiccup, Binance maintenance, or timeout will crash the WebSocket feed permanently. No retry, no backoff, no reconnection.

**Fix**: Add exponential backoff reconnection:
```python
async def start(self):
    retry_delay = 1
    while self._running:
        try:
            async with websockets.connect(stream) as ws:
                retry_delay = 1  # Reset on success
                while self._running:
                    msg = await ws.recv()
                    # ... process
        except (ConnectionClosed, ConnectionError):
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60)
```

---

### 🟡 SQLite Not Thread-Safe

`trade_journal.py` (L76–L77):
```python
def _conn(self):
    return sqlite3.connect(self.db_path)
```

A new connection is created for every operation. If the Streamlit dashboard and the bot write/read concurrently, you'll get `database is locked` errors.

**Fix**: Use `check_same_thread=False` and a connection pool, or use WAL mode:
```python
def _conn(self):
    conn = sqlite3.connect(self.db_path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    return conn
```

---

### 🟡 Google Sheets API Rate Limiting Not Handled

`sheets_sync.py` — `sync_trade()` makes **3 separate API calls** per trade (append row, get column length, format row). Google Sheets API has a limit of **60 requests/min**. During active grid trading, this will break.

**Fix**: Batch writes using `gspread.batch_update()` and implement retry with exponential backoff for `APIError(429)`.

---

### 🟡 No Graceful Shutdown in Strategy

The strategy has `alert_startup` but no cleanup on exit. If the bot crashes or is stopped:
- Open orders remain on Binance
- No shutdown alert is sent to Telegram
- No final PnL snapshot is taken

**Fix**: Implement `on_stop()` override:
```python
def on_stop(self):
    self._cancel_all_orders()
    asyncio.get_event_loop().create_task(
        self.telegram.alert_shutdown("graceful stop")
    )
```

---

## 5. Strategy Logic Issues

### 🔴 State Machine Ordering Bug

`grid_state.py` (L14–L26):

```python
def evaluate(self, price, rsi, ema_200, bb_lower, bb_upper, ...):
    if rsi > rsi_overbought or price < ema_200:          # Check 1
        self.state = GridState.PAUSED
    if rsi < rsi_oversold and price <= bb_lower * 1.02:  # Check 2
        self.state = GridState.REACTIVATING
    if price > ema_200 and rsi < rsi_overbought:         # Check 3
        self.state = GridState.ACTIVE
    return self.state
```

**Problem**: These are `if` statements, not `elif`. If RSI is 30 (< oversold) and price is above EMA200:
1. Check 1 → False (RSI not overbought, price not below EMA)
2. Check 2 → Could be True → Sets REACTIVATING
3. Check 3 → True (price > EMA, RSI < 70) → **Overwrites to ACTIVE**

The REACTIVATING state is effectively **dead code** — it can never persist because Check 3 will always override it in the same conditions.

**Fix**: Use `elif` chain or `return` early after each state transition.

---

### 🟡 Grid Levels Clamp Silently

`grid_manager.py` (L31–L35):
```python
buy_price = bb.mid - spacing * i
buy_price = max(buy_price, bb.lower)  # Clamps to BB lower
```

When spacing × levels > BB width, multiple grid levels collapse to the same price. With 8 levels and high ATR, you could have 5 orders all at `bb.lower` — same price, wasting capital.

**Fix**: After clamping, deduplicate levels or reduce count dynamically:
```python
if buy_price == bb.lower and i > 1:
    break  # No more useful levels below
```

---

### 🟡 Sell Orders Don't Check Position Guard

`ta_grid_btcusdt.py` (L195–L205):
```python
for level in grid.sell_levels:
    if level["price"] <= current_price:
        continue
    self.place_order(...)  # No position guard check!
```

Buy orders are validated by `position_guard.can_place_order()`, but sell orders are placed unconditionally. If the bot holds less BTC than the total sell quantity, sell orders will fail at the exchange level.

---

## 6. Testing Gaps

### 🟡 No Tests for Core Business Logic

| Module | Has Tests? |
|--------|-----------|
| `indicators/*` | ✅ Comprehensive |
| `grid_manager.py` | ✅ Good |
| `circuit_breaker.py` | ✅ Good |
| `position_guard.py` | ✅ Good |
| `candle_feed.py` | ✅ Mocked |
| **`grid_state.py`** | **❌ None** |
| **`order_tracker.py`** | **❌ None** |
| **`trade_journal.py`** | **❌ None** |
| **`telegram_bot.py`** | **❌ None** |
| **`ta_grid_btcusdt.py`** | **❌ None** |
| **`pnl_reporter.py`** | **❌ None** |
| **`sheets_sync.py`** | **❌ None** |

**5 of 12 modules have tests. 7 have zero coverage.** The untested modules include the most important one — the main strategy script.

---

### 🟡 No Integration / End-to-End Tests

All tests are isolated unit tests. There's no test that:
- Runs the full `on_tick()` cycle with mocked connectors
- Verifies that a BUY fill → SELL fill produces correct PnL
- Verifies grid state transitions under realistic indicator sequences

---

### 🟡 RSI Implementation Uses SMA Instead of EMA

The RSI in `rsi.py` uses `.mean()` (SMA) for average gains/losses:
```python
avg_gain = gain.iloc[1:].mean()
avg_loss = loss.iloc[1:].mean()
```

The standard Wilder RSI uses **exponential smoothing** (SMMA). This will produce different RSI values than what Binance/TradingView shows, causing trade entry/exit mismatches during backtesting vs live.

---

### 🟢 Deprecated Pandas `.applymap()`

`app.py` (L228):
```python
styled = display.style.applymap(color_pnl, ...)
```
`.applymap()` was deprecated in Pandas 2.1.0. Use `.map()` instead.

---

## 7. Production Readiness

### 🔴 No Structured Logging

The entire project uses `print()` statements and basic `logger.info()` with no structured format. For a financial system running 24/7:

**Fix**: Add structured JSON logging with:
```python
import logging, json
class JsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "ts": self.formatTime(record),
            "level": record.levelname,
            "module": record.module,
            "msg": record.getMessage(),
        })
```

---

### 🟡 No Health Check Endpoint

The bot has no way to verify it's alive. Railway, Docker, or any orchestrator can't determine if the bot is running but stuck (e.g., in an infinite loop, halted by circuit breaker).

**Fix**: Add a simple HTTP health endpoint (Flask/FastAPI) that returns:
```json
{"status": "ok", "grid_state": "ACTIVE", "last_tick": "2026-05-07T22:00:00Z"}
```

---

### 🟡 No Database Backup Strategy

SQLite is the single source of truth for all trade history. If the volume dies, all data is lost.

**Fix**: 
- Enable WAL mode for concurrent reads
- Add a cron job to backup the DB to S3 daily
- Or migrate to PostgreSQL (Railway offers managed Postgres)

---

### 🟢 `datetime.utcnow()` Is Deprecated

Used throughout `trade_journal.py`, `app.py`, and `telegram_bot.py`. 

As of Python 3.12, `datetime.utcnow()` is deprecated. Use `datetime.now(timezone.utc)` instead.

---

## 📋 Prioritized Action Plan

### Must Fix Before Paper Trading

| # | Issue | Effort |
|---|-------|--------|
| 1 | **Fix `did_fill_order`** — implement buy/sell pairing with real PnL | 4h |
| 2 | **Cache candle fetches** — only refetch on hourly boundary | 1h |
| 3 | **Fix state machine `if` → `elif`** ordering bug | 15min |
| 4 | **Delete duplicate root-level files** | 15min |
| 5 | **Load `strategy.yaml`** in the script constructor | 1h |
| 6 | **Fix RSI** to use Wilder's smoothing (SMMA) | 30min |

### Must Fix Before Live Trading

| # | Issue | Effort |
|---|-------|--------|
| 7 | Lock down Terraform security groups (SSH + dashboard) | 30min |
| 8 | Add WebSocket reconnection with backoff | 2h |
| 9 | Enable SQLite WAL mode + add backup strategy | 1h |
| 10 | Add grid-level deduplication to prevent stacked orders | 1h |
| 11 | Add sell-side position guard check | 30min |
| 12 | Add `on_stop()` graceful shutdown handler | 30min |
| 13 | Add health check endpoint | 2h |

### Should Fix for Robustness

| # | Issue | Effort |
|---|-------|--------|
| 14 | Write tests for `grid_state.py`, `order_tracker.py`, `trade_journal.py` | 3h |
| 15 | Add integration test for full tick cycle | 4h |
| 16 | Implement structured JSON logging | 2h |
| 17 | Add Google Sheets rate limiting + batching | 2h |
| 18 | Replace `sys.path.insert` with proper package install | 1h |
| 19 | Fix deprecated `applymap()` and `datetime.utcnow()` | 30min |

---

> **Estimated total effort**: ~26 hours to address all 19 items.  
> **Critical path**: Items 1–6 block paper trading validation entirely.
