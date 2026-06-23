"""Tests for SignalValidator — every validation path.

Currently 26% coverage. The validator is pure logic (no network/DB) — easy to
fully cover. Each test exercises a distinct rejection/acceptance path.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.signals.signal_validator import SignalValidator
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence


def _valid(**overrides):
    """A signal that passes all validation (entry=100, SL=95, TP3=115, R:R=3)."""
    base = dict(
        action=SignalAction.OPEN_LONG, pair="DOGE-USDT",
        entry_low=100.0, entry_high=100.0, stop_loss=95.0,
        take_profits=[105.0, 110.0, 115.0],
        confidence=SignalConfidence.HIGH, quality_score=8,
    )
    base.update(overrides)
    return ParsedSignal(**base)


def _validator(**cfg):
    defaults = dict(min_rr_ratio=1.5, max_sl_distance_pct=5.0,
                    max_entry_zone_pct=3.0, min_quality_score=5)
    defaults.update(cfg)
    return SignalValidator(defaults)


class TestSignalValidator:
    def test_valid_signal_passes(self):
        v, r = _validator().validate(_valid())
        assert v and r == "", f"should be valid: {r}"

    def test_not_a_signal_rejected(self):
        assert not _validator().validate(_valid(action=SignalAction.NOT_A_SIGNAL))[0]

    def test_close_signal_bypasses_validation(self):
        assert _validator().validate(_valid(action=SignalAction.CLOSE))[0]

    def test_blacklisted_pair_rejected(self):
        v, r = _validator(blacklisted_pairs=["DOGE-USDT"]).validate(_valid())
        assert not v and "blacklisted" in r

    def test_no_stop_loss_rejected(self):
        v, r = _validator().validate(_valid(stop_loss=None))
        assert not v and "stop-loss" in r.lower()

    def test_no_take_profits_rejected(self):
        v, r = _validator().validate(_valid(take_profits=[]))
        assert not v and "take-profit" in r.lower()

    def test_no_entry_rejected(self):
        v, r = _validator().validate(_valid(entry_low=None, entry_high=None))
        assert not v and "entry" in r.lower()

    def test_sl_above_entry_rejected(self):
        v, r = _validator().validate(_valid(stop_loss=105.0))
        assert not v and ">=" in r

    def test_sl_auto_tightened_when_too_far(self):
        s = _valid(stop_loss=80.0)  # 20% > 5% max → auto-tighten, not reject
        v, r = _validator().validate(s)
        assert v, f"should tighten, not reject: {r}"
        assert s.stop_loss > 80.0  # moved closer to entry

    def test_bad_rr_rejected(self):
        s = _valid(take_profits=[101.0, 102.0, 103.0])  # reward=3, risk=5, rr=0.6
        v, r = _validator().validate(s)
        assert not v and "R:R" in r

    def test_tp_below_entry_rejected(self):
        s = _valid(take_profits=[90.0, 95.0, 98.0])  # TP3 < entry
        v, r = _validator().validate(s)
        assert not v

    def test_entry_zone_too_wide_rejected(self):
        s = _valid(entry_low=100.0, entry_high=106.0)  # 6% zone > 3%
        v, r = _validator().validate(s)
        assert not v and "zone" in r.lower()

    def test_low_quality_rejected(self):
        s = _valid(quality_score=3)  # < 5
        v, r = _validator().validate(s)
        assert not v and "quality" in r.lower()

    def test_available_pairs_warning_does_not_reject(self):
        v = _validator()
        v.set_available_pairs({"ETHUSDT", "BTCUSDT"})  # DOGE not in set
        valid, _ = v.validate(_valid())
        assert valid  # warns but doesn't reject (exchange may list new pairs)


import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent))
from src.signals.signal_parser import ParsedSignal, SignalAction, SignalConfidence
from src.signals.signal_validator import SignalValidator

def _short():
    return ParsedSignal(action=SignalAction.OPEN_SHORT, pair="ETH-USDT",
        entry_low=3000.0, entry_high=3000.0, stop_loss=3150.0,
        take_profits=[2900.0, 2800.0], confidence=SignalConfidence.HIGH, quality_score=8)

def _cfg(allow_shorts=False):
    return {"min_rr_ratio": 1.0, "max_sl_distance_pct": 5.0,
            "max_entry_zone_pct": 3.0, "min_quality_score": 5,
            "allow_shorts": allow_shorts}

def test_spot_rejects_short_by_default():
    valid, reason = SignalValidator(_cfg()).validate(_short())
    assert valid is False and "short" in reason.lower()

def test_futures_accepts_valid_short_when_allowed():
    valid, reason = SignalValidator(_cfg(allow_shorts=True)).validate(_short())
    assert valid is True, reason   # entry 3000, SL 3150, TP 2800 → R:R = 200/150 = 1.33 ≥ 1.0

def test_short_rejected_when_sl_below_entry():
    sig = _short(); sig.stop_loss = 2900.0  # SL below entry — invalid for a short
    valid, reason = SignalValidator(_cfg(allow_shorts=True)).validate(sig)
    assert valid is False and "SL" in reason

def test_long_path_unchanged():
    sig = ParsedSignal(action=SignalAction.OPEN_LONG, pair="X-USDT", entry_low=100.0,
        entry_high=100.0, stop_loss=80.0, take_profits=[130.0],
        confidence=SignalConfidence.HIGH, quality_score=8)
    valid, _ = SignalValidator(_cfg()).validate(sig)
    assert valid is True
