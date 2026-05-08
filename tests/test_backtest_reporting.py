import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from backtest.reporting import (
    compute_benchmark, add_slippage, monte_carlo_simulation,
    BacktestResult, format_report,
)


@pytest.fixture
def price_series():
    np.random.seed(42)
    n = 500
    dates = pd.date_range("2025-01-01", periods=n, freq="h")
    close = pd.Series(100_000 + np.cumsum(np.random.normal(0, 200, n)), index=dates)
    return close


class TestComputeBenchmark:
    def test_starts_at_zero(self, price_series):
        bench = compute_benchmark(price_series)
        assert bench.iloc[0] == pytest.approx(0.0, abs=0.01)

    def test_positive_when_rising(self):
        close = pd.Series([100, 110, 120, 130], dtype=float)
        assert compute_benchmark(close).iloc[-1] > 0

    def test_negative_when_falling(self):
        close = pd.Series([130, 120, 110, 100], dtype=float)
        assert compute_benchmark(close).iloc[-1] < 0


class TestAddSlippage:
    def test_returns_series(self, price_series):
        entries = price_series < price_series.mean()
        exits = price_series > price_series.mean()
        sl_e, sl_x = add_slippage(price_series, entries, exits, slip_bps=10)
        assert isinstance(sl_e, pd.Series)

    def test_zero_slippage_no_change(self, price_series):
        entries = price_series < price_series.mean()
        exits = price_series > price_series.mean()
        sl_e, sl_x = add_slippage(price_series, entries, exits, slip_bps=0)
        pd.testing.assert_series_equal(sl_e, entries)


class TestMonteCarlo:
    def test_output_shape_and_columns(self):
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.001, 0.02, 100))
        mc = monte_carlo_simulation(returns, n_sims=100, n_days=30)
        assert "p5" in mc.columns
        assert "p50" in mc.columns
        assert "p95" in mc.columns
        assert len(mc) == 30

    def test_percentile_ordering(self):
        np.random.seed(42)
        returns = pd.Series(np.random.normal(0.001, 0.02, 100))
        mc = monte_carlo_simulation(returns, n_sims=100, n_days=30)
        assert mc["p5"].iloc[-1] < mc["p95"].iloc[-1]


class TestFormatReport:
    def test_contains_key_metrics(self, price_series):
        bench = compute_benchmark(price_series)
        result = BacktestResult(
            strategy_returns=bench, benchmark_returns=bench,
            trades=100, sharpe=1.5, max_drawdown_pct=5.0,
            total_return_pct=12.0, win_rate=55.0,
            total_fees=1.5, slippage_cost=0.3,
        )
        text = format_report(result, label="Test")
        assert "Sharpe" in text
        assert "1.5" in text
        assert "HODL" in text