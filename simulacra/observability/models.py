"""Tenant-scoped observability records and stable query response models."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping

_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class EntityKind(StrEnum):
	APPLICATION = "application"
	WORKFLOW = "workflow"
	AGENT = "agent"


class EventStatus(StrEnum):
	SUCCEEDED = "succeeded"
	FAILED = "failed"
	WARNING = "warning"
	RUNNING = "running"


class HealthState(StrEnum):
	HEALTHY = "healthy"
	DEGRADED = "degraded"
	FAILING = "failing"
	INACTIVE = "inactive"


class Severity(StrEnum):
	CRITICAL = "critical"
	HIGH = "high"
	MEDIUM = "medium"
	LOW = "low"


def _identifier(value: str, label: str) -> str:
	if not isinstance(value, str) or not _ID_RE.fullmatch(value):
		raise ValueError(f"invalid {label}")
	return value


def parse_timestamp(value: str) -> datetime:
	try:
		parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
	except (TypeError, ValueError) as exc:
		raise ValueError("timestamp must be ISO-8601") from exc
	if parsed.tzinfo is None:
		raise ValueError("timestamp must include a timezone")
	return parsed.astimezone(UTC)


@dataclass(frozen=True, slots=True)
class TelemetryEvent:
	id: str
	tenant_id: str
	entity_kind: EntityKind
	entity_id: str
	entity_name: str
	signal: str
	status: EventStatus
	started_at: str
	duration_ms: float = 0
	trace_id: str | None = None
	application_id: str | None = None
	workflow_id: str | None = None
	agent_id: str | None = None
	environment: str = "production"
	message: str = ""
	tags: tuple[str, ...] = ()
	attributes: Mapping[str, Any] = field(default_factory=dict)

	def __post_init__(self) -> None:
		_identifier(self.id, "event id")
		_identifier(self.tenant_id, "tenant id")
		_identifier(self.entity_id, "entity id")
		if not self.entity_name.strip() or not self.signal.strip():
			raise ValueError("entity_name and signal are required")
		parse_timestamp(self.started_at)
		if self.duration_ms < 0:
			raise ValueError("duration_ms cannot be negative")
		for label, value in (("trace id", self.trace_id), ("application id", self.application_id), ("workflow id", self.workflow_id), ("agent id", self.agent_id)):
			if value is not None:
				_identifier(value, label)

	def to_dict(self) -> dict[str, Any]:
		data = asdict(self)
		data["entity_kind"] = self.entity_kind.value
		data["status"] = self.status.value
		data["tags"] = list(self.tags)
		data["attributes"] = dict(self.attributes)
		return data

	@classmethod
	def from_dict(cls, data: Mapping[str, Any]) -> TelemetryEvent:
		row = dict(data)
		row["entity_kind"] = EntityKind(row["entity_kind"])
		row["status"] = EventStatus(row["status"])
		row["tags"] = tuple(row.get("tags", ()))
		return cls(**row)


@dataclass(frozen=True, slots=True)
class TelemetryQuery:
	tenant_id: str
	start_at: str | None = None
	end_at: str | None = None
	entity_kind: EntityKind | None = None
	entity_id: str | None = None
	status: EventStatus | None = None
	environment: str | None = None
	trace_id: str | None = None
	limit: int = 500

	def __post_init__(self) -> None:
		_identifier(self.tenant_id, "tenant id")
		if self.entity_id:
			_identifier(self.entity_id, "entity id")
		if self.trace_id:
			_identifier(self.trace_id, "trace id")
		start = parse_timestamp(self.start_at) if self.start_at else None
		end = parse_timestamp(self.end_at) if self.end_at else None
		if start and end and start > end:
			raise ValueError("start_at must be before end_at")
		if self.limit < 1 or self.limit > 10_000:
			raise ValueError("limit must be between 1 and 10000")


@dataclass(frozen=True, slots=True)
class TrendPoint:
	start_at: str
	runs: int
	errors: int
	p95_ms: float


@dataclass(frozen=True, slots=True)
class InventoryItem:
	id: str
	name: str
	kind: EntityKind
	health: HealthState
	runs: int
	errors: int
	success_rate: float
	p95_ms: float
	last_seen_at: str
	environments: tuple[str, ...]
	deep_link: str


@dataclass(frozen=True, slots=True)
class OverviewSnapshot:
	start_at: str | None
	end_at: str | None
	runs: int
	errors: int
	warnings: int
	success_rate: float
	p95_ms: float
	active_applications: int
	active_workflows: int
	active_agents: int
	health_counts: Mapping[str, int]
	trend: tuple[TrendPoint, ...]


@dataclass(frozen=True, slots=True)
class EntityDetail:
	item: InventoryItem
	recent_events: tuple[TelemetryEvent, ...]
	related_applications: tuple[str, ...]
	related_workflows: tuple[str, ...]
	related_agents: tuple[str, ...]
	trace_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ActionItem:
	id: str
	severity: Severity
	title: str
	rationale: str
	entity_kind: EntityKind
	entity_id: str
	action: str
	trace_id: str | None
	first_seen_at: str
	last_seen_at: str
	occurrences: int
	deep_link: str


@dataclass(frozen=True, slots=True)
class DeepLink:
	view: str
	entity_kind: EntityKind | None = None
	entity_id: str | None = None
	trace_id: str | None = None
