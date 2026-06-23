"""Tests for DeepSeek entry-tuning salvage.

When a signal's price has moved outside the entry zone, DeepSeek decides IN THE
SAME PARSE CALL whether the signal is still tunable (entry shifted to the live
price, original SL/TPs kept) or stale (the move already happened). Scope is the
entry-zone case only.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from src.signals.signal_parser import (  # noqa: E402
    SignalParser,
    ParsedSignal,
    SignalAction,
    SignalConfidence,
    _extract_candidate_pair,
)
from src.signals.signal_validator import SignalValidator  # noqa: E402


# ── Pre-scan regex ──────────────────────────────────────────────────────────

class TestExtractCandidatePair:
    def test_slash_usdt(self):
        assert _extract_candidate_pair("AERO/USDT ENTRY 0.472-0.475") == "AERO-USDT"

    def test_dollar_prefix(self):
        assert _extract_candidate_pair("COIN: $HYPE/USDT ENTRY 56.80") == "HYPE-USDT"

    def test_dash_separator(self):
        assert _extract_candidate_pair("BNB-USDT buy zone below") == "BNB-USDT"

    def test_no_ticker_returns_none(self):
        assert _extract_candidate_pair("general market commentary, no pair here") is None


# ── Parser: tuning fields + live price in prompt ────────────────────────────

class TestParserTuning:
    def _parser_returning(self, response_dict):
        p = SignalParser(api_key="fake")
        p._call_glm = lambda prompt: response_dict  # bypass real HTTP
        return p

    def test_reads_entry_tuned(self):
        resp = {"action": "OPEN_LONG", "pair": "AERO-USDT", "entry_low": 0.478,
                "entry_high": 0.478, "stop_loss": 0.45, "take_profits": [0.50, 0.52],
                "confidence": "medium", "quality_score": 7, "quality_reason": "tuned",
                "reasoning": "entry tuned to live price", "entry_tuned": True, "stale": False}
        sig = self._parser_returning(resp).parse("AERO/USDT ...", live_price=0.478, live_pair="AERO-USDT")
        assert sig.entry_tuned is True
        assert sig.stale is False

    def test_reads_stale(self):
        resp = {"action": "OPEN_LONG", "pair": "AERO-USDT", "entry_low": 0.472,
                "entry_high": 0.475, "stop_loss": 0.45, "take_profits": [0.50],
                "confidence": "low", "quality_score": 4, "quality_reason": "stale",
                "reasoning": "price already at TP1", "entry_tuned": False, "stale": True}
        sig = self._parser_returning(resp).parse("AERO/USDT ...", live_price=0.50, live_pair="AERO-USDT")
        assert sig.stale is True
        assert sig.entry_tuned is False

    def test_defaults_when_fields_absent(self):
        resp = {"action": "OPEN_LONG", "pair": "AERO-USDT", "entry_low": 0.472,
                "entry_high": 0.475, "stop_loss": 0.45, "take_profits": [0.50]}
        sig = self._parser_returning(resp).parse("AERO/USDT ...")
        assert sig.entry_tuned is False
        assert sig.stale is False

    def test_live_price_passed_into_prompt(self):
        captured = {}
        p = SignalParser(api_key="fake")
        p._call_glm = lambda prompt: captured.__setitem__("prompt", prompt) or {"action": "NOT_A_SIGNAL"}
        p.parse("AERO/USDT ENTRY 0.472", live_price=0.478, live_pair="AERO-USDT")
        assert "0.478" in captured["prompt"]
        assert "AERO" in captured["prompt"]

    def test_no_live_price_omits_context(self):
        captured = {}
        p = SignalParser(api_key="fake")
        p._call_glm = lambda prompt: captured.__setitem__("prompt", prompt) or {"action": "NOT_A_SIGNAL"}
        p.parse("AERO/USDT ENTRY 0.472")
        assert "live_price" not in captured["prompt"].lower()


# ── Validator: tuned signals keep the original SL ───────────────────────────

class TestValidatorTunedSl:
    def _cfg(self):
        return {"min_rr_ratio": 1.0, "max_sl_distance_pct": 5.0,
                "max_entry_zone_pct": 3.0, "min_quality_score": 5}

    def test_tuned_signal_keeps_wide_sl(self):
        # entry 0.50, SL 0.40 → 20% distance (>5% max). Tuned → keep SL.
        sig = ParsedSignal(action=SignalAction.OPEN_LONG, pair="AERO-USDT",
                           entry_low=0.50, entry_high=0.50, stop_loss=0.40,
                           take_profits=[0.60], quality_score=8, entry_tuned=True)
        SignalValidator(self._cfg()).validate(sig)
        assert sig.stop_loss == 0.40, "tuned signal must keep original SL"

    def test_non_tuned_signal_tightens_wide_sl(self):
        sig = ParsedSignal(action=SignalAction.OPEN_LONG, pair="AERO-USDT",
                           entry_low=0.50, entry_high=0.50, stop_loss=0.40,
                           take_profits=[0.60], quality_score=8, entry_tuned=False)
        SignalValidator(self._cfg()).validate(sig)
        assert sig.stop_loss > 0.40, "non-tuned wide SL must be tightened"
