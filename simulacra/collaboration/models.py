"""Frozen V0 collaboration records and validation helpers."""

from __future__ import annotations

import re
import hashlib
import hmac
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


CONVERSATION_MESSAGE_KINDS = frozenset({
	"human_message", "agent_message", "assignment_created", "agent_started",
	"agent_progress", "agent_completed", "human_decision_required",
	"human_decision_recorded", "output_ready", "output_verified",
	"automation_event", "system_milestone",
})
CONVERSATION_REACTIONS = ("acknowledge", "check", "question", "celebrate")


@dataclass(frozen=True, slots=True)
class ConversationMessage:
	"""Canonical durable conversation record; public projection is allow-list based."""
	id: str
	tenant_id: str
	project_id: str
	author: dict[str, Any]
	kind: str
	body: str | None
	created_at: str
	revision: int = 1
	root_message_id: str | None = None
	source_message_id: str | None = None
	links: dict[str, Any] = field(default_factory=dict)
	deleted_at: str | None = None
	edited_at: str | None = None

	def __post_init__(self) -> None:
		validate_scope_id(self.id, "message_id")
		validate_scope_id(self.tenant_id, "tenant_id")
		validate_scope_id(self.project_id, "project_id")
		if self.kind not in CONVERSATION_MESSAGE_KINDS:
			raise ValidationError("invalid conversation message kind")
		if self.root_message_id is not None:
			validate_scope_id(self.root_message_id, "root_message_id")
		if self.source_message_id is not None:
			validate_scope_id(self.source_message_id, "source_message_id")
		if not isinstance(self.author, dict) or not isinstance(self.links, dict):
			raise ValidationError("invalid conversation message metadata")

	def to_dict(self) -> dict[str, Any]:
		return _enum_dict(asdict(self))

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> ConversationMessage:
		allowed = set(cls.__dataclass_fields__)
		return cls(**{key: value for key, value in dict(data).items() if key in allowed})


@dataclass(frozen=True, slots=True)
class MessageAudit:
	id: str
	message_id: str
	operation: str
	actor_id: str
	client_request_id: str
	prior_revision: int
	prior_body: str | None
	resulting_revision: int
	occurred_at: str

	def __post_init__(self) -> None:
		validate_scope_id(self.id, "message_audit_id")
		validate_scope_id(self.message_id, "message_id")
		validate_scope_id(self.actor_id, "actor_id")
		if self.operation not in {"edit", "delete"}:
			raise ValidationError("invalid message audit operation")

	def to_dict(self) -> dict[str, Any]:
		return _enum_dict(asdict(self))

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> MessageAudit:
		allowed = set(cls.__dataclass_fields__)
		return cls(**{key: value for key, value in dict(data).items() if key in allowed})


@dataclass(frozen=True, slots=True)
class ConversationReaction:
	message_id: str
	actor_id: str
	reaction: str
	created_at: str

	def __post_init__(self) -> None:
		validate_scope_id(self.message_id, "message_id")
		validate_scope_id(self.actor_id, "actor_id")
		if self.reaction not in CONVERSATION_REACTIONS:
			raise ValidationError("invalid conversation reaction")

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> ConversationReaction:
		allowed = set(cls.__dataclass_fields__)
		return cls(**{key: value for key, value in dict(data).items() if key in allowed})


@dataclass(frozen=True, slots=True)
class SavedReference:
	tenant_id: str
	human_id: str
	object_kind: str
	object_id: str
	created_at: str

	def __post_init__(self) -> None:
		validate_scope_id(self.tenant_id, "tenant_id")
		validate_scope_id(self.human_id, "human_id")
		validate_scope_id(self.object_id, "saved_object_id")
		if self.object_kind != "conversation_message":
			raise ValidationError("invalid saved reference kind")

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> SavedReference:
		allowed = set(cls.__dataclass_fields__)
		return cls(**{key: value for key, value in dict(data).items() if key in allowed})


@dataclass(frozen=True, slots=True)
class Member:
	actor_id: str
	role: str
	display_name: str = ""
	joined_at: str = field(default_factory=iso_now)
	transaction_id: str | None = None
	visibility_state: str = "committed"

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> Member:
		row = dict(data)
		# Expand-only compatibility: old room records were committed rows.
		row.setdefault("transaction_id", None)
		row.setdefault("visibility_state", "committed")
		return cls(**row)


@dataclass(frozen=True, slots=True)
class Invitation:
	"""One single-use enrollment secret; the plaintext token is never a record field."""
	id: str
	tenant_id: str
	project_id: str
	invited_by: str
	invitee_email: str
	requested_role: str
	accept_token_digest: str
	status: str
	expires_at: str
	accepted_actor_id: str | None = None
	revision: int = 1
	created_at: str = field(default_factory=iso_now)
	updated_at: str = field(default_factory=iso_now)
	# Durable action receipts stay private to the collaboration repository. They
	# make a retried revoke return the original result without exposing a token.
	mutation_receipts: dict[str, dict[str, Any]] = field(default_factory=dict)

	def __post_init__(self) -> None:
		for value, label in ((self.id, "invitation_id"), (self.tenant_id, "tenant_id"),
			(self.project_id, "project_id"), (self.invited_by, "invited_by")):
			validate_scope_id(value, label)
		if self.status not in {"pending", "accepted", "revoked", "expired"}:
			raise ValidationError("invalid invitation status")
		if self.visibility_digest_invalid:
			raise ValidationError("invalid invitation token digest")

	@property
	def visibility_digest_invalid(self) -> bool:
		return not bool(re.fullmatch(r"[0-9a-f]{64}", self.accept_token_digest))

	def token_matches(self, token: str) -> bool:
		return hmac.compare_digest(hashlib.sha256(token.encode("utf-8")).hexdigest(), self.accept_token_digest)

	def to_dict(self) -> dict[str, Any]:
		return asdict(self)

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> "Invitation":
		allowed = set(cls.__dataclass_fields__)
		return cls(**{key: value for key, value in dict(data).items() if key in allowed})


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
	# This is not part of the public room serializer. Keeping the receipt beside
	# the membership change makes member removal and its retry one atomic write.
	mutation_receipts: dict[str, dict[str, Any]] = field(default_factory=dict)

	def to_dict(self) -> dict[str, Any]:
		return _enum_dict(asdict(self))

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> ProjectRoom:
		row = dict(data)
		row["members"] = [Member.from_dict(item) for item in row.get("members", [])]
		row.setdefault("mutation_receipts", {})
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
