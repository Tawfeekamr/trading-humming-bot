"""NautilusTrader live trading node bootstrap.

Initializes a TradingNode with the Interactive Brokers adapter,
registers the Forex grid strategy, ML poller actor, and MQTT bridge actor,
then runs the node.

Usage:
    python -m src.nautilus.main
"""
import sys


def main():
    """Bootstrap and run the NautilusTrader live node.

    This function imports nautilus_trader lazily because the package
    is only available inside the Docker container. It allows the module
    to be imported for syntax checking in the dev environment.
    """
    try:
        from decimal import Decimal

        from nautilus_trader.config import TradingNodeConfig, LoggingConfig
        from nautilus_trader.live.node import TradingNode
        from nautilus_trader.model.identifiers import TraderId, InstrumentId
        from nautilus_trader.model.data import BarType

        from nautilus_trader.adapters.interactive_brokers.config import (
            InteractiveBrokersDataClientConfig,
            InteractiveBrokersExecClientConfig,
            InteractiveBrokersInstrumentProviderConfig,
        )
        from nautilus_trader.adapters.interactive_brokers.factories import (
            InteractiveBrokersLiveDataClientFactory,
            InteractiveBrokersLiveExecClientFactory,
        )
    except ImportError as e:
        print(f"ERROR: NautilusTrader not available: {e}")
        print("This module must run inside the nautilus-bot Docker container.")
        print("Build with: docker compose --profile forex build nautilus-bot")
        print("Run with:   docker compose --profile forex up nautilus-bot")
        sys.exit(1)

    from src.nautilus.config import load_config, get_enabled_pairs
    from src.nautilus.strategies.forex_grid import ForexGridStrategy, ForexGridConfig
    from src.nautilus.actors.ml_poller import MLPoller, MLPollerConfig
    from src.nautilus.actors.mqtt_bridge import MQTTBridge, MQTTBridgeConfig

    config = load_config()
    ib_config = config.get("interactive_brokers", {})
    mqtt_config = config.get("mqtt", {})
    ml_config = config.get("ml_service", {})

    # Load instrument IDs for IB
    enabled_pairs = get_enabled_pairs(config)
    if not enabled_pairs:
        print("ERROR: No enabled pairs — nothing to trade. Check config/nautilus.yaml")
        sys.exit(1)

    load_ids = {
        f"{p['symbol']}.{p.get('venue', 'IDEALPRO')}" for p in enabled_pairs
    }

    trader_id = config.get("trader_id", "FOREX-001")

    # Build TradingNode config
    node_config = TradingNodeConfig(
        trader_id=TraderId(trader_id),
        logging=LoggingConfig(
            log_level=config.get("logging_level", "INFO"),
        ),
        data_clients={
            "IB": InteractiveBrokersDataClientConfig(
                host=ib_config.get("host", "127.0.0.1"),
                port=ib_config.get("port", 7497),
                client_id=ib_config.get("client_id", 1),
                instrument_provider=InteractiveBrokersInstrumentProviderConfig(
                    load_ids=load_ids,
                ),
            ),
        },
        exec_clients={
            "IB": InteractiveBrokersExecClientConfig(),
        },
    )

    # Create the node
    node = TradingNode(config=node_config)

    # Register IB adapter factories
    node.add_data_client_factory("IB", InteractiveBrokersLiveDataClientFactory)
    node.add_exec_client_factory("IB", InteractiveBrokersLiveExecClientFactory)

    # Add strategies for each enabled pair
    grid_config = config.get("grid", {})
    indicator_config = config.get("indicators", {})

    for pair in enabled_pairs:
        symbol = pair["symbol"]
        venue = pair.get("venue", "IDEALPRO")
        instrument_id = InstrumentId.from_str(f"{symbol}/{venue}")

        bar_type_str = f"{instrument_id}-1-HOUR-BID-INTERNAL"

        allowed_regimes = None
        if ml_config.get("enabled"):
            allowed_regimes = ["RANGING", "TRENDING_UP"]

        strategy_config = ForexGridConfig(
            instrument_id=instrument_id,
            bar_type=BarType.from_str(bar_type_str),
            levels=grid_config.get("levels", 5),
            spacing_pips=grid_config.get("spacing_pips", 10),
            take_profit_pips=grid_config.get("take_profit_pips", 15),
            stop_loss_pips=grid_config.get("stop_loss_pips", 30),
            trade_size=Decimal(str(pair.get("trade_size", 10000))),
            ema_fast=indicator_config.get("ema_fast", 20),
            ema_slow=indicator_config.get("ema_slow", 50),
            rsi_period=indicator_config.get("rsi_period", 14),
            rsi_oversold=indicator_config.get("rsi_oversold", 35),
            rsi_overbought=indicator_config.get("rsi_overbought", 70),
            allowed_regimes=allowed_regimes,
        )

        strategy = ForexGridStrategy(strategy_config)
        node.trader.add_strategy(strategy)
        print(f"  Strategy loaded: {strategy.id}")

    # Add ML poller actor (if enabled)
    if ml_config.get("enabled"):
        ml_poller = MLPoller(MLPollerConfig(
            ml_service_url=ml_config.get("url", "http://bot:8080/predict"),
            poll_interval_secs=ml_config.get("poll_interval_seconds", 300.0),
            enabled=True,
        ))
        node.actor.add(ml_poller)
        print(f"  ML poller actor loaded: {ml_config.get('url')}")

    # Add MQTT bridge actor (if enabled)
    if mqtt_config.get("enabled", True):
        mqtt_bridge = MQTTBridge(MQTTBridgeConfig(
            mqtt_host=mqtt_config.get("host", "mosquitto"),
            mqtt_port=mqtt_config.get("port", 1883),
            topic_prefix=mqtt_config.get("topic_prefix", "hbot/nautilus"),
            enabled=True,
        ))
        node.actor.add(mqtt_bridge)
        print(f"  MQTT bridge actor loaded: {mqtt_config.get('host')}")

    # Build and run
    node.build()
    print(f"\nNautilusTrader node '{trader_id}' built successfully")
    print(f"  Pairs: {len(enabled_pairs)}")
    print(f"  IB: {ib_config.get('host')}:{ib_config.get('port')} ({ib_config.get('trading_mode')})")
    print(f"\nStarting live trading node...")

    try:
        node.run()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        node.dispose()
        print("Node disposed. Goodbye.")


if __name__ == "__main__":
    main()
