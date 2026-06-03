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
import json
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

# Load secrets from .env into environment (DEEPSEEK_API_KEY, TELEGRAM_API_ID, etc.)
_env_path = PROJECT_ROOT / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(_env_path, override=True)
    except ImportError:
        pass

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
    Gracefully handles missing trading_engine_core wheel.
    """
    try:
        from src.trading_engine.strategy.grid import GridStrategy
    except ImportError as e:
        logger.error(
            "Cannot create strategies — trading_engine_core wheel not available: %s. "
            "Strategies will not run. The Rust engine still handles trading independently.",
            e,
        )
        return []

    strategies = []
    grid_cfg = build_grid_config(config)
    pairs = config.get("pairs", [])

    # Support both list-of-dicts and dict-of-dicts format
    pair_items = pairs.items() if isinstance(pairs, dict) else [(p.get("symbol", ""), p) for p in pairs]

    for pair, pair_cfg in pair_items:
        if not isinstance(pair_cfg, dict):
            continue
        if not pair_cfg.get("enabled", True):
            continue
        if not pair:
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
        proxy = None  # populated below if telegram handler is available

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

        # 7. Signal Copy Engine — listen to Telegram channels for trade signals
        signal_engine = None
        signal_cfg = self._config.get("signal_copy", {})
        if signal_cfg.get("enabled", False):
            try:
                from src.signals.signal_engine import SignalEngine

                signal_engine = SignalEngine(
                    config=signal_cfg,
                    btc_regime_fn=lambda: ("RANGING", 0.0, 0.0),
                    telegram_send_fn=lambda msg: logger.info("Signal TG: %s", msg),
                    buy_fn=lambda symbol, amount, price: self._signal_trade(adapter, "buy", symbol, amount, price),
                    sell_fn=lambda symbol, amount, price: self._signal_trade(adapter, "sell", symbol, amount, price),
                    get_price_fn=lambda symbol: self._get_signal_price(adapter, symbol),
                )
                signal_engine.start_listener()
                logger.info("Signal Copy Engine started — listening to Telegram channels")
            except Exception as e:
                logger.error("Signal Copy Engine failed to start: %s", e)

        # 8. Telegram command handler — poll for user commands
        telegram_handler = None
        try:
            from src.notifications.telegram_commands import TelegramCommandHandler
            from src.monitoring.system_monitor import get_stats
            from src.journal.trade_journal import TradeJournal
            from types import SimpleNamespace

            class RunnerProxy:
                """Proxies runner state to TelegramCommandHandler.
                Returns safe defaults for any Hummingbot-only attributes."""
                def __init__(self, **kwargs):
                    self._attrs = kwargs
                def __getattr__(self, name):
                    if name.startswith('_') or name in self._attrs:
                        return self._attrs.get(name)
                    # Safe defaults for Hummingbot-only attributes
                    return {}

            # Build grid/trend state from strategy host
            state_machines = {}
            grid_order_trackers = {}
            position_managers = {}
            for pair, strategy in strategies:
                status_str = strategy.format_status()
                state_val = "Active" if "Active" in status_str else "Paused"
                state_machines[pair] = SimpleNamespace(state=SimpleNamespace(value=state_val))
                grid_order_trackers[pair] = SimpleNamespace(total_pending=0)
                position_managers[pair] = SimpleNamespace(get_all_positions=lambda: [])

            pairs = {}
            for pair, _ in strategies:
                pairs[pair] = SimpleNamespace(
                    display_pair=pair.replace("-", "/"),
                    symbol=pair,
                    base_asset=pair.split("-")[0],
                )

            trend_capital = self._config.get("trend", {}).get("capital", 0)

            proxy = RunnerProxy(
                env=os.environ.get("ENV", "testnet"),
                pairs=pairs,
                state_machines=state_machines,
                grid_order_trackers=grid_order_trackers,
                _position_managers=position_managers,
                _trend_journal=None,
                _trend_manager=True,          # sentinel so /trend_status doesn't bail
                _trend_statuses={},            # populated by poll loop from Rust API
                _trend_capital=trend_capital,
                _signal_engine=signal_engine,
                _last_price={},
                _ml_classifier=None,
            )

            journal = TradeJournal()
            dummy_sm = SimpleNamespace(state=SimpleNamespace(value="Active"))

            telegram_handler = TelegramCommandHandler(
                journal=journal,
                state_machine=dummy_sm,
                circuit_breaker=SimpleNamespace(halted=False),
                position_guard=None,
                event_logger=None,
                strategy=proxy,
            )
            telegram_handler.start()
            logger.info("Telegram command handler started")
        except Exception as e:
            logger.warning("Telegram command handler not available: %s", e)

        # 9. Main bar loop + signal tick timer
        logger.info("Starting bar feed for pairs: %s", feed.pairs)

        async def signal_tick_loop():
            """Process queued signal messages every 30 seconds, independent of bars."""
            while self._running:
                await asyncio.sleep(30)
                if signal_engine is not None:
                    try:
                        signal_engine.tick()
                    except Exception as e:
                        logger.error("Signal tick error: %s", e)

        tick_task = asyncio.create_task(signal_tick_loop())

        # Telegram polling task — poll every 3 seconds independent of bars
        async def telegram_poll_loop():
            """Poll Telegram for commands and refresh strategy status from Rust."""
            import urllib.request
            rust_url = os.environ.get("RUST_ENGINE_URL", "http://localhost:3030")
            while self._running:
                # Refresh strategy status from Rust engine API
                if proxy is not None:
                    try:
                        req = urllib.request.Request(f"{rust_url}/api/v1/strategies")
                        with urllib.request.urlopen(req, timeout=3) as resp:
                            statuses = json.loads(resp.read())
                            for st in statuses:
                                pair = st.get("pair", "")
                                state = st.get("state", "")
                                pnl = st.get("pnl", 0)
                                orders = st.get("open_orders", 0)
                                name = st.get("name", "")
                                details = st.get("details", "")
                                if name == "grid":
                                    proxy.state_machines[pair] = SimpleNamespace(
                                        state=SimpleNamespace(value=state),
                                        details=details,
                                    )
                                    proxy.grid_order_trackers[pair] = SimpleNamespace(
                                        total_pending=orders
                                    )
                                    proxy.grid_pnl = {**getattr(proxy, 'grid_pnl', {}), pair: pnl}
                                elif name == "trend":
                                    proxy._trend_statuses[pair] = {
                                        "state": state,
                                        "pnl": pnl,
                                        "open_orders": orders,
                                        "details": details,
                                    }
                    except Exception:
                        pass
                # Poll Telegram commands
                if telegram_handler is not None:
                    try:
                        telegram_handler.poll_once()
                    except Exception as e:
                        logger.error("Telegram poll error: %s", e)
                await asyncio.sleep(3)

        tg_task = asyncio.create_task(telegram_poll_loop())
        try:
            async for bar in feed.bars():
                if not self._running:
                    break
                host.on_bar(bar)
                # Update last price cache and strategy states for status reports
                pair = bar.get("symbol", "")
                close = bar.get("close", 0)
                if pair and close and proxy is not None:
                    proxy._last_price[pair] = float(close)
                    # Refresh strategy state in proxy
                    for s in host.strategies():
                        if s.trading_pair() == pair:
                            status_str = s.format_status()
                            state_val = "Active" if "Active" in status_str else "Paused"
                            proxy.state_machines[pair] = SimpleNamespace(state=SimpleNamespace(value=state_val))
                # Process queued signal messages on each bar tick too
                if signal_engine is not None:
                    signal_engine.tick()
        except asyncio.CancelledError:
            pass
        finally:
            logger.info("TradingRunner shutting down...")
            tick_task.cancel()
            tg_task.cancel()
            if signal_engine is not None:
                signal_engine.stop_listener()
            fill_task.cancel()
            host.stop()
            logger.info("TradingRunner stopped.")

    def stop(self):
        """Signal the runner to stop."""
        self._running = False

    def _signal_trade(self, adapter, side: str, symbol: str, amount: float, price: float):
        """Execute a signal trade via the adapter."""
        try:
            from src.trading_engine.adapter.base import Order
            order_side = "BUY" if side == "buy" else "SELL"
            order = Order(
                instrument_id=symbol,
                side=order_side,
                order_type="LIMIT",
                price=price,
                quantity=amount,
                client_order_id=f"sig_{symbol}_{int(time.time())}",
            )
            result = adapter.submit_order(order)
            logger.info("Signal trade executed: %s %s %s @ %.4f → %s", side, amount, symbol, price, result)
            return result
        except Exception as e:
            logger.error("Signal trade failed: %s %s @ %.4f — %s", side, symbol, price, e)
            return None

    def _get_signal_price(self, adapter, symbol: str) -> float:
        """Get current price for a symbol via the adapter."""
        try:
            return adapter.get_mid_price(symbol)
        except Exception:
            return 0.0


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
