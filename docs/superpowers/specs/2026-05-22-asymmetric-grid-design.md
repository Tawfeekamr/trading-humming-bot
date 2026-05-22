# Asymmetric Grid Spacing & Sizing

## Summary

Modify `GridManager.calculate_grid` to use geometric scaling for buy-side levels: spacing widens and order size increases as price drops. This is a DCA-down approach that lowers breakeven faster during dips while reducing initial capital burn.

## Scope

- **File:** `src/grid/grid_manager.py`
- **Direction:** Buy-side only. Sell levels remain uniform.
- **Config:** Fixed constants (`SPACING_FACTOR=0.10`, `SIZE_FACTOR=0.08`). Not configurable via env vars yet.

## Current Behavior

Uniform grid — every buy level uses identical spacing and order size:
```
Spacing: buy_spacing × i   (linear)
Size:    deployable / (levels × 2)   (equal per level)
```

## New Behavior

Geometric scaling on buy levels:
```
Spacing_n = buy_spacing × (1 + α)^n    where α = 0.10
Size_n    = base_buy_value × (1 + β)^n  where β = 0.08
```

Capital normalization ensures total buy spending = half of deployable capital:
```
base_buy_value = deployable_buy / Σ((1 + β)^n) for n in 1..levels
```

Sell levels unchanged.

## Example (4 levels, mid=$650, buy_spacing=$5, deployable_buy=$2500)

| Level | Uniform Price | Asymmetric Price | Uniform Size | Asymmetric Size |
|-------|--------------|------------------|-------------|-----------------|
| 1     | $645.00      | $644.50          | $9.30       | $8.65           |
| 2     | $640.00      | $638.95          | $9.38       | $9.34           |
| 3     | $635.00      | $632.84          | $9.45       | $10.09          |
| 4     | $630.00      | $626.15          | $9.52       | $10.90          |

Inner levels buy less at higher prices. Outer levels buy more at lower prices.

## Implementation

1. Add class constants `SPACING_FACTOR` and `SIZE_FACTOR`
2. Replace the uniform buy loop with geometric calculation
3. Compute `base_buy_value` from the geometric series sum
4. Keep sell loop unchanged
5. Update existing tests
