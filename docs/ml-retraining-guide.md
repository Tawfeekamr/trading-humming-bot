# ML Regime Model Retraining Guide

## When to Retrain

| Trigger | Action |
|---------|--------|
| **Scheduled** | Retrain every 2 weeks (crypto regimes drift fast) |
| **Regime shift detected** | If model confidence drops below 0.55 for 24h+, retrain with latest data |
| **New pair added** | Train a new per-pair model before enabling the pair |
| **Accuracy degradation** | If out-of-sample accuracy drops >10% from baseline, retrain |

## Training Commands

```bash
# Standard retrain (all timeframes, 2000 candles)
python -m src.ml.train_pipeline --pair SOL-USDT

# High-quality retrain (more data, single timeframe)
python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 1h --candles 4000

# Quick validation (check if model still performs)
python -m src.ml.train_pipeline --pair SOL-USDT --timeframe 1h --candles 2000
```

## Data Requirements

| Timeframe | Min Candles | Min Days | Recommended Candles |
|-----------|-------------|----------|---------------------|
| 15m       | 5,760       | 60       | 8,000+              |
| 1h        | 2,160       | 90       | 4,000+              |
| 4h        | 720         | 120      | 2,000+              |
| 1d        | 180         | 180      | 500+                |

## Drift Detection

Compare the last 7 days of model predictions against the training label distribution:

1. Count regime predictions per class over the last 7 days
2. If any class shifts >20% from training distribution, flag for retraining
3. If DANGER predictions increase >3× baseline, investigate immediately (may indicate real market shift)

## Deployment Checklist

- [ ] Train model and review accuracy, confusion matrix, DANGER recall
- [ ] Verify embargo gap is active (check "Embargo gap: N samples" in training output)
- [ ] Review feature importances — if all flat (0.04-0.10), model may be weak
- [ ] Copy `.pkl.new` → `.pkl` (active model)
- [ ] Restart Python container to pick up new model
- [ ] Monitor `/system` Telegram command for regime changes for 30 minutes
