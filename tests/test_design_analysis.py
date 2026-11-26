"""Pins the corrected design analysis: power, Type S and Type M must be
internally consistent (the earlier hand computation double-counted sqrt(n))."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("scipy")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from design_analysis import retrodesign  # noqa: E402
from scipy.stats import norm  # noqa: E402


def test_power_is_80_percent_at_the_minimum_detectable_effect():
    se = 1.0
    mde = (norm.ppf(0.975) + norm.ppf(0.80)) * se
    assert retrodesign(mde, se)["power_at_this_n"] == pytest.approx(0.80, abs=0.005)


def test_tiny_effect_has_power_near_alpha_and_huge_exaggeration():
    r = retrodesign(effect=0.04, se=1.0)  # z = 0.04, like the 1.7 pp/yr ETH anchor
    assert r["power_at_this_n"] == pytest.approx(0.05, abs=0.002)
    assert 0.4 < r["type_S"] < 0.5
    assert r["type_M_factor"] > 40


def test_large_effect_has_no_sign_error_and_no_exaggeration():
    r = retrodesign(effect=10.0, se=1.0)
    assert r["power_at_this_n"] == pytest.approx(1.0, abs=1e-6)
    assert r["type_S"] < 1e-12
    assert r["type_M_factor"] == pytest.approx(1.0, abs=0.01)
