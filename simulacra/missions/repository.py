"""Crash-atomic project-scoped Mission state persistence."""

from __future__ import annotations

import fcntl
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, TypeVar

from simulacra.collaboration.models import validate_scope_id

T = TypeVar("T")
_COLLECTIONS = ("agents", "runs", "triggers", "deliverables", "events", "approvals")


class MissionNotFoundError(Exception):
    pass


class MissionConflictError(Exception):
    pass


class JsonMissionRepository:
    """A single state.json is replaced only after a complete mutation succeeds."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._thread_lock = threading.RLock()

    def _dir(self, tenant_id: str, project_id: str, *, create: bool = False) -> Path:
        validate_scope_id(tenant_id, "tenant_id")
        validate_scope_id(project_id, "project_id")
        directory = (self.root / tenant_id / project_id / "missions").resolve()
        if self.root not in directory.parents:
            raise ValueError("mission path escapes root")
        if create:
            directory.mkdir(parents=True, exist_ok=True)
        return directory

    @contextmanager
    def lock(self, tenant_id: str, project_id: str):
        with self._thread_lock:
            directory = self._dir(tenant_id, project_id, create=True)
            descriptor = os.open(directory / ".lock", os.O_CREAT | os.O_RDWR, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    @staticmethod
    def _empty() -> dict[str, Any]:
        return {"mission": None, "retention": {"dropped_events": 0}, **{name: {} for name in _COLLECTIONS}}

    def _load(self, tenant_id: str, project_id: str) -> dict[str, Any]:
        directory = self._dir(tenant_id, project_id)
        state_path = directory / "state.json"
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError("invalid mission state") from exc
            if not isinstance(state, dict) or "mission" not in state:
                raise ValueError("invalid mission state")
            mission = state.get("mission")
            if mission is not None and (
                not isinstance(mission, dict)
                or mission.get("tenant_id") != tenant_id
                or mission.get("project_id") != project_id
            ):
                raise ValueError("invalid mission scope")
            for name in _COLLECTIONS:
                state.setdefault(name, {})
                if not isinstance(state[name], dict):
                    raise ValueError("invalid mission state")
            state.setdefault("retention", {"dropped_events": 0})
            if not isinstance(state["retention"], dict) or not isinstance(state["retention"].get("dropped_events", 0), int):
                raise ValueError("invalid mission state")
            return state
        # One-time, read-only compatibility import of the first split-file state.
        state = self._empty()
        for name in ("mission", *_COLLECTIONS):
            path = directory / f"{name}.json"
            if not path.exists():
                continue
            try:
                state[name] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid legacy mission store: {name}") from exc
        return state

    def _replace_state(self, tenant_id: str, project_id: str, state: dict[str, Any]) -> None:
        directory = self._dir(tenant_id, project_id, create=True)
        mission = state.get("mission")
        if not isinstance(mission, dict) or mission.get("tenant_id") != tenant_id or mission.get("project_id") != project_id:
            raise ValueError("invalid mission scope")
        discovery = directory / "discovery.json"
        discovery_temporary = discovery.with_name(f".{discovery.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with discovery_temporary.open("w", encoding="utf-8") as handle:
            json.dump({
                "schema_version": 1,
                "tenant_id": tenant_id,
                "project_id": project_id,
                "mission_id": mission.get("id"),
            }, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(discovery_temporary, discovery)
        discovery_directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(discovery_directory_fd)
        finally:
            os.close(discovery_directory_fd)
        target = directory / "state.json"
        temporary = target.with_name(f".{target.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        directory_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)

    def get_mission(self, tenant_id: str, project_id: str) -> dict[str, Any]:
        data = self._load(tenant_id, project_id)["mission"]
        if data is None:
            raise MissionNotFoundError("mission not found")
        return data

    def mutate(self, tenant_id: str, project_id: str, callback: Callable[[dict[str, Any]], T]) -> T:
        with self.lock(tenant_id, project_id):
            state = self._load(tenant_id, project_id)
            result = callback(state)
            self._replace_state(tenant_id, project_id, state)
            return result

    def list_collection(self, tenant_id: str, project_id: str, name: str) -> dict[str, Any]:
        if name not in _COLLECTIONS:
            raise ValueError("unknown mission collection")
        return self._load(tenant_id, project_id)[name]

    def get_collection_item(self, tenant_id: str, project_id: str, name: str, item_id: str) -> dict[str, Any] | None:
        """Read one durable collection record without applying an overview cap."""
        if name not in _COLLECTIONS:
            raise ValueError("unknown mission collection")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("collection item id is required")
        value = self._load(tenant_id, project_id)[name].get(item_id)
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("invalid mission state")
        return dict(value)

    def retention(self, tenant_id: str, project_id: str) -> dict[str, Any]:
        return dict(self._load(tenant_id, project_id).get("retention", {}))
