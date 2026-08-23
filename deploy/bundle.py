"""Deterministic, content-addressed Operational Bundle packaging.

The archive is deliberately target-neutral. Deployment mode, credentials, and
customer-specific endpoints are supplied by the installer environment, never
baked into a bundle.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import io
import json
import os
import re
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Mapping

MANIFEST_NAME = "operational-bundle.json"
BUNDLE_FORMAT = "cmul8.operational-bundle.v1"
DEPLOYMENT_MODES = ("cmul8_cloud", "dedicated_cloud", "private_cloud")
REQUIRED_REFERENCES = (
    "operation_graph",
    "app",
    "api",
    "worker",
    "runtime_agent",
    "migrations",
    "connectors",
    "permissions",
    "approval",
    "tests",
    "evals",
    "deployment",
)
_CREDENTIAL_CONTENT = re.compile(
    rb"(?i)(BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|[\"']?(?:password|passwd|secret|api[_-]?key|access[_-]?token)[\"']?\s*[:=]\s*[\"']?[^$<{\s\"'][^\r\n]{3,})"
)


class BundleError(ValueError):
    """Bundle construction or verification failed."""


@dataclass(frozen=True)
class VerificationResult:
    bundle_hash: str
    manifest: dict
    file_count: int
    signature_verified: bool


def canonical_json(value: object) -> bytes:
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n").encode()


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if not value or path.is_absolute() or ".." in path.parts or "." in path.parts:
        raise BundleError(f"unsafe bundle path: {value!r}")
    if "\\" in value or value.startswith("/"):
        raise BundleError(f"unsafe bundle path: {value!r}")
    return path


def _credential_check(path: str, content: bytes) -> None:
    suspicious_names = {"secret", "secrets", "password", "passwd", "token", "credential", "credentials", "private-key", "private_key"}
    parts = [part.lower() for part in PurePosixPath(path).parts]
    name_is_suspicious = any(
        part == ".env"
        or part.startswith(".env.")
        or Path(part).stem in suspicious_names
        for part in parts
    )
    if name_is_suspicious or _CREDENTIAL_CONTENT.search(content):
        raise BundleError(f"potential credential material is forbidden in bundle: {path}")


class OperationalBundleBuilder:
    """Build an immutable archive from an explicit map of logical references."""

    def __init__(
        self,
        source_root: str | os.PathLike[str],
        references: Mapping[str, str],
        *,
        release: str,
        sbom: Mapping[str, object] | None = None,
        provenance: Mapping[str, object] | None = None,
    ) -> None:
        self.source_root = Path(source_root).resolve()
        self.references = dict(references)
        self.release = release
        self.sbom = dict(sbom or {"format": "SPDX-2.3", "document": None})
        self.provenance = dict(provenance or {"predicate_type": "https://slsa.dev/provenance/v1", "builder": "unknown"})
        missing = sorted(set(REQUIRED_REFERENCES) - self.references.keys())
        extra = sorted(set(self.references) - set(REQUIRED_REFERENCES))
        if missing or extra:
            raise BundleError(f"references must match contract; missing={missing}, extra={extra}")
        if not release or any(c.isspace() for c in release):
            raise BundleError("release must be a non-empty identifier without whitespace")

    def _collect(self) -> tuple[dict[str, bytes], dict[str, str]]:
        payload: dict[str, bytes] = {}
        resolved_refs: dict[str, str] = {}
        for logical_name in REQUIRED_REFERENCES:
            relative = _safe_relative(self.references[logical_name])
            unresolved = self.source_root / Path(*relative.parts)
            cursor = self.source_root
            for part in relative.parts:
                cursor = cursor / part
                if cursor.is_symlink():
                    raise BundleError(f"symlinks are forbidden: {relative}")
            source = unresolved.resolve()
            try:
                source.relative_to(self.source_root)
            except ValueError as exc:
                raise BundleError(f"reference escapes source root: {relative}") from exc
            archive_root = f"payload/{relative.as_posix()}"
            if source.is_file():
                candidates = (source,)
            elif source.is_dir():
                candidates = tuple(sorted(source.rglob("*")))
                if not candidates:
                    raise BundleError(f"referenced directory is empty: {relative}")
            else:
                raise BundleError(f"reference is not a regular file: {relative}")
            files_added = 0
            for candidate in candidates:
                if candidate.is_symlink():
                    raise BundleError(f"symlinks are forbidden: {candidate.relative_to(self.source_root)}")
                if candidate.is_dir():
                    continue
                if not candidate.is_file():
                    raise BundleError(f"special files are forbidden: {candidate.relative_to(self.source_root)}")
                suffix = candidate.relative_to(source).as_posix() if source.is_dir() else ""
                archive_path = f"{archive_root}/{suffix}" if suffix else archive_root
                data = candidate.read_bytes()
                _credential_check(archive_path, data)
                payload[archive_path] = data
                files_added += 1
            if not files_added:
                raise BundleError(f"referenced directory has no regular files: {relative}")
            resolved_refs[logical_name] = archive_root
        return payload, resolved_refs

    def build(
        self,
        output_dir: str | os.PathLike[str],
        *,
        signer: Callable[[bytes], bytes] | None = None,
    ) -> Path:
        payload, resolved_refs = self._collect()
        files = {name: {"sha256": sha256(data), "size": len(data)} for name, data in sorted(payload.items())}
        source_digest = sha256(canonical_json({name: meta["sha256"] for name, meta in files.items()}))
        manifest: dict[str, object] = {
            "format": BUNDLE_FORMAT,
            "release": self.release,
            "deployment_modes": list(DEPLOYMENT_MODES),
            "references": resolved_refs,
            "files": files,
            "source_hash": source_digest,
            "artifact_hash": source_digest,
            "sbom": self.sbom,
            "provenance": self.provenance,
        }
        unsigned = canonical_json(manifest)
        if signer is not None:
            signature = signer(unsigned)
            if not isinstance(signature, bytes):
                raise BundleError("signer must return bytes")
            manifest["signature"] = {"scheme": "injected", "value": signature.hex()}
        manifest_bytes = canonical_json(manifest)
        _credential_check(MANIFEST_NAME, manifest_bytes)
        entries = {MANIFEST_NAME: manifest_bytes, **payload}
        archive = _deterministic_tar(entries)
        digest = sha256(archive)
        output = Path(output_dir)
        output.mkdir(parents=True, exist_ok=True)
        destination = output / f"cmul8-operational-bundle-{digest}.tar"
        if destination.exists() and destination.read_bytes() != archive:
            raise BundleError(f"refusing to replace non-matching immutable bundle: {destination}")
        if not destination.exists():
            fd, temporary = tempfile.mkstemp(prefix=".bundle-", dir=output)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write(archive)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return destination


def _deterministic_tar(entries: Mapping[str, bytes]) -> bytes:
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w", format=tarfile.PAX_FORMAT) as archive:
        for name, data in sorted(entries.items()):
            _safe_relative(name)
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = 0o644
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(data))
    return stream.getvalue()


def build_bundle(source_root: str | os.PathLike[str], references: Mapping[str, str], output_dir: str | os.PathLike[str], *, release: str, signer: Callable[[bytes], bytes] | None = None, sbom: Mapping[str, object] | None = None, provenance: Mapping[str, object] | None = None) -> Path:
    return OperationalBundleBuilder(source_root, references, release=release, sbom=sbom, provenance=provenance).build(output_dir, signer=signer)


def verify_bundle(
    bundle_path: str | os.PathLike[str],
    *,
    verifier: Callable[[bytes, bytes], bool] | None = None,
    expected_hash: str | None = None,
    require_signature: bool = False,
) -> VerificationResult:
    path = Path(bundle_path)
    archive_bytes = path.read_bytes()
    digest = sha256(archive_bytes)
    if expected_hash is not None and not hmac.compare_digest(digest, expected_hash):
        raise BundleError("bundle archive hash mismatch")
    if path.name.startswith("cmul8-operational-bundle-"):
        named_hash = path.stem.removeprefix("cmul8-operational-bundle-")
        if len(named_hash) == 64 and not hmac.compare_digest(named_hash, digest):
            raise BundleError("content address in filename does not match archive")
    members: dict[str, bytes] = {}
    try:
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:") as archive:
            for member in archive:
                _safe_relative(member.name)
                if not member.isfile() or member.issym() or member.islnk():
                    raise BundleError(f"only regular files are allowed: {member.name}")
                if member.name in members:
                    raise BundleError(f"duplicate archive member: {member.name}")
                extracted = archive.extractfile(member)
                if extracted is None:
                    raise BundleError(f"unreadable archive member: {member.name}")
                members[member.name] = extracted.read()
    except (tarfile.TarError, OSError) as exc:
        raise BundleError("invalid bundle archive") from exc
    if MANIFEST_NAME not in members:
        raise BundleError(f"missing {MANIFEST_NAME}")
    manifest_bytes = members.pop(MANIFEST_NAME)
    _credential_check(MANIFEST_NAME, manifest_bytes)
    try:
        manifest = json.loads(manifest_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise BundleError("invalid bundle manifest") from exc
    if manifest.get("format") != BUNDLE_FORMAT or manifest.get("deployment_modes") != list(DEPLOYMENT_MODES):
        raise BundleError("unsupported or target-specific bundle manifest")
    declared = manifest.get("files")
    references = manifest.get("references")
    if not isinstance(declared, dict) or not isinstance(references, dict) or set(references) != set(REQUIRED_REFERENCES):
        raise BundleError("invalid references/files contract")
    if set(declared) != set(members):
        raise BundleError("archive members do not exactly match manifest")
    if any(
        not isinstance(value, str)
        or (value not in declared and not any(name.startswith(value + "/") for name in declared))
        for value in references.values()
    ):
        raise BundleError("every logical reference must identify a declared file or directory")
    for name, data in members.items():
        _credential_check(name, data)
        metadata = declared.get(name)
        if not isinstance(metadata, dict) or metadata.get("sha256") != sha256(data) or metadata.get("size") != len(data):
            raise BundleError(f"file integrity check failed: {name}")
    calculated_artifact_hash = sha256(canonical_json({name: declared[name]["sha256"] for name in sorted(declared)}))
    if manifest.get("source_hash") != calculated_artifact_hash or manifest.get("artifact_hash") != calculated_artifact_hash:
        raise BundleError("manifest source/artifact hash verification failed")
    if not isinstance(manifest.get("sbom"), dict) or not isinstance(manifest.get("provenance"), dict):
        raise BundleError("manifest SBOM and provenance metadata are required")
    signature_verified = False
    signature = manifest.get("signature")
    if signature is None and require_signature:
        raise BundleError("release policy requires a signed bundle")
    if signature is not None:
        if verifier is None:
            raise BundleError("signed bundle requires an injected verifier")
        if not isinstance(signature, dict) or signature.get("scheme") != "injected":
            raise BundleError("unsupported signature scheme")
        try:
            value = bytes.fromhex(signature["value"])
        except (KeyError, TypeError, ValueError) as exc:
            raise BundleError("invalid signature envelope") from exc
        unsigned = dict(manifest)
        del unsigned["signature"]
        if not verifier(canonical_json(unsigned), value):
            raise BundleError("signature verification failed")
        signature_verified = True
    return VerificationResult(digest, manifest, len(members), signature_verified)


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build or verify CMUL8 operational bundles")
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build")
    build.add_argument("source")
    build.add_argument("references", help="JSON file mapping required logical references to source paths")
    build.add_argument("output")
    build.add_argument("--release", required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("bundle")
    verify.add_argument("--expected-hash")
    args = parser.parse_args(argv)
    if args.command == "build":
        refs = json.loads(Path(args.references).read_text())
        print(build_bundle(args.source, refs, args.output, release=args.release))
    else:
        result = verify_bundle(args.bundle, expected_hash=args.expected_hash)
        print(json.dumps({"bundle_hash": result.bundle_hash, "files": result.file_count}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
