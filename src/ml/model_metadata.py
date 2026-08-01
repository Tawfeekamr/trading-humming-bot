"""Immutable, checksummed metadata manifests for trained ML artifacts."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import uuid
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

REQUIRED_KEYS = (
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
)


def canonical_feature_contract_hash(columns: list[str] | tuple[str, ...] | None) -> str | None:
    if columns is None:
        return None
    canonical = json.dumps(list(columns), separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

def metadata_for_classifier(clf: Any) -> dict[str, Any]:
    """Build a manifest only from explicit, truthful classifier provenance."""
    required_fields = ("pair", "timeframe", "train_start", "train_end")
    missing = [field for field in required_fields if not getattr(clf, field, None)]
    if missing:
        raise ValueError(f"model provenance missing required fields: {', '.join(missing)}")
    feature_hash = getattr(clf, "feature_contract_hash", None)
    if feature_hash is None:
        feature_hash = canonical_feature_contract_hash(getattr(clf, "feature_columns", None))
    if not feature_hash:
        raise ValueError("model provenance missing feature_contract_hash")
    class_distribution = dict(getattr(clf, "class_distribution", None) or {})
    if not class_distribution:
        raise ValueError("model provenance missing class_distribution")
    label_params = dict(getattr(clf, "label_params", None) or {})
    if not label_params:
        raise ValueError("model provenance missing label_params")
    metrics = dict(getattr(clf, "metrics", None) or {})
    if not metrics:
        training_samples = getattr(clf, "training_samples", None)
        if training_samples is None:
            raise ValueError("model provenance missing metrics")
        metrics = {"training_samples": int(training_samples)}
    return {
        "pair": str(clf.pair),
        "timeframe": str(clf.timeframe),
        "train_start": str(clf.train_start),
        "train_end": str(clf.train_end),
        "feature_contract_hash": str(feature_hash),
        "label_params": label_params,
        "class_distribution": class_distribution,
        "metrics": metrics,
        "source_commit": getattr(clf, "source_commit", None) or _source_commit(),
    }


def metadata_path(path: str) -> Path:
    """Return the adjacent manifest path for an artifact."""
    return Path(f"{path}.metadata.json")


def artifact_sha256(path: str) -> str:
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as artifact:
            for chunk in iter(lambda: artifact.read(1024 * 1024), b""):
                digest.update(chunk)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"model artifact not found: {path}") from exc
    return digest.hexdigest()


def _source_commit() -> str:
    configured = os.environ.get("GIT_COMMIT") or os.environ.get("SOURCE_COMMIT")
    if configured:
        return configured
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        ).strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _validate_keys(metadata: dict[str, Any]) -> None:
    missing = [key for key in REQUIRED_KEYS if key not in metadata]
    if missing:
        raise ValueError(f"metadata missing required keys: {', '.join(missing)}")
    if not isinstance(metadata["label_params"], dict):
        raise ValueError("metadata label_params must be an object")
    if not isinstance(metadata["class_distribution"], dict):
        raise ValueError("metadata class_distribution must be an object")
    if not isinstance(metadata["metrics"], dict):
        raise ValueError("metadata metrics must be an object")


def write_artifact_with_metadata(path: str, artifact: bytes, metadata: dict) -> str:
    """Cut over a new artifact only after its immutable manifest is accepted."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    temp_artifact = target.with_name(f".{target.name}.{token}.tmp")
    temp_manifest = metadata_path(str(temp_artifact))
    try:
        temp_artifact.write_bytes(artifact)
        digest = write_metadata(str(temp_artifact), metadata)
        final_manifest = metadata_path(path)
        encoded = temp_manifest.read_bytes()
        if final_manifest.exists() and final_manifest.read_bytes() != encoded:
            raise FileExistsError(f"immutable metadata already exists: {final_manifest}")
        if target.exists() and not final_manifest.exists():
            raise FileExistsError(f"refusing to replace unmanifested artifact: {target}")
        os.replace(temp_artifact, target)
        os.replace(temp_manifest, final_manifest)
        return digest
    finally:
        temp_artifact.unlink(missing_ok=True)
        temp_manifest.unlink(missing_ok=True)


def _canonical_json(metadata: dict[str, Any]) -> bytes:
    return (
        json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        + "\n"
    ).encode("utf-8")


def write_metadata(path: str, metadata: dict) -> str:
    """Write an adjacent immutable manifest and return the artifact SHA-256.

    The digest is always calculated from the artifact currently on disk. A
    supplied digest must agree with it; it is never silently corrected.
    """
    actual_digest = artifact_sha256(path)
    payload = dict(metadata)
    if "source_commit" not in payload:
        payload["source_commit"] = _source_commit()
    if payload["source_commit"] == "unknown":
        log.warning("No source commit available for model artifact %s", path)
    supplied_digest = payload.get("artifact_sha256")
    if supplied_digest is not None and supplied_digest != actual_digest:
        raise ValueError(
            f"artifact checksum mismatch before manifest write: expected {supplied_digest}, "
            f"actual {actual_digest}"
        )
    payload["artifact_sha256"] = actual_digest
    _validate_keys(payload)

    target = metadata_path(path)
    encoded = _canonical_json(payload)
    if target.exists():
        if target.read_bytes() == encoded:
            return actual_digest
        raise FileExistsError(f"immutable metadata already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(target, "xb") as manifest:
            manifest.write(encoded)
    except FileExistsError:
        if target.read_bytes() != encoded:
            raise FileExistsError(f"immutable metadata already exists: {target}")
    verified_digest = artifact_sha256(path)
    if verified_digest != actual_digest:
        raise ValueError(
            f"artifact checksum changed while writing manifest: expected {actual_digest}, "
            f"actual {verified_digest}"
        )
    return actual_digest


def read_metadata(path: str) -> dict:
    """Read and validate a manifest, including the artifact checksum."""
    target = metadata_path(path)
    try:
        with target.open("r", encoding="utf-8") as manifest:
            metadata = json.load(manifest)
    except FileNotFoundError as exc:
        raise FileNotFoundError(f"model metadata not found: {target}") from exc
    except (json.JSONDecodeError, OSError) as exc:
        raise ValueError(f"invalid model metadata: {target}") from exc
    if not isinstance(metadata, dict):
        raise ValueError(f"invalid model metadata: {target}")
    _validate_keys(metadata)
    actual_digest = artifact_sha256(path)
    if metadata["artifact_sha256"] != actual_digest:
        raise ValueError(
            f"artifact checksum mismatch for {path}: manifest {metadata['artifact_sha256']}, "
            f"actual {actual_digest}"
        )
    return metadata
