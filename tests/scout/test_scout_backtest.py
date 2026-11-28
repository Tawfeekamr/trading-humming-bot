"""Scout backtest: trade simulator (live ladder parity, conservative bar rules),
portfolio rules, walk-forward helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from backtest.scout.portfolio import run_portfolio
from backtest.scout.sim import simulate_trade
from backtest.scout.walk_forward import folds, max_drawdown, verdict
from src.scout import market_state as ms
from src.scout.config import CostParams, PortfolioParams

FREE = CostParams(fee_bps=0, slippage_bps=0, funding_per_8h=0)


def bars(rows):
    """rows: list of (o, h, l, c)."""
    a = np.array(rows, dtype=float)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


TPS = (110.0, 120.0, 130.0)   # entry 100, stop 90 → 1R/2R/3R


def test_stop_loss_is_minus_one_r():
    r = simulate_trade(*bars([(100, 101, 89, 95)]), 0, 1, 90.0, TPS, 1.0, FREE, 72)
    assert r.reason == "stop" and r.r == pytest.approx(-1.0)


def test_full_ladder_matches_live_split():
    r = simulate_trade(*bars([(100, 101, 99, 100), (101, 111, 100.5, 110), (110, 121, 109, 120),
                              (120, 131, 119, 130)]), 0, 1, 90.0, TPS, 1.0, FREE, 72)
    # 33% @ +1R, 50% of 67% @ +2R, rest @ +3R
    expected = 0.33 * 1 + 0.67 * 0.5 * 2 + 0.67 * 0.5 * 3
    assert r.reason == "tp3" and r.tps_hit == 3 and r.r == pytest.approx(expected)


def test_bar_touching_stop_and_target_takes_stop():
    r = simulate_trade(*bars([(100, 115, 85, 100)]), 0, 1, 90.0, TPS, 1.0, FREE, 72)
    assert r.reason == "stop" and r.r == pytest.approx(-1.0)


def test_tp1_on_entry_bar_closes_rest_at_breakeven():
    # entry bar's low is always <= its open (= entry), so the conservative rule applies
    r = simulate_trade(*bars([(100, 111, 99, 110)]), 0, 1, 90.0, TPS, 1.0, FREE, 72)
    assert r.reason == "breakeven" and r.r == pytest.approx(0.33)


def test_tp1_then_breakeven_same_bar():
    r = simulate_trade(*bars([(100, 101, 99, 100), (100, 111, 99.5, 105)]), 0, 1, 90.0, TPS, 1.0, FREE, 72)
    assert r.reason == "breakeven" and r.tps_hit == 1
    assert r.r == pytest.approx(0.33)


def test_gap_through_stop_fills_at_open():
    r = simulate_trade(*bars([(100, 101, 99, 100), (80, 82, 78, 81)]), 0, 1, 90.0, TPS, 1.0, FREE, 72)
    assert r.reason == "stop" and r.r == pytest.approx(-2.0)


def test_time_exit_at_close():
    rows = [(100, 101, 99, 100)] * 3 + [(100, 104, 99, 104)]
    r = simulate_trade(*bars(rows), 0, 1, 90.0, TPS, 1.0, FREE, 4)
    assert r.reason == "time" and r.exit_idx == 3 and r.r == pytest.approx(0.4)


def test_data_end_exit():
    rows = [(100, 101, 99, 100), (100, 101, 99, 102)] + [(np.nan,) * 4] * 3
    r = simulate_trade(*bars(rows), 0, 1, 90.0, TPS, 1.0, FREE, 72)
    assert r.reason == "data_end" and r.exit_idx == 1


def test_short_mirror():
    tps = (90.0, 80.0, 70.0)
    r = simulate_trade(*bars([(100, 111, 99, 105)]), 0, -1, 110.0, tps, 1.0, FREE, 72)
    assert r.reason == "stop" and r.r == pytest.approx(-1.0)
    r = simulate_trade(*bars([(100, 101, 99, 100), (99, 99.5, 89, 90), (90, 91, 79, 80), (80, 81, 69, 70)]),
                       0, -1, 110.0, tps, 1.0, FREE, 72)
    assert r.reason == "tp3" and r.r > 2


def test_costs_reduce_r():
    c = CostParams(fee_bps=5, slippage_bps=8, funding_per_8h=0.0001)
    r = simulate_trade(*bars([(100, 101, 89, 95)]), 0, 1, 90.0, TPS, 1.0, c, 72)
    # entry 100.08, stop fill 89.928 → loss ≈ 10.15 + fees ≈ 0.095 + funding
    assert r.r < -1.0
    assert r.pnl == pytest.approx(sum(p for _, p in r.legs))


# ── portfolio ──────────────────────────────────────────────────────────
class FakePanel:
    def __init__(self, n, series):
        self.index = pd.date_range("2024-01-01", periods=n, freq="h", tz="UTC")
        self.ohlc = {s: bars(rows) for s, rows in series.items()}
        rng = np.random.default_rng(1)
        self.rets = {s: rng.normal(0, 0.01, n).astype(np.float32) for s in series}

    @property
    def n(self):
        return len(self.index)

    @property
    def day_index(self):
        return (self.index.asi8 // (86_400 * 10**9)).astype(np.int64)


def cand(t, sym, lo=99.0, hi=101.0, stop=90.0, score=50.0, state=ms.RISK_ON):
    return dict(t=t, sym=sym, side=1, entry_low=lo, entry_high=hi, stop=stop, tp1=110.0, tp2=120.0,
                tp3=130.0, score=score, state=state, stop_dist_atr1h=2.0, detector="test")


flat = [(100, 101, 99, 100)] * 30


def test_chased_entry_skipped():
    p = FakePanel(30, {"A": flat})
    trades, skips = run_portfolio(pd.DataFrame([cand(0, "A", lo=101.5, hi=102.5)]), p, costs=FREE)
    assert not trades and skips["chased"] == 1


def test_capacity_and_score_priority():
    p = FakePanel(30, {s: flat for s in "ABCD"})
    c = pd.DataFrame([cand(0, s, score=sc) for s, sc in zip("ABCD", (10, 40, 30, 20))])
    trades, skips = run_portfolio(c, p, PortfolioParams(corr_max=1.0), costs=FREE)
    assert sorted(t.sym for t in trades) == ["B", "C", "D"] and skips["capacity"] == 1


def test_sizing_risk_and_neutral_half():
    p = FakePanel(30, {"A": flat, "B": flat})
    c = pd.DataFrame([cand(0, "A"), cand(0, "B", state=ms.NEUTRAL)])
    trades, _ = run_portfolio(c, p, PortfolioParams(corr_max=1.0), costs=FREE)
    by = {t.sym: t for t in trades}
    assert by["A"].risk == pytest.approx(75.0) and by["B"].risk == pytest.approx(37.5)


def test_notional_cap():
    p = FakePanel(30, {"A": flat})
    trades, _ = run_portfolio(pd.DataFrame([cand(0, "A", stop=99.5, lo=99, hi=101)]), p, costs=FREE)
    assert trades[0].qty * trades[0].entry_fill == pytest.approx(7_500.0)


def test_cooldown_after_stop():
    rows = [(100, 101, 99, 100), (100, 101, 89, 90)] + flat
    p = FakePanel(len(rows), {"A": rows})
    c = pd.DataFrame([cand(0, "A"), cand(5, "A"), cand(14, "A")])
    trades, skips = run_portfolio(c, p, costs=FREE)
    assert [t.signal_idx for t in trades] == [0, 14] and skips["cooldown"] == 1


def test_daily_loss_limit_blocks_rest_of_day():
    stop_rows = [(100, 101, 99, 100), (100, 101, 70, 72)] + flat    # gap-stop → big loss
    p = FakePanel(len(stop_rows), {s: stop_rows for s in "ABCDE"} | {"F": flat * 2})
    # 3 positions × $200 risk stopped at bar 1 = −$600 > 3% of $10k
    c = pd.DataFrame([cand(0, s) for s in "ABC"] + [cand(3, "F")])
    pp = PortfolioParams(corr_max=1.0, max_notional=1e9, risk_pct=0.02)
    trades, skips = run_portfolio(c, p, pp, costs=FREE)
    assert {t.sym for t in trades} == {"A", "B", "C"} and skips["daily_loss"] == 1


def test_correlated_candidate_skipped():
    p = FakePanel(800, {"A": flat * 27, "B": flat * 27})
    p.rets["B"] = p.rets["A"].copy()
    c = pd.DataFrame([cand(750, "A", score=60), cand(750, "B", score=50)])
    trades, skips = run_portfolio(c, p, costs=FREE)
    assert [t.sym for t in trades] == ["A"] and skips["correlated"] == 1


# ── walk-forward helpers ────────────────────────────────────────────────
def test_folds_are_13_quarters_with_6m_train():
    idx = pd.date_range("2022-11-01", "2026-09-30 23:00", freq="h", tz="UTC")
    fd = folds(idx)
    assert len(fd) == 13 and fd[0]["name"] == "2023Q3" and fd[-1]["name"] == "2026Q3"
    assert idx[fd[0]["train"][0]] == pd.Timestamp("2023-01-01", tz="UTC")
    assert fd[-1]["test"][1] == len(idx)
    for f in fd:
        assert f["train"][1] == f["test"][0]


def test_verdict_requires_all_checks():
    good = dict(trades=200, expectancy_r=0.2, profit_factor=1.5, folds_positive=0.7, max_dd=0.1,
                ex_best_coin_r=5.0)
    assert verdict(good, 0.01)["go"]
    assert not verdict(good, 0.2)["go"]
    assert not verdict(dict(good, trades=100), 0.01)["go"]


def test_max_drawdown():
    class T:
        def __init__(self, legs):
            self.legs = legs
    assert max_drawdown([T([(0, 100.0), (1, -300.0), (2, 50.0)])], 10_000) == pytest.approx(0.03)


def test_studies_match_preregistrations():
    from backtest.scout.walk_forward import STUDIES
    p1, su, ss = STUDIES["phase1"], STUDIES["short_unseen"], STUDIES["short_seen"]
    assert p1["side"] is None and p1["detectors"] == ("breakout", "pullback")
    assert su["side"] == -1 and su["detectors"] == ("breakout",)
    # primary v2 period must end before v1's first training window starts (unseen data)
    assert pd.Timestamp(su["data"][1]) < pd.Timestamp("2023-01-01") + pd.DateOffset(months=6)
    assert su["oos_start"] == "2021-01-01" and su["n_folds"] == 10
    idx = pd.date_range(su["data"][0], su["data"][1] + " 23:00", freq="h", tz="UTC")
    fd = folds(idx, su["oos_start"], su["n_folds"])
    assert fd[0]["name"] == "2021Q1" and fd[-1]["name"] == "2023Q2" and fd[-1]["test"][1] == len(idx)
    assert idx[fd[0]["train"][0]] == pd.Timestamp("2020-07-01", tz="UTC")
    assert ss["data"] == p1["data"] and ss["side"] == -1
