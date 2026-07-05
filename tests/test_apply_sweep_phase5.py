import json, os, tempfile, pathlib
from backtest.apply_sweep import apply, PARAM_MAP, _cli_dry_run

STRATEGY_YAML = """\
trend:
  enabled: true
  ema_fast: 30            # was 12
  rr_ratio: 2.0  # RR
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
        assert "ema_fast: 20" in new and "rr_ratio: 1.5" in new
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

def test_cli_defaults_to_dry_run_safe():
    # Regression: bare CLI invocation (no flags) used to WRITE live config
    # because --dry-run was store_true (opt-in). The default must be dry-run.
    # Bare invocation (no flags) → dry-run (NO write) — the Critical safety default
    assert _cli_dry_run(apply_flag=False, dry_run_flag=False) is True
    # --apply → writes
    assert _cli_dry_run(apply_flag=True, dry_run_flag=False) is False
    # --dry-run wins even if --apply also passed (safe)
    assert _cli_dry_run(apply_flag=True, dry_run_flag=True) is True
    # explicit --dry-run alone → dry-run
    assert _cli_dry_run(apply_flag=False, dry_run_flag=True) is True
