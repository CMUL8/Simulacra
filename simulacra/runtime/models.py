"""Serializable records used by the independent runtime plane."""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping

SCHEMA_VERSION = "cmul8.runtime.v0"
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")


def utc_now() -> str:
	return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def new_id(prefix: str) -> str:
	return f"{prefix}_{uuid.uuid4().hex}"


def validate_scope(value: str, label: str) -> str:
	if not isinstance(value, str) or not _SAFE_ID.fullmatch(value):
		raise ValueError(f"{label} contains unsafe path characters")
	return value


class RecordMixin:
	def to_dict(self) -> dict[str, Any]:
		return copy.deepcopy(asdict(self))

	@classmethod
	def from_dict(cls, value: Mapping[str, Any]):
		return cls(**copy.deepcopy(dict(value)))


@dataclass(frozen=True)
class EntityRecord(RecordMixin):
	id: str
	tenant_id: str
	environment_id: str
	project_id: str
	entity_type: str
	data: dict[str, Any]
	operation_graph_version: str
	revision: int = 0
	created_at: str = field(default_factory=utc_now)
	updated_at: str = field(default_factory=utc_now)
	schema_version: str = SCHEMA_VERSION

	@property
	def entity_id(self) -> str:
		return self.entity_type


@dataclass(frozen=True)
class WorkflowInstance(RecordMixin):
	id: str
	tenant_id: str
	environment_id: str
	project_id: str
	workflow_id: str
	state: str
	operation_graph_version: str
	entity_record_id: str | None = None
	context: dict[str, Any] = field(default_factory=dict)
	revision: int = 0
	created_at: str = field(default_factory=utc_now)
	updated_at: str = field(default_factory=utc_now)
	schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class HumanTask(RecordMixin):
	id: str
	tenant_id: str
	environment_id: str
	project_id: str
	title: str
	status: str = "open"
	assignee_id: str | None = None
	payload: dict[str, Any] = field(default_factory=dict)
	revision: int = 0
	created_at: str = field(default_factory=utc_now)
	updated_at: str = field(default_factory=utc_now)
	# Human work is governed by the exact graph revision that created it.
	operation_graph_version: str = ""
	schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class ApprovalDecision(RecordMixin):
	actor_id: str
	decision: str
	decided_at: str
	reason: str = ""


@dataclass(frozen=True)
class ApprovalRequest(RecordMixin):
	id: str
	tenant_id: str
	environment_id: str
	project_id: str
	action: str
	requester_id: str
	status: str = "pending"
	approvals_required: int = 1
	allow_self_approval: bool = False
	payload: dict[str, Any] = field(default_factory=dict)
	decisions: list[dict[str, Any]] = field(default_factory=list)
	expires_at: str | None = None
	revision: int = 0
	created_at: str = field(default_factory=utc_now)
	updated_at: str = field(default_factory=utc_now)
	# Approval decisions are governed by the graph revision that created the
	# request.  A newer worker must not decide an older revision's policy.
	operation_graph_version: str = ""
	schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class ScheduledJob(RecordMixin):
	id: str
	tenant_id: str
	environment_id: str
	project_id: str
	kind: str
	payload: dict[str, Any]
	run_at: str
	status: str = "queued"
	attempts: int = 0
	max_attempts: int = 3
	lease_owner: str | None = None
	lease_until: str | None = None
	last_error: str | None = None
	idempotency_key: str | None = None
	# The revision is copied from the verified runtime policy at admission time.
	# A worker must never run a durable job under a different graph revision.
	operation_graph_version: str = ""
	revision: int = 0
	created_at: str = field(default_factory=utc_now)
	updated_at: str = field(default_factory=utc_now)
	schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class ActionRecord(RecordMixin):
	id: str
	tenant_id: str
	environment_id: str
	project_id: str
	connector_id: str
	operation: str
	idempotency_key: str
	requester_id: str
	input: dict[str, Any]
	status: str
	consequential: bool = True
	approval_id: str | None = None
	result: Any = None
	error: str | None = None
	attempts: int = 0
	max_attempts: int = 3
	next_attempt_at: str | None = None
	lease_owner: str | None = None
	lease_until: str | None = None
	revision: int = 0
	created_at: str = field(default_factory=utc_now)
	updated_at: str = field(default_factory=utc_now)
	# Consequential connector work remains pinned to the approved graph that
	# admitted it, even when the project later approves another revision.
	operation_graph_version: str = ""
	schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class AuditEvent(RecordMixin):
	id: str
	tenant_id: str
	environment_id: str
	project_id: str
	action: str
	actor_id: str
	result: str
	payload: dict[str, Any] = field(default_factory=dict)
	correlation_id: str | None = None
	trace_id: str | None = None
	timestamp: str = field(default_factory=utc_now)
	schema_version: str = SCHEMA_VERSION


@dataclass(frozen=True)
class TelemetryEvent(RecordMixin):
	id: str
	tenant_id: str
	environment_id: str
	project_id: str
	name: str
	value: float = 1.0
	attributes: dict[str, Any] = field(default_factory=dict)
	timestamp: str = field(default_factory=utc_now)
	schema_version: str = SCHEMA_VERSION
