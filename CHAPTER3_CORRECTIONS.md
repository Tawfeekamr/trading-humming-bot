# CHAPTER3_CORRECTIONS.md — Ratified corrections applied 2026-09-19

Author-ratified corrections applied to Chapter 3 (`docs/chapters/03_methodology.md`),
the manuscript (`docs/dissertation_manuscript.md`), and `REPRODUCE.md`.
**No frozen evaluation artifact was modified** — verification at the end.

Ratifications applied: fifth discovery mode APPROVED (rows 19–20 stand, dated
2026-09-19, marked as affecting no reported figure); data provenance — the
record (data.binance.vision public archives) is correct and the original brief
was wrong, so the chapter's version stands unchanged.

---

## TASK 1 — Observation dimension: the true value is 25

**Finding.** `src/rl/env.py:16-28` defines a 25-dimensional observation. The
manuscript stated $\mathbb{R}^{18}$; the equation's own terms summed to 17
(14 market + 3 time). Both were wrong; the implementation is authoritative.

**Composition of the 25 dimensions** (`src/rl/env.py:16-28`):

| Block | Dims | Content |
|---|---|---|
| Market features | [0:14] | the 14-feature contract shared with the supervised path (`src/data/feature_contract.py`) |
| Time features | [14:17] | 3 time features |
| Engine one-hot | [17:21] | {flat, grid, trend, swing} |
| Unrealised PnL | [21] | $(E_t - E_0)/E_0$ |
| Drawdown-from-peak | [22] | $(\text{peak}_t - E_t)/\text{peak}_t$ |
| Position notional ratio | [23] | $|\text{position}_t|/E_0$ |
| Active bar count | [24] | bars in current engine / engine cap |

**Locations changed:**
- Manuscript line 97 (equation list): "Equation 3.1: State Space Vector
  Representation $s_t \in \mathbb{R}^{25}$" (was R^18).
- Manuscript §3.1 (~line 147): equation rewritten to the full decomposition
  (14 market + 3 time + 4 one-hot + 4 account), with per-term definitions and
  a dated correction marker noting the earlier R^18/sums-to-17 version and
  citing `src/rl/env.py:16-28`.
- Chapter §3.2 already stated 25 (drafted from env.py); unchanged.

## TASK 2 — Slippage claim removed

No slippage was modelled: the environment's only friction is the 0.1%-per-side
fee on turnover (`env.py`); the walk-forward harness's fee and slippage
overlays default to zero and the corrected run passed neither flag.

**Locations changed:**
- Manuscript abstract (line 48): "under simulated market frictions (a
  0.1%-per-side fee in the bar-level replay environment; slippage was not
  modelled — Section 5.3, item 15)" — was "fees and slippage".
- Manuscript §1.3 (line 124): same replacement.
- Manuscript §5.3: new item 15 added — "No slippage was modelled", stating
  the fee-only treatment, the joint asymmetry with the fee double-count
  (cost signal amplified in one respect — fees subtracted twice — and absent
  in another — no spread or impact), the consequence for the withdrawal
  finding (simultaneously too punitive on fees and too optimistic on
  execution, neither direction representative), with a dated marker noting
  the abstract and §1.3 previously claimed "fees and slippage".
- Chapter §3.2 already stated fee-only (drafted from source); unchanged.
- Swept the manuscript and chapter for other occurrences of "slippage"
  claimed as modelled: none remain.

## TASK 3 — Superseded figures replaced

- Manuscript Chapter 4 intro (~line 436): "canonical drawdowns
  (multiplicative equity curve, per-fold distributions summarised by the
  median — never pooled across the 1,440-bar fold gaps)" — was
  "pooled-by-concatenation".
- Manuscript §5.1 finding 2 (~line 1013): MDE now "81.7pp on ETH and 179.3pp
  on BNB cumulative at 80% power" against observed differences "9.66 and
  −11.34 points"; n-required now "on the order of 2,506 years (ETH) and
  7,413 years (BNB) at the 1.7%-per-year anchor, or 72 and 214 years at a
  10%-per-year crypto anchor", with the observed-anchored "21–36 years"
  explicitly marked retracted in Batch 10 as retrospective
  (Hoenig & Heisey, 2001). Was "MDE 60–103%" + "21–36 years".
- Manuscript §5.3 item 4 (~line 1197): same replacement pattern.

## TASK 4 — Reference list completed

Eight entries added to the manuscript reference list (Harvard, alphabetical):
- Hoenig & Heisey (2001), *The American Statistician* 55(1), 19–24
- Gelman & Carlin (2014), *Perspectives on Psychological Science* 9(6), 641–651
- Newey & West (1987), *Econometrica* 55(3), 703–708
- Holm (1979), *Scandinavian Journal of Statistics* 6(2), 65–70
- Politis & Romano (1994), *JASA* 89(428), 1303–1313
- Politis & White (2004), *Econometric Reviews* 23(1), 53–70
- Flyvbjerg (2006), *Qualitative Inquiry* 12(2), 219–245 — cited in §5.2 body
  text with inline bibliographic data but missing from the list (found by the
  verification sweep, beyond the ratified five)
- Tsang (2014), *International Journal of Management Reviews* 16(4), 369–383 —
  same class as Flyvbjerg; title verified by search

Citation sweep result: every author-year citation in the body now resolves to
a list entry, **except one flag for the author**: "Agarwal et al. 2021"
(manuscript §4.6, attributed as the CPCV origin alongside arXiv:2209.05559)
matches no locatable work — CPCV traces to López de Prado (2018). Per project
discipline no entry was invented; the citation needs an author decision
(remove, or supply the intended source).

## TASK 5 — Row 21 and the REPRODUCE.md erratum

- REPRODUCE.md §C corrected to the manifest values: "scikit-learn 1.6.1,
  numpy 2.5.2, pandas 3.0.5, torch 2.13, stable-baselines3 2.9" (was
  "numpy 2.4.x, pandas 2.3.x"), with an inline dated erratum note naming
  this as the third documentation-drift instance and stating the manifest was
  always authoritative. Manifest: `reports/run_manifest_20260815T030518Z.json`.
- Chapter Appendix 3.A: row 21 added (documentation-drift class,
  SELF-VERIFY, no reported figure affected).
- Chapter §3.8: the four-modes paragraph extended with the row-21 drift
  sentence and the pattern statement ("Drift is the one class that recurs
  after detection, because every correction made downstream of written text
  can invalidate the text again").

## TASK 6 — Accuracy-range footnote

Chapter §3.3 now carries footnote [^accuracy] at the 0.80–0.87 statement,
defining both recorded ranges — FIX_REPORT's 0.78–0.87 (four-pair training
set: ETH, BNB, XRP, DOGE) and the manuscript's 0.80–0.87 (the two pairs the
walk-forward evaluation trained and scored: ETH, BNB) — and stating why the
chapter uses the narrower: §3.3 describes the walk-forward baseline, whose
evaluated universe is ETH and BNB only.

## TASK 7 — Failure-mode table moved to Appendix 3.A

- New "Appendix 3.A — Evaluation-defect register" at the end of the chapter:
  the full 21-row table (rows 1–18 from FIX_REPORT Batches 1–16; rows 19–20
  symptom-driven operational findings; row 21 documentation drift), with a
  legend stating provenance, the 2026-09-19 addition and ratification of
  rows 19–21, and that rows 19–21 affect no reported figure. A closing
  paragraph names row 21 as the third documentation-drift instance.
- §3.7 in-text now keeps only "Table 3.1 — The six defects that flipped a
  conclusion" (rows 5, 7, 8, 9, 10, 11) with a forward reference to
  Appendix 3.A. The six detail paragraphs are unchanged.
- §3.8's rows-19–20 table removed; replaced by the pointer sentence
  "These are rows 19 and 20 of the failure register (Appendix 3.A),
  ratified by the author on 2026-09-19."
- §3.8's justification paragraph and the front-matter roadmap updated to the
  ratified form ("rows 19–21 were added in this chapter and ratified by the
  author on 2026-09-19, with rows 19–20 marked as affecting no reported
  figure").

**New word counts** (same method as the draft report — split on `^## `,
front matter excluded, appendix excluded per instruction):

| Section | Words |
|---|---|
| 3.1 | 236 |
| 3.2 | 488 |
| 3.3 | 395 |
| 3.4 | 474 |
| 3.5 | 558 |
| 3.6 | 580 |
| **3.7** | **931** (was the longest; now table-free except the six-row summary) |
| 3.8 | 552 |
| 3.9 | 368 |
| 3.10 | 359 |
| **Chapter body total** | **4,941** |
| Appendix 3.A | 579 (excluded) |

---

## Freeze verification

`git status --porcelain -- src/rl/ src/ml/ src/data/ reports/ models/
trading-engine-core/` returns **empty** — zero frozen paths modified.

Files modified by this correction pass, all documentation:

- `docs/dissertation_manuscript.md` (Tasks 1, 2, 3, 4)
- `docs/chapters/03_methodology.md` (Tasks 5, 6, 7)
- `REPRODUCE.md` (Task 5 erratum)
- `CHAPTER3_CORRECTIONS.md` (this report)
