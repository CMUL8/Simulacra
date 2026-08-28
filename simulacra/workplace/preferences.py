"""Private, crash-atomic workplace preferences for one authenticated human."""

from __future__ import annotations

import fcntl
import json
import os
import re
import stat
import threading
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from simulacra.collaboration.models import validate_scope_id


_VIEWS = frozenset({"list", "board"})
_FILTER_KEYS = frozenset({"bucket", "mission_id", "assignee_id"})
_WORK_BUCKETS = frozenset({"needs_you", "in_progress", "ready_for_review", "done", "stopped"})
_EVENT_SELECTIONS = frozenset({"all_actionable", "mentions_and_decisions", "off"})
_CHANNELS = frozenset({"browser", "email", "push"})
_DIGESTS = frozenset({"off", "immediate", "daily", "weekly"})
_MAX_STATE_BYTES = 1024 * 1024
_SCOPE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class RevisionConflict(ValueError):
    """The client edited an older preference revision."""


class PreferenceValidationError(ValueError):
    """A stable validation boundary with no private path details."""


class _PreferenceDirectoryMissing(FileNotFoundError):
    """An absent preference directory, distinct from an unsafe directory."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _default_state() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "work_view_preferences": {},
        "notification_preference": {
            "event_selection": "all_actionable",
            "channels": ["browser"],
            "digest": "off",
            "muted_mission_ids": [],
            "revision": 0,
            "updated_at": None,
        },
    }


class JsonWorkplacePreferenceRepository:
    """One descriptor-safe state file per tenant/human, never client-addressed."""

    def __init__(self, root: str | Path, *, clock: Callable[[], str] = _now) -> None:
        self.root = Path(os.path.abspath(os.fspath(root)))
        if self.root == Path("/"):
            raise PreferenceValidationError("preference storage is unavailable")
        self.root.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.fault_injector: Callable[[str], None] | None = None
        self._thread_lock = threading.RLock()

    @staticmethod
    def _dir_flags() -> int:
        return os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)

    def _root_fd(self) -> int:
        try:
            descriptor = os.open(self.root, self._dir_flags())
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise PreferenceValidationError("preference storage is unavailable")
            return descriptor
        except OSError as exc:
            raise PreferenceValidationError("preference storage is unavailable") from exc

    @classmethod
    def _open_child(cls, parent_fd: int, name: str, *, create: bool) -> int:
        validate_scope_id(name, "preference scope")
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent_fd)
                os.fsync(parent_fd)
            except FileExistsError:
                pass
            except OSError as exc:
                raise PreferenceValidationError("preference storage is unavailable") from exc
        try:
            descriptor = os.open(name, cls._dir_flags(), dir_fd=parent_fd)
        except FileNotFoundError as exc:
            raise _PreferenceDirectoryMissing(name) from exc
        except OSError as exc:
            raise PreferenceValidationError("preference storage is unavailable") from exc
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise PreferenceValidationError("preference storage is unavailable")
        return descriptor

    @contextmanager
    def _human_dir(self, tenant_id: str, human_id: str, *, create: bool) -> Iterator[int | None]:
        try:
            validate_scope_id(tenant_id, "tenant_id")
            validate_scope_id(human_id, "human_id")
        except (TypeError, ValueError) as exc:
            raise PreferenceValidationError("preference scope is invalid") from exc
        root = self._root_fd()
        tenant: int | None = None
        human: int | None = None
        try:
            try:
                tenant = self._open_child(root, tenant_id, create=create)
                human = self._open_child(tenant, human_id, create=create)
            except _PreferenceDirectoryMissing:
                if create:
                    raise PreferenceValidationError("preference storage is unavailable")
                yield None
                return
            yield human
        finally:
            if human is not None:
                os.close(human)
            if tenant is not None:
                os.close(tenant)
            os.close(root)

    @staticmethod
    def _validate_state(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or value.get("schema_version") != 1:
            raise PreferenceValidationError("preference state is unavailable")
        work = value.get("work_view_preferences")
        notification = value.get("notification_preference")
        if not isinstance(work, dict) or not isinstance(notification, dict):
            raise PreferenceValidationError("preference state is unavailable")
        work_fields = {"scope", "view", "filters", "revision", "updated_at"}
        for scope, row in work.items():
            if (
                not isinstance(scope, str) or not _SCOPE.fullmatch(scope)
                or not isinstance(row, dict) or set(row) != work_fields or row.get("scope") != scope
                or row.get("view") not in _VIEWS
                or isinstance(row.get("revision"), bool) or not isinstance(row.get("revision"), int) or row["revision"] < 1
                or not (row.get("updated_at") is None or isinstance(row.get("updated_at"), str))
            ):
                raise PreferenceValidationError("preference state is unavailable")
            JsonWorkplacePreferenceRepository._filters(row.get("filters"))
        notification_fields = {"event_selection", "channels", "digest", "muted_mission_ids", "revision", "updated_at"}
        if (
            set(notification) != notification_fields
            or notification.get("event_selection") not in _EVENT_SELECTIONS
            or notification.get("digest") not in _DIGESTS
            or not isinstance(notification.get("channels"), list)
            or len(notification["channels"]) != len(set(notification["channels"]))
            or any(channel not in _CHANNELS for channel in notification["channels"])
            or not isinstance(notification.get("muted_mission_ids"), list)
            or len(notification["muted_mission_ids"]) != len(set(notification["muted_mission_ids"]))
            or isinstance(notification.get("revision"), bool) or not isinstance(notification.get("revision"), int) or notification["revision"] < 0
            or not (notification.get("updated_at") is None or isinstance(notification.get("updated_at"), str))
        ):
            raise PreferenceValidationError("preference state is unavailable")
        try:
            for mission_id in notification["muted_mission_ids"]:
                validate_scope_id(mission_id, "mission_id")
        except (TypeError, ValueError) as exc:
            raise PreferenceValidationError("preference state is unavailable") from exc
        return value

    def _load_fd(self, directory_fd: int | None) -> dict[str, Any]:
        if directory_fd is None:
            return _default_state()
        try:
            descriptor = os.open(
                "state.json",
                os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                dir_fd=directory_fd,
            )
        except FileNotFoundError:
            return _default_state()
        except OSError as exc:
            raise PreferenceValidationError("preference state is unavailable") from exc
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_STATE_BYTES:
                raise PreferenceValidationError("preference state is unavailable")
            content = b""
            while len(content) <= _MAX_STATE_BYTES:
                chunk = os.read(descriptor, min(65536, _MAX_STATE_BYTES + 1 - len(content)))
                if not chunk:
                    break
                content += chunk
            if len(content) > _MAX_STATE_BYTES:
                raise PreferenceValidationError("preference state is unavailable")
            return self._validate_state(json.loads(content.decode("utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PreferenceValidationError("preference state is unavailable") from exc
        finally:
            os.close(descriptor)

    def _replace_fd(self, directory_fd: int, state: Mapping[str, Any]) -> None:
        temporary = f".state.json.{os.getpid()}.{threading.get_ident()}.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        descriptor = -1
        replaced = False
        checkpoint = self.fault_injector or (lambda _stage: None)
        try:
            checkpoint("before_write")
            descriptor = os.open(temporary, flags, 0o600, dir_fd=directory_fd)
            payload = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode() + b"\n"
            view = memoryview(payload)
            while view:
                written = os.write(descriptor, view)
                if written <= 0:
                    raise PreferenceValidationError("preference state is unavailable")
                view = view[written:]
            checkpoint("before_temp_fsync")
            os.fsync(descriptor)
            checkpoint("after_temp_fsync")
            os.close(descriptor)
            descriptor = -1
            checkpoint("before_replace")
            os.replace(temporary, "state.json", src_dir_fd=directory_fd, dst_dir_fd=directory_fd)
            replaced = True
            checkpoint("after_replace")
            checkpoint("before_parent_fsync")
            os.fsync(directory_fd)
            checkpoint("after_parent_fsync")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if not replaced:
                try:
                    os.unlink(temporary, dir_fd=directory_fd)
                except OSError:
                    pass

    @contextmanager
    def _locked_state(self, tenant_id: str, human_id: str) -> Iterator[tuple[int, dict[str, Any]]]:
        with self._thread_lock, self._human_dir(tenant_id, human_id, create=True) as human_fd:
            assert human_fd is not None
            try:
                lock_fd = os.open(
                    ".lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0),
                    0o600, dir_fd=human_fd,
                )
            except OSError as exc:
                raise PreferenceValidationError("preference storage is unavailable") from exc
            try:
                if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
                    raise PreferenceValidationError("preference storage is unavailable")
                fcntl.flock(lock_fd, fcntl.LOCK_EX)
                yield human_fd, self._load_fd(human_fd)
            finally:
                fcntl.flock(lock_fd, fcntl.LOCK_UN)
                os.close(lock_fd)

    def get(self, tenant_id: str, human_id: str) -> dict[str, Any]:
        with self._thread_lock, self._human_dir(tenant_id, human_id, create=False) as human_fd:
            state = self._load_fd(human_fd)
        work = [dict(value) for _, value in sorted(state["work_view_preferences"].items())]
        return {"work_view_preferences": work, "notification_preference": dict(state["notification_preference"])}

    @staticmethod
    def _filters(value: Any) -> dict[str, Any]:
        if not isinstance(value, dict) or set(value) - _FILTER_KEYS:
            raise PreferenceValidationError("work filters are invalid")
        result: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(item, str) or not item or len(item) > 128:
                raise PreferenceValidationError("work filters are invalid")
            if key == "bucket" and item not in _WORK_BUCKETS:
                raise PreferenceValidationError("work filters are invalid")
            if key in {"mission_id", "assignee_id"}:
                try:
                    validate_scope_id(item, key)
                except (TypeError, ValueError) as exc:
                    raise PreferenceValidationError("work filters are invalid") from exc
            result[key] = item
        return result

    def put_work_view(
        self, tenant_id: str, human_id: str, *, expected_revision: int, scope: str, view: str, filters: Any,
    ) -> dict[str, Any]:
        if not isinstance(scope, str) or not _SCOPE.fullmatch(scope) or view not in _VIEWS:
            raise PreferenceValidationError("work preference is invalid")
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
            raise PreferenceValidationError("work preference is invalid")
        normalized_filters = self._filters(filters)
        with self._locked_state(tenant_id, human_id) as (directory_fd, state):
            current = state["work_view_preferences"].get(scope)
            actual = current.get("revision", 0) if isinstance(current, dict) else 0
            if actual != expected_revision:
                raise RevisionConflict("revision_conflict")
            result = {
                "scope": scope, "view": view, "filters": normalized_filters,
                "revision": actual + 1, "updated_at": self.clock(),
            }
            state["work_view_preferences"][scope] = result
            self._replace_fd(directory_fd, state)
            return dict(result)

    def put_notification(
        self,
        tenant_id: str,
        human_id: str,
        *,
        expected_revision: int,
        event_selection: str,
        channels: Any,
        digest: str,
        muted_mission_ids: Any,
    ) -> dict[str, Any]:
        if (
            isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0
            or event_selection not in _EVENT_SELECTIONS or digest not in _DIGESTS
            or not isinstance(channels, list) or len(channels) != len(set(channels))
            or any(channel not in _CHANNELS for channel in channels)
            or not isinstance(muted_mission_ids, list) or len(muted_mission_ids) != len(set(muted_mission_ids))
        ):
            raise PreferenceValidationError("notification preference is invalid")
        try:
            for mission_id in muted_mission_ids:
                validate_scope_id(mission_id, "mission_id")
        except (TypeError, ValueError) as exc:
            raise PreferenceValidationError("notification preference is invalid") from exc
        with self._locked_state(tenant_id, human_id) as (directory_fd, state):
            current = state["notification_preference"]
            actual = current.get("revision", 0) if isinstance(current, dict) else 0
            if actual != expected_revision:
                raise RevisionConflict("revision_conflict")
            result = {
                "event_selection": event_selection,
                "channels": list(channels),
                "digest": digest,
                "muted_mission_ids": sorted(muted_mission_ids),
                "revision": actual + 1,
                "updated_at": self.clock(),
            }
            state["notification_preference"] = result
            self._replace_fd(directory_fd, state)
            return dict(result)

    @staticmethod
    def allows_external(preference: Mapping[str, Any], *, event_type: str, mission_id: str) -> bool:
        if mission_id in preference.get("muted_mission_ids", []):
            return False
        selection = preference.get("event_selection")
        if selection == "off":
            return False
        if selection == "all_actionable":
            return True
        return selection == "mentions_and_decisions" and event_type in {"mention", "decision_required"}
