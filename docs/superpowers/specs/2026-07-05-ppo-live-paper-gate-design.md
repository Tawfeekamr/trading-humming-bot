# PPO → Live Engine Integration (Paper-Gated) — Design

**Status: DRAFT for review. NOT implemented.** Grounded in the live-bot
architecture map (see bottom). This spec stages PPO toward live trading behind
a realistic paper gate whose primary job is to **falsify** the sim-to-real
transfer before any capital is at risk.

## Honest framing — what the paper gate is for

The PPO policy was trained on **simplified engine primitives** in `src/rl/env.py`,
not the production Rust engines — and on out-of-sample data it shows **no
return edge** vs a clean RF baseline (ETH p=0.77, BNB p=0.48; see
`docs/rl_walk_forward_results.md`). So the prior is: this model probably does
not deserve to trade live. The paper gate exists to **test that prior against
the real engines cheaply**, in a way that *cannot* touch capital:

1. Does the sim-trained policy behave sensibly on the real Rust engines
   (sim-to-real fidelity), or does its behaviour diverge from what `env.py`
   predicted?
2. Does it produce a positive risk-adjusted return on **realistic** paper
   (slippage + fees modelled), or does the no-edge result hold?

**If the paper run confirms no edge, we do NOT go live.** The gate is a
falsification test, not a rubber stamp. "Go live after the experiment" means
*only if the experiment passes*.

## Architecture decisions (with rationale)

### D1 — Run a **separate paper Rust instance**, don't touch the live one

Paper mode is per-Rust-instance (one `Connector` shared by all strategies —
blocker #2). Per-strategy paper gating doesn't exist. So the clean, zero-risk
layout is **two Rust engines**:

| Instance | `exchange.testnet` | Routing | Capital |
|---|---|---|---|
| **Live (unchanged)** | false | current RF-regime (status quo) | real/paper per its current config |
| **PPO paper (new)** | **true** | PPO, via the gate below | **synthetic fills only** |

The paper instance ingests the **same real market data** (Binance WS) as live
but fills synthetically via `PaperTradeConnector`. It cannot reach real capital
by construction. Compare PPO-routed paper P&L against the live instance's
un-routed P&L on identical market conditions.

### D2 — Push decisions Python → Rust (paper phase); ONNX-in-Rust (live phase)

The bridge is one-way (blocker #1); Rust can't query Python per-bar. Two options:

- **Paper phase: the regime-cache push pattern (reuse what exists).** Python
  computes the routing decision each bar, `POST /api/v1/routing` writes a
  `RoutingCache` in Rust, `tick_strategies` reads it — exactly like the existing
  `RegimeCache` / `POST /api/v1/regime`. Fast to build, lets us iterate.
- **Live phase: ONNX-in-Rust (hard requirement for promotion).** Export the PPO
  actor to ONNX (template: `scripts/convert_to_onnx.py` already does this for the
  RF regime classifier), load it in Rust. No Python hot-path dependency — a
  Python crash can never stall live routing. This is a *promotion gate*, not
  needed for the paper experiment.

### D3 — New Rust machinery to actually apply a routing decision

A routing output `(engine_choice, size_mult, flat)` is meaningless without
engine-level support, which **does not exist today** (blockers #3, #4). Add,
scoped to the paper instance only:

- **`RoutingCache`** (`trading-engine-core/src/strategy/routing_cache.rs`)
  mirroring `regime_cache.rs`: holds the latest `{active_engine, size_mult, flat_flag, ts}`.
- **`tick_strategies` gate** (`engine.rs`): consult `RoutingCache` and call the
  existing-but-unused `Strategy::set_paused(true)` on every non-active engine.
  (The trait method exists; nothing calls it today.)
- **`flat` op per strategy**: a new `Strategy::force_flat()` that closes all open
  positions in that strategy and sets an entry-suppress flag until un-flatted.
- **Size multiplier**: multiply the `desired` passed to
  `CapitalManager::request_capital(name, desired)` (or equivalently scale
  `risk_per_trade_pct`) by `size_mult` for the active engine.

### D4 — Realistic paper layer (the whole point of "paper, not skipping it")

`PaperTradeConnector` already has realism knobs (`slippage_bps`, `taker_fee_bps`,
`maker_fee_bps` in the `paper:` block of `config/strategy.yaml`). The paper
instance must set these to **live-realistic values** (e.g. slippage 5–10 bps,
real maker/taker fees), not the zero-slippage default that inflated prior paper
P&L (per `paper_vs_live_realism.md`). This is non-negotiable — a zero-slippage
paper run is worthless as a falsification test.

### D5 — Python routing service (paper phase)

A new `src/rl/live_router.py` (loop, not part of the signal listener):
1. Each closed 1h bar: load the latest klines, `compute_features`, build the
   19-dim observation (same as `env.py::_build_obs` — must match column-for-column
   or the policy sees a different distribution).
2. `PPORouter(model_path).predict(obs, deterministic=True)` → action → decode
   `(engine, size_mult, flat)`.
3. `POST /api/v1/routing` to the paper instance.
4. Log decision + (later) realised paper P&L attribution per engine.

## Sim-to-real fidelity validation (the core test)

Before trusting any paper P&L number, validate that the policy behaves on the
**real** Rust engines consistently with what `env.py` predicted on the same bars:

- Replay a historical window through **both** `env.py` (the toy engines) **and**
  the paper Rust instance (real engines, same bars, same decisions).
- Compare per-bar: equity trajectory, engine selection, position sizing.
- **Pass criterion:** equity trajectories correlate above a stated threshold
  (e.g. Pearson > 0.7) and mean per-bar P&L difference within a stated band.
  If they diverge, the toy engines don't represent the real ones → the trained
  policy is built on a wrong simulator → **stop, do not promote**. This is the
  sim-to-real gap made measurable.

## Safety (paper instance)

- **Capital isolation by construction** — testnet connector, synthetic fills,
  no API keys to a funded account. The worst case is wasted compute.
- **Kill switch** — `routing.enabled: false` in config → paper instance falls
  back to ticking all engines flat/static.
- **Position limits** — paper instance respects the same `CapitalManager`
  budgets; size multiplier capped at 1.5x (max action).
- **Circuit breaker** — if paper equity drawdown > X% (e.g. 20%), auto-pause
  routing and alert.

## Promotion criteria (paper → live) — all must hold

1. **Fidelity**: sim-to-real validation passes (D5 test, above).
2. **Duration**: ≥ 4–8 weeks of paper running across varied regimes.
3. **Edge on realistic paper**: PPO-routed paper risk-adjusted return
   **beats the un-routed (RF-regime) paper baseline** over the period — not
   just absolute positive, relative to the alternative.
4. **No live-only pathologies**: rejection rate, slippage, partial-fill rate
   within modelled bounds.
5. **Hardening done**: ONNX export in Rust (D2), no Python hot-path dependency.
6. **Position sizing reviewed**: live max_position_pct / leverage conservative.

If any criterion fails, the model stays on paper (or is abandoned). Promotion is
a human decision, not automatic.

## Scope (Phase 1 — the paper experiment)

- **One pair: ETHUSDT** (most data; clean 24m model already trained).
- **Paper instance only.** Live instance untouched.
- **PPO model**: `models/rl/ppo_ETHUSDT_2026-07-05_clean-oos-24m.zip`.
- Realistic slippage/fees; ≥4-week run; fidelity test first.

## Phasing

- **Phase 1 (this spec):** paper-gated integration — fidelity test + paper run.
  Deliverable: a yes/no on whether PPO deserves live promotion.
- **Phase 2 (only if Phase 1 passes):** ONNX-in-Rust + promotion into the live
  instance behind a conservative size cap. Separate spec.

## Open questions for you

1. **Two-instance OK?** Running a second Rust engine (paper, port 3031) alongside
   live — is that acceptable on the EC2 host, or do you want per-strategy paper
   gating in one instance (more Rust surgery)?
2. **Paper realism values** — confirm slippage/fee bps to model (propose 8 bps
   slippage, real Binance maker/taker).
3. **Promotion bar** — agree the 6 criteria above are the gate, especially #3
   (must beat the un-routed paper baseline, not just be positive)?
4. **Duration** — 4 weeks minimum acceptable, or longer?
5. **Honest prior** — accepting that this may end in a "no-go, don't go live"
   result? (The evidence so far says that's the likely outcome.)

## Architecture references (from the live-bot map)

- Rust orchestration loop: `trading-engine-core/src/engine.rs` — `Engine::run`,
  `tick_strategies` (the integration point), `process_paper_fills`.
- Static strategy registration at boot: `trading-engine-core/src/main.rs`.
- `Strategy` trait (incl. unused `set_paused`): `trading-engine-core/src/strategy/mod.rs`.
- `CapitalManager` (per-strategy budgets, `request_capital`):
  `trading-engine-core/src/capital/mod.rs`.
- Existing Python→Rust per-bar precedent: `RegimeCache`
  (`trading-engine-core/src/strategy/regime_cache.rs`) + `POST /api/v1/regime`
  (`api/handlers.rs`).
- HTTP bridge one-way Python→Rust: `src/run_signal_listener.py` (urllib to :3030).
- Paper connector + realism knobs: `connector::paper::PaperTradeConnector`,
  config `paper:` block in `config/strategy.yaml`.
- PPO policy (research-only, not wired): `src/rl/router.py::PPORouter`.
- Deploy topology: `docker-compose.hybrid.yml` + `Dockerfile.hybrid`.
