"""Deterministic upgrade and rollback records used by installers."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Iterable, Mapping

from .bundle import canonical_json


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
