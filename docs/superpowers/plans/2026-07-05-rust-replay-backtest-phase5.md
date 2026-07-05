# Rust Replay Backtest — Phase 5 Implementation Plan (auto-apply workflow)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the loop: a weekly GitHub Actions workflow that sweeps grid/trend/swing behind the Phase-4 OOS gate and — **only when `decision.apply == true`** — writes the winning params to `config/strategy.yaml` and commits (→ triggers the live redeploy). Retires the unfaithful `sweep.yml`. First runs are `--dry-run` (report only, no commit).

**Architecture:** Task 1 tightens the Rust→Python JSON contract (structured `param_deltas` on `SweepResult` + snake_case `EngineKind`). Task 2 rewrites `backtest/apply_sweep.py` to consume `*_sweep.json`, apply ONLY `decision.apply==true` engines via a `PARAM_MAP` (comment-preserving YAML edit), with `--dry-run`. Task 3 adds `.github/workflows/backtest-rust.yml` (weekly cron + `workflow_dispatch` dry-run input; runs on the Actions runner; sweep → apply → commit-if-changed-and-not-dry-run → Telegram → artifact). Task 4 deletes `sweep.yml`. Task 5 smoke-tests the apply step locally with a synthetic APPLY json.

**Tech Stack:** Rust (Phase 1-4 crate — small `SweepResult`/`EngineKind` touches); Python 3 (`apply_sweep.py`, `pyyaml`/line-editor — matches existing apply_sweep.py style); GitHub Actions (yaml). No new Rust crates.

## Global Constraints

(Carried from spec §6 + §8 + the Phase-4 final review. Every task inherits these.)

- **The apply step is GATED twice (defense in depth):** (1) the Rust `apply_gate` (spec §6, already shipped in Phase 4) sets `decision.apply` in the JSON; (2) the Python `apply_sweep.py` writes `config/strategy.yaml` ONLY for engines where `decision.apply == true`. A Python bug that writes unconditionally is a Critical defect.
- **Comment-preserving YAML edit:** `config/strategy.yaml` is heavily commented. Use the line-based replacement pattern from the existing `apply_sweep.py` (split on `#` to preserve trailing comments) — NOT a full YAML round-trip (which would drop comments).
- **`--dry-run` default for first runs (spec §13.5):** the workflow's `workflow_dispatch` has a `dry_run` input (default `true` initially). In dry-run, `apply_sweep.py` reports proposed changes + posts to Telegram but does NOT commit. Flip the default to `false` only after N successful dry-runs.
- **The commit-to-main triggers `deploy.yml`** (auto-redeploy with new params). This is the intended auto-apply path. The gate + dry-run protect it. Never commit in dry-run.
- **MR is never applied** (it's not swept — tick-resolution). If an `ETHUSDT_mean_reversion_sweep.json` ever appears, `apply_sweep.py` skips it (MR has no `PARAM_MAP` entries).
- **Engines run verbatim** — Phase 5 touches only `SweepResult`/`EngineKind` (backtest module, Task 1), `apply_sweep.py` (Task 2), the new workflow (Task 3), and deletes `sweep.yml` (Task 4). No engine/strategy/portfolio changes.
- Run cargo with `--manifest-path trading-engine-core/Cargo.toml`. Run Python from repo root. Frequent commits, one per task. Branch: `feat/rust-replay-backtest`.

---

## File Structure (Phase 5)

| File | Phase 5 change |
|---|---|
| `backtest/sweep.rs` | `Grid` gains a `param_deltas: Vec<(String,String)>` field; `SweepResult` gains `param_deltas`; `run_sweep` populates it from the IS-best arm. (Additive — the apply closure stays the source of truth; no sweep-behavior change.) |
| `backtest/replay.rs` | `EngineKind` gains `#[serde(rename_all = "snake_case")]` so JSON matches `budget_key()` filenames. |
| `tests/backtest_sweep.rs` | consistency test (closure-applied config matches `param_deltas`); `param_deltas` on SweepResult. |
| `backtest/apply_sweep.py` | **Rewrite** — consume `*_sweep.json` (the Phase-4 `SweepResult` schema, now with `param_deltas`); apply ONLY `decision.apply==true`; `PARAM_MAP` (param_name → YAML path); comment-preserving edit; `--dry-run`; changes manifest. |
| `tests/test_apply_sweep_phase5.py` (new) | apply-step unit tests (APPLY writes, KEEP skips, dry-run no-op, comment preservation). |
| `.github/workflows/backtest-rust.yml` (new) | weekly cron + `workflow_dispatch(dry_run)`; sweep → apply → commit-if-not-dry-run → Telegram → artifact. |
| `.github/workflows/sweep.yml` | **Delete** (Task 4) — the unfaithful auto-applying sweep, retired once Phase 5 is green. |

---

## Task 1: Rust contract — `param_deltas` on SweepResult + snake_case EngineKind

**Files:**
- Modify: `trading-engine-core/src/backtest/sweep.rs`
- Modify: `trading-engine-core/src/backtest/replay.rs`
- Test: `trading-engine-core/tests/backtest_sweep.rs`

**Interfaces:**
- Consumes: Phase 4 `Grid`, `grid_for`, `sweep_is`, `run_sweep`, `SweepResult`, `EngineKind` (now `Serialize`).
- Produces:
  - `Grid` becomes `Vec<(String, Vec<(String, String)>, Box<dyn FnOnce(&mut ReplayConfig) + Send>)>` — each entry is `(label, param_deltas, apply_closure)`.
  - `SweepResult` gains `pub param_deltas: Vec<(String, String)>` (the IS-best arm's deltas; empty when no candidate).
  - `EngineKind` derives `Serialize` with `#[serde(rename_all = "snake_case")]` → JSON `"trend"`/`"grid"`/`"swing"`/`"mean_reversion"` (matches `budget_key()` filenames).

- [ ] **Step 1: Add `#[serde(rename_all = "snake_case")]` to EngineKind**

In `backtest/replay.rs`, on the `EngineKind` enum:
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum EngineKind { Grid, Trend, Swing, MeanReversion }
```
Run `cargo build` → clean. (This makes the JSON `engine` field lowercase-snake, matching `budget_key()`.)

- [ ] **Step 2: Write the failing tests (param_deltas presence + closure/deltas consistency)**

Append to `tests/backtest_sweep.rs`:
```rust
use trading_engine_core::backtest::sweep::{grid_for};

#[test]
fn grid_yields_structured_param_deltas_matching_labels() {
    let g = grid_for(trading_engine_core::backtest::replay::EngineKind::Trend);
    // every grid point carries non-empty param_deltas
    assert!(g.iter().all(|(_, d, _)| !d.is_empty()));
    // the deltas' values reconstruct the label (e.g. deltas [("ema_fast","20"),("rr","2.0")] → label "ema_fast=20,rr=2.0")
    for (label, deltas, _) in &g {
        let reconstructed: String = deltas.iter().map(|(k, v)| format!("{}={}", k, v)).collect::<Vec<_>>().join(",");
        assert_eq!(label, &reconstructed, "label must match param_deltas (Phase-5 Python parses neither — it reads param_deltas directly; the label is for humans)");
    }
}

#[test]
fn sweep_result_carries_best_arm_param_deltas() {
    // run a tiny sweep where trend's IS-best is well-defined, then assert param_deltas is non-empty
    // and matches one of the grid's arms. (Reuses the rc()/bars helpers from the existing tests.)
    let rc = rc();                       // existing helper in backtest_sweep.rs
    let bars: Vec<trading_engine_core::models::bar::Bar> = (0..300).map(|i| {
        let p = 100.0 + ((i % 8) as f64 / 2.0);
        trading_engine_core::models::bar::Bar::new(p, p + 1.0, p - 1.0, p, 10.0, (i as i64) * 3_600_000)
    }).collect();
    let res = futures::executor::block_on(
        trading_engine_core::backtest::sweep::run_sweep(
            trading_engine_core::backtest::replay::EngineKind::Trend, &rc, bars, 1.0/3.0, 1.0)).unwrap();
    // param_deltas is non-empty iff there was an IS candidate with ≥5 trades; on this sawtooth trend trades,
    // so assert non-empty + that it's one of the declared grid arms.
    if res.best_label.is_some() {
        assert!(!res.param_deltas.is_empty(), "param_deltas must be populated when there's a best arm");
        let grid = grid_for(trading_engine_core::backtest::replay::EngineKind::Trend);
        let arms: Vec<_> = grid.into_iter().map(|(_, d, _)| d).collect();
        assert!(arms.iter().any(|a| *a == res.param_deltas), "param_deltas must equal a declared grid arm");
    }
}
```
(If `futures::executor` isn't available, use `tokio::runtime::Runtime::new().unwrap().block_on(...)` — `tokio` is a dep. Or mark the test `#[tokio::test]` like the existing sweep tests.)

- [ ] **Step 3: Run to verify it fails**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_sweep`
Expected: FAIL — grid tuples don't carry param_deltas (3-tuple vs 2-tuple mismatch), `SweepResult.param_deltas` missing.

- [ ] **Step 4: Extend `Grid` + `grid_for` to carry param_deltas (alongside the closure)**

In `backtest/sweep.rs`, change the Grid type + each `grid_for` arm to yield `(label, param_deltas, closure)`:
```rust
pub type Grid = Vec<(String, Vec<(String, String)>, Box<dyn FnOnce(&mut ReplayConfig) + Send>)>;

pub fn grid_for(kind: EngineKind) -> Grid {
    match kind {
        EngineKind::Trend => {
            let mut g = Vec::new();
            for &ema_fast in &[20_u32, 30] {
                for &rr in &[1.5_f64, 2.0, 2.5] {
                    let deltas = vec![("ema_fast".into(), ema_fast.to_string()), ("rr".into(), rr.to_string())];
                    let label = format!("ema_fast={},rr={}", ema_fast, rr);
                    g.push((label, deltas, Box::new(move |rc: &mut ReplayConfig| {
                        rc.trend.ema_fast = ema_fast; rc.trend.risk_reward_ratio = rr;
                    })));
                }
            }
            g
        }
        EngineKind::Grid => {
            let mut g = Vec::new();
            for &adx in &[22.0_f64, 25.0, 28.0] {
                for &chop in &[45.0_f64, 50.0, 55.0] {
                    let deltas = vec![("adx_max".into(), adx.to_string()), ("chop_min".into(), chop.to_string())];
                    let label = format!("adx_max={},chop_min={}", adx, chop);
                    g.push((label, deltas, Box::new(move |rc: &mut ReplayConfig| {
                        rc.grid.adx_range_max = adx; rc.grid.chop_range_min = chop;
                    })));
                }
            }
            g
        }
        EngineKind::Swing => {
            let mut g = Vec::new();
            for &min_score in &[2_usize, 3] {
                for &adx_entry in &[22.0_f64, 25.0, 28.0] {
                    let deltas = vec![("min_score".into(), min_score.to_string()), ("adx_entry".into(), adx_entry.to_string())];
                    let label = format!("min_score={},adx_entry={}", min_score, adx_entry);
                    g.push((label, deltas, Box::new(move |rc: &mut ReplayConfig| {
                        if let Some(s) = rc.swing.as_mut() { s.min_score = min_score; s.adx_range_entry = adx_entry; }
                    })));
                }
            }
            g
        }
        EngineKind::MeanReversion => panic!("MR is not swept (tick-resolution, no-edge)"),
    }
}
```
Update `sweep_is` to destructure `(label, _deltas, apply)` (it ignores deltas — the closure is the apply source of truth):
```rust
for (label, _deltas, apply) in grid_for(kind) {
    let mut point_rc = rc.clone();
    apply(&mut point_rc);
    point_rc.engine = kind;
    let run = run_engine_on_bars(kind, &point_rc, is_bars.to_vec()).await?;
    out.push((label, compute(&run, 0.0, bar_hours)));
}
```
Update `run_sweep`'s best-arm reconstruction to (a) find the `(label, deltas, apply)` triple and (b) populate `SweepResult.param_deltas`:
```rust
// In run_sweep, after picking best_label:
let grid = grid_for(kind);
if let Some((_, deltas, apply)) = grid.into_iter().find(|(l, _, _)| l == best_label) {
    let mut cand_rc = rc.clone();
    apply(&mut cand_rc);
    cand_rc.engine = kind;
    let candidate = run_validation(kind, &cand_rc, bars.clone(), oos_frac, bar_hours).await?;
    let decision = apply_gate(&baseline, &candidate);
    return Ok(SweepResult { engine: kind, baseline, best_label: Some(best_label), candidate: Some(candidate), decision, param_deltas: deltas });
}
```
And add `param_deltas: Vec::new()` to the `SweepResult` struct + to the no-candidate branch (`param_deltas: Vec::new()`). Add the field to the `#[derive(Debug, Clone, Serialize)] pub struct SweepResult { ..., pub param_deltas: Vec<(String, String)> }`.

- [ ] **Step 5: Run → GREEN**

Run: `cargo test --manifest-path trading-engine-core/Cargo.toml --test backtest_sweep`
Expected: PASS (all existing sweep tests + the 2 new ones). `cargo build --bin backtest_replay` clean.

- [ ] **Step 6: Commit**

```bash
git add trading-engine-core/src/backtest/{sweep.rs,replay.rs} trading-engine-core/tests/backtest_sweep.rs
git commit -m "feat(backtest): param_deltas on SweepResult + snake_case EngineKind (Phase 5 contract)"
```

---

## Task 2: Rewrite `apply_sweep.py` ( gated, comment-preserving, dry-run )

**Files:**
- Modify: `backtest/apply_sweep.py`
- Test: `tests/test_apply_sweep_phase5.py` (new)

**Interfaces:**
- Consumes: the `*_sweep.json` files written by `write_sweep_report` (the `SweepResult` schema: `{ engine, baseline, best_label, candidate, decision: { apply, gate_reasons }, param_deltas }`).
- Produces: `apply_sweep.py` with:
  - `PARAM_MAP = { ("trend","ema_fast"): "trend.ema_fast", ("trend","rr"): "trend.risk_reward_ratio", ("grid","adx_max"): "grid.adx_range_max", ("grid","chop_min"): "grid.chop_range_min", ("swing","min_score"): "swing.min_score", ("swing","adx_entry"): "swing.adx_range_entry" }` — keyed by `(engine, param_name)` → dotted YAML path in `config/strategy.yaml`.
  - `def apply(results_dir, config_path, dry_run=True) -> list[dict]` — reads every `*_sweep.json` in `results_dir`; for each where `decision.apply == true`, writes each `param_deltas` value to the YAML path from PARAM_MAP (comment-preserving line edit); returns a changes manifest `[{engine, param, yaml_path, from, to}]`.
  - CLI: `python backtest/apply_sweep.py <results_dir> <config_path> [--dry-run]`. In `--dry-run` (default), prints the manifest but does NOT write. Without `--dry-run`, writes the file.

- [ ] **Step 1: Write the failing tests**

`tests/test_apply_sweep_phase5.py`:
```python
import json, os, tempfile, pathlib
from backtest.apply_sweep import apply, PARAM_MAP

STRATEGY_YAML = """\
trend:
  enabled: true
  ema_fast: 30            # was 12
  risk_reward_ratio: 2.0  # RR
grid:
  adx_range_max: 25.0     # gate
  chop_range_min: 50.0
swing:
  min_score: 2
  adx_range_entry: 25.0
"""

def _write_sweep(tmp, engine, apply_flag, param_deltas):
    data = {"engine": engine, "baseline": {}, "best_label": "x",
            "candidate": {}, "decision": {"apply": apply_flag, "gate_reasons": [] if apply_flag else ["x"]},
            "param_deltas": param_deltas}
    p = tmp / f"ETHUSDT_{engine}_sweep.json"
    p.write_text(json.dumps(data)); return p

def test_apply_writes_only_apply_true_engines_comment_preserved():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d); cfg = tmp / "strategy.yaml"; cfg.write_text(STRATEGY_YAML)
        _write_sweep(tmp, "trend", True, [("ema_fast", "20"), ("rr", "1.5")])   # APPLY
        _write_sweep(tmp, "grid",  False, [("adx_max", "28.0")])                # KEEP → skip
        changes = apply(str(tmp), str(cfg), dry_run=False)
        # only trend changed
        assert {(c["engine"], c["param"]) for c in changes} == {("trend","ema_fast"), ("trend","rr")}
        new = cfg.read_text()
        assert "ema_fast: 20" in new and "risk_reward_ratio: 1.5" in new
        # grid unchanged
        assert "adx_range_max: 25.0" in new
        # comments preserved
        assert "# was 12" in new and "# RR" in new

def test_dry_run_writes_nothing_but_reports_manifest():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d); cfg = tmp / "strategy.yaml"; orig = STRATEGY_YAML; cfg.write_text(orig)
        _write_sweep(tmp, "trend", True, [("ema_fast", "20")])
        changes = apply(str(tmp), str(cfg), dry_run=True)
        assert len(changes) == 1 and changes[0]["to"] == "20"
        assert cfg.read_text() == orig, "dry-run must NOT write the file"

def test_mr_sweep_json_is_skipped():
    with tempfile.TemporaryDirectory() as d:
        tmp = pathlib.Path(d); cfg = tmp / "strategy.yaml"; cfg.write_text(STRATEGY_YAML)
        _write_sweep(tmp, "mean_reversion", True, [("anything","1")])  # MR has no PARAM_MAP entries
        changes = apply(str(tmp), str(cfg), dry_run=False)
        assert changes == [], "MR must never be applied (no PARAM_MAP entries)"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_apply_sweep_phase5.py -v` (from repo root; ensure `pip install pytest` if missing — the repo's Python tests already use it)
Expected: FAIL — `apply` / `PARAM_MAP` not found (or the old signature doesn't match).

- [ ] **Step 3: Rewrite `apply_sweep.py`**

`backtest/apply_sweep.py` (full rewrite — the old schema was unrelated to SweepResult):
```python
"""Apply gated sweep results to live config (Phase 5).

Reads *_sweep.json (the Phase-4 SweepResult schema, now with param_deltas) from a
results dir. For each engine where decision.apply == true, writes each param_deltas
value to config/strategy.yaml via PARAM_MAP (comment-preserving line edit). Skips
KEEP engines and MR (no PARAM_MAP entries). --dry-run reports the manifest without
writing.

Usage: python backtest/apply_sweep.py <results_dir> <config_path> [--dry-run]
"""
import argparse, json, sys
from pathlib import Path

# (engine, param_name) -> dotted YAML path in config/strategy.yaml
PARAM_MAP = {
    ("trend", "ema_fast"): "trend.ema_fast",
    ("trend", "rr"):       "trend.risk_reward_ratio",
    ("grid",  "adx_max"):  "grid.adx_range_max",
    ("grid",  "chop_min"): "grid.chop_range_min",
    ("swing", "min_score"):"swing.min_score",
    ("swing", "adx_entry"):"swing.adx_range_entry",
}

def _set_yaml_value(text: str, dotted_key: str, value: str) -> str:
    """Comment-preserving line edit. dotted_key e.g. 'trend.ema_fast' -> find the
    line 'ema_fast:' at the section depth of 'trend.' and set its value, keeping
    any trailing '  # comment'."""
    keys = dotted_key.split(".")
    leaf = keys[-1]
    depth = (len(keys) - 1) * 2  # YAML 2-space indent per section
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if indent == depth and stripped.startswith(f"{leaf}:"):
            comment = ""
            if "#" in stripped:
                comment = "  " + stripped.split("#", 1)[1].rstrip()
            lines[i] = f"{' '*depth}{leaf}: {value}{comment}"
            break
    return "\n".join(lines)

def _value_for(yaml_text: str, dotted_key: str) -> str:
    keys = dotted_key.split("."); leaf = keys[-1]; depth = (len(keys)-1)*2
    for line in yaml_text.split("\n"):
        s = line.lstrip()
        if len(line)-len(s) == depth and s.startswith(f"{leaf}:"):
            v = s[len(leaf)+1:].split("#",1)[0].strip()
            return v
    return ""

def apply(results_dir: str, config_path: str, dry_run: bool = True) -> list:
    changes = []
    text = Path(config_path).read_text()
    for jf in sorted(Path(results_dir).glob("*_sweep.json")):
        try:
            rep = json.loads(jf.read_text())
        except Exception as e:
            print(f"warn: skip {jf.name}: {e}"); continue
        engine = rep.get("engine"); decision = rep.get("decision", {})
        if not decision.get("apply"):
            print(f"{engine}: KEEP ({decision.get('gate_reasons', [])}) — skipping")
            continue
        for param, value in rep.get("param_deltas", []):
            yaml_path = PARAM_MAP.get((engine, param))
            if not yaml_path:
                print(f"warn: {engine}.{param} not in PARAM_MAP — skipping (MR?)")
                continue
            old = _value_for(text, yaml_path)
            if old == value:
                continue
            changes.append({"engine": engine, "param": param, "yaml_path": yaml_path, "from": old, "to": value})
            text = _set_yaml_value(text, yaml_path, value)
    if changes and not dry_run:
        Path(config_path).write_text(text)
        print(f"APPLIED {len(changes)} change(s) to {config_path}")
    elif changes and dry_run:
        print(f"DRY-RUN: would apply {len(changes)} change(s) (no file written)")
    else:
        print("no gated changes to apply")
    return changes

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("results_dir"); ap.add_argument("config_path")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    changes = apply(a.results_dir, a.config_path, dry_run=a.dry_run)
    print(json.dumps(changes, indent=2))
    sys.exit(0)
```

- [ ] **Step 4: Run → GREEN**

Run: `python -m pytest tests/test_apply_sweep_phase5.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add backtest/apply_sweep.py tests/test_apply_sweep_phase5.py
git commit -m "feat(backtest): rewrite apply_sweep.py — gated, comment-preserving, dry-run (Phase 5 Task 2)"
```

---

## Task 3: `.github/workflows/backtest-rust.yml` (weekly cron + dry-run + Telegram)

**Files:**
- Create: `.github/workflows/backtest-rust.yml`

**Interfaces:**
- Produces: a workflow on `schedule: weekly cron` + `workflow_dispatch` (inputs: `dry_run` bool default `true`, `pairs` string, `months` string). Runs on `ubuntu-latest`. Steps: checkout → Rust toolchain → Python → `cargo build --release --bin backtest_replay` → for each engine × pair: `cargo run --bin backtest_replay -- <pair> <months> <engine> --sweep` → `python backtest/apply_sweep.py backtest/results/replay config/strategy.yaml [--dry-run]` → if not dry-run AND `apply` produced changes: commit + push to main (→ triggers deploy) → Telegram per-engine verdict (APPLY/KEEP) → upload artifact.

- [ ] **Step 1: Write the workflow**

`.github/workflows/backtest-rust.yml`:
```yaml
name: Rust Replay Backtest + Sweep

# Weekly faithful sweep (grid/trend/swing) behind the OOS gate. Applies winning
# params to config/strategy.yaml ONLY when decision.apply==true AND not dry-run.
# A commit to main triggers deploy.yml (live redeploy with new params).
on:
  schedule:
    - cron: '17 4 * * 1'  # weekly Monday ~04:17 UTC (off-round, off-:00)
  workflow_dispatch:
    inputs:
      dry_run:
        description: "Dry-run (report only, no commit)"
        type: boolean
        default: true
      pairs:
        description: "Comma-separated pairs"
        default: "ETHUSDT,BNBUSDT,DOGEUSDT,XRPUSDT"
      months:
        description: "Months of history"
        default: "12"

jobs:
  sweep:
    runs-on: ubuntu-latest
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.12" }
      - uses: dtolnay/rust-toolchain@stable
      - name: Build backtest_replay
        run: cargo build --release --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay
      - name: Sweep each engine x pair
        run: |
          ENGINES="trend grid swing"
          for PAIR in $(echo "${{ inputs.pairs || 'ETHUSDT,BNBUSDT,DOGEUSDT,XRPUSDT' }}" | tr ',' ' '); do
            for ENG in $ENGINES; do
              echo "=== sweep $PAIR $ENG ==="
              cargo run --release --manifest-path trading-engine-core/Cargo.toml --bin backtest_replay --quiet -- $PAIR ${{ inputs.months || '12' }} $ENG --sweep || echo "  (sweep failed, continuing)"
            done
          done
      - name: Apply gated changes
        id: apply
        run: |
          if [ "${{ inputs.dry_run || true }}" = "true" ]; then
            python backtest/apply_sweep.py backtest/results/replay config/strategy.yaml --dry-run | tee apply_manifest.json
            echo "changed=false" >> $GITHUB_OUTPUT
          else
            python backtest/apply_sweep.py backtest/results/replay config/strategy.yaml | tee apply_manifest.json
            # changed iff the manifest lists any change
            if [ "$(python -c 'import json,sys; print(len(json.load(open("apply_manifest.json"))))')" -gt 0 ]; then
              echo "changed=true" >> $GITHUB_OUTPUT
            else
              echo "changed=false" >> $GITHUB_OUTPUT
            fi
          fi
      - name: Commit + push (only if changed and not dry-run)
        if: steps.apply.outputs.changed == 'true' && inputs.dry_run != true
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add config/strategy.yaml
          git commit -m "auto(backtest): apply OOS-gated sweep params $(date -u +%F)
          $(python -c 'import json; [print(f"  {c[\"engine\"]}.{c[\"param\"]}: {c[\"from\"]} -> {c[\"to\"]}") for c in json.load(open("apply_manifest.json"))]')
          Co-Authored-By: Claude <noreply@anthropic.com>"
          git push origin main  # → triggers deploy.yml (live redeploy with new params)
      - name: Telegram per-engine verdict
        if: always()
        env: { TG: "${{ secrets.TELEGRAM_BOT_TOKEN }}", CHAT: "${{ secrets.TELEGRAM_CHAT_ID }}" }
        run: |
          [ -z "$TG" ] && exit 0
          MSG="📊 *Rust Replay Sweep* (`${{ inputs.dry_run == false && 'APPLY' || 'dry-run' }}`)%0A"
          for f in backtest/results/replay/*_sweep.json; do
            [ -f "$f" ] || continue
            MSG+="$(python -c "import json; r=json.load(open('$f')); d=r['decision']; print(f\"• {r['engine']}: {'APPLY' if d['apply'] else 'KEEP'} — {', '.join(d['gate_reasons']) or 'all checks pass'}\")")"%0A"
          done
          curl -s -X POST "https://api.telegram.org/bot$TG/sendMessage" -d chat_id="$CHAT" -d parse_mode=HTML --data-urlencode "text=$MSG" || true
      - name: Upload artifacts
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: rust-replay-sweep
          path: |
            backtest/results/replay/
            apply_manifest.json
```

- [ ] **Step 2: Validate the YAML locally**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/backtest-rust.yml'))" && echo OK`
Expected: `OK` (valid YAML).

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/backtest-rust.yml
git commit -m "ci: backtest-rust.yml — weekly sweep + OOS-gated apply + Telegram (Phase 5 Task 3)"
```

---

## Task 4: Retire `.github/workflows/sweep.yml`

**Files:**
- Delete: `.github/workflows/sweep.yml`

**Context:** `sweep.yml` is the OLD unfaithful auto-applying sweep (vectorbt, EC2/SSM, auto-applies on in-sample delta-Sharpe alone — no OOS gate). Phase 5 replaces it. Deleting it removes the redundant + ungated auto-apply path. (Its weekly cron and EC2 machinery are no longer needed — `backtest-rust.yml` runs on the Actions runner.)

- [ ] **Step 1: Delete the file**

```bash
git rm .github/workflows/sweep.yml
```

- [ ] **Step 2: Confirm no other workflow references it**

Run: `grep -rl "sweep.yml\|backtest-sweep" .github/ backtest/ 2>/dev/null` (expect no references to the deleted workflow).
Expected: empty (or only the new `backtest-rust.yml` if it mentions the image name — it doesn't).

- [ ] **Step 3: Commit**

```bash
git commit -m "chore: retire sweep.yml (unfaithful ungated auto-apply) — replaced by backtest-rust.yml (Phase 5 Task 4)"
```

---

## Task 5: Smoke — apply step on a synthetic APPLY sweep.json (local)

**Files:** none (run + report)

- [ ] **Step 1: Generate a synthetic APPLY sweep.json + run apply**

```bash
# Build a fake ETHUSDT_trend_sweep.json where decision.apply==true + param_deltas
python -c "
import json, os
os.makedirs('backtest/results/replay', exist_ok=True)
json.dump({'engine':'trend','baseline':{'oos':{'sharpe':0.5,'total_trades':20,'max_drawdown_pct':4.0}},
 'best_label':'ema_fast=20,rr=1.5','candidate':{'oos':{'sharpe':1.2,'total_trades':25,'max_drawdown_pct':5.0}},
 'decision':{'apply':True,'gate_reasons':[]},'param_deltas':[('ema_fast','20'),('rr','1.5')]},
 open('backtest/results/replay/ETHUSDT_trend_sweep.json','w'))
"
cp config/strategy.yaml /tmp/strategy.yaml.bak
python backtest/apply_sweep.py backtest/results/replay config/strategy.yaml --dry-run
python backtest/apply_sweep.py backtest/results/replay config/strategy.yaml   # applies
grep -E "^  ema_fast:|^  risk_reward_ratio:" config/strategy.yaml | head -2    # confirm 20 + 1.5
cp /tmp/strategy.yaml.bak config/strategy.yaml   # restore (don't leave the local edit)
```

- [ ] **Step 2: Record the smoke result**

Confirm in `/Users/amro/WebstormProjects/trading-humming-bot/.superpowers/sdd/task-5-report.md`: the dry-run reported the manifest without writing; the real apply wrote `ema_fast: 20` + `risk_reward_ratio: 1.5` with comments preserved; the file was restored. Note: the actual weekly workflow will run in CI (GitHub Actions) — this local smoke proves the apply step works end-to-end on a controlled APPLY case.

- [ ] **Step 3: Commit (only if a bug was found + fixed; otherwise the deliverable is the recorded smoke)**

---

## Phase 5 Exit Criteria

- `cargo test --test backtest_sweep` green (param_deltas + snake_case EngineKind + the Phase 4 suite).
- `python -m pytest tests/test_apply_sweep_phase5.py` green (apply writes only APPLY engines, dry-run no-op, MR skipped, comments preserved).
- `backtest-rust.yml` valid YAML; `sweep.yml` deleted.
- Local smoke: apply step writes the synthetic APPLY params + preserves comments + restores.
- No engine/strategy/portfolio changes; the gate (Phase 4) remains the first line of defense, the Python `decision.apply` check the second.

**First production runs MUST be `workflow_dispatch` with `dry_run=true`** (the default). Flip to a real run (commit) only after 2-4 successful dry-runs show sane KEEP/APPLY verdicts on Telegram. The weekly cron is the steady-state.

---

## Self-Review

1. **Spec coverage:** §8 extend apply_sweep.py (Task 2) + workflow (Task 3) + retire sweep.yml (Task 4) + dry-run-first (§13.5, the workflow's dry_run default). §6 gate (Phase 4, untouched) + Python `decision.apply` check (defense in depth, Task 2). §7 MR never applied (Task 2 test + no PARAM_MAP entries). Phase-4 review's param_deltas (Task 1) + EngineKind naming (Task 1). ✅
2. **Placeholder scan:** Task 5 is run+report (specified commands + restore). The workflow YAML is complete (not a sketch). The Python apply is full. No bare TODOs.
3. **Type consistency:** `Grid = Vec<(String, Vec<(String,String)>, Box<dyn FnOnce...>)>` (Task 1) consumed by `sweep_is` (destructure `_deltas`) + `run_sweep` (find triple, populate `param_deltas`). `SweepResult.param_deltas: Vec<(String,String)>` serialized to JSON, consumed by Python `apply_sweep.py` (reads `param_deltas`, maps via `PARAM_MAP`). `PARAM_MAP` keys `("trend","ema_fast")` etc. match the grid's delta keys exactly ("ema_fast","rr","adx_max","chop_min","min_score","adx_entry"). EngineKind JSON now snake_case (Task 1) → matches `*_sweep.json` filename's `budget_key()` → Python globs `*_sweep.json` + reads `engine` field. ✅
