# Dissertation Writing Plan

**Module:** 771952 MSc AI Project, University of Hull  
**Deadline:** Tuesday 12 January 2027, 14:00 UK time (report and presentation are both due that day)  
**Made:** 26 November 2026

This plan tells you **what** to write in each missing section. It does not write it for you. Hull's
AI policy says AI must not write or rewrite your work unless you have explicit permission, so the
sentences in the dissertation have to be yours. Use this as a checklist and a source of facts.

To rebuild the Word file at any time:

```
python3 docs/thesis/assemble.py
```

**Update 26 Nov 2026:** Chapters 1 (1.1–1.4), 3, 4, 5, 6 and Appendix A were rewritten as clean
academic text in `docs/thesis/chapters/` — same results, numbers and citations, with all internal
file names, batch codes, commit IDs and revision history removed. **These files are now the ones to
edit.** The supervisor must approve this AI-assisted rewrite before submission (Hull AI policy).
The sections you still write appear as empty headings in the Word file (blue notes are switched
off; set `SHOW_NOTES = True` in `assemble.py` to see them).

---

## Part 1. The rules, in short

| Rule | What it means for you |
|---|---|
| Max **20,000 words** (not counting references and appendices) | Up to 22,000 has no penalty, but aim for **19,500** |
| **Six chapters**: Introduction, Literature Review, Methodology, Results, Discussion & Recommendations, Conclusions | Your draft had five. The new layout is already set up |
| **Harvard referencing** | Your reference list already uses it |
| Hand in as **one PDF**, plus a **ZIP of code and data** (separate submission) | Make the ZIP from the frozen commit |
| **Student ID only**, no name, also not in the file name | Already fixed in the new title page |
| Must include **install and run instructions** | Appendix B (not counted in words) |
| Must make **recommendations for deployment** | New section 5.5 |
| Presentation: 15 min + 10 min questions + **live software demo**. Video: max 10 min, you visible, for a non-expert audience | You are doing the video yourself |

### How the marks are given

| Area | Marks | Where it is earned |
|---|---|---|
| Project definition, background, literature | 10 | Ch1, Ch2 |
| Problem analysis and solution design (alternatives, risks, plan) | 20 | Ch1, Ch3 (new 3.11 and 3.13) |
| **Technical achievement** (working system, rigorous testing) | **40** | Ch3 (new 3.12), Ch4, Appendix C, the demo |
| Critical evaluation and reflection | 20 | Ch5, Ch6 (new 6.3) |
| Legal, social and ethical issues | 5 | Ch3 section 3.10 |
| Presentation and referencing | 5 | Whole document, figures, references |

**The biggest risk:** the draft is a very good *evaluation* study, but it hardly describes the
*software you built*. Technical achievement is 40% of the mark. The examiners need to see the
system, the engineering and the testing. Sections 3.12 and Appendix C fix this.

---

## Part 2. Word budget

| Chapter | Already written | Still to write | Target total |
|---|---|---|---|
| Abstract | 300 | 0 (check at the end) | 300 |
| 1 Introduction | 390 (needs rewriting) | ~700 | 1,100 |
| 2 Literature Review | 0 | ~3,000 | 3,000 |
| 3 Methodology | 5,030 | ~1,250 | 6,280 |
| 4 Results | 3,280 | 0 | 3,280 |
| 5 Discussion & Recommendations | 3,690 | ~500 | 4,190 |
| 6 Conclusions | 500 | ~600 | 1,100 |
| **Total** | **13,233** (measured) | **~6,050** | **~19,250** |

That leaves only about 750 words of slack. If you go over, trim these first (they are the longest):

- 5.3 Engineering & Scientific Contributions (984 words): can lose about 300
- 5.4 Limitations + 5.4.1 (1,528 words): move the deployment-layer list (5.4.1) to an appendix, saving about 400
- 4.1 The learned policy is abstention (1,224 words): can lose about 200

---

## Part 3. Things to fix across the whole document

1. **Statement on the use of AI.** Hull's policy needs you to say what AI you used and how.
   Talk to your supervisor about the wording. Be honest about earlier AI help on the manuscript
   and code. Turnitin will check the PDF.
2. **Names.** The old title page had your name, supervisor name and a "Publications" entry with
   your name. These are removed from the new build. Do not add them back.
3. **Chapter 1 framing.** Sections 1.1 to 1.3 still describe the June idea (grid vs trend
   "single-strategy trap", BTC contagion). The dissertation is now about *reward-induced
   abstention and evaluation blindness*. Rewrite 1.1 to 1.3 to match (see Chapter 1 below).
   The hypotheses SH2 and SH3 in 1.4 are never tested in the results. Either drop them or
   say why they were not tested.
4. **Figures.** The draft has **no figures at all**. Aim for 6 to 8 (list in Part 6).
5. **Cross-references** broke when sections moved (Part 4).
6. **Drafting notes** in the text, such as "Draft for revision" or "[FIGURE NOT IN RECORD]" (one in Chapter 3),
   must be resolved or removed.
7. **Ethics form PDF** goes in Appendix D.
8. **Slippage limitation.** Your September supervisor question is still open: does the missing
   slippage model need more caveats? Make sure 5.4 answers it.

---

## Part 4. Section numbers that changed (DONE automatically, 26 Nov 2026)

`assemble.py` now renumbers every cross-reference when it builds the Word file (each one
checked against its context). The source Markdown files keep their own numbers, so **keep
writing in the old numbering inside `dissertation_manuscript.md`**, or write new text in the new
numbering only in new sections. The abstract's 20.6 is corrected to 20.5 in the source.

The mapping used:

| Old number | New number | Section |
|---|---|---|
| 4.6 | **5.1** | Relation to positive-result literature |
| 4.7 | **5.2** | The withdrawal claim, narrowed |
| 4.8 | **4.6** | Artifact-audit survey of open-source repos |
| 5.1 | **6.1** | Summary of findings |
| 5.2 | **5.3** | Engineering & scientific contributions |
| 5.3 | **5.4** | Limitations |
| 5.3.1 | **5.4.1** | Deployment-layer limitations |
| 5.4 | **6.4** | Future work |
| Appendix 3.A | **Appendix A** | Defect register |
| "Section 3.6" **in Ch4 and Ch5 only** | **3.7** | Failure modes (the old Ch3 numbered it 3.6) |

Search the text for "Section", "§" and "Chapter" and check each one. There are about 80.

---

## Part 5. Section-by-section plan

### Chapter 1 — Introduction (about 1,100 words in total)

**1.1 Background** (rewrite, ~250 words)
- Reinforcement learning (RL) is popular for trading research. Many papers report good
  risk numbers such as lower drawdown or higher Sharpe ratio.
- Those numbers are usually read as proof the agent made good decisions.
- Your project built a real, deployed crypto trading system and tested an RL agent (PPO)
  against a supervised baseline (Random Forest) inside it.

**1.2 Problem statement** (rewrite, ~250 words)
- The core problem: standard metrics can make an agent that *does almost nothing* look like a
  *careful risk manager*.
- Use your headline numbers here: drawdown 55–70% lower than the baseline, but only 4.3% of
  capital deployed vs 49.2% for an untrained network (from the abstract).
- Say why this matters: someone could deploy such an agent believing it manages risk.

**1.3 Aims and objectives** (rewrite, ~150 words)
- Aim: find out whether PPO routing beats supervised routing, and whether standard evaluation
  can tell the difference.
- Objectives as a numbered list: build the system; build the corrected evaluation protocol;
  compare the policies; explain the result; document how evaluation defects were found.
- Keep the honest note that slippage was not modelled.

**1.4 Research question and hypotheses** (exists; small fixes only)
- Decide what to do with SH2 and SH3 (see Part 3, point 3).

**1.5 Industrial relevance** (new, ~300 words). This earns marks in the "definition and context" area.
- Who cares: fund managers, crypto trading firms, regulators reviewing algorithmic trading.
- Regulators already require firms to test their algorithms before going live. For example, the
  EU algorithmic trading rules (MiFID II, RTS 6) require testing of algorithms. A test that
  rewards inactivity is a real risk for them.
- Practical message: always report how much capital the agent actually puts to work.

**1.6 Contributions** (new, ~250 words). Take these from the current 5.3 and PRIOR_ART.md "What survives".
- Be careful. PRIOR_ART.md shows the flat-policy floor (D1) and exposure matching (D2) were
  **partly done before** by others, and reward decomposition (D3) is **already established**.
  Claim them as *applications* to RL trading evaluation, not inventions.
- The main new thing: a documented case of **concealed abstention** (24.3 points of the
  exposure gap hidden inside "active" actions), plus the discovery-mode analysis.

**1.7 Structure of the dissertation** (new, ~150 words): one sentence per chapter.

---

### Chapter 2 — Literature Review (about 3,000 words)

This chapter is worth a big part of the 10 "context" marks. The rubric says "broad and deep,
covering all the important sources". At the moment your references are deep on the abstention
niche but missing the **foundations**. Each section below lists sources you already have
(**H**) and well-known sources to look up and read (**R**). **Check every source yourself**
before citing it (Hull policy: AI can get references wrong).

**2.1 Reinforcement learning for trading** (~600 words)
- What RL is (states, actions, rewards). PPO in one paragraph.
- History: early direct RL trading, then deep RL, then libraries like FinRL.
- Crypto-specific RL studies and their reported results.
- H: Mnih et al. 2015; Schulman et al. 2017; Gort et al. 2022; Kochliaridis et al. 2023;
  Amirzadeh et al. 2023; Felizardo et al. 2022; Xue & Ye 2025; Ma 2025
- R: Sutton & Barto (2018) *Reinforcement Learning: An Introduction*, 2nd edn;
  Moody & Saffell (2001) "Learning to trade via direct reinforcement", *IEEE Trans. Neural Networks*;
  Deng et al. (2017) "Deep direct reinforcement learning for financial signal representation and trading", *IEEE TNNLS*;
  Jiang, Xu & Liang (2017) deep RL portfolio management, arXiv:1706.10059;
  Liu et al. (2020) FinRL, arXiv:2011.09607;
  Théate & Ernst (2021) "An application of deep RL to algorithmic trading", *Expert Systems with Applications*;
  Hambly, Xu & Yang (2023) "Recent advances in RL in finance", *Mathematical Finance* (a survey, good anchor)

**2.2 Market regimes and supervised regime classification** (~450 words)
- Markets switch between calm, trending and crash states. Classic regime-switching models.
- Machine learning classifiers for regimes. Why Random Forest. Why probability calibration matters
  (your baseline uses isotonic calibration).
- Crypto market behaviour (high volatility, links between coins).
- H: Zadrozny & Elkan 2002
- R: Hamilton (1989) regime-switching, *Econometrica*; Ang & Timmermann (2012) "Regime changes and
  financial markets", *Annual Review of Financial Economics*; Breiman (2001) "Random forests",
  *Machine Learning*; Niculescu-Mizil & Caruana (2005) "Predicting good probabilities", ICML;
  Moskowitz, Ooi & Pedersen (2012) "Time series momentum", *JFE*; Liu & Tsyvinski (2021)
  "Risks and returns of cryptocurrency", *Review of Financial Studies*

**2.3 Evaluating trading strategies** (~600 words)
- Backtest overfitting, data snooping, look-ahead leakage (you found one yourself, see RL OOS
  contamination), walk-forward testing with an embargo.
- Statistical testing: Diebold–Mariano, HAC errors, bootstrap, multiple testing, statistical power.
- Reproducibility problems in deep RL (seed variance; your seeds dominate method differences).
- H: Bailey & López de Prado 2014; Diebold & Mariano 1995; Newey & West 1987; Politis & Romano
  1994; Politis & White 2004; Holm 1979; Hoenig & Heisey 2001; Gelman & Carlin 2014; Agarwal et al. 2021
- R: López de Prado (2018) *Advances in Financial Machine Learning* (book: walk-forward, embargo);
  Bailey et al. (2017) "The probability of backtest overfitting", *Journal of Computational Finance*;
  Harvey & Liu (2015) "Backtesting", *Journal of Portfolio Management*; White (2000) "A reality
  check for data snooping", *Econometrica*; Henderson et al. (2018) "Deep RL that matters", AAAI

**2.4 Reward design, misspecification and abstention** (~600 words). This sets up your main finding.
- Reward shaping and how it changes what the agent learns.
- Reward hacking and specification gaming: agents optimise the reward, not what you meant.
- Abstention as a designed choice: reject option (Chow), selective prediction, market-making
  "not to quote". Contrast with your case, where abstention was **not designed** and **not visible**.
- Lazy agents, offline-RL pessimism (searched in PRIOR_ART section 6: different mechanism).
- H: Chow 1970; Geifman & El-Yaniv 2017; Geifman et al. 2019; Wang, Ventre & Polukarov 2025;
  Liu et al. 2023 (lazy agents); Mohl et al. 2025; Hafsi & Vittori 2024; Zhang 2025;
  Gašperov & Kostanjčar 2021; Lin et al. 2019, 2020 (reward decomposition)
- R: Ng, Harada & Russell (1999) "Policy invariance under reward transformations", ICML;
  Amodei et al. (2016) "Concrete problems in AI safety", arXiv:1606.06565;
  Skalse et al. (2022) "Defining and characterizing reward hacking", NeurIPS;
  Krakovna et al. (2020) "Specification gaming: the flip side of AI ingenuity", DeepMind blog
  (a web source: the rubric likes a range of source types)

**2.5 Measuring exposure: lessons from fund evaluation** (~350 words)
- Fund managers are judged on how active they really are, and on what drives returns
  (allocation vs selection). Low-risk investing can look skilful just by taking less risk.
- This is the idea behind your capital-weighted exposure measure.
- H: Cremers & Petajisto 2009; Brinson, Hood & Beebower 1986; Frazzini & Pedersen 2014;
  Marot et al. 2021 (the "do nothing" baseline in power-grid RL)

**2.6 Summary and research gap** (~400 words)
- What the literature does: reports outcome metrics; sometimes has a passive baseline but
  compares on performance, not on the training reward; rarely reports capital-weighted exposure.
- The gap your work fills (stated carefully, see 1.6). Use PRIOR_ART.md Section 4 "What survives".
- Optional: a summary table of 8–10 key papers (method, market, baseline, reports exposure? yes/no).
  A table counts as few words but shows breadth.

---

### Chapter 3 — three new sections (about 1,250 words)

**3.11 Alternatives considered and why this design was chosen** (~400 words). This is worth up to 20 marks.
The rubric wants "a good range of potential solutions rigorously evaluated". Cover:
- RL algorithm: PPO vs DQN vs SAC vs Decision Transformer (your June roadmap planned a Decision
  Transformer; say why PPO was used instead: stable, standard, discrete action space).
- Baseline: Random Forest vs HMM regime model vs simple volatility rule.
- Evaluation: single train/test split vs walk-forward with embargo (why walk-forward).
- Data: live paper-trading record vs public Binance archives (why archives: fixed, hashable,
  not affected by the testnet feed problem).
- Platform: Hummingbot vs Freqtrade vs your own Rust engine (why you migrated off Hummingbot;
  see memory notes / docs on the hybrid bridge).

**3.12 System implementation and testing** (~500 words, details in Appendix C). This is worth up to 40 marks.
Facts you can use (checked 26 Nov 2026):
- About 23,000 lines of Rust (trading engine core) and 11,000 lines of Python (ML, RL, bots)
- **411 Rust tests and 550 Python tests**
- 1,060 commits since 7 May 2026
- 5 CI workflows: backtest, Rust backtest, coverage, deploy, retrain
- Deployed on AWS EC2 with Docker; Telegram control interface; regime-pusher sidecar
- Engines: grid, trend, swing, mean-reversion, signal, futures (paper)
- Safety features: circuit breaker, price-sanity filter, capital allocation per strategy
- Testing approach: unit tests, regression tests for real bugs found (e.g. duplicate-close test),
  replay backtest, OOS contamination guard tests

Include one **architecture diagram** here (Figure 3.1).

**3.13 Project plan and risk management** (~350 words)
- A short timeline of what was done each month (from your monthly reports).
- A risk table: risk, likelihood, impact, what you did. Examples: data leakage (found and fixed
  with the embargo and guard); data loss (git, committed artefacts, SHA-256 hashes); live-money
  loss (paper trading only); bad price ticks (price-sanity filter); schedule slip (honest in the
  September report).

---

### Chapter 5 — new section

**5.5 Recommendations for deployment** (~500 words). The rubric asks for "clear recommendations".
- **Do not deploy** the PPO agent with real money. Say why in one line.
- What the live system can safely do (which engines showed an edge in paper trading, with care:
  paper results ignore slippage).
- Rules for anyone evaluating an RL trading agent: report capital-weighted exposure; compare
  with a do-nothing policy under the same reward; check reward parts separately.
- Before any real-money use: fix the items in 5.4.1 (no position cap enforced, unauthenticated
  API, retraining workflow not working), add slippage, run on the production feed.

---

### Chapter 6 — two new sections

**6.2 Answer to the research question** (~200 words): a direct yes/no answer in plain words, then
the one-sentence reason.

**6.3 Reflection on the project** (~400 words). Personal and honest; examiners reward this.
- What went well (the corrected protocol, the evidence chain).
- What you would do differently (e.g. set up independent review earlier: 6 defects were only
  found by outside review after 5 self-audits; start writing earlier; model slippage from the start).
- What you learned about doing research.

---

## Part 6. Figures (DONE, 26 Nov 2026)

All 8 figures are built by `make_figures.py` (Apache ECharts for charts, Mermaid for the
architecture diagram, rendered at 300 dpi) straight from `reports/`, and placed in the Word file
with real captions. Still for you to do: refer to each figure in your own text, and reword the
captions if you want. Hull's AI policy: say in your AI-use statement that the figures were
produced with AI help from your data.

Note: the exposure split gives explicit abstention **20.5** points on ETH (20.5491 in
FIX_REPORT B13.4), but the abstract says **20.6**. Fix the abstract.

Figure list (for reference):

| # | Figure | Data |
|---|---|---|
| 3.1 | System architecture diagram | code layout, docker-compose files |
| 3.2 | Walk-forward folds on a timeline (train / embargo / test) | numbers in Ch4 intro |
| 4.1 | Capital deployed: PPO vs untrained network vs RF | `reports/exposure_*.json` |
| 4.2 | Exposure gap split: explicit vs concealed abstention | `EXPOSURE_DECOMPOSITION.md` |
| 4.3 | Action mix shift toward the grid primitive | `reports/exposure_traces` |
| 4.4 | Per-fold max drawdown, PPO vs RF (box plot) | `reports/rl_walk_forward_*.json` |
| 4.5 | Flat policy vs trained policy reward, per seed and fold | `reports/flat_vs_trained_by_seed.json` |
| 4.6 | Minimum detectable effect vs observed differences | `reports/mde_power.json` |

Every figure needs a caption ("Figure 4.1: ...") made with Word's *Insert Caption*, so it appears
in the List of Figures.

---

## Part 7. Timeline to 12 January

| Dates | Work |
|---|---|
| 26 Nov – 2 Dec | Chapter 2 |
| 3 – 9 Dec | Chapter 1 rewrite; Chapter 3 new sections; Appendix B and C |
| 10 – 16 Dec | 5.5, 6.2, 6.3; fix cross-references; add figures |
| 17 – 22 Dec | Send full draft to supervisor; write the AI-use statement |
| 23 Dec – 3 Jan | Holiday buffer; supervisor feedback |
| 4 – 9 Jan | Apply feedback; proofread; word count; make PDF and code ZIP; slides and video |
| **12 Jan, before 14:00** | **Submit PDF + ZIP; give presentation** |

---

## Part 8. Final checklist before submitting

- [ ] All blue "TO WRITE" and guidance notes deleted
- [ ] Contents, List of Figures and List of Tables updated (right-click, then Update Field)
- [ ] Word count under 20,000 (Word's count minus references and appendices)
- [ ] No name anywhere, including in the PDF file name and file properties (File > Info > Author)
- [ ] Every citation in the text is in the reference list, and the other way round
- [ ] Every reference checked by you
- [ ] AI-use statement agreed with your supervisor
- [ ] Ethics form in Appendix D
- [ ] Install and run instructions in Appendix B, tested on a clean machine
- [ ] Spell-check (UK English)
- [ ] Code ZIP made from the frozen commit, with no secrets (`.env`, API keys)
