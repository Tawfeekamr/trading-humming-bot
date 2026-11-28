# Scout Bot — v3 funding / open-interest conclusion (2026-11-28)

**Verdict: NO-GO for both detectors** under `PREREGISTRATION_FUNDING.md` (commit 6403186).
Results: `FUNDING_RESULT_C.md` and `FUNDING_RESULT_D.md` (code 6538b54).
Filenames deviate from pre-registration §8 (`FUNDING_RESULT.md`): one file per detector,
because C and D have different test periods. Content is as specified.

## Results

| | C: funding fade | D: funding fade + OI build-up |
|---|---|---|
| Test period | 2021-01 → 2026-09 | 2022-07 → 2026-09 |
| Trades | 1,597 | 275 |
| Average per trade | −0.20R | −0.18R |
| Before costs (approx.) | −0.14R | — |
| Random entries, same exits | −0.19R (p = 0.67) | −0.21R (p = 0.33) |
| Positive quarters | 13% | 35% |
| First half / second half | −0.08R / −0.24R | −0.12R / −0.22R |
| Criteria passed | 1 of 8 | 1 of 8 |

All 9 grid settings were negative for both detectors (−0.17R to −0.24R), so this isn't one
unlucky parameter choice.

## What it means, in plain words

1. **Betting against crowded funding didn't work.** Extreme funding, in either direction,
   didn't predict a reversal once the bar turned. The trades did no better than random
   entries with the same exits.
2. **Adding "the crowd is still piling in" (OI up 10%+) didn't help.** It cut the trade count
   by about 6× but left the average the same.
3. **Not a cost problem.** Detector C loses about 0.14R per trade even before fees.
4. **Both halves lose**, so it isn't one bad regime either.

## Checks

- 72 tests, including print timing (an 08:00 settlement is first usable at the close of the
  07:00 bar) and OI staleness (snapshot ≤ 30 min old).
- Earlier v1/v2 results reproduce exactly with the v3 code.
- Exit types have the expected shape (stop ≈ −1.05R, TP3 ≈ +1.9R).
- OI coverage: 3,954 of 3,958 candidate coin-times had valid OI. 1 file was missing.

## Decision (as pre-registered)

Both fail → **the scout project stops.** Across three pre-registered tests (v1 breakout +
pullback, v2 breakdown shorts, v3 funding/OI crowding), no detector passed. The best result
(v2 shorts, +0.15R on 2021–23) was mostly the 2022 crash and wasn't distinguishable from
random shorts. No relaxed re-tests.
