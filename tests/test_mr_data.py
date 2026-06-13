# tests/test_mr_data.py
import numpy as np
import pandas as pd
from datetime import date
from unittest.mock import Mock, patch
import pytest

from backtest.mean_reversion.data import resample_bars, load_bars


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


def test_to_timestamp_detects_ms_vs_us():
    # Same instants expressed as milliseconds and as microseconds must parse
    # to the same timestamp (regression for the µs-precision dataset bug).
    from backtest.mean_reversion.data import _to_timestamp
    ms = pd.Series([1749000000000, 1749000001000])   # ms ~ 2025
    us = ms * 1000                                    # µs, same instants
    assert _to_timestamp(ms).iloc[0] == _to_timestamp(us).iloc[0]


def test_download_day_raises_file_not_found_on_404():
    """BUG-1: download_day raises FileNotFoundError on 404."""
    from backtest.mean_reversion.data import download_day
    import backtest.mean_reversion.data as data_module
    from pathlib import Path
    import tempfile

    # Mock session to return 404
    mock_response = Mock()
    mock_response.status_code = 404
    mock_session = Mock()
    mock_session.get.return_value = mock_response

    # Use a temp directory for RAW_DIR
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(data_module, "_SESSION", mock_session):
            with patch.object(data_module, "RAW_DIR", Path(tmpdir)):
                with pytest.raises(FileNotFoundError, match="No aggTrade file"):
                    download_day("BTCUSDT", date(2024, 1, 1))


def test_load_bars_zero_day_range_404_returns_empty():
    """TEST-2: load_bars over zero-day range (404) returns empty DataFrame."""
    import backtest.mean_reversion.data as data_module
    from pathlib import Path
    import tempfile

    # Mock session to return 404
    mock_response = Mock()
    mock_response.status_code = 404
    mock_session = Mock()
    mock_session.get.return_value = mock_response

    # Use a temp directory for BARS_DIR to avoid cache hits
    with tempfile.TemporaryDirectory() as tmpdir:
        with patch.object(data_module, "_SESSION", mock_session):
            with patch.object(data_module, "BARS_DIR", Path(tmpdir)):
                result = load_bars("BTCUSDT", date(2024, 1, 1), date(2024, 1, 1))
    assert isinstance(result, pd.DataFrame)
    assert result.empty
