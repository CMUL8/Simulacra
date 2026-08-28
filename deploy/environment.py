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
    }
    if environment.get("CMUL8_OBJECT_STORAGE_URL"):
        schemes["CMUL8_OBJECT_STORAGE_URL"] = {"s3", "gs", "az", "https"}
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
    if environment.get("CMUL8_SECRET_PROVIDER") in {"env", "compose_env"}:
        warnings.append(
            "Compose environment secrets are acceptable for V0; "
            "use Docker secrets or an external provider for production"
        )
    if environment.get("CMUL8_TLS_REQUIRED", "true").lower() not in {"true", "1", "yes"}:
        warnings.append("TLS is not marked required")
    preview_enabled = environment.get("CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1", "").lower() in {"1", "true", "yes"}
    control_origin = environment.get("CONTROL_ORIGIN") or environment.get("CMUL8_CONTROL_ORIGIN")
    preview_origin = environment.get("PREVIEW_ORIGIN") or environment.get("CMUL8_PREVIEW_ORIGIN")
    preview_secret = environment.get("CMUL8_PREVIEW_EXCHANGE_SECRET") or environment.get("SIMULACRA_PREVIEW_EXCHANGE_SECRET")
    if preview_enabled or control_origin or preview_origin:
        control = _safe_origin(control_origin)
        preview = _safe_origin(preview_origin)
        if control is None or preview is None:
            errors.append("preview origin requires exact control and preview HTTPS/HTTP origins")
        elif control.scheme != "https" or preview.scheme != "https":
            errors.append("preview origin requires HTTPS on both control and preview hosts")
        elif control.hostname == preview.hostname or not _same_site(
            control.hostname, preview.hostname,
            environment.get("PREVIEW_REGISTRABLE_DOMAIN") or environment.get("CMUL8_PREVIEW_REGISTRABLE_DOMAIN"),
        ):
            errors.append("preview origin must use a same-site distinct hostname from control")
        if preview_enabled and not preview_secret:
            errors.append("CMUL8_PREVIEW_EXCHANGE_SECRET is required when preview origin is enabled")
    return PreflightResult(not errors, tuple(errors), tuple(warnings), tuple(REQUIRED))


def _safe_origin(value: str | None):
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlparse(value.strip())
        # Accessing ``port`` is deliberate: urllib otherwise accepts a malformed
        # port in a URL that cannot be an exact browser origin.
        _ = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"} or not parsed.hostname
        or parsed.username or parsed.password or parsed.path not in {"", "/"}
        or parsed.query or parsed.fragment
    ):
        return None
    return parsed


def _same_site(first: str | None, second: str | None, registrable_domain: str | None) -> str | None:
    if not first or not second or not isinstance(registrable_domain, str):
        return None
    domain = registrable_domain.lower().strip(".")
    labels = domain.split(".")
    if len(labels) < 2 or any(not label or not label.replace("-", "").isalnum() for label in labels):
        return None
    return domain if first.lower().endswith(f".{domain}") and second.lower().endswith(f".{domain}") and first != second else None


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
