# Supervised Baseline Proof Plan & Results-Chapter Outline

**Thesis title:** *Machine Learning Regime Dynamics in Multi-Asset Execution: Benchmarking Supervised and Reinforcement Learning Policies*

*(Superseded title — the thesis was retitled 2026-08-16 to 'Learned Withdrawal and Evaluation Blindness in Reinforcement Learning for Trading: A Corrected-Protocol Case Study' after the evaluation audit; see FIX_REPORT.md. This planning document predates the audit and its outcome assumptions did not hold.)*

**Purpose.** Before any RL, establish that the supervised regime-switching router's results are *real* — not an overfit backtest artifact. This is both the scientifically correct ordering and the spec's Phase-0 gate. This document is the proof pipeline **as a checklist**, and it doubles as the **Results-chapter outline**.

**Status legend:** ✅ done · 🟡 partial · ⬜ new work

**Headline:** the baseline is *designed and deployed* but **not yet proven**. Steps 1–7 are mostly 🟡/⬜; only Step 1's raw metrics exist.

---

## Benchmarks (the standard comparison set)

Every active policy — the supervised router and each RL agent — is measured against one fixed ladder of standard benchmarks, per pair. **The primary bar is risk-adjusted outperformance of Buy-and-Hold** (RQ1/H1); the rest contextualise whether the edge is real and where it comes from.

| Benchmark | Represents | Why it's standard | Source | In plan |
|---|---|---|---|---|
| **Buy-and-Hold** (per pair) | Passive ownership | Universal floor — active must beat "do nothing" | existing klines (`backtest/data_cache`) | ✅ |
| **BTC return** (market beta) | The crypto "market" | BTC drives the market — crypto's S&P-500 equivalent; returns also expressed **excess over BTC** | fetch `BTCUSDT` klines (Binance) | ⬜ add |
| **Rule-based regime filter** | Practitioner norm | Simplest "does ML help?" test (ADX/vol threshold) | hand-coded | Step 2 |
| **HMM / Markov-switching** | Classical academic regime model | THE literature standard for "regime-switching" (Hamilton 1989) | `hmmlearn` `GaussianHMM` | Step 2 |
| **Each engine standalone** | Static-strategy control | Routing must beat any single static engine | existing backtest | Step 3 |
| **Equal-weight 4-pair portfolio** | Passive diversification | Standard portfolio benchmark | compute from the 4 pairs | ⬜ add |
| **Risk-free rate** | Sharpe denominator | Crypto convention ≈ 0 (or stablecoin yield) | — | note |

> **"Better than Buy-and-Hold" = risk-adjusted, net of costs, across regimes.** Crypto buy-and-hold in a bull run is nearly unbeatable on raw return; the defensible win is higher Sharpe / lower drawdown net of fees. The RL reward (`R_agent − R_buyandhold`) already trains agents to beat buy-and-hold — evaluation checks whether they actually do, risk-adjusted. **Main RQ: do RL or supervised beat Buy-and-Hold?**

---

## Step 1 — Prove the classifier itself is sound *(the estimation part)*  🟡

Show the regime *predictions* are trustworthy before judging any trading result.

| Item | Detail | Status |
|------|--------|--------|
| OOS accuracy + per-class P/R/F1 | Especially **DANGER recall** (rare, costly class). Existing: acc≈62%, RANGING F1 0.73, TRENDING 0.56, DANGER 0.23 (`docs/19_ml_regime_classifier.md`) | 🟡 known, re-run OOS-only |
| Confusion matrix | 3×3, per pair, on held-out test set | ⬜ plot |
| Calibration proof | **Reliability diagram + Brier score + ECE.** You *apply* `CalibratedClassifierCV`; this *proves* it worked. Essential — you size positions on these confidences. (Niculescu-Mizil & Caruana 2005; Zadrozny & Elkan 2002) | ⬜ new |
| CV discipline | **Purged + embargoed walk-forward** (López de Prado), never random k-fold (`backtest/ml_walk_forward.py` is the scaffold) | 🟡 chronological split exists; purging/embargo ⬜ |

**Done when:** OOS per-class metrics + confusion matrix + a reliability diagram showing calibration, all from purged/embargoed CV.

---

## Step 2 — Prove the classifier beats simpler alternatives  ⬜

A good F1 means nothing if a trivial rule matches it. This is the experiment that turns "RF replaces HMM" from a *claim* into a *result*, and it earns the word "Supervised."

| Baseline | Tooling | Status |
|----------|---------|--------|
| Majority-class predictor | sklearn `DummyClassifier` | ⬜ |
| Rule-based regime detector (ADX / volatility threshold) | hand-coded | ⬜ |
| **HMM / GMM** (the classical regime-switching foil) | `hmmlearn` / sklearn `GaussianMixture` | ⬜ |
| Your supervised RandomForest | existing `src/ml/regime_classifier.py` | ✅ |

**Done when:** RF beats all three on OOS F1 (esp. DANGER recall) — then "supervised regime-switching" is a *result*, not a label.

---

## Step 3 — Prove routing adds value over standalone strategies *(the control part)*  ⬜

The full equity curve. The router must beat what it's meant to improve:

- Buy-and-hold (HODL) per pair — `backtest/reporting.compute_benchmark` ✅ exists
- **Grid-only, Trend-only, Swing-only** equity curves — per-engine backtests exist, **but the combined router's curve vs each standalone does not**
- Walk-forward OOS, realistic 0.2% round-trip costs + slippage

**Done when:** supervised router's OOS equity curve ≥ every standalone + HODL on the risk-adjusted metric, net of costs.

---

## Step 4 — Prove the edge is statistically real, not luck  ⬜

| Test | Targets | Status |
|------|---------|--------|
| **Diebold-Mariano** (1995) | per-bar returns: router vs each benchmark | ⬜ |
| Block bootstrap (Politis-Romano) | 95% CIs on Sharpe & Max Drawdown | ⬜ |
| **Deflated Sharpe Ratio** (Bailey & López de Prado 2014) | corrects for trying multiple configs — blocks the "best-of-many" reviewer objection | ⬜ |

**Done when:** router's Sharpe CI excludes the benchmarks', and DSR > 0.

---

## Step 5 — Prove it's not overfit  🟡

- OOS-only reporting; hyperparams chosen on train, **frozen**, reported on test. 🟡
- **Final untouched hold-out window** — edge survives there. ⬜
- **Cost sensitivity** — does the edge persist if fees/slippage are raised? (An edge that vanishes at realistic cost isn't an edge.) ⬜

**Done when:** edge holds on the never-touched window and under stressed costs.

---

## Step 6 — Per-regime attribution *(the killer evidence)*  ⬜

The one that convinces a skeptic. Show that **within each regime**, the router's chosen engine beats the *wrong* engine — i.e. the routing decision itself adds value, not just market drift.

- Group bars by **predicted regime**; within each, compare P&L of the routed engine vs the alternatives.
- Example proof: TRENDING bars are more profitable under Trend than Grid, and the router sends them to Trend.

**Done when:** per-regime table shows the routed engine ≥ alternatives within each regime, consistently across pairs.

---

## Step 7 — Prove live behaviour matches the backtest  🟡

- Paper-trading live since 8 May 2026 (~6 weeks) — data accumulating in `data/trades.db`.
- **Backtest-vs-live P&L gap** over the same window — exposes instant-fill bias / slippage.

**Done when:** live P&L tracks backtest within a stated tolerance band; gaps explained (slippage, fill timing).

---

## Step 8 — The bridge to RL *(spec Phase 0)*  ⬜

Once 1–7 validate the baseline, plug it into the RL `TradingEnv` and confirm the env **reproduces that same validated equity curve within tolerance**. Proves the env is faithful → the later RL-vs-supervised comparison is fair. (Spec §9 Phase 0; the baseline-regression test in §12.)

**Done when:** env equity curve ≈ validated baseline curve (within tolerance) on the same window.

---

## How this reorders the spec's phasing

The pipeline reveals a **Phase 0-prime (Baseline Validation)** that must precede the spec's current Phase 0 (env build):

```
Phase 0-prime  Baseline validation   ← Steps 1–7  (this doc)   [MOSTLY NEW]
Phase 0        Env fidelity gate     ← Step 8     (spec §9)
Phase 1–5      RL build & compare    ← spec §9
```

**Implication:** most of the near-term work is *proving the supervised baseline* (Steps 1–7), not building RL yet. That's the correct order — you can't fairly benchmark RL against a baseline whose edge is unproven.

---

## Results-chapter mapping (outline)

| Results § | Source steps |
|-----------|--------------|
| 5.1 Classifier performance (metrics, confusion matrix, calibration) | Step 1 |
| 5.2 Classifier vs alternatives (incl. HMM/GMM) — *the "supervised" result* | Step 2 |
| 5.3 Backtest: router vs HODL vs standalone engines | Step 3 |
| 5.4 Statistical significance (DM, bootstrap, DSR) | Step 4 + 5 |
| 5.5 Per-regime attribution — *the mechanism proof* | Step 6 |
| 5.6 Live vs backtest fidelity | Step 7 |
| 5.7 RL vs supervised router | (later — spec Phases 1–5) |

---

*Related: `docs/superpowers/specs/2026-06-18-rl-execution-agent-design.md` (§4.1, §9, §11), `docs/19_ml_regime_classifier.md`, `docs/study/msc_project_feedback.md`.*
