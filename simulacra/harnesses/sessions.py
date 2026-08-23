"""Project-scoped, atomic and symlink-safe session repository implementations."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from abc import ABC, abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .contracts import AgentSession


class SessionRepository(ABC):
    @abstractmethod
    def get(self, project_id: str, role: str) -> AgentSession | None: ...

    @abstractmethod
    def save(self, session: AgentSession) -> None: ...


class JsonSessionRepository(SessionRepository):
    """Atomic project repository for opaque session/thread identities.

    Credential values are neither accepted nor stored. POSIX file locking covers
    the entire read-modify-replace transaction so concurrent role saves preserve
    each other's records.
    """

    def __init__(self, workspace: Path) -> None:
        original = Path(workspace)
        if not original.exists() or not original.is_dir():
            raise ValueError("workspace must be an existing directory")
        self.workspace = original.resolve(strict=True)
        self._cmul8 = self.workspace / ".cmul8"
        self._harness = self._cmul8 / "harness"
        self.path = self._harness / "sessions.json"
        self._lock_path = self._harness / "sessions.lock"

    def _assert_contained(self, path: Path) -> Path:
        resolved = path.resolve(strict=False)
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise PermissionError(f"Harness session path escapes workspace: {path}")
        return resolved

    def _assert_not_symlink(self, path: Path) -> None:
        # Never follow an existing component. This protects the project-local
        # audit/work area from redirection outside the requested workspace.
        if path.exists() or path.is_symlink():
            if path.is_symlink():
                raise PermissionError(f"Harness session component may not be a symlink: {path}")
            self._assert_contained(path)

    def _ensure_secure_directory(self) -> None:
        for path in (self._cmul8, self._harness):
            self._assert_not_symlink(path)
            path.mkdir(mode=0o700, exist_ok=True)
            self._assert_not_symlink(path)
            if not path.is_dir():
                raise RuntimeError(f"Harness session component is not a directory: {path}")
            self._assert_contained(path)

    def _assert_secure_files(self) -> None:
        self._ensure_secure_directory()
        for path in (self.path, self._lock_path):
            self._assert_not_symlink(path)
            self._assert_contained(path)

    @contextmanager
    def _exclusive_lock(self) -> Iterator[None]:
        self._ensure_secure_directory()
        self._assert_not_symlink(self._lock_path)
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            fd = os.open(self._lock_path, flags, 0o600)
        except OSError as exc:
            raise RuntimeError(f"Unable to open harness session lock safely: {exc}") from exc
        try:
            self._assert_not_symlink(self._lock_path)
            self._assert_contained(self._lock_path)
            fcntl.flock(fd, fcntl.LOCK_EX)
            # Revalidate after acquiring the lock before loading/replacing.
            self._assert_secure_files()
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _load(self) -> dict[str, Any]:
        self._assert_secure_files()
        if not self.path.exists():
            return {"schema_version": 1, "sessions": {}}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Unable to read harness session repository: {exc}") from exc
        if not isinstance(value, dict) or not isinstance(value.get("sessions"), dict):
            raise RuntimeError("Invalid harness session repository")
        return value

    def get(self, project_id: str, role: str) -> AgentSession | None:
        item = self._load()["sessions"].get(f"{project_id}:{role}")
        if not isinstance(item, dict):
            return None
        from datetime import datetime

        return AgentSession(
            session_id=str(item["session_id"]), project_id=project_id, role=role,
            harness=str(item["harness"]), provider=str(item["provider"]), model_id=str(item["model_id"]),
            environment_id=str(item.get("environment_id", "")),
            model_reasoning_effort=item.get("model_reasoning_effort"),
            codex_profile=item.get("codex_profile"),
            thread_id=item.get("thread_id"), resumed=True,
            created_at=datetime.fromisoformat(str(item["created_at"])),
            configuration_fingerprint=str(item.get("configuration_fingerprint", "")),
            configuration_identity=item.get("configuration_identity", {}),
        )

    def save(self, session: AgentSession) -> None:
        with self._exclusive_lock():
            payload = self._load()
            payload["sessions"][f"{session.project_id}:{session.role}"] = {
                "session_id": session.session_id, "harness": session.harness, "provider": session.provider,
                "model_id": session.model_id, "environment_id": session.environment_id,
                "model_reasoning_effort": session.model_reasoning_effort, "codex_profile": session.codex_profile,
                "configuration_fingerprint": session.configuration_fingerprint,
                "configuration_identity": dict(session.configuration_identity),
                "thread_id": session.thread_id, "created_at": session.created_at.isoformat(),
            }
            fd, tmp_name = tempfile.mkstemp(prefix="sessions.", suffix=".tmp", dir=self._harness)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                    handle.flush()
                    os.fsync(handle.fileno())
                self._assert_secure_files()
                os.replace(tmp_name, self.path)
                try:
                    directory_fd = os.open(self._harness, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
                except OSError:
                    pass
            finally:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
