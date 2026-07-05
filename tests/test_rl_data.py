# tests/test_rl_data.py
"""Unit tests for src/rl/data.py — the RL pipeline's 1h kline loader.

These tests deliberately exercise the *existing* ETHUSDT cache (no network):
the basic load test confirms the daily CSV format is parsed correctly and the
datetime index is sound. The 404 / dedup paths use mocked sessions so they
run offline too.
"""
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import pytest

from src.rl.data import (
    OHLCV_COLS,
    fetch_monthly_klines,
    load_klines,
)


# Path to the real ETHUSDT 1h cache (populated by the Rust backtest fetcher).
_ETH_CACHE_DIR = (
    Path(__file__).resolve().parent.parent
    / "backtest" / "data_cache" / "klines" / "ETHUSDT" / "1h"
)


def _eth_cache_days() -> list[date]:
    """Days actually present in the ETHUSDT cache (sorted)."""
    if not _ETH_CACHE_DIR.exists():
        return []
    out = []
    for p in _ETH_CACHE_DIR.glob("ETHUSDT-1h-*.csv"):
        stem = p.stem  # ETHUSDT-1h-2026-06-01
        try:
            out.append(date.fromisoformat(stem.split("-1h-")[1]))
        except (IndexError, ValueError):
            continue
    return sorted(out)


_ETH_DAYS = _eth_cache_days()


@pytest.mark.skipif(not _ETH_DAYS, reason="ETHUSDT 1h cache not populated")
def test_load_klines_real_cache_shape_and_index():
    """Load 3 contiguous cached days and verify shape, columns, sorted index."""
    # Find 3 contiguous days actually in the cache.
    run = None
    for i in range(len(_ETH_DAYS) - 2):
        a, b, c = _ETH_DAYS[i], _ETH_DAYS[i + 1], _ETH_DAYS[i + 2]
        if (b - a).days == 1 and (c - b).days == 1:
            run = (a, c)
            break
    assert run is not None, "cache must contain 3 contiguous days for this test"
    start, end = run

    df = load_klines("ETHUSDT", start, end)

    assert list(df.columns) == OHLCV_COLS
    assert isinstance(df.index, pd.DatetimeIndex)
    assert df.index.is_monotonic_increasing
    # 3 days of 1h bars = 72 rows (allow fewer only if Binance published gaps).
    assert len(df) == 72, f"expected 72 1h bars over 3 days, got {len(df)}"
    # No NaN prices.
    assert not df[["open", "high", "low", "close"]].isna().any().any()


@pytest.mark.skipif(not _ETH_DAYS, reason="ETHUSDT 1h cache not populated")
def test_load_klines_dedup_same_ts_once():
    """A timestamp appearing more than once (e.g. concat boundary) must
    collapse to a single row under the same index."""
    start, end = _ETH_DAYS[0], _ETH_DAYS[0]
    df = load_klines("ETHUSDT", start, end)
    assert df.index.is_unique, "datetime index must be de-duplicated"
    assert len(df) == len(df.index.unique())


def test_load_klines_skips_missing_days_gracefully():
    """A 404 day in the middle of the range must be skipped, not raise."""
    import src.rl.data as mod

    # Mock session: 2099-01-02 returns 404; the surrounding days serve
    # synthetic in-memory zips with day-distinct timestamps (so dedup keeps
    # all rows).
    def fake_get(url, timeout=None):
        resp = Mock()
        if "ETHUSDT-1h-2099-01-02" in url:
            resp.status_code = 404  # the missing day
            return resp
        # Pick the day from the URL so each day's bars have distinct ts.
        for d in ("2099-01-01", "2099-01-03"):
            if d in url:
                # Midnight + 1am on that day (µs).
                day_unix_s = int(date.fromisoformat(d).strftime("%s"))
                t0 = day_unix_s * 1_000_000
                t1 = t0 + 3600 * 1_000_000
                rows = [
                    f"{t0},1,2,3,4,5,{t0 + 3599_999_999},0,1,0,0,0",
                    f"{t1},1,2,3,4,5,{t1 + 3599_999_999},0,1,0,0,0",
                ]
                csv_bytes = ("\n".join(rows) + "\n").encode()
                resp.status_code = 200
                resp.content = io_zip_bytes(
                    csv_bytes, f"ETHUSDT-1h-{d}.csv")
                return resp
        raise AssertionError(f"unexpected url: {url}")

    start = date(2099, 1, 1)
    end = date(2099, 1, 3)
    with tempfile_cache(mod):
        with patch.object(mod, "_SESSION", Mock(get=Mock(side_effect=fake_get))):
            df = load_klines("ETHUSDT", start, end)

    # Missing day skipped, no raise; the two surrounding days give 4 rows
    # (2 bars/day, all distinct timestamps).
    assert len(df) == 4
    assert df.index.is_monotonic_increasing
    assert df.index.is_unique


def test_load_klines_all_missing_returns_empty():
    """Range with no cached days and all-404 downloads returns empty frame
    with the correct OHLCV columns (not a crash)."""
    import src.rl.data as mod

    resp = Mock(status_code=404)
    with tempfile_cache(mod):
        with patch.object(mod, "_SESSION", Mock(get=Mock(return_value=resp))):
            df = load_klines("NOPEUSDT", date(2099, 1, 1), date(2099, 1, 2))
    assert isinstance(df, pd.DataFrame)
    assert df.empty
    assert list(df.columns) == OHLCV_COLS


def test_fetch_monthly_klines_parses_zip_and_caches():
    """A monthly zip is downloaded, parsed into OHLCV, and cached as parquet
    so the second call is local-only."""
    import src.rl.data as mod

    csv_bytes = (
        "1780272000000000,10,11,9,10.5,100,1780275599999999,0,1,0,0,0\n"
        "1780275600000000,10.5,12,10,11,200,1780279199999999,0,1,0,0,0\n"
    ).encode()
    zip_buf = io_zip_bytes(csv_bytes, "ETHUSDT-1h-2099-01.csv")

    resp = Mock(status_code=200, content=zip_buf)

    with tempfile_cache(mod) as tmp:
        with patch.object(mod, "_SESSION", Mock(get=Mock(return_value=resp))):
            df1 = fetch_monthly_klines("ETHUSDT", date(2099, 1, 1),
                                       date(2099, 1, 31))
            # Second call should NOT hit the network (cache hit).
            with patch.object(mod, "_SESSION",
                              Mock(get=Mock(side_effect=AssertionError(
                                  "expected cache hit, no network")))):
                df2 = fetch_monthly_klines("ETHUSDT", date(2099, 1, 1),
                                           date(2099, 1, 31))

    assert list(df1.columns) == OHLCV_COLS
    assert df1.index.is_monotonic_increasing
    assert df1.index.is_unique
    assert len(df1) == 2
    pd.testing.assert_frame_equal(df1, df2)


def test_fetch_monthly_klines_skips_404_month():
    """A future month (404) is skipped, not raised."""
    import src.rl.data as mod

    resp = Mock(status_code=404)
    with tempfile_cache(mod):
        with patch.object(mod, "_SESSION", Mock(get=Mock(return_value=resp))):
            df = fetch_monthly_klines("ETHUSDT", date(2099, 1, 1),
                                      date(2099, 3, 31))
    assert df.empty
    assert list(df.columns) == OHLCV_COLS


# --- helpers -----------------------------------------------------------------

def io_zip_bytes(csv_bytes: bytes, inner_name: str) -> bytes:
    """Wrap a CSV payload as a single-file zip in memory."""
    import io as _io
    import zipfile as _zip
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w", _zip.ZIP_DEFLATED) as z:
        z.writestr(inner_name, csv_bytes)
    return buf.getvalue()


class tempfile_cache:
    """Context manager: redirect CACHE_ROOT (and the monthly dir) to a temp
    directory so tests don't pollute the real cache. The daily cache lookup
    is patched to a non-existent path under the temp dir, forcing the
    missing-day download path for the mocked-session tests."""

    def __init__(self, mod):
        self._mod = mod
        import tempfile
        self._tmp = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmp.name)

    def __enter__(self):
        # Empty cache root so daily lookups always miss.
        self._mod._CACHE_ROOT = self._tmp_path / "kl"
        self._mod._MONTHLY_CACHE_DIR = self._tmp_path / "kl" / "_monthly"
        return self

    def __exit__(self, *exc):
        self._tmp.cleanup()
        return False
