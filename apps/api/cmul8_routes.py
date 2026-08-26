"""Tenant-authorized CMUL8 Operation Graph and Project Room routes.

The legacy builder remains available, but these routes expose the durable V0
contracts instead of manufacturing multiplayer state in the browser.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from apps.api.security import audit_request, require_project_access
from simulacra.collaboration import ActivityInbox, CollaborationService, JsonCollaborationRepository, PresenceRegistry
from simulacra.collaboration.errors import CollaborationError
from simulacra.collaboration.models import CommentTargetType, ReviewDecision, TaskState
from simulacra.demo.identity import AuthContext, get_membership, get_user, get_user_by_email
from simulacra.demo.paths import RUNS_DIR
from simulacra.demo.runs import load_state, project_dir
from simulacra.harnesses import HarnessConfig, create_harness
from simulacra.operation_graph import OperationGraphStore
from simulacra.operation_graph.errors import OperationGraphError
from simulacra.runtime.security import assert_opaque_credentials
from simulacra.runtime import RuntimePlane, SUPPORTED_JOB_KINDS
from simulacra.observability import (
	EntityKind, EventStatus, JsonlTelemetryRepository, ObservabilityQueries,
	TelemetryEvent, TelemetryQuery,
)

router = APIRouter(prefix="/projects/{project_id}/cmul8", tags=["cmul8-v0"])
_collaboration_root = RUNS_DIR / ".cmul8-control"
# Deployment processes use the same explicit roots; local development retains
# isolated project fixtures without requiring environment configuration.
_telemetry_root = Path(os.environ.get("CMUL8_TELEMETRY_ROOT", str(RUNS_DIR / ".cmul8-telemetry")))
_runtime_root = Path(os.environ.get("CMUL8_RUNTIME_ROOT", str(RUNS_DIR / ".cmul8-runtime")))
_presence = PresenceRegistry(ttl_seconds=60)


def _collaboration() -> tuple[JsonCollaborationRepository, CollaborationService]:
	repository = JsonCollaborationRepository(_collaboration_root)
	return repository, CollaborationService(repository)


def _graph_store(project_id: str, tenant_id: str) -> OperationGraphStore:
	return OperationGraphStore(project_dir(project_id), tenant_id=tenant_id, project_id=project_id)


def _room_role(room: Any, actor_id: str) -> str | None:
	return next((member.role for member in room.members if member.actor_id == actor_id), None)


def _display_name(actor_id: str, stored_name: str = "") -> str:
	if stored_name.strip():
		return stored_name.strip()
	try:
		return get_user(actor_id).name.strip() or actor_id
	except KeyError:
		return actor_id


def _room_dict(room: Any) -> dict[str, Any]:
	payload = room.to_dict()
	for member in payload.get("members", []):
		member["display_name"] = _display_name(member["actor_id"], member.get("display_name", ""))
	return payload


def _require_graph_mutator(project_id: str, ctx: AuthContext) -> None:
	repository, _ = _collaboration()
	room = repository.get_room(ctx.tenant_id, project_id)
	if _room_role(room, ctx.user.id) not in {"owner", "admin"}:
		raise HTTPException(403, "project room owner or admin role required for Operation Graph mutations")


def _translate(exc: Exception) -> HTTPException:
	name = type(exc).__name__.lower()
	if "notfound" in name or "not_found" in name:
		return HTTPException(404, str(exc))
	if "authorization" in name or "unapproved" in name:
		return HTTPException(403, str(exc))
	if "conflict" in name or "transition" in name:
		return HTTPException(409, str(exc))
	return HTTPException(400, str(exc))


class RoomCreateBody(BaseModel):
	display_name: str = Field(default="", max_length=120)


class RoomMemberBody(BaseModel):
	member_id: str | None = Field(default=None, min_length=1, max_length=200)
	member_email: str | None = Field(default=None, min_length=3, max_length=320)
	role: str = Field(default="member", pattern="^(owner|admin|member|viewer|reviewer|approver)$")
	expected_revision: int = Field(ge=1)


class PresenceBody(BaseModel):
	status: str = Field(default="active", pattern="^(active|away)$")
	location: str | None = Field(default=None, max_length=200)


class TaskCreateBody(BaseModel):
	title: str = Field(min_length=1, max_length=200)
	objective: str = Field(min_length=1, max_length=4000)
	acceptance_criteria: list[str] = Field(min_length=1, max_length=30)
	owner_id: str | None = None
	operation_graph_version: str | None = None


class TaskTransitionBody(BaseModel):
	state: TaskState
	expected_revision: int = Field(ge=1)
	result: dict[str, Any] | None = None


class TaskReviewBody(BaseModel):
	decision: ReviewDecision
	expected_revision: int = Field(ge=1)
	note: str = Field(default="", max_length=4000)


class CommentCreateBody(BaseModel):
	body: str = Field(min_length=1, max_length=8000)
	target_type: CommentTargetType = CommentTargetType.PROJECT
	target_id: str | None = None
	task_id: str | None = None
	graph_path: str | None = None
	graph_revision: str | None = None
	mentions: list[dict[str, str]] = Field(default_factory=list, max_length=50)


class GraphRevisionBody(BaseModel):
	graph: dict[str, Any]
	expected_revision_hash: str | None = None


class TelemetryEventBody(BaseModel):
	id: str
	entity_kind: EntityKind
	entity_id: str
	entity_name: str = Field(min_length=1, max_length=200)
	signal: str = Field(min_length=1, max_length=200)
	status: EventStatus
	started_at: str
	duration_ms: float = Field(default=0, ge=0)
	trace_id: str | None = None
	workflow_id: str | None = None
	agent_id: str | None = None
	environment: str = Field(default="production", min_length=1, max_length=120)
	message: str = Field(default="", max_length=4000)
	tags: list[str] = Field(default_factory=list, max_length=100)
	attributes: dict[str, Any] = Field(default_factory=dict)


class RuntimeJobBody(BaseModel):
	"""Admission request for one graph-confined durable runtime job."""
	revision_hash: str = Field(min_length=1, max_length=200)
	environment_id: str = Field(default="production", min_length=1, max_length=128)
	kind: str = Field(min_length=1, max_length=120)
	payload: dict[str, Any] = Field(default_factory=dict)
	idempotency_key: str | None = Field(default=None, max_length=256)
	max_attempts: int = Field(default=3, ge=1, le=20)


class _ProjectTelemetryRepository:
	"""Narrow a tenant repository to one application/project before aggregation."""

	def __init__(self, repository: JsonlTelemetryRepository, project_id: str):
		self.repository = repository
		self.project_id = project_id

	def query(self, query: TelemetryQuery) -> list[TelemetryEvent]:
		return [event for event in self.repository.query(query) if event.application_id == self.project_id]


def _runtime_plane(project_id: str, tenant_id: str, environment_id: str, revision_hash: str) -> RuntimePlane:
	"""Construct only from the caller's exact approved graph revision."""
	return RuntimePlane.from_approved_revision(
		_runtime_root, _graph_store(project_id, tenant_id), revision_hash,
		environment_id=environment_id,
		observability_repository=JsonlTelemetryRepository(_telemetry_root),
	)


def _room_payload(project_id: str, ctx: AuthContext) -> dict[str, Any]:
	repository, _ = _collaboration()
	room = repository.get_room(ctx.tenant_id, project_id)
	state = load_state(project_id)
	tasks = [task.to_dict() for task in repository.list_tasks(ctx.tenant_id, project_id)]
	comments = [comment.to_dict() for comment in repository.list_comments(ctx.tenant_id, project_id)]
	reviews = [review.to_dict() for review in repository.list_reviews(ctx.tenant_id, project_id)]
	events = [event.to_dict() for event in repository.list_events(ctx.tenant_id, project_id)]
	graph_store = _graph_store(project_id, ctx.tenant_id)
	graph = graph_store.current_revision()
	approvals = graph_store.list_approvals(graph.revision_hash) if graph else []
	role = _room_role(room, ctx.user.id)
	inbox = ActivityInbox(repository)
	away = inbox.while_you_were_away(
		tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.user.id
	)
	return {
		"room": _room_dict(room),
		"project": {"id": project_id, "name": state.app_config.title, "objective": state.goal or state.prompt},
		"tasks": tasks,
		"comments": comments,
		"reviews": reviews,
		"events": events,
		"operation_graph": asdict(graph) if graph else None,
		"operation_graph_approvals": [asdict(item) for item in approvals],
		"away": {
			"since": away.since, "total": away.total, "unread": away.unread,
			"counts": away.counts,
			"highlights": [
				{"position": item.position, "category": item.category.value,
				 "unread": item.unread, "event": item.event.to_dict(), "deep_link": item.deep_link}
				for item in away.highlights
			],
		},
		"presence": [asdict(item) for item in _presence.list_active(tenant_id=ctx.tenant_id, project_id=project_id)],
		"permissions": {
			"manage_tasks": role in {"owner", "admin", "member", "reviewer", "approver"},
			"review_tasks": role in {"owner", "admin", "reviewer", "approver"},
			"review_graph": role in {"owner", "admin"},
			"invite": role in {"owner", "admin"},
			"comment": role in {"owner", "admin", "member", "reviewer", "approver"},
		},
	}


@router.post("/room")
def create_room(
	project_id: str, body: RoomCreateBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:approve"))],
) -> dict[str, Any]:
	if ctx.role not in {"owner", "admin"} and not ctx.user.is_platform_admin:
		raise HTTPException(403, "only project owners and admins can create a Project Room")
	_, service = _collaboration()
	try:
		service.create_room(
			tenant_id=ctx.tenant_id, project_id=project_id, creator_id=ctx.user.id,
			# A bootstrapper is the initial room authority.  Seeding an owner avoids
			# an admin-created room that no member can administrate as its owner.
			creator_role="owner", creator_name=body.display_name or ctx.user.name,
		)
	except CollaborationError as exc:
		if "already exists" not in str(exc):
			raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.room.ensure", project_id=project_id)
	return _room_payload(project_id, ctx)


@router.get("/room")
def get_room(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	try:
		return _room_payload(project_id, ctx)
	except (CollaborationError, OperationGraphError) as exc:
		raise _translate(exc) from exc


@router.post("/room/members")
def add_room_member(
	project_id: str, body: RoomMemberBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	_, service = _collaboration()
	member_id = body.member_id
	if body.member_email:
		user = get_user_by_email(body.member_email.strip().lower())
		if user is None:
			raise HTTPException(404, "No workspace teammate uses that email yet. Invite them to the workspace first.")
		if get_membership(ctx.tenant_id, user.id) is None:
			raise HTTPException(403, "That person is not a member of this workspace yet.")
		member_id = user.id
	if not member_id:
		raise HTTPException(422, "member_email or member_id is required")
	try:
		room = service.add_member(
			tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.user.id,
			member_id=member_id, role=body.role, expected_revision=body.expected_revision,
			member_name=_display_name(member_id),
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.room.member_add", project_id=project_id, member_id=member_id)
	return _room_dict(room)


@router.post("/presence")
def heartbeat_presence(
	project_id: str, body: PresenceBody,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	repository, _ = _collaboration()
	try:
		room = repository.get_room(ctx.tenant_id, project_id)
		if ctx.user.id not in {member.actor_id for member in room.members}:
			raise HTTPException(403, "project room membership required")
		return asdict(_presence.heartbeat(
			tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.user.id,
			status=body.status, location=body.location,
		))
	except CollaborationError as exc:
		raise _translate(exc) from exc


@router.post("/inbox/read")
def mark_inbox_read(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
	position: int | None = None, event_id: str | None = None,
) -> dict[str, Any]:
	repository, _ = _collaboration()
	try:
		return ActivityInbox(repository).mark_read(
			tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.user.id,
			position=position, event_id=event_id,
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc


@router.post("/tasks")
def create_task(
	project_id: str, body: TaskCreateBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	_, service = _collaboration()
	try:
		task = service.create_task(
			tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.user.id,
			title=body.title, objective=body.objective, acceptance_criteria=body.acceptance_criteria,
			owner_id=body.owner_id, operation_graph_version=body.operation_graph_version,
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.task.create", project_id=project_id, task_id=task.id)
	return task.to_dict()


@router.post("/tasks/{task_id}/transition")
def transition_task(
	project_id: str, task_id: str, body: TaskTransitionBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	_, service = _collaboration()
	try:
		task = service.transition_task(
			tenant_id=ctx.tenant_id, project_id=project_id, task_id=task_id, actor_id=ctx.user.id,
			to_state=body.state, expected_revision=body.expected_revision, result=body.result,
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.task.transition", project_id=project_id, task_id=task_id)
	return task.to_dict()


@router.post("/tasks/{task_id}/claim")
def claim_task(
	project_id: str, task_id: str, expected_revision: int, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	_, service = _collaboration()
	try:
		task = service.claim_task(
			tenant_id=ctx.tenant_id, project_id=project_id, task_id=task_id,
			actor_id=ctx.user.id, expected_revision=expected_revision,
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.task.claim", project_id=project_id, task_id=task_id)
	return task.to_dict()


@router.post("/tasks/{task_id}/reviews")
def review_task(
	project_id: str, task_id: str, body: TaskReviewBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	_, service = _collaboration()
	try:
		review, task = service.review_task(
			tenant_id=ctx.tenant_id, project_id=project_id, task_id=task_id,
			reviewer_id=ctx.user.id, decision=body.decision,
			expected_revision=body.expected_revision, body=body.note,
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.task.review", project_id=project_id, task_id=task_id)
	return {"review": review.to_dict(), "task": task.to_dict()}


@router.post("/comments")
def create_comment(
	project_id: str, body: CommentCreateBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	_, service = _collaboration()
	try:
		comment = service.add_comment(
			tenant_id=ctx.tenant_id, project_id=project_id, author_id=ctx.user.id,
			body=body.body, target_type=body.target_type, target_id=body.target_id,
			task_id=body.task_id, graph_path=body.graph_path, graph_revision=body.graph_revision,
			mentions=body.mentions,
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.comment.create", project_id=project_id, comment_id=comment.id)
	return comment.to_dict()


@router.post("/operation-graph/revisions")
def create_graph_revision(
	project_id: str, body: GraphRevisionBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	try:
		_require_graph_mutator(project_id, ctx)
		revision = _graph_store(project_id, ctx.tenant_id).create_revision(
			body.graph, expected_revision_hash=body.expected_revision_hash
		)
	except (CollaborationError, OperationGraphError, ValueError) as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.graph.revise", project_id=project_id, revision_hash=revision.revision_hash)
	return asdict(revision)


@router.post("/operation-graph/revisions/{revision_hash}/approve")
def approve_graph_revision(
	project_id: str, revision_hash: str, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	try:
		_require_graph_mutator(project_id, ctx)
		approval = _graph_store(project_id, ctx.tenant_id).approve_revision(revision_hash, actor_id=ctx.user.id)
	except (CollaborationError, OperationGraphError, ValueError) as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.graph.approve", project_id=project_id, revision_hash=revision_hash)
	return asdict(approval)


@router.get("/harness")
async def harness_status(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	config = HarnessConfig.from_env()
	harness = create_harness(config)
	return {"config": config.metadata(), "health": dict(await harness.healthcheck())}


@router.post("/runtime/jobs")
def enqueue_runtime_job(
	project_id: str, body: RuntimeJobBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict[str, Any]:
	if body.kind not in SUPPORTED_JOB_KINDS:
		raise HTTPException(400, f"unsupported runtime job kind: {body.kind}")
	try:
		assert_opaque_credentials(body.payload, context="scheduled job payload")
		plane = _runtime_plane(project_id, ctx.tenant_id, body.environment_id, body.revision_hash)
		job = plane.scheduler.enqueue(
			body.kind, body.payload, max_attempts=body.max_attempts,
			idempotency_key=body.idempotency_key,
		)
	except Exception as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.runtime.enqueue", project_id=project_id, job_id=job.id, kind=job.kind)
	return job.to_dict()


@router.get("/runtime/jobs/{job_id}")
def get_runtime_job(
	project_id: str, job_id: str, revision_hash: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
	environment_id: str = "production",
) -> dict[str, Any]:
	try:
		plane = _runtime_plane(project_id, ctx.tenant_id, environment_id, revision_hash)
		return plane.scheduler.get(job_id).to_dict()
	except Exception as exc:
		raise _translate(exc) from exc


@router.post("/observability/events")
def ingest_telemetry(
	project_id: str, body: TelemetryEventBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict[str, Any]:
	try:
		assert_opaque_credentials(body.model_dump(), context="telemetry event")
		event = TelemetryEvent(
			id=body.id, tenant_id=ctx.tenant_id, entity_kind=body.entity_kind,
			entity_id=body.entity_id, entity_name=body.entity_name, signal=body.signal,
			status=body.status, started_at=body.started_at, duration_ms=body.duration_ms,
			trace_id=body.trace_id, application_id=project_id, workflow_id=body.workflow_id,
			agent_id=body.agent_id, environment=body.environment, message=body.message,
			tags=tuple(body.tags), attributes=body.attributes,
		)
		JsonlTelemetryRepository(_telemetry_root).append(event)
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc
	audit_request(request, ctx, "cmul8.telemetry.ingest", project_id=project_id, event_id=event.id)
	return event.to_dict()


@router.get("/observability")
def get_observability(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	from datetime import UTC, datetime
	repository = _ProjectTelemetryRepository(JsonlTelemetryRepository(_telemetry_root), project_id)
	payload = ObservabilityQueries(repository).api_payload(TelemetryQuery(tenant_id=ctx.tenant_id))
	payload["generated_at"] = datetime.now(UTC).isoformat()
	return payload


@router.get("/observability/{kind}/{entity_id}")
def get_observability_detail(
	project_id: str, kind: EntityKind, entity_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	kind = EntityKind(kind)
	repository = _ProjectTelemetryRepository(JsonlTelemetryRepository(_telemetry_root), project_id)
	detail = ObservabilityQueries(repository).detail(TelemetryQuery(tenant_id=ctx.tenant_id), kind, entity_id)
	if detail is None:
		raise HTTPException(404, "telemetry entity not found")
	return asdict(detail)
