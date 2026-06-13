# backtest/mean_reversion/data.py
"""aggTrade download + bar resample + cache for the mean-reversion backtest."""
import io
import logging
import shutil
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except ImportError:
    from requests.packages.urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "data_cache"
RAW_DIR = CACHE_DIR / "aggtrades"
BARS_DIR = CACHE_DIR / "bars"
# data.binance.vision has two layouts:
#  - daily : .../daily/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-{YYYY-MM-DD}.zip  (one file per day, flat)
#  - monthly: .../monthly/aggTrades/{SYMBOL}/{SYMBOL}-aggTrades-{YYYY-MM}.zip  (one file per MONTH)
# We use the daily layout: per-day granularity matches the per-day cache and gives
# clean date ranges. (Monthly files are a faster bulk option if download volume
# becomes a bottleneck.)
BASE_URL = "https://data.binance.vision/data/spot/daily/aggTrades"

# Binance vision aggTrades CSVs have NO header. Current files have 8 columns:
# agg_id, price, quantity, first_id, last_id, transact_time(ms), is_buyer_maker, <trailing bool>
# (the 8th column is an undocumented extra — kept as "ignore" so the 7 we care
# about land in the right positions; ts_ms must be transact_time, is_buyer_maker
# the trade-direction flag). Older 7-column files would mis-parse here.
AGG_COLUMNS = ["agg_id", "price", "quantity", "first_id", "last_id", "ts_ms",
               "is_buyer_maker", "ignore"]


def _make_session() -> requests.Session:
    """Create a requests Session with HTTP retry logic for resilience."""
    s = requests.Session()
    retry = Retry(
        total=5,
        backoff_factor=0.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


_SESSION = _make_session()


def _to_timestamp(s: pd.Series) -> pd.Series:
    """Parse Binance transact_time, auto-detecting ms vs µs by magnitude.

    Standard historical aggTrades are milliseconds (~1.7e12); some datasets are
    microseconds (~1.7e15, i.e. 1e3 larger). Since >1e14 ms would be year >5000,
    any value above that is treated as microseconds. This keeps synthetic ms-scale
    tests correct and handles µs-precision downloads.
    """
    unit = "us" if float(s.max()) > 1e14 else "ms"
    return pd.to_datetime(s, unit=unit, utc=True)


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
    url = f"{BASE_URL}/{symbol}/{symbol}-aggTrades-{day:%Y-%m-%d}.zip"
    resp = _SESSION.get(url, timeout=120)
    if resp.status_code == 404:
        raise FileNotFoundError(f"No aggTrade file for {symbol} {day}: {url}")
    resp.raise_for_status()  # Raises HTTPError for persistent 5xx after retries
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            df = pd.read_csv(f, header=None, names=AGG_COLUMNS)
    # BUG-3: dtype coercion before caching
    df["ts_ms"] = pd.to_numeric(df["ts_ms"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["quantity"] = pd.to_numeric(df["quantity"], errors="coerce")
    df = df.dropna(subset=["ts_ms", "price", "quantity"])
    df["price"] = df["price"].astype("float64")
    df["quantity"] = df["quantity"].astype("float64")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(cache_path)
    return cache_path


def load_aggtrades(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Concat cached daily aggTrades for [start, end]. Skips missing/corrupt days."""
    frames = []
    for day in _date_range(start, end):
        try:
            path = download_day(symbol, day)
            try:
                df = pd.read_parquet(path)
            except Exception:
                # BUG-2: corrupt-cache recovery - delete and re-download
                logger.warning("Corrupt cache for %s %s, re-downloading", symbol, day)
                path.unlink(missing_ok=True)
                path = download_day(symbol, day, overwrite=True)
                df = pd.read_parquet(path)
            frames.append(df)
        except FileNotFoundError:
            # BUG-1 part 2: skip 404 days gracefully
            continue
        except requests.RequestException as e:
            # BUG-1 part 2: resilient day-skip for 5xx after retries/timeout
            logger.warning("Failed to download %s %s after retries: %s", symbol, day, e)
            continue
    if not frames:
        return pd.DataFrame(columns=AGG_COLUMNS)
    df = pd.concat(frames, ignore_index=True)
    df["ts"] = _to_timestamp(df["ts_ms"])
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
        t["ts"] = _to_timestamp(t["ts_ms"])
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
    if bars.empty:
        # Don't cache a no-data result, and avoid requiring a parquet engine on
        # the empty path (CI has no pyarrow). Real (non-empty) runs use
        # requirements-sweep.txt which includes pyarrow.
        return bars
    bars.to_parquet(cache_path)
    # Free the raw aggTrades (build input) now that bars (the product) are
    # cached — keeps peak disk low on constrained runners (CI / small hosts),
    # so the full multi-pair range fits without filling the disk.
    shutil.rmtree(RAW_DIR / symbol, ignore_errors=True)
    return bars
