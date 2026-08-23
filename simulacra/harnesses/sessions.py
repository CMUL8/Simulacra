"""Project-scoped, atomic session repository implementations."""

from __future__ import annotations

import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from .contracts import AgentSession


class SessionRepository(ABC):
    @abstractmethod
    def get(self, project_id: str, role: str) -> AgentSession | None: ...

    @abstractmethod
    def save(self, session: AgentSession) -> None: ...


class JsonSessionRepository(SessionRepository):
    """Stores thread identifiers only, never provider credential values."""

    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.path = self.workspace / ".cmul8" / "harness" / "sessions.json"

    def _load(self) -> dict[str, Any]:
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
            thread_id=item.get("thread_id"), resumed=True,
            created_at=datetime.fromisoformat(str(item["created_at"])),
        )

    def save(self, session: AgentSession) -> None:
        payload = self._load()
        payload["sessions"][f"{session.project_id}:{session.role}"] = {
            "session_id": session.session_id, "harness": session.harness, "provider": session.provider,
            "model_id": session.model_id, "thread_id": session.thread_id,
            "created_at": session.created_at.isoformat(),
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix="sessions.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
