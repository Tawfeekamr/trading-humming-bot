from pathlib import Path


class TestDashboardSmoke:
    def test_app_exists(self):
        assert Path("app.py").exists()

    def test_journal_fee_methods(self):
        from src.journal.trade_journal import TradeJournal
        assert hasattr(TradeJournal, "fee_summary")
        assert hasattr(TradeJournal, "is_overtrading")
        assert hasattr(TradeJournal, "fee_time_series")

    def test_indicators_instantiate(self):
        from src.indicators.bollinger import BollingerBands
        from src.indicators.rsi import RSI
        from src.indicators.ema import EMA
        from src.indicators.atr import ATR
        assert BollingerBands(20, 2.0) is not None
        assert RSI(14) is not None
        assert EMA(200) is not None
        assert ATR(14, 0.8) is not None

    def test_telegram_has_fees_command(self):
        from src.notifications.telegram_commands import TelegramCommandHandler
        assert hasattr(TelegramCommandHandler, "_cmd_fees")

    def test_backtest_reporting_exists(self):
        from backtest.reporting import compute_benchmark, monte_carlo_simulation
        assert callable(compute_benchmark)
        assert callable(monte_carlo_simulation)