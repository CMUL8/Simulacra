"""Frozen V0 collaboration records and validation helpers."""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping

from .errors import ValidationError

SCHEMA_VERSION = "cmul8.collaboration.v0"
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


def utc_now() -> datetime:
	return datetime.now(UTC)


def iso_now() -> str:
	return utc_now().isoformat()


def new_id(prefix: str) -> str:
	return f"{prefix}_{uuid.uuid4().hex}"


def validate_scope_id(value: str, label: str) -> str:
	if not isinstance(value, str) or not _ID_RE.fullmatch(value):
		raise ValidationError(f"invalid {label}")
	if value in {".", ".."} or "/" in value or "\\" in value:
		raise ValidationError(f"invalid {label}")
	return value


def _enum_dict(data: dict[str, Any]) -> dict[str, Any]:
	for key, value in tuple(data.items()):
		if isinstance(value, StrEnum):
			data[key] = value.value
		elif isinstance(value, list):
			data[key] = [asdict(item) if hasattr(item, "__dataclass_fields__") else item for item in value]
		elif hasattr(value, "__dataclass_fields__"):
			data[key] = asdict(value)
	return data


class TaskState(StrEnum):
	PROPOSED = "proposed"
	READY = "ready"
	WORKING = "working"
	IN_REVIEW = "in_review"
	DONE = "done"
	BLOCKED = "blocked"
	FAILED = "failed"
	CANCELLED = "cancelled"


class ReviewDecision(StrEnum):
	APPROVE = "approve"
	REQUEST_CHANGES = "request_changes"
	QUESTION = "question"
	REJECT = "reject"
	ROLLBACK = "rollback"


class ActorType(StrEnum):
	HUMAN = "human"
	BUILDER_AGENT = "builder_agent"
	RUNTIME_AGENT = "runtime_agent"
	SYSTEM = "system"


class CommentTargetType(StrEnum):
	PROJECT = "project"
	TASK = "task"
	GRAPH_ELEMENT = "graph_element"


@dataclass(frozen=True, slots=True)
class Member:
	actor_id: str
	role: str
	display_name: str = ""
	joined_at: str = field(default_factory=iso_now)

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> Member:
		return cls(**dict(data))


@dataclass(slots=True)
class ProjectRoom:
	id: str
	tenant_id: str
	project_id: str
	members: list[Member]
	revision: int = 1
	schema_version: str = SCHEMA_VERSION
	created_at: str = field(default_factory=iso_now)
	updated_at: str = field(default_factory=iso_now)

	def to_dict(self) -> dict[str, Any]:
		return _enum_dict(asdict(self))

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> ProjectRoom:
		row = dict(data)
		row["members"] = [Member.from_dict(item) for item in row.get("members", [])]
		return cls(**row)


@dataclass(slots=True)
class Task:
	id: str
	tenant_id: str
	project_id: str
	title: str
	objective: str
	acceptance_criteria: list[str]
	source_message_id: str | None = None
	owner_id: str | None = None
	collaborator_ids: list[str] = field(default_factory=list)
	state: TaskState = TaskState.PROPOSED
	operation_graph_version: str | None = None
	application_version: str | None = None
	activity: list[dict[str, Any]] = field(default_factory=list)
	result: dict[str, Any] | None = None
	revision: int = 1
	schema_version: str = SCHEMA_VERSION
	created_at: str = field(default_factory=iso_now)
	updated_at: str = field(default_factory=iso_now)

	def to_dict(self) -> dict[str, Any]:
		return _enum_dict(asdict(self))

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> Task:
		row = dict(data)
		row["state"] = TaskState(row["state"])
		return cls(**row)


@dataclass(frozen=True, slots=True)
class Mention:
	ref_type: str
	ref_id: str

	@classmethod
	def normalize(cls, value: str | Mapping[str, Any] | Mention) -> Mention:
		if isinstance(value, Mention):
			return value
		if isinstance(value, Mapping):
			kind = str(value.get("ref_type") or value.get("type") or "actor").strip().lower()
			ref = str(value.get("ref_id") or value.get("id") or "").strip()
		else:
			text = str(value).strip()
			if text.startswith("@"):
				kind, ref = "actor", text[1:]
			elif ":" in text:
				kind, ref = text.split(":", 1)
			else:
				kind, ref = "actor", text
			kind = kind.strip().lower()
			ref = ref.strip()
		kind = kind.strip().lower()
		ref = ref.strip().lower()
		if not kind or not ref or not _ID_RE.fullmatch(ref):
			raise ValidationError("invalid mention reference")
		return cls(ref_type=kind, ref_id=ref)


def normalize_mentions(values: list[str | Mapping[str, Any] | Mention]) -> list[Mention]:
	unique: dict[tuple[str, str], Mention] = {}
	for value in values:
		mention = Mention.normalize(value)
		unique[(mention.ref_type, mention.ref_id)] = mention
	return [unique[key] for key in sorted(unique)]


@dataclass(slots=True)
class Comment:
	id: str
	tenant_id: str
	project_id: str
	author_id: str
	body: str
	target_type: CommentTargetType
	target_id: str
	task_id: str | None = None
	graph_path: str | None = None
	graph_revision: str | None = None
	mentions: list[Mention] = field(default_factory=list)
	revision: int = 1
	schema_version: str = SCHEMA_VERSION
	created_at: str = field(default_factory=iso_now)
	updated_at: str = field(default_factory=iso_now)

	def to_dict(self) -> dict[str, Any]:
		return _enum_dict(asdict(self))

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> Comment:
		row = dict(data)
		row["target_type"] = CommentTargetType(row["target_type"])
		row["mentions"] = [Mention(**item) for item in row.get("mentions", [])]
		return cls(**row)


@dataclass(slots=True)
class Review:
	id: str
	tenant_id: str
	project_id: str
	task_id: str
	reviewer_id: str
	reviewer_role: str
	actor_type: ActorType
	decision: ReviewDecision
	body: str = ""
	task_revision: int = 1
	revision: int = 1
	schema_version: str = SCHEMA_VERSION
	created_at: str = field(default_factory=iso_now)
	updated_at: str = field(default_factory=iso_now)

	def to_dict(self) -> dict[str, Any]:
		return _enum_dict(asdict(self))

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> Review:
		row = dict(data)
		row["actor_type"] = ActorType(row["actor_type"])
		row["decision"] = ReviewDecision(row["decision"])
		return cls(**row)


@dataclass(frozen=True, slots=True)
class DomainEvent:
	id: str
	actor_type: ActorType
	actor_id: str
	tenant_id: str
	project_id: str
	task_id: str | None
	operation_graph_version: str | None
	application_version: str | None
	environment_id: str | None
	action: str
	result: str
	timestamp: str
	correlation_id: str | None
	trace_id: str | None
	payload: dict[str, Any]

	def to_dict(self) -> dict[str, Any]:
		return _enum_dict(asdict(self))

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> DomainEvent:
		row = dict(data)
		row["actor_type"] = ActorType(row["actor_type"])
		return cls(**row)
