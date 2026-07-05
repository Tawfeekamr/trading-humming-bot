# src/rl/data.py
"""1h kline loader + fetcher for the RL training pipeline.

Two entry points:

- ``load_klines(pair, start, end)`` — reads the existing Rust-backtest daily
  kline cache at ``backtest/data_cache/klines/{PAIR}/1h/`` and fills any gap
  by downloading daily zips from ``data.binance.vision``. This is the primary
  loader used by the RL env: the cache already covers ~1y of 1h bars.

- ``fetch_monthly_klines(pair, start, end)`` — bulk-downloads monthly 1h zips
  from ``data.binance.vision``. Used when we want to extend history beyond the
  daily cache (2-3 years for the spec's training horizon).

Binance kline CSV format (no header, 12 columns):
    open_time, open, high, low, close, volume,
    close_time, quote_vol, count, taker_buy_vol, taker_buy_quote_vol, ignore

``open_time`` magnitude varies across Binance datasets: standard historical
klines are microseconds (~1.7e15) on data.binance.vision, but some downstream
consumers emit milliseconds. We auto-detect by magnitude (the existing Rust
backtest cache is microseconds).
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3.util.retry import Retry
except ImportError:  # pragma: no cover - very old urllib3 layout
    from requests.packages.urllib3.util.retry import Retry  # type: ignore

logger = logging.getLogger(__name__)

# Reuse the Rust backtest daily cache so we don't duplicate ~360 files.
_CACHE_ROOT = (
    Path(__file__).resolve().parents[2] / "backtest" / "data_cache" / "klines"
)
_MONTHLY_CACHE_DIR = _CACHE_ROOT / "_monthly"

_DAILY_BASE_URL = "https://data.binance.vision/data/spot/daily/klines"
_MONTHLY_BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"

KLINE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_vol",
    "count",
    "taker_buy_vol",
    "taker_buy_quote_vol",
    "ignore",
]
OHLCV_COLS = ["open", "high", "low", "close", "volume"]


def _make_session() -> requests.Session:
    """HTTPS session with retry/backoff for transient 429/5xx (cribbed from
    backtest/mean_reversion/data.py). 404 is raised immediately by the adapter
    so callers can skip-not-retry missing days/months."""
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
    """Parse Binance open_time, auto-detecting ms vs µs by magnitude.

    data.binance.vision historical klines are microseconds (~1.7e15); some
    emitters emit milliseconds (~1.7e12). >1e14 ms would be year >5000, so any
    value above that is treated as microseconds.
    """
    unit = "us" if float(s.max()) > 1e14 else "ms"
    return pd.to_datetime(s, unit=unit, utc=True)


def _date_range(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1
            y += 1


def _daily_cache_path(pair: str, day: date) -> Path:
    return _CACHE_ROOT / pair / "1h" / f"{pair}-1h-{day.isoformat()}.csv"


def _read_klines_csv(path: Path) -> pd.DataFrame:
    """Read one Binance-format klines CSV (no header, 12 cols) and return a
    DataFrame indexed by datetime with OHLCV columns."""
    df = pd.read_csv(path, header=None, names=KLINE_COLUMNS)
    df["ts"] = _to_timestamp(df["open_time"])
    df = df.set_index("ts")[OHLCV_COLS]
    # Coerce numeric (defensive: stray strings become NaN, dropped downstream).
    for c in OHLCV_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def _parse_klines_csv_bytes(raw: bytes) -> tuple[pd.DataFrame, bytes]:
    """Parse raw Binance klines CSV text into an OHLCV-indexed DataFrame.
    Returns (df, raw_bytes_stripped) so callers can persist the exact bytes
    we parsed in the original 12-column format."""
    df = pd.read_csv(io.BytesIO(raw), header=None, names=KLINE_COLUMNS)
    df["ts"] = _to_timestamp(df["open_time"])
    df = df.set_index("ts")[OHLCV_COLS]
    for c in OHLCV_COLS:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df, raw


def _download_daily_zip(pair: str, day: date) -> tuple[pd.DataFrame, bytes]:
    """Download one day's 1h kline zip from data.binance.vision.

    Returns ``(df, raw_csv_bytes)`` so the caller can cache the bytes verbatim
    in the original 12-column Binance format (keeps ``_read_klines_csv`` simple
    and avoids an index→open_time round-trip on re-read).

    Raises ``FileNotFoundError`` on 404 (clean signal for the day-skip path).
    """
    url = f"{_DAILY_BASE_URL}/{pair}/1h/{pair}-1h-{day.isoformat()}.zip"
    resp = _SESSION.get(url, timeout=120)
    if resp.status_code == 404:
        raise FileNotFoundError(f"No daily kline file for {pair} {day}: {url}")
    resp.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
        name = z.namelist()[0]
        with z.open(name) as f:
            raw = f.read()
    return _parse_klines_csv_bytes(raw)


def load_klines(pair: str, start: date, end: date) -> pd.DataFrame:
    """Load 1h klines from the daily CSV cache, downloading missing days from
    data.binance.vision. Returns an OHLCV DataFrame indexed by UTC datetime,
    sorted and de-duplicated on the index. Missing days (404) are skipped with
    a warning rather than raising."""
    pair_dir = _CACHE_ROOT / pair / "1h"
    pair_dir.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    for day in _date_range(start, end):
        cache_path = _daily_cache_path(pair, day)
        if cache_path.exists():
            try:
                frames.append(_read_klines_csv(cache_path))
                continue
            except Exception as e:
                logger.warning(
                    "Corrupt kline cache %s (%s); re-downloading",
                    cache_path,
                    e,
                )
                cache_path.unlink(missing_ok=True)

        # Not cached (or was corrupt) -> download daily zip. Cache the raw CSV
        # bytes verbatim (Binance 12-col format) so the next load is local-only
        # and re-reads identically to a Rust-backtest-cached day.
        try:
            df, raw = _download_daily_zip(pair, day)
        except FileNotFoundError:
            logger.warning(
                "No kline data for %s %s (404) — skipping", pair, day
            )
            continue
        except requests.RequestException as e:
            logger.warning(
                "Failed to download %s %s after retries: %s", pair, day, e
            )
            continue

        cache_path.write_bytes(raw)
        frames.append(df)

    if not frames:
        logger.warning(
            "load_klines(%s, %s, %s): no data found", pair, start, end
        )
        return pd.DataFrame(columns=OHLCV_COLS)

    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()
    return df


def fetch_monthly_klines(pair: str, start: date, end: date) -> pd.DataFrame:
    """Bulk-download monthly 1h klines from data.binance.vision.

    Used to extend coverage beyond the daily cache (2-3 years for the spec's
    training horizon). Each pair-month is cached as a parquet file under
    ``backtest/data_cache/klines/_monthly/{PAIR}/{PAIR}-1h-{YYYY-MM}.parquet``
    so repeated runs are local-only. Returns an OHLCV DataFrame indexed by UTC
    datetime, sorted and de-duplicated on the index. Missing months (404 —
    typically the current partial month or future months) are skipped.
    """
    cache_dir = _MONTHLY_CACHE_DIR / pair
    cache_dir.mkdir(parents=True, exist_ok=True)

    frames: list[pd.DataFrame] = []
    for y, m in _months(start, end):
        tag = f"{y}-{m:02d}"
        cache_path = cache_dir / f"{pair}-1h-{tag}.parquet"
        if cache_path.exists():
            try:
                frames.append(pd.read_parquet(cache_path))
                continue
            except Exception as e:
                logger.warning(
                    "Corrupt monthly cache %s (%s); re-downloading",
                    cache_path,
                    e,
                )
                cache_path.unlink(missing_ok=True)

        url = f"{_MONTHLY_BASE_URL}/{pair}/1h/{pair}-1h-{tag}.zip"
        try:
            resp = _SESSION.get(url, timeout=120)
            if resp.status_code == 404:
                logger.info(
                    "Monthly klines %s %s: 404 (future/partial) — skipping",
                    pair,
                    tag,
                )
                continue
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning(
                "Failed to download %s %s after retries: %s", pair, tag, e
            )
            continue

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            name = z.namelist()[0]
            with z.open(name) as f:
                raw = f.read()
        df, _ = _parse_klines_csv_bytes(raw)
        df.to_parquet(cache_path)
        logger.info("Monthly klines %s %s: +%d bars", pair, tag, len(df))
        frames.append(df)

    if not frames:
        logger.warning(
            "fetch_monthly_klines(%s, %s, %s): no data found", pair, start, end
        )
        return pd.DataFrame(columns=OHLCV_COLS)

    df = pd.concat(frames)
    df = df[~df.index.duplicated(keep="first")]
    df = df.sort_index()
    return df
