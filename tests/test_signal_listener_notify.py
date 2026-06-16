"""Tests for the signal-engine Telegram sender.

Background: SignalEngine._notify routes every trade event (entry, TP hits,
close) through a `telegram_send_fn` callable. The runner
(src/run_signal_listener.py) used to wire that callable to a no-op logger, so
no signal trade ever reached Telegram. These tests pin the contract of the
real sender so it can't silently regress to a logger again.
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.run_signal_listener import _telegram_send  # noqa: E402


class TestTelegramSend:
    """Signal-engine Telegram sender (was a no-op logger)."""

    def test_posts_message_to_telegram_api(self, monkeypatch):
        """With creds set, POSTs chat_id/text/parse_mode=HTML to the Bot API."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "TEST_CHAT")

        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)
            return None

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        _telegram_send("TP1 hit: ASTER")

        assert len(calls) == 1
        req = calls[0]
        assert req.full_url == "https://api.telegram.org/botTEST_TOKEN/sendMessage"
        payload = json.loads(req.data)
        assert payload["chat_id"] == "TEST_CHAT"
        assert payload["text"] == "TP1 hit: ASTER"
        assert payload["parse_mode"] == "HTML"

    def test_missing_credentials_skips_without_raising(self, monkeypatch):
        """No creds -> no HTTP call, no exception (engine keeps ticking)."""
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        _telegram_send("anything")  # must not raise

        assert calls == []

    def test_network_error_is_swallowed(self, monkeypatch):
        """A failed send must never crash the signal tick."""
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST_TOKEN")
        monkeypatch.setenv("TELEGRAM_CHAT_ID", "TEST_CHAT")

        def raising_urlopen(req, timeout=None):
            raise OSError("boom")

        monkeypatch.setattr("urllib.request.urlopen", raising_urlopen)

        _telegram_send("anything")  # must not propagate
