"""Durable Mission V0 records and deterministic trigger helpers."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from simulacra.collaboration.models import validate_scope_id
from simulacra.runtime.security import assert_opaque_credentials

MISSION_STATUSES = frozenset(
    {
        "draft",
        "ready",
        "running",
        "waiting_for_human",
        "paused",
        "blocked",
        "completed",
        "failed",
        "archived",
    }
)
RUN_STATUSES = frozenset(
    {
        "queued",
        "preparing",
        "running",
        "awaiting_approval",
        "verifying",
        "succeeded",
        "failed",
        "cancelled",
        "expired",
    }
)
AUTONOMIES = frozenset({"assist", "execute_safely", "operate_with_checkpoints"})
TRIGGER_TYPES = frozenset({"manual", "cron", "condition"})
CONCURRENCY_POLICIES = frozenset({"queue", "skip", "replace", "merge"})
DELIVERABLE_TYPES = frozenset(
    {"code", "report", "application", "visualization", "dataset"}
)
DELIVERABLE_STATES = frozenset(
    {
        "draft",
        "validated",
        "awaiting_verification",
        "verified",
        "changes_requested",
        "published",
    }
)
PROFILES = frozenset({"routine", "balanced", "deep", "code", "verification"})
_SECRET = re.compile(
    r"(?:api[_-]?key|token|secret|password|credential|provider|model|runtime|computer)",
    re.I,
)


def now() -> str:
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def clean_public_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Reject control-plane/credential input before persistence."""
    for key in value:
        if _SECRET.search(str(key)):
            raise ValueError(
                "provider, model, runtime, computer, and credential fields are server-controlled"
            )
    try:
        assert_opaque_credentials(value, context="mission public payload")
    except Exception as exc:
        raise ValueError("mission payload contains credential material") from exc
    return dict(value)


@dataclass(slots=True)
class Mission:
    id: str
    tenant_id: str
    project_id: str
    title: str
    objective: str
    definition_of_done: str = ""
    template: str = "custom"
    owner_id: str = ""
    verifier_ids: list[str] = field(default_factory=list)
    status: str = "draft"
    priority: str = "normal"
    risk_level: str = "medium"
    deadline: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    approved_contract_revision: str | None = None
    revision: int = 1
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def __post_init__(self):
        validate_scope_id(self.id, "mission_id")
        validate_scope_id(self.tenant_id, "tenant_id")
        validate_scope_id(self.project_id, "project_id")
        if self.status not in MISSION_STATUSES:
            raise ValueError("invalid mission status")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**dict(data))


@dataclass(slots=True)
class AgentDefinition:
    id: str
    tenant_id: str
    project_id: str
    mission_id: str
    name: str
    role: str
    mandate: str
    responsibilities: list[str] = field(default_factory=list)
    data_scope: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    autonomy: str = "assist"
    escalation_actor_id: str | None = None
    budget: dict[str, Any] = field(default_factory=dict)
    state: str = "idle"
    inbox: list[dict[str, Any]] = field(default_factory=list)
    memory_ref: str | None = None
    next_wake_at: str | None = None
    revision: int = 1
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def __post_init__(self):
        validate_scope_id(self.id, "agent_id")
        if self.autonomy not in AUTONOMIES:
            raise ValueError("invalid agent autonomy")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**dict(data))


@dataclass(slots=True)
class MissionRun:
    id: str
    tenant_id: str
    project_id: str
    mission_id: str
    trigger_snapshot: dict[str, Any]
    contract_revision: str | None
    status: str = "queued"
    execution_profile: dict[str, Any] = field(default_factory=dict)
    started_at: str | None = None
    completed_at: str | None = None
    progress: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    occurrence_key: str | None = None
    revision: int = 1
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def __post_init__(self):
        validate_scope_id(self.id, "run_id")
        if self.status not in RUN_STATUSES:
            raise ValueError("invalid run status")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**dict(data))


@dataclass(slots=True)
class AutomationTrigger:
    id: str
    tenant_id: str
    project_id: str
    mission_id: str
    type: str
    cron: str | None = None
    condition: dict[str, Any] | None = None
    timezone: str = "UTC"
    concurrency_policy: str = "queue"
    enabled: bool = True
    next_due_at: str | None = None
    handled_occurrences: dict[str, dict[str, Any]] = field(default_factory=dict)
    revision: int = 1
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def __post_init__(self):
        validate_scope_id(self.id, "trigger_id")
        if (
            self.type not in TRIGGER_TYPES
            or self.concurrency_policy not in CONCURRENCY_POLICIES
        ):
            raise ValueError("invalid trigger")
        ZoneInfo(self.timezone)
        if self.type == "cron":
            self.next_due_at = (
                self.next_due_at
                or next_cron_due(self.cron or "", self.timezone).isoformat()
            )
        if self.type == "condition":
            validate_condition(self.condition or {})

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**dict(data))


@dataclass(slots=True)
class Deliverable:
    id: str
    tenant_id: str
    project_id: str
    mission_id: str
    type: str
    name: str
    producer_id: str
    version: int
    content_hash: str
    source_ref: str
    artifact_ref: str | None = None
    validation_evidence: list[dict[str, Any]] = field(default_factory=list)
    state: str = "draft"
    verified_by: str | None = None
    verified_hash: str | None = None
    verified_at: str | None = None
    supersedes_id: str | None = None
    revision: int = 1
    created_at: str = field(default_factory=now)
    updated_at: str = field(default_factory=now)

    def __post_init__(self):
        validate_scope_id(self.id, "deliverable_id")
        if self.type not in DELIVERABLE_TYPES or self.state not in DELIVERABLE_STATES:
            raise ValueError("invalid deliverable")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_hash):
            raise ValueError("content_hash must be sha256")

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        return cls(**dict(data))


def hash_artifact(value: str | bytes) -> str:
    return hashlib.sha256(
        value.encode() if isinstance(value, str) else value
    ).hexdigest()


def validate_condition(condition: Mapping[str, Any]) -> None:
    if set(condition) != {"fact", "operator", "value"}:
        raise ValueError("condition must contain fact, operator, value")
    if not isinstance(condition["fact"], str) or not re.fullmatch(
        r"[A-Za-z][A-Za-z0-9_.-]{0,127}", condition["fact"]
    ):
        raise ValueError("invalid condition fact")
    if condition["operator"] not in {"eq", "ne", "gt", "gte", "lt", "lte", "contains"}:
        raise ValueError("invalid condition operator")
    if isinstance(condition["value"], (dict, list)):
        raise ValueError("condition value must be scalar")


def condition_matches(condition: Mapping[str, Any], facts: Mapping[str, Any]) -> bool:
    validate_condition(condition)
    left, right, op = (
        facts.get(condition["fact"]),
        condition["value"],
        condition["operator"],
    )
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if op == "contains":
        return isinstance(left, (str, list, tuple, set)) and right in left
    try:
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
        if op == "lt":
            return left < right
        return left <= right
    except TypeError:
        return False


def next_cron_due(
    expression: str, timezone: str = "UTC", after: datetime | None = None
) -> datetime:
    """Numeric five-field V0 cron: lists, ranges, and steps over an 8-year horizon."""
    fields = expression.split()
    if len(fields) != 5:
        raise ValueError("cron must have five fields")
    ranges = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
    allowed = []
    for raw, (lo, hi) in zip(fields, ranges):
        vals = set()
        for part in raw.split(","):
            base, separator, step_text = part.partition("/")
            if separator:
                if not step_text.isdigit() or int(step_text) <= 0:
                    raise ValueError("invalid cron step")
                step = int(step_text)
            else:
                step = 1
            if base == "*":
                start, end = lo, hi
            elif "-" in base:
                pieces = base.split("-")
                if len(pieces) != 2 or not all(piece.isdigit() for piece in pieces):
                    raise ValueError("invalid cron range")
                start, end = map(int, pieces)
            elif not separator and base.isdigit():
                start = end = int(base)
            else:
                raise ValueError("unsupported or invalid cron expression")
            if start < lo or end > hi or start > end:
                raise ValueError("invalid cron range")
            vals.update(range(start, end + 1, step))
        if hi == 7:
            vals = {0 if value == 7 else value for value in vals}
        allowed.append(vals)
    zone = ZoneInfo(timezone)
    point = (after or datetime.now(zone)).astimezone(zone).replace(
        second=0, microsecond=0
    ) + timedelta(minutes=1)
    for _ in range(4_210_000):
        # Python uses Monday=0; standard five-field cron uses Sunday=0.
        cron_weekday = (point.weekday() + 1) % 7
        day_of_month_match = point.day in allowed[2]
        day_of_week_match = cron_weekday in allowed[4]
        dom_restricted = fields[2] != "*"
        dow_restricted = fields[4] != "*"
        if dom_restricted and dow_restricted:
            day_match = day_of_month_match or day_of_week_match
        elif dom_restricted:
            day_match = day_of_month_match
        elif dow_restricted:
            day_match = day_of_week_match
        else:
            day_match = True
        if (
            point.minute in allowed[0]
            and point.hour in allowed[1]
            and point.month in allowed[3]
            and day_match
        ):
            return point
        point += timedelta(minutes=1)
    raise ValueError("cron has no due time in the next eight years")
