"""Adapter factory — selects the correct ExecutionAdapter based on environment.

The EXECUTION_MODE env var controls which adapter is used:
    - "rust"       → RustEngineAdapter (HTTP client to Rust trading engine API)
    - "hummingbot" → HummingbotAdapter (wraps Hummingbot connectors)
    - "mock"       → MockAdapter (in-memory, for testing)

This enables instant rollback: change the env var and redeploy.
"""
import os
import logging

from .base import ExecutionAdapter

logger = logging.getLogger(__name__)


def create_adapter(mode: str | None = None, **kwargs) -> ExecutionAdapter:
    """Create an ExecutionAdapter based on the execution mode.

    Args:
        mode: Override the EXECUTION_MODE env var. One of "rust", "hummingbot", "mock".
        **kwargs: Adapter-specific arguments:
            - For "rust": base_url (optional, defaults to RUST_ENGINE_URL env var)
            - For "hummingbot": connector, strategy_ref (required)
            - For "mock": balances (optional)

    Returns:
        An ExecutionAdapter instance

    Raises:
        ValueError: If the mode is unknown or required kwargs are missing
    """
    mode = (mode or os.environ.get("EXECUTION_MODE", "hummingbot")).lower().strip()

    if mode == "rust":
        from .rust_engine import RustEngineAdapter
        base_url = kwargs.get("base_url") or os.environ.get(
            "RUST_ENGINE_URL", "http://localhost:3030"
        )
        logger.info("Creating RustEngineAdapter (url=%s)", base_url)
        return RustEngineAdapter(base_url=base_url)

    elif mode == "hummingbot":
        from .hummingbot import HummingbotAdapter
        connector = kwargs.get("connector")
        strategy_ref = kwargs.get("strategy_ref")
        if connector is None or strategy_ref is None:
            raise ValueError(
                "HummingbotAdapter requires 'connector' and 'strategy_ref' kwargs"
            )
        logger.info("Creating HummingbotAdapter")
        return HummingbotAdapter(connector, strategy_ref)

    elif mode == "mock":
        from .mock import MockAdapter
        balances = kwargs.get("balances")
        logger.info("Creating MockAdapter")
        return MockAdapter(balances=balances)

    else:
        raise ValueError(
            f"Unknown EXECUTION_MODE: '{mode}'. "
            f"Expected one of: rust, hummingbot, mock"
        )
