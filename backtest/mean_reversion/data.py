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
