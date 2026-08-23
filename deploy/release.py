"""Deterministic upgrade and rollback records used by installers."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

from .bundle import canonical_json

_IMAGE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class UpgradeAssessment:
    ready: bool
    rollback_allowed: bool
    errors: tuple[str, ...]
    gates: tuple[str, ...]


def assess_upgrade(current: Mapping[str, object], target: Mapping[str, object]) -> UpgradeAssessment:
    """Assess immutable artifacts, schema direction, and recovery evidence."""
    errors: list[str] = []
    for label, state in (("current", current), ("target", target)):
        try:
            _bundle_id(str(state["bundle_hash"]))
        except (KeyError, ValueError):
            errors.append(f"{label}.bundle_hash must be a lowercase SHA-256 digest")
        if not _IMAGE_DIGEST.fullmatch(str(state.get("image_digest", ""))):
            errors.append(f"{label}.image_digest must be an immutable sha256 digest")
        if not str(state.get("chart_version", "")).strip():
            errors.append(f"{label}.chart_version is required")
    if current.get("bundle_hash") == target.get("bundle_hash"):
        errors.append("target bundle must differ from current bundle")
    try:
        if isinstance(current.get("schema_version"), bool) or isinstance(target.get("schema_version"), bool):
            raise ValueError
        current_schema = int(current["schema_version"])
        target_schema = int(target["schema_version"])
        if current_schema < 0 or target_schema < 0:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        current_schema = target_schema = 0
        errors.append("schema_version values must be non-negative integers")
    if target_schema < current_schema:
        errors.append("schema downgrade is unsupported")
    schema_change = target_schema > current_schema
    if schema_change and not str(target.get("backup_reference", "")).strip():
        errors.append("schema-changing upgrade requires a backup reference")
    if schema_change and not str(target.get("restore_test_reference", "")).strip():
        errors.append("schema-changing upgrade requires tested restore evidence")
    backward_compatible = target.get("migration_backward_compatible") is True
    return UpgradeAssessment(
        ready=not errors,
        rollback_allowed=not errors and (not schema_change or backward_compatible),
        errors=tuple(errors),
        gates=("verify-artifacts", "preflight", "backup-evidence", "migrate", "rollout", "smoke", "promote"),
    )


def _bundle_id(value: str) -> str:
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError("bundle hash must be a lowercase SHA-256 digest")
    return value


def create_upgrade_manifest(current_bundle: str, target_bundle: str, *, database_migration: str = "required-before-rollout") -> dict:
    if current_bundle == target_bundle:
        raise ValueError("upgrade target must differ from current bundle")
    return {
        "format": "cmul8.upgrade.v1",
        "from_bundle": _bundle_id(current_bundle),
        "to_bundle": _bundle_id(target_bundle),
        "database_migration": database_migration,
        "phases": ["verify", "preflight", "migrate", "rollout", "smoke", "promote"],
        "rollback_on_smoke_failure": True,
    }


def create_rollback_manifest(current_bundle: str, previous_bundle: str, *, migration_compatible: bool) -> dict:
    if current_bundle == previous_bundle:
        raise ValueError("rollback target must differ from current bundle")
    return {
        "format": "cmul8.rollback.v1",
        "from_bundle": _bundle_id(current_bundle),
        "to_bundle": _bundle_id(previous_bundle),
        "migration_compatible": bool(migration_compatible),
        "requires_operator_approval": True,
        "phases": ["verify-target", "scale-workers", "rollout", "smoke", "record"],
    }


def write_release_manifest(path: str | Path, manifest: Mapping[str, object]) -> Path:
    destination = Path(path)
    destination.write_bytes(canonical_json(dict(manifest)))
    return destination


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a CMUL8 upgrade or rollback record")
    commands = parser.add_subparsers(dest="command", required=True)
    upgrade = commands.add_parser("upgrade")
    upgrade.add_argument("current_bundle")
    upgrade.add_argument("target_bundle")
    upgrade.add_argument("output")
    rollback = commands.add_parser("rollback")
    rollback.add_argument("current_bundle")
    rollback.add_argument("previous_bundle")
    rollback.add_argument("output")
    rollback.add_argument("--migration-compatible", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "upgrade":
        manifest = create_upgrade_manifest(args.current_bundle, args.target_bundle)
    else:
        manifest = create_rollback_manifest(
            args.current_bundle,
            args.previous_bundle,
            migration_compatible=args.migration_compatible,
        )
    write_release_manifest(args.output, manifest)
    print(json.dumps(manifest, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
