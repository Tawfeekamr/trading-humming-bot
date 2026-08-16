# Manuscript Revision Report — Batch 4 (2026-08-16)

Restructuring pass on `docs/dissertation_manuscript.md`. Documentation
only; no code, models, evaluation artifacts, or reports/ were modified.
Empirical results frozen at commit `8f9810e`.

---

## 1. Files changed

| File | Change |
|---|---|
| `docs/dissertation_manuscript.md` | Retitled (T1); retraction list moved to new methodology §3.6 (T2); Chapter 4 reordered with learned-withdrawal first (T3); reward's B&H-relative term foregrounded in §3.1 + cross-ref in §4.1 (T4); one unsourced figure replaced (T6); limitations §5.3 added (T7) |
| `docs/study_docs/research_roadmap.md` | Retitled + retitle note (T1) |
| `docs/study/00_thesis_structure.md` | Retitled + pre-audit caveat (T1) |
| `docs/rl_walk_forward_results.md` | SUPERSEDED banner: its "return parity", "PPO does risk control better", legacy-RF p=0.0019/<0.0001 and mislabelled-DM conclusions withdrawn; body kept as historical record (T5) |
| `docs/study/supervised_baseline_proof_plan.md` | Superseded-title note (T5) |
| `docs/superpowers/specs/2026-06-18-rl-execution-agent-design.md` | Superseded-title note (T5) |
| `FIX_REPORT.md` | B2.2 random-entry medians corrected to final artifact values with annotation (T6) — a report, not an evaluation artifact; figure text only |

**New title (T1):** *Learned Withdrawal and Evaluation Blindness in
Reinforcement Learning for Trading: A Corrected-Protocol Case Study*
— replaces the 22-word verdict-promising title with one naming the two
actual findings.

**Notes:** `project_summary.md` (named in the task) no longer exists in
the repo — deleted in an earlier commit; verified via git history
(introduced c8c49e2, absent from all branches). `proposal.txt` retains
the original pre-thesis title deliberately as a historical record.
`AUDIT_REPORT.md` is untracked and quotes old titles as part of the
audit — untouched.

## 2. Traceability table (Task 6)

43 numeric claims checked. All verified against committed artifacts:

| Claim | Value | Source | MS § |
|---|---|---|---|
| ETH B&H / RF / PPO total return | +44.8% / −22.3% / −13.1% | rl_walk_forward_ETHUSDT.json (0.4483 / −0.2232 / −0.1310) | 4.2, Abstract, 5.1 |
| BNB B&H / RF / PPO total return | +3.8% / −17.0% / −29.0% | rl_walk_forward_BNBUSDT.json | 4.2, Abstract, 5.1 |
| ETH PPO/RF/TA MaxDD | 0.165 / 0.247 / 0.511 | same | 4.2 |
| BNB PPO/RF/TA MaxDD | 0.293 / 0.426 / 0.503 | same | 4.2 |
| ETH/BNB PPO Sharpe | −1.74 / −3.04 | risk_inference.sharpe_all_bars | 4.2 |
| ETH/BNB RF Sharpe | −1.86 / −0.72 | same | 4.2 |
| ETH/BNB PPO capital exposure | 4.3% / 5.2% | exposure_definitions.json | 4.1, 4.2 |
| ETH/BNB RF capital exposure | 21.1% / 60.5% | same | 4.1, 4.2 |
| ETH/BNB p (HAC test) | 0.665 / 0.743 (Holm 1.00) | metrics.ppo_vs_rf | 4.3 |
| ETH/BNB MDE (80% power) | 59.7pp / 102.7pp | mde_power.json | 4.3.1, Abstract |
| ETH/BNB achieved power | 7.2% / 6.2% | mde_power.json | 4.3.1 |
| ETH/BNB n required | 180,943 (20.7y) / 314,103 (35.9y) | mde_power.json | 4.3.1, 5.3 |
| flat policy reward ETH/BNB | −0.448 / −0.038 | exposure_diagnosis.json | 4.1 |
| trained reward ETH/BNB | −0.994 / −0.854 | same | 4.1 |
| trained flat share ETH/BNB | 41.9% / 45.8% | same | 4.1 |
| untrained flat share ETH/BNB | 0.2% / 0.0% | same | 4.1 |
| penalty term ETH/BNB | 0.310 / 0.401 | same | 4.1 |
| PnL term ETH/BNB | −0.579 / −0.328 | same | 4.1 |
| binding test | 73–83% of max; 41–43% ≥90% | same | 4.1 |
| capital-matched random median MaxDD | 0.0509 / 0.0508 | exposure_match_*.json | 4.2 |
| PPO percentile in random dist | 100th both | exposure_match_*.json | 4.2, 3.6#7 |
| exposure ratio RF/PPO | 4.9× / 11.5× | exposure_definitions.json | 4.2 |
| BNB fold3 seed7 / seed999 return | −22.99% / +3.95% (27pp swing) | seed_sensitivity.json | 4.3.2, Abstract |
| seed MaxDD range | 0.007 vs 0.312 (45×) | same | 4.3.2 |
| paired stat across seeds BNB | −0.92 to +0.80 | same | 3.6 note, 4.3.2 |
| false-DANGER rate ETH/BNB | 47% / 38% | regime_conditioned.json (0.4725/0.3849) | 4.5, Abstract |
| missed-danger rate ETH/BNB | 36.9% / 17.9% | same (0.3688/0.1789) | 4.5 |
| DANGER mean fwd return | −0.79% / −0.68% | false_danger_corrected.json | 4.5, 5.1 |
| per-signal false-DANGER mean | +3.34% / +3.88% | same | 3.6#8 |
| 24-bar block gap | 0.96pp / 0.72pp | same | 3.6#8, 4.5 |
| portfolio gap gated−B&H | −67.1pp / −20.8pp | same | 4.5, 5.1 |
| block returns flagged/unflagged | −0.57/−0.49 vs +0.39/+0.23 | same | 4.5 |
| ETH TA MaxDD flip | 0.353 → 0.511 | FIX_REPORT B2.1 | 3.6#5 |
| invested-Sharpe old→new | −4.47→−1.75 / −7.81→−3.05 | FIX_REPORT B2.6 | 3.6#6 |
| retracted false-DANGER totals | ~~2,242.9pp / 1,183.2pp~~ | FIX_REPORT B3.2 | 3.6#8 |
| protocol figures (rows/slices) | 769→719 rows; 6 slices; 70-bar embargo | FIX_REPORT §2–3 | 3.6#1–2 |

## 3. Claims deleted or fixed for lack of source

1. **"capital-matched random-entry median MaxDD 2.9–3.5%"** (manuscript
   4.2 + 3.6#7) — sourced from a FIX_REPORT draft line, but the final
   committed artifacts (`reports/exposure_match_*.json`) give
   **0.0509 / 0.0508 (5.1% both)**. REPLACED with the artifact values
   in both manuscript locations; FIX_REPORT B2.2 annotated. (The
   conclusion — PPO at 100th percentile — was verified against the
   artifacts: both report 100.0.)
2. **Ch 5.1 "mean forward return −0.7% to −1.0%"** — mixed two artifact
   sources (regime_conditioned.json vs false_danger_corrected.json
   compute DANGER means on different valid-mask sets). REPLACED with
   4.5's sourced figures (−0.79%/−0.68%).

No claim was reconstructed from memory; both fixes cite the artifact.

## 4. Withdrawn figures — gone (grep proof)

`grep -rn` over `docs/dissertation_manuscript.md`:

| Pattern | Result |
|---|---|
| `1.85`, `2.40` (Sharpe/Sortino) | only inside the 4.2 withdrawal note ("previously reported table (Sharpe 1.85, MaxDD -0.4%...) is withdrawn") |
| `p = 0.14` / DM=1.48 | only inside 3.6#4 describing the mislabelled statistic |
| `0.0019`, `0.0000` (legacy-RF p) | **zero hits** |
| `2,243`, `1,183` | only inside 3.6#8 with strikethrough + "retracted" |
| `0.995`, `0.928` (n=6 corr) | **zero hits** |
| "return parity" as a claim | **zero hits** (the phrase appears only as withdrawn/superseded text in secondary docs' banners) |

Secondary-doc sweep (`*.md`/`*.txt`, excluding FIX_REPORT/AUDIT/
reports/): the only pre-audit carriers remaining are
`docs/rl_walk_forward_results.md` (now banner-marked SUPERSEDED),
`proposal.txt` (historical proposal, intentionally preserved), and
`AUDIT_REPORT.md` (untracked audit document quoting old titles as
evidence). README.md checked: describes the bot, no withdrawn
performance figures.

## 5. Reward-function discrepancy check (Task 4.3)

**No discrepancy.** Manuscript §3.1 states
`r_t = (R_eq − R_bh) − f·Turnover − λ·ΔDD, λ=0.5`, verified against
`src/rl/env.py:349-356` (reward assembly) and `env.py:352`
(`bench_return = (bar_close − prev_close)/prev_close`). The
manuscript now cites these lines. Semantic point added and verified:
on a flat bar `R_eq = 0`, so the reward equals `−R_bh` — a rising bar
penalises abstention by exactly the foregone passive gain. The
withdrawal therefore occurred **despite** an explicit anti-withdrawal
incentive in the first term.

## 6. Overclaim check (final pass)

One remaining framing choice flagged rather than silently accepted: the
Abstract's second paragraph still describes the dissertation as
proposing a "regime-aware multi-asset execution framework" and
conducting a "controlled empirical benchmark" — vocabulary from the
pre-audit framing. It is defensible as a description of the apparatus,
but "execution" retains the connotation the audit rejected in the
title. Recommend a future wording pass on those two sentences; not
changed here because no corrected replacement wording was specified
and the apparatus description is factually accurate.
