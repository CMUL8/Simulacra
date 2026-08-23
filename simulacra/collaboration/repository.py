"""Repository contract and deterministic project-scoped JSON/JSONL backend."""

from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, TypeVar

from .errors import AuthorizationError, ConflictError, NotFoundError, ScopeError
from .models import Comment, DomainEvent, ProjectRoom, Review, Task, validate_scope_id

Record = TypeVar("Record", ProjectRoom, Task, Comment, Review)
_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}


class CollaborationRepository(Protocol):
	def create_room(self, room: ProjectRoom) -> ProjectRoom: ...
	def get_room(self, tenant_id: str, project_id: str) -> ProjectRoom: ...
	def save_room(self, room: ProjectRoom, expected_revision: int) -> ProjectRoom: ...
	def create_task(self, task: Task) -> Task: ...
	def get_task(self, tenant_id: str, project_id: str, task_id: str) -> Task: ...
	def list_tasks(self, tenant_id: str, project_id: str) -> list[Task]: ...
	def save_task(self, task: Task, expected_revision: int) -> Task: ...
	def create_comment(self, comment: Comment) -> Comment: ...
	def list_comments(self, tenant_id: str, project_id: str) -> list[Comment]: ...
	def create_review(self, review: Review) -> Review: ...
	def list_reviews(self, tenant_id: str, project_id: str, task_id: str | None = None) -> list[Review]: ...
	def append_event(self, event: DomainEvent) -> DomainEvent: ...
	def list_events(self, tenant_id: str, project_id: str) -> list[DomainEvent]: ...


class JsonCollaborationRepository:
	"""Filesystem backend with one isolated directory per tenant/project.

	JSON record files use fsync + atomic replacement. Domain events use an
	append-only JSONL log and are idempotent by event id.
	"""

	_FILES = {"tasks": Task, "comments": Comment, "reviews": Review}

	def __init__(self, root: str | Path):
		self.root = Path(root).resolve()
		self.root.mkdir(parents=True, exist_ok=True)
		with _LOCKS_GUARD:
			self._lock = _ROOT_LOCKS.setdefault(str(self.root), threading.RLock())

	def _project_dir(self, tenant_id: str, project_id: str, *, create: bool = False) -> Path:
		validate_scope_id(tenant_id, "tenant_id")
		validate_scope_id(project_id, "project_id")
		path = (self.root / tenant_id / project_id / "collaboration").resolve()
		try:
			path.relative_to(self.root)
		except ValueError as exc:
			raise ScopeError("project path escapes repository root") from exc
		if create:
			path.mkdir(parents=True, exist_ok=True)
		return path

	def _assert_scope(self, record: Any, tenant_id: str, project_id: str) -> None:
		if record.tenant_id != tenant_id or record.project_id != project_id:
			raise ScopeError("record tenant/project scope mismatch")

	@staticmethod
	def _assert_record_id(record: Any, prefix: str) -> None:
		validate_scope_id(record.id, f"{prefix}_id")
		if not record.id.startswith(f"{prefix}_"):
			raise ScopeError(f"{prefix} id must use the {prefix}_ prefix")

	@staticmethod
	def _read_json(path: Path, default: Any) -> Any:
		if not path.exists():
			return default
		try:
			return json.loads(path.read_text(encoding="utf-8"))
		except (json.JSONDecodeError, OSError) as exc:
			raise ScopeError(f"invalid collaboration store: {path.name}") from exc

	@staticmethod
	def _atomic_json(path: Path, value: Any) -> None:
		path.parent.mkdir(parents=True, exist_ok=True)
		tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
		encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
		with tmp.open("w", encoding="utf-8") as handle:
			handle.write(encoded)
			handle.flush()
			os.fsync(handle.fileno())
		os.replace(tmp, path)
		try:
			dir_fd = os.open(path.parent, os.O_RDONLY)
			try:
				os.fsync(dir_fd)
			finally:
				os.close(dir_fd)
		except OSError:
			pass

	def create_room(self, room: ProjectRoom) -> ProjectRoom:
		with self._lock:
			self._assert_record_id(room, "room")
			path = self._project_dir(room.tenant_id, room.project_id, create=True) / "room.json"
			if path.exists():
				raise ConflictError("project room already exists")
			self._atomic_json(path, room.to_dict())
		return room

	def get_room(self, tenant_id: str, project_id: str) -> ProjectRoom:
		path = self._project_dir(tenant_id, project_id) / "room.json"
		if not path.exists():
			raise NotFoundError("project room not found")
		room = ProjectRoom.from_dict(self._read_json(path, {}))
		self._assert_scope(room, tenant_id, project_id)
		return room

	def save_room(self, room: ProjectRoom, expected_revision: int) -> ProjectRoom:
		with self._lock:
			current = self.get_room(room.tenant_id, room.project_id)
			if current.id != room.id:
				raise ConflictError("project room identity conflict")
			if current.revision != expected_revision:
				raise ConflictError(f"stale room revision: expected {expected_revision}, current {current.revision}")
			if room.revision != expected_revision + 1:
				raise ConflictError("room revision must increment exactly once")
			self._atomic_json(self._project_dir(room.tenant_id, room.project_id) / "room.json", room.to_dict())
		return room

	def _collection(
		self, tenant_id: str, project_id: str, name: str, *, create: bool = False
	) -> tuple[Path, dict[str, Any]]:
		path = self._project_dir(tenant_id, project_id, create=create) / f"{name}.json"
		rows = self._read_json(path, {})
		if not isinstance(rows, dict):
			raise ScopeError(f"invalid {name} store")
		return path, rows

	def _create_record(self, name: str, record: Record) -> Record:
		with self._lock:
			self._assert_record_id(record, {"tasks": "task", "comments": "comment", "reviews": "review"}[name])
			path, rows = self._collection(record.tenant_id, record.project_id, name, create=True)
			if record.id in rows:
				raise ConflictError(f"{name[:-1]} already exists")
			rows[record.id] = record.to_dict()
			self._atomic_json(path, {key: rows[key] for key in sorted(rows)})
		return record

	def _get_record(self, name: str, cls: type[Record], tenant_id: str, project_id: str, record_id: str) -> Record:
		validate_scope_id(record_id, f"{name[:-1]}_id")
		_, rows = self._collection(tenant_id, project_id, name)
		if record_id not in rows:
			raise NotFoundError(f"{name[:-1]} not found")
		record = cls.from_dict(rows[record_id])
		self._assert_scope(record, tenant_id, project_id)
		return record

	def _list_records(self, name: str, cls: type[Record], tenant_id: str, project_id: str) -> list[Record]:
		_, rows = self._collection(tenant_id, project_id, name)
		result = [cls.from_dict(rows[key]) for key in sorted(rows)]
		for record in result:
			self._assert_scope(record, tenant_id, project_id)
		return result

	def _save_record(self, name: str, record: Record, expected_revision: int) -> Record:
		with self._lock:
			path, rows = self._collection(record.tenant_id, record.project_id, name)
			if record.id not in rows:
				raise NotFoundError(f"{name[:-1]} not found")
			current_revision = int(rows[record.id].get("revision", 0))
			if current_revision != expected_revision:
				raise ConflictError(
					f"stale {name[:-1]} revision: expected {expected_revision}, current {current_revision}"
				)
			if record.revision != expected_revision + 1:
				raise ConflictError(f"{name[:-1]} revision must increment exactly once")
			rows[record.id] = record.to_dict()
			self._atomic_json(path, {key: rows[key] for key in sorted(rows)})
		return record

	def create_task(self, task: Task) -> Task:
		room = self.get_room(task.tenant_id, task.project_id)
		member_ids = {member.actor_id for member in room.members}
		if task.owner_id is not None and task.owner_id not in member_ids:
			raise ScopeError("task owner is not a project room member")
		if not set(task.collaborator_ids).issubset(member_ids):
			raise ScopeError("task collaborator is not a project room member")
		return self._create_record("tasks", task)

	def get_task(self, tenant_id: str, project_id: str, task_id: str) -> Task:
		self.get_room(tenant_id, project_id)
		return self._get_record("tasks", Task, tenant_id, project_id, task_id)

	def list_tasks(self, tenant_id: str, project_id: str) -> list[Task]:
		self.get_room(tenant_id, project_id)
		return self._list_records("tasks", Task, tenant_id, project_id)

	def save_task(self, task: Task, expected_revision: int) -> Task:
		return self._save_record("tasks", task, expected_revision)

	def create_comment(self, comment: Comment) -> Comment:
		room = self.get_room(comment.tenant_id, comment.project_id)
		if comment.author_id not in {member.actor_id for member in room.members}:
			raise ScopeError("comment author is not a project room member")
		if comment.task_id is not None:
			self.get_task(comment.tenant_id, comment.project_id, comment.task_id)
		return self._create_record("comments", comment)

	def list_comments(self, tenant_id: str, project_id: str) -> list[Comment]:
		self.get_room(tenant_id, project_id)
		return self._list_records("comments", Comment, tenant_id, project_id)

	def create_review(self, review: Review) -> Review:
		room = self.get_room(review.tenant_id, review.project_id)
		if review.reviewer_id not in {member.actor_id for member in room.members}:
			raise ScopeError("reviewer is not a project room member")
		self.get_task(review.tenant_id, review.project_id, review.task_id)
		return self._create_record("reviews", review)

	def list_reviews(self, tenant_id: str, project_id: str, task_id: str | None = None) -> list[Review]:
		self.get_room(tenant_id, project_id)
		rows = self._list_records("reviews", Review, tenant_id, project_id)
		return [row for row in rows if task_id is None or row.task_id == task_id]

	def append_event(self, event: DomainEvent) -> DomainEvent:
		self.get_room(event.tenant_id, event.project_id)
		validate_scope_id(event.id, "event_id")
		if not event.id.startswith("evt_"):
			raise ScopeError("event id must use the evt_ prefix")
		try:
			stamp = datetime.fromisoformat(event.timestamp)
		except ValueError as exc:
			raise ScopeError("event timestamp must be ISO-8601") from exc
		if stamp.tzinfo is None:
			raise ScopeError("event timestamp must be timezone-aware")
		with self._lock:
			path = self._project_dir(event.tenant_id, event.project_id, create=True) / "events.jsonl"
			for existing in self.list_events(event.tenant_id, event.project_id):
				if existing.id == event.id:
					if existing.to_dict() == event.to_dict():
						return existing
					raise ConflictError("event id already exists with different content")
			with path.open("a", encoding="utf-8") as handle:
				handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
				handle.flush()
				os.fsync(handle.fileno())
		return event

	def list_events(self, tenant_id: str, project_id: str) -> list[DomainEvent]:
		self.get_room(tenant_id, project_id)
		path = self._project_dir(tenant_id, project_id) / "events.jsonl"
		if not path.exists():
			return []
		result: list[DomainEvent] = []
		for line in path.read_text(encoding="utf-8").splitlines():
			if not line.strip():
				continue
			event = DomainEvent.from_dict(json.loads(line))
			self._assert_scope(event, tenant_id, project_id)
			result.append(event)
		return result

	def get_inbox_state(self, tenant_id: str, project_id: str, actor_id: str) -> dict[str, Any]:
		validate_scope_id(actor_id, "actor_id")
		room = self.get_room(tenant_id, project_id)
		if actor_id not in {member.actor_id for member in room.members}:
			raise AuthorizationError("actor is not a project room member")
		path = self._project_dir(tenant_id, project_id) / "inbox_state.json"
		states = self._read_json(path, {})
		return dict(states.get(actor_id, {"last_read_position": 0, "updated_at": None}))

	def save_inbox_state(
		self, tenant_id: str, project_id: str, actor_id: str, *, last_read_position: int, updated_at: str
	) -> dict[str, Any]:
		validate_scope_id(actor_id, "actor_id")
		room = self.get_room(tenant_id, project_id)
		if actor_id not in {member.actor_id for member in room.members}:
			raise AuthorizationError("actor is not a project room member")
		if last_read_position < 0:
			raise ConflictError("read position cannot be negative")
		with self._lock:
			path = self._project_dir(tenant_id, project_id, create=True) / "inbox_state.json"
			states = self._read_json(path, {})
			current = int(states.get(actor_id, {}).get("last_read_position", 0))
			if last_read_position < current:
				raise ConflictError("read position cannot move backwards")
			states[actor_id] = {"last_read_position": last_read_position, "updated_at": updated_at}
			self._atomic_json(path, {key: states[key] for key in sorted(states)})
		return dict(states[actor_id])
