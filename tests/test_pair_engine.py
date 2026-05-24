# tests/test_pair_engine.py
import pytest
from pathlib import Path
from hummingbot_files.scripts.pair_engine import PairEngine, PairConfig


class TestPairEngine:
    def test_config_derives_helpers(self):
        cfg = PairConfig(symbol="DOGE-USDT", step_size=1, enabled=True)
        assert cfg.base_asset == "DOGE"
        assert cfg.binance_symbol == "DOGEUSDT"
        assert cfg.display_pair == "DOGE/USDT"

    def test_config_disabled(self):
        cfg = PairConfig(symbol="BTC-USDT", step_size=0.00001, enabled=False)
        assert not cfg.enabled

    def test_engine_creates_indicators(self):
        cfg = PairConfig(symbol="DOGE-USDT", step_size=1, enabled=True)
        engine = PairEngine(cfg)
        assert engine.bb is not None
        assert engine.rsi is not None
        assert engine.ema is not None
        assert engine.atr is not None

    def test_engine_state_files(self, tmp_path):
        cfg = PairConfig(symbol="DOGE-USDT", step_size=1, enabled=True)
        engine = PairEngine(cfg, state_dir=tmp_path)
        assert engine.grid_state_path == tmp_path / "grid_state_DOGE.json"
        assert engine.trend_state_path == tmp_path / "trend_state_DOGE.json"


class TestPairConfigTickSize:
    def test_default_tick_size(self):
        cfg = PairConfig(symbol="ETH-USDT", step_size=0.0001)
        assert cfg.tick_size == 0.01

    def test_custom_tick_size(self):
        cfg = PairConfig(symbol="DOGE-USDT", step_size=1, tick_size=0.00001)
        assert cfg.tick_size == 0.00001

    def test_engine_inherits_tick_size(self):
        cfg = PairConfig(symbol="XRP-USDT", step_size=0.1, tick_size=0.0001)
        engine = PairEngine(cfg)
        assert engine.tick_size == 0.0001

    def test_engine_inherits_default_tick_size(self):
        cfg = PairConfig(symbol="ETH-USDT", step_size=0.0001)
        engine = PairEngine(cfg)
        assert engine.tick_size == 0.01
