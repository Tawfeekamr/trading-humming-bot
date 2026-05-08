import pytest
from datetime import datetime, timezone, timedelta
from src.journal.trade_journal import TradeJournal, Trade


@pytest.fixture
def journal(tmp_path):
    return TradeJournal(db_path=tmp_path / "test_fees.db")


def _trade(journal, gross_pnl, fee, net_pnl, hours_ago=0):
    ts = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")
    journal.log_trade(Trade(
        timestamp=ts, pair="BTC/USDT", side="SELL",
        entry_price=100_000, exit_price=100_050, quantity=0.001,
        gross_pnl=gross_pnl, fee=fee, net_pnl=net_pnl,
        grid_level=1, duration_min=60,
        rsi=50, bb_upper=105_000, bb_lower=95_000,
        ema_200=100_000, atr=800, grid_state="ACTIVE",
    ))


class TestFeeSummary:
    def test_empty_returns_zeros(self, journal):
        r = journal.fee_summary("2000-01-01")
        assert r["total_fees"] == 0
        assert r["fee_to_gross_ratio"] == 0.0

    def test_single_trade(self, journal):
        _trade(journal, gross_pnl=5.0, fee=0.15, net_pnl=4.85)
        r = journal.fee_summary("2000-01-01")
        assert r["total_fees"] == pytest.approx(0.15, abs=0.01)
        assert r["trade_count"] == 1

    def test_ratio_calculation(self, journal):
        _trade(journal, gross_pnl=10.0, fee=3.0, net_pnl=7.0)
        r = journal.fee_summary("2000-01-01")
        assert r["fee_to_gross_ratio"] == pytest.approx(0.3, abs=0.01)


class TestIsOvertrading:
    def test_no_trades(self, journal):
        assert journal.is_overtrading()["is_overtrading"] is False

    def test_below_threshold(self, journal):
        _trade(journal, gross_pnl=10.0, fee=2.0, net_pnl=8.0)
        assert journal.is_overtrading(threshold=0.30)["is_overtrading"] is False

    def test_above_threshold(self, journal):
        _trade(journal, gross_pnl=5.0, fee=2.0, net_pnl=3.0)
        assert journal.is_overtrading(threshold=0.30)["is_overtrading"] is True

    def test_custom_threshold(self, journal):
        _trade(journal, gross_pnl=10.0, fee=2.0, net_pnl=8.0)
        assert journal.is_overtrading(threshold=0.10)["is_overtrading"] is True


class TestFeeTimeSeries:
    def test_empty(self, journal):
        assert journal.fee_time_series(days=30) == []

    def test_returns_daily(self, journal):
        _trade(journal, gross_pnl=5.0, fee=0.10, net_pnl=4.90)
        _trade(journal, gross_pnl=3.0, fee=0.10, net_pnl=2.90)
        r = journal.fee_time_series(days=1)
        assert len(r) == 1
        assert r[0]["daily_fees"] == pytest.approx(0.20, abs=0.01)