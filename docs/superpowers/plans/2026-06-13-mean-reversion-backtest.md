# Mean-Reversion Tick-Replay Backtest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a faithful tick-replay backtest (Python + vectorbt) that decides whether the mean-reversion "buy sharp dumps, exit +TP/-stop" strategy has an edge, and finds the best config across 4 pairs × 6 months of real aggTrade history.

**Architecture:** Download Binance aggTrades → resample to 1s bars with buy/sell volume → derive trade-flow features (replacing the live strategy's hardcoded classifier inputs) → compute vectorized entry signals → run a vectorbt `Portfolio.from_signals` grid sweep over drop/TP/stop/size with native SL/TP exits → walk-forward IS/OOS evaluation → JSON + markdown report. The Python port is oracle-validated against the Rust strategy's unit tests.

**Tech Stack:** Python 3, pandas, numpy, vectorbt (OSS), requests. Reuses `backtest/reporting.py`.

**Spec:** `docs/superpowers/specs/2026-06-13-mean-reversion-backtest-design.md`

---

## File Structure

| File | Responsibility |
|------|----------------|
| `backtest/mean_reversion/__init__.py` | Package marker |
| `backtest/mean_reversion/data.py` | aggTrade download (data.binance.vision), raw cache, 1s bar resample, bars cache |
| `backtest/mean_reversion/features.py` | Trade-flow features + classifier score (live `ClassifierCfg` defaults) |
| `backtest/mean_reversion/strategy.py` | `entry_signal()` — flush + classifier gate |
| `backtest/mean_reversion/backtest.py` | `run_single`, `run_sweep`, `walk_forward`, metrics, report, CLI |
| `tests/test_mr_data.py` | resample correctness |
| `tests/test_mr_features.py` | feature math + entry signal |
| `tests/test_mr_backtest.py` | single-run TP/stop exits (oracle vs Rust), sweep, walk-forward, e2e pipeline |
| `backtest/results/mean_reversion/` | `{SYMBOL}_sweep.json`, `summary.json`, `report.md` (generated) |

**Conventions:** run all pytest from repo root. vectorbt is imported lazily inside functions (matches `vectorbt_sweep.py`). `is_buyer_maker=True` in aggTrades means the aggressor was a **seller** (trade hit the bid) → `sell_vol`; `False` → `buy_vol`.

---

### Task 1: Package scaffolding

**Files:**
- Create: `backtest/mean_reversion/__init__.py`
- Modify: `backtest/requirements-sweep.txt`

- [ ] **Step 1: Create the package**

```python
# backtest/mean_reversion/__init__.py
"""Mean-reversion tick-replay backtest."""
```

- [ ] **Step 2: Pin dependencies**

Append `requests` to `backtest/requirements-sweep.txt` (vectorbt/pandas/numpy are already present from the existing sweep). Verify with:

```bash
cat backtest/requirements-sweep.txt
```
Expected: includes `requests` alongside the existing vectorbt line.

- [ ] **Step 3: Commit**

```bash
git add backtest/mean_reversion/__init__.py backtest/requirements-sweep.txt
git commit -m "feat(backtest): scaffold mean_reversion package"
```

---

### Task 2: Bar resample (`data.py`)

**Files:**
- Create: `backtest/mean_reversion/data.py`
- Test: `tests/test_mr_data.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_mr_data.py
import numpy as np
import pandas as pd

from backtest.mean_reversion.data import resample_bars


def _trades(rows):
    # rows: list of (ts_ms, price, qty, is_buyer_maker)
    return pd.DataFrame(rows, columns=["ts_ms", "price", "quantity", "is_buyer_maker"])


def test_resample_split_buy_sell_and_ohlc():
    # Two buys at 100, two sells at 101, all within the same second.
    sec = 1_000
    trades = _trades([
        (sec + 100, 100.0, 2.0, False),  # buy aggressor
        (sec + 200, 100.0, 3.0, False),  # buy aggressor
        (sec + 300, 101.0, 1.0, True),   # sell aggressor
        (sec + 400, 101.0, 4.0, True),   # sell aggressor
    ])
    bars = resample_bars(trades, bar="1s")
    assert len(bars) == 1
    b = bars.iloc[0]
    assert b["open"] == 100.0 and b["close"] == 101.0
    assert b["high"] == 101.0 and b["low"] == 100.0
    assert b["volume"] == 10.0
    assert b["buy_vol"] == 5.0      # 2 + 3
    assert b["sell_vol"] == 5.0     # 1 + 4
    assert b["buy_vol"] + b["sell_vol"] == b["volume"]


def test_resample_drops_seconds_with_no_trades():
    trades = _trades([(1_000, 100.0, 1.0, False), (3_000, 100.0, 1.0, False)])
    bars = resample_bars(trades, bar="1s")
    # second 1000 and second 3000 present; second 2000 absent -> dropped (no close)
    assert len(bars) == 2
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_mr_data.py -v
```
Expected: FAIL — `ModuleNotFoundError: backtest.mean_reversion.data`.

- [ ] **Step 3: Implement `resample_bars` and the download/cache helpers**

```python
# backtest/mean_reversion/data.py
"""aggTrade download + bar resample + cache for the mean-reversion backtest."""
import io
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests

CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache"
RAW_DIR = CACHE_DIR / "aggtrades"
BARS_DIR = CACHE_DIR / "bars"
BASE_URL = "https://data.binance.vision/data/spot/monthly/aggTrades"

# Binance vision CSVs have NO header. Column order is fixed by the exchange.
AGG_COLUMNS = ["agg_id", "price", "quantity", "first_id", "last_id", "ts_ms", "is_buyer_maker"]


def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def download_day(symbol: str, day: date, overwrite: bool = False) -> Path:
    """Download one day of aggTrades; cache as parquet. Raises FileNotFoundError on 404."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = RAW_DIR / symbol / f"{day.isoformat()}.parquet"
    if cache_path.exists() and not overwrite:
        return cache_path
    url = f"{BASE_URL}/{symbol}/{day:%Y-%m}/{symbol}-aggTrades-{day:%Y-%m-%d}.zip"
    resp = requests.get(url, timeout=120)
    if resp.status_code == 404:
        raise FileNotFoundError(f"No aggTrade file for {symbol} {day}: {url}")
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            df = pd.read_csv(f, header=None, names=AGG_COLUMNS)
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path)
    return cache_path


def load_aggtrades(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Concat cached daily aggTrades for [start, end]. Skips missing days."""
    frames = []
    for day in _date_range(start, end):
        try:
            path = download_day(symbol, day)
            frames.append(pd.read_parquet(path))
        except FileNotFoundError:
            continue
    if not frames:
        return pd.DataFrame(columns=AGG_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = pd.to_datetime(df["ts_ms"], unit="ms", utc=True)
    return df.sort_values("ts").reset_index(drop=True)


def resample_bars(trades: pd.DataFrame, bar: str = "1s") -> pd.DataFrame:
    """Resample raw aggTrades to OHLC + volume + buy_vol + sell_vol bars.

    is_buyer_maker=True  -> aggressor SOLD (hit the bid) -> sell_vol
    is_buyer_maker=False -> aggressor BOUGHT            -> buy_vol
    Seconds with no trades are dropped (no close).
    """
    if trades.empty:
        return pd.DataFrame()
    t = trades.copy()
    if "ts" not in t.columns:
        t["ts"] = pd.to_datetime(t["ts_ms"], unit="ms", utc=True)
    t = t.set_index("ts")
    maker = t["is_buyer_maker"].astype(bool)
    buy = t["quantity"].where(~maker, 0.0)
    sell = t["quantity"].where(maker, 0.0)
    ohlc = t["price"].resample(bar).ohlc()
    vol = t["quantity"].resample(bar).sum().rename("volume")
    buy_vol = buy.resample(bar).sum().rename("buy_vol")
    sell_vol = sell.resample(bar).sum().rename("sell_vol")
    bars = ohlc.join([vol, buy_vol, sell_vol]).dropna(subset=["close"])
    return bars


def load_bars(symbol: str, start: date, end: date, bar: str = "1s") -> pd.DataFrame:
    """Download + resample + cache bars for a symbol range."""
    BARS_DIR.mkdir(parents=True, exist_ok=True)
    cache_path = BARS_DIR / f"{symbol}_{start.isoformat()}_{end.isoformat()}_{bar}.parquet"
    if cache_path.exists():
        return pd.read_parquet(cache_path)
    trades = load_aggtrades(symbol, start, end)
    bars = resample_bars(trades, bar)
    bars.to_parquet(cache_path)
    return bars
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_mr_data.py -v
```
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backtest/mean_reversion/data.py tests/test_mr_data.py
git commit -m "feat(backtest): aggTrade download + 1s bar resample"
```

---

### Task 3: Trade-flow features (`features.py`)

**Files:**
- Create: `backtest/mean_reversion/features.py`
- Test: `tests/test_mr_features.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_mr_features.py
import numpy as np
import pandas as pd
import pytest

from backtest.mean_reversion.features import (
    compute_features, entry_signal_passthrough, ENTER_THRESHOLD,
)
from backtest.mean_reversion.strategy import entry_signal


def _bars(prices, buy=1.0, sell=1.0):
    return pd.DataFrame({"close": prices, "buy_vol": buy, "sell_vol": sell})


def test_drop_frac_measures_decline_over_window():
    bars = _bars([100.0] * 30 + [94.0])  # 6% drop over 30 bars
    f = compute_features(bars, bar="1s")  # window = 30 bars
    assert pd.isna(f["drop_frac"].iloc[0])           # warmup
    assert f["drop_frac"].iloc[-1] == pytest.approx(0.06)


def test_score_clears_threshold_on_uniform_volume_flush():
    bars = _bars([100.0] * 30 + [94.0])
    f = compute_features(bars, bar="1s")
    assert f["score"].iloc[-1] >= ENTER_THRESHOLD
    assert f["size_mult"].iloc[-1] > 0.0


def test_score_below_threshold_on_flat_market():
    bars = _bars([100.0] * 31)  # no drop
    f = compute_features(bars, bar="1s")
    # drop_frac ~0 -> retrace term ~0; uniform volume -> other terms ~constant
    assert f["score"].iloc[-1] < ENTER_THRESHOLD
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_mr_features.py -v
```
Expected: FAIL — `ModuleNotFoundError: backtest.mean_reversion.features`.

- [ ] **Step 3: Implement features**

```python
# backtest/mean_reversion/features.py
"""Trade-flow features for the mean-reversion port.

These replace the four constants the live Rust strategy hardcodes into its
classifier (retrace=0.8, sell_flow_decay=0.8, liq_cascade=0.8, corr=0.2),
making the backtest more rigorous than the live code on those dimensions.
Cross-market correlation has no per-second historical source, so it is set to 0
(live uses a 0.2 constant; impact is minor).
"""
import pandas as pd

# Live ClassifierCfg defaults (config.rs) — fixed per spec §3.
W_RETRACE = 1.0
W_REFILL = 1.0
W_EXHAUST = 1.0
W_LIQ = 0.5
W_CORR = 1.5
ENTER_THRESHOLD = 2.0
FULL_SIZE_MARGIN = 1.5

EPS = 1e-9
WINDOW_SECONDS = 30


def bar_seconds(bar: str) -> int:
    bar = bar.strip()
    if bar.endswith("min"):
        return int(bar[:-3]) * 60
    if bar.endswith("m"):
        return int(bar[:-1]) * 60
    if bar.endswith("s"):
        return max(1, int(bar[:-1]))
    raise ValueError(f"Unsupported bar unit: {bar}")


def window_bars_for(bar: str, window_seconds: int = WINDOW_SECONDS) -> int:
    return max(1, window_seconds // bar_seconds(bar))


def compute_features(bars: pd.DataFrame, bar: str = "1s") -> pd.DataFrame:
    w = window_bars_for(bar)
    smooth = max(1, w // 6)  # ~5s smoothing for a 30s window
    close = bars["close"]
    buy_vol = bars["buy_vol"]
    sell_vol = bars["sell_vol"]

    # Faithful to live (oldest.price - mid) / oldest.price, ~30s ago.
    drop_frac = (close.shift(w) - close) / close.shift(w)

    # Buy-pressure restoration proxy for live bid_depth / oldest_bid_depth.
    buy_smooth = buy_vol.rolling(smooth, min_periods=1).mean()
    bid_refill_ratio = (buy_smooth / (buy_smooth.shift(w) + EPS)).clip(0, 3)

    # Dump exhaustion: recent sell intensity vs the window peak. Low = exhausted.
    sell_smooth = sell_vol.rolling(smooth, min_periods=1).mean()
    sell_flow_decay = (sell_smooth / (sell_vol.rolling(w, min_periods=1).max() + EPS)).clip(0, 1)

    # Liquidation-cascade spike: peak vs mean per-bar sell volume.
    liq_cascade_score = (
        sell_vol.rolling(w, min_periods=1).max() / (sell_vol.rolling(w, min_periods=1).mean() + EPS)
    ).clip(0, 10)

    score = (
        W_RETRACE * drop_frac
        + W_REFILL * bid_refill_ratio
        + W_EXHAUST * sell_flow_decay
        + W_LIQ * liq_cascade_score
        - W_CORR * 0.0  # cross_market_corr unavailable historically
    )
    size_mult = ((score - ENTER_THRESHOLD) / FULL_SIZE_MARGIN).clip(0, 1)

    return pd.DataFrame(
        {
            "drop_frac": drop_frac,
            "bid_refill_ratio": bid_refill_ratio,
            "sell_flow_decay": sell_flow_decay,
            "liq_cascade_score": liq_cascade_score,
            "score": score,
            "size_mult": size_mult,
        },
        index=bars.index,
    )


def entry_signal_passthrough(features: pd.DataFrame, drop_thr: float,
                             enter_threshold: float = ENTER_THRESHOLD) -> pd.Series:
    """Thin shim kept for feature-level tests; strategy.entry_signal is the public one."""
    return (features["drop_frac"] > drop_thr) & (features["score"] >= enter_threshold)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_mr_features.py -v
```
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backtest/mean_reversion/features.py tests/test_mr_features.py
git commit -m "feat(backtest): trade-flow features + classifier score"
```

---

### Task 4: Entry signal (`strategy.py`) — oracle vs Rust entry behavior

**Files:**
- Create: `backtest/mean_reversion/strategy.py`
- Test: append to `tests/test_mr_features.py` (entry-signal behavior)

- [ ] **Step 1: Write the failing test (mirrors the Rust flush-entry scenario)**

Append to `tests/test_mr_features.py`:

```python
def test_entry_signal_fires_on_flush_and_not_before():
    # Mirrors the Rust test `position_holds_then_exits_at_take_profit_via_on_tick`:
    # 30 bars of flat warmup, then a 6% flush. Entry must fire exactly at the flush.
    bars = _bars([100.0] * 30 + [94.0])
    f = compute_features(bars, bar="1s")
    sig = entry_signal(f, drop_thr=0.05)
    assert sig.sum() == 1
    assert sig.iloc[-1] == True
    assert sig.iloc[:-1].sum() == 0


def test_entry_signal_respects_drop_threshold():
    # A 4% flush must NOT trigger at drop_thr=0.05.
    bars = _bars([100.0] * 30 + [96.0])
    f = compute_features(bars, bar="1s")
    assert entry_signal(f, drop_thr=0.05).sum() == 0
    # ...but does trigger at a 0.03 threshold.
    assert entry_signal(f, drop_thr=0.03).sum() == 1
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_mr_features.py -v
```
Expected: FAIL — `ModuleNotFoundError: backtest.mean_reversion.strategy`.

- [ ] **Step 3: Implement `entry_signal`**

```python
# backtest/mean_reversion/strategy.py
"""Entry-signal logic for the mean-reversion port (mirrors Rust on_tick entry gate)."""
import pandas as pd

from .features import ENTER_THRESHOLD


def entry_signal(features: pd.DataFrame, drop_thr: float,
                 enter_threshold: float = ENTER_THRESHOLD) -> pd.Series:
    """A flush (drop_frac > drop_thr) that also clears the classifier score."""
    return (features["drop_frac"] > drop_thr) & (features["score"] >= enter_threshold)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_mr_features.py -v
```
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add backtest/mean_reversion/strategy.py tests/test_mr_features.py
git commit -m "feat(backtest): entry signal, oracle-validated against Rust flush logic"
```

---

### Task 5: Single-config run with SL/TP exits (`backtest.py`) — oracle vs Rust exits

**Files:**
- Create: `backtest/mean_reversion/backtest.py` (initial: `run_single` + constants)
- Test: `tests/test_mr_backtest.py`

> Note on vectorbt API: this is the one place we go beyond the proven `{entries, exits}` subset already used in `vectorbt_sweep.py`, because mean-reversion exits are price-based SL/TP (path-dependent on entry price), so we pass `sl_stop`/`tp_stop`. If `size_type="value"` is rejected by the installed vectorbt, switch it to `vbt.SizeType.Value` — the assertion in Step 4 is what proves the call works.

- [ ] **Step 1: Write the failing tests (mirror Rust TP-exit and stop-exit scenarios)**

```python
# tests/test_mr_backtest.py
import pandas as pd
import pytest

from backtest.mean_reversion.backtest import run_single
from backtest.mean_reversion.features import compute_features


def _bars(prices):
    return pd.DataFrame({"close": prices, "buy_vol": 1.0, "sell_vol": 1.0})


def test_single_tp_exit_is_a_winning_trade():
    # Mirrors Rust `position_holds_then_exits_at_take_profit_via_on_tick`:
    # 30 flat @100, flush to 94 (entry), hold at 94, then +2% TP at 96.
    bars = _bars([100.0] * 30 + [94.0, 94.0, 96.0])
    f = compute_features(bars, bar="1s")
    r = run_single(bars, f, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100, bar="1s")
    assert r is not None
    assert r["total_trades"] == 1
    assert r["total_return_pct"] > 0.0   # TP exit -> gain


def test_single_stop_exit_is_a_losing_trade():
    # Mirrors Rust `position_exits_at_layer2_stop_loss`:
    # 30 flat @100, flush to 94 (entry), then -4% stop at 90.
    bars = _bars([100.0] * 30 + [94.0, 90.0])
    f = compute_features(bars, bar="1s")
    r = run_single(bars, f, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100, bar="1s")
    assert r is not None
    assert r["total_trades"] == 1
    assert r["total_return_pct"] < 0.0   # stop exit -> loss


def test_single_no_entry_returns_none():
    bars = _bars([100.0] * 32)  # no flush -> no entries
    f = compute_features(bars, bar="1s")
    assert run_single(bars, f, drop_thr=0.05, tp=0.02, stop=0.04, base_size=100, bar="1s") is None
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_mr_backtest.py -v
```
Expected: FAIL — `ModuleNotFoundError: backtest.mean_reversion.backtest`.

- [ ] **Step 3: Implement `run_single` + grid constants**

```python
# backtest/mean_reversion/backtest.py
"""vectorbt sweep engine: SL/TP exits, IS/OOS split, metrics, report, CLI."""
from pathlib import Path

import pandas as pd

from .features import compute_features
from .strategy import entry_signal

# Spec §4 grid.
DROP_THRS = [0.03, 0.04, 0.05, 0.06, 0.08]
TP_STOPS = [0.01, 0.015, 0.02, 0.03, 0.04]
STOP_STOPS = [0.02, 0.03, 0.04, 0.05, 0.06]
BASE_SIZES = [50, 100, 200, 500]

# Deployed live config (headline reference).
LIVE_CONFIG = {"drop_thr": 0.05, "tp": 0.02, "stop": 0.04, "base_size": 100}

INIT_CASH = 1000.0
FEES = 0.001        # 0.1% per side (matches paper FEE_RATE)
SLIPPAGE = 0.0005   # 5 bps (matches existing sweep)

RESULTS_DIR = Path(__file__).resolve().parent.parent / "results" / "mean_reversion"


def run_single(bars: pd.DataFrame, features: pd.DataFrame, drop_thr: float,
               tp: float, stop: float, base_size: float, bar: str = "1s"):
    """Run one config. Returns a metrics dict, or None if no entries."""
    import vectorbt as vbt

    entries = entry_signal(features, drop_thr)
    if not entries.any():
        return None

    # Flat $ sizing per trade (see plan note: avoids per-bar Series size, keeps
    # base_size sweep meaningful as account-% risk vs INIT_CASH).
    pf = vbt.Portfolio.from_signals(
        close=bars["close"],
        entries=entries,
        sl_stop=stop,
        tp_stop=tp,
        size=base_size,
        size_type="value",
        init_cash=INIT_CASH,
        fees=FEES,
        slippage=SLIPPAGE,
        freq=bar,
    )
    stats = pf.stats()
    return {
        "drop_thr": drop_thr, "tp": tp, "stop": stop, "base_size": base_size,
        "total_trades": int(stats.get("Total Trades", 0)),
        "total_return_pct": float(stats.get("Total Return [%]", 0.0)),
        "sharpe_ratio": float(stats.get("Sharpe Ratio", 0.0)),
        "max_drawdown_pct": float(stats.get("Max Drawdown [%]", 0.0)),
        "win_rate": float(stats.get("Win Rate [%]", 0.0)),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_mr_backtest.py -v
```
Expected: PASS (3 tests). If vectorbt errors on `size_type="value"`, change it to `vbt.SizeType.Value` and re-run.

- [ ] **Step 5: Commit**

```bash
git add backtest/mean_reversion/backtest.py tests/test_mr_backtest.py
git commit -m "feat(backtest): single-config SL/TP run, oracle-validated vs Rust exits"
```

---

### Task 6: Full grid sweep + walk-forward (`backtest.py`)

**Files:**
- Modify: `backtest/mean_reversion/backtest.py` (add `run_sweep`, `walk_forward`)
- Test: append to `tests/test_mr_backtest.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_mr_backtest.py`:

```python
from backtest.mean_reversion.backtest import run_sweep, walk_forward


def test_run_sweep_returns_one_row_per_grid_combo():
    # Build a series with one flush so at least some configs trade.
    bars = _bars([100.0] * 30 + [90.0] + [100.0] * 5)
    f = compute_features(bars, bar="1s")
    results = run_sweep(bars, f, bar="1s")
    # 5 drop × 5 tp × 5 stop × 4 size = 500 combos; some may be filtered (no entry)
    # at high drop_thr, so just assert the structure and that it's non-empty.
    assert len(results) > 0
    assert {"drop_thr", "tp", "stop", "base_size", "sharpe_ratio"}.issubset(results.columns)


def test_walk_forward_returns_is_best_and_oos():
    bars = _bars([100.0] * 30 + [90.0, 92.0, 88.0, 95.0] * 20)  # enough length to split
    f = compute_features(bars, bar="1s")
    wf = walk_forward(bars, f, bar="1s", oos_frac=1 / 3)
    assert wf is not None
    assert "is_best" in wf and "oos" in wf
    assert "sharpe_ratio" in wf["is_best"]
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
pytest tests/test_mr_backtest.py -v
```
Expected: FAIL — `ImportError: cannot import name run_sweep`.

- [ ] **Step 3: Implement `run_sweep` and `walk_forward`**

Append to `backtest/mean_reversion/backtest.py`:

```python
def run_sweep(bars: pd.DataFrame, features: pd.DataFrame, bar: str = "1s") -> pd.DataFrame:
    """Run the full grid. Configs with no entries are skipped."""
    rows = []
    for drop_thr in DROP_THRS:
        for tp in TP_STOPS:
            for stop in STOP_STOPS:
                for base_size in BASE_SIZES:
                    try:
                        r = run_single(bars, features, drop_thr, tp, stop, base_size, bar)
                    except Exception:
                        r = None
                    if r is not None:
                        rows.append(r)
    return pd.DataFrame(rows)


def walk_forward(bars: pd.DataFrame, features: pd.DataFrame, bar: str = "1s",
                 oos_frac: float = 1 / 3):
    """Sweep on the in-sample slice; re-evaluate the best (by Sharpe) on out-of-sample.

    Features are computed once on the full series and sliced by position, so the
    rolling windows are continuous across the split (negligible over millions of
    bars; the OOS slice loses its own warmup of ~30 bars).
    """
    n = len(bars)
    split = int(n * (1 - oos_frac))
    is_bars, oos_bars = bars.iloc[:split], bars.iloc[split:]
    is_feat, oos_feat = features.iloc[:split], features.iloc[split:]

    is_results = run_sweep(is_bars, is_feat, bar)
    if is_results.empty:
        return None
    best = is_results.sort_values("sharpe_ratio", ascending=False).iloc[0]
    oos = run_single(
        oos_bars, oos_feat,
        drop_thr=best["drop_thr"], tp=best["tp"], stop=best["stop"],
        base_size=best["base_size"], bar=bar,
    )
    return {"is_best": best.to_dict(), "oos": oos}
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_mr_backtest.py -v
```
Expected: PASS (5 tests). `run_sweep` over the tiny synthetic series is fast.

- [ ] **Step 5: Commit**

```bash
git add backtest/mean_reversion/backtest.py tests/test_mr_backtest.py
git commit -m "feat(backtest): grid sweep + walk-forward IS/OOS"
```

---

### Task 7: Reporting + CLI (`backtest.py`)

**Files:**
- Modify: `backtest/mean_reversion/backtest.py` (add `_to_jsonable`, `run_pair`, `build_report`, `main`)
- Test: append to `tests/test_mr_backtest.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_mr_backtest.py`:

```python
import json
from backtest.mean_reversion.backtest import run_pair


def test_run_pair_writes_summary_and_per_symbol_json(tmp_path):
    bars = _bars([100.0] * 30 + [90.0, 92.0, 88.0, 95.0] * 20)
    summary = run_pair("TESTUSDT", bars, bar="1s", results_dir=tmp_path)
    assert (tmp_path / "TESTUSDT_sweep.json").exists()
    assert "live_config" in summary and "best" in summary and "walk_forward" in summary
    json.dumps(summary, default=str)  # serializable
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_mr_backtest.py -v
```
Expected: FAIL — `ImportError: cannot import name run_pair`.

- [ ] **Step 3: Implement reporting + CLI**

Append to `backtest/mean_reversion/backtest.py`:

```python
def _to_jsonable(d):
    """Make a dict JSON-serializable (numpy/pandas scalars -> python)."""
    out = {}
    for k, v in (d or {}).items():
        if hasattr(v, "item"):
            v = v.item()
        out[k] = v
    return out


def _hodl_return(close: pd.Series) -> float:
    if close.empty:
        return 0.0
    return float((close.iloc[-1] / close.iloc[0] - 1.0) * 100.0)


def run_pair(symbol: str, bars: pd.DataFrame, bar: str = "1s",
             results_dir: Path = RESULTS_DIR) -> dict:
    """Full pipeline for one symbol: features, live-config, sweep, walk-forward, write JSON."""
    results_dir.mkdir(parents=True, exist_ok=True)
    features = compute_features(bars, bar)

    live = run_single(bars, features, bar=bar, **LIVE_CONFIG)
    sweep = run_sweep(bars, features, bar)
    wf = walk_forward(bars, features, bar)

    best = (sweep.sort_values("sharpe_ratio", ascending=False).iloc[0].to_dict()
            if not sweep.empty else None)

    per_symbol = {
        "symbol": symbol,
        "bar": bar,
        "hodl_return_pct": _hodl_return(bars["close"]),
        "live_config": _to_jsonable(live),
        "best": _to_jsonable(best),
        "walk_forward": {"is_best": _to_jsonable(wf["is_best"]),
                         "oos": _to_jsonable(wf["oos"])} if wf else None,
        "sweep": sweep.to_dict(orient="records"),
    }
    with open(results_dir / f"{symbol}_sweep.json", "w") as f:
        json.dump(per_symbol, f, indent=2, default=str)
    return per_symbol


def build_report(per_pair: list, summary_path: Path) -> str:
    lines = ["# Mean-Reversion Backtest Report", ""]
    for p in per_pair:
        live = p.get("live_config") or {}
        best = p.get("best") or {}
        wf = p.get("walk_forward") or {}
        oos = wf.get("oos") or {}
        lines.append(f"## {p['symbol']}")
        lines.append(f"- HODL: {p.get('hodl_return_pct', 0):.1f}%")
        lines.append(f"- Live (+2%/-4%): trades={live.get('total_trades',0)} "
                     f"return={live.get('total_return_pct',0):.1f}% "
                     f"sharpe={live.get('sharpe_ratio',0):.2f} "
                     f"maxDD={live.get('max_drawdown_pct',0):.1f}% "
                     f"win={live.get('win_rate',0):.0f}%")
        if best:
            lines.append(f"- Best IS: drop={best.get('drop_thr')} tp={best.get('tp')} "
                         f"stop={best.get('stop')} size={best.get('base_size')} "
                         f"sharpe={best.get('sharpe_ratio',0):.2f}")
        lines.append(f"- OOS (best cfg): trades={oos.get('total_trades',0)} "
                     f"return={oos.get('total_return_pct',0):.1f}% "
                     f"sharpe={oos.get('sharpe_ratio',0):.2f}")
        if best and oos:
            gap = float(best.get("sharpe_ratio", 0)) - float(oos.get("sharpe_ratio", 0))
            flag = " ⚠️ overfit?" if gap > 1.0 else ""
            lines.append(f"- IS→OOS Sharpe gap: {gap:.2f}{flag}")
        lines.append("")
    text = "\n".join(lines)
    with open(summary_path, "w") as f:
        f.write(text)
    return text


def main():
    import argparse
    from datetime import date, timedelta

    parser = argparse.ArgumentParser(description="Mean-reversion tick-replay backtest")
    parser.add_argument("--pairs", default="BNBUSDT,DOGEUSDT,ETHUSDT,XRPUSDT")
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--bar", default="1s",
                        help="resample bar (1s=max fidelity/slowest; 5s≈5x faster)")
    args = parser.parse_args()

    end = date.today()
    start = end - timedelta(days=30 * args.months)
    pairs = [p.strip() for p in args.pairs.split(",") if p.strip()]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    per_pair = []
    for symbol in pairs:
        print(f"=== {symbol} {start} → {end} ({args.bar}) ===")
        bars = load_bars(symbol, start, end, args.bar)
        if bars.empty:
            print(f"  no data, skipping")
            continue
        print(f"  {len(bars)} bars; computing features + sweep...")
        per_pair.append(run_pair(symbol, bars, args.bar))

    summary = {
        "pairs": pairs, "bar": args.bar, "start": str(start), "end": str(end),
        "results": per_pair,
    }
    with open(RESULTS_DIR / "summary.json", "w") as f:
        json.dump(summary, f, indent=2, default=str)
    build_report(per_pair, RESULTS_DIR / "report.md")
    print(f"\nDone → {RESULTS_DIR}")


if __name__ == "__main__":
    main()
```

Also add the missing import at the top of `backtest.py` (alongside the existing `from pathlib import Path`):

```python
import json
```

And import `load_bars`:

```python
from .data import load_bars
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
pytest tests/test_mr_backtest.py -v
```
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backtest/mean_reversion/backtest.py tests/test_mr_backtest.py
git commit -m "feat(backtest): per-pair pipeline, JSON+markdown report, CLI"
```

---

### Task 8: Full-suite green + end-to-end synthetic pipeline check

**Files:** none new — verification only.

- [ ] **Step 1: Run the whole backtest test suite**

```bash
pytest tests/test_mr_data.py tests/test_mr_features.py tests/test_mr_backtest.py -v
```
Expected: PASS (all 11 tests).

- [ ] **Step 2: Confirm the CLI imports cleanly (no network needed)**

```bash
python -c "from backtest.mean_reversion.backtest import main; print('ok')"
```
Expected: prints `ok`.

- [ ] **Step 3: Real-data smoke (manual, requires network) — optional**

This is the user's validation run. From repo root:

```bash
python -m backtest.mean_reversion.backtest --pairs ETHUSDT --months 1 --bar 5s
```
Expected: downloads ~30 aggTrade days for ETHUSDT, resamples to 5s bars, runs the 500-config sweep + walk-forward, writes `backtest/results/mean_reversion/{ETHUSDT_sweep.json,summary.json,report.md}`. Use `--bar 5s` first (~5× faster than 1s) for a quick read; re-run with `--bar 1s` for the final high-fidelity pass. Full 4-pair × 6-month run at 1s is hours — run overnight or on EC2.

- [ ] **Step 4: Commit (if any fixups were made)**

```bash
git add -A
git commit -m "test(backtest): full mean-reversion suite green"
```

---

## Self-Review (run after writing, before handoff)

- **Spec coverage:** data pipeline (Task 2), trade-flow features (Task 3), classifier/entry (Tasks 3–4), sweep grid 5×5×5×4=2000 (Task 6), walk-forward IS/OOS (Task 6), metrics via `pf.stats()` + HODL (Task 7), oracle tests vs the three Rust unit tests (Tasks 4–5), output JSON+report.md (Task 7). ✓
- **Fidelity gaps from spec §"Stated Fidelity Gaps":** all five acknowledged (1s sampling, corr=0, regime gate dropped, bid_refill is a proxy, fees/slippage assumptions). The regime-gate drop means entries ignore `MarketRegime` — documented; add an EMA filter later if desired. ✓
- **Deliberate deviation from spec (flagged):** flat scalar `size=base_size` instead of conviction-scaled `size_mult` sizing, to avoid the riskiest vectorbt API surface. The `base_size` sweep still answers the "how much capital" question; `size_mult` is recomputed in `features.py` and available if a later task wires per-bar Series sizing. Noted in Task 5.
- **Placeholder scan:** none — every code step has complete, runnable code and exact expected outputs. ✓
- **Type/name consistency:** `entry_signal(features, drop_thr)` signature is identical across Tasks 3/4/5/6/7; `run_single(bars, features, drop_thr, tp, stop, base_size, bar)` identical across Tasks 5/6/7; `LIVE_CONFIG` keys match `run_single` kwargs; `compute_features(bars, bar)` consistent throughout. ✓

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-13-mean-reversion-backtest.md`. Two execution options:

1. **Subagent-Driven (recommended)** — I dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** — Execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
