# Appendix B: Installation and Running Instructions

This appendix explains how to check the results, re-run the evaluation, run the tests and start the live system. All commands are run from the root folder of the submitted code archive.

**B.1 What you need**

| Task | Software needed |
|---|---|
| Check the results (B.2) | Python 3 with pandas and numpy |
| Re-run the evaluation (B.3) | Python with scikit-learn 1.6.1, numpy 2.5.2, pandas 3.0.5, PyTorch 2.13 and stable-baselines3 2.9 (the exact versions are in the run manifest; `requirements.txt` and `requirements-rl.txt` give the packages) |
| Run the tests (B.4) | Python 3.12 with `requirements.txt` and `requirements-rl.txt`; the Rust toolchain (edition 2021) with Cargo |
| Run the live system (B.5) | Docker with Docker Compose; exchange and Telegram keys for paper trading |

**B.2 Check the headline results (about five minutes)**

This check needs no training, no models and no internet. It recomputes the compounded total return of each strategy from the saved per-bar returns:

```bash
python3 - <<'EOF'
import pandas as pd, numpy as np
for pair in ("ETHUSDT", "BNBUSDT"):
    for strat in ("ppo", "rf", "ta"):
        r = np.concatenate([
            pd.read_csv(f"reports/returns/{pair}_{strat}_fold{f}.csv",
                        index_col=0)["return"].to_numpy() for f in range(6)])
        print(f"{pair} {strat}: {np.prod(1 + r) - 1:+.4f}")
EOF
```

Expected output (`ppo` = PPO agent, `rf` = Random-Forest baseline, `ta` = buy-and-hold):

```
ETHUSDT ppo: -0.1278
ETHUSDT rf: -0.2117
ETHUSDT ta: +0.3797
BNBUSDT ppo: -0.2590
BNBUSDT rf: -0.2025
BNBUSDT ta: -0.0427
```

The reproduction guide (`REPRODUCE.md`) has similar short checks for the per-fold drawdowns, the do-nothing comparison and the exposure decomposition.

**B.3 Re-run the walk-forward evaluation**

```bash
pip install -r requirements.txt -r requirements-rl.txt
python -m src.rl.walk_forward --pairs ETHUSDT BNBUSDT \
  --data-end 2026-07-05 --timesteps 1000000 --report-dir reports
```

The data end date must be given; it fixes the data window. If the trained models are present, they are reused and the run takes about two minutes. A full run from scratch (12 PPO models and 12 Random-Forest models) takes about one hour on an Apple-silicon laptop. Missing price data is downloaded from the public Binance archive.

**B.4 Run the tests**

```bash
pip install -r requirements.txt -r requirements-rl.txt
pytest tests/ -v                       # Python tests (the RL tests are skipped without requirements-rl.txt)

cd trading-engine-core
cargo test --no-fail-fast              # Rust tests
cargo run --bin validate_config -- ../config/strategy.yaml
```

**B.5 Run the live system (paper trading)**

1. Create `.env` from `.env.example` (exchange and Telegram keys) and `.env.docker` from `.env.docker.example` (signal-listener settings), and fill in your own values. Do not commit these files. (`.env.rust` holds no secrets; it only tells the engine which variable names contain the keys.)
2. Check the strategy settings in `config/strategy.yaml` (B.4 shows how to validate them).
3. Start the three containers. The compose file pulls the prebuilt images from the project's container registry; to run the archived code instead, build the images first from `Dockerfile.rust` and `Dockerfile.hybrid`.

```bash
docker compose -f docker-compose.rust.yml up -d
docker compose -f docker-compose.rust.yml ps      # all three should be "Up"
docker logs trading-bot-rust --tail 30            # engine log
```

The system trades in paper mode only. No real orders are sent.

**B.6 Known limits of reproduction**

- PPO model weights are not bit-for-bit identical across machines, because of small numerical differences in PyTorch on different hardware. The saved return series, and all statistics computed from them, are exact.
- The trained model files are not included in the archive. The walk-forward models can be regenerated with B.3, which uses the recorded seed and data window for each model; the 20 seed-sweep models need a separate run of the seed-sweep script.
- The saved results were produced from slightly different versions of the code. The differences affect only evaluation and reporting code, not training or the environment.
