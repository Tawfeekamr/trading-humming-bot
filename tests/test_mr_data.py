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
