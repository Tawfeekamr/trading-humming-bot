# RL Walk-Forward Results — PPO vs Supervised RF (ETH + BNB)

**Date:** 2026-07-05. Sweep: 6 walk-forward OOS windows per pair, ~4620 pooled
bars each, PPO trained 1M steps/window (MPS). Train strictly precedes test.

## Headline: PPO does **not** significantly beat a properly-trained RF baseline

Pooled Diebold-Mariano (HAC), PPO vs RF, across 6 OOS windows:

| Pair | vs legacy RF (opaque `.pkl`) | vs **clean RF** (reproducible) |
|---|---|---|
| ETH | stat 3.11, **p = 0.0019** (sig.) | stat 0.30, **p = 0.77** (not sig.) |
| BNB | stat 4.08, **p < 0.0001** (sig.) | stat 0.71, **p = 0.48** (not sig.) |

PPO *crushes* the legacy baseline — but that baseline was poorly-trained
(labeling unrecoverable; trained by uncommitted code). Against a clean,
reproducible RF (`src/ml/train_regime.py` + `src/data/label_generation.py`),
**PPO and RF are at return parity** on both pairs. The original thesis claim
*"RL execution routing beats the supervised gate"* is **not supported** on the
raw-return axis.

## What PPO *does* demonstrably do better: risk control

Single-window ETH OOS (2026-06-01 → 2026-07-04), PPO 24m clean model:

| | Buy & Hold | RF (clean) | PPO |
|---|---|---|---|
| Total Return | −4.06% | −11.50% | −3.42% |
| **Max Drawdown** | 19.22% | 13.90% | **5.30%** |
| Time in Market | 100% | 73.5% | **52.3%** |

PPO achieves comparable-or-better return with **~¼ the drawdown of B&H** and
**~half the capital exposure**. The consistent pattern across windows: when RF
loses, it loses big (legacy RF: −10% to −35%; clean RF: smaller but still
larger than PPO); PPO caps drawdowns. This is the genuine, defensible value
proposition.

## Per-slice PPO returns (same policies either way)

```
ETH: +3.67  +4.32  −0.40  −10.71  −6.18  −0.70
BNB: +2.90  −1.82  −1.34  +2.65   −4.17  +0.23
```

## Implication for the thesis

The claim should be reframed from *"PPO beats supervised"* to something like:
*"RL execution routing achieves return parity with a strong supervised regime
baseline while materially reducing drawdown and capital exposure — i.e.
superior risk-adjusted routing, not superior return."* That is a real,
defensible result; the raw-return dominance was an artifact of a weak baseline.

## Caveats

- **Clean RF labeling is *defined*, not recovered.** The scheme (forward-looking
  3-class, `src/data/label_generation.py`) is one defensible choice; a
  different labeling could shift the baseline. Document and sensitivity-check it.
- **2 pairs only** (cache limit). BTC/DOGE/XRP would need kline download.
- **Single 1M-step PPO per window** — converged (explained_variance 0.02–0.63),
  but hyperparameter/timestep sensitivity not checked.
- **Multiple comparisons**: 2 pairs × 2 baselines — directional, not a
  Bonferroni-corrected panel.
- **RF labeling lookahead is the supervised target** (correct); no feature
  leakage (features are current-bar; temporal train/test split enforced).


## Reproducible evaluation artifacts

`python -m src.rl.walk_forward` now emits a CI-friendly JSON summary and a
concise operator summary. Each pair also writes
`reports/rl_walk_forward_<PAIR>.json` with sorted keys, source commit, model
SHA-256 checksums, feature-contract hash, date windows, fees/slippage, every
walk-forward slice, and the promotion decision.

Reports contain PnL, total return, profit factor, maximum drawdown,
time-in-market, fees/slippage, trade count, and fixed-seed 95% bootstrap
intervals for PPO, RF/ML, and the passive TA comparator. PPO-vs-RF and
TA-vs-ML pooled return series are retained separately. Fewer than 100
independent trades per strategy/regime is reported as `inconclusive_sample`;
the gate never activates PPO or changes live routing. A candidate must also
have multiple OOS windows and cannot increase drawdown or exposure. The
`human_review_required` reason is included for every otherwise eligible
candidate.

Train windows remain strictly before test windows. `--embargo-bars` can
preserve a non-zero gap between training and evaluation data; the training
subprocess uses the test-start boundary so the provenance check cannot
silently include the embargo or OOS bars.