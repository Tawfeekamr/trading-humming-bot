"""Historical kline download + on-disk cache for the scout backtest.

Source: data.binance.vision monthly archives for Binance USDT-M perpetuals.
The archive keeps delisted symbols, which is what lets the backtest rebuild
the universe as it was on each past day (no survivorship bias).

Cache layout: backtest/data_cache/scout/<interval>/<SYMBOL>.parquet
Columns: open_time (UTC, tz-aware), open, high, low, close, volume, quote_volume.

CLI:
    python3 -m src.scout.data daily            # 1d bars for every USDT perp
    python3 -m src.scout.data hourly           # 1h bars for symbols that ever made the universe
    python3 -m src.scout.data daily-early      # v2: 1d 2019-09 → 2023-06 (separate cache)
    python3 -m src.scout.data hourly-early     # v2: 1h 2020-05 → 2023-06
"""
from __future__ import annotations

import io
import logging
import re
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd
import requests

logger = logging.getLogger(__name__)

S3_LIST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
DL_BASE = "https://data.binance.vision"
KLINE_PREFIX = "data/futures/um/monthly/klines/"
CACHE_ROOT = Path("backtest/data_cache/scout")
EARLY_CACHE_ROOT = Path("backtest/data_cache/scout_early")   # v2 pre-registration: 2019-09 → 2023-06

# Not real trend instruments: stablecoins, fiat, index contracts.
STABLE_BASES = {"USDC", "BUSD", "TUSD", "FDUSD", "USDP", "DAI", "EUR", "GBP",
                "AEUR", "USDE", "PYUSD", "UST", "USTC", "SUSD", "XUSD", "USD1"}
INDEX_SYMBOLS = {"BTCDOMUSDT", "DEFIUSDT", "FOOTBALLUSDT", "BLUEBIRDUSDT"}

KLINE_COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time",
              "quote_volume", "count", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
KEEP_COLS = ["open_time", "open", "high", "low", "close", "volume", "quote_volume"]

_session = requests.Session()
_session.headers["User-Agent"] = "scout-backtest/1.0"


def _get(url: str, params: Optional[dict] = None, retries: int = 4) -> requests.Response:
    for attempt in range(retries):
        try:
            r = _session.get(url, params=params, timeout=30)
            if r.status_code in (200, 404):
                return r
        except requests.RequestException as e:  # pragma: no cover - network
            logger.debug("GET %s failed: %s", url, e)
        time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} tries: {url}")


def _s3_list(prefix: str, delimiter: Optional[str]) -> list[str]:
    """List S3 keys (delimiter=None) or common prefixes (delimiter='/')."""
    out, marker = [], ""
    while True:
        params = {"prefix": prefix, "marker": marker}
        if delimiter:
            params["delimiter"] = delimiter
        xml = _get(S3_LIST, params).text
        tag = "Prefix" if delimiter else "Key"
        found = re.findall(rf"<{tag}>([^<]+)</{tag}>", xml)
        if delimiter:
            found = [f for f in found if f != prefix]
        out.extend(found)
        if "<IsTruncated>true</IsTruncated>" not in xml:
            return out
        nm = re.search(r"<NextMarker>([^<]+)</NextMarker>", xml)
        marker = nm.group(1) if nm else found[-1]


def is_tradeable_symbol(sym: str) -> bool:
    """USDT-quoted perpetual, not a dated contract, index or stablecoin pair."""
    if not sym.endswith("USDT") or "_" in sym or sym in INDEX_SYMBOLS:
        return False
    base = sym[: -len("USDT")]
    return bool(base) and base not in STABLE_BASES


def list_symbols() -> list[str]:
    prefixes = _s3_list(KLINE_PREFIX, "/")
    syms = [p[len(KLINE_PREFIX):].strip("/") for p in prefixes]
    return sorted(s for s in syms if is_tradeable_symbol(s))


def parse_kline_csv(raw: bytes) -> pd.DataFrame:
    """Parse one archive CSV. Handles optional header and ms/µs timestamps."""
    text = raw.decode()
    first = text.split("\n", 1)[0]
    has_header = not first.split(",")[0].strip().lstrip("-").isdigit()
    df = pd.read_csv(io.StringIO(text), header=0 if has_header else None)
    df = df.iloc[:, :12]
    df.columns = KLINE_COLS
    df = df[KEEP_COLS].copy()
    ot = df["open_time"].astype("int64")
    unit = "us" if ot.max() > 10**14 else "ms"   # archives switched to µs in 2025
    df["open_time"] = pd.to_datetime(ot, unit=unit, utc=True)
    for c in KEEP_COLS[1:]:
        df[c] = df[c].astype("float64")
    return df


def _month_in_range(key: str, start: str, end: str) -> bool:
    m = re.search(r"-(\d{4}-\d{2})\.zip$", key)
    return bool(m) and start <= m.group(1) <= end


def _download_zip(key: str) -> Optional[pd.DataFrame]:
    r = _get(f"{DL_BASE}/{key}")
    if r.status_code == 404:
        return None
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        return parse_kline_csv(zf.read(zf.namelist()[0]))


def fetch_symbol(sym: str, interval: str, start_month: str, end_month: str,
                 cache_root: Path = CACHE_ROOT) -> Optional[Path]:
    """Download all monthly archives for one symbol into one parquet file."""
    out = cache_root / interval / f"{sym}.parquet"
    if out.exists():
        return out
    keys = [k for k in _s3_list(f"{KLINE_PREFIX}{sym}/{interval}/", None)
            if k.endswith(".zip") and _month_in_range(k, start_month, end_month)]
    if not keys:
        return None
    frames = [f for f in (_download_zip(k) for k in sorted(keys)) if f is not None and len(f)]
    if not frames:
        return None
    df = (pd.concat(frames).drop_duplicates("open_time")
          .sort_values("open_time").reset_index(drop=True))
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    return out


def fetch_many(symbols: Iterable[str], interval: str, start_month: str, end_month: str,
               workers: int = 12, cache_root: Path = CACHE_ROOT) -> dict[str, Optional[Path]]:
    symbols = list(symbols)
    done: dict[str, Optional[Path]] = {}
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(fetch_symbol, s, interval, start_month, end_month, cache_root): s for s in symbols}
        for i, f in enumerate(as_completed(futs), 1):
            s = futs[f]
            try:
                done[s] = f.result()
            except Exception as e:  # pragma: no cover - network
                logger.warning("fetch %s %s failed: %s", s, interval, e)
                done[s] = None
            if i % 25 == 0 or i == len(symbols):
                logger.info("%s: %d/%d symbols", interval, i, len(symbols))
    return done


def load_bars(sym: str, interval: str, cache_root: Path = CACHE_ROOT) -> pd.DataFrame:
    df = pd.read_parquet(cache_root / interval / f"{sym}.parquet")
    return df.set_index("open_time")


def cached_symbols(interval: str, cache_root: Path = CACHE_ROOT) -> list[str]:
    d = cache_root / interval
    return sorted(p.stem for p in d.glob("*.parquet")) if d.exists() else []


def main(argv: list[str]) -> int:  # pragma: no cover - CLI
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    mode = argv[1] if len(argv) > 1 else "daily"
    if mode == "daily":
        syms = list_symbols()
        logger.info("%d tradeable USDT perps in archive", len(syms))
        fetch_many(syms, "1d", "2022-06", "2026-09")
    elif mode == "hourly":
        from .universe import ever_in_universe
        syms = sorted(ever_in_universe("2023-01-01", "2026-09-30") | {"BTCUSDT"})
        logger.info("%d symbols ever in universe → fetching 1h", len(syms))
        fetch_many(syms, "1h", "2022-11", "2026-09")
    elif mode == "daily-early":
        syms = list_symbols()
        fetch_many(syms, "1d", "2019-09", "2023-06", cache_root=EARLY_CACHE_ROOT)
    elif mode == "hourly-early":
        from .universe import ever_in_universe
        syms = sorted(ever_in_universe("2020-07-01", "2023-06-30", EARLY_CACHE_ROOT) | {"BTCUSDT"})
        logger.info("%d symbols ever in early universe → fetching 1h", len(syms))
        fetch_many(syms, "1h", "2020-05", "2023-06", cache_root=EARLY_CACHE_ROOT)
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
