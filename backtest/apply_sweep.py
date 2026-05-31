"""
Apply Sweep Recommendations to Live Config
Reads sweep JSON results, picks best params by delta Sharpe, updates strategy.yaml.

Usage: cat results/*.json | python apply_sweep.py config/strategy.yaml
Outputs: list of changes made (or "no changes" if live params are optimal)
"""

import sys
import json
from pathlib import Path

# Mapping: sweep param name → YAML path in strategy.yaml
PARAM_MAP = {
    # Grid params (from grid.recommendations)
    "bb_period": "indicators.bollinger.period",
    "rsi_oversold": "indicators.rsi.oversold",
    "rsi_overbought": "indicators.rsi.overbought",
    "atr_multiplier": "indicators.atr.spacing_multiplier",
    # Trend params (from trend.recommendations)
    "trend_ema_fast": "trend.ema_fast",
    "trend_ema_slow": "trend.ema_slow",
    "trend_rsi_min": "trend.rsi_min",
    "trend_rsi_max": "trend.rsi_max",
}


def parse_sweep_results(raw_json: str) -> list[dict]:
    """Parse concatenated JSON objects from sweep output."""
    results = []
    # Split on }{ boundary for concatenated JSON
    chunks = raw_json.strip().split("}{")
    for i, chunk in enumerate(chunks):
        if i > 0:
            chunk = "{" + chunk
        if i < len(chunks) - 1:
            chunk = chunk + "}"
        try:
            results.append(json.loads(chunk))
        except json.JSONDecodeError:
            continue
    return results


def collect_recommendations(sweep_results: list[dict]) -> dict:
    """
    Collect all suggestions across pairs and strategies.
    For each param, keep the one with the highest delta_sharpe.
    """
    # param_name → {value, delta_sharpe, pair, strategy}
    best = {}

    for result in sweep_results:
        pair = result.get("pair", "UNKNOWN")

        # Grid recommendations
        grid_recs = result.get("grid", {}).get("recommendations", {})
        for param, info in grid_recs.items():
            if info.get("suggest_change", False):
                delta = abs(info.get("delta_sharpe", 0))
                key = f"grid_{param}" if not param.startswith("grid_") else param
                if key not in best or delta > best[key]["delta_sharpe"]:
                    best[key] = {
                        "yaml_path": PARAM_MAP.get(param, None),
                        "value": info["best"],
                        "delta_sharpe": delta,
                        "pair": pair,
                        "strategy": "grid",
                    }

        # Trend recommendations
        trend_recs = result.get("trend", {}).get("recommendations", {})
        for param, info in trend_recs.items():
            if info.get("suggest_change", False):
                delta = abs(info.get("delta_sharpe", 0))
                key = f"trend_{param}" if not param.startswith("trend_") else param
                if key not in best or delta > best[key]["delta_sharpe"]:
                    best[key] = {
                        "yaml_path": PARAM_MAP.get(key, None),
                        "value": info["best"],
                        "delta_sharpe": delta,
                        "pair": pair,
                        "strategy": "trend",
                    }

    return best


def update_yaml(yaml_path: str, changes: dict) -> bool:
    """Update strategy.yaml with the recommended changes. Returns True if any changes made."""
    content = Path(yaml_path).read_text()
    original = content
    lines = content.split("\n")

    for param_key, info in changes.items():
        yaml_key = info.get("yaml_path")
        if not yaml_key:
            continue

        value = info["value"]
        # Convert float to int if it's a whole number
        if isinstance(value, float) and value == int(value):
            value = int(value)

        keys = yaml_key.split(".")
        key_name = keys[-1]

        # Find the line with this key in the right section
        # Simple approach: find the key in the right indent level
        section_depth = len(keys) - 1  # 0=top, 1=nested, 2=double-nested

        for i, line in enumerate(lines):
            stripped = line.lstrip()
            indent = len(line) - len(stripped)
            expected_indent = section_depth * 2  # YAML uses 2-space indent

            if indent != expected_indent:
                continue

            if stripped.startswith(f"{key_name}:"):
                # Update the value
                comment = ""
                if "#" in stripped:
                    comment = "  # " + stripped.split("#", 1)[1].strip()
                lines[i] = f"{' ' * expected_indent}{key_name}: {value}{comment}"
                print(f"  {yaml_path}: {key_name} → {value} (from {info['pair']} {info['strategy']}, Δ Sharpe +{info['delta_sharpe']:.2f})")
                break

    new_content = "\n".join(lines)
    if new_content != original:
        Path(yaml_path).write_text(new_content)
        return True
    return False


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: cat results/*.json | python apply_sweep.py config/strategy.yaml")
        sys.exit(1)

    yaml_path = sys.argv[1]

    # Read sweep JSON from stdin
    raw = sys.stdin.read()
    if not raw.strip():
        print("No sweep data provided")
        sys.exit(0)

    results = parse_sweep_results(raw)
    print(f"Parsed {len(results)} sweep results")

    recommendations = collect_recommendations(results)

    if not recommendations:
        print("No param changes recommended — live config is optimal")
        sys.exit(0)

    print(f"\nApplying {len(recommendations)} param changes:")
    changed = update_yaml(yaml_path, recommendations)

    if changed:
        print(f"\n✅ Updated {yaml_path}")
    else:
        print("\n⚠️ No changes written (keys not found in YAML)")
