"""Integration test — verify the full config → strategy → actor pipeline
loads without errors (no live connections)."""
import os
from unittest.mock import MagicMock, patch

import pytest
import yaml

from src.nautilus.config import load_config, get_enabled_pairs


@pytest.fixture
def full_config(tmp_path):
    """Write a complete nautilus.yaml to temp dir."""
    config = {
        "trader_id": "TEST-INTEGRATION",
        "logging_level": "DEBUG",
        "interactive_brokers": {
            "host": "127.0.0.1",
            "port": 7497,
            "client_id": 99,
            "trading_mode": "paper",
        },
        "pairs": [
            {"symbol": "EUR/USD", "venue": "IDEALPRO", "enabled": True, "trade_size": 10000},
        ],
        "grid": {"levels": 3, "spacing_pips": 5, "take_profit_pips": 10, "stop_loss_pips": 20},
        "indicators": {"ema_fast": 10, "ema_slow": 20, "rsi_period": 14},
        "risk": {"max_drawdown_pct": 5},
        "ml_service": {"enabled": False, "url": "http://localhost:8000/predict", "poll_interval_seconds": 60},
        "mqtt": {"enabled": False, "host": "localhost", "port": 1883, "topic_prefix": "test/nautilus"},
    }
    path = tmp_path / "nautilus.yaml"
    with open(path, "w") as f:
        yaml.dump(config, f)

    original = os.environ.get("NAUTILUS_CONFIG_PATH")
    os.environ["NAUTILUS_CONFIG_PATH"] = str(path)
    yield config
    if original is None:
        os.environ.pop("NAUTILUS_CONFIG_PATH", None)
    else:
        os.environ["NAUTILUS_CONFIG_PATH"] = original


def test_config_loads_full_yaml(full_config):
    config = load_config()
    assert config["trader_id"] == "TEST-INTEGRATION"
    assert config["interactive_brokers"]["port"] == 7497


def test_enabled_pairs_from_full_config(full_config):
    config = load_config()
    pairs = get_enabled_pairs(config)
    assert len(pairs) == 1
    assert pairs[0]["symbol"] == "EUR/USD"
    assert pairs[0]["trade_size"] == 10000


def test_grid_config_present(full_config):
    config = load_config()
    grid = config["grid"]
    assert grid["levels"] == 3
    assert grid["spacing_pips"] == 5


def test_strategy_can_be_instantiated_from_config(full_config):
    """Verify strategy config builds from loaded YAML values."""
    from decimal import Decimal
    from src.nautilus.strategies.forex_grid import ForexGridStrategy, ForexGridConfig

    config = load_config()
    pair = get_enabled_pairs(config)[0]
    grid = config["grid"]
    indicators = config["indicators"]

    strategy_config = ForexGridConfig(
        instrument_id="EUR/USD.IDEALPRO",
        bar_type="EUR/USD.IDEALPRO-1-HOUR-BID-INTERNAL",
        levels=grid["levels"],
        spacing_pips=grid["spacing_pips"],
        take_profit_pips=grid["take_profit_pips"],
        stop_loss_pips=grid["stop_loss_pips"],
        trade_size=Decimal(str(pair["trade_size"])),
        ema_fast=indicators["ema_fast"],
        ema_slow=indicators["ema_slow"],
        rsi_period=indicators["rsi_period"],
    )

    strategy = ForexGridStrategy(strategy_config)
    assert strategy.config.levels == 3
    assert strategy.config.trade_size == Decimal("10000")


def test_ml_poller_disabled_in_config(full_config):
    """When ml_service.enabled=False, poller should skip."""
    from src.nautilus.actors.ml_poller import MLPoller, MLPollerConfig

    config = load_config()
    ml = config["ml_service"]

    poller = MLPoller(MLPollerConfig(
        ml_service_url=ml["url"],
        poll_interval_secs=ml["poll_interval_seconds"],
        enabled=ml["enabled"],
    ))
    poller.log = MagicMock()
    poller.clock = MagicMock()

    poller.on_start()
    poller.clock.timer.assert_not_called()


def test_mqtt_bridge_disabled_in_config(full_config):
    """When mqtt.enabled=False, bridge should skip."""
    from src.nautilus.actors.mqtt_bridge import MQTTBridge, MQTTBridgeConfig

    config = load_config()
    mqtt = config["mqtt"]

    bridge = MQTTBridge(MQTTBridgeConfig(
        mqtt_host=mqtt["host"],
        mqtt_port=mqtt["port"],
        topic_prefix=mqtt["topic_prefix"],
        enabled=mqtt["enabled"],
    ))
    bridge.log = MagicMock()
    bridge.clock = MagicMock()

    bridge.on_start()
    bridge.clock.timer.assert_not_called()
