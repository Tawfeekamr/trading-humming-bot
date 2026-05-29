"""Load and validate nautilus.yaml configuration."""
import os
from pathlib import Path

import yaml


def get_config_path() -> Path:
    """Resolve config path from env var or default location."""
    env_path = os.environ.get("NAUTILUS_CONFIG_PATH")
    if env_path:
        return Path(env_path)
    return Path(__file__).resolve().parents[2] / "config" / "nautilus.yaml"


def load_config() -> dict:
    """Load nautilus.yaml and merge environment variable overrides."""
    config_path = get_config_path()
    if not config_path.exists():
        raise FileNotFoundError(f"NautilusTrader config not found: {config_path}")

    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    config.setdefault("interactive_brokers", {})
    ib = config["interactive_brokers"]

    env_host = os.environ.get("IB_HOST")
    if env_host:
        ib["host"] = env_host

    env_port = os.environ.get("IB_PORT")
    if env_port:
        ib["port"] = int(env_port)

    env_client_id = os.environ.get("IB_CLIENT_ID")
    if env_client_id:
        ib["client_id"] = int(env_client_id)

    env_mode = os.environ.get("IB_TRADING_MODE")
    if env_mode:
        ib["trading_mode"] = env_mode

    return config


def get_enabled_pairs(config: dict) -> list[dict]:
    """Return only enabled pairs from config."""
    return [p for p in config.get("pairs", []) if p.get("enabled", False)]
