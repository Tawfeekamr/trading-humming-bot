import pytest
import tempfile
from pathlib import Path
from src.trend.trend_journal import TrendJournal


@pytest.fixture
def journal():
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    path = Path(tmp.name)
    j = TrendJournal(db_path=path)
    yield j
    path.unlink()


class TestTrendJournal:
    def test_log_and_retrieve_trade(self, journal):
        trade_id = journal.log_trade(
            side="BUY", entry_price=94.0, exit_price=100.0,
            amount=14.0, fee=2.10, pnl=82.9, pnl_pct=6.02,
            stop_loss=91.3, take_profit=100.0,
            exit_reason="take_profit", signal_score=4,
            duration_minutes=180,
        )
        assert trade_id > 0
        trades = journal.get_trades()
        assert len(trades) == 1
        assert trades[0]["side"] == "BUY"
        assert trades[0]["pnl"] == 82.9
        assert trades[0]["exit_reason"] == "take_profit"

    def test_summary_empty(self, journal):
        summary = journal.summary()
        assert summary["total_trades"] == 0
        assert summary["win_rate"] == 0.0

    def test_summary_with_trades(self, journal):
        journal.log_trade("BUY", 94.0, 100.0, 14.0, 2.0, 82.0, 6.0, 91.0, 100.0, "take_profit", 4, 180)
        journal.log_trade("BUY", 95.0, 92.0, 14.0, 2.0, -44.0, -3.1, 92.0, 101.0, "stop_loss", 3, 60)
        summary = journal.summary()
        assert summary["total_trades"] == 2
        assert summary["winning"] == 1
        assert summary["losing"] == 1
        assert abs(summary["win_rate"] - 50.0) < 0.1
        assert abs(summary["net_pnl"] - 38.0) < 0.1

    def test_recent_trades_limit(self, journal):
        for i in range(15):
            journal.log_trade("BUY", 94.0, 95.0, 14.0, 1.0, 13.0, 1.0, 92.0, 96.0, "take_profit", 4, 60)
        recent = journal.recent_trades(limit=10)
        assert len(recent) == 10

    def test_performance_metrics(self, journal):
        journal.log_trade("BUY", 94.0, 100.0, 14.0, 2.0, 82.0, 6.0, 91.0, 100.0, "take_profit", 4, 180)
        journal.log_trade("BUY", 95.0, 100.0, 14.0, 2.0, 68.6, 5.0, 92.0, 101.0, "trailing_stop", 5, 240)
        journal.log_trade("BUY", 93.0, 90.0, 14.0, 2.0, -44.0, -3.2, 90.0, 99.0, "stop_loss", 3, 30)
        metrics = journal.performance()
        assert abs(metrics["profit_factor"] - (150.6 / 44.0)) < 0.1
        assert metrics["avg_win"] > 0
        assert metrics["avg_loss"] < 0
