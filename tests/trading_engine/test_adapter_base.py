"""Verify ExecutionAdapter ABC cannot be instantiated directly."""
import pytest
from src.trading_engine.adapter.base import ExecutionAdapter


def test_cannot_instantiate_abc():
    with pytest.raises(TypeError):
        ExecutionAdapter()
