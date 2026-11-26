## Alternatives Considered and Why This Design Was Chosen

Each main design decision had reasonable alternatives. Table 3.2 lists them and the reason for each choice.

Table 3.2: Main design choices and the alternatives considered

| Decision | Chosen | Alternatives considered | Reason for the choice |
|---|---|---|---|
| RL algorithm | PPO | DQN; SAC; Decision Transformer | PPO is stable, widely used in trading research and handles a small discrete action set well. SAC is designed for continuous actions. A Decision Transformer needs a large offline dataset of good trajectories, which did not exist. |
| Supervised baseline | Calibrated Random Forest | Hidden Markov model; simple volatility rule | A Random Forest works well on noisy tabular features and gives probabilities that can be calibrated. A hidden Markov model finds unlabelled states that are hard to map to trading engines. A single volatility rule would be a weaker baseline. |
| Evaluation scheme | Walk-forward with an embargo | Single train-test split; combinatorial purged cross-validation | A single split gives one test result and hides variance. Combinatorial cross-validation gives more test paths but mixes non-adjacent periods (Section 3.4). Walk-forward keeps time order and is easy to explain. |
| Evaluation data | Public Binance archive, pinned and hashed | The live paper-trading record | Archive data are fixed and can be re-checked by anyone. The live record was short, affected by changes to the system, and later found to be partly priced from a test feed (Section 3.8). |
| Trading platform | Own Rust engine | The Hummingbot framework (used at the start); Freqtrade | The project began on Hummingbot. Running several engines with shared risk controls and regime gating was easier to build, test and control in a dedicated engine, so a Rust engine was built at the end of May 2026 and replaced Hummingbot during June. |

The main cost of these choices is scale. A single system, two assets and one reward keep the study manageable and fully checkable, but they limit how far the findings generalise (Section 5.4).

## System Implementation and Testing

The system was built for this project, starting from a prototype on the Hummingbot framework. It has two main parts. The execution engine is written in Rust (about 20,200 lines of source code). The machine-learning, reinforcement-learning and signal components are written in Python (about 11,400 lines, plus 3,600 lines of scripts). Development ran from May to November 2026 and is recorded in 1,069 commits. Most of the building happened early: 459 commits in May and 324 in June, falling to 113 in August, when the evaluation code was frozen. Most November commits fixed defects found in the live system (Section 5.4.1); one added the reward ablation (Section 4.6). Appendix C lists every module and its size.

### Deployment

The live system runs in paper-trading mode on one AWS EC2 server (t3.small: 2 virtual CPUs and 2 GB of memory) in the Tokyo region. Paper mode means orders are simulated and no real money is used. The system runs as three Docker containers:

- the Rust trading engine, which also serves an HTTP interface (closed to the internet by the server's firewall) and is checked by Docker to be running;
- the signal listener, written in Python;
- the regime pusher, which sends each pair's regime label and confidence to the engine every 180 seconds.

Every container restarts on its own if it stops. The container images are built by the continuous-integration pipeline and stored in a container registry, so the server never compiles code. On 26 November 2026 the server had been running for 175 days without a reboot. The containers had been up for seven weeks, and the server was using about 0.9 GB of its 1.9 GB of memory and 38% of its 20 GB disk. From June to October 2026 the live system recorded 68 paper trades (13 grid, 20 trend, 35 signal). These trades show that the system works end to end. They are not part of the evaluation, which uses historical data only (Section 3.4).

### Safety features

Three safety features protect the live system. A portfolio-wide circuit breaker stops all new entries when equity falls 10% below its peak, 5% below its level at the start of the day, or to zero. It stays stopped until midnight (UTC), when it re-trips at once if the drawdown limit is still breached, or until a manual reset. A price-sanity filter rejects prices that are far from recent market prices. It was added after a single bad price tick created a large false loss. Finally, each strategy has its own capital budget, so one engine cannot use all the available funds.

### Testing

The code has 979 automated tests: 423 in Rust and 556 in Python. They cover three kinds of checks:

- unit tests for single components, such as each technical indicator, the grid and trend logic, order fills and the risk rules;
- regression tests, written after a real defect was found, so that the same defect cannot return unnoticed (for example, two exits of the same position four seconds apart);
- guard tests for the research code, such as a check that no training data can enter an out-of-sample test window.

Every change pushed to the main branch runs the Rust tests, a configuration check and the Python tests before it can be deployed; if any of them fails, the deployment stops. The reinforcement-learning tests, including the leakage guards, need the heavy RL libraries and run locally rather than in this pipeline, and documentation-only commits skip it. Of the last 200 runs of this pipeline, 168 passed and deployed, 23 failed and were blocked, and 9 were cancelled. Four more pipelines run replay backtests, measure test coverage and retrain the regime models each month. The retraining pipeline never completed a run before it was repaired on 26 November 2026; it now replaces a live model only if the new one is at least as accurate on the most recent 30 days, which it has not seen, and its first scheduled run is on 1 December 2026.

The research results can be checked without retraining any model. A short script in the reproduction guide (Appendix B) recomputes the headline returns from the saved per-bar return series. When it was run on 26 November 2026, it reproduced every reported total return to four decimal places.

## Project Plan and Risk Management

The project followed five phases (Table 3.3).

Table 3.3: Project phases

| Phase | Period | Main work |
|---|---|---|
| 1. First system | May 2026 | Python grid and trend bot on the Hummingbot framework; server set up; circuit breaker; first regime classifier (17 May); Rust engine built and running in paper mode by 31 May |
| 2. Rust engine | June 2026 | Rust engine replaces Hummingbot completely; paper trading on the server; per-strategy capital budgets |
| 3. Research | July 2026 | Reinforcement-learning environment and PPO training (from 5 July); walk-forward evaluation; live regime pipeline (13 July) |
| 4. Audit and freeze | August 2026 | Audit of the evaluation protocol (15–23 August); corrected results; code and data frozen |
| 5. Writing | September 2026 – January 2027 | Dissertation chapters; monthly progress reports; submission on 12 January 2027 |

Progress was reported to the supervisor every month. When the September plan slipped (Chapter 3 was written instead of the introduction), the schedule was revised in the September report rather than hidden.

The main risks and how they were handled are shown in Table 3.4.

Table 3.4: Main project risks and how they were handled

| Risk | What could go wrong | How it was handled |
|---|---|---|
| Data leakage | Test results look better because the model has seen future data | 70-bar embargo between training and testing; automated guard tests (Section 3.4) |
| Overfitting and chance results | A good result comes from one lucky seed or period | Walk-forward folds, several seeds, and power analysis (Section 3.5) |
| Blind spots in self-checking | Errors that the author cannot see in their own work | Independent review of the evaluation; six defects were found only this way (Section 3.7) |
| Loss of work or data | Code, models or results are lost | Version control with a remote copy; data and model files recorded with SHA-256 hashes (Section 3.9) |
| Financial loss | The live system loses real money | Paper trading only; circuit breaker; per-strategy budgets |
| Bad market data | A wrong price creates a false trade or loss | Price-sanity filter |
| Server failure | The live system stops | Containers restart on their own; images kept in a registry for fast redeployment |
| Schedule slip | Chapters are not finished on time | Monthly progress reports and a revised schedule |
