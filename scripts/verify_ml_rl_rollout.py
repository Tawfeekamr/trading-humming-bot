#!/usr/bin/env python3
"""Verify the ML/RL paper rollout without touching active routing state.

The verifier is intentionally report-only.  It reads model artifacts, manifests,
walk-forward evidence, the shadow journal, and the configured routing mode.  It
never starts a service, writes a cache, promotes PPO, or calls an exchange API.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Iterable


_CACHE_TTL_MS = 180_000
_MANIFEST_SUFFIX = ".metadata.json"
_ARTIFACT_SUFFIXES = {".pkl", ".onnx", ".zip"}
_REQUIRED_MANIFEST_KEYS = {
    "pair",
    "timeframe",
    "train_start",
    "train_end",
    "feature_contract_hash",
    "label_params",
    "class_distribution",
    "metrics",
    "source_commit",
    "artifact_sha256",
}


def _failure(failures: list[str], code: str) -> None:
    if code not in failures:
        failures.append(code)


def _warning(warnings: list[str], code: str) -> None:
    if code not in warnings:
        warnings.append(code)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("JSON root must be an object")
    return value


def _report_metadata(report: dict[str, Any]) -> dict[str, Any]:
    value = report.get("metadata", {})
    return value if isinstance(value, dict) else {}


def _verification_now_ms(report: dict[str, Any]) -> int:
    metadata = _report_metadata(report)
    value = (
        metadata.get("verification_now_ms")
        or report.get("verification_now_ms")
        or os.environ.get("ML_RL_VERIFY_NOW_MS")
    )
    if value is None:
        return int(time.time() * 1000)
    if isinstance(value, bool):
        raise ValueError("verification_now_ms must be an integer")
    parsed = int(value)
    if parsed < 0:
        raise ValueError("verification_now_ms must be non-negative")
    return parsed


def _relative_path(root: Path, value: str | Path) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else root / candidate


def _model_paths(root: Path, report: dict[str, Any]) -> list[Path]:
    metadata = _report_metadata(report)
    raw: list[Any] = []
    for source in (metadata, report):
        for key in ("model_paths", "model_artifacts", "artifacts", "models", "model_checksums"):
            value = source.get(key)
            if isinstance(value, dict):
                raw.extend(value.keys() if key == "model_checksums" else value.values())
            elif isinstance(value, (list, tuple)):
                raw.extend(value)
            elif isinstance(value, str):
                raw.append(value)
        for key in ("rf_model", "ppo_model", "model_path"):
            value = source.get(key)
            if isinstance(value, str):
                raw.append(value)
    paths: list[Path] = []
    for value in raw:
        if isinstance(value, dict):
            value = value.get("path") or value.get("artifact")
        if not isinstance(value, str) or not value:
            continue
        path = _relative_path(root, value)
        if path.name.endswith(_MANIFEST_SUFFIX):
            path = Path(str(path)[: -len(_MANIFEST_SUFFIX)])
        if path not in paths:
            paths.append(path)
    # Only discover artifacts that have a manifest.  Unrelated historical model
    # files must not make a paper report fail merely by being present.
    models_root = root / "models"
    if not paths and models_root.exists():
        for path in sorted(models_root.rglob("*")):
            if path.is_file() and path.suffix in _ARTIFACT_SUFFIXES and Path(
                f"{path}{_MANIFEST_SUFFIX}"
            ).exists():
                paths.append(path)
    return paths


def _verify_manifests(
    root: Path,
    report: dict[str, Any],
    failures: list[str],
    warnings: list[str],
) -> dict[str, str]:
    checksums: dict[str, str] = {}
    expected_feature_hash = (
        _report_metadata(report).get("feature_contract_hash")
        or _report_metadata(report).get("feature_hash")
        or report.get("feature_contract_hash")
    )
    if expected_feature_hash is None:
        _failure(failures, "feature_hash_missing")
    paths = _model_paths(root, report)
    if not paths:
        _warning(warnings, "model_manifests_missing")
        return checksums
    for artifact in paths:
        key = str(artifact.relative_to(root)) if artifact.is_relative_to(root) else str(artifact)
        manifest_path = Path(f"{artifact}{_MANIFEST_SUFFIX}")
        if not artifact.exists() or not manifest_path.exists():
            _failure(failures, "manifest_missing")
            continue
        try:
            actual = _sha256(artifact)
            manifest = _load_json(manifest_path)
            missing = _REQUIRED_MANIFEST_KEYS - manifest.keys()
            if missing:
                raise ValueError("manifest missing required keys")
            if not isinstance(manifest.get("artifact_sha256"), str):
                raise ValueError("artifact_sha256 missing")
            if manifest["artifact_sha256"] != actual:
                _failure(failures, "metadata_checksum_mismatch")
            checksums[key] = actual
            if expected_feature_hash is not None and manifest.get("feature_contract_hash") != expected_feature_hash:
                _failure(failures, "feature_hash_mismatch")
        except (OSError, ValueError, json.JSONDecodeError):
            _failure(failures, "manifest_invalid")
    claims = _report_metadata(report).get("model_checksums") or report.get("model_checksums")
    if isinstance(claims, dict):
        for path, claimed in claims.items():
            actual = checksums.get(str(Path(path)))
            if actual is not None and claimed != actual:
                _failure(failures, "metadata_checksum_mismatch")
    return checksums


def _routing_mode(root: Path, report: dict[str, Any]) -> str | None:
    config = root / "config" / "strategy.yaml"
    if config.exists():
        text = config.read_text(encoding="utf-8")
        match = re.search(
            r"(?ms)^routing:\s*\n(?:(?!^\S).)*?^\s+mode:\s*([^#\s]+)", text
        )
        if match:
            return match.group(1).strip().lower()
    metadata = _report_metadata(report)
    for source in (metadata, report):
        for key in ("routing_mode", "mode", "shadow_mode"):
            value = source.get(key)
            if isinstance(value, str):
                return value.lower()
    return None


def _cache_snapshot(path: Path) -> tuple[str | None, int | None, int | None]:
    if not path.exists():
        return None, None, None
    stat = path.stat()
    return _sha256(path), stat.st_size, stat.st_mtime_ns


def _verify_shadow_mode_and_cache(
    root: Path,
    report: dict[str, Any],
    failures: list[str],
    warnings: list[str],
) -> tuple[str | None, tuple[str | None, int | None, int | None]]:
    mode = _routing_mode(root, report)
    if mode != "shadow":
        _failure(failures, "shadow_mode")
    cache = root / "data" / "routing_cache.json"
    before = _cache_snapshot(cache)
    metadata = _report_metadata(report)
    expected = (
        metadata.get("routing_cache_sha256")
        or metadata.get("active_routing_cache_sha256")
        or metadata.get("active_cache_sha256")
        or report.get("routing_cache_sha256")
        or report.get("active_cache_sha256")
    )
    if expected is not None and before[0] != expected:
        _failure(failures, "active_routing_cache_changed")
    elif before[0] is not None and expected is None:
        _warning(warnings, "active_routing_cache_untracked")
    return mode, before


def _verify_report_gate(
    report: dict[str, Any], failures: list[str], warnings: list[str]
) -> None:
    gate = report.get("promotion") or report.get("promotion_gate") or _report_metadata(report).get("promotion")
    if not isinstance(gate, dict) or not isinstance(gate.get("eligible"), bool):
        _failure(failures, "report_gate")
        return
    if gate["eligible"]:
        return
    reasons = gate.get("reasons", [])
    reasons = [str(reason) for reason in reasons] if isinstance(reasons, list) else [str(reasons)]
    inconclusive = any("inconclusive" in reason for reason in reasons)
    if inconclusive:
        _warning(warnings, "report_gate_inconclusive")
    else:
        _failure(failures, "report_gate_rejected")


def _verify_attribution(
    report: dict[str, Any], failures: list[str], warnings: list[str]
) -> dict[str, Any]:
    metrics = report.get("metrics", {})
    status = report.get("status", {})
    evidence = status.get("evidence", {}) if isinstance(status, dict) else {}
    total_raw = (
        metrics.get("trade_count")
        if isinstance(metrics, dict)
        else report.get("trade_count")
    )
    if total_raw is None:
        total_raw = report.get("trade_count") or (
            evidence.get("trade_count") if isinstance(evidence, dict) else None
        )
    missing_raw = report.get("attribution_missing_count", 0)
    try:
        total = int(total_raw) if total_raw is not None else None
        missing = int(missing_raw)
    except (TypeError, ValueError):
        _failure(failures, "attribution_coverage")
        return {"total": 0, "attributed": 0, "missing": 0, "ratio": 0.0}
    if missing < 0 or (total is not None and (total < 0 or missing > total)):
        _failure(failures, "attribution_coverage")
        return {"total": max(total or 0, 0), "attributed": 0, "missing": max(missing, 0), "ratio": 0.0}
    if total is None:
        _warning(warnings, "attribution_total_missing")
        total = missing
    attributed = max(total - missing, 0)
    ratio = 1.0 if total == 0 else round(attributed / total, 12)
    if missing:
        _warning(warnings, "attribution_incomplete")
    return {"total": total, "attributed": attributed, "missing": missing, "ratio": ratio}


def _verify_shadow_records(
    root: Path,
    shadow_path: str,
    report: dict[str, Any],
    failures: list[str],
    warnings: list[str],
) -> int:
    path = _relative_path(root, shadow_path)
    if not path.exists():
        _warning(warnings, "shadow_records_missing")
        return 0
    try:
        from src.rl.shadow_schema import validate_decision
    except ImportError:
        _failure(failures, "shadow_schema_unavailable")
        return 0
    now_ms = _verification_now_ms(report)
    count = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        _failure(failures, "shadow_records_invalid")
        return 0
    for line_number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (TypeError, ValueError, json.JSONDecodeError):
            _failure(failures, "shadow_records_invalid")
            continue
        if not isinstance(value, dict):
            _failure(failures, "shadow_records_invalid")
            continue
        valid, reason = validate_decision(value, now_ms=now_ms, max_age_ms=_CACHE_TTL_MS)
        if not valid:
            _failure(failures, "shadow_records_stale" if "stale" in reason else "shadow_records_invalid")
            continue
        count += 1
    return count


def verify_ml_rl_rollout(repo_root: str, report_path: str, shadow_path: str) -> dict[str, Any]:
    """Return deterministic paper-rollout evidence and conservative gate status."""
    root = Path(repo_root).resolve()
    failures: list[str] = []
    warnings: list[str] = []
    report: dict[str, Any] = {}
    ppo_active = False
    try:
        report = _load_json(_relative_path(root, report_path))
    except (OSError, ValueError, json.JSONDecodeError):
        # A missing report is not evidence of safety.  Keep the public boolean
        # conservative and expose the required ppo_active failure code.
        _failure(failures, "ppo_active")
        _failure(failures, "report_missing")
        coverage = {"total": 0, "attributed": 0, "missing": 0, "ratio": 0.0}
        checksums: dict[str, str] = {}
    else:
        metadata = _report_metadata(report)
        ppo_active = any(
            source.get("ppo_active") is True
            for source in (report, metadata)
            if isinstance(source, dict)
        )
        if ppo_active:
            _failure(failures, "ppo_active")
        mode, before_cache = _verify_shadow_mode_and_cache(root, report, failures, warnings)
        if mode is None:
            _warning(warnings, "routing_mode_unreported")
        elif mode == "live":
            ppo_active = True
            _failure(failures, "ppo_active")
        _verify_report_gate(report, failures, warnings)
        coverage = _verify_attribution(report, failures, warnings)
        checksums = _verify_manifests(root, report, failures, warnings)
        _verify_shadow_records(root, shadow_path, report, failures, warnings)
        status = report.get("status")
        cache_status = status.get("cache") if isinstance(status, dict) else None
        shadow_status = status.get("shadow") if isinstance(status, dict) else None
        if isinstance(cache_status, dict):
            if cache_status.get("state") == "stale":
                _failure(failures, "cache_stale")
            try:
                if int(cache_status.get("age_ms", 0) or 0) > _CACHE_TTL_MS:
                    _failure(failures, "cache_stale")
            except (TypeError, ValueError):
                _failure(failures, "cache_stale")
        if isinstance(shadow_status, dict):
            try:
                if int(shadow_status.get("decision_age_ms", 0) or 0) > _CACHE_TTL_MS:
                    _failure(failures, "shadow_records_stale")
            except (TypeError, ValueError):
                _failure(failures, "shadow_records_invalid")
        after_cache = _cache_snapshot(root / "data" / "routing_cache.json")
        if before_cache != after_cache:
            _failure(failures, "active_routing_cache_changed")
    return {
        "ok": not failures,
        "failures": failures,
        "warnings": warnings,
        "ppo_active": ppo_active,
        "model_checksums": checksums,
        "attribution_coverage": coverage,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--report", required=True)
    parser.add_argument("--shadow", required=True)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    result = verify_ml_rl_rollout(args.repo_root, args.report, args.shadow)
    encoded = json.dumps(result, sort_keys=True, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    print(
        f"rollout verification: {'PASS' if result['ok'] else 'FAIL'} "
        f"failures={len(result['failures'])} warnings={len(result['warnings'])}",
        file=sys.stderr,
    )
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
