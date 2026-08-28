"""Repository contract and deterministic project-scoped JSON/JSONL backend."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import stat
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Protocol, TypeVar

from .errors import AuthorizationError, ConflictError, NotFoundError, ScopeError, ValidationError
from .models import ActorType, Comment, DomainEvent, Invitation, Member, ProjectRoom, Review, ReviewDecision, Task, TaskState, iso_now, validate_scope_id

Record = TypeVar("Record", ProjectRoom, Task, Comment, Review)
T = TypeVar("T", bound=object)
_LOCKS_GUARD = threading.Lock()
_ROOT_LOCKS: dict[str, threading.RLock] = {}


class CollaborationRepository(Protocol):
	def create_room(self, room: ProjectRoom) -> ProjectRoom: ...
	def get_room(self, tenant_id: str, project_id: str) -> ProjectRoom: ...
	def room_lock(self, tenant_id: str, project_id: str): ...
	def save_room(self, room: ProjectRoom, expected_revision: int) -> ProjectRoom: ...
	def create_task(self, task: Task) -> Task: ...
	def get_task(self, tenant_id: str, project_id: str, task_id: str) -> Task: ...
	def list_tasks(self, tenant_id: str, project_id: str) -> list[Task]: ...
	def save_task(self, task: Task, expected_revision: int) -> Task: ...
	def create_comment(self, comment: Comment) -> Comment: ...
	def list_comments(self, tenant_id: str, project_id: str) -> list[Comment]: ...
	def create_review(self, review: Review) -> Review: ...
	def list_reviews(self, tenant_id: str, project_id: str, task_id: str | None = None) -> list[Review]: ...
	def commit_task_review(
		self, review: Review, *, expected_task_revision: int,
		expected_task_state: TaskState, allowed_reviewer_roles: frozenset[str],
	) -> tuple[Review, Task]: ...
	def append_event(self, event: DomainEvent) -> DomainEvent: ...
	def list_events(self, tenant_id: str, project_id: str) -> list[DomainEvent]: ...
	def get_inbox_state(self, tenant_id: str, project_id: str, actor_id: str) -> dict[str, Any]: ...
	def save_inbox_state(
		self, tenant_id: str, project_id: str, actor_id: str, *, last_read_position: int, updated_at: str
	) -> dict[str, Any]: ...
	def conversation_state(self, tenant_id: str, project_id: str) -> dict[str, Any]: ...
	def mutate_conversation_state(self, tenant_id: str, project_id: str, callback: Callable[[dict[str, Any]], T]) -> T: ...
	def member_project_ids(self, tenant_id: str, actor_id: str) -> list[str]: ...
	def visible_members(self, room: ProjectRoom) -> list[Member]: ...
	def visible_member(self, room: ProjectRoom, actor_id: str) -> Member | None: ...
	def get_invitation(self, tenant_id: str, project_id: str, invitation_id: str) -> Invitation: ...
	def save_invitation(self, invitation: Invitation, expected_revision: int) -> Invitation: ...
	def revoke_pending_invitation(
		self, *, tenant_id: str, project_id: str, actor_id: str, invitation_id: str,
		client_request_id: str, expected_revision: int,
	) -> tuple[Invitation, bool]: ...
	def remove_room_member_idempotent(
		self, *, tenant_id: str, project_id: str, actor_id: str, member_id: str,
		client_request_id: str, expected_room_revision: int,
	) -> tuple[ProjectRoom, bool]: ...


class JsonCollaborationRepository:
	"""Filesystem backend with one isolated directory per tenant/project.

	JSON record files use fsync + atomic replacement. Domain events use an
	append-only JSONL log and are idempotent by event id.
	"""

	_FILES = {"tasks": Task, "comments": Comment, "reviews": Review}
	_REVIEW_TRANSACTION_FILE = "review_transactions.json"
	_REVIEW_TRANSACTION_SCHEMA = 1
	_MAX_REVIEW_TRANSACTIONS = 1_000
	_MAX_REVIEW_TRANSACTION_BYTES = 64 * 1024

	def __init__(self, root: str | Path):
		self.root = Path(root).resolve()
		self.root.mkdir(parents=True, exist_ok=True)
		with _LOCKS_GUARD:
			self._lock = _ROOT_LOCKS.setdefault(str(self.root), threading.RLock())
		self._write_depth = threading.local()

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

	def _lock_path(self, tenant_id: str, project_id: str) -> Path:
		validate_scope_id(tenant_id, "tenant_id")
		validate_scope_id(project_id, "project_id")
		lock_root = (self.root / ".collaboration-locks").resolve()
		try:
			lock_root.relative_to(self.root)
		except ValueError as exc:
			raise ScopeError("lock directory escapes repository root") from exc
		lock_root.mkdir(parents=True, exist_ok=True)
		lock_dir = (lock_root / tenant_id).resolve()
		try:
			lock_dir.relative_to(lock_root)
		except ValueError as exc:
			raise ScopeError("tenant lock directory escapes repository root") from exc
		lock_dir.mkdir(parents=True, exist_ok=True)
		path = (lock_dir / f"{project_id}.lock").resolve()
		try:
			path.relative_to(lock_dir)
		except ValueError as exc:
			raise ScopeError("project lock path escapes repository root") from exc
		return path

	@contextmanager
	def _write_lock(self, tenant_id: str, project_id: str):
		"""Serialize complete project writes across threads and POSIX processes."""
		key = (tenant_id, project_id)
		depths = getattr(self._write_depth, "depths", {})
		if depths.get(key, 0):
			depths[key] += 1
			self._write_depth.depths = depths
			try:
				yield
			finally:
				depths[key] -= 1
			return
		with self._lock:
			path = self._lock_path(tenant_id, project_id)
			flags = os.O_CREAT | os.O_RDWR
			if hasattr(os, "O_NOFOLLOW"):
				flags |= os.O_NOFOLLOW
			try:
				fd = os.open(path, flags, 0o600)
			except OSError as exc:
				raise ScopeError("unable to open project collaboration lock") from exc
			try:
				fcntl.flock(fd, fcntl.LOCK_EX)
				depths[key] = 1
				self._write_depth.depths = depths
				try:
					yield
				finally:
					depths.pop(key, None)
			finally:
				fcntl.flock(fd, fcntl.LOCK_UN)
				os.close(fd)

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

	@staticmethod
	def _empty_conversation_state() -> dict[str, Any]:
		return {
			"schema_version": 1, "messages": {}, "message_audits": {}, "reactions": {},
			"saved_references": {}, "idempotency": {}, "attention_receipts": {}, "wake_events": {},
		}

	def _load_conversation_state(self, tenant_id: str, project_id: str) -> dict[str, Any]:
		path = self._project_dir(tenant_id, project_id) / "conversation_state.json"
		state = self._read_json(path, self._empty_conversation_state())
		if not isinstance(state, dict):
			raise ScopeError("invalid conversation state")
		if state.setdefault("schema_version", 1) != 1:
			raise ScopeError("unsupported conversation state schema")
		for key in ("messages", "message_audits", "reactions", "saved_references", "idempotency", "attention_receipts", "wake_events"):
			state.setdefault(key, {})
			if not isinstance(state[key], dict):
				raise ScopeError("invalid conversation state")
		return state

	def _replace_conversation_state(self, tenant_id: str, project_id: str, state: dict[str, Any]) -> None:
		"""The sole conversation publish operation, always under the room lock."""
		directory = self._project_dir(tenant_id, project_id, create=True)
		path = directory / "conversation_state.json"
		tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
		fault = getattr(self, "conversation_fault", None)
		def checkpoint(stage: str) -> None:
			if fault is not None:
				fault(stage)
		replaced = False
		try:
			checkpoint("before_write")
			encoded = json.dumps(state, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
			with tmp.open("w", encoding="utf-8") as handle:
				handle.write(encoded)
				handle.flush()
				checkpoint("before_temp_fsync")
				os.fsync(handle.fileno())
			checkpoint("after_temp_fsync")
			checkpoint("before_replace")
			os.replace(tmp, path)
			replaced = True
			checkpoint("after_replace")
			dir_fd = os.open(directory, os.O_RDONLY)
			try:
				checkpoint("before_parent_fsync")
				os.fsync(dir_fd)
				checkpoint("after_parent_fsync")
			finally:
				os.close(dir_fd)
		finally:
			if not replaced:
				try:
					tmp.unlink(missing_ok=True)
				except OSError:
					pass

	def conversation_state(self, tenant_id: str, project_id: str) -> dict[str, Any]:
		self.get_room(tenant_id, project_id)
		return self._load_conversation_state(tenant_id, project_id)

	def mutate_conversation_state(self, tenant_id: str, project_id: str, callback: Callable[[dict[str, Any]], T]) -> T:
		with self._write_lock(tenant_id, project_id):
			self.get_room(tenant_id, project_id)
			state = self._load_conversation_state(tenant_id, project_id)
			result = callback(state)
			self._replace_conversation_state(tenant_id, project_id, state)
			return result

	def member_project_ids(self, tenant_id: str, actor_id: str) -> list[str]:
		"""Return only descriptor-safe projects where the actor is a current member."""
		validate_scope_id(tenant_id, "tenant_id")
		validate_scope_id(actor_id, "actor_id")
		tenant_root = (self.root / tenant_id).resolve()
		try:
			tenant_root.relative_to(self.root)
		except ValueError as exc:
			raise ScopeError("tenant path escapes repository root") from exc
		if not tenant_root.exists() or not tenant_root.is_dir():
			return []
		result: list[str] = []
		for child in sorted(tenant_root.iterdir(), key=lambda item: item.name):
			if not child.is_dir() or child.is_symlink():
				continue
			try:
				validate_scope_id(child.name, "project_id")
				room = self.get_room(tenant_id, child.name)
			except (NotFoundError, ScopeError):
				continue
			if any(member.actor_id == actor_id and self.member_is_visible(tenant_id, child.name, member) for member in room.members):
				result.append(child.name)
		return result

	def member_is_visible(self, tenant_id: str, project_id: str, member: Any) -> bool:
		transaction_id = getattr(member, "transaction_id", None)
		if transaction_id is None:
			return True
		if getattr(member, "visibility_state", "committed") != "committed":
			return False
		try:
			from .invitation_acceptance import is_acceptance_complete
			return is_acceptance_complete(tenant_id, project_id, transaction_id, root=self.root)
		except Exception:
			return False

	def visible_members(self, room: ProjectRoom) -> list[Member]:
		"""The single authorization/read filter for recoverable room membership."""
		return [
			member for member in room.members
			if self.member_is_visible(room.tenant_id, room.project_id, member)
		]

	def visible_member(self, room: ProjectRoom, actor_id: str) -> Member | None:
		return next((member for member in self.visible_members(room) if member.actor_id == actor_id), None)

	def visible_room(self, tenant_id: str, project_id: str) -> ProjectRoom:
		"""Public/authorization read; raw writes deliberately use ``get_room``."""
		room = self.get_room(tenant_id, project_id)
		return replace(room, members=self.visible_members(room))

	def create_room(self, room: ProjectRoom) -> ProjectRoom:
		with self._write_lock(room.tenant_id, room.project_id):
			self._assert_record_id(room, "room")
			path = self._project_dir(room.tenant_id, room.project_id, create=True) / "room.json"
			if path.exists():
				raise ConflictError("project room already exists")
			self._atomic_json(path, room.to_dict())
			return room

	def _invitation_path(self, tenant_id: str, project_id: str, invitation_id: str, *, create: bool = False) -> Path:
		validate_scope_id(invitation_id, "invitation_id")
		directory = self._project_dir(tenant_id, project_id, create=create) / "invitations"
		if create:
			directory.mkdir(parents=True, exist_ok=True)
		return directory / f"{invitation_id}.json"

	def create_invitation(self, invitation: Invitation) -> Invitation:
		with self._write_lock(invitation.tenant_id, invitation.project_id):
			path = self._invitation_path(invitation.tenant_id, invitation.project_id, invitation.id, create=True)
			if path.exists():
				raise ConflictError("invitation already exists")
			self._atomic_json(path, invitation.to_dict())
			return invitation

	def get_invitation(self, tenant_id: str, project_id: str, invitation_id: str) -> Invitation:
		with self._write_lock(tenant_id, project_id):
			path = self._invitation_path(tenant_id, project_id, invitation_id)
			if not path.exists():
				raise NotFoundError("invitation not found")
		return Invitation.from_dict(self._read_json(path, {}))

	def save_invitation(self, invitation: Invitation, expected_revision: int) -> Invitation:
		with self._write_lock(invitation.tenant_id, invitation.project_id):
			path = self._invitation_path(invitation.tenant_id, invitation.project_id, invitation.id)
			if not path.exists():
				raise NotFoundError("invitation not found")
			current = Invitation.from_dict(self._read_json(path, {}))
			if current.revision != expected_revision:
				raise ConflictError("stale invitation revision")
			self._atomic_json(path, invitation.to_dict())
			return invitation

	@staticmethod
	def _mutation_identity(
		tenant_id: str, project_id: str, actor_id: str, operation: str, client_request_id: str,
	) -> str:
		for value, label in ((tenant_id, "tenant_id"), (project_id, "project_id"), (actor_id, "actor_id")):
			validate_scope_id(value, label)
		if not isinstance(client_request_id, str) or not client_request_id or len(client_request_id) > 128:
			raise ValidationError("invalid client_request_id")
		encoded = json.dumps(
			[tenant_id, project_id, actor_id, operation, client_request_id],
			ensure_ascii=False, separators=(",", ":"),
		).encode("utf-8")
		return hashlib.sha256(encoded).hexdigest()

	@staticmethod
	def _mutation_body_hash(value: dict[str, Any]) -> str:
		encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
		return hashlib.sha256(encoded).hexdigest()

	@staticmethod
	def _authorized_room_actor(room: ProjectRoom, actor_id: str, repository: "JsonCollaborationRepository") -> Member:
		actor = repository.visible_member(room, actor_id)
		if actor is None or actor.role not in {"owner", "admin"}:
			raise AuthorizationError("only owners and admins can manage Mission membership")
		return actor

	def _project_invitations(self, tenant_id: str, project_id: str) -> list[Invitation]:
		directory = self._project_dir(tenant_id, project_id) / "invitations"
		if not directory.exists():
			return []
		if directory.is_symlink() or not directory.is_dir():
			raise ScopeError("invalid invitation store")
		result: list[Invitation] = []
		for path in sorted(directory.iterdir(), key=lambda item: item.name):
			if path.is_symlink() or not path.is_file() or path.suffix != ".json":
				continue
			validate_scope_id(path.stem, "invitation_id")
			invitation = Invitation.from_dict(self._read_json(path, {}))
			self._assert_scope(invitation, tenant_id, project_id)
			if invitation.id != path.stem:
				raise ScopeError("invitation identity conflict")
			result.append(invitation)
		return result

	@staticmethod
	def _replay_receipt(
		receipt: Any, *, operation: str, actor_id: str, client_request_id: str, body_hash: str,
	) -> dict[str, Any]:
		if not isinstance(receipt, dict) or receipt.get("canonical_body_hash") != body_hash:
			raise ConflictError("idempotency_mismatch")
		if (
			receipt.get("operation") != operation
			or receipt.get("authenticated_human_actor_id") != actor_id
			or receipt.get("client_request_id") != client_request_id
			or not isinstance(receipt.get("result_snapshot"), dict)
		):
			raise ConflictError("idempotency_corrupt")
		return dict(receipt["result_snapshot"])

	def revoke_pending_invitation(
		self, *, tenant_id: str, project_id: str, actor_id: str, invitation_id: str,
		client_request_id: str, expected_revision: int,
	) -> tuple[Invitation, bool]:
		"""Revoke one pending invitation with a project-wide request identity."""
		validate_scope_id(invitation_id, "invitation_id")
		operation = "invitation_revoke"
		identity = self._mutation_identity(tenant_id, project_id, actor_id, operation, client_request_id)
		body_hash = self._mutation_body_hash({
			"method": "POST", "invitation_id": invitation_id, "expected_revision": expected_revision,
		})
		with self._write_lock(tenant_id, project_id):
			room = self.get_room(tenant_id, project_id)
			self._authorized_room_actor(room, actor_id, self)
			invitations = self._project_invitations(tenant_id, project_id)
			for invitation in invitations:
				prior = invitation.mutation_receipts.get(identity)
				if prior is None:
					continue
				snapshot = self._replay_receipt(
					prior, operation=operation, actor_id=actor_id,
					client_request_id=client_request_id, body_hash=body_hash,
				)
				if snapshot != {"id": invitation.id, "status": invitation.status, "revision": invitation.revision}:
					raise ConflictError("idempotency_corrupt")
				return invitation, False
			invitation = next((item for item in invitations if item.id == invitation_id), None)
			if invitation is None or invitation.status != "pending":
				raise NotFoundError("pending invitation not found")
			if invitation.revision != expected_revision:
				raise ConflictError("stale invitation revision")
			updated = replace(
				invitation, status="revoked", revision=invitation.revision + 1, updated_at=iso_now(),
				mutation_receipts={
					**invitation.mutation_receipts,
					identity: {
						"operation": operation,
						"authenticated_human_actor_id": actor_id,
						"client_request_id": client_request_id,
						"canonical_body_hash": body_hash,
						"result_snapshot": {
							"id": invitation.id, "status": "revoked", "revision": invitation.revision + 1,
						},
					},
				},
			)
			self._atomic_json(self._invitation_path(tenant_id, project_id, invitation_id), updated.to_dict())
			return updated, True

	def remove_room_member_idempotent(
		self, *, tenant_id: str, project_id: str, actor_id: str, member_id: str,
		client_request_id: str, expected_room_revision: int,
	) -> tuple[ProjectRoom, bool]:
		"""Commit membership removal and its replay result in the same room write."""
		validate_scope_id(member_id, "member_id")
		operation = "room_member_remove"
		identity = self._mutation_identity(tenant_id, project_id, actor_id, operation, client_request_id)
		body_hash = self._mutation_body_hash({
			"method": "POST", "member_id": member_id,
			"expected_room_revision": expected_room_revision,
		})
		with self._write_lock(tenant_id, project_id):
			room = self.get_room(tenant_id, project_id)
			self._authorized_room_actor(room, actor_id, self)
			prior = room.mutation_receipts.get(identity)
			if prior is not None:
				snapshot = self._replay_receipt(
					prior, operation=operation, actor_id=actor_id,
					client_request_id=client_request_id, body_hash=body_hash,
				)
				replayed = ProjectRoom.from_dict(snapshot)
				self._assert_scope(replayed, tenant_id, project_id)
				return replayed, False
			visible_members = self.visible_members(room)
			target = next((item for item in visible_members if item.actor_id == member_id), None)
			if target is None:
				raise NotFoundError("Mission member not found")
			if room.revision != expected_room_revision:
				raise ConflictError("stale room revision")
			if target.role == "owner" and sum(item.role == "owner" for item in visible_members) <= 1:
				raise AuthorizationError("cannot remove the last owner")
			result = replace(
				room, members=[item for item in room.members if item.actor_id != member_id],
				revision=room.revision + 1, updated_at=iso_now(), mutation_receipts={},
			)
			receipt = {
				"operation": operation,
				"authenticated_human_actor_id": actor_id,
				"client_request_id": client_request_id,
				"canonical_body_hash": body_hash,
				"result_snapshot": result.to_dict(),
			}
			stored = replace(result, mutation_receipts={**room.mutation_receipts, identity: receipt})
			self._atomic_json(self._project_dir(tenant_id, project_id) / "room.json", stored.to_dict())
			return result, True

	def get_room(self, tenant_id: str, project_id: str) -> ProjectRoom:
		path = self._project_dir(tenant_id, project_id) / "room.json"
		if not path.exists():
			raise NotFoundError("project room not found")
		room = ProjectRoom.from_dict(self._read_json(path, {}))
		self._assert_scope(room, tenant_id, project_id)
		return room

	@contextmanager
	def room_lock(self, tenant_id: str, project_id: str):
		"""Hold the durable room lock while an external mutation is committed."""
		with self._write_lock(tenant_id, project_id):
			yield self.get_room(tenant_id, project_id)

	def save_room(self, room: ProjectRoom, expected_revision: int) -> ProjectRoom:
		with self._write_lock(room.tenant_id, room.project_id):
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

	def _review_transaction_path(self, tenant_id: str, project_id: str, *, create: bool = False) -> Path:
		return self._project_dir(tenant_id, project_id, create=create) / self._REVIEW_TRANSACTION_FILE

	@staticmethod
	def _read_regular_json(path: Path, default: Any) -> Any:
		"""Read a recovery record only from a regular, non-symlink descriptor."""
		flags = os.O_RDONLY
		if hasattr(os, "O_NOFOLLOW"):
			flags |= os.O_NOFOLLOW
		try:
			fd = os.open(path, flags)
		except FileNotFoundError:
			return default
		except OSError as exc:
			raise ScopeError("invalid review transaction store") from exc
		try:
			if not stat.S_ISREG(os.fstat(fd).st_mode):
				raise ScopeError("invalid review transaction store")
			with os.fdopen(fd, "r", encoding="utf-8") as handle:
				fd = -1
				return json.load(handle)
		except (json.JSONDecodeError, OSError) as exc:
			raise ScopeError("invalid review transaction store") from exc
		finally:
			if fd >= 0:
				os.close(fd)

	def _load_review_transactions(self, tenant_id: str, project_id: str) -> dict[str, dict[str, Any]]:
		rows = self._read_regular_json(self._review_transaction_path(tenant_id, project_id), {})
		if not isinstance(rows, dict) or len(rows) > self._MAX_REVIEW_TRANSACTIONS:
			raise ScopeError("invalid review transaction store")
		validated: dict[str, dict[str, Any]] = {}
		for transaction_id, row in rows.items():
			if not isinstance(transaction_id, str) or not isinstance(row, dict):
				raise ScopeError("invalid review transaction store")
			validate_scope_id(transaction_id, "review_id")
			if not transaction_id.startswith("review_"):
				raise ScopeError("invalid review transaction store")
			if set(row) != {"schema_version", "review", "task", "expected_task_revision", "expected_task_state"}:
				raise ScopeError("invalid review transaction store")
			if row["schema_version"] != self._REVIEW_TRANSACTION_SCHEMA or not isinstance(row["expected_task_revision"], int):
				raise ScopeError("invalid review transaction store")
			if row["expected_task_revision"] < 1 or not isinstance(row["review"], dict) or not isinstance(row["task"], dict):
				raise ScopeError("invalid review transaction store")
			try:
				if len(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")) > self._MAX_REVIEW_TRANSACTION_BYTES:
					raise ScopeError("invalid review transaction store")
				review = Review.from_dict(row["review"])
				task = Task.from_dict(row["task"])
				TaskState(row["expected_task_state"])
			except (TypeError, ValueError, ValidationError) as exc:
				raise ScopeError("invalid review transaction store") from exc
			self._assert_record_id(review, "review")
			self._assert_record_id(task, "task")
			self._assert_scope(review, tenant_id, project_id)
			self._assert_scope(task, tenant_id, project_id)
			if review.actor_type != ActorType.HUMAN:
				raise ScopeError("invalid review transaction store")
			if review.id != transaction_id or review.task_id != task.id or review.task_revision != row["expected_task_revision"]:
				raise ScopeError("invalid review transaction store")
			if task.revision != row["expected_task_revision"] + 1:
				raise ScopeError("invalid review transaction store")
			validated[transaction_id] = row
		return validated

	def _save_review_transactions(self, tenant_id: str, project_id: str, rows: dict[str, dict[str, Any]]) -> None:
		self._atomic_json(self._review_transaction_path(tenant_id, project_id, create=True), {
			key: rows[key] for key in sorted(rows)
		})

	def _review_checkpoint(self, stage: str) -> None:
		fault = getattr(self, "review_commit_fault", None)
		if fault is not None:
			fault(stage)
		hook = getattr(self, "review_commit_hook", None)
		if hook is not None:
			hook(stage)

	@staticmethod
	def _review_result_state(state: TaskState, decision: ReviewDecision) -> TaskState:
		if decision == ReviewDecision.ROLLBACK:
			if state != TaskState.DONE:
				raise ConflictError("rollback requires a done task")
			return TaskState.WORKING
		if state != TaskState.IN_REVIEW:
			raise ConflictError("review decisions require a task in review")
		if decision == ReviewDecision.APPROVE:
			return TaskState.DONE
		if decision == ReviewDecision.REJECT:
			return TaskState.FAILED
		if decision == ReviewDecision.REQUEST_CHANGES:
			return TaskState.WORKING
		return TaskState.IN_REVIEW

	@staticmethod
	def _review_activity(review: Review) -> dict[str, str]:
		return {
			"action": "reviewed", "review_id": review.id, "decision": review.decision.value,
			"actor_id": review.reviewer_id, "role": review.reviewer_role, "at": review.created_at,
		}

	def _derive_reviewed_task(self, current: Task, review: Review) -> Task:
		if current.id != review.task_id or current.revision != review.task_revision:
			raise ConflictError("review transaction task conflict")
		state = self._review_result_state(current.state, review.decision)
		return replace(
			current,
			state=state,
			revision=current.revision + 1,
			updated_at=review.created_at,
			activity=[*current.activity, self._review_activity(review)],
		)

	def _is_published_review_target(
		self, task: Task, review: Review, *, expected_task_revision: int, expected_task_state: TaskState,
	) -> bool:
		try:
			result_state = self._review_result_state(expected_task_state, review.decision)
		except ConflictError:
			return False
		return (
			task.id == review.task_id
			and task.revision == expected_task_revision + 1
			and task.state == result_state
			and task.updated_at == review.created_at
			and bool(task.activity)
			and task.activity[-1] == self._review_activity(review)
		)

	def _recover_task_reviews_locked(self, tenant_id: str, project_id: str) -> None:
		"""Finish a decision already made durable, or discard an uncommitted intent."""
		transactions = self._load_review_transactions(tenant_id, project_id)
		if not transactions:
			return
		tasks_path, task_rows = self._collection(tenant_id, project_id, "tasks")
		reviews_path, review_rows = self._collection(tenant_id, project_id, "reviews", create=True)
		transactions_changed = False
		tasks_changed = False
		for review_id in sorted(transactions):
			row = transactions[review_id]
			review = Review.from_dict(row["review"])
			target = Task.from_dict(row["task"])
			expected_revision = row["expected_task_revision"]
			expected_state = TaskState(row["expected_task_state"])
			stored_review = review_rows.get(review_id)
			if stored_review is None:
				# The review record is the decision boundary. An intent alone publishes nothing.
				del transactions[review_id]
				transactions_changed = True
				continue
			try:
				persisted_review = Review.from_dict(stored_review)
			except (TypeError, ValueError, ValidationError) as exc:
				raise ScopeError("invalid review store") from exc
			if persisted_review.to_dict() != review.to_dict():
				raise ConflictError("review transaction identity conflict")
			if target.id not in task_rows:
				raise NotFoundError("task not found")
			try:
				current = Task.from_dict(task_rows[target.id])
			except (TypeError, ValueError, ValidationError) as exc:
				raise ScopeError("invalid task store") from exc
			self._assert_scope(current, tenant_id, project_id)
			if current.revision == expected_revision and current.state == expected_state:
				derived = self._derive_reviewed_task(current, review)
				if target.to_dict() != derived.to_dict():
					raise ConflictError("review transaction task conflict")
				task_rows[target.id] = derived.to_dict()
				tasks_changed = True
			elif current.to_dict() == target.to_dict() and self._is_published_review_target(
				current, review, expected_task_revision=expected_revision, expected_task_state=expected_state,
			):
				pass
			else:
				raise ConflictError("review transaction task conflict")
			del transactions[review_id]
			transactions_changed = True
		if tasks_changed:
			self._atomic_json(tasks_path, {key: task_rows[key] for key in sorted(task_rows)})
		if transactions_changed:
			self._save_review_transactions(tenant_id, project_id, transactions)

	def _recover_task_reviews(self, tenant_id: str, project_id: str) -> None:
		with self._write_lock(tenant_id, project_id):
			self.get_room(tenant_id, project_id)
			self._recover_task_reviews_locked(tenant_id, project_id)

	def _create_record(self, name: str, record: Record) -> Record:
		with self._write_lock(record.tenant_id, record.project_id):
			if name in {"tasks", "reviews"}:
				self._recover_task_reviews_locked(record.tenant_id, record.project_id)
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
		validated: list[Record] = []
		for record in result:
			self._assert_scope(record, tenant_id, project_id)
			validated.append(record)
		return validated

	def _save_record(self, name: str, record: Record, expected_revision: int) -> Record:
		with self._write_lock(record.tenant_id, record.project_id):
			if name == "tasks":
				self._recover_task_reviews_locked(record.tenant_id, record.project_id)
			path, rows = self._collection(record.tenant_id, record.project_id, name)
			if record.id not in rows:
				raise NotFoundError(f"{name[:-1]} not found")
			current = self._FILES[name].from_dict(rows[record.id])
			self._assert_scope(current, record.tenant_id, record.project_id)
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
		member_ids = {member.actor_id for member in self.visible_members(room)}
		if task.owner_id is not None and task.owner_id not in member_ids:
			raise ScopeError("task owner is not a project room member")
		if not set(task.collaborator_ids).issubset(member_ids):
			raise ScopeError("task collaborator is not a project room member")
		return self._create_record("tasks", task)

	def get_task(self, tenant_id: str, project_id: str, task_id: str) -> Task:
		self._recover_task_reviews(tenant_id, project_id)
		self.get_room(tenant_id, project_id)
		return self._get_record("tasks", Task, tenant_id, project_id, task_id)

	def list_tasks(self, tenant_id: str, project_id: str) -> list[Task]:
		self._recover_task_reviews(tenant_id, project_id)
		self.get_room(tenant_id, project_id)
		return self._list_records("tasks", Task, tenant_id, project_id)

	def save_task(self, task: Task, expected_revision: int) -> Task:
		return self._save_record("tasks", task, expected_revision)

	def create_comment(self, comment: Comment) -> Comment:
		room = self.get_room(comment.tenant_id, comment.project_id)
		if self.visible_member(room, comment.author_id) is None:
			raise ScopeError("comment author is not a project room member")
		if comment.task_id is not None:
			self.get_task(comment.tenant_id, comment.project_id, comment.task_id)
		return self._create_record("comments", comment)

	def list_comments(self, tenant_id: str, project_id: str) -> list[Comment]:
		self.get_room(tenant_id, project_id)
		return self._list_records("comments", Comment, tenant_id, project_id)

	def create_review(self, review: Review) -> Review:
		room = self.get_room(review.tenant_id, review.project_id)
		if self.visible_member(room, review.reviewer_id) is None:
			raise ScopeError("reviewer is not a project room member")
		self.get_task(review.tenant_id, review.project_id, review.task_id)
		return self._create_record("reviews", review)

	def list_reviews(self, tenant_id: str, project_id: str, task_id: str | None = None) -> list[Review]:
		self._recover_task_reviews(tenant_id, project_id)
		self.get_room(tenant_id, project_id)
		rows = self._list_records("reviews", Review, tenant_id, project_id)
		return [row for row in rows if task_id is None or row.task_id == task_id]

	def commit_task_review(
		self, review: Review, *, expected_task_revision: int,
		expected_task_state: TaskState, allowed_reviewer_roles: frozenset[str],
	) -> tuple[Review, Task]:
		"""Commit one authorized human decision before its resulting task state.

		The review record is the durable decision boundary. A remaining transaction
		intent with no review record is discarded on recovery; a persisted review
		always converges to the exact target task before future task/review access.
		"""
		with self._write_lock(review.tenant_id, review.project_id):
			self._recover_task_reviews_locked(review.tenant_id, review.project_id)
			self._assert_record_id(review, "review")
			room = self.get_room(review.tenant_id, review.project_id)
			member = self.visible_member(room, review.reviewer_id)
			if member is None:
				raise AuthorizationError("reviewer is not a project room member")
			if review.actor_type != ActorType.HUMAN:
				raise AuthorizationError("task review requires a human reviewer")
			if member.role != review.reviewer_role or member.role not in allowed_reviewer_roles:
				raise AuthorizationError("task review requires an owner, admin, approver, or reviewer role")
			self._review_checkpoint("after_authority_recheck")
			tasks_path, task_rows = self._collection(review.tenant_id, review.project_id, "tasks")
			if review.task_id not in task_rows:
				raise NotFoundError("task not found")
			current = Task.from_dict(task_rows[review.task_id])
			self._assert_scope(current, review.tenant_id, review.project_id)
			if current.revision != expected_task_revision:
				raise ConflictError(
					f"stale task revision: expected {expected_task_revision}, current {current.revision}"
				)
			if current.state != expected_task_state:
				raise ConflictError("task review state changed")
			if current.owner_id == review.reviewer_id:
				raise AuthorizationError("task owner cannot review their own work")
			if review.task_revision != expected_task_revision:
				raise ConflictError("review transaction revision conflict")
			updated_task = self._derive_reviewed_task(current, review)
			reviews_path, review_rows = self._collection(review.tenant_id, review.project_id, "reviews", create=True)
			if review.id in review_rows:
				raise ConflictError("review already exists")
			if any(
				row.get("task_id") == review.task_id and row.get("task_revision") == expected_task_revision
				for row in review_rows.values()
			):
				raise ConflictError("task revision already has a review")
			transactions = self._load_review_transactions(review.tenant_id, review.project_id)
			if review.id in transactions:
				raise ConflictError("review transaction already exists")
			if len(transactions) >= self._MAX_REVIEW_TRANSACTIONS:
				raise ConflictError("too many pending review transactions")
			intent = {
				"schema_version": self._REVIEW_TRANSACTION_SCHEMA,
				"review": review.to_dict(),
				"task": updated_task.to_dict(),
				"expected_task_revision": expected_task_revision,
				"expected_task_state": expected_task_state.value,
			}
			self._review_checkpoint("before_intent")
			transactions[review.id] = intent
			self._save_review_transactions(review.tenant_id, review.project_id, transactions)
			self._review_checkpoint("after_intent")
			review_rows[review.id] = review.to_dict()
			self._atomic_json(reviews_path, {key: review_rows[key] for key in sorted(review_rows)})
			self._review_checkpoint("after_review")
			task_rows[updated_task.id] = updated_task.to_dict()
			self._atomic_json(tasks_path, {key: task_rows[key] for key in sorted(task_rows)})
			self._review_checkpoint("after_task")
			del transactions[review.id]
			self._save_review_transactions(review.tenant_id, review.project_id, transactions)
			self._review_checkpoint("after_cleanup")
			return review, updated_task

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
		with self._write_lock(event.tenant_id, event.project_id):
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
		if self.visible_member(room, actor_id) is None:
			raise AuthorizationError("actor is not a project room member")
		path = self._project_dir(tenant_id, project_id) / "inbox_state.json"
		states = self._read_json(path, {})
		return dict(states.get(actor_id, {"last_read_position": 0, "updated_at": None}))

	def save_inbox_state(
		self, tenant_id: str, project_id: str, actor_id: str, *, last_read_position: int, updated_at: str
	) -> dict[str, Any]:
		validate_scope_id(actor_id, "actor_id")
		if last_read_position < 0:
			raise ConflictError("read position cannot be negative")
		with self._write_lock(tenant_id, project_id):
			room = self.get_room(tenant_id, project_id)
			if self.visible_member(room, actor_id) is None:
				raise AuthorizationError("actor is not a project room member")
			path = self._project_dir(tenant_id, project_id, create=True) / "inbox_state.json"
			states = self._read_json(path, {})
			current = int(states.get(actor_id, {}).get("last_read_position", 0))
			if last_read_position < current:
				raise ConflictError("read position cannot move backwards")
			states[actor_id] = {"last_read_position": last_read_position, "updated_at": updated_at}
			self._atomic_json(path, {key: states[key] for key in sorted(states)})
		return dict(states[actor_id])
