import json
import pytest
from pathlib import Path
from unittest.mock import patch
from hummingbot_files.scripts.capital_manager import CapitalManager


class TestCapitalManager:
    def test_initial_state(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        assert cm.available == 5000.0
        assert cm.total_capital == 5000.0

    def test_allocate_grid_success(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        assert cm.allocate("DOGE-USDT", "grid", 1000.0) is True
        assert cm.available == 4000.0
        assert cm.allocated("DOGE-USDT", "grid") == 1000.0

    def test_allocate_insufficient_capital(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        assert cm.allocate("DOGE-USDT", "grid", 6000.0) is False
        assert cm.available == 5000.0

    def test_release(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        cm.allocate("DOGE-USDT", "grid", 1000.0)
        cm.release("DOGE-USDT", "grid")
        assert cm.available == 5000.0

    def test_max_per_pair_enforced(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, max_per_pair=0.25, state_dir=tmp_path)
        # 25% of 5000 = 1250 max per pair
        assert cm.allocate("DOGE-USDT", "grid", 1250.0) is True
        assert cm.allocate("DOGE-USDT", "trend", 1.0) is False

    def test_multiple_pairs(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, max_per_pair=0.5, state_dir=tmp_path)
        cm.allocate("DOGE-USDT", "grid", 1000.0)
        cm.allocate("ETH-USDT", "grid", 2000.0)
        assert cm.available == 2000.0

    def test_save_and_load(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        cm.allocate("DOGE-USDT", "grid", 1000.0)
        cm.save()

        cm2 = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        cm2.load()
        assert cm2.allocated("DOGE-USDT", "grid") == 1000.0
        assert cm2.available == 4000.0

    def test_release_nonexistent_is_noop(self, tmp_path):
        cm = CapitalManager(total_capital=5000.0, state_dir=tmp_path)
        cm.release("DOGE-USDT", "grid")  # should not raise
        assert cm.available == 5000.0
