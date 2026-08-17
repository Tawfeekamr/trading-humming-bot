# PRIOR_ART.md — adversarial prior-art search against the claimed contributions

Executed 2026-08-17 (searches 1–5 and part of 6 run 18:27–18:44 in the
prior session, recovered from its transcript; searches 6-completion and
four follow-ups run 2026-08-17 ~23:00). Thesis results untouched; this
document exists so no novelty claim survives that a reviewer could
overturn with one citation.

The four claims tested (manuscript §5.2 / §4.1):

- **D1** flat-policy floor — compare a trained policy against permanent
  abstention under the SAME reward before concluding it learned anything.
- **D2** capital-exposure matching — match baselines on capital-weighted
  exposure (mean |position|/equity), not time-in-market.
- **D3** reward-component decomposition — report each reward term's
  magnitude so a dominating penalty is visible.
- **P1** the phenomenon — an agent converging to reward-induced
  abstention that is invisible in standard metrics because it produces
  a BETTER drawdown than the baseline (reads as good risk management).

---

## Section 1 — Verdict per contribution

### D1. Flat-policy floor — **PARTIALLY ANTICIPATED**

The mechanical comparison — score a do-nothing policy on the same
objective as the learned policy — is established practice outside
trading, and acknowledged inside it:

- **L2RPN (Learning to Run a Power Network) competition series** — the
  "Do Nothing" agent is the institutionalised reference: every
  competitor's score is normalised so that doing nothing scores 0
  (Marot et al., "Learning to run a Power Network Challenge: a
  Retrospective Analysis", arXiv:2103.03104, 2021 —
  https://arxiv.org/abs/2103.03104; scoring documented on the CodaLab
  competition pages, e.g. L2RPN Delft 2023 —
  https://codalab.lisn.upsaclay.fr/competitions/12420). In power-grid
  RL, "compare against doing nothing" is not a diagnostic anyone gets
  credit for; it is the scoreboard.
- **Market-making RL** — "Market Making With Signals Through Deep
  Reinforcement Learning" (IEEE Access, vol. 9, 2021 —
  https://ieeexplore.ieee.org/iel7/6287639/9312710/09410223.pdf)
  states: "even a trivial policy for which At = [0, 0] ∀t provides a
  relatively strong benchmark." The no-quote policy being competitive
  is stated as a known property of the setting.
- **Trading RL near-misses, all comparing on PERFORMANCE metrics, not
  the reward function:**
  - Hafsi & Vittori, "Optimal Execution with Reinforcement Learning"
    (arXiv:2411.06389, 2024 — https://arxiv.org/abs/2411.06389): a
    "Passive Policy" baseline that does nothing 60% of the time — but
    the comparison metric is implementation shortfall.
  - Zhang, "Law-Strength Frontiers and a No-Free-Lunch Result…"
    (arXiv:2511.17304, 2025 — https://arxiv.org/abs/2511.17304): a
    "zero-hedge" identically-zero structural baseline — compared on
    P&L, penalties, GFI.
  - Mohl et al., "JaxMARL-HFT" (arXiv:2511.02136, 2025; also ACM
    DOI 10.1145/3768292.3770416 — https://arxiv.org/abs/2511.02136):
    market makers "learn to trade very infrequently", and the paper
    states that under this reward family "an optimal policy seems to
    be to never trade" (§5.2.1; Fig. 4(a): "The market making agent
    mostly refrains from trading"). This is an OBSERVED emergent
    behaviour plus an analytic remark — no flat-policy baseline is
    run; comparisons are portfolio value / slippage vs TWAP and
    Avellaneda–Stoikov.
- Also found (other domains): do-nothing baselines in power-grid
  topology RL and the CECOR framework using a Do-Nothing baseline
  specifically to test whether evaluation METRICS discriminate
  (arXiv:2605.02277 — https://arxiv.org/html/2605.02277v2);
  constant-action policies in the Arcade Learning Environment
  methodology; "beating the do-nothing baseline" in a 2025
  credit-limit RL paper.

**What differs, and it is narrow:** no found work in trading runs the
permanently-flat policy through the environment and scores it under
the TRAINING REWARD as a test of reward misspecification ("the reward
does not distinguish the policy from doing nothing"). Every trading
hit scores abstention on performance metrics (IS, P&L, Sharpe,
slippage). L2RPN scores do-nothing on the same objective but as score
normalisation, not as a misspecification diagnostic. The thesis's
19-of-20 flat-outscores-trained cells (§4.1) is a use of the
comparison, not an invention of it — the manuscript already says so
("Origin: naive-baseline flooring (RL evaluation practice)"), and
that attribution is confirmed accurate.

Search terms used (negatives are checkable): "no-op / do-nothing
baseline reinforcement learning evaluation"; "trivial policy
outperforms learned policy Atari constant action"; "degenerate policy
reward hacking abstention"; "agent learns to do nothing penalty term
dominates".

### D2. Capital-exposure matching — **PARTIALLY ANTICIPATED**

The finance literature on benchmark-relative evaluation with exposure
control is old and deep, and it anticipates the PRINCIPLE:

- Benchmark-relative attribution is standard (Brinson-model
  attribution and its descendants; CFA Institute performance
  attribution guidance — https://rpc.cfainstitute.org/topics/performance-attribution).
- Active Share (Cremers & Petajisto — https://activeshare.nd.edu)
  measures how far holdings deviate from the benchmark before returns
  are compared.
- Long-short evaluation reports NET EXPOSURE as a mandatory
  disclosure alongside returns — the practice of not comparing
  strategies at different deployment is institutionalised
  (e.g. https://www.caisgroup.com/articles/an-introduction-to-long-short-equity-strategies).
- Beta-matched benchmark comparison (Frazzini & Pedersen, "Betting
  Against Beta", JFE 2014 —
  https://www.sciencedirect.com/science/article/pii/S0304405X13002675).
- Practitioner literature explicitly discusses the Sharpe/exposure
  scaling trap ("Sharpe ratio adjusted for time in market",
  r/quant — mirrors this thesis's own B2.6 annualisation artifact).

**What differs:** portfolio attribution matches on beta, holdings, or
net exposure of PORTFOLIOS. No found work — in attribution or in RL
trading evaluation — matches a learned POLICY against random-entry
BASELINES on capital-weighted exposure (mean |position|/equity over
the evaluation window) before comparing risk metrics. The specific
construction (exposure-matched random-entry percentile bands as the
null distribution for a MaxDD comparison) was not found. The
manuscript's origin note ("exposure matching (portfolio
attribution)") is accurate and should carry the citations above.

Search terms used: "exposure-matched baseline portfolio evaluation";
"risk-adjusted comparison unequal capital deployment"; "cash drag
benchmark comparison low exposure strategy"; "beta-matched benchmark
portfolio attribution"; "average exposure matched benchmark";
"'time in market' versus 'capital deployed' performance comparison".

### D3. Reward-component decomposition — **ALREADY ESTABLISHED**

Both as a published research area and as documented engineering
practice:

- **Research area (XRL):** "Distributional Reward Decomposition for
  Reinforcement Learning" (NeurIPS 2019 —
  http://papers.neurips.cc/paper/8852-distributional-reward-decomposition-for-reinforcement-learning.pdf);
  "RD2: Reward Decomposition with Representation Disentanglement"
  (Microsoft Research, 2020 —
  https://www.microsoft.com/en-us/research/wp-content/uploads/2020/11/learning_multiple_abstractions-4.pdf);
  "Explainable Reinforcement Learning via Reward Decomposition"
  (IJCAI — https://finale.seas.harvard.edu/publications/explainable-reinforcement-learning-reward-decomposition);
  policy summaries combined with reward decomposition (U. Augsburg
  dissertation —
  https://opus.bibliothek.uni-augsburg.de/opus4/files/105945/105945.pdf).
- **Engineering practice:** the torchrl debugging guide explicitly
  instructs checking "whether the agent is favoring a single
  component of the reward function" — "Things to consider when
  debugging RL", torchrl 0.13 documentation —
  https://docs.pytorch.org/rl/stable/reference/generated/knowledge_base/DEBUGGING_RL.html;
  logging individual reward terms to TensorBoard is a routine
  ask/answer in RL tooling forums (Isaac Gym/Lab, SB3).

**Consequence for the manuscript:** the origin note for contribution
2b — "*Origin: component reporting (econometric model diagnostics)*" —
names the WRONG origin discipline. The direct origins are inside RL:
the XRL reward-decomposition literature and RL debugging practice.
Revision required (Section 3). What survives on D3 is only the case
documentation: a trading evaluation in which the absence of the
(practice-established) decomposition hid a drawdown penalty that
rivals the entire PnL term plus a fee double-count.

### P1. Invisible abstention / flattering risk metrics — **PARTIALLY ANTICIPATED**

The raw inversion — an abstaining policy scoring BETTER on a risk
metric — IS documented:

- **"Robust Market Making: To Quote, or not To Quote"** (arXiv:2508.16588,
  2025 — https://arxiv.org/abs/2508.16588): agents that can refuse to
  quote; "occasional refusal to provide bid-ask quotes improves
  returns and/or Sharpe ratios." Abstention improving a risk-adjusted
  metric is PUBLISHED — but framed as a beneficial, designed
  adaptation (quoting ratios remain >95%), not as a failure mode
  invisible to evaluation.
- **JaxMARL-HFT** (arXiv:2511.02136): never-trade optimal under a
  reward family — recognised, but treated as a reward-DESIGN
  drawback, not an evaluation problem. The same framing holds in
  "Market Making Strategies with Reinforcement Learning"
  (arXiv:2507.18680, 2025 — https://arxiv.org/abs/2507.18680:
  inventory handled by "reward engineering and Multi-Objective
  Reinforcement Learning"). Across this sub-literature the no-trade
  collapse is consistently a reward-design problem; no found work
  treats it as an EVALUATION-blindness problem.
- **Selective prediction (classification)** — the closest structural
  analog, decades old: Chow's reject option (1970); Geifman &
  El-Yaniv, "Selective Classification for Deep Neural Networks"
  (NeurIPS 2017 —
  https://proceedings.neurips.cc/paper_files/paper/7073-selective-classification-for-deep-neural-networks.pdf);
  SelectiveNet (ICML 2019 — https://proceedings.mlr.press/v97/geifman19a.html).
  Abstention improving accuracy is the field's FOUNDING observation —
  and its remedy (reporting the risk–coverage curve, i.e. making
  abstention VISIBLE by reporting its rate) is institutionalised.
  A reviewer can cite this as "abstention flattering metrics while
  being under-reported is known, with a known cure."
- **The lazy agent problem (MARL):** Liu et al., "Lazy Agents: A New
  Perspective on Solving Sparse Reward Problems in Multi-Agent
  Reinforcement Learning" (ICML 2023, PMLR v202 —
  https://proceedings.mlr.press/v202/liu23ac.html): agents learn to
  do nothing while reward accrues via teammates. Known phenomenon,
  different mechanism (free-riding requires other agents), but a
  reviewer will name it against "the agent learned to do nothing
  because the reward permitted it."
- **Practitioner folklore** (not literature, but shows the failure is
  recognised at the workbench): "reward shaping around drawdown tends
  to produce overly conservative policies that learn to do nothing"
  (r/algotrading); "What to do when the agent does nothing?"
  (r/reinforcementlearning). General metric-gaming framing exists
  (Goodhart's Law in RL, arXiv:2310.09144 —
  https://arxiv.org/html/2310.09144; Weng's reward-hacking survey
  does NOT cover inaction as a hacking strategy — closest example is
  an agent circling a goal).

**What differs:** no found work, in any domain, makes the thesis's
specific argument — that DEGENERATE reward-induced abstention reads
as good risk management in standard trading metrics (a better
drawdown than the baseline), is invisible without exposure matching,
and survived four self-audit passes in a real evaluation before being
caught. The inversion's components all exist separately (abstention
improves Sharpe: To-Quote; abstention invisible without coverage
reporting: selective prediction; abstention from reward design: JaxMARL).
The combination as an evaluation-blindness case study was not found.
P1's framing survives; its phenomenon does not.

Search terms used for the negatives: "failure mode invisible to
standard metrics reinforcement learning"; "misleading risk metrics
low exposure strategy"; "drawdown metric gamed by inactivity";
"performance metric fails to detect degenerate policy"; ""do nothing"
policy "lower drawdown" OR "better Sharpe" than learned agent";
""inaction" OR "abstention" policy "outperforms" risk-adjusted
benchmark paper"; "lazy agent problem multi-agent reinforcement
learning".

---

## Section 2 — Citations to add

| # | Work | Why it must be cited | Where |
|---|------|----------------------|-------|
| 1 | Mohl et al., JaxMARL-HFT, arXiv:2511.02136 (2025) | Published instance of never-trade-optimal under a reward family — the closest published relative of the withdrawal finding | §4.7 (comparative rewards), §5.2a |
| 2 | "Robust Market Making: To Quote, or not To Quote", arXiv:2508.16588 (2025) | Documented case of abstention IMPROVING Sharpe (as designed behaviour) — both a positive-result cite and the closest prior instance of the P1 inversion | §4.6, §4.7 |
| 3 | Marot et al., L2RPN retrospective, arXiv:2103.03104 (2021) | Do-nothing baseline institutionalised in RL evaluation outside trading — the concrete origin for D1's "naive-baseline flooring" | §5.2a origin note |
| 4 | "Market Making With Signals Through Deep RL", IEEE Access 9 (2021) | "a trivial policy for which At=[0,0] ∀t provides a relatively strong benchmark" — explicit trivial-policy benchmark in trading RL | §5.2a, §4.6 |
| 5 | Hafsi & Vittori, arXiv:2411.06389 (2024) | Passive-policy (60% inactive) baseline precedent in execution RL | §4.6 |
| 6 | Liu et al., Lazy Agents, ICML 2023 (PMLR v202) | "Agent learns to do nothing while reward accrues" is a named MARL problem | §4.1 or §4.6 (related-work positioning) |
| 7 | Geifman & El-Yaniv, NeurIPS 2017 (+ Chow 1970; SelectiveNet ICML 2019) | Abstention-flatters-metrics is founding knowledge in classification, with coverage reporting as the cure — the closest structural analog to D2/P1 | §5.2c origin note; §4.1 (framing) |
| 8 | Distributional Reward Decomposition, NeurIPS 2019; RD2 (2020); Explainable-RL-via-Reward-Decomposition (IJCAI); torchrl debugging guidance | The actual origin discipline for D3 — supersedes the "econometric model diagnostics" attribution | §5.2b origin note (revision, see §3) |
| 9 | Cremers & Petajisto (Active Share); Brinson attribution; Frazzini & Pedersen (Betting Against Beta, JFE 2014) | The portfolio-attribution origin for D2, cited concretely | §5.2c origin note |
| 10 | Zhang, arXiv:2511.17304 (2025) | Zero-hedge identically-zero baseline precedent | §4.6 (optional) |
| 11 | Ma, "Myopic Optimality…", arXiv:2509.12764 (2025) | Recent negative result: MO beats RL in portfolio management | §4.6 (optional) |

---

## Section 3 — Claims requiring revision

The manuscript already scopes contribution 2 ("None of the
diagnostics was invented here") — grep finds no "first to", "novel",
or "no prior" sentence anywhere. Three items still need revision:

1. **§5.2 contribution 2b — origin attribution is wrong.**
   Current: "*Origin: component reporting (econometric model
   diagnostics);*"
   Problem: the direct origin is RL's own reward-decomposition
   literature and debugging practice (Section 1, D3). Naming
   econometrics invites a reviewer to supply the RL citations as a
   correction.
   Proposed: "*Origin: reward decomposition (XRL literature —
   Distributional Reward Decomposition, NeurIPS 2019; RL debugging
   practice — torchrl debugging guidance on checking whether one
   reward component dominates);*"

2. **§5.2 contributions 2a and 2c — origin claims are accurate but
   uncited.** Add the citations from Section 2 (rows 3, 4 for 2a;
   rows 7, 9 for 2c) so the origin claims are checkable rather than
   asserted.

3. **§5.2a — the surviving distinction should be STATED, currently
   it is only implied.** Add one scoped sentence after the flat-policy
   floor entry, e.g.: "As of an adversarial prior-art search
   (PRIOR_ART.md, 2026-08-17), do-nothing baselines are established
   outside trading (L2RPN scoring; trivial-policy benchmarks in
   market making) but every found trading use scores abstention on
   performance metrics (implementation shortfall, P&L, Sharpe) rather
   than under the training reward; the misspecification-test use
   appears unclaimed." Without this, the thesis's actual residual on
   D1 is invisible; with it, the claim is scoped to what the search
   supports.

Also required (content, not novelty): §4.7's comparative argument
("specifications at least as punitive … WITHOUT producing
abstention") should acknowledge arXiv:2508.16588, where abstention
appears by DESIGN and improves Sharpe — it is a positive-result
neighbour that also demonstrates abstention is not intrinsically a
failure; the thesis's claim is specifically about UNDESIGNED
abstention under a misspecified reward, and saying so pre-empts the
obvious reviewer conflation.

The abstract and §1–§4 were checked; no novelty overclaim found
there. No retraction of any result follows from this search.

---

## Section 4 — What survives

Stated plainly, after an adversarial search:

1. **The negative result and its evidence chain** — no policy beats
   buy-and-hold; the corrected protocol; the committed per-bar
   evidence. Untouched by prior art.
2. **D1's residual is narrow but real:** scoring permanent abstention
   under the TRAINING REWARD, as a reward-misspecification test, in a
   trading evaluation. The mechanic (do-nothing baselines) is
   established elsewhere; this use was not found.
3. **D2's residual:** exposure matching defined on capital-weighted
   position (mean |position|/equity) with random-entry percentile
   bands as the null for risk-metric comparisons. Attribution matches
   on beta/holdings/net exposure; this construction was not found.
4. **D3: nothing survives except the case** — the decomposition is
   established practice; the contribution is that its absence here
   hid two reward defects across four audit passes.
5. **P1's residual is the framing:** evaluation BLINDNESS —
   degenerate abstention reading as good risk management, invisible
   without exposure matching. Every ingredient is published
   separately (abstention-improves-Sharpe; abstention-needs-coverage;
   abstention-from-reward-design); the combination as an
   evaluation-blindness case was not found. The manuscript's own
   §3.6.1 argument (the false premise surviving four self-audit
   passes) remains this thesis's alone.

If only one thing survives untouched, it is the case study and its
evidence chain — which is what the manuscript already claims
(§5.2: "The claim is a documented case study in which each one's
absence changed a conclusion").

---

## Section 5 — Search limitations

1. **Web-index search only** (general web + arXiv), not a systematic
   Scholar/Scopus/WoS sweep; no forward or backward snowballing from
   the reference lists of the found papers. Systematic-review
   coverage this is not.
2. **Paywalls:** IEEE/Elsevier/Springer full texts were reachable
   only via indexed abstracts and snippets. Journal venues likely to
   hold relevant work (Quantitative Finance, JFM, Mathematical
   Finance, Management Science) were not systematically searched.
3. **Sub-literatures NOT covered:**
   - Offline RL pessimism/conservative policies (CQL and successors) —
     pessimism-induced inaction is adjacent and unsearched.
   - Classical (non-RL) market making: Avellaneda–Stoikov descendants
     study quoting regions and trading interrupts — optimal no-quote
     regions may be established there outside any RL framing.
   - Safe RL and constrained MDP literature on intervention costs.
   - Behavioural-cloning/preference baselines that default to the
     dataset (no-op) policy.
   - Deep-hedging literature beyond two probe searches.
4. **Folklore sources** (Reddit, forums) are cited as folklore — they
   evidence practitioner recognition, not literature.
5. **Date-stamp:** searches executed 2026-08-17; coverage reflects
   that day's index. The one search interrupted by the prior
   session's context overflow (Search 6, first two queries) was
   re-run and extended; its recovered results are incorporated.

Search evidence: 32 tool results from the prior session (18:27–18:44)
recovered verbatim from its transcript, plus 7 results this session;
all quotes above are short summaries, not extracts.
