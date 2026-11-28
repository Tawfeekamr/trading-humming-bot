# Scout Bot — Phase 1 conclusion (2026-11-28)

**Verdict: NO-GO for both detectors.** Phase 2 (shadow) does not start. Rules were
fixed beforehand in `PREREGISTRATION.md` (commit 2695bd6). Full numbers are in
`WALK_FORWARD_RESULT.md` / `.json` (code commit 6af6816).

## What happened (out-of-sample, 2023-07 → 2026-09, 524 coins incl. delisted)

| | Breakout (A) | Pullback (B) |
|---|---|---|
| Trades | 632 | 2,730 |
| Average result per trade | −0.09R | −0.13R |
| Average **before** costs (approx.) | −0.07R | −0.06R |
| Random entries, same exits (1,000 runs) | −0.10R | −0.15R |
| p-value vs random | 0.40 | 0.08 |
| Positive quarters | 3 of 13 | 2 of 13 |
| Criteria passed | 1 of 7 (trade count) | 1 of 7 (trade count) |

## What it means, in plain words

1. **The setups don't predict direction.** They lose even before fees, and they do no
   better than random entries with the same stop/target rules. Breakout is
   indistinguishable from random (p = 0.40). Pullback is slightly less bad than random
   (p = 0.08), but still loses money.
2. **It is not a cost problem.** Costs take about 0.04R (breakout) and 0.07R (pullback)
   per trade. Removing them still leaves both negative.
3. **The exit ladder can't rescue a bad entry.** Stop exits average −1.03 to −1.07R and full
   TP3 exits about +1.9R. That's the expected shape, but stops happen about 3× more often than TP3.
4. **Market backdrop:** over this period BTC returned +174% while an equal-weight basket of
   the top-60 alts lost about 80%. Even "strong vs BTC" alt longs mostly lost. Shorts did
   better than longs (breakout shorts ≈ −0.01R), but not enough to pass.

## Checks done before trusting the result

- 54 unit/pipeline tests (no-lookahead, live-ladder parity, stop-first bar rule, portfolio rules).
- R by exit type matches the design (stop ≈ −1R − costs, TP3 ≈ +2R − costs).
- Dry run and official run give identical trade lists. Only the null's sample size differs.

## Options (each would be a NEW pre-registration — no retuning these two)

- **Stop here.** This matches the repo's other findings (MR, swing, PPO): simple TA
  rules on crypto show no robust edge after costs. The time goes to the dissertation.
- **Short-side-only test** on breakdowns in `RISK_OFF`. Only if pre-registered fresh,
  because the idea came from looking at this result.
- **Different signal family** (funding-rate / open-interest crowding, Phase 4 ideas),
  with a new pre-registration and the same gate.
