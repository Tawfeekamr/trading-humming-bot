"""v3 funding / OI: timing, features, detector C, OI filter, verdict rules."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.scout.walk_forward import STUDIES, verdict
from src.scout import market_state as ms
from src.scout.config import FundingParams
from src.scout.data import parse_funding_csv
from src.scout.funding import funding_hourly, oi_at, oi_change_24h
from src.scout.setups import detect_funding_fade, oi_filter

UTC = "UTC"


def ts(s):
    return pd.Timestamp(s, tz=UTC)


def test_parse_funding_rounds_jitter_and_normalises_interval():
    raw = b"calc_time,funding_interval_hours,last_funding_rate\n1614556800003,8,0.0008\n1614571200000,4,0.0004\n"
    df = parse_funding_csv(raw)
    assert df["time"].iloc[0] == ts("2021-03-01 00:00:00")
    assert df["f8"].tolist() == pytest.approx([0.0008, 0.0008])   # 4h rate ×2


def test_print_belongs_to_bar_that_closes_at_settlement():
    idx = pd.date_range("2024-01-01", periods=24, freq="h", tz=UTC)
    prints = pd.DataFrame({"time": [ts("2024-01-01 08:00:00"), ts("2024-01-01 16:00:00")], "f8": [0.001, -0.002]})
    f8, isp = funding_hourly(prints, idx)
    assert isp[ts("2024-01-01 07:00")] and not isp[ts("2024-01-01 08:00")]
    assert np.isnan(f8[ts("2024-01-01 06:00")])                    # not known before the print
    assert f8[ts("2024-01-01 07:00")] == 0.001
    assert f8[ts("2024-01-01 14:00")] == 0.001 and f8[ts("2024-01-01 15:00")] == -0.002


def test_funding_ffill_limit():
    idx = pd.date_range("2024-01-01", periods=30, freq="h", tz=UTC)
    f8, _ = funding_hourly(pd.DataFrame({"time": [ts("2024-01-01 01:00")], "f8": [0.001]}), idx)
    assert f8[ts("2024-01-01 08:00")] == 0.001 and np.isnan(f8[ts("2024-01-01 09:00")])


def _metrics(times, ois):
    return pd.DataFrame({"time": pd.to_datetime(times, utc=True), "oi": ois})


def test_oi_at_staleness_and_change():
    m = _metrics(["2024-01-01 00:00", "2024-01-02 00:00", "2024-01-02 00:55"], [100.0, 120.0, 130.0])
    assert oi_at(m, ts("2024-01-02 00:20")) == 120.0
    assert np.isnan(oi_at(m, ts("2024-01-02 00:45")))              # last snapshot 45 min old
    assert oi_change_24h(m, ts("2024-01-02 00:00")) == pytest.approx(0.2)
    assert np.isnan(oi_change_24h(m, ts("2024-01-03 00:00")))


def _row(**kw):
    row = dict(f8=0.001, f_rank=0.95, is_print=True, ret24=0.05, close=100.0, low_prev=100.5, high_prev=102.0,
               high_max6=103.0, low_min6=98.0, atr1h=1.0, rank=0.5, state=ms.RISK_ON)
    row.update(kw)
    return pd.DataFrame([row], index=pd.DatetimeIndex([ts("2024-01-01 07:00")]))


def test_funding_short_fires_against_trend_state():
    out = detect_funding_fade(_row())
    r = out.iloc[0]
    assert r.side == -1                                            # short allowed in RISK_ON (no direction gate)
    assert r.stop == pytest.approx(max(103.1, 101.5))
    assert r.score == pytest.approx(95.0) and r.size_mult == 1.0


def test_funding_long_mirror_and_score():
    out = detect_funding_fade(_row(f8=-0.001, f_rank=0.04, ret24=-0.05, close=103.0, high_prev=102.5,
                                   low_min6=101.0, state=ms.RISK_OFF))
    r = out.iloc[0]
    assert r.side == 1 and r.score == pytest.approx(96.0)
    assert r.stop == pytest.approx(min(100.9, 101.5))


@pytest.mark.parametrize("kw", [dict(is_print=False), dict(f8=0.0004), dict(f_rank=0.85), dict(ret24=-0.01),
                                dict(close=101.0), dict(state=ms.NO_TRADE)])
def test_funding_rejects(kw):
    assert detect_funding_fade(_row(**kw)).empty


def test_funding_grid_threshold():
    assert detect_funding_fade(_row(f8=0.0004), FundingParams(f_min=0.0003)).shape[0] == 1


def test_oi_filter_keeps_only_buildup_and_drops_missing():
    c = pd.DataFrame({"sym": ["A", "B", "C"], "t": [1, 2, 3], "detector": "funding"})
    oi = pd.Series([0.15, 0.05, np.nan], index=pd.MultiIndex.from_tuples([("A", 1), ("B", 2), ("C", 3)]))
    out = oi_filter(c, oi, 0.10)
    assert out["sym"].tolist() == ["A"] and out["detector"].iloc[0] == "funding_oi"


def test_verdict_bonferroni_and_halves():
    good = dict(trades=200, expectancy_r=0.2, profit_factor=1.5, folds_positive=0.7, max_dd=0.1, ex_best_coin_r=5.0)
    assert verdict(good, 0.03)["go"]                               # v1 alpha 0.05
    assert not verdict(good, 0.03, alpha=0.025)["go"]
    assert verdict(good, 0.01, 0.025, {"h1": 0.1, "h2": 0.05})["go"]
    assert not verdict(good, 0.01, 0.025, {"h1": 0.3, "h2": -0.01})["go"]
    assert not verdict(good, 0.01, 0.025, {"h1": 0.3, "h2": float("nan")})["go"]


def test_v3_studies_match_preregistration():
    c, d = STUDIES["funding_C"], STUDIES["funding_D"]
    for st in (c, d):
        assert st["null_alpha"] == 0.025 and st["direction_gate"] is False and st["side"] is None
    assert c["oos_start"] == "2021-01-01" and c["n_folds"] == 23
    assert d["oos_start"] == "2022-07-01" and d["n_folds"] == 17
    assert c["halves"][1] == ("2023-07-01", "2026-09-30") and d["halves"][0] == ("2022-07-01", "2024-06-30")
