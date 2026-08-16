# REPRODUCE.md — Independent verification guide

For a reader with this repository and nothing else. The dissertation's
evaluation claims reduce to committed artifacts; this file maps each
claim to its artifact, gives five-minute verification commands against
the raw per-bar data, specifies the full re-run, and states honestly
what cannot be reproduced.

All paths are repository-relative. All headline numbers are on the
**compounded** basis (canonical since Batch 7).

---

## Section A — Claim-to-evidence map

| # | Claim (manuscript §) | Artifact file | Field / column |
|---|---|---|---|
| 1 | ETH total returns: B&H +38.0%, RF-gated −21.2%, PPO −12.8% (4.2) | `reports/rl_walk_forward_ETHUSDT.json` | `metrics.{ta,rf,ppo}.total_return` (0.3797 / −0.2117 / −0.1278) |
| 2 | BNB total returns: B&H −4.3%, RF −20.3%, PPO −25.9% (4.2) | `reports/rl_walk_forward_BNBUSDT.json` | `metrics.{ta,rf,ppo}.total_return` (−0.0427 / −0.2025 / −0.2590) |
| 3 | Per-fold MaxDD distributions (4.2) | same two files | `metrics.<strat>.max_drawdown_distribution` (`per_fold`, `median`, `min`, `max`) |
| 4 | HAC paired test: ETH stat +0.43 p 0.665; BNB −0.33 p 0.743; Holm 1.00 (4.3) | same two files | `metrics.ppo_vs_rf.dm_stat / dm_p` (+ caveat field) |
| 5 | Fold-clustered MaxDD diff: ETH −0.070 [−0.121,−0.020] excludes 0; BNB −0.054 [−0.138,+0.022] (4.3) | same two files | `metrics.risk_inference.maxdd_diff_ppo_minus_rf.{estimate,ci95}` + `method` |
| 6 | MDE 81.7pp (ETH) / 179.3pp (BNB); literature-anchored n-required (4.3.1; achieved-power retracted B10) | `reports/mde_power.json` | `per_bar_basis.<PAIR>.{mde_per_bar, new.mde_cumulative_pp_compounded}` + `reports/design_analysis.json` (achieved_power fields in mde_power.json are retracted — see FIX_REPORT B10) |
| 7 | Flat outscores trained in 19/20 cells; exception BNB f3 s999 +0.0143 (4.1) | `reports/flat_vs_trained_by_seed.json` | rows: `flat_outscored`, `diff_trained_minus_flat` (count 19 True / 1 False) |
| 8 | Trained-flat action share 41.9%/45.8% vs untrained 0.2%/0.0%; reward decomposition 0.310/0.401 vs −0.579/−0.328; flat −0.448/−0.038 vs trained −0.994/−0.854 (4.1) | `reports/exposure_diagnosis.json` | `flat_share_trained`, `flat_share_untrained`, `reward.{lambda_dd_term_total,pnl_term_total,flat_total,trained_total}` |
| 9 | Capital exposure 4.3%/5.2% (PPO) vs 21.1%/60.5% (RF); ratios 4.9×/11.5× (4.1, 4.2) | `reports/exposure_definitions.json` | `<PAIR>.<strat>.capital_weighted_exposure` |
| 10 | Capital-matched random percentiles: PPO 100th (worst) both pairs (4.2) | `reports/exposure_match_{ETHUSDT,BNBUSDT}.json` | `ppo_percentile_in_random_distribution.max_drawdown` (100.0) |
| 11 | False-DANGER rates 47%/38%; missed-danger 36.9%/17.9% (4.5) | `reports/regime_conditioned.json` | `<PAIR>.false_danger.rate_within_danger` (0.4725/0.3849), `missed_danger.rate_within_nondanger` |
| 12 | False-DANGER per-signal means +3.34%/+3.88%; block gaps 0.96/0.72pp; portfolio gaps −67.1/−20.8pp (4.5, 3.6) | `reports/false_danger_corrected.json` | `per_signal.mean_fwd_return_per_false_danger_signal_pct`, `de_overlapped_blocks_24bar.mean_return_gap_pp_per_block`, `portfolio_level_cost.gap_pp` |
| 13 | Seed sweep: 27pp swing (BNB f3 −22.99/+3.95); MaxDD 0.007–0.312; paired stat −0.92..+0.80 (4.3.2) | `reports/seed_sensitivity.json` | `per_seed` rows + `scope_annotation` |
| 14 | Run provenance: commit, data hashes, seeds, library versions | `reports/run_manifest_20260815T030518Z.json` | all top-level keys |
| 15 | Raw per-bar return series (the audit trail underpinning 1–6) | `reports/returns/<PAIR>_<strat>_<fold>.csv` (36 files) | column `return`, index `timestamp` |

---

## Section B — Five-minute verification

Requires only Python + pandas and the committed CSVs. No training, no
models, no network. Run from the repository root.

### B1. Compounded total return per strategy per pair

```bash
python3 - <<'EOF'
import pandas as pd, numpy as np
for pair in ("ETHUSDT","BNBUSDT"):
    for strat in ("ppo","rf","ta"):
        fs = sorted(range(6))
        r = np.concatenate([
            pd.read_csv(f"reports/returns/{pair}_{strat}_fold{f}.csv",
                        index_col=0)["return"].to_numpy() for f in fs])
        print(f"{pair} {strat}: {np.prod(1+r)-1:+.4f}")
EOF
```

Expected output (matches §4.2 / claims 1–2):

```
ETHUSDT ppo: -0.1278
ETHUSDT rf:  -0.2117
ETHUSDT ta:  +0.3797
BNBUSDT ppo: -0.2590
BNBUSDT rf:  -0.2025
BNBUSDT ta:  -0.0427
```

### B2. Per-fold MaxDD (canonical, per-fold; medians match §4.2)

```bash
python3 - <<'EOF'
import pandas as pd, numpy as np
for pair in ("ETHUSDT","BNBUSDT"):
    for strat in ("ppo","rf","ta"):
        dds = []
        for f in range(6):
            r = pd.read_csv(f"reports/returns/{pair}_{strat}_fold{f}.csv",
                            index_col=0)["return"].to_numpy()
            eq = np.cumprod(1+r); pk = np.maximum.accumulate(eq)
            dds.append(np.max((pk-eq)/pk))
        print(f"{pair} {strat}: per-fold",
              [f"{d:.4f}" for d in dds], f"median {np.median(dds):.4f}")
EOF
```

Expected (ETH ppo folds .0769/.0083/.0145/.0490/.1200/.0071 — median
0.0317; BNB ppo median 0.0529; ETH ta median 0.1947; BNB ta 0.1975).

### B3. Confirm the arithmetic-vs-compounded defect was real

```bash
python3 - <<'EOF'
import pandas as pd, numpy as np, json
for pair in ("ETHUSDT","BNBUSDT"):
    fs = sorted(range(6))
    r = np.concatenate([
        pd.read_csv(f"reports/returns/{pair}_ta_fold{f}.csv",
                    index_col=0)["return"].to_numpy() for f in fs])
    stored = json.load(open(f"reports/rl_walk_forward_{pair}.json"))["metrics"]["ta"]["total_return"]
    print(f"{pair} B&H: stored {stored:+.4f} | sum {np.sum(r):+.4f} | "
          f"compounded {np.prod(1+r)-1:+.4f}")
EOF
```

Expected: the stored value equals the SUM (the superseded arithmetic
definition), and differs from compounded. On BNB the sign flips
(+0.0378 vs −0.0427) — this is the Batch-7 correction, verifiable from
raw data.

### B4. Flat-vs-trained cell count (claim 7)

```bash
python3 - <<'EOF'
import json
rows = json.load(open("reports/flat_vs_trained_by_seed.json"))
wins = [r for r in rows if r["flat_outscored"]]
print(f"flat outscored trained in {len(wins)} of {len(rows)} cells")
print("exception:", [(r["pair"],r["fold"],r["seed"],r["diff_trained_minus_flat"])
      for r in rows if not r["flat_outscored"]])
EOF
```

Expected: `19 of 20`; exception `[("BNBUSDT", 3, 999, 0.0143)]`.

---

## Section C — Full re-run

Command (evaluation only; models are cached and reused via provenance
match — no retraining occurs):

```bash
python -m src.rl.walk_forward --pairs ETHUSDT BNBUSDT \
  --data-end 2026-07-05 --timesteps 1000000 --report-dir reports
```

- **Pinned data-end:** 2026-07-05 (required flag; no default).
- **Library versions:** scikit-learn 1.6.1, numpy 2.4.x, pandas 2.3.x,
  torch 2.13, stable-baselines3 2.9 (see
  `reports/run_manifest_20260815T030518Z.json`).
- **Hardware assumption:** Apple Silicon (M-series); PPO training ~5
  min/1M steps on CPU. With all models cached, the walk-forward
  re-evaluation is ~2 minutes; a from-scratch run (12 PPO trainings +
  12 fold-RF trainings) is ~1 hour.
- **Bit-reproducible:** the fold-RF models and their manifests
  (seeded sklearn on pinned windows); the CSV return series given the
  same cached PPO models and pinned data; all Section-B statistics.
- **NOT bit-reproducible:** PPO model weights across machines
  (torch/CPU non-determinism in reductions); hence PPO return series
  may differ in the last decimals on different hardware. The kline
  cache (`backtest/data_cache/klines/`) determines the data; if a
  day's CSV is re-downloaded the frame changes. Reports regenerated
  from the committed CSVs (Section B) are always exact.

---

## Section D — What cannot be reproduced

1. **Single-snapshot provenance is not demonstrated.** The run
   manifest (7e5feaf8), PPO sidecars (df1c3bed), and RF manifests
   (353cb252) cite different commits. Diffed: evaluation/reporting
   code only, no training/env logic — semantically identical for
   training and evaluation — but the artifacts were not produced from
   one frozen commit.
2. **Untracked model binaries.** The 20 seed-sweep PPO models, 12
   walk-forward PPO slice models, and 12 fold-RF models exist only on
   the author's machine (the reports/CSVs/manifests they produced ARE
   committed). The `--seed` and data window for each are recoverable
   from sidecars, so they can be REGENERATED, but the exact binaries
   cannot be recovered.
3. **Library-version sensitivity.** Models were trained under
   scikit-learn 1.6.1; loading under 1.8.0 fires version warnings
   though predictions were verified bit-identical on one fixed sample
   (200 ETH bars). No broader cross-version guarantee exists.
4. **The kline data source.** `src/rl/data.py` downloads daily files
   from data.binance.vision on cache miss. The cache is local and not
   fully committed; a fresh clone's first run may fetch data, and the
   archive is append-only upstream (history is stable but
   availability is outside this repository's control).
5. **The 1.6.1→2.x Python environment.** The training environment
   (`/opt/anaconda3`) no longer exists on the author's machine; the
   evaluation environment (`.mlvenv/`, python 3.14) is local and not
   committed.
