"""Tenant-authorized CMUL8 Operation Graph and Project Room routes.

The legacy builder remains available, but these routes expose the durable V0
contracts instead of manufacturing multiplayer state in the browser.
"""

from __future__ import annotations

import os
import hashlib
import secrets
from dataclasses import asdict, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from apps.api.security import InvitationAcceptPrincipal, audit_request, require_invitation_accept_authenticated_email, require_project_access
from simulacra.collaboration import ActivityInbox, CollaborationService, JsonCollaborationRepository, PresenceRegistry
from simulacra.collaboration.invitation_acceptance import InvitationAcceptanceCoordinator, InvitationUnavailable
from simulacra.collaboration.models import Invitation, iso_now, new_id
from simulacra.collaboration.errors import CollaborationError
from simulacra.collaboration.models import CommentTargetType, ReviewDecision, TaskState
from simulacra.demo.identity import AuthContext, get_membership, get_user, get_user_by_email
from simulacra.demo.paths import RUNS_DIR
from simulacra.demo.runs import load_state, project_dir
from simulacra.missions import JsonMissionRepository, MissionService
from simulacra.harnesses import HarnessConfig, create_harness
from simulacra.operation_graph import OperationGraphStore
from simulacra.operation_graph.errors import OperationGraphError, UnapprovedRevisionError
from simulacra.runtime.security import assert_opaque_credentials
from simulacra.runtime import RuntimePlane, SUPPORTED_JOB_KINDS
from simulacra.workplace.assignment_coordinator import AssignmentCoordinator
from simulacra.observability import (
	EntityKind, EventStatus, JsonlTelemetryRepository, ObservabilityQueries,
	TelemetryEvent, TelemetryQuery,
)

router = APIRouter(prefix="/projects/{project_id}/cmul8", tags=["cmul8-v0"])
_collaboration_root = RUNS_DIR / ".cmul8-control"
_mission_root = RUNS_DIR / ".mission-control"
# Deployment processes use the same explicit roots; local development retains
# isolated project fixtures without requiring environment configuration.
_telemetry_root = Path(os.environ.get("CMUL8_TELEMETRY_ROOT", str(RUNS_DIR / ".cmul8-telemetry")))
_runtime_root = Path(os.environ.get("CMUL8_RUNTIME_ROOT", str(RUNS_DIR / ".cmul8-runtime")))
# Keep the final ``away`` boundary observable.  At exactly 180 seconds the
# product still reports away; only an older heartbeat is offline.
_presence = PresenceRegistry(ttl_seconds=181)


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


def _public_member(value: Any) -> dict[str, Any]:
	"""Expose a collaborator, never a room scope or internal actor class."""
	actor_id = str(getattr(value, "actor_id", ""))
	return {
		"actor_id": actor_id,
		"display_name": _display_name(actor_id, str(getattr(value, "display_name", ""))),
		"role": str(getattr(value, "role", "member")),
		# Project Room membership represents human collaborators. Mission agents
		# are rendered from the Mission crew, not misclassified from internal IDs.
		"actor_type": "human",
		"joined_at": str(getattr(value, "joined_at", "")),
	}


def _room_dict(room: Any) -> dict[str, Any]:
	"""A product room view, not the persisted collaboration envelope."""
	return {
		"id": str(room.id),
		"members": [_public_member(member) for member in room.members],
		"revision": int(room.revision),
		"created_at": str(room.created_at),
		"updated_at": str(room.updated_at),
	}


def _public_domain_event(value: dict[str, Any]) -> dict[str, Any]:
	"""Project Room activity is collaborative history, never execution telemetry."""
	public = {key: value[key] for key in {"id", "actor_type", "actor_id", "task_id", "action", "timestamp"} if key in value}
	# Domain events carry a small, product-facing outcome rather than an arbitrary
	# result object written by a worker or integration.
	outcome = str(value.get("result") or "").lower()
	public["result"] = outcome if outcome in {"succeeded", "failed", "rejected"} else "updated"
	payload = value.get("payload")
	if value.get("action") == "task.reviewed" and isinstance(payload, dict) and isinstance(payload.get("reviewer_role"), str):
		public["reviewer_role"] = payload["reviewer_role"]
	return public


def _public_runtime_job(value: dict[str, Any]) -> dict[str, Any]:
	return {key: value[key] for key in {"id", "kind", "status", "attempt", "created_at", "updated_at"} if key in value}


def _public_telemetry_event(value: dict[str, Any]) -> dict[str, Any]:
	return {key: value[key] for key in {"id", "entity_kind", "entity_id", "entity_name", "signal", "status", "started_at", "duration_ms"} if key in value}


def _public_task(value: dict[str, Any]) -> dict[str, Any]:
	"""Return the collaboration fields the room UI can safely display.

	Task ``result`` and ``activity`` are durable implementation records.  They are
	not a public transport for arbitrary worker output.
	"""
	return {
		key: value[key]
		for key in {
			"id", "title", "objective", "acceptance_criteria", "owner_id",
			"collaborator_ids", "state", "revision", "created_at", "updated_at",
		}
		if key in value
	}


_ASSIGNMENT_TASK_UNAVAILABLE = {
	"code": "mission_conflict",
	"message": "That Mission item changed. Refresh and try again.",
}


def _assignment_transaction_id(task: Any) -> str | None:
	"""Return the private admission link only for a coordinator-created task."""
	for item in getattr(task, "activity", []):
		if isinstance(item, dict) and isinstance(item.get("transaction_id"), str):
			return item["transaction_id"]
	return None


def _assignment_coordinator(repository: JsonCollaborationRepository, project_id: str) -> AssignmentCoordinator:
	return AssignmentCoordinator(
		repository,
		MissionService(JsonMissionRepository(_mission_root)),
		project_dir(project_id),
		runs_root=RUNS_DIR,
		clock=lambda: datetime.now(UTC).isoformat(),
	)


def _assignment_task_is_visible(
	repository: JsonCollaborationRepository, *, tenant_id: str, project_id: str, task: Any,
) -> bool:
	"""Legacy tasks stay available; assigned work needs one coherent admission."""
	transaction_id = _assignment_transaction_id(task)
	if transaction_id is None:
		return True
	result = _assignment_coordinator(repository, project_id).visible_result(
		tenant_id=tenant_id, project_id=project_id, transaction_id=transaction_id,
	)
	return result is not None and result.task_id == getattr(task, "id", None)


def _require_public_task(
	repository: JsonCollaborationRepository, *, tenant_id: str, project_id: str, task_id: str,
) -> None:
	try:
		task = repository.get_task(tenant_id, project_id, task_id)
	except CollaborationError:
		raise
	if not _assignment_task_is_visible(repository, tenant_id=tenant_id, project_id=project_id, task=task):
		raise HTTPException(409, dict(_ASSIGNMENT_TASK_UNAVAILABLE))


def _public_comment(value: dict[str, Any], plan_revision: int | None = None) -> dict[str, Any]:
	public = {key: value[key] for key in {"id", "author_id", "body", "created_at", "updated_at"} if key in value}
	public["status"] = "posted"
	if plan_revision is not None:
		public["plan_revision"] = plan_revision
	return public


def _public_review(value: Any) -> dict[str, Any]:
	"""Return the decision humans need, without its durable scope envelope."""
	reviewer_id = str(getattr(value, "reviewer_id", ""))
	return {
		"id": str(getattr(value, "id", "")),
		"task_id": str(getattr(value, "task_id", "")),
		"author_id": reviewer_id,
		"author_name": _display_name(reviewer_id),
		"role": str(getattr(value, "reviewer_role", "member")),
		"decision": str(getattr(value, "decision", "")),
		"comment": str(getattr(value, "body", "")),
		"task_revision": int(getattr(value, "task_revision", 1)),
		"created_at": str(getattr(value, "created_at", "")),
		"updated_at": str(getattr(value, "updated_at", "")),
	}


def _public_presence(value: Any) -> dict[str, Any]:
	"""Presence is a lightweight human status, not a tenant/project record."""
	return {
		"actor_id": str(getattr(value, "actor_id", "")),
		"status": str(getattr(value, "status", "active")),
		"last_seen_at": str(getattr(value, "last_seen_at", "")),
	}


def _require_graph_mutator(project_id: str, ctx: AuthContext) -> None:
	repository, _ = _collaboration()
	room = repository.visible_room(ctx.tenant_id, project_id)
	if _room_role(room, ctx.user.id) not in {"owner", "admin"}:
		raise HTTPException(403, "project room owner or admin role required for Mission plan changes")


def _translate(exc: Exception) -> HTTPException:
	name = type(exc).__name__.lower()
	if str(exc) == "idempotency_mismatch":
		return HTTPException(409, {"code": "idempotency_mismatch", "message": "That request ID was already used for different Mission changes."})
	if "notfound" in name or "not_found" in name:
		return HTTPException(404, {"code": "mission_not_found", "message": "The requested Mission item was not found."})
	if "authorization" in name or "unapproved" in name:
		return HTTPException(403, {"code": "mission_forbidden", "message": "You do not have permission for that Mission action."})
	if "conflict" in name or "transition" in name:
		return HTTPException(409, {"code": "mission_conflict", "message": "That Mission item changed. Refresh and try again."})
	return HTTPException(400, {"code": "mission_invalid", "message": "That Mission action could not be completed."})


class PublicBody(BaseModel):
	model_config = ConfigDict(extra="forbid")


class RoomCreateBody(PublicBody):
	display_name: str = Field(default="", max_length=120)


class RoomMemberBody(BaseModel):
	member_id: str | None = Field(default=None, min_length=1, max_length=200)
	member_email: str | None = Field(default=None, min_length=3, max_length=320)
	role: str = Field(default="member", pattern="^(owner|admin|member|viewer|reviewer|approver)$")
	expected_revision: int = Field(ge=1)


class InvitationCreateBody(PublicBody):
	client_request_id: str = Field(min_length=1, max_length=128)
	email: str = Field(min_length=3, max_length=320)
	role: str = Field(default="member", pattern="^(owner|admin|member|viewer|reviewer|approver)$")


class InvitationAcceptBody(PublicBody):
	client_request_id: str = Field(min_length=1, max_length=128)
	token: str = Field(min_length=20, max_length=512)


class InvitationRevokeBody(PublicBody):
	client_request_id: str = Field(min_length=1, max_length=128)
	expected_revision: int = Field(ge=1)


class MemberRemoveBody(PublicBody):
	client_request_id: str = Field(min_length=1, max_length=128)
	expected_room_revision: int = Field(ge=1)


class TaskCreateBody(PublicBody):
	title: str = Field(min_length=1, max_length=200)
	objective: str = Field(min_length=1, max_length=4000)
	acceptance_criteria: list[str] = Field(min_length=1, max_length=30)
	owner_id: str | None = None


class TaskTransitionBody(PublicBody):
	state: TaskState
	expected_revision: int = Field(ge=1)
	completion_summary: str | None = Field(default=None, max_length=4000)


class TaskReviewBody(BaseModel):
	decision: ReviewDecision
	expected_revision: int = Field(ge=1)
	note: str = Field(default="", max_length=4000)


class CommentCreateBody(PublicBody):
	"""A bounded Mission-plan discussion message."""
	body: str = Field(min_length=1, max_length=8000)
	plan_revision: int | None = Field(default=None, ge=1)


class CurrentPlanApprovalBody(PublicBody):
	expected_revision: int = Field(ge=1)


class GraphRevisionBody(BaseModel):
	"""Legacy route body retained only to return a stable retirement response."""
	model_config = ConfigDict(extra="forbid")


class TelemetryEventBody(PublicBody):
	"""A concise product progress signal, without control-plane metadata."""
	id: str
	entity_kind: EntityKind
	entity_id: str
	entity_name: str = Field(min_length=1, max_length=200)
	signal: str = Field(min_length=1, max_length=200)
	status: EventStatus
	started_at: str
	duration_ms: float = Field(default=0, ge=0)


class RuntimeJobBody(PublicBody):
	"""Deliberately empty: job dispatch is an internal Missions service boundary."""
	pass


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


def _public_mission_plan(graph: Any, approvals: list[Any], head_revision: int | None) -> dict[str, Any] | None:
	"""A review summary, not the underlying plan or its access controls."""
	if graph is None:
		return None
	contents = getattr(graph, "graph", {})
	metadata = contents.get("metadata", {}) if isinstance(contents, dict) else {}
	workflows = contents.get("workflows", []) if isinstance(contents, dict) else []
	approval_rules = contents.get("approval_rules", []) if isinstance(contents, dict) else []
	steps = [str(item.get("name") or "Mission step") for item in workflows if isinstance(item, dict)][:8]
	checkpoints = [str(item.get("name") or "Human checkpoint") for item in approval_rules if isinstance(item, dict)][:8]
	approved = any(getattr(item, "decision", "") == "approved" for item in approvals)
	return {
		# The head sequence changes even when a rollback points back to an older
		# immutable plan.  It is the optimistic-concurrency value reviewed by the UI.
		"revision": head_revision,
		"objective": str(metadata.get("description") or metadata.get("name") or "Mission plan"),
		"steps": steps,
		"human_checkpoints": checkpoints,
		"status": "approved" if approved else "pending_approval",
	}


def _mission_plan_snapshot(store: OperationGraphStore) -> dict[str, Any] | None:
	"""Read the displayed plan, head sequence, and approval state as one snapshot."""
	with store._locked():
		head = store._head()
		if head is None:
			return None
		graph = store.load_revision(str(head["revision_hash"]))
		approvals = store.list_approvals(graph.revision_hash)
		return _public_mission_plan(graph, approvals, int(head["revision"]))


def _current_approved_plan_revision(project_id: str, tenant_id: str) -> str | None:
	store = _graph_store(project_id, tenant_id)
	current = store.current_revision()
	if current is None:
		return None
	try:
		return store.require_approved_revision(current.revision_hash).revision_hash
	except UnapprovedRevisionError:
		raise HTTPException(409, "Review and approve the current Mission plan before creating Mission work.")


def _room_payload(project_id: str, ctx: AuthContext) -> dict[str, Any]:
	repository, _ = _collaboration()
	room = repository.visible_room(ctx.tenant_id, project_id)
	role = _room_role(room, ctx.user.id)
	if role is None:
		raise HTTPException(403, {"code": "membership_required", "message": "Project room membership is required."})
	state = load_state(project_id)
	tasks = [
		_public_task(task.to_dict())
		for task in repository.list_tasks(ctx.tenant_id, project_id)
		if _assignment_task_is_visible(repository, tenant_id=ctx.tenant_id, project_id=project_id, task=task)
	]
	comments = [_public_comment(comment.to_dict()) for comment in repository.list_comments(ctx.tenant_id, project_id)]
	reviews = [_public_review(review) for review in repository.list_reviews(ctx.tenant_id, project_id)]
	events = [_public_domain_event(event.to_dict()) for event in repository.list_events(ctx.tenant_id, project_id)]
	graph_store = _graph_store(project_id, ctx.tenant_id)
	mission_plan = _mission_plan_snapshot(graph_store)
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
		"mission_plan": mission_plan,
		"away": {
			"since": away.since, "total": away.total, "unread": away.unread,
			"counts": away.counts,
			"highlights": [
				 {"position": item.position, "category": item.category.value,
				  "unread": item.unread, "event": _public_domain_event(item.event.to_dict()), "deep_link": {"task_id": item.deep_link.get("task_id"), "section": item.deep_link.get("section")}}
				for item in away.highlights
			],
		},
		"presence": [_public_presence(item) for item in _presence.list_active(tenant_id=ctx.tenant_id, project_id=project_id)],
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


@router.post("/room/presence")
def heartbeat_presence(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	repository, _ = _collaboration()
	try:
		room = repository.visible_room(ctx.tenant_id, project_id)
		if ctx.user.id not in {member.actor_id for member in room.members}:
			raise HTTPException(403, "project room membership required")
		return {"presence": _public_presence(_presence.heartbeat(
			tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.user.id,
		))}
	except CollaborationError as exc:
		raise _translate(exc) from exc


def _invitation_unavailable() -> HTTPException:
	return HTTPException(404, {"code": "invitation_unavailable", "message": "This invitation is unavailable."})


@router.post("/room/invitations")
def create_invitation(project_id: str, body: InvitationCreateBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))]) -> dict[str, Any]:
	repository, _ = _collaboration()
	room = repository.visible_room(ctx.tenant_id, project_id)
	if _room_role(room, ctx.user.id) not in {"owner", "admin"}:
		raise HTTPException(403, {"code": "mission_forbidden", "message": "You do not have permission for that Mission action."})
	token = secrets.token_urlsafe(32)
	now = datetime.now(UTC)
	from datetime import timedelta
	invitation = Invitation(id=new_id("invite"), tenant_id=ctx.tenant_id, project_id=project_id,
		invited_by=ctx.user.id, invitee_email=body.email.strip().lower(), requested_role=body.role,
		accept_token_digest=hashlib.sha256(token.encode("utf-8")).hexdigest(), status="pending",
		expires_at=(now + timedelta(days=7)).isoformat())
	repository.create_invitation(invitation)
	audit_request(request, ctx, "cmul8.room.invitation_create", project_id=project_id, invitation_id=invitation.id)
	# The token is an enrollment credential returned once; it is deliberately not
	# included in the durable invitation serializer or an audit event.
	return {"invitation": {"id": invitation.id, "status": invitation.status, "expires_at": invitation.expires_at, "revision": invitation.revision}, "token": token}


@router.post("/room/invitations/{invitation_id}/accept")
def accept_invitation(project_id: str, invitation_id: str, body: InvitationAcceptBody,
	principal: Annotated[InvitationAcceptPrincipal, Depends(require_invitation_accept_authenticated_email)]) -> dict[str, Any]:
	try:
		state = load_state(project_id)
		repository, _ = _collaboration()
		accepted, member = InvitationAcceptanceCoordinator(repository).accept(
			tenant_id=state.tenant_id, project_id=project_id, invitation_id=invitation_id,
			actor_id=principal.actor_id, verified_email=principal.verified_email,
			client_request_id=body.client_request_id, token=body.token)
		return {"membership": {"actor_id": member.actor_id, "role": member.role}, "invitation": {"id": accepted.id, "status": accepted.status, "revision": accepted.revision}}
	except Exception as exc:
		# Email, token, tenant, project, expiry, revocation and reuse are one surface.
		raise _invitation_unavailable() from exc


@router.post("/room/invitations/{invitation_id}/revoke")
def revoke_invitation(project_id: str, invitation_id: str, body: InvitationRevokeBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))]) -> dict[str, Any]:
	_, service = _collaboration()
	try:
		updated = service.revoke_invitation(
			tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.user.id,
			invitation_id=invitation_id, client_request_id=body.client_request_id,
			expected_revision=body.expected_revision,
		)
		audit_request(request, ctx, "cmul8.room.invitation_revoke", project_id=project_id, invitation_id=invitation_id)
		return {"invitation": {"id": updated.id, "status": updated.status, "revision": updated.revision}}
	except HTTPException:
		raise
	except CollaborationError as exc:
		if type(exc).__name__ == "NotFoundError":
			raise _invitation_unavailable() from exc
		raise _translate(exc) from exc


@router.post("/room/members/{actor_id}/remove")
def remove_room_member(project_id: str, actor_id: str, body: MemberRemoveBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))]) -> dict[str, Any]:
	repository, service = _collaboration()
	try:
		room = service.remove_member(tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.user.id,
			member_id=actor_id, client_request_id=body.client_request_id,
			expected_room_revision=body.expected_room_revision)
		_presence.leave(tenant_id=ctx.tenant_id, project_id=project_id, actor_id=actor_id)
		audit_request(request, ctx, "cmul8.room.member_remove", project_id=project_id, member_id=actor_id)
		# A raw room can retain incomplete acceptance rows for recovery. They never
		# become public merely because a different member was removed.
		public_room = replace(room, members=repository.visible_members(room))
		return _room_dict(public_room)
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
		store = _graph_store(project_id, ctx.tenant_id)
		# Keep the graph admission lock through the collaboration write. A rollback
		# cannot make this plan stale between its approval check and durable task
		# creation; graph lock precedes the collaboration transaction consistently.
		with store.locked_current_approved_revision() as current:
			if current is None and store.current_revision() is not None:
				raise HTTPException(409, "Review and approve the current Mission plan before creating Mission work.")
			task = service.create_task(
				tenant_id=ctx.tenant_id, project_id=project_id, actor_id=ctx.user.id,
				title=body.title, objective=body.objective, acceptance_criteria=body.acceptance_criteria,
				owner_id=body.owner_id, operation_graph_version=current.revision_hash if current else None,
			)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.task.create", project_id=project_id, task_id=task.id)
	return _public_task(task.to_dict())


@router.post("/tasks/{task_id}/transition")
def transition_task(
	project_id: str, task_id: str, body: TaskTransitionBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	repository, service = _collaboration()
	try:
		_require_public_task(repository, tenant_id=ctx.tenant_id, project_id=project_id, task_id=task_id)
		task = service.transition_task(
			tenant_id=ctx.tenant_id, project_id=project_id, task_id=task_id, actor_id=ctx.user.id,
		to_state=body.state, expected_revision=body.expected_revision,
		result={"summary": body.completion_summary} if body.completion_summary else None,
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.task.transition", project_id=project_id, task_id=task_id)
	return _public_task(task.to_dict())


@router.post("/tasks/{task_id}/claim")
def claim_task(
	project_id: str, task_id: str, expected_revision: int, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	repository, service = _collaboration()
	try:
		_require_public_task(repository, tenant_id=ctx.tenant_id, project_id=project_id, task_id=task_id)
		task = service.claim_task(
			tenant_id=ctx.tenant_id, project_id=project_id, task_id=task_id,
			actor_id=ctx.user.id, expected_revision=expected_revision,
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.task.claim", project_id=project_id, task_id=task_id)
	return _public_task(task.to_dict())


@router.post("/tasks/{task_id}/reviews")
def review_task(
	project_id: str, task_id: str, body: TaskReviewBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	repository, service = _collaboration()
	try:
		_require_public_task(repository, tenant_id=ctx.tenant_id, project_id=project_id, task_id=task_id)
		review, task = service.review_task(
			tenant_id=ctx.tenant_id, project_id=project_id, task_id=task_id,
			reviewer_id=ctx.user.id, decision=body.decision,
			expected_revision=body.expected_revision, body=body.note,
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.task.review", project_id=project_id, task_id=task_id)
	return {"review": _public_review(review), "task": _public_task(task.to_dict())}


@router.post("/comments")
def create_comment(
	project_id: str, body: CommentCreateBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	_, service = _collaboration()
	try:
		comment = service.add_comment(
			tenant_id=ctx.tenant_id, project_id=project_id, author_id=ctx.user.id,
			body=body.body, target_type=CommentTargetType.PROJECT, target_id=project_id,
		)
	except CollaborationError as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.comment.create", project_id=project_id, comment_id=comment.id)
	return _public_comment(comment.to_dict(), body.plan_revision)


@router.post("/operation-graph/revisions")
def create_graph_revision(
	project_id: str, body: GraphRevisionBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	raise HTTPException(410, "Mission plans are managed by Missions.")


@router.post("/operation-graph/revisions/{revision_hash}/approve")
def approve_graph_revision(
	project_id: str, revision_hash: str, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	raise HTTPException(410, "Mission plans are managed by Missions.")


@router.post("/operation-graph/current/approve")
def approve_current_graph_revision(
	project_id: str, body: CurrentPlanApprovalBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	"""Approve the current Mission plan without publishing its revision hash."""
	try:
		_require_graph_mutator(project_id, ctx)
		store = _graph_store(project_id, ctx.tenant_id)
		with store._locked():
			current = store.current_revision()
			head = store._head()
			if current is None or head is None:
				raise ValueError("no current Mission plan")
			# Graph revisions are immutable and can reappear after a rollback.  Compare
			# the monotonic head sequence shown to the reviewer, not that graph's own
			# historical revision number.
			if int(head["revision"]) != body.expected_revision:
				raise HTTPException(409, "This Mission plan changed. Refresh and review it again.")
			approval = store.approve_revision(current.revision_hash, actor_id=ctx.user.id)
	except (CollaborationError, OperationGraphError, ValueError) as exc:
		raise _translate(exc) from exc
	audit_request(request, ctx, "cmul8.graph.approve", project_id=project_id)
	return {"revision": body.expected_revision, "status": "approved"}


@router.get("/harness")
async def harness_status(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	config = HarnessConfig.from_env()
	harness = create_harness(config)
	health = dict(await harness.healthcheck())
	return {"status": "available" if health.get("ok", True) else "unavailable"}


@router.post("/runtime/jobs")
def enqueue_runtime_job(
	project_id: str, body: RuntimeJobBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict[str, Any]:
	# Mission execution is dispatched by the Mission service.  This historical
	# control-plane route remains mounted for compatibility but cannot accept a
	# user-supplied environment or opaque payload.
	raise HTTPException(410, "Mission work is managed by Missions.")


@router.get("/runtime/jobs/{job_id}")
def get_runtime_job(
	project_id: str, job_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	raise HTTPException(410, "Mission work is managed by Missions.")


@router.post("/observability/events")
def ingest_telemetry(
	project_id: str, body: TelemetryEventBody, request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict[str, Any]:
	try:
		event = TelemetryEvent(
			id=body.id, tenant_id=ctx.tenant_id, entity_kind=body.entity_kind,
			entity_id=body.entity_id, entity_name=body.entity_name, signal=body.signal,
			status=body.status, started_at=body.started_at, duration_ms=body.duration_ms,
			application_id=project_id,
		)
		JsonlTelemetryRepository(_telemetry_root).append(event)
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc
	audit_request(request, ctx, "cmul8.telemetry.ingest", project_id=project_id, event_id=event.id)
	return _public_telemetry_event(event.to_dict())


@router.get("/observability")
def get_observability(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, Any]:
	from datetime import UTC, datetime
	repository = _ProjectTelemetryRepository(JsonlTelemetryRepository(_telemetry_root), project_id)
	payload = ObservabilityQueries(repository).api_payload(TelemetryQuery(tenant_id=ctx.tenant_id))
	payload["generated_at"] = datetime.now(UTC).isoformat()
	overview = payload.get("overview", {})
	return {
		"overview": {key: overview.get(key) for key in {"runs", "errors", "warnings", "success_rate", "p95_ms"}},
		"inventories": {
			kind: [
				{key: item.get(key) for key in {"id", "name", "kind", "health", "runs", "errors", "success_rate", "p95_ms", "last_seen_at"}}
				for item in values
			]
			for kind, values in payload.get("inventories", {}).items()
		},
		"generated_at": payload["generated_at"],
	}


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
	item = asdict(detail.item)
	return {
		"item": {key: item.get(key) for key in {"id", "name", "kind", "health", "runs", "errors", "success_rate", "p95_ms", "last_seen_at"}},
		"recent_events": [_public_telemetry_event(event.to_dict()) for event in detail.recent_events],
	}
