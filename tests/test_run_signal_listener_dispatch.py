"""Tests for the one-listener-two-engines dispatch in run_signal_listener.

A single Telethon listener (owned by the spot engine) must feed BOTH the spot
and the futures engine. The futures engine is headless (own_listener=False).
This test exercises `dispatch_cycle`, the per-cycle seam extracted from the
main loop, so the dispatch wiring can be verified without Telethon or a live
event loop.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import src.run_signal_listener as rsl  # noqa: E402


class _FakeListener:
    """Yields one canned message, then None forever."""

    def __init__(self, msg):
        self._msg = msg
        self._served = False

    def get_message(self):
        if self._served:
            return None
        self._served = True
        return self._msg


class _RecordingEngine:
    """Records process_one / manage calls and masquerades as a SignalEngine."""

    def __init__(self, name, own_listener, listener=None):
        self.name = name
        self._own_listener = own_listener
        self._listener = listener
        self.processed = []
        self.managed = 0

    def process_one(self, msg, connector):
        self.processed.append((msg, connector))

    def manage(self, connector):
        self.managed += 1


def test_dispatch_cycle_fanouts_message_to_both_engines():
    """One message from the shared listener reaches BOTH engines' process_one."""
    canned_msg = {"text": "BUY ETH-USDT 3000 sl 3150 tp 2900"}
    listener = _FakeListener(canned_msg)

    spot = _RecordingEngine("spot", own_listener=True, listener=listener)
    futures = _RecordingEngine("futures", own_listener=False)

    connector = object()
    rsl.dispatch_cycle(spot, futures, connector)

    # The spot engine's listener was drained and the message dispatched to both.
    assert spot.processed == [(canned_msg, connector)], spot.processed
    assert futures.processed == [(canned_msg, connector)], futures.processed
    # Both engines had their manage() called once per cycle.
    assert spot.managed == 1
    assert futures.managed == 1


def test_dispatch_cycle_futures_error_does_not_kill_spot():
    """An exception in the futures engine must not abort the spot dispatch."""
    listener = _FakeListener("MSG")

    spot = _RecordingEngine("spot", own_listener=True, listener=listener)

    class _BoomEngine(_RecordingEngine):
        def process_one(self, msg, connector):
            raise RuntimeError("futures boom")
        # manage is fine; we still want spot to manage even if futures manage
        # would also blow up — so make manage raise too.

    class _BoomManage(_BoomEngine):
        def manage(self, connector):
            raise RuntimeError("futures manage boom")

    futures = _BoomManage("futures", own_listener=False)

    # Must not raise — futures errors are swallowed.
    rsl.dispatch_cycle(spot, futures, object())

    # Spot still processed its message and managed, despite futures raising.
    assert len(spot.processed) == 1
    assert spot.managed == 1


def test_dispatch_cycle_spot_error_still_manages_both_engines():
    """A spot processing error must not skip SL/TP management for the cycle."""
    listener = _FakeListener("MSG")

    class _BoomSpot(_RecordingEngine):
        def process_one(self, msg, connector):
            raise RuntimeError("spot boom")

    spot = _BoomSpot("spot", own_listener=True, listener=listener)
    futures = _RecordingEngine("futures", own_listener=False)

    rsl.dispatch_cycle(spot, futures, object())

    assert spot.managed == 1
    assert futures.managed == 1


def test_dispatch_cycle_spot_only_when_futures_is_none():
    """Futures disabled => dispatch_cycle with futures=None still drains + spot.

    This is the regression-equivalent of today's spot-only tick().
    """
    canned_msg = "MSG"
    listener = _FakeListener(canned_msg)
    spot = _RecordingEngine("spot", own_listener=True, listener=listener)

    rsl.dispatch_cycle(spot, None, object())

    assert spot.processed == [(canned_msg, object())] or len(spot.processed) == 1
    assert spot.managed == 1


def test_main_wires_signal_engine_into_telegram_handler():
    """Guard: main() must attach the spot engine to the Telegram handler.

    Without this call, every signal control command (/signal_pause,
    /signal_resume, /signal_pnl, /signal_inject, /signal_close) replies
    'Signal engine not configured.' main() is async + network-bound so it
    can't be driven directly here; instead we assert the wiring call is
    present in main's source. This is an integration-glue guard, deliberately
    string-based — if the call is removed this test fails loudly.
    """
    import inspect

    source = inspect.getsource(rsl.main)
    assert "attach_signal_engines" in source, (
        "run_signal_listener.main() must call handler.attach_signal_engines(...) "
        "or all signal control commands stay dead ('Signal engine not configured.')"
    )
