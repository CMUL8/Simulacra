"""Immutable, actor-scoped source staging for recoverable Mission bootstrap."""

from __future__ import annotations

import hashlib
import fcntl
import json
import os
import re
import stat
import tempfile
import uuid
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable
from contextlib import contextmanager

from simulacra.collaboration.models import validate_scope_id
from simulacra.demo.paths import RUNS_DIR
from simulacra.demo.sources import safe_source_name

_REQUEST_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_request(value: str) -> str:
    if not isinstance(value, str) or not _REQUEST_RE.fullmatch(value):
        raise ValueError("invalid client request id")
    return value


def _fsync_dir(directory: Path) -> None:
    """Durably publish an earlier replacement.

    A record must never report success until the directory entry that names it
    is durable.  Callers deliberately let this error escape: retrying an
    unpublished stage is safe, but pretending it completed is not.
    """
    try:
        mode = os.lstat(directory).st_mode
    except OSError as exc:
        raise ValueError("staged source unavailable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ValueError("staged source unavailable")
    fd = os.open(directory, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise ValueError("staged source unavailable")
        os.fsync(fd)
    finally:
        os.close(fd)


def _atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":")))
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path); _fsync_dir(path.parent)
    finally:
        if os.path.exists(temp): os.unlink(temp)


@dataclass(frozen=True)
class StagedSource:
    source_ref: str
    tenant_id: str
    authenticated_human_actor_id: str
    operation: str
    client_request_id: str
    canonical_content_sha256: str
    normalized_filename: str
    media_type: str
    blob_ref: str
    state: str
    created_at: str

    @classmethod
    def from_dict(cls, value: dict) -> "StagedSource":
        return cls(**{key: value[key] for key in cls.__dataclass_fields__})

    def public(self) -> dict[str, str]:
        return {"source_ref": self.source_ref, "sha256": self.canonical_content_sha256,
                "filename": self.normalized_filename, "media_type": self.media_type}


class SourceStaging:
    """Blob-first staging. Records are the only public publication boundary."""
    def __init__(self, root: str | Path | None = None) -> None:
        # Resolving a supplied root would silently follow a malicious control
        # volume link before we have a chance to reject it.
        self.root = Path(root or RUNS_DIR / ".workplace-control" / "source-staging").absolute()
        self._assert_absolute_ancestors(self.root)
        self._mkdir(self.root)

    @staticmethod
    def _assert_absolute_ancestors(path: Path) -> None:
        """Reject a linked control root before any mkdir/open can follow it.

        The staging root is a security boundary.  Checking only descendants of
        ``self.root`` is insufficient because an attacker can replace the root
        (or an ancestor supplied by a deployment) with a link to another
        tenant's volume.  We inspect the absolute chain with lstat first and
        then use O_NOFOLLOW on opened leaves below.
        """
        absolute = path.absolute()
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current = current / part
            try:
                mode = os.lstat(current).st_mode
            except FileNotFoundError:
                return
            except OSError as exc:
                raise ValueError("staged source unavailable") from exc
            if stat.S_ISLNK(mode):
                raise ValueError("staged source unavailable")
            if current != absolute and not stat.S_ISDIR(mode):
                raise ValueError("staged source unavailable")

    def _assert_safe(self, path: Path, *, require_file: bool = False, require_dir: bool = False) -> None:
        """Reject symlinked control roots, ancestors, and leaves."""
        self._assert_absolute_ancestors(self.root)
        try:
            relative = path.absolute().relative_to(self.root)
        except ValueError as exc:
            raise ValueError("staged source unavailable") from exc
        current = self.root
        for part in (".", *relative.parts):
            if part != ".":
                current = current / part
            try:
                mode = os.lstat(current).st_mode
            except FileNotFoundError:
                break
            except OSError as exc:
                raise ValueError("staged source unavailable") from exc
            if stat.S_ISLNK(mode):
                raise ValueError("staged source unavailable")
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            if require_file or require_dir:
                raise ValueError("staged source unavailable")
            return
        if stat.S_ISLNK(mode) or (require_file and not stat.S_ISREG(mode)) or (require_dir and not stat.S_ISDIR(mode)):
            raise ValueError("staged source unavailable")

    def _mkdir(self, path: Path) -> None:
        """Create only checked directories within this owned control tree."""
        if path == self.root:
            if path.exists() or path.is_symlink():
                self._assert_safe(path, require_dir=True)
                return
            # ``parents=True`` would follow a link in a deployment-supplied
            # ancestor.  Create one checked component at a time instead.
            absolute = path.absolute()
            current = Path(absolute.anchor)
            for part in absolute.parts[1:]:
                current = current / part
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
                self._assert_absolute_ancestors(current)
                mode = os.lstat(current).st_mode
                if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                    raise ValueError("staged source unavailable")
            self._assert_safe(path, require_dir=True)
            return
        self._assert_safe(self.root, require_dir=True)
        relative = path.absolute().relative_to(self.root)
        current = self.root
        for part in relative.parts:
            current = current / part
            try:
                current.mkdir()
            except FileExistsError:
                pass
            self._assert_safe(current, require_dir=True)

    def _read_regular(self, path: Path) -> bytes:
        self._assert_safe(path, require_file=True)
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("staged source unavailable")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(fd, 1 << 20)
                if not chunk:
                    return b"".join(chunks)
                chunks.append(chunk)
        finally:
            os.close(fd)

    def _scope(self, tenant_id: str, actor_id: str) -> Path:
        validate_scope_id(tenant_id, "tenant_id"); validate_scope_id(actor_id, "actor_id")
        return self.root / tenant_id / actor_id / "workspace_source_stage"

    def _record_path(self, tenant_id: str, actor_id: str, request_id: str) -> Path:
        return self._scope(tenant_id, actor_id) / f"{_safe_request(request_id)}.json"

    @contextmanager
    def _locked(self, tenant_id: str, actor_id: str):
        scope = self._scope(tenant_id, actor_id); self._mkdir(scope)
        lock = scope / ".stage.lock"
        self._assert_safe(lock)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("staged source unavailable")
            handle = os.fdopen(fd, "a+b")
        except Exception:
            os.close(fd)
            raise
        with handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @contextmanager
    def _publication_locked(self):
        """Coordinate record publication and orphan collection globally."""
        lock = self.root / ".publication.lock"
        self._assert_safe(lock)
        fd = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                raise ValueError("staged source unavailable")
            handle = os.fdopen(fd, "a+b")
        except Exception:
            os.close(fd)
            raise
        with handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def _blob_path(self, digest: str) -> Path:
        if not re.fullmatch(r"[0-9a-f]{64}", digest): raise ValueError("invalid source hash")
        return self.root / "blobs" / digest[:2] / digest

    def _read_record(self, path: Path, *, tenant_id: str, actor_id: str, request_id: str) -> StagedSource:
        """Accept only a record whose path and immutable identity agree."""
        _safe_request(request_id)
        raw = json.loads(self._read_regular(path).decode("utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("staged source unavailable")
        try:
            record = StagedSource.from_dict(raw)
        except (KeyError, TypeError) as exc:
            raise ValueError("staged source unavailable") from exc
        if (
            record.tenant_id != tenant_id
            or record.authenticated_human_actor_id != actor_id
            or record.operation != "workspace_source_stage"
            or record.client_request_id != request_id
            or not re.fullmatch(r"src_[0-9a-f]{32}", record.source_ref)
            or not re.fullmatch(r"[0-9a-f]{64}", record.canonical_content_sha256)
            or record.blob_ref != f"blob:{record.canonical_content_sha256}"
            or record.state != "staged"
            or not isinstance(record.created_at, str) or not record.created_at
            or not isinstance(record.media_type, str) or not record.media_type.strip()
        ):
            raise ValueError("staged source unavailable")
        try:
            if safe_source_name(record.normalized_filename) != record.normalized_filename:
                raise ValueError("staged source unavailable")
        except Exception as exc:
            raise ValueError("staged source unavailable") from exc
        return record

    def stage(self, *, tenant_id: str, actor_id: str, client_request_id: str,
              filename: str, media_type: str, data: bytes) -> StagedSource:
        name = safe_source_name(filename)
        if not data: raise ValueError("source is empty")
        digest = _hash(data)
        request_id = _safe_request(client_request_id)
        record_path = self._record_path(tenant_id, actor_id, request_id)
        # Hold the publication barrier until the blob and its immutable record
        # are both durable; GC uses the same barrier before unlinking blobs.
        with self._publication_locked(), self._locked(tenant_id, actor_id):
            if record_path.exists():
                # A previous process can have replaced this record immediately
                # before losing its directory-sync acknowledgement.  Rebuild
                # that durability barrier before treating it as a replay.
                _fsync_dir(record_path.parent)
                existing = self._read_record(record_path, tenant_id=tenant_id, actor_id=actor_id, request_id=request_id)
                _fsync_dir(self._blob_path(existing.canonical_content_sha256).parent)
                if _hash(self._read_regular(self._blob_path(existing.canonical_content_sha256))) != existing.canonical_content_sha256:
                    raise ValueError("source staging verification failed")
                if (existing.canonical_content_sha256, existing.normalized_filename, existing.media_type) != (digest, name, media_type or "application/octet-stream"):
                    raise ValueError("idempotency_mismatch")
                return existing
            blob = self._blob_path(digest); self._mkdir(blob.parent)
            if not blob.exists():
                fd, temp = tempfile.mkstemp(prefix=f".{digest}.", dir=blob.parent)
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(data); stream.flush(); os.fsync(stream.fileno())
                    # Immutable content address: linking ensures an existing blob is never overwritten.
                    try: os.link(temp, blob)
                    except FileExistsError: pass
                    _fsync_dir(blob.parent)
                finally:
                    if os.path.exists(temp): os.unlink(temp)
            # A retry can discover a blob left by a failed post-link fsync.
            _fsync_dir(blob.parent)
            if _hash(self._read_regular(blob)) != digest:
                raise ValueError("source staging verification failed")
            record = StagedSource(
            source_ref=f"src_{uuid.uuid4().hex}", tenant_id=tenant_id,
            authenticated_human_actor_id=actor_id, operation="workspace_source_stage",
            client_request_id=request_id, canonical_content_sha256=digest,
            normalized_filename=name, media_type=media_type or "application/octet-stream",
            blob_ref=f"blob:{digest}", state="staged", created_at=_now(),
            )
            _atomic_json(record_path, asdict(record))
            return record

    def get(self, *, tenant_id: str, actor_id: str, source_ref: str) -> StagedSource:
        # Source resolution is a publication operation: share the same lock as
        # GC so a returned source can never point at a blob collected halfway
        # through verification.
        with self._publication_locked():
            scope = self._scope(tenant_id, actor_id)
            try:
                self._assert_safe(scope, require_dir=True)
            except ValueError:
                raise KeyError("staged source unavailable")
            _fsync_dir(scope)
            for path in scope.glob("*.json"):
                try:
                    self._assert_safe(path, require_file=True)
                    record = self._read_record(path, tenant_id=tenant_id, actor_id=actor_id, request_id=path.stem)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if record.source_ref == source_ref:
                    try:
                        blob = self._blob_path(record.canonical_content_sha256)
                        _fsync_dir(blob.parent)
                        if _hash(self._read_regular(blob)) != record.canonical_content_sha256:
                            raise ValueError("staged source unavailable")
                        return record
                    except (OSError, ValueError):
                        # The public lookup boundary intentionally does not
                        # distinguish a missing, tampered, or linked blob.
                        raise KeyError("staged source unavailable") from None
        raise KeyError("staged source unavailable")

    def resolve_many(self, *, tenant_id: str, actor_id: str, source_refs: Iterable[str]) -> list[StagedSource]:
        refs = list(source_refs)
        if len(refs) != len(set(refs)): raise ValueError("duplicate staged source reference")
        return [self.get(tenant_id=tenant_id, actor_id=actor_id, source_ref=item) for item in refs]

    def blob_bytes(self, record: StagedSource) -> bytes:
        # Keep the read and its existence/hash boundary inside the shared
        # publication barrier.  Callers receive bytes, not a path that can go
        # stale after the lock is released.
        with self._publication_locked():
            path = self._blob_path(record.canonical_content_sha256)
            data = self._read_regular(path)
            if _hash(data) != record.canonical_content_sha256:
                raise ValueError("staged source unavailable")
            return data

    def gc_orphans(self, *, limit: int = 100, protected_hashes: Iterable[str] = ()) -> int:
        """Remove only blobs that no published stage or in-flight journal uses."""
        # If any persisted descriptor is malformed, do not guess which blobs
        # it referenced.  Retention is safer than deleting an active source.
        with self._publication_locked():
            referenced = set(protected_hashes)
            try:
                self._assert_safe(self.root, require_dir=True)
                for tenant in self.root.iterdir():
                    if tenant.name in {"blobs", ".publication.lock"}:
                        continue
                    self._assert_safe(tenant, require_dir=True)
                    for actor in tenant.iterdir():
                        self._assert_safe(actor, require_dir=True)
                        scope = actor / "workspace_source_stage"
                        if not scope.exists():
                            continue
                        self._assert_safe(scope, require_dir=True)
                        for path in scope.glob("*.json"):
                            self._assert_safe(path, require_file=True)
                            raw = json.loads(self._read_regular(path).decode("utf-8"))
                            record = StagedSource.from_dict(raw)
                            if not re.fullmatch(r"[0-9a-f]{64}", record.canonical_content_sha256):
                                return 0
                            referenced.add(record.canonical_content_sha256)
                blobs = self.root / "blobs"
                if not blobs.exists():
                    return 0
                self._assert_safe(blobs, require_dir=True)
                removed = 0
                for shard in sorted(blobs.iterdir()):
                    self._assert_safe(shard, require_dir=True)
                    for blob in sorted(shard.iterdir()):
                        if removed >= limit:
                            return removed
                        self._assert_safe(blob, require_file=True)
                        if blob.name not in referenced:
                            # unlink itself never follows a symlink; the
                            # descriptor checks above make the containing path
                            # safe as well.
                            blob.unlink()
                            _fsync_dir(shard)
                            removed += 1
                return removed
            except (OSError, ValueError, json.JSONDecodeError, TypeError):
                return 0
