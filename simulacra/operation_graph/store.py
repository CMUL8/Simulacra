from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from .codec import canonical_json_bytes, deterministic_json
from .errors import RevisionConflictError, RevisionNotFoundError, UnapprovedRevisionError
from .security import assert_connector_configurations_opaque
from .validation import validate_operation_graph

RECORD_SCHEMA_VERSION = "cmul8.operation-graph.store.v0"
_SCOPE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


def _utc_now() -> str:
	return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class GraphRevision:
	schema_version: str
	tenant_id: str
	project_id: str
	revision: int
	revision_hash: str
	created_at: str
	updated_at: str
	graph: dict[str, Any]


@dataclass(frozen=True)
class ApprovalRecord:
	schema_version: str
	approval_id: str
	tenant_id: str
	project_id: str
	revision: int
	revision_hash: str
	actor_id: str
	decision: str
	created_at: str
	updated_at: str


@dataclass(frozen=True)
class RollbackRecord:
	schema_version: str
	rollback_id: str
	tenant_id: str
	project_id: str
	revision: int
	from_revision_hash: str
	target_revision_hash: str
	actor_id: str
	reason: str
	created_at: str
	updated_at: str


class OperationGraphStore:
	"""Project-scoped filesystem store for immutable graphs and exact approvals."""

	def __init__(
		self,
		project_root: str | Path,
		*,
		tenant_id: str,
		project_id: str,
		clock: Callable[[], str] = _utc_now,
	) -> None:
		for label, value in (("tenant_id", tenant_id), ("project_id", project_id)):
			if not _SCOPE_RE.fullmatch(value):
				raise ValueError(f"{label} contains unsafe path characters")
		root = Path(project_root).resolve()
		if not root.is_dir():
			raise ValueError(f"project_root must be an existing directory: {root}")
		self.project_root = root
		self.tenant_id = tenant_id
		self.project_id = project_id
		self._clock = clock
		self._root = root / ".simulacra" / "operation-graph"
		self._prepare_directory(self._root)
		self._revisions = self._root / "revisions"
		self._approvals = self._root / "approvals"
		self._rollbacks = self._root / "rollbacks"
		for directory in (self._revisions, self._approvals, self._rollbacks):
			self._prepare_directory(directory)

	def _prepare_directory(self, path: Path) -> None:
		current = self.project_root
		for part in path.relative_to(self.project_root).parts:
			current = current / part
			if current.is_symlink():
				raise ValueError(f"Operation Graph storage path may not contain symlinks: {current}")
			current.mkdir(exist_ok=True)
		resolved = path.resolve()
		if resolved != self.project_root and self.project_root not in resolved.parents:
			raise ValueError("Operation Graph storage escaped project_root")

	@contextmanager
	def _locked(self) -> Iterator[None]:
		lock_path = self._root / ".lock"
		with lock_path.open("a+b") as handle:
			fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
			try:
				yield
			finally:
				fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

	def _read_json(self, path: Path) -> dict[str, Any]:
		with path.open("r", encoding="utf-8") as handle:
			value = json.load(handle)
		if not isinstance(value, dict):
			raise ValueError(f"Invalid persisted Operation Graph record: {path.name}")
		if value.get("tenant_id") != self.tenant_id or value.get("project_id") != self.project_id:
			raise ValueError(f"Persisted Operation Graph scope mismatch: {path.name}")
		return value

	def _atomic_write(self, path: Path, value: Mapping[str, Any]) -> None:
		fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
		try:
			with os.fdopen(fd, "w", encoding="utf-8") as handle:
				handle.write(deterministic_json(value, indent=2))
				handle.flush()
				os.fsync(handle.fileno())
			os.replace(temp_name, path)
			try:
				directory_fd = os.open(path.parent, os.O_RDONLY)
				try:
					os.fsync(directory_fd)
				finally:
					os.close(directory_fd)
			except OSError:
				pass
		finally:
			if os.path.exists(temp_name):
				os.unlink(temp_name)

	def _fsync_directory(self, directory: Path) -> None:
		try:
			directory_fd = os.open(directory, os.O_RDONLY)
			try:
				os.fsync(directory_fd)
			finally:
				os.close(directory_fd)
		except OSError:
			pass

	def _write_immutable(self, path: Path, value: Mapping[str, Any]) -> None:
		payload = deterministic_json(value, indent=2)
		fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
		try:
			with os.fdopen(fd, "w", encoding="utf-8") as handle:
				handle.write(payload)
				handle.flush()
				os.fsync(handle.fileno())
			try:
				os.link(temp_name, path)
			except FileExistsError:
				if path.read_text(encoding="utf-8") != payload:
					raise ValueError(f"Immutable record collision: {path.name}")
				return
			self._fsync_directory(path.parent)
		finally:
			if os.path.exists(temp_name):
				os.unlink(temp_name)
				self._fsync_directory(path.parent)

	def _head(self) -> dict[str, Any] | None:
		path = self._root / "head.json"
		return self._read_json(path) if path.exists() else None

	def _assert_expected(self, expected_revision_hash: str | None) -> dict[str, Any] | None:
		head = self._head()
		actual = head.get("revision_hash") if head else None
		if actual != expected_revision_hash:
			raise RevisionConflictError(f"stale Operation Graph head: expected {expected_revision_hash!r}, found {actual!r}")
		return head

	def _next_revision(self) -> int:
		revisions = self.list_revisions()
		rollbacks = self.list_rollbacks()
		return max([record.revision for record in revisions] + [record.revision for record in rollbacks] + [0]) + 1

	def create_revision(
		self,
		graph: Mapping[str, Any],
		*,
		expected_revision_hash: str | None,
	) -> GraphRevision:
		validated = validate_operation_graph(graph)
		# Check before acquiring the lock or calculating/writing immutable bytes.  This
		# keeps credential-like values out of revision and head records entirely.
		assert_connector_configurations_opaque(validated)
		metadata = validated["metadata"]
		if metadata["tenant_id"] != self.tenant_id or metadata["project_id"] != self.project_id:
			raise ValueError("graph tenant_id/project_id does not match store scope")
		revision_hash = hashlib.sha256(canonical_json_bytes(validated)).hexdigest()
		with self._locked():
			head = self._assert_expected(expected_revision_hash)
			existing_path = self._revisions / f"{revision_hash}.json"
			if existing_path.exists():
				record = self.load_revision(revision_hash)
				if head is None or head["revision_hash"] != revision_hash:
					raise RevisionConflictError(
						"historical Operation Graph revisions must be activated with rollback_to so the change is audited"
					)
				return record
			else:
				now = self._clock()
				record = GraphRevision(
					schema_version=RECORD_SCHEMA_VERSION,
					tenant_id=self.tenant_id,
					project_id=self.project_id,
					revision=self._next_revision(),
					revision_hash=revision_hash,
					created_at=now,
					updated_at=now,
					graph=validated,
				)
				self._write_immutable(existing_path, asdict(record))
			head = {
				"schema_version": RECORD_SCHEMA_VERSION,
				"tenant_id": self.tenant_id,
				"project_id": self.project_id,
				"revision": record.revision,
				"revision_hash": record.revision_hash,
				"created_at": record.created_at,
				"updated_at": self._clock(),
			}
			self._atomic_write(self._root / "head.json", head)
		return record

	def list_revisions(self) -> list[GraphRevision]:
		records = [self.load_revision(path.stem) for path in self._revisions.glob("*.json")]
		return sorted(records, key=lambda record: record.revision)

	def load_revision(self, revision_hash: str) -> GraphRevision:
		if not _HASH_RE.fullmatch(revision_hash):
			raise RevisionNotFoundError(f"invalid Operation Graph revision hash {revision_hash!r}")
		path = self._revisions / f"{revision_hash}.json"
		if not path.is_file():
			raise RevisionNotFoundError(f"Operation Graph revision not found: {revision_hash}")
		record = GraphRevision(**self._read_json(path))
		actual_hash = hashlib.sha256(canonical_json_bytes(record.graph)).hexdigest()
		if record.revision_hash != revision_hash or actual_hash != revision_hash:
			raise ValueError(f"Immutable Operation Graph revision failed content hash verification: {revision_hash}")
		# This check is deliberately on the central load path so legacy unsafe
		# revisions cannot be listed, activated, approved, rolled back to, or built.
		assert_connector_configurations_opaque(record.graph)
		return record

	def current_revision(self) -> GraphRevision | None:
		head = self._head()
		return self.load_revision(head["revision_hash"]) if head else None

	def approve_revision(self, revision_hash: str, *, actor_id: str) -> ApprovalRecord:
		if not actor_id.strip():
			raise ValueError("approval actor_id must be explicit")
		revision = self.load_revision(revision_hash)
		validate_operation_graph(revision.graph)
		now = self._clock()
		record = ApprovalRecord(
			schema_version=RECORD_SCHEMA_VERSION,
			approval_id=f"approval_{uuid.uuid4().hex}",
			tenant_id=self.tenant_id,
			project_id=self.project_id,
			revision=revision.revision,
			revision_hash=revision_hash,
			actor_id=actor_id,
			decision="approved",
			created_at=now,
			updated_at=now,
		)
		self._write_immutable(self._approvals / f"{record.approval_id}.json", asdict(record))
		return record

	def list_approvals(self, revision_hash: str | None = None) -> list[ApprovalRecord]:
		records = [ApprovalRecord(**self._read_json(path)) for path in self._approvals.glob("*.json")]
		if revision_hash is not None:
			records = [record for record in records if record.revision_hash == revision_hash]
		return sorted(records, key=lambda record: (record.created_at, record.approval_id))

	def require_approved_revision(self, revision_hash: str) -> GraphRevision:
		revision = self.load_revision(revision_hash)
		validate_operation_graph(revision.graph)
		approvals = self.list_approvals(revision_hash)
		if not approvals:
			raise UnapprovedRevisionError(f"Operation Graph revision is not approved exactly: {revision_hash}")
		if any(record.revision != revision.revision or record.decision != "approved" for record in approvals):
			raise ValueError(f"Invalid approval metadata for Operation Graph revision: {revision_hash}")
		return revision

	def rollback_to(
		self,
		target_revision_hash: str,
		*,
		expected_revision_hash: str,
		actor_id: str,
		reason: str,
	) -> RollbackRecord:
		if not actor_id.strip() or not reason.strip():
			raise ValueError("rollback actor_id and reason must be explicit")
		target = self.load_revision(target_revision_hash)
		with self._locked():
			head = self._assert_expected(expected_revision_hash)
			if head is None:
				raise RevisionConflictError("cannot roll back an empty Operation Graph store")
			now = self._clock()
			record = RollbackRecord(
				schema_version=RECORD_SCHEMA_VERSION,
				rollback_id=f"rollback_{uuid.uuid4().hex}",
				tenant_id=self.tenant_id,
				project_id=self.project_id,
				revision=self._next_revision(),
				from_revision_hash=head["revision_hash"],
				target_revision_hash=target_revision_hash,
				actor_id=actor_id,
				reason=reason,
				created_at=now,
				updated_at=now,
			)
			self._write_immutable(self._rollbacks / f"{record.rollback_id}.json", asdict(record))
			new_head = {
				"schema_version": RECORD_SCHEMA_VERSION,
				"tenant_id": self.tenant_id,
				"project_id": self.project_id,
				"revision": record.revision,
				"revision_hash": target.revision_hash,
				"created_at": head["created_at"],
				"updated_at": now,
			}
			self._atomic_write(self._root / "head.json", new_head)
		return record

	def list_rollbacks(self) -> list[RollbackRecord]:
		records = [RollbackRecord(**self._read_json(path)) for path in self._rollbacks.glob("*.json")]
		return sorted(records, key=lambda record: record.revision)
