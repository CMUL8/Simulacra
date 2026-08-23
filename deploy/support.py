"""Create deterministic, redacted diagnostic archives for support."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable, Mapping

from .bundle import BundleError, _deterministic_tar, _safe_relative, canonical_json, sha256
from .environment import redacted_environment

_AUTHORIZATION = re.compile(r"(?im)(?P<prefix>\bauthorization\s*[=:]\s*)[^\r\n]*")
_QUOTED_PAIR = re.compile(
    r'''(?P<prefix>["'](?P<key>[^"']+)["']\s*:\s*)'''
    r'''(?P<value>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^,}\r\n]+)'''
)
_ASSIGNMENT = re.compile(
    r'''(?im)(?P<prefix>(?P<key>[A-Za-z][A-Za-z0-9_.-]*)\s*[=:]\s*)'''
    r'''(?P<value>"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;\r\n]+)'''
)
_SECRET_SUFFIXES = ("password", "passwd", "token", "secret", "apikey", "authorization", "privatekey")


def _is_secret_key(key: object) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
    return any(normalized.endswith(suffix) for suffix in _SECRET_SUFFIXES)


def _redact_json(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if _is_secret_key(key) else _redact_json(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_json(item) for item in value]
    if isinstance(value, str):
        return _redact_text(value)
    return value


def _replace_pair(match: re.Match[str]) -> str:
    if not _is_secret_key(match.group("key")):
        return match.group(0)
    return match.group("prefix") + '"[REDACTED]"'


def _replace_assignment(match: re.Match[str]) -> str:
    if not _is_secret_key(match.group("key")):
        return match.group(0)
    return match.group("prefix") + "[REDACTED]"


def _redact_text(text: str) -> str:
    text = _AUTHORIZATION.sub(lambda match: match.group("prefix") + "[REDACTED]", text)
    text = _QUOTED_PAIR.sub(_replace_pair, text)
    return _ASSIGNMENT.sub(_replace_assignment, text)


def _redact(data: bytes) -> bytes:
    if b"\x00" in data:
        raise BundleError("binary diagnostics are forbidden in support bundles")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BundleError("diagnostics must be UTF-8 text") from exc
    try:
        structured = json.loads(text)
    except json.JSONDecodeError:
        structured = None
    if structured is not None:
        return canonical_json(_redact_json(structured))
    return _redact_text(text).encode("utf-8")


def create_support_bundle(
    output_dir: str | Path,
    *,
    environment: Mapping[str, str],
    diagnostics: Mapping[str, bytes | str],
) -> Path:
    entries: dict[str, bytes] = {"environment.json": canonical_json(redacted_environment(environment))}
    for name, value in sorted(diagnostics.items()):
        relative = _safe_relative(name)
        unsafe_name = any(
            any(marker in part.lower() for marker in ("credential", "private-key", ".env", "secret"))
            or _is_secret_key(Path(part).stem)
            for part in relative.parts
        )
        if unsafe_name:
            raise BundleError(f"secret-bearing diagnostic filename is forbidden: {name}")
        data = value.encode() if isinstance(value, str) else value
        entries[f"diagnostics/{relative.as_posix()}"] = _redact(data)
    archive = _deterministic_tar(entries)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    destination = output / f"cmul8-support-{sha256(archive)}.tar"
    destination.write_bytes(archive)
    return destination


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create a redacted CMUL8 support bundle")
    parser.add_argument("environment", help="JSON environment object")
    parser.add_argument("output")
    parser.add_argument("diagnostics", nargs="*", help="diagnostic text files")
    args = parser.parse_args(argv)
    environment = json.loads(Path(args.environment).read_text())
    diagnostics = {Path(value).name: Path(value).read_bytes() for value in args.diagnostics}
    print(create_support_bundle(args.output, environment=environment, diagnostics=diagnostics))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
