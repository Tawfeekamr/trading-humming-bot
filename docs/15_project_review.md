# 15 — Project Review & Audit Feedback

> **Date:** 2026-05-09  
> **Scope:** Full codebase audit — architecture, code quality, security, testing, deployment, risk management  
> **Result:** 130/130 tests passing ✅ · 7 critical issues · 8 important gaps · 10 nice-to-haves

---

## Overall Assessment

The TA-Enhanced BTC/USDT Grid Bot is a **well-architected, production-aware** system with clean separation of concerns, comprehensive Telegram integration, and robust risk management. The codebase demonstrates strong engineering discipline across modules.

However, several critical gaps exist that **must be addressed before live trading** with real capital.

---

## 🔴 Critical Issues (Fix Before Going Live)

### 1. Circuit Breaker Halts on Fresh Start (Boot Loop)

**File:** `src/risk/circuit_breaker.py` (lines 38–49)  
**File:** `hummingbot_files/scripts/ta_grid_btcusdt.py` (lines 449–451)

When `peak_equity == 0` (fresh start), `check()` immediately halts the bot. The strategy attempts to set equity on the first `on_tick`, but `_cached_indicators` is `None` on tick #1, so equity evaluates to `$0` — triggering the fail-safe.

**Flow:**
```
on_tick → _on_tick_inner → SOD reset → _cached_indicators is None → equity = 0
→ circuit_breaker.check(0) → peak_equity == 0 → HALT
```

**Impact:** Bot can never start trading on a fresh deployment.

**Fix:** Initialize `peak_equity` and `sod_equity` to `capital_usdt` in the constructor, or skip SOD reset until indicators are populated:
```python
# In __init__, after circuit_breaker initialization:
self.circuit_breaker.set_peak_equity(self.capital_usdt)
self.circuit_breaker.set_start_of_day_equity(self.capital_usdt)
```

---

### 2. Fee Calculation Missing Buy-Side Fee in Round-Trip PnL

**File:** `hummingbot_files/scripts/ta_grid_btcusdt.py` (lines 947, 1048)

A round-trip trade (BUY → SELL) incurs fees on **both** legs. Currently, only the sell-side fee is deducted from the round-trip net PnL:

```python
fee_est = quantity * price * self._fee_rate   # Line 947 — one side only
net_pnl = gross_pnl - fee                     # Line 1048 — missing buy fee
```

**Impact:** Net PnL is overstated by ~0.075% per round-trip. With frequent trading, this compounds significantly and gives a false sense of profitability.

**Fix:** Store the buy-side fee in `FillRecord`, then compute total fee on round-trip close:
```python
# In FillRecord dataclass, add:
fee: float = 0.0

# On BUY fill:
buy_fee = quantity * price * self._fee_rate
self._open_buys[order_id] = FillRecord(..., fee=buy_fee)

# On SELL round-trip close:
total_fee = matching_buy.fee + fee_est
net_pnl = gross_pnl - total_fee
```

---

### 3. OrderTracker Memory Leak

**File:** `src/grid/order_tracker.py` (lines 61–66)

`clear_history()` exists to purge filled/cancelled orders, but it is **never called** anywhere. Every order ever tracked stays in `_orders` indefinitely.

**Impact:** Unbounded memory growth over days/weeks of continuous operation.

**Fix:** Call `clear_history()` during grid refresh after `cancel_all()`:
```python
# In _cancel_all_orders():
self._grid_order_tracker.cancel_all()
self._grid_order_tracker.clear_history()
```

---

### 4. Terraform State Not in Remote Backend

**File:** `iac/aws-tokyo/terraform.tfstate` (35 KB on disk)

While `.gitignore` correctly excludes tfstate from version control, the state file resides only on the local disk. It likely contains EC2 instance IDs, security group IDs, VPC/subnet IDs, and IAM role ARNs.

**Impact:** If the local disk fails or the file is accidentally deleted, the entire infrastructure state is lost.

**Fix:** Migrate to S3 + DynamoDB remote backend:
```hcl
terraform {
  backend "s3" {
    bucket         = "trading-bot-tfstate"
    key            = "aws-tokyo/terraform.tfstate"
    region         = "ap-northeast-1"
    dynamodb_table = "terraform-locks"
    encrypt        = true
  }
}
```

---

### 5. No Minimum Order Size Validation (Binance LOT_SIZE)

**File:** `src/grid/grid_manager.py` (line 54)

Grid order quantities are calculated purely from `capital / (levels * 2) / price`, with no validation against Binance exchange filters:

```python
buy_qty = order_value / buy_price  # Could produce 0.000001 BTC = ~$0.10
```

**Impact:** Orders below Binance's `MIN_NOTIONAL` ($5–$10) or `LOT_SIZE` step size are silently rejected by the exchange. With auto-compound on equity drawdown, this becomes increasingly likely.

**Fix:** Fetch exchange info on init and validate:
```python
def _validate_order(self, price: float, quantity: float) -> tuple[float, float]:
    # Round to tick size and step size
    price = round(price / self.tick_size) * self.tick_size
    quantity = round(quantity / self.step_size) * self.step_size
    if price * quantity < self.min_notional:
        return None, None
    return price, quantity
```

---

### 6. Docker Health Check Port Not Exposed

**File:** `docker-compose.yml`

The health server runs on port 8080 inside the container (`src/health.py`), but the port is never exposed or mapped in docker-compose. No healthcheck directive exists.

**Impact:** External monitoring (AWS ALB, Railway, uptime checkers) cannot detect a stuck or crashed bot.

**Fix:**
```yaml
bot:
  build: .
  ports:
    - "8080:8080"
  healthcheck:
    test: ["CMD", "curl", "-f", "http://localhost:8080/"]
    interval: 30s
    timeout: 5s
    retries: 3
    start_period: 60s
```

---

### 7. Duplicate Imports in Strategy Script

**File:** `hummingbot_files/scripts/ta_grid_btcusdt.py` (lines 57, 67–68)

```python
import sys                # Line 57
import sys                # Line 67 — duplicate
from pathlib import Path  # Line 68 — already imported at line 16
```

**Impact:** Not a runtime bug, but signals copy-paste drift and harms readability.

**Fix:** Remove the duplicate imports at lines 67–68.

---

## 🟡 Important Gaps (Fix Soon)

### 8. No State Persistence on Shutdown

**File:** `hummingbot_files/scripts/ta_grid_btcusdt.py` (lines 1197–1216)

`on_stop()` cancels all orders and sends a Telegram alert but **never calls `_save_state()`**. Open positions may be lost on graceful shutdown.

**Fix:** Add `self._save_state()` at the beginning of `on_stop()`.

---

### 9. Missing `.dockerignore`

No `.dockerignore` exists. Every build sends `.git/`, `__pycache__/`, `data/`, `logs/`, `iac/`, and test files to the Docker daemon.

**Impact:** Slower builds, larger build context, potential leakage of sensitive data into images.

**Fix:** Create `.dockerignore`:
```
.git
__pycache__
*.py[cod]
data/
logs/
reports/
iac/
.env
.idea/
.vscode/
.pytest_cache/
scratch/
docs/
tests/
```

---

### 10. Empty DataFrame Crash Path in Candle Feed

**File:** `src/data/candle_feed.py` (line 27)  
**File:** `hummingbot_files/scripts/ta_grid_btcusdt.py` (line 500)

On API failure, `fetch_candles()` returns an empty DataFrame. The caller at line 500 does:

```python
current_price = float(closes.iloc[-1])  # IndexError if empty
```

While the `except` block at lines 492–495 catches `fetch_candles` exceptions, a successful API call returning zero rows (e.g., invalid symbol, empty market) would pass through unprotected.

**Fix:**
```python
df = self.candle_feed.fetch_candles(limit=250)
if df.empty or len(df) < self.bb_period:
    logger.warning("Insufficient candle data")
    return
```

---

### 11. Daily Report Fires Twice on Restart

**File:** `hummingbot_files/scripts/ta_grid_btcusdt.py` (line 449)

`_last_sod_reset` is not persisted. If the bot restarts mid-day, a second daily report is sent with partial data, and SOD equity is reset to current value (potentially different from the real start-of-day).

**Fix:** Persist `_last_sod_reset` in `grid_state.json`.

---

### 12. Non-Atomic State File Writes

**File:** `hummingbot_files/scripts/ta_grid_btcusdt.py` (lines 361–389)

`_save_state()` writes directly to `grid_state.json`. A crash mid-write corrupts the file and `_load_state()` fails silently, losing all open position tracking.

**Fix:** Use write-to-temp-then-rename:
```python
import os
tmp = self._state_file.with_suffix('.tmp')
with open(tmp, "w") as f:
    json.dump(data, f, indent=2)
os.replace(tmp, self._state_file)  # Atomic on Linux/macOS
```

---

### 13. WebSocket Feed is Implemented but Unused

**File:** `src/data/ws_feed.py`

A complete WebSocket price feed exists with reconnection logic and price validation, but is never imported or used by the strategy.

**Suggestion:** Integrate for real-time mid-price updates between 1h candle intervals. This would improve grid placement accuracy without the 55-minute data gap.

---

### 14. Telegram Commands Use subprocess for HTTP

**File:** `src/notifications/telegram_commands.py` (lines 63–96)

Every Telegram command spawns a Python subprocess to make HTTP calls (~300ms overhead). This was done to avoid PTB v20 async thread deadlocks, but `urllib.request` is thread-safe and can be used directly:

```python
def _tg_get(self, path, params=None, timeout=35):
    url = f"https://api.telegram.org/bot{self._token}/{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    resp = urllib.request.urlopen(url, timeout=timeout)
    return json.loads(resp.read().decode())
```

---

### 15. No Pinned Dependencies

**File:** `requirements.txt`

All dependencies use `>=` version constraints. A single upstream breaking change (e.g., `python-telegram-bot>=21.0` → `22.0`) could break the production bot silently after a fresh install.

**Fix:** Generate and maintain a lockfile:
```bash
pip freeze > requirements.lock
```
Or adopt `pyproject.toml` with exact versions.

---

## 🟢 Nice-to-Have Improvements

| # | Area | Issue | Suggestion |
|---|------|-------|------------|
| 16 | Testing | `test_zero_atr_rejected` is SKIPPED | Fix or remove the skip marker |
| 17 | Config | `strategy.yaml` has `atr.spacing_multiplier: 1.5` but `TAGridConfig` defaults to `0.8` | Document which is source of truth |
| 18 | Config | `strategy.yaml` has `exchange: "binance"` but `TAGridConfig` defaults to `"binance_paper_trade"` | Align config with code |
| 19 | Logging | `logging_config.py` uses date-based filenames + `RotatingFileHandler` | Won't rotate at midnight — use `TimedRotatingFileHandler` |
| 20 | Dashboard | `app.py` stale `sys.path.insert` to `parent.parent.parent` | Should be project root |
| 21 | Dashboard | `@st.cache_resource` on `TradeJournal` has no TTL | Data never auto-refreshes |
| 22 | Backtest | `vectorbt_sweep.py` uses `vbt.BinanceData.download` | Check vectorbt v2 API compatibility |
| 23 | Security | `/clear` command deletes crash logs | Add archive-before-delete option |
| 24 | Monitoring | Health endpoint only returns `ok`/`halted` | Add `last_trade_time`, `open_positions`, `uptime` |
| 25 | Code | `_peak_equity` exists on both strategy class and circuit breaker | Two separate trackers — consolidate |

---

## ✅ What's Done Well

| Area | Details |
|------|---------|
| **Architecture** | Clean `src/` layout: indicators, grid, risk, journal, notifications, monitoring, data |
| **Thread Safety** | Proper locks on state machine, circuit breaker, order tracker, event logger, journal |
| **Risk Management** | Dual-layer: circuit breaker (max DD + daily loss) + position guard (exposure + reserve) |
| **Observability** | JSON structured logging, daily rotation, crash log separation, event JSONL audit trail |
| **Telegram** | 16 commands: status, PnL, balance, pause/resume, capital, price, trades, pending, fees, system, logs, errors, clear, help |
| **State Persistence** | `grid_state.json` for positions; SQLite trade journal with WAL mode |
| **CI/CD** | GitHub Actions: test → deploy via SSM → verify containers → Telegram notify |
| **IaC** | Terraform for AWS Tokyo (ap-northeast-1) — reproducible infrastructure |
| **Security** | `.env` gitignored, API key checklist, separate testnet/live keys, dashboard bcrypt auth |
| **Testing** | 130 passing tests: indicators, grid, risk, lifecycle, paper trading, websocket validation |

---

## Priority Roadmap

```
Phase 1 — Pre-Live (Blockers)
├── 1. Fix circuit breaker boot loop
├── 2. Fix double-fee accounting
├── 3. Add OrderTracker cleanup
├── 5. Add Binance min order validation
└── 7. Clean up duplicate imports

Phase 2 — Reliability
├── 6. Expose health port in Docker
├── 8. Persist state on shutdown
├── 9. Add .dockerignore
├── 10. Guard against empty candle data
├── 12. Atomic state file writes
└── 15. Pin dependency versions

Phase 3 — Optimization
├── 11. Persist SOD reset flag
├── 13. Integrate WebSocket feed
├── 14. Replace subprocess HTTP calls
└── 16–25. Polish and backlog items
```

---

*Generated from full codebase audit — May 9, 2026*
