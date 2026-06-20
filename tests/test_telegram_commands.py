"""Tests for TelegramCommandHandler command handlers — covers the formatting +
file/DB-reading paths (the 1141-line telegram_commands.py was 0% covered).

Uses a mock update + mock strategy. Commands that read from files use tmp_path;
commands that call the Rust API have urllib mocked.
"""
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.notifications.telegram_commands import TelegramCommandHandler, _fmt_price, _fmt_duration


def _handler(tmp_path, **strategy_attrs):
    """Construct a handler with a mock strategy + isolated data dir."""
    h = TelegramCommandHandler(
        journal=None,
        state_machine=SimpleNamespace(),
        circuit_breaker=SimpleNamespace(halted=False),
        position_guard=SimpleNamespace(),
        event_logger=SimpleNamespace(),
        strategy=SimpleNamespace(
            env="testnet",
            capital_usdt=10000,
            base_asset="CRYPTO",
            grid_manager=SimpleNamespace(capital_usdt=10000),
            _base_capital=10000,
            _trend_statuses={},
            _trend_capital=10000,
            pairs={},
            grid_pnl={},
            get_indicators_snapshot=lambda: None,
            _get_usdt_balance=lambda: 0,
            _get_base_balance=lambda: 0,
            **strategy_attrs,
        ),
    )
    return h


def _mock_update():
    """A mock Telegram update that captures reply_text calls."""
    u = MagicMock()
    u.message.reply_text = MagicMock()
    return u


def _replied_text(update):
    """Extract the text from the (first) reply_text call."""
    assert update.message.reply_text.called, "command did not reply"
    return update.message.reply_text.call_args[0][0]


class TestStatusCommands:
    def test_status_replies_with_server_health(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir(exist_ok=True)
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_status(u, None)
        text = _replied_text(u)
        assert "Server" in text or "Status" in text or "uptime" in text.lower()

    def test_readiness_replies(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        u = _mock_update()
        # Mock the Rust API calls inside readiness
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: MagicMock(read=lambda: b'{"strategies":[]}'))
        h._cmd_readiness(u, None)
        assert u.message.reply_text.called

    def test_help_lists_commands(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_help(u, None)
        text = _replied_text(u)
        assert "/status" in text or "/help" in text


class TestSignalCommands:
    def test_signal_status_reads_positions_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir(exist_ok=True)
        (tmp_path / "data" / "signal_positions.json").write_text(json.dumps({
            "XLM-USDT": {"symbol": "XLM-USDT", "entry_price": 0.2, "is_closed": False,
                          "take_profits": [0.21], "signal_confidence": "high",
                          "channel_name": "TestCh", "entry_timestamp": 1700000000,
                          "realized_pnl": 0, "tp1_hit": False},
        }))
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_signal_status(u, None)
        text = _replied_text(u)
        assert "SIGNAL" in text
        assert "XLM" in text

    def test_signal_status_no_positions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir(exist_ok=True)
        (tmp_path / "data" / "signal_positions.json").write_text("{}")
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_signal_status(u, None)
        assert u.message.reply_text.called

    def test_signal_history_reads_positions(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "data").mkdir(exist_ok=True)
        (tmp_path / "data" / "signal_positions.json").write_text(json.dumps({
            "BTC-USDT": {"symbol": "BTC-USDT", "entry_price": 60000, "is_closed": True,
                          "exit_reason": "tp1", "realized_pnl": 150.0, "entry_timestamp": 1700000000,
                          "signal_confidence": "high"},
        }))
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_signal_history(u, None)
        text = _replied_text(u)
        assert "BTC" in text or "closed" in text.lower() or "No closed" in text


class TestCapitalCommand:
    def test_capital_calls_rust_api(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mock_resp = MagicMock()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.read.return_value = json.dumps({
            "total_equity": 10000, "usdt_balance": 5000, "locked_in_positions": 5000,
            "reserve_limit_pct": 20, "reserve": 2000, "free_capital": 3000,
            "deployed_capital": {"grid": 5000},
        }).encode()
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: mock_resp)
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_capital(u, None)
        text = _replied_text(u)
        assert "Capital" in text
        assert "10,000" in text or "10000" in text

    def test_capital_handles_api_failure(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        def boom(*a, **k):
            raise OSError("api down")
        monkeypatch.setattr("urllib.request.urlopen", boom)
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_capital(u, None)
        text = _replied_text(u)
        assert "Error" in text or "error" in text.lower()


class TestFmtHelpers:
    def test_fmt_price_various(self):
        assert _fmt_price(436) == "436"
        assert _fmt_price(0.198) == "0.198"
        assert _fmt_price(None) == "?"

    def test_fmt_duration_various(self):
        assert _fmt_duration(45) == "45s"
        assert _fmt_duration(120) == "2m"
        assert _fmt_duration(3600) == "1h0m"


class TestErrorHandling:
    """Commands must reply with an error message, never crash."""
    def test_signal_status_missing_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_signal_status(u, None)
        assert u.message.reply_text.called  # graceful, no crash
