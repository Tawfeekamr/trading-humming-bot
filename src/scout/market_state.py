"""Market state (pre-registration §5). Works on scalars or numpy arrays."""
from __future__ import annotations

import numpy as np

NO_TRADE, RISK_ON, RISK_OFF, NEUTRAL = 0, 1, 2, 3
NAMES = {NO_TRADE: "NO_TRADE", RISK_ON: "RISK_ON", RISK_OFF: "RISK_OFF", NEUTRAL: "NEUTRAL"}

MIN_MED_NATR = 0.0035
BREADTH_ON = 0.55
BREADTH_OFF = 0.45

SIZE_MULT = {NO_TRADE: 0.0, RISK_ON: 1.0, RISK_OFF: 1.0, NEUTRAL: 0.5}


def classify(breadth, btc_up, med_natr):
    """Return state code(s). NaN inputs → NO_TRADE (we never guess)."""
    breadth = np.asarray(breadth, dtype=float)
    btc_up_f = np.asarray(btc_up, dtype=float)
    med_natr = np.asarray(med_natr, dtype=float)
    valid = ~(np.isnan(breadth) | np.isnan(btc_up_f) | np.isnan(med_natr))
    up = btc_up_f > 0.5
    out = np.full(breadth.shape, NEUTRAL, dtype=np.int8)
    out[up & (breadth >= BREADTH_ON)] = RISK_ON
    out[~up & (breadth <= BREADTH_OFF)] = RISK_OFF
    out[med_natr < MIN_MED_NATR] = NO_TRADE
    out[~valid] = NO_TRADE
    return out if out.ndim else int(out)


def allows(state, side):
    """side: +1 long, -1 short. Works elementwise."""
    state = np.asarray(state)
    side = np.asarray(side)
    long_ok = (state == RISK_ON) | (state == NEUTRAL)
    short_ok = (state == RISK_OFF) | (state == NEUTRAL)
    return np.where(side > 0, long_ok, short_ok)


def size_mult(state):
    state = np.asarray(state)
    out = np.select([state == RISK_ON, state == RISK_OFF, state == NEUTRAL], [1.0, 1.0, 0.5], 0.0)
    return out if out.ndim else float(out)
