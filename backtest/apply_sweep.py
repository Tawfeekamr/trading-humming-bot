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
                # keep the '#' — split drops it; restore so '# comment' survives
                comment = "  #" + stripped.split("#", 1)[1].rstrip()
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
