"""Adapter factory — selects the correct ExecutionAdapter based on environment.

The EXECUTION_MODE env var controls which adapter is used:
    - "rust" → RustEngineAdapter (HTTP client to Rust trading engine API)

Default is "rust" for Docker deployments.
"""
import os
import logging

from .base import ExecutionAdapter

logger = logging.getLogger(__name__)


def create_adapter(mode: str | None = None, **kwargs) -> ExecutionAdapter:
    """Create an ExecutionAdapter based on the execution mode.

    Args:
        mode: Override the EXECUTION_MODE env var. Currently only "rust".
        **kwargs: Adapter-specific arguments:
            - For "rust": base_url (optional, defaults to RUST_ENGINE_URL env var)

    Returns:
        An ExecutionAdapter instance

    Raises:
        ValueError: If the mode is unknown or required kwargs are missing
    """
    mode = (mode or os.environ.get("EXECUTION_MODE", "rust")).lower().strip()

    if mode == "rust":
        from .rust_engine import RustEngineAdapter
        base_url = kwargs.get("base_url") or os.environ.get(
            "RUST_ENGINE_URL", "http://localhost:3030"
        )
        logger.info("Creating RustEngineAdapter (url=%s)", base_url)
        return RustEngineAdapter(base_url=base_url)

    else:
        raise ValueError(
            f"Unknown EXECUTION_MODE: '{mode}'. Expected: rust"
        )
