"""Verify-first installer for an Operational Bundle."""

from __future__ import annotations

import argparse
import io
import os
import tarfile
from pathlib import Path
from typing import Callable, Iterable

from .bundle import BundleError, _safe_relative, sha256, verify_bundle


def install_bundle(
    bundle_path: str | os.PathLike[str],
    destination: str | os.PathLike[str],
    *,
    expected_hash: str | None = None,
    verifier: Callable[[bytes, bytes], bool] | None = None,
    require_signature: bool = False,
) -> Path:
    """Install only a verified archive into an empty release directory."""
    result = verify_bundle(
        bundle_path,
        expected_hash=expected_hash,
        verifier=verifier,
        require_signature=require_signature,
    )
    archive_bytes = Path(bundle_path).read_bytes()
    if sha256(archive_bytes) != result.bundle_hash:
        raise BundleError("bundle changed after verification")
    root = Path(destination)
    release = root / result.bundle_hash
    if release.exists():
        raise BundleError(f"immutable release already exists: {release}")
    release.mkdir(parents=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            for member in archive:
                relative = _safe_relative(member.name)
                target = release.joinpath(*relative.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = archive.extractfile(member)
                if stream is None:
                    raise BundleError(f"unreadable archive member: {member.name}")
                target.write_bytes(stream.read())
                target.chmod(0o640)
        (release / ".verified").write_text(result.bundle_hash + "\n")
    except Exception:
        # Do not recursively remove an unknown path. Only unlink files created
        # beneath the newly-created, content-addressed release directory.
        for candidate in sorted(release.rglob("*"), reverse=True):
            if candidate.is_file():
                candidate.unlink()
            elif candidate.is_dir():
                candidate.rmdir()
        release.rmdir()
        raise
    return release


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify and install an immutable CMUL8 bundle")
    parser.add_argument("bundle")
    parser.add_argument("destination")
    parser.add_argument("--expected-hash")
    args = parser.parse_args(argv)
    print(install_bundle(args.bundle, args.destination, expected_hash=args.expected_hash))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
