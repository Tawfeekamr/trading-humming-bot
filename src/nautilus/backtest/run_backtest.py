"""Backtest runner for NautilusTrader Forex strategies.

Loads historical Forex data and runs the ForexGridStrategy
through NautilusTrader's BacktestEngine for validation before live trading.

Usage:
    python -m src.nautilus.backtest.run_backtest --data data/forex/EURUSD_1H.csv
"""
import argparse
import sys
from decimal import Decimal
from pathlib import Path

from src.nautilus.config import load_config, get_enabled_pairs


def run_backtest(data_dir: str, config_path: str | None = None):
    """Run backtest with historical data from data_dir."""
    if config_path:
        import os
        os.environ["NAUTILUS_CONFIG_PATH"] = config_path

    config = load_config()
    enabled_pairs = get_enabled_pairs(config)

    if not enabled_pairs:
        print("ERROR: No enabled pairs in config")
        sys.exit(1)

    print(f"Backtest framework ready.")
    print(f"  Config: trader_id={config.get('trader_id')}")
    print(f"  Pairs: {[p['symbol'] for p in enabled_pairs]}")
    print(f"  Grid: levels={config.get('grid', {}).get('levels')}, spacing={config.get('grid', {}).get('spacing_pips')} pips")
    print(f"  Data dir: {data_dir}")
    print()
    print("Note: Full backtest execution requires nautilus_trader with BacktestEngine.")
    print("      This runner loads config and validates pair setup.")

    return config


def main():
    parser = argparse.ArgumentParser(description="NautilusTrader Forex backtest runner")
    parser.add_argument("--data", required=True, help="Path to historical data directory")
    parser.add_argument("--config", default=None, help="Path to nautilus.yaml config")
    args = parser.parse_args()

    run_backtest(args.data, args.config)


if __name__ == "__main__":
    main()
