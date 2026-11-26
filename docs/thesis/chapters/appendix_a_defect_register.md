# Appendix A: Evaluation-Defect Register

This register lists every evaluation defect found by the audit, plus four later findings. Rows 1–18 are the evaluation defects found by the audit of August 2026 (Section 3.7). Rows 19 and 20 are the operational findings of Section 3.8, and row 21 is a further instance of documentation drift. Row 22 records an ambiguity in the definition of a metric, found while checking the abstract against the saved exposure traces. Rows 19–22 affect no reported figure.

| # | Defect | Effect on the result | Changed a conclusion? | How it was caught |
|---|---|---|---|---|
| 1 | Warm-up bars from training inside the start of the test frame | About 49 training bars entered the out-of-sample series; comparators misaligned | No | Self-audit |
| 2 | No embargo between training and test windows | Test features could be computed from training bars | No | Self-audit |
| 3 | Random-forest baseline not specific to each fold | Early folds scored by a model trained later | No | Self-audit |
| 4 | Statistic mislabelled as "Diebold–Mariano" | Reported statistic could not be traced to code | Claim withdrawn | Self-audit |
| 5 | Additive (cumulative-sum) equity for maximum drawdown | Misstated drawdown (ETH buy-and-hold 0.353 additive against 0.511 multiplicative) | Yes | Self-audit |
| 6 | Sharpe ratio annualised over invested bars only | Ratio overstated by √(1/f) | No (artefact) | Self-audit |
| 7 | Conflated exposure definitions | Baselines matched on different quantities | Yes (ETH verdict reversed) | Self-audit |
| 8 | Opportunity cost summed over overlapping windows | 2,243 pp: impossible against a +38% window | Yes (magnitude) | Self-audit |
| 9 | Reliance on a single seed | 28 pp within-fold swing between seeds hidden | Yes | Self-audit |
| 10 | Arithmetic (uncompounded) total return | BNB buy-and-hold sign flip, +3.78% → −4.27% | Yes | Independent review |
| 11 | Pooling across the 1,440-bar fold gaps | Drawdown inflated up to 5.5×; dependence estimates corrupted | Yes | Independent review |
| 12 | Uncapped denominator in the grid "ceiling" diagnostic | Capacity diagnostic meaningless | Strengthens the finding | Independent review |
| 13 | Transaction fee counted twice in the reward | Reward more punitive than designed | Scopes the finding | Independent review |
| 14 | Length-only block-size rule described as Politis–White selection | Intervals conditional on a heuristic | Label corrected | Independent review |
| 15 | Diagnostic data never saved | Central claim could not be verified | Traces now saved | Observing system behaviour |
| 16 | "Structural amplifier" explanation asserted without measurement | Mechanism wrongly attributed | Explanation replaced by measurement | Observing system behaviour |
| 17 | Classifier accuracies shown only in console output | Accuracy claims could not be verified | Caveat added | Independent operational audit |
| 18 | Reproduction guide contradicted by a later correction | A reader following it exactly would see a false failure | Guide corrected | Re-running the reproduction guide |
| 19 | Live market-data feed connected to the test network for 110 days (31 May–18 Sep 2026) | Live paper-trading record priced partly from the test network; no evaluation result affected (Section 3.9) | No | Investigating a symptom |
| 20 | Continuous-integration pipeline failing since August 2026 while deployments continued | Quality gate not working for about five weeks | No | Investigating a symptom |
| 21 | Library versions in the reproduction guide contradicted the run manifest | Guide listed numpy 2.4.x / pandas 2.3.x; manifest records 2.5.2 / 3.0.5 (guide corrected) | No (documentation) | Re-running the reproduction guide |
| 22 | "Time in market" counts bars with an active engine, not bars holding inventory | Draft abstract described it as "holding a position on 58% of bars"; corrected to "reported time in market", with both quantities labelled (Sections 3.6 and 4.1) | No (definition only) | Checking the abstract against the saved traces |

Row 21 is the third instance of documentation drift. The first was row 18, where an expected value in the reproduction guide became wrong after the compounding correction (row 10). The second was a description of a logging component as write-only, which became wrong once programs that read it were identified. The class recurs because every correction made after a piece of text was written can make that text wrong again. For this reason the reproduction guide is re-checked against the saved artefacts rather than trusted (Section 3.8).
