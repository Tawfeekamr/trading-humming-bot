# Three Feature Enhancements — Design Spec

**Date:** 2026-05-23
**Status:** Approved
**Build Order:** Feature 2 (Correlation Gate) → Feature 1 (Fee Optimization) → Feature 4 (Auto-Retraining)

---

## Feature 2: Cross-Asset ML Correlation Gate

**Goal:** When BTC signals DANGER, immediately halt all altcoin buy-side operations. Capital protection: +15–25% drawdown saved during market-wide selloffs.

### Current State
Each pair's ML regime is fully independent. BTC entering DANGER has zero effect on ETH, BNB, DOGE, or XRP grids — they keep buying into a cascading dump.

### Design

**Logic in `ta_grid_trend.py`:**
- Add a `_btc_danger_active()` helper that reads `self._ml_predictions.get("BTC-USDT", (None, 0.0, 0.0))` and returns `True` when regime == 2
- Before placing any buy order on a non-BTC pair, check `_btc_danger_active()`. If `True`, skip the buy and log `"Correlation gate: BTC DANGER — halting {pair} buy-side"`
- Sell orders are **unaffected** — the bot can still exit positions and realize gains during a selloff
- When the gate transitions (active → inactive or vice versa), send a Telegram alert

**Edge cases:**
- BTC pair disabled (`enabled: false` in config): BTC model still runs predictions since it's a systemic signal, not a trading pair. The regime classifier still loads `models/regime_BTC-USDT.pkl`.
- BTC model fails to load or predict: Default to safe mode (halt altcoin buys). Log a warning. This prevents silent failures from leaving altcoins unprotected.

**Files changed:** `ta_grid_trend.py` only (~30–40 lines)
**New files:** None
**New dependencies:** None

---

## Feature 1: Dynamic Fee Optimization

**Goal:** Automate BNB rebalancing for 25% fee discount and enforce LIMIT_MAKER (post-only) orders for maker-only execution. Risk-free ROI: +3–6% annually.

### Current State
All orders use `OrderType.LIMIT` — which can fill as taker if the price crosses the spread. No BNB balance management exists. CapitalManager handles grid/trend allocation but ignores BNB entirely.

### Design

#### A. BNB Rebalancer (`src/risk/bnb_rebalancer.py`)

Maintains BNB balance within a target range for fee payments.

**Mechanism:**
- Target: keep ~$15–25 worth of BNB (covers ~2 weeks of grid trading fees)
- Every indicator refresh cycle (55 min), check BNB balance via connector
- If BNB balance < `$10` (configurable `bnb_min_usdt`): buy `$20` of BNB via market order from USDT reserve
- If BNB balance > `$50` (configurable `bnb_max_usdt`): sell excess back to USDT via market order
- Only runs when grid is ACTIVE (not during PAUSED/DANGER states)
- Logs every rebalance action to event logger with BNB price and quantities

**Integration in `ta_grid_trend.py`:**
- Instantiate `BNBRebalancer` in `__init__()` alongside other risk modules
- Call `rebalancer.check_and_rebalance(bnb_balance, usdt_balance)` from `on_tick()` after indicator refresh, before grid order placement

**Config addition to `strategy.yaml`:**
```yaml
fee_optimization:
  bnb_target_usdt: 20
  bnb_min_usdt: 10
  bnb_max_usdt: 50
  use_limit_maker: true
```

#### B. LIMIT_MAKER Enforcement

**Mechanism:**
- Replace all `OrderType.LIMIT` with `OrderType.LIMIT_MAKER` in `ta_grid_trend.py` (5 call sites)
- LIMIT_MAKER is post-only: if the order would cross the spread (take liquidity), the exchange rejects it instead of filling as a taker order
- Guarantees maker fee rate: 0.075% with BNB discount vs 0.1% taker rate
- `OrderType.LIMIT_MAKER` is already imported in `ta_grid_trend.py` line 109

**Error handling:**
- If a LIMIT_MAKER order is rejected (price crossed spread), log `"Order rejected (would take liquidity) — retrying next tick"` and wait for next tick with updated price
- No fallback to regular `OrderType.LIMIT` — the entire purpose is to never pay taker fees
- During high-volatility moments, some orders may take an extra tick to place, which is acceptable

**Files changed:**
- `ta_grid_trend.py`: ~10 lines (order type swap + rebalancer integration)
- `src/risk/bnb_rebalancer.py`: new file (~80 lines)

**New dependencies:** None

---

## Feature 4: Continuous Parameter Sweeps & Auto-Retraining

**Goal:** Automate weekly VectorBT parameter sweeps and monthly ML retraining via GitHub Actions. Hot-reload optimized configurations to prevent parameter drift. Edge preservation: +5–10% Sharpe stability.

### Current State
VectorBT sweep infrastructure exists in `backtest/` (sweep, walk-forward, reporting). Training pipeline exists in `src/ml/train_pipeline.py` with `--pair` flag. GitHub Actions deploys on push to main. No cron jobs, no automated retraining, no hot-reload.

### Design

#### A. Weekly Parameter Sweep (`.github/workflows/sweep.yml`)

Runs every Sunday at 00:00 UTC via `schedule: - cron: '0 0 * * 0'`.

**Steps:**
1. Checkout repo, set up Python 3.12, install dependencies
2. Run `backtest/vectorbt_sweep.py` for each active pair (ETH, BNB, DOGE, XRP)
3. Compare sweep results against current `config/strategy.yaml` as baseline
4. If a configuration beats the baseline Sharpe ratio by >5%:
   - Update the relevant parameters in `config/strategy.yaml`
   - Commit with message `"sweep: optimize {pair} params (Sharpe {old} → {new})"`
   - Commit triggers existing `deploy.yml` → bot restarts with optimized params
5. If no improvement: log results and exit
6. Upload full sweep results as GitHub Actions artifacts (30-day retention)

**Sweep scope:** Only parameters that don't require code changes:
- BB period, BB std_dev
- RSI oversold/overbought thresholds
- ATR spacing multiplier
- Grid levels

Does NOT sweep: ML model architecture, risk thresholds, capital allocation, enabled pairs.

**Minor refactor to `backtest/vectorbt_sweep.py`:**
- Accept pair symbol as CLI arg (`--pair ETHUSDT`)
- Output structured JSON results to `backtest/results/{pair}_sweep.json`
- Compare against current config and print diff

#### B. Monthly ML Retraining (`.github/workflows/retrain.yml`)

Runs on the 1st of each month via `schedule: - cron: '0 0 1 * *'`.

**Steps:**
1. Checkout repo, set up Python 3.12, install dependencies
2. For each active pair, run in parallel:
   - `python -m src.ml.train_pipeline --pair ETHUSDT` (fetches latest data from Binance public API)
   - New model saved to `models/regime_ETHUSDT.pkl.new`
3. Compare new model accuracy against current deployed model
4. If new model accuracy > current by >1%:
   - Replace `models/regime_{symbol}.pkl` with the `.new` file
   - Commit with message `"retrain: update {pair} model (accuracy {old}% → {new}%)"`
   - Commit triggers `deploy.yml` → bot restarts with new models
5. If worse: log comparison report and skip — never deploy a degraded model
6. Clean up `.new` files after comparison

**Data access:** Binance public API requires no keys. The training pipeline already handles paginated data fetching.

#### C. Hot-Reload Detection

**Mechanism in `ta_grid_trend.py`:**
- Track `last_loaded_mtime` per model alongside predictions in `self._ml_predictions`
- During indicator refresh cycle (every 55 min), check each model file's `os.path.getmtime()`
- If file was modified since last load:
  - Load new model, run validation prediction on last known features
  - Log `"Hot-reloaded ML model for {pair}"`
  - Send Telegram notification: `"ML model updated for {pair}"`
- Zero downtime — old model continues serving predictions until new one is fully loaded in memory

**Files changed:**
- `ta_grid_trend.py`: ~20 lines for hot-reload detection
- `backtest/vectorbt_sweep.py`: minor refactor for CLI arg + structured output

**New files:**
- `.github/workflows/sweep.yml` (~60 lines)
- `.github/workflows/retrain.yml` (~80 lines)

**New dependencies:** None — vectorbt and sklearn already in requirements.txt

---

## Implementation Priority (Build Order)

| Order | Feature | Effort | Impact | New Files |
|-------|---------|--------|--------|-----------|
| 1st | Cross-Asset ML Correlation Gate | Small (~30 lines) | High (drawdown protection) | None |
| 2nd | Dynamic Fee Optimization | Medium (~90 lines) | Medium (fee savings) | `src/risk/bnb_rebalancer.py` |
| 3rd | Auto-Retraining Pipeline | Medium (~160 lines) | High (edge preservation) | 2 GitHub Actions workflows |

**Safety guard for auto-sweep commits:** Both sweep.yml and retrain.yml use `[skip ci]` in commit messages to prevent an infinite deploy loop. A separate manual trigger or daily deploy window applies the optimized config. Alternatively, the sweep/retrain workflows can create a PR instead of committing directly — the PR triggers tests but not deployment until merged.

## Testing Strategy

- **Feature 2:** Unit test that verifies buy orders are skipped when BTC regime is DANGER, sell orders are unaffected, and gate transitions send Telegram alerts
- **Feature 1:** Unit test for BNB rebalancer thresholds, integration test for LIMIT_MAKER rejection handling
- **Feature 4:** Unit test for hot-reload detection, manual verification of GitHub Actions cron execution

## Deployment

Each feature deploys independently via the existing `deploy.yml` pipeline (push to main → SSM deploy to EC2). No infrastructure changes needed.
