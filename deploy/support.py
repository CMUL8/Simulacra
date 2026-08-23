"""Create deterministic, redacted diagnostic archives for support."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Iterable, Mapping

from .bundle import BundleError, _deterministic_tar, _safe_relative, canonical_json, sha256
from .environment import redacted_environment

_SECRET = re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key|authorization)(\s*[=:]\s*)([^\s,;\"']+)")


def _redact(data: bytes) -> bytes:
    text = data.decode("utf-8", errors="replace")
    return _SECRET.sub(lambda match: match.group(1) + match.group(2) + "[REDACTED]", text).encode()


def create_support_bundle(
    output_dir: str | Path,
    *,
    environment: Mapping[str, str],
    diagnostics: Mapping[str, bytes | str],
) -> Path:
    entries: dict[str, bytes] = {"environment.json": canonical_json(redacted_environment(environment))}
    for name, value in sorted(diagnostics.items()):
        relative = _safe_relative(name)
        if any(marker in name.lower() for marker in ("credential", "private-key", ".env", "secret")):
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
