"""Scout Bot core: indicators, features (no lookahead), universe, market state,
setups, config strictness, archive parsing."""
from __future__ import annotations

import io
import zipfile

import numpy as np
import pandas as pd
import pytest

from src.scout import indicators as ind
from src.scout import market_state as ms
from src.scout.config import BreakoutParams, PortfolioParams, PullbackParams, strict_from_dict
from src.scout.data import is_tradeable_symbol, parse_kline_csv
from src.scout.features import hourly_features
from src.scout.setups import detect_breakout, detect_pullback
from src.scout.universe import build_daily_universe


def random_bars(n=1200, seed=0, start="2024-01-01"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="h", tz="UTC")
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    open_ = np.concatenate([[100.0], close[:-1]])
    high = np.maximum(open_, close) * (1 + rng.uniform(0, 0.005, n))
    low = np.minimum(open_, close) * (1 - rng.uniform(0, 0.005, n))
    vol = rng.uniform(100, 200, n)
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "volume": vol}, index=idx)


# ── indicators ──────────────────────────────────────────────────────────
def test_rsi_all_up_is_100_and_all_down_is_0():
    up = pd.Series(np.arange(1.0, 40.0))
    assert ind.rsi(up, 14).iloc[-1] == pytest.approx(100.0)
    assert ind.rsi(up[::-1].reset_index(drop=True), 14).iloc[-1] == pytest.approx(0.0)


def test_atr_constant_range():
    n = 50
    c = pd.Series(np.full(n, 100.0))
    assert ind.atr(c + 1, c - 1, c, 14).iloc[-1] == pytest.approx(2.0)


def test_donchian_excludes_current_bar():
    h = pd.Series([1.0] * 20 + [5.0])
    hi, _ = ind.donchian_prev(h, h, 20)
    assert hi.iloc[-1] == 1.0     # the 5.0 bar itself is not part of its own channel


def test_rvol_uses_previous_bars():
    v = pd.Series([10.0] * 20 + [30.0])
    assert ind.rvol(v, 20).iloc[-1] == pytest.approx(3.0)


def test_adx_strong_trend_high():
    n = 200
    c = pd.Series(np.linspace(100, 300, n))
    assert ind.adx(c + 0.5, c - 0.5, c, 14).iloc[-1] > 50


# ── features ────────────────────────────────────────────────────────────
def test_features_have_no_lookahead():
    bars = random_bars()
    full = hourly_features(bars)
    cut = 1000
    tampered = bars.copy()
    tampered.iloc[cut + 1:, :] *= 3.0           # change only the future
    f2 = hourly_features(tampered)
    a, b = full.iloc[: cut + 1], f2.iloc[: cut + 1]
    pd.testing.assert_frame_equal(a, b)


def test_4h_values_change_only_at_4h_close_hours():
    f = hourly_features(random_bars())
    closes = f.index[f["is_4h_close"]]
    assert set(closes.hour % 4) == {3}
    changed = f["close4h"].ne(f["close4h"].shift()) & f["close4h"].notna() & f["close4h"].shift().notna()
    assert set(f.index[changed].hour % 4) <= {3}
    # at a 4h close row, close4h equals that hour's 1h close
    assert np.allclose(f.loc[closes, "close4h"].dropna(), f.loc[closes, "close"][f.loc[closes, "close4h"].notna()])


def test_ret24_handles_gaps():
    bars = random_bars(200)
    gap = bars.index[100]
    bars = bars.drop(gap)                        # missing hour
    f = hourly_features(bars)
    t = bars.index[130]
    expected = bars.loc[t, "close"] / bars.loc[t - pd.Timedelta(hours=24), "close"] - 1
    assert f.loc[t, "ret24"] == pytest.approx(expected)
    assert np.isnan(f.loc[gap + pd.Timedelta(hours=24), "ret24"])


# ── universe ────────────────────────────────────────────────────────────
def _daily(vol, start="2024-01-01"):
    idx = pd.date_range(start, periods=len(vol), freq="D", tz="UTC")
    return pd.DataFrame({"close": 1.0, "quote_volume": vol}, index=idx)


def test_universe_needs_age_volume_and_uses_previous_day():
    big = _daily([50e6] * 40)
    small = _daily([5e6] * 40)
    late_spike = _daily([1e6] * 35 + [1e12] * 5)
    U = build_daily_universe({"AAAUSDT": big, "BBBUSDT": small, "CCCUSDT": late_spike})
    assert not U["AAAUSDT"].iloc[29]      # only 29 days of history before day 30
    assert U["AAAUSDT"].iloc[30]
    assert not U["BBBUSDT"].any()
    # the spike on day 35 cannot qualify day 35 itself (uses data to D-1)
    spike_day = late_spike.index[35]
    assert not U.loc[spike_day, "CCCUSDT"]


def test_universe_top_n_cut():
    d = {f"C{i}USDT": _daily([(i + 1) * 30e6] * 40) for i in range(5)}
    U = build_daily_universe(d, top_n=2)
    last = U.iloc[-1]
    assert set(last[last].index) == {"C4USDT", "C3USDT"}


def test_symbol_filter():
    assert is_tradeable_symbol("SOLUSDT")
    for s in ("USDCUSDT", "BTCUSDT_250328", "BTCDOMUSDT", "ETHBUSD", "FDUSDUSDT"):
        assert not is_tradeable_symbol(s)


# ── market state ────────────────────────────────────────────────────────
def test_market_state_rules():
    assert ms.classify(0.6, 1, 0.01) == ms.RISK_ON
    assert ms.classify(0.4, 0, 0.01) == ms.RISK_OFF
    assert ms.classify(0.5, 1, 0.01) == ms.NEUTRAL
    assert ms.classify(0.6, 0, 0.01) == ms.NEUTRAL
    assert ms.classify(0.9, 1, 0.001) == ms.NO_TRADE
    assert ms.classify(float("nan"), 1, 0.01) == ms.NO_TRADE
    arr = ms.classify(np.array([0.6, 0.4]), np.array([1.0, 0.0]), np.array([0.01, 0.01]))
    assert list(arr) == [ms.RISK_ON, ms.RISK_OFF]


def test_market_state_direction_and_size():
    assert ms.allows(ms.RISK_ON, 1) and not ms.allows(ms.RISK_ON, -1)
    assert ms.allows(ms.RISK_OFF, -1) and not ms.allows(ms.RISK_OFF, 1)
    assert ms.allows(ms.NEUTRAL, 1) and ms.allows(ms.NEUTRAL, -1)
    assert not ms.allows(ms.NO_TRADE, 1) and not ms.allows(ms.NO_TRADE, -1)
    assert ms.size_mult(ms.NEUTRAL) == 0.5 and ms.size_mult(ms.NO_TRADE) == 0.0


# ── setups ──────────────────────────────────────────────────────────────
def _brk_row(**kw):
    row = dict(is_4h_close=True, rank=0.9, close=110.0, close4h=110.0, dc_high4h=108.0, dc_low4h=90.0,
               rvol4h=2.5, adx4h=25.0, adx4h_prev=24.0, atr4h=2.0, atr1h=1.0, state=ms.RISK_ON)
    row.update(kw)
    return pd.DataFrame([row], index=pd.DatetimeIndex([pd.Timestamp("2024-01-01 03:00", tz="UTC")]))


def test_breakout_long_levels():
    out = detect_breakout(_brk_row())
    assert len(out) == 1
    r = out.iloc[0]
    assert r.side == 1
    assert r.entry_low == 108.0 and r.entry_high == pytest.approx(109.0)
    assert r.stop == pytest.approx(108.0 - 1.5 * 2.0)
    risk = 110.0 - r.stop
    assert (r.tp1, r.tp2, r.tp3) == pytest.approx((110 + risk, 110 + 2 * risk, 110 + 3 * risk))
    assert r.score == pytest.approx(90.0)


@pytest.mark.parametrize("kw", [dict(rank=0.7), dict(rvol4h=1.5), dict(adx4h=19.0),
                                dict(adx4h_prev=26.0), dict(is_4h_close=False),
                                dict(state=ms.RISK_OFF), dict(close4h=107.0)])
def test_breakout_rejects(kw):
    assert detect_breakout(_brk_row(**kw)).empty


def test_breakout_short_mirror():
    out = detect_breakout(_brk_row(rank=0.1, close=89.0, close4h=89.0, state=ms.RISK_OFF))
    r = out.iloc[0]
    assert r.side == -1 and r.stop == pytest.approx(90.0 + 3.0) and r.entry_high == 90.0


def test_breakout_stop_distance_bounds():
    # stop 15% away → rejected by max_stop_pct
    assert detect_breakout(_brk_row(atr4h=20.0, rvol4h=3.0)).empty


def _pb_row(**kw):
    row = dict(rank=0.8, close=101.0, ema20_4h=100.0, ema50_4h=98.0, ema200_4h=95.0, atr4h=2.0,
               atr1h=0.8, low_min3=100.2, high_max3=102.0, rsi1h_min3_prior=45.0, rsi1h_max3_prior=55.0,
               rsi1h=52.0, rsi1h_prev=47.0, high_prev=100.8, low_prev=99.0, low_min6=99.5,
               high_max6=102.0, state=ms.RISK_ON)
    row.update(kw)
    return pd.DataFrame([row], index=pd.DatetimeIndex([pd.Timestamp("2024-01-01 05:00", tz="UTC")]))


def test_pullback_long_and_min_stop():
    r = detect_pullback(_pb_row()).iloc[0]
    assert r.side == 1
    # structure stop 99.5-0.08=99.42; min-ATR stop 101-0.8=100.2 → the lower (wider) one wins
    assert r.stop == pytest.approx(99.42)
    tight = detect_pullback(_pb_row(low_min6=100.9)).iloc[0]
    assert tight.stop == pytest.approx(101.0 - 0.8)


@pytest.mark.parametrize("kw", [dict(rank=0.6), dict(ema50_4h=101.0), dict(low_min3=101.0),
                                dict(rsi1h_min3_prior=35.0), dict(rsi1h=46.0), dict(high_prev=101.5)])
def test_pullback_rejects(kw):
    assert detect_pullback(_pb_row(**kw)).empty


def test_pullback_grid_param_changes_result():
    assert detect_pullback(_pb_row(rank=0.65), PullbackParams(rank_min=0.6)).shape[0] == 1


# ── config / data ──────────────────────────────────────────────────────
def test_strict_config_rejects_unknown_keys():
    assert strict_from_dict(BreakoutParams, {"rvol_min": 1.5}).rvol_min == 1.5
    with pytest.raises(ValueError, match="unknown"):
        strict_from_dict(PortfolioParams, {"max_positons": 3})


def test_parse_kline_csv_header_and_units():
    ms_row = "1704067200000,1,2,0.5,1.5,10,1704070799999,15,3,5,7,0\n"
    us_row = "1704067200000000,1,2,0.5,1.5,10,1704070799999999,15,3,5,7,0\n"
    header = "open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore\n"
    for raw in (ms_row, header + ms_row, us_row):
        df = parse_kline_csv(raw.encode())
        assert df["open_time"].iloc[0] == pd.Timestamp("2024-01-01", tz="UTC")
        assert df["quote_volume"].iloc[0] == 15.0
