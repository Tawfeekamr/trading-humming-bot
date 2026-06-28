"""Tests for TelegramCommandHandler — comprehensive command coverage.

Targets the biggest coverage gap: telegram_commands.py was 0%, now pushing toward
~40%+. Tests commands that read from files (signal_positions.json, logs),
call the Rust API (urllib mocked), or format output (helpers).
"""
import json
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.notifications.telegram_commands import (
    TelegramCommandHandler,
    _fmt_price,
    _fmt_duration,
    _signal_price,
)


# ── Fixtures ──────────────────────────────────────────────────────────────

def _handler(tmp_path, **strategy_attrs):
    h = TelegramCommandHandler(
        journal=None,
        state_machine=SimpleNamespace(),
        circuit_breaker=SimpleNamespace(halted=False),
        position_guard=SimpleNamespace(),
        event_logger=SimpleNamespace(),
        strategy=SimpleNamespace(
            env="testnet", capital_usdt=10000, base_asset="CRYPTO",
            grid_manager=SimpleNamespace(capital_usdt=10000),
            _base_capital=10000, _trend_statuses={}, _trend_capital=10000,
            pairs={}, grid_pnl={},
            get_indicators_snapshot=lambda: None,
            _get_usdt_balance=lambda: 0, _get_base_balance=lambda: 0,
            **strategy_attrs,
        ),
    )
    return h


def _mock_update():
    u = MagicMock()
    u.message.reply_text = MagicMock()
    return u


def _replied(update):
    assert update.message.reply_text.called, "command did not reply"
    return update.message.reply_text.call_args[0][0]


def _mock_urlopen(json_data):
    """Create a mock urlopen that returns json_data as bytes (context-manager safe)."""
    m = MagicMock()
    m.__enter__.return_value = m
    m.read.return_value = json.dumps(json_data).encode() if isinstance(json_data, (dict, list)) else json_data.encode()
    return lambda *a, **k: m


# ── Helper tests ───────────────────────────────────────────────────────────

class TestHelpers:
    def test_fmt_price(self):
        assert _fmt_price(436) == "436"
        assert _fmt_price(0.198) == "0.198"
        assert _fmt_price(None) == "?"

    def test_fmt_duration(self):
        assert _fmt_duration(45) == "45s"
        assert _fmt_duration(120) == "2m"
        assert _fmt_duration(3700) == "1h1m"
        assert _fmt_duration(90000) == "1d1h"

    def test_signal_price_fallback(self, monkeypatch):
        monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
        assert _signal_price("BTC-USDT") == 0.0


# ── System commands ───────────────────────────────────────────────────────

class TestSystemCommands:
    def test_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "logs").mkdir(exist_ok=True)
        _handler(tmp_path)._cmd_status(_mock_update(), None)

    def test_readiness(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({"strategies": []}))
        _handler(tmp_path)._cmd_readiness(_mock_update(), None)

    def test_help(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_help(u, None)
        assert "/status" in _replied(u)

    def test_logs(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        logdir = tmp_path / "logs"
        logdir.mkdir(exist_ok=True)
        (logdir / "telegram.log").write_text("test log line\n")
        u = _mock_update()
        _handler(tmp_path)._cmd_logs(u, None)
        assert u.message.reply_text.called

    def test_errors(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        logdir = tmp_path / "logs"
        logdir.mkdir(exist_ok=True)
        (logdir / "error.log").write_text("some error\n")
        u = _mock_update()
        _handler(tmp_path)._cmd_errors(u, None)
        assert u.message.reply_text.called

    def test_price(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen([
            {"symbol": "BTCUSDT", "lastPrice": "60000", "priceChangePercent": "2.5"},
        ]))
        u = _mock_update()
        _handler(tmp_path)._cmd_price(u, None)
        assert u.message.reply_text.called


# ── P&L + overview commands ───────────────────────────────────────────────

class TestPnlCommands:
    def test_pnl_all(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({"strategies": []}))
        (tmp_path / "data").mkdir(exist_ok=True)
        u = _mock_update()
        _handler(tmp_path)._cmd_pnl_all(u, None)
        assert u.message.reply_text.called

    def test_bots(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen([
            {"name": "grid", "pair": "DOGE-USDT", "state": "Active", "pnl": 10.5,
             "open_orders": 2, "details": "ranging"},
        ]))
        u = _mock_update()
        _handler(tmp_path)._cmd_bots(u, None)
        assert u.message.reply_text.called

    def test_trades(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen([]))
        u = _mock_update()
        _handler(tmp_path)._cmd_trades(u, None)
        assert u.message.reply_text.called


# ── Engine status commands (Rust API) ─────────────────────────────────────

class TestEngineStatus:
    def _api_mock(self, strategies):
        return _mock_urlopen(strategies)

    def test_grid_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", self._api_mock([
            {"name": "grid", "pair": "DOGE-USDT", "state": "Active", "pnl": 29.57,
             "open_orders": 5, "details": "ranging ADX=20"},
        ]))
        u = _mock_update()
        _handler(tmp_path)._cmd_grid_status(u, None)
        assert u.message.reply_text.called

    def test_trend_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", self._api_mock([
            {"name": "trend", "pair": "ETH-USDT", "state": "WAITING", "pnl": -434,
             "open_orders": 0, "details": "Score:2/9"},
        ]))
        u = _mock_update()
        _handler(tmp_path)._cmd_trend_status(u, None)
        assert u.message.reply_text.called

    def test_swing_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", self._api_mock([
            {"name": "swing", "pair": "BNB-USDT", "state": "SEARCHING", "pnl": -44,
             "open_orders": 0, "details": "Capital: 4955"},
        ]))
        u = _mock_update()
        _handler(tmp_path)._cmd_swing_status(u, None)
        assert u.message.reply_text.called

    def test_mean_status(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", self._api_mock([
            {"name": "mr", "pair": "BNB-USDT", "state": "Scanning", "pnl": 1.31,
             "open_orders": 0, "details": "Trades: 0"},
        ]))
        u = _mock_update()
        _handler(tmp_path)._cmd_mean_status(u, None)
        assert u.message.reply_text.called


# ── Signal commands ────────────────────────────────────────────────────────

class TestSignalCommands:
    def _setup_positions(self, tmp_path, positions=None):
        d = tmp_path / "data"
        d.mkdir(exist_ok=True)
        (d / "signal_positions.json").write_text(json.dumps(positions or {}))

    def test_signal_status_with_position(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_positions(tmp_path, {
            "XLM-USDT": {"symbol": "XLM-USDT", "entry_price": 0.2, "is_closed": False,
                          "take_profits": [0.21], "signal_confidence": "high",
                          "channel_name": "BK", "entry_timestamp": time.time(),
                          "realized_pnl": 0, "tp1_hit": False, "stop_loss": 0.18},
        })
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({"bids": [[0.21, 1]], "asks": [[0.21, 1]]}))
        u = _mock_update()
        _handler(tmp_path)._cmd_signal_status(u, None)
        text = _replied(u)
        assert "SIGNAL" in text
        assert "XLM" in text

    def test_signal_status_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_positions(tmp_path)
        u = _mock_update()
        _handler(tmp_path)._cmd_signal_status(u, None)
        assert u.message.reply_text.called

    def test_signal_history(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        self._setup_positions(tmp_path, {
            "BTC-USDT": {"symbol": "BTC-USDT", "entry_price": 60000, "is_closed": True,
                          "exit_reason": "tp1", "realized_pnl": 150.0,
                          "entry_timestamp": time.time(), "signal_confidence": "high"},
        })
        u = _mock_update()
        _handler(tmp_path)._cmd_signal_history(u, None)
        assert u.message.reply_text.called

    def test_signal_channels(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("os.environ", {**os.environ, "SIGNAL_CHANNEL_IDS": "123,456"})
        u = _mock_update()
        _handler(tmp_path)._cmd_signal_channels(u, None)
        assert u.message.reply_text.called

    def test_signal_pause(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        u = _mock_update()
        _handler(tmp_path)._cmd_signal_pause(u, None)
        assert u.message.reply_text.called

    def test_signal_resume(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        u = _mock_update()
        _handler(tmp_path)._cmd_signal_resume(u, None)
        assert u.message.reply_text.called


# ── Capital command ────────────────────────────────────────────────────────

class TestCapitalCommand:
    def test_capital_success(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({
            "total_equity": 10000, "usdt_balance": 5000, "locked_in_positions": 5000,
            "reserve_limit_pct": 20, "reserve": 2000, "free_capital": 3000,
            "deployed_capital": {"grid": 5000},
        }))
        u = _mock_update()
        _handler(tmp_path)._cmd_capital(u, None)
        text = _replied(u)
        assert "Capital" in text

    def test_capital_api_error(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        def boom(*a, **k):
            raise OSError("down")
        monkeypatch.setattr("urllib.request.urlopen", boom)
        u = _mock_update()
        _handler(tmp_path)._cmd_capital(u, None)
        assert "Error" in _replied(u)


# ── Error handling ─────────────────────────────────────────────────────────

class TestErrorHandling:
    def test_signal_status_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        u = _mock_update()
        _handler(tmp_path)._cmd_signal_status(u, None)
        assert u.message.reply_text.called  # graceful, never crashes

    def test_any_command_never_raises(self, tmp_path, monkeypatch):
        """Every command catches exceptions and replies with an error."""
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        for cmd_name in ["_cmd_status", "_cmd_pnl_all", "_cmd_bots", "_cmd_help",
                         "_cmd_price", "_cmd_signal_status", "_cmd_capital"]:
            u = _mock_update()
            try:
                getattr(h, cmd_name)(u, None)
                assert u.message.reply_text.called, f"{cmd_name} did not reply"
            except Exception as e:
                pytest.fail(f"{cmd_name} raised {e} — commands must never crash")


# ── Recent Trades display ──────────────────────────────────────────────────

def _seed_trades_db(db_path, rows):
    """rows: list of (id, timestamp, engine, pair, pnl, exit_reason, quantity, is_backfilled)."""
    import sqlite3
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trades (id INTEGER PRIMARY KEY, timestamp TEXT, "
        "engine TEXT, pair TEXT, pnl REAL, exit_reason TEXT, quantity REAL, "
        "is_backfilled INTEGER DEFAULT 0)"
    )
    conn.executemany(
        "INSERT INTO trades (id, timestamp, engine, pair, pnl, exit_reason, quantity, is_backfilled) "
        "VALUES (?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


# ── Futures commands ───────────────────────────────────────────────────────

class TestFuturesCommands:
    def test_futures_status_reads_namespaced_file(self, tmp_path, monkeypatch):
        """The futures engine writes state to signal_positions_futures.json
        (namespaced) — not the spot file signal_positions.json. After the
        one-listener refactor both engines share data/, so the reader must
        use the namespaced path or it would show spot positions instead."""
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "data"
        d.mkdir(exist_ok=True)
        # futures (namespaced) file: has a position
        (d / "signal_positions_futures.json").write_text(json.dumps({
            "BTC-USDT": {"symbol": "BTC-USDT", "entry_price": 60000, "is_closed": False,
                         "take_profits": [61000], "signal_confidence": "high",
                         "channel_name": "FUT", "entry_timestamp": time.time(),
                         "realized_pnl": 0, "tp1_hit": False, "stop_loss": 58000},
        }))
        # spot file: a different pair that must NOT appear in the futures reply
        (d / "signal_positions.json").write_text(json.dumps({
            "XRP-USDT": {"symbol": "XRP-USDT", "entry_price": 0.5, "is_closed": False,
                         "take_profits": [0.6], "signal_confidence": "low",
                         "channel_name": "SPOT", "entry_timestamp": time.time(),
                         "realized_pnl": 0, "tp1_hit": False, "stop_loss": 0.4},
        }))
        monkeypatch.setattr("urllib.request.urlopen", _mock_urlopen({"bids": [[61000, 1]], "asks": [[61000, 1]]}))
        u = _mock_update()
        _handler(tmp_path)._cmd_futures_status(u, None)
        text = _replied(u)
        assert "FUTURES" in text
        assert "BTC" in text          # futures file content
        assert "XRP" not in text      # spot file must NOT leak

    def test_futures_pnl_reads_namespaced_file(self, tmp_path, monkeypatch):
        """/futures_pnl must also read the futures-namespaced file."""
        monkeypatch.chdir(tmp_path)
        d = tmp_path / "data"
        d.mkdir(exist_ok=True)
        (d / "signal_positions_futures.json").write_text(json.dumps({
            "ETH-USDT": {"symbol": "ETH-USDT", "is_closed": True, "realized_pnl": 250.0,
                         "exit_reason": "tp1", "entry_timestamp": time.time(),
                         "exit_timestamp": time.time(), "signal_confidence": "high"},
        }))
        (d / "signal_positions.json").write_text(json.dumps({
            "SOL-USDT": {"symbol": "SOL-USDT", "is_closed": True, "realized_pnl": -50.0,
                         "exit_reason": "sl", "entry_timestamp": time.time(),
                         "exit_timestamp": time.time(), "signal_confidence": "low"},
        }))
        u = _mock_update()
        _handler(tmp_path)._cmd_futures_pnl(u, None)
        text = _replied(u)
        assert "ETH" in text
        assert "SOL" not in text


# ── Recent Trades display ──────────────────────────────────────────────────

class TestRecentTrades:
    def test_orders_by_timestamp_not_insertion_id(self, tmp_path, monkeypatch):
        """Backfill re-inserts old trades with fresh high IDs every restart, so
        ORDER BY id DESC shows stale backfilled trades as 'most recent'. The
        display must order by trade timestamp."""
        monkeypatch.chdir(tmp_path)
        _seed_trades_db(tmp_path / "data" / "trades.db", [
            # OLD trade, but HIGH id (exactly what backfill re-insert produces)
            (100, "2026-06-14T10:00:00+00:00", "trend", "BNB-USDT", 30.02, "tp1", 0.5, 1),
            # RECENT trade, LOWER id (live insert)
            (50, "2026-06-23T13:26:00+00:00", "mr", "ETH-USDT", 19.82, "TakeProfit", 0.6, 0),
        ])
        u = _mock_update()
        _handler(tmp_path)._cmd_trades(u, None)
        reply = _replied(u)
        # Most-recent-by-timestamp (ETH, 06-23) must list before the older BNB (06-14).
        assert reply.index("ETH") < reply.index("BNB"), reply
        # Date must be shown so old vs new is visible (not just HH:MM).
        assert "06-23" in reply and "06-14" in reply, reply

    def test_filters_zero_qty_zero_pnl_artifacts(self, tmp_path, monkeypatch):
        """qty=0, pnl=0 rows are paper-engine artifacts, not real trades — hide them."""
        monkeypatch.chdir(tmp_path)
        _seed_trades_db(tmp_path / "data" / "trades.db", [
            (1, "2026-06-23T13:26:00+00:00", "mr", "ETH-USDT", 19.82, "TakeProfit", 0.6, 0),
            (2, "2026-06-23T12:00:00+00:00", "mr", "ETH-USDT", 0.0, "StopLoss", 0.0, 0),
        ])
        u = _mock_update()
        _handler(tmp_path)._cmd_trades(u, None)
        reply = _replied(u)
        assert "19.82" in reply  # real trade shown
        assert reply.count("StopLoss") == 0, f"artifact should be hidden: {reply}"


# ── Signal control-command wiring (Bug: "Signal engine not configured") ─────
#
# Root cause: in the headless signal-listener (run_signal_listener.py) the
# TelegramCommandHandler's strategy object never receives the live SignalEngine,
# so /signal_pause /signal_resume /signal_pnl /signal_inject /signal_close all
# bail with "Signal engine not configured." The legacy Hummingbot script wired
# it; the headless migration dropped the wiring. These tests pin the contract:
# once the engine is attached, the control commands must actually drive it, the
# registry must route /signal_pnl to the P&L handler (not status), /signal_close
# must be registered, and the menus must advertise the futures commands.

class TestSignalControlWiring:
    def _engine_with_journal(self):
        journal = MagicMock()
        journal.summary.return_value = {"total_trades": 3, "total_pnl": 12.5, "win_rate": 66.0}
        journal.summary_by_channel.return_value = {}
        engine = MagicMock()
        engine._journal = journal
        return engine

    def test_attach_signal_engines_method_exists_and_stores_engine(self, tmp_path):
        """attach_signal_engines is the seam the listener uses to hand the live
        engine to the handler. Without it every control command is dead."""
        h = _handler(tmp_path)
        engine = self._engine_with_journal()
        h.attach_signal_engines(engine)
        assert getattr(h.strategy, "_signal_engine", None) is engine

    def test_pause_calls_engine_when_attached(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        engine = self._engine_with_journal()
        h.attach_signal_engines(engine)
        u = _mock_update()
        h._cmd_signal_pause(u, None)
        engine.pause.assert_called_once()
        assert "paused" in _replied(u).lower()

    def test_resume_calls_engine_when_attached(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        engine = self._engine_with_journal()
        h.attach_signal_engines(engine)
        u = _mock_update()
        h._cmd_signal_resume(u, None)
        engine.resume.assert_called_once()
        assert "resum" in _replied(u).lower()

    def test_signal_pnl_reads_journal_when_attached(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        engine = self._engine_with_journal()
        h.attach_signal_engines(engine)
        u = _mock_update()
        h._cmd_signal_pnl(u, None)
        text = _replied(u)
        assert "SIGNAL P&L" in text
        engine._journal.summary.assert_called()

    def test_signal_pnl_does_not_say_not_configured_when_attached(self, tmp_path, monkeypatch):
        """Regression guard: the old failure mode was 'Signal engine not configured.'"""
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        h.attach_signal_engines(self._engine_with_journal())
        u = _mock_update()
        h._cmd_signal_pnl(u, None)
        assert "not configured" not in _replied(u).lower()


class TestRegistryCorrectness:
    def test_signal_pnl_routes_to_pnl_handler(self, tmp_path):
        """/signal_pnl must run the P&L handler, NOT the status handler."""
        h = _handler(tmp_path)
        assert h._commands["signal_pnl"] == h._cmd_signal_pnl

    def test_signal_close_is_registered(self, tmp_path):
        """/signal_close was missing from the registry entirely (silently ignored)."""
        h = _handler(tmp_path)
        assert h._commands["signal_close"] == h._cmd_signal_close

    def test_futures_commands_registered(self, tmp_path):
        h = _handler(tmp_path)
        assert h._commands["futures_status"] == h._cmd_futures_status
        assert h._commands["futures_pnl"] == h._cmd_futures_pnl


class TestMenusListFutures:
    def test_help_lists_futures_commands(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_help(u, None)
        text = _replied(u)
        assert "/futures_status" in text
        assert "/futures_pnl" in text

    def test_help_lists_all_signal_commands(self, tmp_path, monkeypatch):
        """All five signal control commands must be discoverable in /help."""
        monkeypatch.chdir(tmp_path)
        h = _handler(tmp_path)
        u = _mock_update()
        h._cmd_help(u, None)
        text = _replied(u)
        for cmd in ("/signal_status", "/signal_pnl", "/signal_history",
                    "/signal_pause", "/signal_resume", "/signal_close",
                    "/signal_inject", "/signal_channels"):
            assert cmd in text, f"{cmd} missing from /help"

    def test_startup_message_lists_futures(self, tmp_path):
        """The bot-online ping must advertise the futures commands too."""
        h = _handler(tmp_path)
        msg = h._startup_message()
        assert "/futures_status" in msg
        assert "/futures_pnl" in msg


class TestTrendHistorySource:
    """Regression: /trend_history must read the unified trades.db (engine='trend',
    is_backfilled=0 live rows), NOT the vestigial data/trend_journal.db which
    holds only stale rows and made trend P&L look like +$93 when it was actually
    -$472. See memory: pnl-reporting-source-of-truth."""

    def _seed(self, tmp_path):
        import sqlite3
        data = tmp_path / "data"
        data.mkdir(exist_ok=True)
        # Authoritative unified store.
        conn = sqlite3.connect(data / "trades.db")
        conn.execute("""CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp TEXT NOT NULL,
            engine TEXT NOT NULL, pair TEXT NOT NULL, side TEXT,
            entry_price REAL, exit_price REAL, quantity REAL, pnl REAL NOT NULL,
            exit_reason TEXT, duration_mins INTEGER, is_backfilled INTEGER DEFAULT 0)""")
        # Two LIVE trend rows (the real, losing trades).
        conn.execute("INSERT INTO trades (timestamp,engine,pair,side,entry_price,exit_price,quantity,pnl,exit_reason,is_backfilled) VALUES ('2026-06-17','trend','ETH-USDT','BUY',2000,1800,1.0,-259.25,'stop_loss',0)")
        conn.execute("INSERT INTO trades (timestamp,engine,pair,side,entry_price,exit_price,quantity,pnl,exit_reason,is_backfilled) VALUES ('2026-06-23','trend','BNB-USDT','BUY',607,600,1.0,-6.73,'signal_exit',0)")
        # A stale BACKFILLED trend row (old winner) — must be excluded.
        conn.execute("INSERT INTO trades (timestamp,engine,pair,side,entry_price,exit_price,quantity,pnl,exit_reason,is_backfilled) VALUES ('2026-06-14','trend','BNB-USDT','BUY',607,614,1.0,30.02,'tp1',1)")
        # A grid row — wrong engine, must be excluded.
        conn.execute("INSERT INTO trades (timestamp,engine,pair,side,entry_price,exit_price,quantity,pnl,exit_reason,is_backfilled) VALUES ('2026-06-18','grid','BNB-USDT','BUY',607,610,1.0,27.23,'grid_sell',0)")
        conn.commit(); conn.close()
        # Vestigial per-engine journal with a misleading phantom winner — must be IGNORED.
        vj = sqlite3.connect(data / "trend_journal.db")
        vj.execute("CREATE TABLE trend_trades (id INTEGER PRIMARY KEY, side TEXT, entry_price REAL, exit_price REAL, amount REAL, pnl REAL, exit_reason TEXT, pair TEXT)")
        vj.execute("INSERT INTO trend_trades VALUES (1,'BUY',607,999,1.0,999.0,'phantom','BNB-USDT')")
        vj.commit(); vj.close()

    def test_reads_unified_trades_db_live_trend_rows(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        monkeypatch.chdir(tmp_path)
        trades = _handler(tmp_path)._rust_trend_trades(limit=10)
        pnls = sorted(round(t["pnl"], 2) for t in trades)
        assert pnls == [-259.25, -6.73], \
            f"expected only the 2 live trend rows from trades.db, got {pnls}"

    def test_result_shape_matches_display_template(self, tmp_path, monkeypatch):
        self._seed(tmp_path)
        monkeypatch.chdir(tmp_path)
        trades = _handler(tmp_path)._rust_trend_trades(limit=10)
        assert trades, "expected seeded live trend rows"
        # The /trend_history template reads these exact keys (amount <- quantity).
        assert set(trades[0]) >= {"side", "entry_price", "exit_price", "amount", "pnl", "exit_reason", "pair"}
        assert all(t["pair"] in ("ETH-USDT", "BNB-USDT") for t in trades)

    def test_missing_trades_db_returns_empty(self, tmp_path, monkeypatch):
        # No trades.db at all -> graceful empty list, no crash.
        monkeypatch.chdir(tmp_path)
        assert _handler(tmp_path)._rust_trend_trades(limit=10) == []
