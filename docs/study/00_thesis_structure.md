# Thesis Structure and Writing Guide

## Project Title
**Learned Withdrawal and Evaluation Blindness in Reinforcement Learning for Trading: A Corrected-Protocol Case Study**

*(Retitled 2026-08-16; the chapters below predate the corrected-protocol evaluation — see docs/dissertation_manuscript.md for the authoritative version and FIX_REPORT.md for the audit.)*

## Purpose of this Directory
This directory contains the foundational structure and outlines for your Master's degree thesis/project report. Each file represents a core chapter or section of a standard academic thesis in Computer Science or Financial Engineering.

## Files Overview
* **`01_abstract.md`**: A concise summary of the problem, methodology, and results. Write this *last*.
* **`02_introduction.md`**: Sets the context. Why is algorithmic trading difficult? Why do static strategies fail? Introduces your hybrid solution.
* **`03_literature_review.md`**: Reviews existing research on algorithmic trading, grid strategies, trend following, and machine learning in finance.
* **`04_methodology.md`**: The theoretical core. Detailed explanation of the Random Forest classifier, the dual-engine strategy, and cross-asset correlation.
* **`05_system_architecture.md`**: The engineering core. Explains the Hummingbot v2 integration, AWS deployment, CI/CD pipeline, and data flow.
* **`06_results_evaluation.md`**: Empirical evidence. Backtesting metrics, paper trading performance, risk analysis (drawdown, Sharpe ratio).
* **`07_conclusion.md`**: Final thoughts, limitations, and future work.

## Next Steps
1. Review the outlines provided in each file.
2. Fill in the specific details of your implementation (e.g., specific hyperparameters, specific backtest results).
3. Use academic language: avoid "I did this", use "This research implements" or "The system architecture demonstrates".
4. Gather references and citations for the Literature Review section.
