"""Design analysis (Gelman & Carlin, 2014) at literature-anchored effect sizes.

Writes reports/design_analysis.json. The standard error used is the SE of the
per-bar mean return difference that also produces the minimum detectable
effect in reports/mde_power.json:  SE = MDE_per_bar / (z_{1-a/2} + z_{power}).

Correction (2026-11-26): the earlier design_analysis.json was computed by hand
and multiplied the effect by sqrt(n) a second time (z = delta*sqrt(n)/SE with
an SE that is already the SE of the mean), which reported power 0.73 / 0.32 at
the 1.7 pp/yr anchor. That contradicted its own n_required (decades to
millennia of data for 80% power). The correct power at n = 4,314 is ~0.05.

    python scripts/design_analysis.py
"""
from __future__ import annotations

import json
from pathlib import Path

from scipy.stats import norm

ALPHA, POWER = 0.05, 0.80
BARS_PER_YEAR = 24 * 365
ANCHORS = {
    "A1_sp500_RL_1.7pp_CAGR": {
        "source": "Xue & Ye (2025), arXiv:2510.06466: PPO vs buy-and-hold ~1.7 pp CAGR, S&P 500 2020-2025",
        "annual_effect": 0.017,
    },
    "A2_crypto_10pp_CAGR": {
        "source": "order-of-magnitude anchor for published positive crypto-RL results; stated assumption",
        "annual_effect": 0.10,
    },
}


def retrodesign(effect: float, se: float, alpha: float = ALPHA) -> dict:
    """Power, Type S and Type M for a two-sided z-test (Gelman & Carlin, 2014)."""
    c = norm.ppf(1 - alpha / 2) * se
    hi, lo = (c - effect) / se, (-c - effect) / se
    p_hi, p_lo = norm.sf(hi), norm.cdf(lo)
    power = p_hi + p_lo
    # E[|X| ; |X| > c] for X ~ N(effect, se^2), via normal partial expectations
    e_hi = effect * p_hi + se * norm.pdf(hi)
    e_lo = -effect * p_lo + se * norm.pdf(lo)
    return {
        "power_at_this_n": power,
        "type_S": p_lo / power,
        "type_M_factor": (e_hi + e_lo) / power / effect,
    }


def main() -> int:
    mde = json.loads(Path("reports/mde_power.json").read_text())["per_bar_basis"]
    z = norm.ppf(1 - ALPHA / 2) + norm.ppf(POWER)
    out = {
        "method": "Gelman & Carlin (2014) design analysis; two-sided z-test; SE of the per-bar mean "
                  "difference = MDE_per_bar / (z_0.975 + z_0.80) from reports/mde_power.json (HAC)",
        "correction_2026_11_26": "earlier version double-counted sqrt(n) and reported power 0.73/0.32; "
                                 "recomputed here (power ~0.05 at n = 4,314)",
        "anchors": {},
    }
    for key, anchor in ANCHORS.items():
        effect = anchor["annual_effect"] / BARS_PER_YEAR
        entry = {"source": anchor["source"], "per_bar_effect": effect}
        for pair in ("ETHUSDT", "BNBUSDT"):
            n = mde[pair]["n"]
            se = mde[pair]["mde_per_bar"] / z
            n_req = n * (mde[pair]["mde_per_bar"] / effect) ** 2
            entry[pair] = {
                "n": n,
                "se_per_bar_mean_diff": se,
                "n_required_bars": round(n_req),
                "n_required_years": round(n_req / BARS_PER_YEAR),
                **{k: round(v, 4) for k, v in retrodesign(effect, se).items()},
            }
        out["anchors"][key] = entry
    Path("reports/design_analysis.json").write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
