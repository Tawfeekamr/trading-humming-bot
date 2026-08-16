# Design Spec — Reinforcement Learning Execution Agent

**Thesis title:** *Machine Learning Regime Dynamics in Multi-Asset Execution: Benchmarking Supervised and Reinforcement Learning Policies*

*(Superseded title — the thesis was retitled 2026-08-16 to 'Learned Withdrawal and Evaluation Blindness in Reinforcement Learning for Trading: A Corrected-Protocol Case Study' after the evaluation audit; see FIX_REPORT.md. This planning document predates the audit and its outcome assumptions did not hold.)*

**Date:** 2026-06-18
**Status:** Design — pending user review
**Author:** tawfeekamr
**Related:** `docs/study/project_summary.md`, `docs/19_ml_regime_classifier.md`, `docs/study_docs/research_roadmap.md`

---

## 1. Problem & Motivation

The deployed framework routes capital across specialised execution engines (Grid, Trend, Swing, Signal-Copy) using a **supervised Random-Forest regime classifier** plus hand-written gating rules. This *supervised router* is a strong, interpretable baseline — but it is not the *optimal* routing policy. Regime-switched execution is naturally a **Markov Decision Process (MDP)**: a state (market regime + inventory), an action (which engine + size), and a reward (risk-adjusted return net of costs). The supervised router solves the *estimation* sub-problem (what regime are we in?) with ML and the *control* sub-problem (what to do?) with expert rules. **Reinforcement Learning learns the control policy directly.**

This project runs a **controlled comparative study**: do active regime-switching policies (Supervised or RL) beat passive Buy-and-Hold? And if so, does the generative complexity of an offline sequence-based **Decision Transformer** (DT) routing policy beat the simpler *supervised* ruleset, net of realistic fees and slippage?

### 1.1 Why this is a valid and defensible thesis
- It establishes a **fundamental baseline first** (Buy-and-Hold) to prove active management is warranted.
- It then evaluates the "cost of complexity" by benchmarking the state-of-the-art **Decision Transformer** against a strong heuristic baseline (the existing supervised hybrid system).
- Negative results are valid: if RL does not beat the baseline, that is a publishable finding about the limits of RL in low-frequency regime-switched crypto execution.

---

## 2. Goals & Non-Goals

### Goals
1. Define and implement a Gymnasium trading environment that replays historical bars with realistic fee/slippage and reproduces the supervised baseline's known equity curve (regression test).
2. Implement an offline sequence-based **Decision Transformer (DT)** agent that outputs a high-level routing + sizing action based on past market sequences.
3. Run a **walk-forward, purged & embargoed** 3-way comparison (Buy-and-Hold vs Supervised Router vs Decision Transformer) on ETH, BNB, DOGE, XRP.
4. Statistically test whether the active policies beat B&H, and whether the DT beats the Supervised baseline (Diebold-Mariano + bootstrapped Sharpe CIs).
5. Deploy the best agent's policy into the Rust paper connector for a realism check.

### Non-Goals (out of scope for this thesis)
- Sub-second / market-making execution (the system trades on closed 1h bars).
- Low-level order placement (RL routes among existing engines; it does not place raw limit orders).
- Live capital deployment (paper trading only; the August go-live decision is separate).
- Replacing the Signal-Copy engine (external LLM-driven; not a routing decision the agent controls).

---

## 3. Research Questions & Hypotheses

**RQ1 (Primary).** Do active regime-switching policies — supervised and/or reinforcement-learning — outperform **Buy-and-Hold on a risk-adjusted basis, net of costs**, across walk-forward out-of-sample windows spanning multiple market regimes?
**RQ2.** Where active policies clear Buy-and-Hold, **does the Decision Transformer (DT) beat the supervised router** — i.e., does the DT's sequence-modeling complexity pay off over the supervised heuristic ruleset?
**RQ3.** How sensitive is the Decision Transformer to sequence length (context window) and reward design (λ, fee model), and how does its turnover compare to the supervised baseline?

**H1.** Both the Supervised Router and Decision Transformer will achieve a higher OOS Sharpe Ratio and lower Max Drawdown than Buy-and-Hold, though absolute return may be lower.
**H2.** The Decision Transformer will outperform the Supervised Router in predicting regime shifts, but will suffer from higher turnover and be highly sensitive to trading fees.
**H3.** Active policies help most on pairs where the supervised system already has edge (ETH, BNB) and least on no-edge pairs (DOGE, XRP).
**H4.** Costs-aware reward shaping (λ > 0) reduces turnover and drawdown versus λ = 0.

---

## 4. The MDP Formulation

| Element | Definition |
|---------|------------|
| **Decision frequency** | Once per **closed 1h bar** (matches the existing system). |
| **State `sₜ`** (observation) | (a) the 14 regime features already computed for the classifier; (b) position state — one-hot of active engine {flat, grid, trend, swing}, unrealised PnL %, current notional/size; (c) portfolio state — equity, drawdown-from-peak, heat used; (d) time features — sin/cos of hour-of-day, day-of-week. ~25-dim vector. |
| **Action `aₜ`** | Discrete, 10 actions: `{GRID, TREND, SWING} × {0.5x, 1.0x, 1.5x}` (9) + `GO_FLAT` (1). Size scales the engine's notional. The four routes are distinct targets: a strategy-redundancy analysis (§4.1) confirmed Grid and Swing are disjoint (Pearson ≈ 0), not duplicates — collapsing them is **not** warranted. |
| **Transition** | Semi-deterministic: given the bar's OHLCV (exogenous market) and the action, the engine executes deterministically → new position. The stochasticity is entirely market-driven. |
| **Reward `rₜ`** | `rₜ = (R_agent − R_bench) − fee_rate · turnoverₜ − λ · dd_stepₜ` (see §6). |
| **Discount `γ`** | 0.99 (long-horizon; episodes are finite multi-month windows). |
| **Episode** | One walk-forward train or test window (e.g. 6 months of 1h bars ≈ 4,300 steps). |

**Decision Transformer variant:** the same MDP, but the agent is conditioned on a **return-to-go (RTG)** token and predicts actions autoregressively over a sequence of past `(s, a, r)` — trained offline on logged trajectories (§7.3).

### 4.1 Strategy non-redundancy evidence (action-space justification)

A natural challenge to a multi-engine routing design: *are the engines redundant, so that some actions are equivalent and the action space is artificially inflated?* This was tested directly rather than assumed.

**Method** (`backtest/strategy_redundancy.py`; results in `backtest/results/redundancy_diagnosis.csv`). Each engine's *real* entry/activation logic was reconstructed from source — Grid's live activation gates (`grid.rs`: ADX<25 ∧ Choppiness>50 ∧ NATR∈[0.5%,4%] ∧ price>EMA200) and Swing's entry (`swing.rs`: ADX<22 ranging ∧ near-Donchian-lower-band ∧ score≥2/5 ∧ R:R≥2) — replayed on 12,384 1h bars × 4 pairs (Jan 2025–May 2026). Two overlap metrics per pair: entry coincidence (±2 bars) and long-exposure-mask Pearson correlation / Jaccard. Trend was included as a discrimination baseline.

**Result: Grid and Swing are disjoint, not redundant.**

| Pair | Grid active | Swing exposure | Exposure Pearson (Grid↔Swing) |
|------|-------------|----------------|-------------------------------|
| ETH  | 13.2% | 1.0% | −0.04 |
| BNB  | 10.7% | 1.1% | +0.01 |
| DOGE | 11.2% | 1.6% | −0.05 |
| XRP  | 13.6% | 0.4% | −0.01 |

Pearson ≈ 0 and ≤0.1% entry coincidence on every pair — well below any redundancy threshold (Pearson > ~0.4). The baseline validates the metric: Trend↔Grid = 0.15–0.27 (sensibly positive — both want price>EMA200); Trend↔Swing ≈ 0 (unrelated).

**Why disjoint (structural).** Grid harvests oscillation around the Bollinger *center* inside an *uptrend* (`close>EMA200`); Swing buys the *range floor* (close ≤ Donchian-low + 1.5·ATR) in *flat* ranging. When price dumps to the range low (Swing's trigger), Grid deactivates (drops below EMA200, or volatility exceeds its cap) — so the two fire in anti-correlated sub-regimes.

**Design implication.** The four-route action space `{GRID, TREND, SWING, FLAT}` is evidence-backed: each route maps to a distinct, non-overlapping market niche. No engines are merged or excluded on redundancy grounds. The analysis also stands as an independent thesis sub-finding (strategy-clustering / portfolio-diversification evidence).

*Caveat:* Swing was reconstructed on 1h bars (deployed uses 4h HTF + 1h LTF), so Swing timing is approximate; the zero-overlap result is robust to this. A separate, non-redundancy weakness surfaced: **Swing is under-active** (0.4–1.6% exposure, 6–26 entries/17mo) — a gate-tuning matter tracked in §11, not duplication.

---

## 5. System Architecture

```
                ┌─────────────────────────────────────────────────────────┐
                │              ROUTING POLICY (swappable)                 │
                │  SupervisedRegimeRouter (baseline)  ◄── existing        │
                │  DTRouter (Decision Transformer)    ◄── new             │
                │  common interface: decide(state) -> (engine, size)     │
                └───────────────┬─────────────────────────┬───────────────┘
                                │                         │
              TRAINING (offline)│                         │ LIVE/PAPER EVAL
                                ▼                         ▼
   ┌───────────────────────────────────────┐   ┌──────────────────────────┐
   │  TradingEnv  (Gymnasium, new)         │   │  Rust Engine (existing)  │
   │  • replays 1h bars from data cache    │   │  consumes routing        │
   │  • fee 0.1% maker + slippage model    │   │  decision via generalized│
   │  • runs Grid/Trend/Swing as prims     │   │  regime-cache JSON/API   │
   │  • computes reward (§6)               │   │  → paper connector       │
   └───────────────┬───────────────────────┘   └──────────────────────────┘
                   │ step()
                   ▼
          Decision Transformer
          (offline sequence-modeling on
           logged 5-engine trajectories)
```

### 5.1 Key abstraction: `RoutingPolicy`
A single Python interface both routers implement, so the baseline and every RL agent share the **exact same execution path** — this is what makes the comparison fair:
```python
class RoutingPolicy(Protocol):
    def decide(self, state: EnvState) -> RoutingAction: ...   # (engine, size)
```
- `SupervisedRegimeRouter` wraps the existing `RegimeClassifier` + gating rules → produces `(engine, size)`.
- `RLRouter` loads a trained policy (`.zip` from SB3, or DT checkpoint) → produces `(engine, size)`.

### 5.2 Integration with the Rust engine
The Rust engine already consumes a regime decision from a shared cache (JSON) each tick. We **generalise** that cache from `{regime, confidence}` to `{active_engine, size_multiplier}`. Both routers write to it. No engine internals change — the engines already know how to run/stop given an enable signal. A feature flag (`ROUTER=supervised|rl_<algo>`) selects the source, enabling instant A/B and rollback.

---

## 6. Reward Function (precise)

```
rₜ = (R_agentₜ − R_benchₜ) − fee_rate · turnoverₜ − λ · dd_stepₜ

R_agentₜ  = equity_return over bar t
R_benchₜ  = buy-and-hold return over the same bar   (excess-over-passive)
turnoverₜ = sum of (|buy_notional| + |sell_notional|) executed this bar
fee_rate  = 0.001 (0.1% maker); counting both sides ⇒ ~0.2% round-trip cost
dd_stepₜ  = max(0, DDₜ − DDₜ₋₁),  where  DDₜ = (peakₜ − equityₜ)/peakₜ
            and  peakₜ = max(peakₜ₋₁, equityₜ)
            (penalises only the *deepening* of drawdown this bar, not recovery)
λ         = risk-aversion ablation knob ∈ {0, 0.5, 1.0, 2.0}
```

**Design rationale.**
- *Excess-over-passive* (`R_agent − R_bench`): the agent is rewarded only for beating buy-and-hold, not for riding a bull market — de-biases reward from market direction.
- *Cost inside the reward*: prevents the classic RL failure of churn-to-death; turnover is penalised at real maker rates.
- *Drawdown penalty*: gives a risk-adjusted signal so a high-return/high-drawdown policy is not favoured.
- **Ablation knobs** (RQ3): λ toggled across 4 values; optional **regime-shaping bonus** (`+β` when the chosen engine matches the classifier's regime) on/off.

---

## 7. Components

### 7.1 `TradingEnv` (Gymnasium) — `src/rl/env.py` *(new)*
- Replays closed 1h bars from the existing `backtest/data_cache` (klines), extended to ~2–3 years per pair.
- Computes the 14 regime features per bar via the existing feature pipeline.
- Holds a lightweight position simulator: applies the chosen engine's *activation/exit logic* as deterministic primitives (Grid = mean-revert levels; Trend = trailing stop; Swing = range-low entry). Engines are modelled at the bar level (not tick level) — sufficient at 1h granularity and ~100× faster.
- Fee = 0.1% maker on each side; slippage configurable (half-spread by default for realism; 0 bps available as an optimistic ablation).
- Emits `(observation, reward, done, info)`; `info` carries the metrics needed for evaluation (turnover, drawdown, per-engine attribution).
- **Regression test:** with `SupervisedRegimeRouter` plugged in, the env must reproduce the known backtest equity curve (from `backtest_validation`) within a documented tolerance. This proves the env is faithful before any RL training.

### 7.2 Offline sequence agent — Decision Transformer — `src/rl/agents/decision_transformer.py` *(new, custom)*
- Built on PyTorch (e.g., using HuggingFace `transformers` or a custom min-DT implementation).
- Trains on **logged trajectories**: the existing 5-engine trade journal (`data/trades.db`) + the env-replayed supervised baseline runs become the offline dataset `(R, s, a)` sequences with returns-to-go.
- The agent ingests a sequence of past `(state, action, reward-to-go)` tuples (the context window).
- At inference, condition on a target RTG (Return-to-Go) and sample actions autoregressively.
- Callbacks log Sharpe/MaxDD/turnover per epoch to TensorBoard + a results DB.

### 7.4 Routing glue — `src/rl/router.py` *(new)*
- `RoutingPolicy` implementations + the `RLRouter` that loads any trained policy.
- Writes `{active_engine, size_multiplier}` to the Rust-shared cache; reads back engine state for the observation.

---

## 8. Experiment Design (the comparison matrix)

| Pair | Benchmarks | Supervised baseline | RL agents | Windows |
|------|-----------|---------------------|-----------|---------|
| ETH-USDT | Buy-and-Hold | SupervisedRegimeRouter | Decision Transformer (DT) | walk-forward |
| BNB-USDT | Buy-and-Hold | SupervisedRegimeRouter | Decision Transformer (DT) | walk-forward |
| DOGE-USDT | Buy-and-Hold | SupervisedRegimeRouter | Decision Transformer (DT) | walk-forward |
| XRP-USDT | Buy-and-Hold | SupervisedRegimeRouter | Decision Transformer (DT) | walk-forward |

- **Per-pair agents** (mirrors the per-pair regime models).
- **Walk-forward:** rolling train/test (e.g. train 12mo → test 3mo, slide by 3mo) across the full data range.
- **Purged & embargoed CV** (López de Prado): drop overlap bands around the test window so the reward's forward-looking component cannot leak.
- **Ablations:** λ ∈ {0, 0.5, 1.0, 2.0}; regime-shaping bonus on/off; slippage {0, half-spread}.
- **Reproducibility:** fixed seeds, hyperparams frozen in config, every run logged with git SHA + data hash.

### 8.1 Benchmarks (standard comparison set)

Every active policy (supervised router + each RL agent) is measured against one fixed ladder of standard benchmarks, per pair. **The primary bar (RQ1/H1) is risk-adjusted outperformance of Buy-and-Hold**; the rest contextualise whether the edge is real and where it comes from.

| Benchmark | Represents | Why standard | Source |
|---|---|---|---|
| **Buy-and-Hold** (per pair) | Passive ownership | Universal floor — active must beat "do nothing" | existing klines (`backtest/data_cache`) |
| **BTC return** (market beta) | The crypto "market" | BTC drives the market — crypto's S&P-500 equivalent; all returns also expressed **excess over BTC** | fetch `BTCUSDT` klines (Binance) |
| **Rule-based regime filter** | Practitioner norm | Simplest "does ML help?" test (ADX/vol threshold) | hand-coded |
| **HMM / Markov-switching** | Classical academic regime model | THE literature standard for "regime-switching" (Hamilton 1989) | `hmmlearn` `GaussianHMM` |
| **Each engine standalone** | Static-strategy control | Routing must beat any single static engine | existing backtest |
| **Equal-weight 4-pair portfolio** | Passive diversification | Standard portfolio benchmark | compute from the 4 pairs |
| **Risk-free rate** | Sharpe denominator | Crypto convention ≈ 0 (or stablecoin yield) | — |

### 8.2 Evaluation metrics
Total return · **Sharpe** · **Sortino** · **Maximum Drawdown** · Calmar · **Profit Factor** · Win rate · **Turnover** (cost-efficiency) · per-regime breakdown.

### 8.3 Statistical significance
- **Diebold-Mariano test** on per-bar returns: each active policy vs Buy-and-Hold (H1), and RL vs supervised (H2).
- **Block bootstrap** 95% CIs on Sharpe and MaxDD.
- **Deflated Sharpe Ratio** (Bailey & López de Prado) to correct for trying multiple configs/algorithms.
- Correct for multiple comparisons (4 pairs × 3 algos) — Bonferroni or Benjamini-Hochberg.

---

## 9. Implementation Phasing

| Phase | Deliverable | Gate |
|-------|-------------|------|
| **0** | `TradingEnv` + reward unit tests + **baseline regression** (reproduce known equity curve) | Env faithful ✓ |
| **1** | B&H + Supervised baseline evaluation on walk-forward windows | Baseline benchmarked |
| **2** | Trajectory logging + Decision Transformer data pipeline | Offline dataset ready |
| **3** | Decision Transformer agent; beats random + walks forward on pairs | DT working |
| **4** | Full 3-way comparison matrix + statistical tests + figures | Results chapter ready |
| **5** | Best-policy deploy to Rust paper connector; realism check | Live-loop validated |

Phase 0 is the critical risk-reducer: if the env cannot reproduce the baseline, nothing downstream is trustworthy.

---

## 10. Data & Infrastructure

- **Data:** Binance 1h klines, ~2–3 years/pair, via existing `backtest/data_cache` + `fetch_*` scripts. Offline trajectories from `data/trades.db`.
- **Stack:** Python 3.13 (existing `.mltrain` venv), PyTorch, HuggingFace `transformers` (for DT), Gymnasium, pandas/numpy. RL deps added to a new `requirements-rl.txt` (kept separate so the live bot image stays lean).
- **Compute:** sequence modeling requires slightly more compute than basic RL, but training on 1h bars can still be done in hours on a local GPU or Google Colab/Kaggle.
- **Storage:** trained policies under `models/rl/<algo>_<pair>.zip`; run logs to `data/rl_runs/`.

---

## 11. Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| **Overfitting** to the train window | Walk-forward + purged/embargoed CV; freeze hyperparams; report OOS only. |
| **Non-stationarity** violates MDP assumption | Retraining cadence; regime-stratified evaluation; honest negative results allowed. |
| **Fee trap** (agent over-trades) | Cost inside reward; monitor turnover metric; λ ablation. |
| **Env/baseline mismatch** invalidates comparison | Phase-0 regression test is a hard gate. |
| **RL doesn't beat supervised / neither beats Buy-and-Hold** | Valid negative result (limits of active regime-switching, or of RL at low frequency) — still answers RQ1/RQ2. |
| **DOGE/XRP have no edge** | Expected; treated as stress-tests / negative-result evidence (H3). |
| **Swing under-activity** (sparse primitive) | Diagnosis (§4.1) showed Swing fires only 0.4–1.6% of bars → routing to it yields sparse reward. Mitigation: Phase-0 measures per-route activity; if a route is <2% active, either loosen its gates in the *env primitives* (research-only, not live config) or drop the route and document why. |
| **Reproducibility** | Fixed seeds, config-locked, git SHA + data hash logged per run. |

---

## 12. Testing Strategy

- **Unit tests:** reward computation (hand-checked examples), fee accounting, drawdown increments, action→execution mapping, observation assembly.
- **Property tests:** env never exceeds position/heat limits; equity never goes negative; reward finite.
- **Regression test (critical):** `SupervisedRegimeRouter` in the env reproduces the documented backtest equity curve within tolerance.
- **Integration test:** `RLRouter` writes a valid routing decision that the Rust engine (via mock connector) consumes and routes correctly.
- **Determinism:** fixed seed ⇒ identical episode rollout (required for credible walk-forward).

---

## 13. Thesis Deliverables

1. `TradingEnv` + reward + regression harness (Phase 0).
2. Three trained agent families × four pairs, with walk-forward OOS results.
3. Comparison report: equity curves, metric tables, Diebold-Mariano + bootstrap CIs, per-regime attribution, ablation tables (λ, shaping, slippage).
4. Best-policy paper-connector deployment + realism-vs-backtest analysis.
5. Dissertation Results, Discussion, and Future-Work sections, fed directly by the above.

---

## 14. Honest Constraints (stated up-front)

- The system trades on **closed 1h bars**; RL cannot react intra-bar to flash crashes.
- RL competes with a **well-engineered supervised baseline**, so beating it is genuinely hard — a null/negative result is a legitimate and likely outcome on some pairs.
- Slippage is **modelled**, not measured; the paper-connector step (Phase 5) is where real slippage is first observed.
- The comparison is on **paper/testnet**, not live capital.

---

*Next step after user approval of this spec: invoke the writing-plans skill to produce a detailed, phased implementation plan.*
