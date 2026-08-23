"""Private-runtime environment contract and preflight validation."""

from __future__ import annotations

import argparse
import json
import re
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping
from urllib.parse import urlparse

MODES = {"cmul8_cloud", "dedicated_cloud", "private_cloud"}
REQUIRED = (
    "CMUL8_DEPLOYMENT_MODE",
    "CMUL8_TENANT_ID",
    "CMUL8_ENVIRONMENT",
    "CMUL8_POSTGRES_URL",
    "CMUL8_REDIS_URL",
    "CMUL8_OBJECT_STORAGE_URL",
    "CMUL8_SECRET_PROVIDER",
    "CMUL8_IMAGE_REGISTRY",
)
SECRET_KEYS = {"CMUL8_POSTGRES_URL", "CMUL8_REDIS_URL"}
_IDENTIFIER = re.compile(r"^[a-z][a-z0-9_-]{1,62}$")


@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    checked: tuple[str, ...]


def validate_environment(environment: Mapping[str, str], *, resolve_hosts: bool = False) -> PreflightResult:
    errors: list[str] = []
    warnings: list[str] = []
    for key in REQUIRED:
        if not environment.get(key, "").strip():
            errors.append(f"{key} is required")
    mode = environment.get("CMUL8_DEPLOYMENT_MODE")
    if mode and mode not in MODES:
        errors.append(f"CMUL8_DEPLOYMENT_MODE must be one of {sorted(MODES)}")
    for key in ("CMUL8_TENANT_ID", "CMUL8_ENVIRONMENT"):
        value = environment.get(key)
        if value and not _IDENTIFIER.fullmatch(value):
            errors.append(f"{key} must be a lowercase deployment identifier")
    schemes = {
        "CMUL8_POSTGRES_URL": {"postgres", "postgresql"},
        "CMUL8_REDIS_URL": {"redis", "rediss"},
        "CMUL8_OBJECT_STORAGE_URL": {"s3", "gs", "az", "https"},
    }
    for key, allowed in schemes.items():
        value = environment.get(key)
        if not value:
            continue
        parsed = urlparse(value)
        if parsed.scheme not in allowed or not (parsed.hostname or parsed.netloc):
            errors.append(f"{key} must use one of {sorted(allowed)} and include a host/bucket")
        if key == "CMUL8_OBJECT_STORAGE_URL" and (parsed.username or parsed.password):
            errors.append("CMUL8_OBJECT_STORAGE_URL must not embed credentials")
        if resolve_hosts and parsed.hostname:
            try:
                socket.getaddrinfo(parsed.hostname, parsed.port)
            except OSError:
                errors.append(f"{key} host does not resolve")
    if environment.get("CMUL8_SECRET_PROVIDER") == "env":
        warnings.append("environment-backed secrets are for development only; use an external secret provider")
    if environment.get("CMUL8_TLS_REQUIRED", "true").lower() not in {"true", "1", "yes"}:
        warnings.append("TLS is not marked required")
    return PreflightResult(not errors, tuple(errors), tuple(warnings), tuple(REQUIRED))


def redacted_environment(environment: Mapping[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in environment.items():
        upper = key.upper()
        if key in SECRET_KEYS or any(marker in upper for marker in ("PASSWORD", "TOKEN", "SECRET", "KEY")):
            result[key] = "[REDACTED]"
        elif key.startswith("CMUL8_"):
            result[key] = value
    return result


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate the CMUL8 private-runtime environment contract")
    parser.add_argument("environment", help="JSON object file")
    parser.add_argument("--resolve-hosts", action="store_true")
    args = parser.parse_args(argv)
    environment = json.loads(Path(args.environment).read_text())
    result = validate_environment(environment, resolve_hosts=args.resolve_hosts)
    print(json.dumps({"ok": result.ok, "errors": result.errors, "warnings": result.warnings}, sort_keys=True))
    return 0 if result.ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
