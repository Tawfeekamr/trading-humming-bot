"""Trading engine runner — main orchestration loop for hybrid mode.

This is the entry point that replaces Hummingbot's runtime. It:
1. Loads configuration from config/strategy.yaml
2. Creates the execution adapter via factory (rust/hummingbot/mock)
3. Creates a StrategyHost with strategies for each configured pair
4. Starts a data feed that polls the Rust engine for closed bars
5. Routes bars to strategies and detects fills

Usage (via Dockerfile.hybrid ENTRYPOINT):
    python -m src.trading_engine.runner

Or directly:
    EXECUTION_MODE=rust RUST_ENGINE_URL=http://localhost:3030 python -m src.trading_engine.runner
"""
import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

import yaml

# Add project root to path for src/ imports
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.trading_engine.adapter.factory import create_adapter
from src.trading_engine.adapter.base import Order, OrderFill
from src.trading_engine.host import StrategyHost
from src.trading_engine.data_feed import DataFeed

logger = logging.getLogger(__name__)


def load_config(path: str = "config/strategy.yaml") -> dict:
    """Load strategy configuration from YAML."""
    config_path = PROJECT_ROOT / path
    if not config_path.exists():
        logger.warning("Config not found at %s, using defaults", config_path)
        return _default_config()
    with open(config_path) as f:
        return yaml.safe_load(f)


def _default_config() -> dict:
    """Fallback config when strategy.yaml is missing."""
    return {
        "pairs": {
            "BTC-USDT": {"enabled": True, "tick_size": 0.01, "step_size": 0.0001},
        },
        "grid": {
            "levels": 5,
            "capital_usdt": 5000,
            "min_usdt_reserve": 100,
        },
        "data_feed": {
            "interval": "1m",
        },
    }


def build_grid_config(config: dict) -> dict:
    """Extract grid strategy config from the main config."""
    grid = config.get("grid", {})
    indicators = config.get("indicators", {})
    return {
        "levels": grid.get("levels", 5),
        "capital": grid.get("capital_usdt", 5000),
        "spacing_atr_multiplier": grid.get("spacing_atr_multiplier", 1.5),
        "min_usdt_reserve": grid.get("min_usdt_reserve", 100),
        "ema_period": indicators.get("ema", {}).get("period", 200),
        "rsi_period": indicators.get("rsi", {}).get("period", 14),
        "bollinger_period": indicators.get("bollinger", {}).get("period", 20),
        "bollinger_std_dev": indicators.get("bollinger", {}).get("std_dev", 2.0),
        "atr_period": indicators.get("atr", {}).get("period", 14),
        "order_refresh_seconds": grid.get("order_refresh_time", 60),
    }


def create_strategies(config: dict) -> list:
    """Create strategy instances for each enabled pair.

    Returns a list of (pair, strategy_instance) tuples.
    """
    from src.trading_engine.strategy.grid import GridStrategy

    strategies = []
    grid_cfg = build_grid_config(config)
    pairs = config.get("pairs", {})

    for pair, pair_cfg in pairs.items():
        if not isinstance(pair_cfg, dict):
            continue
        if not pair_cfg.get("enabled", True):
            continue

        strategy = GridStrategy(pair, grid_cfg)
        strategies.append((pair, strategy))
        logger.info("Created GridStrategy for %s", pair)

    return strategies


class FillDetector:
    """Detects order fills by polling open orders and comparing against tracked state.

    This replaces Hummingbot's `did_fill_order` callback with a simple
    diff-based approach: if a tracked order disappears from the exchange's
    open orders list, it was filled (or cancelled).
    """

    def __init__(self, host: StrategyHost, poll_interval: float = 15.0):
        self._host = host
        self._poll_interval = poll_interval
        self._tracked_orders: dict[str, Order] = {}

    def track(self, order_id: str, order: Order):
        """Start tracking an order for fill detection."""
        self._tracked_orders[order_id] = order

    async def poll_loop(self):
        """Periodically check for fills by diffing open orders."""
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
                self._check_fills()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("FillDetector error: %s", e)

    def _check_fills(self):
        """Check each tracked order against exchange state."""
        if not self._tracked_orders:
            return

        filled_ids = []
        for pair in {o.instrument_id for o in self._tracked_orders.values()}:
            try:
                open_orders = self._host.adapter.get_open_orders(pair)
                open_ids = {o.client_order_id for o in open_orders}

                for order_id, order in self._tracked_orders.items():
                    if order.instrument_id != pair:
                        continue
                    if order_id not in open_ids:
                        # Order is no longer open — it was filled or cancelled
                        fill = OrderFill(
                            client_order_id=order_id,
                            instrument_id=order.instrument_id,
                            side=order.side,
                            price=order.price,
                            quantity=order.quantity,
                            timestamp=int(time.time() * 1000),
                        )
                        self._host.on_order_filled(fill)
                        filled_ids.append(order_id)
                        logger.info(
                            "Fill detected: %s %s %s @ %.2f",
                            order.side, order.instrument_id, order_id, order.price,
                        )
            except Exception as e:
                logger.warning("Fill check failed for %s: %s", pair, e)

        for oid in filled_ids:
            del self._tracked_orders[oid]


class TradingRunner:
    """Main orchestration loop for the hybrid trading system.

    Coordinates: adapter creation → strategy host → data feed → fill detection.
    """

    def __init__(self, config: dict):
        self._config = config
        self._running = False
        self._tasks: list[asyncio.Task] = []

    async def run(self):
        """Start the trading system."""
        self._running = True
        logger.info("TradingRunner starting...")

        # 1. Create execution adapter via factory
        adapter = create_adapter()
        logger.info("Adapter: %s", type(adapter).__name__)

        # 2. Check adapter health (for RustEngineAdapter)
        if hasattr(adapter, "is_healthy"):
            healthy = adapter.is_healthy()
            logger.info("Adapter health: %s", "OK" if healthy else "UNAVAILABLE")
            if not healthy:
                logger.warning("Rust engine not reachable — waiting for it to start")

        # 3. Build strategy host
        host = StrategyHost(adapter)
        strategies = create_strategies(self._config)
        for pair, strategy in strategies:
            host.add_strategy(strategy)
        host.start()
        logger.info("StrategyHost started with %d strategies", len(strategies))

        # 4. Create data feed
        feed_cfg = self._config.get("data_feed", {})
        rust_url = os.environ.get("RUST_ENGINE_URL", "http://localhost:3030")
        feed = DataFeed(
            base_url=rust_url,
            interval=feed_cfg.get("interval", "1m"),
        )
        for pair, _ in strategies:
            feed.add_pair(pair)

        # 5. Create fill detector
        fill_detector = FillDetector(host, poll_interval=15.0)

        # 6. Start background tasks
        fill_task = asyncio.create_task(fill_detector.poll_loop())
        self._tasks.append(fill_task)

        # 7. Main bar loop
        logger.info("Starting bar feed for pairs: %s", feed.pairs)
        try:
            async for bar in feed.bars():
                if not self._running:
                    break
                host.on_bar(bar)
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("TradingRunner shutting down...")
            fill_task.cancel()
            host.stop()
            logger.info("TradingRunner stopped.")

    def stop(self):
        """Signal the runner to stop."""
        self._running = False


async def main():
    """Entry point for the runner."""
    # Setup logging
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Load config
    config = load_config()
    logger.info("Config loaded from config/strategy.yaml")

    # Create and run
    runner = TradingRunner(config)

    # Handle shutdown signals
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, runner.stop)

    await runner.run()


if __name__ == "__main__":
    asyncio.run(main())
