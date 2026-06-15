#!/usr/bin/env python3
"""Bulk-download 5m klines from data.binance.vision (monthly zips, no auth/rate-limit)
for the swing-bot backtest. Outputs one CSV per pair: ts_ms,open,high,low,close,volume.

Same Jan 2025–May 2026 window the other bots were backtested on, so results are
directly comparable. Timestamps come down in microseconds -> divided to ms.
"""
import csv
import io
import time
import urllib.request
import zipfile
from datetime import date
from pathlib import Path

CACHE_DIR = Path(__file__).resolve().parent / "data_cache"
BASE_URL = "https://data.binance.vision/data/spot/monthly/klines"
PAIRS = ["BNBUSDT", "ETHUSDT", "DOGEUSDT", "XRPUSDT"]
START = date(2025, 1, 1)
END = date(2026, 5, 31)


def _months(start: date, end: date):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m > 12:
            m = 1; y += 1


def _get(url: str, retries: int = 5) -> bytes:
    """GET with simple backoff for transient 429/5xx. Returns bytes; raises HTTPError otherwise."""
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "swing-backtest-fetch/1.0"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
            time.sleep(0.5 * (attempt + 1))
        except urllib.error.URLError as e:
            last = e
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} retries: {url} ({last})")


def fetch_pair(symbol: str) -> list[list]:
    rows: list[list] = []
    for y, m in _months(START, END):
        tag = f"{y}-{m:02d}"
        url = f"{BASE_URL}/{symbol}/5m/{symbol}-5m-{tag}.zip"
        try:
            data = _get(url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print(f"  {symbol} {tag}: 404 (future month / not published) — skipping")
                continue
            raise
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            name = z.namelist()[0]
            with z.open(name) as f:
                lines = f.read().decode().splitlines()
        # columns: open_time(us),o,h,l,c,vol,close_time,quote_vol,trades,...
        added = 0
        for ln in lines:
            if not ln.strip():
                continue
            p = ln.split(",")
            rows.append([
                int(p[0]) // 1000,   # open_time us -> ms
                float(p[1]), float(p[2]), float(p[3]), float(p[4]),
                float(p[5]),
            ])
            added += 1
        print(f"  {symbol} {tag}: +{added} bars")
    rows.sort(key=lambda r: r[0])
    return rows


def main() -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    span = f"{START.isoformat()}_{END.isoformat()}"
    for sym in PAIRS:
        print(f"== {sym} ==")
        rows = fetch_pair(sym)
        out = CACHE_DIR / f"{sym}_5m_{span}.csv"
        with out.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts_ms", "open", "high", "low", "close", "volume"])
            w.writerows(rows)
        print(f"  -> {out} ({len(rows)} bars)")


if __name__ == "__main__":
    main()

