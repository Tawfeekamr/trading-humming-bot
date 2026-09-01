# Flat Baseline Verification

## Verdict

**The 19-of-20 claim holds, and the stored `flat_outscored` flags are computed correctly from the stored rounded rewards.** All 20 trained artifacts exist and are connected to the corresponding seed/fold paths in the verification script. One material reporting discrepancy remains: the stored flat rewards equal the negative **sum of per-bar buy-and-hold returns**, not the negative **compounded** buy-and-hold return. That discrepancy does not change the within-cell 19-of-20 ordering because trained and flat policies use the same summed environment reward, but it must not be described as a compounded return.

## Check 1 — Timestamp identity: PASS

The evaluation frame is constructed once per pair/fold at `scripts/flat_vs_trained_by_seed.py:62-73`. The same `tmp` CSV is passed to the worker for every seed at `:76-86`. Inside the worker, one `TradingEnv` is constructed from that frame at `:35-39`; the trained policy runs first at `:41-48`, then the same environment is reset and the flat action is run at `:50-56`.

The exact emitted real-bar ranges are confirmed by the committed TA return CSVs. The environment emits 719 real returns after the 100-bar warmup; the final padding step has zero reward and no real bar.

| Cell | Trained policy range | Flat policy range | Rows each |
|---|---|---|---:|
| ETHUSDT fold 3 | 2025-10-10 23:00 UTC → 2025-11-09 21:00 UTC | identical frame and reset | 719 |
| BNBUSDT fold 0 | 2025-01-13 23:00 UTC → 2025-02-12 21:00 UTC | identical frame and reset | 719 |

Evidence for the endpoints: `reports/returns/ETHUSDT_ta_fold3.csv:1-2,720` and `reports/returns/BNBUSDT_ta_fold0.csv:1-2,720`. The frame construction and shared environment path are `scripts/flat_vs_trained_by_seed.py:35-56,62-86`.

**Result:** identical bars, zero-bar discrepancy, zero calendar-time discrepancy.

## Check 2 — Same reward function: PASS

The trained policy path is:

1. Load the seed/fold model through `PPORouter` at `scripts/flat_vs_trained_by_seed.py:41-42`.
2. Predict an action at `:44`.
3. Pass that action to the real `TradingEnv.step()` at `:46`.
4. Sum the returned environment reward at `:47`.

The flat policy path is:

1. Reset the same environment at `scripts/flat_vs_trained_by_seed.py:50-51`.
2. Pass discrete action `9` to the real `TradingEnv.step()` at `:53-55`.
3. Sum the returned environment reward at `:55`.

Both use the same `EnvConfig(window_length=len(test_df), warmup_bars=100)` at `scripts/flat_vs_trained_by_seed.py:35-39`. The defaults are `fee_rate=0.001`, `lambda_dd=0.5`, and `warmup_bars=50`; the script overrides warmup to 100 while leaving fee and lambda unchanged, as defined at `src/rl/env.py:65-90`.

Both reward paths execute `src/rl/env.py:268-373`. Equity, fees, drawdown, benchmark return, explicit fee term, and drawdown term are all computed by that same environment at `src/rl/env.py:330-357`.

**Result:** no separate hand-written flat reward formula is used. Configuration is identical between the two policies: same frame, same `EnvConfig`, same fee rate, same lambda, and same warmup.

## Check 3 — Is “flat” genuinely flat: PASS

Action 9 is the canonical flat action: `src/rl/action_map.py:14-25` maps it to `("flat", 0.0)`.

On action 9, `src/rl/env.py:298-308` calls `_close_engine`, sets the current engine to `flat`, sets the size multiplier to zero, clears engine state, and resets the engine bar count. The flat engine returns `(0.0, 0.0)` at `src/rl/env.py:379-400`.

The environment reset starts with `current_engine="flat"`, initial flat state, and zero turnover at `src/rl/env.py:247-258`. `_close_engine` returns zero for a flat engine because only trend, swing, or grid state can contribute turnover at `src/rl/env.py:402-424`. Therefore repeated action 9 neither opens a position nor accumulates grid inventory.

**Transaction cost:** zero. The flat path starts with no open position and `_close_engine` returns zero on every flat step.

**Total turnover:** exactly zero by the executed code path. The JSON artifact does not store a flat turnover field, so there is no independent persisted turnover scalar to quote; the exact zero follows from the reset state, action-9 branch, and flat-engine branch cited above.

## Check 4 — Hand reproduction of flat reward: FAIL for compounded-return interpretation; implementation arithmetic is verified

For a flat bar, reset equity remains unchanged, turnover is zero, and drawdown increment is zero. The reward therefore reduces to the negative close-to-close benchmark return:

```text
reward = (0 - R_bh) - fee_rate * 0 - lambda_dd * 0
       = -R_bh
```

The code implements this at `src/rl/env.py:330-357`: equity is updated at `:333-334`, drawdown increment is calculated at `:338-343`, benchmark return at `:351-353`, and the final reward at `:354-357`.

The stored flat reward is the sum of those per-bar rewards. The committed per-bar CSVs contain simple close-to-close returns, not log returns. Independent aggregation over the exact 719 timestamps gives:

| Cell | Negated arithmetic sum of CSV returns | Negated compounded CSV return | Stored `flat_reward` | Difference: stored − compounded |
|---|---:|---:|---:|---:|
| ETHUSDT fold 3 | `+0.0528748232` → stored `+0.0529` | `+0.0711085863` | `+0.0529` | `−0.0182085863` |
| BNBUSDT fold 0 | `−0.0363891352` → stored `−0.0364` | `−0.0159363711` | `−0.0364` | `−0.0204636289` |

Source ranges: `reports/returns/ETHUSDT_ta_fold3.csv:2-720` and `reports/returns/BNBUSDT_ta_fold0.csv:2-720`. Stored ETH fold-3 values are at `reports/flat_vs_trained_by_seed.json:48-90`; stored BNB fold-0 values are at `reports/flat_vs_trained_by_seed.json:92-135`.

The stored values match the negated **arithmetic sums** to four decimals. They do not match negated compounded returns. The divergence is explained by the aggregation convention, not by a drawdown term: the flat path has zero equity drawdown and zero turnover by Check 3. The environment sums per-step rewards in `scripts/flat_vs_trained_by_seed.py:43-55`; it does not compound the benchmark returns when producing `flat_reward`.

**Result:** the flat baseline is internally computed according to the implemented reward sum, but the compounded-return comparison requested here fails. This discrepancy is reported and unfixed.

## Check 5 — Determinism of flat baseline: PASS, with a fixed reset seed

The JSON repeats one flat value within each cell:

- ETH fold 0: `0.1184`, rows shown at `reports/flat_vs_trained_by_seed.json:3-27` and subsequent fold-0 rows.
- ETH fold 3: `0.0529`, repeated across all five ETH fold-3 rows.
- BNB fold 0: `-0.0364`, repeated at `reports/flat_vs_trained_by_seed.json:101-135`.
- BNB fold 3: `0.0801`, repeated at `reports/flat_vs_trained_by_seed.json:137-171`.

The script asserts this directly at `scripts/flat_vs_trained_by_seed.py:98-102`.

There is a seeded environment reset at `scripts/flat_vs_trained_by_seed.py:50-51`, and `TradingEnv.reset(seed=42)` passes that seed to Gymnasium at `src/rl/env.py:241-245`. Window selection is seed-controlled in principle at `src/rl/env.py:618-628`, but the test frame is exactly the episode window, so `_pick_window_start` falls back to the fixed warmup index rather than selecting among multiple windows (`src/rl/env.py:623-628`). No stochastic policy is used for the flat path; action 9 is supplied directly at `scripts/flat_vs_trained_by_seed.py:53-55`.

**Result:** flat rewards are deterministic within each cell. The fixed reset seed is part of the path, but it does not create cross-seed variation because it is always 42 and the frame length forces the same start.

## Check 6 — Trained rewards: PASS for artifact selection and deterministic inference

The script constructs the exact artifact path from pair, fold, and seed at `scripts/flat_vs_trained_by_seed.py:76-77`, checks that it exists at `:78-80`, passes it to the worker at `:81-86`, and loads it through `PPORouter` at `:41-42`. All 20 expected `.zip` artifacts exist in `models/rl/`.

Deterministic inference is explicit in `src/rl/router.py:29-37`, where `PPORouter.predict` calls `self.model.predict(obs, deterministic=True)`.

Two stored output spot checks:

- ETHUSDT fold 0 seed 42: `trained_reward=0.0066`, `flat_reward=0.1184`, difference `-0.1118`, flag `true` at `reports/flat_vs_trained_by_seed.json:3-9`. Its model path and provenance are recorded at `models/rl/_seed_ETHUSDT_f0_s42.json:1-18`.
- BNBUSDT fold 3 seed 999: `trained_reward=0.0944`, `flat_reward=0.0801`, difference `+0.0143`, flag `false` at `reports/flat_vs_trained_by_seed.json:173-180`. Its model path and provenance are recorded at `models/rl/_seed_BNBUSDT_f3_s999.json:1-18`.

No model was re-run for this check.

## Check 7 — Exception cell: PASS

BNBUSDT fold 3 seed 999 uses the same loop, worker, environment, reset, and reward path as every other row. There is no conditional branch for the exception in `scripts/flat_vs_trained_by_seed.py:62-95`; only the artifact path changes with the seed.

The exception uses `models/rl/_seed_BNBUSDT_f3_s999.zip`, with provenance at `models/rl/_seed_BNBUSDT_f3_s999.json:1-18`: seed 999, 1,000,000 timesteps, lambda 0.5, the same PPO hyperparameters, and the same recorded training data length as the other seed artifacts.

Its outcome is the only anomaly: trained reward `+0.0944` exceeds flat reward `+0.0801` by `+0.0143`, so `flat_outscored=false` at `reports/flat_vs_trained_by_seed.json:173-180`. Nothing anomalous in code path, configuration, or artifact handling was found.

## Check 8 — Independent recount: PASS

Loading all 20 rows gives:

```text
rows = 20
flat_outscored = true: 19
flat_outscored = false: 1
```

Independently evaluating `trained_reward < flat_reward` for every row produces the same boolean as the stored `flat_outscored` field for all 20 rows. No arithmetic/flag mismatch exists.

The BNB fold-3 seed-999 row is the sole false result, as shown at `reports/flat_vs_trained_by_seed.json:173-180`. The BNB fold-0 rows all remain true at `:101-135`.

## Plain-language conclusion

The central count is real: the file contains 20 seed/fold/asset comparisons, and in 19 of them the flat policy received a higher total reward than the trained policy. The trained models are correctly selected, evaluated deterministically, and compared with the flat action on the same bars and through the same environment. The one important qualification is numerical: the flat reward is a sum of hourly reward values, so it matches the negative sum of hourly buy-and-hold returns. It is not the negative compounded buy-and-hold return. That compounding discrepancy changes the absolute reward interpretation but does not change which policy wins within any of the 20 stored cells.
