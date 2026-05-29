"""Tests for nautilus config loader."""
import os
import tempfile
from pathlib import Path

import pytest
import yaml

from src.nautilus.config import load_config, get_config_path, get_enabled_pairs


@pytest.fixture
def sample_config():
    return {
        "trader_id": "TEST-001",
        "interactive_brokers": {
            "host": "127.0.0.1",
            "port": 7497,
            "client_id": 1,
            "trading_mode": "paper",
        },
        "pairs": [
            {"symbol": "EUR/USD", "venue": "IDEALPRO", "enabled": True, "trade_size": 10000},
            {"symbol": "GBP/USD", "venue": "IDEALPRO", "enabled": False, "trade_size": 10000},
        ],
        "grid": {"levels": 5, "spacing_pips": 10},
        "risk": {"max_drawdown_pct": 10},
    }


@pytest.fixture
def config_file(sample_config, tmp_path):
    """Write sample config to a temp file and set env var."""
    path = tmp_path / "nautilus.yaml"
    with open(path, "w") as f:
        yaml.dump(sample_config, f)
    original = os.environ.get("NAUTILUS_CONFIG_PATH")
    os.environ["NAUTILUS_CONFIG_PATH"] = str(path)
    yield path
    if original is None:
        os.environ.pop("NAUTILUS_CONFIG_PATH", None)
    else:
        os.environ["NAUTILUS_CONFIG_PATH"] = original


def test_load_config_reads_yaml(config_file):
    config = load_config()
    assert config["trader_id"] == "TEST-001"
    assert config["interactive_brokers"]["host"] == "127.0.0.1"
    assert config["interactive_brokers"]["port"] == 7497


def test_load_config_env_overrides(config_file):
    os.environ["IB_HOST"] = "192.168.1.100"
    os.environ["IB_PORT"] = "4001"
    try:
        config = load_config()
        assert config["interactive_brokers"]["host"] == "192.168.1.100"
        assert config["interactive_brokers"]["port"] == 4001
    finally:
        os.environ.pop("IB_HOST", None)
        os.environ.pop("IB_PORT", None)


def test_load_config_missing_file():
    os.environ["NAUTILUS_CONFIG_PATH"] = "/nonexistent/nautilus.yaml"
    with pytest.raises(FileNotFoundError, match="not found"):
        load_config()


def test_get_enabled_pairs_filters_disabled(sample_config):
    enabled = get_enabled_pairs(sample_config)
    assert len(enabled) == 1
    assert enabled[0]["symbol"] == "EUR/USD"


def test_get_enabled_pairs_all_disabled():
    config = {"pairs": [{"symbol": "X", "enabled": False}]}
    assert get_enabled_pairs(config) == []


def test_get_enabled_pairs_empty():
    assert get_enabled_pairs({}) == []
