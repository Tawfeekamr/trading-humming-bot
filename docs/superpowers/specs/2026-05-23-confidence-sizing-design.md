# Confidence-Weighted Position Sizing

## Problem

Trend engine risks a flat 2% of capital on every trade regardless of signal quality. Grid engine deploys 100% capital regardless of ML regime confidence. ML confidence (0.0-1.0) is already calculated every 60s per pair but unused for sizing.

## Design

### Trend position sizing

`PositionManager.calculate_position_size()` accepts optional `confidence` parameter.

Formula: `risk_pct = 0.005 + (confidence * 0.02)`, clamped to [0.005, 0.03]

| Confidence | Risk % | Behavior |
|-----------|--------|----------|
| < 0.4 | 0.5-1.3% | Minimal exposure, uncertain regime |
| 0.4-0.7 | 1.3-1.9% | Moderate sizing |
| > 0.7 | 1.9-3.0% | Full conviction sizing |
| None (default) | 2.0% | Unchanged backward compat |

Caller `_open_trend_position` passes `ml_confidence` from per-pair predictions.

### Grid capital scaling

In `_grid_tick`, after computing `compound_capital`, scale by regime:

```python
def _grid_confidence_scale(ml_regime, ml_confidence):
    if ml_regime == 0:  # RANGING
        return 1.0 if ml_confidence > 0.7 else 0.8
    if ml_regime == 1:  # TRENDING
        return 0.6
    return 1.0  # default (DANGER already paused by state machine)
```

### Files to modify

1. `src/trend/position_manager.py` — add `confidence` param to `calculate_position_size()`
2. `hummingbot_files/scripts/ta_grid_trend.py` — pass confidence to position sizing, add grid capital scaling
