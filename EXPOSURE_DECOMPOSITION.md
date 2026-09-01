CANNOT DETERMINE — the committed artifacts do not contain the joint per-bar action/position traces required to distinguish structural non-fills from learned abstention.

# Exposure Decomposition

## Scope and decisive limitation

The available diagnostic report stores marginal action distributions and aggregate position-size statistics, but not the per-bar pairing of selected action with position value. The diagnostic script collects `actions` and `pos_vals` in memory at `scripts/diagnose_exposure.py:55-76`, but its persisted JSON contains only marginal distributions and aggregate binding values at `scripts/diagnose_exposure.py:168-205`. For the untrained policy, the script explicitly discards the worker result after retaining only actions at `scripts/diagnose_exposure.py:153-155`; it never aggregates untrained position values.

Therefore the requested A/B/C classification, grid fill rate, untrained capital-weighted exposure, and numeric attainable ceiling cannot be recovered from existing artifacts without re-evaluating policies. The instruction prohibits that re-evaluation.

## Task 1 — Inactive-bar decomposition

### Available category A counts

The action distributions establish explicit FLAT selection, but not whether active actions held zero inventory.

| Pair / policy | Total action observations | A: FLAT | Active actions B+C | A percentage |
|---|---:|---:|---:|---:|
| ETH trained | 4,320 | 1,812 | 2,508 | 41.944% |
| BNB trained | 4,320 | 1,980 | 2,340 | 45.833% |
| ETH untrained | 4,220 | 10 | 4,210 | 0.237% |
| BNB untrained | 4,320 | 1 | 4,319 | 0.023% |

The trained action distributions and `n_steps` are stored at `reports/exposure_diagnosis.json:8-38` and `:47-76`. The untrained ETH denominator is 4,220, recoverable from the exact persisted action proportions and the script’s `count / len(actions)` calculation at `scripts/diagnose_exposure.py:168-182`; `n_steps` in the JSON is the trained-action count only at `scripts/diagnose_exposure.py:177-180`.

### Categories B and C

**CANNOT DETERMINE.** The artifacts do not record, for each active action, whether `position_value == 0` or `position_value > 0`. Consequently:

- B: active action with zero position — unavailable;
- C: active action with non-zero position — unavailable;
- the B breakdown by grid multiplier, trend, and swing — unavailable;
- the fraction of zero-exposure bars attributable to A versus B — unavailable.

The marginal active-action counts are recoverable, but they do not solve the missing joint classification. For example, ETH trained has 717 grid-0.5, 1,147 grid-1.0, 520 grid-1.5, 39 swing-0.5, 36 swing-1.5, 37 trend-0.5, 1 trend-1.0, and 11 trend-1.5 selections (`reports/exposure_diagnosis.json:19-30`). No persisted field identifies which of those selections had a fill.

The reported trained capital exposures are 4.2677% ETH and 5.24696% BNB, with time-in-market 58.0787% and 54.1898%, respectively (`reports/exposure_definitions.json:2-9,16-23`). Those aggregates do not identify the A/B/C split.

## Task 2 — Grid fill mechanics

### 1. Condition required to acquire inventory

On grid activation, the environment creates buy and sell levels but does not buy immediately: `src/rl/env.py:443-458`. Inventory is acquired only when a buy level is crossed by the current bar:

```python
if low <= bl <= high:
    units = level_notional / bl
    ...
    inventory += units
```

This condition is at `src/rl/env.py:475-484`. Sell-level crossings are handled separately at `:487-495`.

### 2. Fraction of grid-selected bars with a crossed level

**CANNOT DETERMINE.** No persisted artifact records grid-selected bars together with bar high/low crossings or per-bar inventory changes. `reports/exposure_diagnosis.json` stores only action proportions and aggregate binding statistics (`reports/exposure_diagnosis.json:19-39,58-78`). The diagnostic’s transient `pos_vals` and `actions` are not written to the report (`scripts/diagnose_exposure.py:153-186,197-205`).

### 3. Mean and median position value on grid-selected bars

**CANNOT DETERMINE.** The reported binding mean/median is not a grid-conditioned position statistic. The script appends `position_value / max_notional` for every trained non-flat action with a positive denominator at `scripts/diagnose_exposure.py:162-165`, then aggregates all positive values without retaining the action label at `:175-186`. It combines grid, trend, and swing bars. The stored values are therefore not the requested grid-only mean and median.

For reference, the stored aggregate values are ETH mean 0.7337 / median 0.6062 and BNB mean 0.8298 / median 0.6051 (`reports/exposure_diagnosis.json:3-6,42-46`). They cannot be interpreted as grid fill statistics.

### 4. Does selecting grid deploy capital immediately?

**No.** Initial grid selection creates the level ladder and marks it deployed at `src/rl/env.py:443-458`; it does not create inventory or turnover. Capital enters only through the crossing condition at `src/rl/env.py:475-484`. A previously existing grid inventory could persist while the same grid engine remains active, but selecting or activating an otherwise empty grid does not itself buy.

## Task 3 — Untrained control

### 1. Untrained A/B/C breakdown

Only category A is available:

- ETH untrained: 10 FLAT selections out of 4,220 observations, 0.237%; active selections: 4,210;
- BNB untrained: 1 FLAT selection out of 4,320 observations, 0.023%; active selections: 4,319.

The proportions are stored at `reports/exposure_diagnosis.json:31-39,69-76`. Categories B and C are **CANNOT DETERMINE** because the script drops untrained position traces at `scripts/diagnose_exposure.py:153-155`.

### 2. Untrained capital-weighted exposure

**CANNOT DETERMINE.** No untrained `position_value` array or capital-weighted exposure aggregate is stored. The untrained branch retains only `res["actions"]` and skips the position aggregation path at `scripts/diagnose_exposure.py:153-165`. The available report contains trained binding statistics only (`reports/exposure_diagnosis.json:3-6,42-46`).

### 3. Decisive comparison

The existing evidence does **not** show whether the untrained policy has substantially higher capital exposure. Its nearly zero FLAT share shows that it selects active actions, but active selection is not equivalent to filled inventory under the grid mechanics. Because untrained exposure is absent, the structural alternative remains live and cannot be excluded.

No claim that the untrained policy achieved higher capital exposure is made here. The repository does not contain the required measurement.

## Task 4 — Attainable exposure ceiling

### Primitive mechanics

Trend and swing can enter immediately when activated. Trend computes entry notional as:

```python
notional = self.equity * cfg.max_position_pct * state["size_mult"]
```

at `src/rl/env.py:513-533`. Swing uses the same immediate-entry structure at `src/rl/env.py:559-583`. With `max_position_pct=0.6666` and size multiplier 1.5, their nominal entry notional is 0.9999 of current equity (`src/rl/env.py:74-79`; `src/rl/action_map.py:15-25`).

Grid does not have an equivalent finite enforced total-position cap. Every qualifying buy-level crossing adds another `units` allocation at `src/rl/env.py:475-484`; the code does not limit cumulative inventory to five purchases or to 75% of equity. The diagnostic’s grid denominator `5 * grid_level_pct * size_mult * equity` is only a nominal comparison value at `scripts/diagnose_exposure.py:68-75`, not an attainable-ceiling invariant.

### Numeric ceiling

**CANNOT DETERMINE.** There is no single finite primitive-level attainable capital-weighted exposure ceiling supported by the code:

- trend/swing have an immediate nominal entry size of up to 99.99% of equity, but mark-to-market position value can change with price;
- grid can accumulate inventory across repeated crossings without a total inventory cap;
- the fixed evaluation data and all possible action sequences were not exhaustively evaluated, and no per-bar traces are persisted.

Therefore the trained 4.2677% ETH / 5.24696% BNB exposures cannot be expressed as a verified percentage of a computed attainable ceiling. Comparing them to the nominal 67% trend cap is not valid for grid actions.

### RF baseline comparison

The RF capital-weighted exposures are 21.0986% ETH and 60.5434% BNB (`reports/exposure_definitions.json:7-9,21-23`). RF is not using a separate execution engine: the walk-forward evaluator runs `SupervisedRegimeRouter` through the same `TradingEnv` at `src/rl/walk_forward.py:419-427`. The RF router maps RANGING to grid-1.0, TRENDING to trend-1.0, and DANGER to flat at `src/rl/router.py:75-80`.

Thus RF is subject to the same grid fill mechanics, but it selects a narrower action set than PPO: grid-1.0, trend-1.0, or flat. The RF/PPO exposure comparison is not unfair because of different environment fill mechanics; it is still not a comparison against a verified common maximum because no finite common ceiling was computed.

## Task 5 — Verdict

**CANNOT DETERMINE between (i), (ii), and (iii) from the committed evidence.** The available numbers establish explicit FLAT selection for the trained policy and almost no FLAT selection for the untrained policy. They do not establish the untrained policy’s capital exposure or the active-action B/C split for either policy.

The decisive missing measurement is Task 3: `scripts/diagnose_exposure.py:153-165` discards untrained position traces. Task 1 is also incomplete because the persisted report lacks action-position pairs. Without those traces, the evidence cannot distinguish:

- learned abstention with active actions filling normally;
- structural low deployment caused by unfilled grid/trend primitives; or
- a mixed decomposition.

### Status of the manuscript claim

The current manuscript claim that learned withdrawal is established and that the configuration ceiling is excluded is **not supported as written**. The data support only the narrower statement that the trained deterministic policy selected FLAT frequently: 41.944% of ETH observations and 45.833% of BNB observations, versus 0.237% and 0.023% for the untrained controls. The causal decomposition into learned abstention versus structural non-filling requires measurements that are absent from the committed artifacts.
