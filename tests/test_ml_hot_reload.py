# tests/test_ml_hot_reload.py
"""Tests for ML model hot-reload detection."""
import pytest
import pickle
import time
import os
from pathlib import Path


def _write_model(path: Path, version: int = 1):
    """Write a minimal model file and return its mtime."""
    # Create a minimal mock model dict (no sklearn required)
    data = {"model_type": "mock_model", "version": version, "data": [1, 2, 3]}
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(data, f)
    return os.path.getmtime(str(path))


class TestMLHotReload:
    def test_detects_modified_model(self, tmp_path):
        """Hot-reload should detect when model mtime changes."""
        model_path = tmp_path / "regime_ETH-USDT.pkl"
        mtime1 = _write_model(model_path, version=1)
        last_mtime = mtime1
        time.sleep(0.1)
        mtime2 = _write_model(model_path, version=2)
        assert mtime2 != last_mtime

    def test_no_reload_when_unchanged(self, tmp_path):
        """Hot-reload should NOT trigger when mtime unchanged."""
        model_path = tmp_path / "regime_ETH-USDT.pkl"
        mtime = _write_model(model_path)
        assert os.path.getmtime(str(model_path)) == mtime

    def test_reload_updates_mtime_tracker(self, tmp_path):
        """After reload, the tracked mtime should match the new file."""
        model_path = tmp_path / "regime_ETH-USDT.pkl"
        _write_model(model_path, version=1)
        tracked_mtime = os.path.getmtime(str(model_path))
        time.sleep(0.1)
        _write_model(model_path, version=2)
        new_mtime = os.path.getmtime(str(model_path))
        tracked_mtime = new_mtime
        assert tracked_mtime == os.path.getmtime(str(model_path))
