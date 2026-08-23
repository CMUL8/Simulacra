"""Repository protocol and durable POSIX filesystem implementation."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Protocol, TypeVar

from .errors import RuntimeConflictError, RuntimeNotFoundError, RuntimeScopeError
from .models import (
	ActionRecord,
	ApprovalRequest,
	AuditEvent,
	EntityRecord,
	HumanTask,
	ScheduledJob,
	TelemetryEvent,
	WorkflowInstance,
	validate_scope,
)
from .security import assert_opaque_credentials

T = TypeVar("T")
R = TypeVar("R")
_THREAD_LOCKS_GUARD = threading.Lock()
_THREAD_LOCKS: dict[str, threading.RLock] = {}


class RuntimeRepository(Protocol):
	def create_entity(self, record: EntityRecord) -> EntityRecord: ...
	def get_entity(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> EntityRecord: ...
	def query_entities(self, tenant_id: str, environment_id: str, project_id: str, *, entity_type: str | None = None, filters: dict[str, Any] | None = None) -> list[EntityRecord]: ...
	def save_entity(self, record: EntityRecord, expected_revision: int) -> EntityRecord: ...
	def delete_entity(self, tenant_id: str, environment_id: str, project_id: str, record_id: str, *, expected_revision: int) -> None: ...
	def create_workflow(self, record: WorkflowInstance) -> WorkflowInstance: ...
	def get_workflow(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> WorkflowInstance: ...
	def list_workflows(self, tenant_id: str, environment_id: str, project_id: str) -> list[WorkflowInstance]: ...
	def save_workflow(self, record: WorkflowInstance, expected_revision: int) -> WorkflowInstance: ...
	def create_human_task(self, record: HumanTask) -> HumanTask: ...
	def get_human_task(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> HumanTask: ...
	def list_human_tasks(self, tenant_id: str, environment_id: str, project_id: str) -> list[HumanTask]: ...
	def save_human_task(self, record: HumanTask, expected_revision: int) -> HumanTask: ...
	def create_approval(self, record: ApprovalRequest) -> ApprovalRequest: ...
	def get_approval(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> ApprovalRequest: ...
	def list_approvals(self, tenant_id: str, environment_id: str, project_id: str) -> list[ApprovalRequest]: ...
	def save_approval(self, record: ApprovalRequest, expected_revision: int) -> ApprovalRequest: ...
	def create_job(self, record: ScheduledJob) -> ScheduledJob: ...
	def get_job(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> ScheduledJob: ...
	def list_jobs(self, tenant_id: str, environment_id: str, project_id: str) -> list[ScheduledJob]: ...
	def save_job(self, record: ScheduledJob, expected_revision: int) -> ScheduledJob: ...
	def create_action(self, record: ActionRecord) -> ActionRecord: ...
	def create_action_with_approval(self, record: ActionRecord, approval: ApprovalRequest) -> tuple[ActionRecord, ApprovalRequest]: ...
	def get_action(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> ActionRecord: ...
	def list_actions(self, tenant_id: str, environment_id: str, project_id: str) -> list[ActionRecord]: ...
	def save_action(self, record: ActionRecord, expected_revision: int) -> ActionRecord: ...
	def append_audit(self, record: AuditEvent) -> AuditEvent: ...
	def list_audit(self, tenant_id: str, environment_id: str, project_id: str) -> list[AuditEvent]: ...
	def append_telemetry(self, record: TelemetryEvent) -> TelemetryEvent: ...
	def list_telemetry(self, tenant_id: str, environment_id: str, project_id: str) -> list[TelemetryEvent]: ...
	def read_project(self, tenant_id: str, environment_id: str, project_id: str) -> dict[str, Any]: ...
	def mutate_project(self, tenant_id: str, environment_id: str, project_id: str, callback: Callable[[dict[str, Any]], R]) -> R: ...


class JsonRuntimeRepository:
	"""Deterministic tenant/environment/project scoped JSON repository.

	Every read-modify-write transaction is serialized by an in-process lock and a
	POSIX advisory lock. The complete scoped document is atomically replaced, so
	workflow transitions, idempotency indexes, claims and decisions cannot tear.
	"""

	_COLLECTIONS: dict[str, type[Any]] = {
		"entities": EntityRecord,
		"workflows": WorkflowInstance,
		"human_tasks": HumanTask,
		"approvals": ApprovalRequest,
		"jobs": ScheduledJob,
		"actions": ActionRecord,
		"audit": AuditEvent,
		"telemetry": TelemetryEvent,
	}

	def __init__(self, root: str | Path):
		candidate = Path(root)
		candidate.mkdir(parents=True, exist_ok=True)
		if candidate.is_symlink():
			raise RuntimeScopeError("runtime repository root may not be a symlink")
		self.root = candidate.resolve()
		with _THREAD_LOCKS_GUARD:
			self._thread_lock = _THREAD_LOCKS.setdefault(str(self.root), threading.RLock())

	def _safe_child(self, parent: Path, name: str, *, create: bool = False) -> Path:
		validate_scope(name, "scope id")
		path = parent / name
		if path.is_symlink():
			raise RuntimeScopeError(f"runtime storage path may not contain symlinks: {path}")
		if create:
			path.mkdir(exist_ok=True)
		resolved = path.resolve()
		try:
			resolved.relative_to(self.root)
		except ValueError as exc:
			raise RuntimeScopeError("runtime storage escaped repository root") from exc
		return resolved

	def _project_dir(self, tenant_id: str, environment_id: str, project_id: str, *, create: bool = False) -> Path:
		current = self.root
		for value in (tenant_id, environment_id, project_id, "runtime"):
			current = self._safe_child(current, value, create=create)
		return current

	def _lock_path(self, tenant_id: str, environment_id: str, project_id: str) -> Path:
		lock_root = self.root / ".runtime-locks"
		if lock_root.is_symlink():
			raise RuntimeScopeError("runtime lock root may not be a symlink")
		lock_root.mkdir(exist_ok=True)
		current = lock_root
		for value in (tenant_id, environment_id):
			current = self._safe_child(current, value, create=True)
		validate_scope(project_id, "project_id")
		return current / f"{project_id}.lock"

	@contextmanager
	def _locked(self, tenant_id: str, environment_id: str, project_id: str) -> Iterator[None]:
		for label, value in (("tenant_id", tenant_id), ("environment_id", environment_id), ("project_id", project_id)):
			try:
				validate_scope(value, label)
			except ValueError as exc:
				raise RuntimeScopeError(str(exc)) from exc
		with self._thread_lock:
			path = self._lock_path(tenant_id, environment_id, project_id)
			flags = os.O_CREAT | os.O_RDWR
			if hasattr(os, "O_NOFOLLOW"):
				flags |= os.O_NOFOLLOW
			try:
				fd = os.open(path, flags, 0o600)
			except OSError as exc:
				raise RuntimeScopeError("unable to open runtime project lock") from exc
			try:
				fcntl.flock(fd, fcntl.LOCK_EX)
				yield
			finally:
				fcntl.flock(fd, fcntl.LOCK_UN)
				os.close(fd)

	@staticmethod
	def _empty_state(tenant_id: str, environment_id: str, project_id: str) -> dict[str, Any]:
		return {
			"schema_version": "cmul8.runtime.repository.v0",
			"tenant_id": tenant_id,
			"environment_id": environment_id,
			"project_id": project_id,
			**{name: {} for name in JsonRuntimeRepository._COLLECTIONS},
			"idempotency": {},
			"workflow_commands": {},
		}

	def _read_state(self, tenant_id: str, environment_id: str, project_id: str) -> dict[str, Any]:
		path = self._project_dir(tenant_id, environment_id, project_id) / "state.json"
		if not path.exists():
			return self._empty_state(tenant_id, environment_id, project_id)
		if path.is_symlink():
			raise RuntimeScopeError("runtime state may not be a symlink")
		try:
			state = json.loads(path.read_text(encoding="utf-8"))
		except (OSError, json.JSONDecodeError) as exc:
			raise RuntimeScopeError("invalid runtime state document") from exc
		for key, expected in (("tenant_id", tenant_id), ("environment_id", environment_id), ("project_id", project_id)):
			if state.get(key) != expected:
				raise RuntimeScopeError(f"persisted runtime {key} mismatch")
		return state

	def _atomic_state(self, tenant_id: str, environment_id: str, project_id: str, state: dict[str, Any]) -> None:
		directory = self._project_dir(tenant_id, environment_id, project_id, create=True)
		path = directory / "state.json"
		encoded = json.dumps(state, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")) + "\n"
		fd, temp_name = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=directory)
		try:
			with os.fdopen(fd, "w", encoding="utf-8") as handle:
				handle.write(encoded)
				handle.flush()
				os.fsync(handle.fileno())
			os.replace(temp_name, path)
			try:
				dir_fd = os.open(directory, os.O_RDONLY)
				try:
					os.fsync(dir_fd)
				finally:
					os.close(dir_fd)
			except OSError:
				pass
		finally:
			if os.path.exists(temp_name):
				os.unlink(temp_name)

	def mutate_project(self, tenant_id: str, environment_id: str, project_id: str, callback: Callable[[dict[str, Any]], R]) -> R:
		"""Atomically mutate a scoped state document (repository/service SPI)."""
		with self._locked(tenant_id, environment_id, project_id):
			state = self._read_state(tenant_id, environment_id, project_id)
			result = callback(state)
			self._atomic_state(tenant_id, environment_id, project_id, state)
			return result

	def read_project(self, tenant_id: str, environment_id: str, project_id: str) -> dict[str, Any]:
		with self._locked(tenant_id, environment_id, project_id):
			return copy.deepcopy(self._read_state(tenant_id, environment_id, project_id))

	@staticmethod
	def _check_record_scope(record: Any, tenant_id: str, environment_id: str, project_id: str) -> None:
		if (record.tenant_id, record.environment_id, record.project_id) != (tenant_id, environment_id, project_id):
			raise RuntimeScopeError("runtime record scope mismatch")

	def _create(self, collection: str, record: T) -> T:
		def change(state: dict[str, Any]) -> T:
			rows = state[collection]
			if record.id in rows:
				raise RuntimeConflictError(f"{collection} record already exists")
			rows[record.id] = record.to_dict()
			return record
		return self.mutate_project(record.tenant_id, record.environment_id, record.project_id, change)

	def _get(self, collection: str, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> Any:
		validate_scope(record_id, "record_id")
		row = self.read_project(tenant_id, environment_id, project_id)[collection].get(record_id)
		if row is None:
			raise RuntimeNotFoundError(f"{collection} record not found")
		record = self._COLLECTIONS[collection].from_dict(row)
		self._check_record_scope(record, tenant_id, environment_id, project_id)
		return record

	def _list(self, collection: str, tenant_id: str, environment_id: str, project_id: str) -> list[Any]:
		rows = self.read_project(tenant_id, environment_id, project_id)[collection]
		result: list[Any] = []
		for key in sorted(rows):
			record = self._COLLECTIONS[collection].from_dict(rows[key])
			self._check_record_scope(record, tenant_id, environment_id, project_id)
			result.append(record)
		return result

	def _save(self, collection: str, record: T, expected_revision: int) -> T:
		def change(state: dict[str, Any]) -> T:
			row = state[collection].get(record.id)
			if row is None:
				raise RuntimeNotFoundError(f"{collection} record not found")
			if row.get("revision") != expected_revision or record.revision != expected_revision + 1:
				raise RuntimeConflictError(f"stale {collection} revision")
			state[collection][record.id] = record.to_dict()
			return record
		return self.mutate_project(record.tenant_id, record.environment_id, record.project_id, change)

	def create_entity(self, record: EntityRecord) -> EntityRecord:
		return self._create("entities", record)

	def get_entity(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> EntityRecord:
		return self._get("entities", tenant_id, environment_id, project_id, record_id)

	def query_entities(self, tenant_id: str, environment_id: str, project_id: str, *, entity_type: str | None = None, filters: dict[str, Any] | None = None) -> list[EntityRecord]:
		rows = self._list("entities", tenant_id, environment_id, project_id)
		filters = filters or {}
		return [row for row in rows if (entity_type is None or row.entity_type == entity_type) and all(row.data.get(key) == value for key, value in filters.items())]

	def save_entity(self, record: EntityRecord, expected_revision: int) -> EntityRecord:
		return self._save("entities", record, expected_revision)

	def delete_entity(self, tenant_id: str, environment_id: str, project_id: str, record_id: str, *, expected_revision: int) -> None:
		def change(state: dict[str, Any]) -> None:
			row = state["entities"].get(record_id)
			if row is None:
				raise RuntimeNotFoundError("entity record not found")
			if row.get("revision") != expected_revision:
				raise RuntimeConflictError("stale entity revision")
			del state["entities"][record_id]
		self.mutate_project(tenant_id, environment_id, project_id, change)

	def create_workflow(self, record: WorkflowInstance) -> WorkflowInstance: return self._create("workflows", record)
	def get_workflow(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> WorkflowInstance: return self._get("workflows", tenant_id, environment_id, project_id, record_id)
	def list_workflows(self, tenant_id: str, environment_id: str, project_id: str) -> list[WorkflowInstance]: return self._list("workflows", tenant_id, environment_id, project_id)
	def save_workflow(self, record: WorkflowInstance, expected_revision: int) -> WorkflowInstance: return self._save("workflows", record, expected_revision)
	def create_human_task(self, record: HumanTask) -> HumanTask: return self._create("human_tasks", record)
	def get_human_task(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> HumanTask: return self._get("human_tasks", tenant_id, environment_id, project_id, record_id)
	def list_human_tasks(self, tenant_id: str, environment_id: str, project_id: str) -> list[HumanTask]: return self._list("human_tasks", tenant_id, environment_id, project_id)
	def save_human_task(self, record: HumanTask, expected_revision: int) -> HumanTask: return self._save("human_tasks", record, expected_revision)
	def create_approval(self, record: ApprovalRequest) -> ApprovalRequest: return self._create("approvals", record)
	def get_approval(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> ApprovalRequest: return self._get("approvals", tenant_id, environment_id, project_id, record_id)
	def list_approvals(self, tenant_id: str, environment_id: str, project_id: str) -> list[ApprovalRequest]: return self._list("approvals", tenant_id, environment_id, project_id)
	def save_approval(self, record: ApprovalRequest, expected_revision: int) -> ApprovalRequest: return self._save("approvals", record, expected_revision)
	def create_job(self, record: ScheduledJob) -> ScheduledJob: return self._create("jobs", record)
	def get_job(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> ScheduledJob: return self._get("jobs", tenant_id, environment_id, project_id, record_id)
	def list_jobs(self, tenant_id: str, environment_id: str, project_id: str) -> list[ScheduledJob]: return self._list("jobs", tenant_id, environment_id, project_id)
	def save_job(self, record: ScheduledJob, expected_revision: int) -> ScheduledJob: return self._save("jobs", record, expected_revision)

	def create_action(self, record: ActionRecord) -> ActionRecord:
		assert_opaque_credentials(record.input, context="action payload")
		def change(state: dict[str, Any]) -> ActionRecord:
			key = record.idempotency_key
			existing_id = state["idempotency"].get(key)
			if existing_id:
				existing = ActionRecord.from_dict(state["actions"][existing_id])
				if (existing.connector_id, existing.operation, existing.input) != (record.connector_id, record.operation, record.input):
					raise RuntimeConflictError("idempotency key reused for a different action")
				return existing
			state["actions"][record.id] = record.to_dict()
			state["idempotency"][key] = record.id
			return record
		return self.mutate_project(record.tenant_id, record.environment_id, record.project_id, change)

	def create_action_with_approval(self, record: ActionRecord, approval: ApprovalRequest) -> tuple[ActionRecord, ApprovalRequest]:
		"""Atomically persist a pending action, its approval, and both links."""
		assert_opaque_credentials(record.input, context="action payload")
		if record.approval_id != approval.id or approval.payload.get("action_id") != record.id:
			raise RuntimeConflictError("action and approval linkage is incomplete")
		self._check_record_scope(approval, record.tenant_id, record.environment_id, record.project_id)
		def change(state: dict[str, Any]) -> tuple[ActionRecord, ApprovalRequest]:
			existing_id = state["idempotency"].get(record.idempotency_key)
			if existing_id:
				existing = ActionRecord.from_dict(state["actions"][existing_id])
				if (existing.connector_id, existing.operation, existing.input) != (record.connector_id, record.operation, record.input):
					raise RuntimeConflictError("idempotency key reused for a different action")
				if not existing.approval_id or existing.approval_id not in state["approvals"]:
					raise RuntimeConflictError("persisted pending action has no linked approval")
				return existing, ApprovalRequest.from_dict(state["approvals"][existing.approval_id])
			if record.id in state["actions"] or approval.id in state["approvals"]:
				raise RuntimeConflictError("action or approval record already exists")
			state["actions"][record.id] = record.to_dict()
			state["approvals"][approval.id] = approval.to_dict()
			state["idempotency"][record.idempotency_key] = record.id
			return record, approval
		return self.mutate_project(record.tenant_id, record.environment_id, record.project_id, change)

	def get_action(self, tenant_id: str, environment_id: str, project_id: str, record_id: str) -> ActionRecord: return self._get("actions", tenant_id, environment_id, project_id, record_id)
	def list_actions(self, tenant_id: str, environment_id: str, project_id: str) -> list[ActionRecord]: return self._list("actions", tenant_id, environment_id, project_id)
	def save_action(self, record: ActionRecord, expected_revision: int) -> ActionRecord:
		assert_opaque_credentials(record.input, context="action payload")
		return self._save("actions", record, expected_revision)
	def append_audit(self, record: AuditEvent) -> AuditEvent:
		def change(state: dict[str, Any]) -> AuditEvent:
			existing = state["audit"].get(record.id)
			if existing is not None:
				if existing == record.to_dict(): return AuditEvent.from_dict(existing)
				raise RuntimeConflictError("audit event id reused with different content")
			state["audit"][record.id] = record.to_dict()
			return record
		return self.mutate_project(record.tenant_id, record.environment_id, record.project_id, change)
	def list_audit(self, tenant_id: str, environment_id: str, project_id: str) -> list[AuditEvent]: return self._list("audit", tenant_id, environment_id, project_id)
	def append_telemetry(self, record: TelemetryEvent) -> TelemetryEvent: return self._create("telemetry", record)
	def list_telemetry(self, tenant_id: str, environment_id: str, project_id: str) -> list[TelemetryEvent]: return self._list("telemetry", tenant_id, environment_id, project_id)


FileRuntimeRepository = JsonRuntimeRepository
