# DOCS_UPDATE_REPORT — Documentation hygiene pass, 2026-10-23

Scope: `docs/dissertation_manuscript.md`, `docs/chapters/03_methodology.md`,
`docs/study/00–07_*.md`. No file under `src/`, `reports/`, `models/`,
`trading-engine-core/` was touched (verified: `git diff` over those paths is
empty). REPRODUCE.md untouched. Every figure written below exists in
REPRODUCE.md §B or the committed reports.

Tasks 1, 2, 4, 5, 6 applied. **Task 3 (abstract replacement) STOPPED per
the verification clause — see §3.**

---

## 1. Task 1 — Stale figures fixed (before/after)

### 1.1 Pooled drawdown shown as current — labelled as superseded basis

The replacement option (swap in per-fold medians) would have **flipped the
claim** at two of the three sites: against the exposure-matched random-entry
medians (0.0509/0.0508), the canonical PPO medians (0.032/0.053) would put
PPO *better* than random, not worse. The 0.165/0.293 values are correct for
what those sentences describe (percentile within the exposure-matched random
distribution, which was computed on the pooled basis for both sides), so the
fix is an explicit superseded-basis label, per the work order's "whichever
keeps the argument true".

**(a) manuscript §3.6 failure-register row (was line 369):**

> BEFORE: `on capital: random-entry median MaxDD 0.0509 (ETH) / 0.0508 (BNB) vs PPO's 0.165 / 0.293.`
>
> AFTER: `on capital: random-entry median MaxDD 0.0509 (ETH) / 0.0508 (BNB) vs PPO's 0.165 / 0.293 (both sides of this comparison on the superseded pooled basis; the canonical per-fold medians are 0.032 / 0.053, Section 4.2).`

**(b) manuscript §4.2 (was line 628):**

> BEFORE: `(ETH 0.0509 / BNB 0.0508, reports/exposure_match_*.json), versus PPO's 16.5-29.3% - PPO sits at the 100th percentile (worst) of the exposure-matched random distribution on both assets.`
>
> AFTER: `(ETH 0.0509 / BNB 0.0508, reports/exposure_match_*.json), versus PPO's 16.5-29.3% — both sides of this comparison on the superseded pooled drawdown basis, as computed by the exposure-matching diagnostic (the canonical per-fold medians are 3.2% / 5.3%, the table rows above) — PPO sits at the 100th percentile (worst) of the exposure-matched random distribution on both assets.`

**(c) Chapter 3 §3.8 (was line 370):**

> BEFORE: `medians are 0.0509 (ETH) and 0.0508 (BNB) against PPO's 0.165 and 0.293 —`
>
> AFTER: `medians are 0.0509 (ETH) and 0.0508 (BNB) against PPO's 0.165 and 0.293 (both sides on the superseded pooled basis; the canonical per-fold medians are 0.032 and 0.053, as stated above) —`

Already-labelled occurrences left untouched: ms §3.6 correction record
(`0.172 -> 0.165`), the §4.4 "pooled (old) | per-fold median (new)" table,
ch3 §3.8's "0.165 pooled versus a 0.032 per-fold median" contrast.

### 1.2 Arithmetic-basis B&H returns shown as current — replaced with compounded

**(a) manuscript §3.6 row 7 (was line 377):**

> BEFORE: `2,243pp of "foregone return" on a window where B&H returned +44.8% is`
>
> AFTER: `2,243pp of "foregone return" on a window where B&H returned +38.0% is`

Argument survives: 2,243pp against +38.0% is still impossible as portfolio
costs. Value = §B1 ETH ta +0.3797.

**(b) Chapter 3 §3.8 (was line 405):**

> BEFORE: `"foregone return" on windows where buy-and-hold returned +44.8% and +3.8% —`
>
> AFTER: `"foregone return" on windows where buy-and-hold returned +38.0% and −4.3% (compounded basis) —`

Values = §B1 ETH ta +0.3797, BNB ta −0.0427. Chapter 3 now agrees with its
own Appendix 3.A row 8 ("vs a +38% window"). The explicit correction record
at ch3 §3.7 (`+44.83% → +37.97%`) was already a labelled description of the
pre-correction value and is unchanged.

---

## 2. Task 2 — References completed

### 2.1 Six entries added to the manuscript reference list (as written)

- Agarwal, R., Schwarzer, M., Castro, P.S., Courville, A.C. and Bellemare, M.G. (2021) 'Deep Reinforcement Learning at the Edge of the Statistical Precipice', *Advances in Neural Information Processing Systems 34 (NeurIPS 2021)*.
- Amirzadeh, R., Thiruvady, D., Nazari, A. and Ee, M.S. (2023) 'A Framework for Empowering Reinforcement Learning Agents with Causal Analysis: Enhancing Automated Cryptocurrency Trading', arXiv:2310.09462.
- Felizardo, L.K. et al. (2022) 'Outperforming Algorithmic Trading Reinforcement Learning Systems: A Supervised Approach to the Cryptocurrency Market', *Expert Systems with Applications*, 202, Art. 117259.
- Gort, B.J.D., Liu, X.-Y., Sun, X., Gao, J., Chen, S. and Wang, C.D. (2022) 'Deep Reinforcement Learning for Cryptocurrency Trading: Practical Approach to Address Backtest Overfitting', arXiv:2209.05559.
- Kochliaridis, V., Kouloumpris, E. and Vlahavas, I. (2023) 'Combining Deep Reinforcement Learning with Technical Analysis and Trend Monitoring on Cryptocurrency Markets', *Neural Computing and Applications*, 35, pp. 21445–21462.
- Xue, P. and Ye, Y. (2025) 'Attention-Enhanced Reinforcement Learning for Dynamic Portfolio Optimization', arXiv:2510.06466.

All six verified by web search (arXiv abstract pages, NeurIPS proceedings,
Springer/ACM listings). List grew 29 → 35 entries; nothing deleted.

Verification notes and [UNVERIFIED] fields:

- **Kochliaridis et al.** — the work order's title fragment ("…technical
  analysis to handle trading frequency constraints") does not match the
  published title; the actual title is "Combining deep reinforcement
  learning with technical analysis **and trend monitoring on
  cryptocurrency markets**" (NCAA 35, 21445–21462, 2023). The entry above
  uses the published title. [UNVERIFIED: nothing — resolved by second
  search; recorded here because it contradicts the task brief.]
- **Felizardo et al.** — first three authors verified (Leonardo Kanashiro
  Felizardo, Francisco Caio Lima Paiva, C. de Vita Graves); the complete
  author list was not retrievable (ScienceDirect paywalled, ACM 403), so the
  entry uses the manuscript's existing et-al house style.
  [UNVERIFIED: complete author list beyond three names.]
- **Xue & Ye (2025)** — title/authors verified on arXiv; the arXiv abstract
  does **not** mention S&P 500 or 2020–2025. The manuscript's §4.6 claim
  ("PPO terminal wealth … (S&P 500, 2020–2025)") was left exactly as
  written per the no-claim-changes boundary, but the author should check
  that experimental detail against the full paper.

### 2.2 Bare identifiers replaced with author-year citations (before/after)

| Location | BEFORE | AFTER |
|---|---|---|
| ms §4.3.1 MDE table | `1.7pp/yr (arXiv:2510.06466, S&P RL)` | `1.7pp/yr (Xue & Ye, 2025 — S&P RL)` |
| ms §4.6 | `- **arXiv:2510.06466** (attention-enhanced RL): PPO terminal wealth` | `- **Xue & Ye (2025)** (attention-enhanced RL): PPO terminal wealth` |
| ms §4.6 | `- **Felizardo et al.** (Expert Systems with Applications): reports` | `- **Felizardo et al. (2022)** (*Expert Systems with Applications*): reports` |
| ms §4.6 | `- **arXiv:2310.09462** (causal RL): CRN-PPO reports positive ROI on` | `- **Amirzadeh et al. (2023)** (causal RL): CRN-PPO reports positive ROI on` |
| ms §4.6 | `- **arXiv:2209.05559**: PPO trained with combinatorial purged` | `- **Gort et al. (2022)**: PPO trained with combinatorial purged` |
| ms §4.6 | `citing Agarwal et al. 2021)` | `citing Agarwal et al., 2021)` |
| ms §4.6 | `- **10.1007/s00521-023-08516-x**: five PPO variants failed to beat` | `- **Kochliaridis et al. (2023)**: five PPO variants failed to beat` |
| ms §5.3 | `diagnosis. arXiv:2510.06466 and the Bitcoin-hourly PPO/SAC/TD3/A2C` | `diagnosis. Xue & Ye (2025) and the Bitcoin-hourly PPO/SAC/TD3/A2C` |
| ch3 §3.5 | `arXiv:2209.05559 documents the alternative` | `Gort et al. (2022) documents the alternative` |

### 2.3 Method citations added where the methods are introduced

- **Chapter 3 §3.2** (PPO introduced):
  > BEFORE: `The agent acts once per completed hourly bar.`
  >
  > AFTER: `The agent — trained with Proximal Policy Optimization (PPO; Schulman et al., 2017) — acts once per completed hourly bar.`
- **Manuscript §1.3** (first deep-RL mention in the manuscript; the
  manuscript's Chapter 3 is the compressed draft, so its §3.2-equivalent is
  covered by the ch3 edit above):
  > BEFORE: `…against Deep Reinforcement Learning (PPO) under simulated market frictions`
  >
  > AFTER: `…against Deep Reinforcement Learning (PPO; Mnih et al., 2015; Schulman et al., 2017) under simulated market frictions`

(Schulman et al. 2017 and Mnih et al. 2015 were already in the reference
list — no new entries needed.)

- **Chapter 3 closing references note** rewritten: the "Agarwal et al. 2021
  unverifiable" paragraph is replaced by a 2026-10-23 note recording that
  Agarwal was located/verified and the five remaining bare citations were
  resolved author-year.

### 2.4 Bailey & López de Prado (2014)

Left in the reference list, untouched. **To be cited in Chapter 2** — it is
currently uncited in body text (Chapter 2 does not yet exist).

---

## 3. Task 3 — Abstract replacement STOPPED (figure disagrees with §B)

Per the clause "if any figure disagrees, STOP and report rather than
inserting it": the proposed abstract was verified figure-by-figure against
REPRODUCE.md §B and **one figure disagrees**.

> Proposed: "…while **holding a position on 58% of bars**"

§B5a (re-run today, matches committed expected output):
`ETHUSDT trained A/B/C: 1812 / 1979 / 529 (sum 4320)` — the trained ETH
agent held inventory (position units ≠ 0) on **529/4,320 = 12.2% of bars**.
58.1% (2,508/4,320) is the share of bars on which it selected a *non-FLAT
action* — the active-action share. Calling that "holding a position"
conflates the active-action share with the inventory share, which is
precisely the explicit/concealed-abstention distinction the abstract's own
third paragraph draws (the grid primitive "acquires a position on only
10.2% of the bars where it is selected"). The manuscript's §4.1 makes the
same distinction ("a policy reported at '58% exposure' was in fact
deploying 4.3% of capital").

Every other figure in the proposed text verified against §B: 0.032/0.107
(ETH) and 0.053/0.117 (BNB) medians ✓ (B2); 4.3%/49.2% capital exposure ✓
(B5); 19 of 20 ✓ (B4); 20.6 + 24.3 pp components and 41.9% FLAT ✓ (B5a;
REPRODUCE §A row 8); 82–179pp MDE ✓ (§A row 6); eighteen corrections ✓
(register rows 1–18).

**Proposed minimal rewording (needs author approval):**

- "while holding a position on 58% of bars" →
  "while selecting active actions on 58% of bars", or
- "while recording time in market of 58%".

**Proposed text word count: 297** (within the 250–350 target; computed on
the text as provided, before any rewording).

The existing 412-word abstract remains in the manuscript unchanged, and is
not reproduced here (nothing superseded it). Once the author picks a
wording, the swap is a two-minute edit.

---

## 4. Task 4 — §5.2 sentence-structure repairs (before/after)

Each diagnostic's `*Origin: …*` block was spliced mid-sentence. All six
repaired the same way: the sentence is rejoined, the Origin block moves to
the end of the diagnostic's paragraph (after Cost / Would-have-caught), and
the block's trailing `;*` becomes `.*` where it now ends a sentence. No
claim, citation, or Residual text changed.

- **(a) flat-policy floor** — "Compare any trained policy against
  *Origin: …* permanent abstention under the same reward before concluding
  it learned anything." → "Compare any trained policy against permanent
  abstention under the same reward before concluding it learned anything."
  followed by Cost/Would-have-caught, then the Origin block, then the
  unchanged Residual block.
- **(b) reward-component decomposition** — "Report the magnitude of each
  *Origin: …* reward term, not just the total…" → sentence rejoined; the
  Origin block (including its superseded-attribution note) now closes the
  item after the "Nothing survives…" passage.
- **(c) capital-exposure matching** — "Match baselines on *Origin: …*
  capital-weighted exposure, not time-in-market, and report both." →
  rejoined; Origin moved to end.
- **(d) fold-aware pooling** — "Never concatenate non-adjacent test
  *Origin: …* windows into a single equity curve…" → rejoined; Origin
  moved to end.
- **(e) power reporting** — "State the minimum detectable effect
  *Origin: …* alongside any null result…" → rejoined; Origin moved to end.
- **(f) seed-distribution reporting** — "Never report a single-seed RL
  *Origin: …* result." → rejoined; Origin moved to end.

---

## 5. Task 5 — June skeletons marked superseded

Prepended to each of `docs/study/00_thesis_structure.md`, `01_abstract.md`,
`02_introduction.md`, `03_literature_review.md`, `04_methodology.md`,
`05_system_architecture.md`, `06_results_evaluation.md`,
`07_conclusion.md` (nothing deleted):

> **SUPERSEDED (June 2026, pre-audit framing). Do not use as draft text.
> Current manuscript: docs/dissertation_manuscript.md.**

---

## 6. Task 6 — TOC synchronised

In `docs/dissertation_manuscript.md`, appended " (to be written)" to every
TOC entry whose section does not exist in the body: List of Figures, List
of Tables, §1.5, Chapter 2 and 2.1–2.5, §3.4, Appendix 1. The Chapter 3
entry now reads:

> Chapter 3: System Methodology & Mathematical Formalism — current
> version: docs/chapters/03_methodology.md (the section below is an
> earlier compressed draft)

---

## 7. Task 7 — Verification

1. **REPRODUCE.md §B re-run** (system python3, pandas 2.3.3, from repo
   root, read-only over committed CSVs): B1 all six returns, B2 all six
   medians, B4 (19/20, exception BNBUSDT/3/999/+0.0143), B5 all four
   anchor rows, B5a all four A/B/C rows — **every value matches the
   expected outputs printed in §B**.
2. **Stale-figure grep** (`0.165|16.5|29.3|44.8|+3.8%` over both
   dissertation files): every remaining occurrence is inside an explicitly
   labelled historical/correction context (correction records, the
   "pooled (old) vs per-fold median (new)" table, the ch3 `+44.83% →
   +37.97%` correction record, the now-labelled superseded-basis
   sentences). **No unlabelled current use.**
3. **Freeze check**: `git diff --stat -- src/ reports/ models/
   trading-engine-core/` — **empty**.
4. **Commit**: one docs-only commit. The work order's prescribed subject
   ("docs: abstract reframed, stale figures fixed, references completed")
   would be false — the abstract was NOT reframed (Task 3 stopped), so the
   commit uses an honest subject: `docs: stale figures labelled, references
   completed, §5.2 repaired, TOC and skeletons synced`. Commit hash:
   see chat message accompanying this report (a commit cannot contain its
   own hash).
