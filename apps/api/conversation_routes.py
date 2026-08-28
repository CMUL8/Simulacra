"""Public Mission conversation routes.

The route layer deliberately exposes only durable collaboration records.  An
assignment is not readable here until the coordinator has made its complete,
cross-store decision; a queued child on its own is never a public message.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Any, Mapping

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError as PydanticValidationError

from apps.api.security import get_auth
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.collaboration.conversation import (
    ConversationConflictError,
    ConversationMessageView,
    serialize_conversation_message,
)
from simulacra.collaboration.errors import AuthorizationError, NotFoundError, ValidationError
from simulacra.collaboration.models import ConversationMessage, Task
from simulacra.demo.identity import AuthContext
from simulacra.demo.paths import RUNS_DIR
from simulacra.demo.runs import project_dir
from simulacra.missions import JsonMissionRepository, MissionConflictError, MissionService
from simulacra.missions.projections import WorkItem, serialize_work_item
from simulacra.operation_graph import OperationGraphStore
from simulacra.operation_graph.errors import UnapprovedRevisionError
from simulacra.workplace.assignment_coordinator import AssignmentCoordinator, AssignmentError, AssignmentResult


router = APIRouter(prefix="/projects/{project_id}/conversation", tags=["workplace-conversation"])
_mission_root = RUNS_DIR / ".mission-control"
_collaboration_root = RUNS_DIR / ".cmul8-control"
_runs_root = RUNS_DIR
_cursor_secret = os.environ.get("SIMULACRA_WORKPLACE_CURSOR_SECRET", "simulacra-workplace-development-cursor-key")
_CURSOR_MESSAGE = "This conversation changed. Refresh and try again."
_FORBIDDEN_MESSAGE = "You do not have access to this Mission conversation."
_INVALID_MESSAGE = "This message could not be completed. Check the details and try again."
_CONFLICT_MESSAGE = "This message changed. Refresh and try again."
_NOT_FOUND_MESSAGE = "This Mission conversation item is unavailable."
_PLAN_MESSAGE = "This Mission needs an approved plan before work can begin."


class PublicBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ConversationCreateBody(PublicBody):
    client_request_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=8000)
    mode: str = Field(pattern="^(message|assignment)$")
    assignee_agent_ids: list[str] = Field(default_factory=list, max_length=32)
    reviewer_human_ids: list[str] = Field(default_factory=list, max_length=128)
    source_message_id: str | None = Field(default=None, max_length=128)


class ConversationPatchBody(PublicBody):
    client_request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)
    body: str = Field(min_length=1, max_length=8000)


class ConversationDeleteBody(PublicBody):
    client_request_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1)


class ConversationReplyBody(PublicBody):
    client_request_id: str = Field(min_length=1, max_length=128)
    body: str = Field(min_length=1, max_length=8000)


class ConversationActionBody(PublicBody):
    client_request_id: str = Field(min_length=1, max_length=128)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"code": code, "message": message})


def _parse_body(model: type[BaseModel], value: Any) -> Any:
    """Keep every JSON shape/field validation failure on the public envelope."""
    try:
        return model.model_validate(value)
    except PydanticValidationError as exc:
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE) from exc


def _collaboration() -> JsonCollaborationRepository:
    return JsonCollaborationRepository(_collaboration_root)


def _mission_service() -> MissionService:
    return MissionService(JsonMissionRepository(_mission_root))


def _coordinator_for(project_id: str) -> AssignmentCoordinator:
    return AssignmentCoordinator(
        _collaboration(), _mission_service(), project_dir(project_id), runs_root=_runs_root,
        clock=lambda: datetime.now(UTC).isoformat(),
    )


def _member_role(repository: JsonCollaborationRepository, *, tenant_id: str, project_id: str, human_id: str) -> str:
    try:
        room = repository.get_room(tenant_id, project_id)
    except Exception as exc:
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE) from exc
    member = repository.visible_member(room, human_id)
    if member is None:
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE)
    return member.role


def _require_current_member_locked(repository: JsonCollaborationRepository, room: Any, human_id: str) -> None:
    if repository.visible_member(room, human_id) is None:
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE)


def _require_current_member_at_publication(
    repository: JsonCollaborationRepository, *, tenant_id: str, project_id: str, human_id: str,
) -> None:
    """Do not publish an already-committed result to a removed collaborator."""
    with repository.room_lock(tenant_id, project_id) as room:
        _require_current_member_locked(repository, room, human_id)


def _require_message_member(repository: JsonCollaborationRepository, *, tenant_id: str, project_id: str, human_id: str) -> None:
    role = _member_role(repository, tenant_id=tenant_id, project_id=project_id, human_id=human_id)
    if role == "viewer":
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE)


def _require_reviewer_members(repository: JsonCollaborationRepository, *, tenant_id: str, project_id: str, reviewer_ids: list[str]) -> None:
    if len(set(reviewer_ids)) != len(reviewer_ids):
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE)
    try:
        members = {item.actor_id for item in repository.visible_room(tenant_id, project_id).members}
    except Exception as exc:
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE) from exc
    if any(not isinstance(item, str) or item not in members for item in reviewer_ids):
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE)


def _require_agents(service: MissionService, *, tenant_id: str, project_id: str, agent_ids: list[str]) -> None:
    if not agent_ids or len(set(agent_ids)) != len(agent_ids):
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE)
    try:
        allowed = {agent.id for agent in service.agents(tenant_id, project_id)}
    except Exception as exc:
        raise _error(404, "conversation_unavailable", _NOT_FOUND_MESSAGE) from exc
    if any(not isinstance(item, str) or item not in allowed for item in agent_ids):
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE)


def _validate_source_message(service: CollaborationService, *, tenant_id: str, project_id: str, source_message_id: str | None) -> None:
    if source_message_id is None:
        return
    try:
        if not any(message.id == source_message_id for message in service.conversation_messages(tenant_id, project_id)):
            raise _error(404, "conversation_unavailable", _NOT_FOUND_MESSAGE)
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE) from exc


def _current_approved_revision(project_id: str, tenant_id: str) -> str:
    try:
        store = OperationGraphStore(project_dir(project_id), tenant_id=tenant_id, project_id=project_id)
        current = store.current_revision()
        if current is None:
            raise UnapprovedRevisionError("unapproved")
        return store.require_approved_revision(current.revision_hash).revision_hash
    except UnapprovedRevisionError as exc:
        raise _error(409, "plan_unapproved", _PLAN_MESSAGE) from exc
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(409, "plan_unapproved", _PLAN_MESSAGE) from exc


def _assignment_fields(body: str) -> tuple[str, str, list[str]]:
    """Deterministically derive internal work fields from the public message.

    The first non-empty line becomes a compact title.  The full screened body
    is the objective; completion always requires a reviewable result.  This is
    intentionally deterministic so a retry cannot invent a different task.
    """
    cleaned = body.strip()
    first_line = next((line.strip() for line in cleaned.splitlines() if line.strip()), cleaned)
    title = first_line[:160].rstrip() or "Mission work"
    return title, cleaned, ["Provide a reviewable result with the supporting evidence."]


def _message_author(message: ConversationMessage, repository: JsonCollaborationRepository, tenant_id: str, project_id: str) -> dict[str, Any]:
    raw = message.author if isinstance(message.author, Mapping) else {}
    author_id = raw.get("id") if isinstance(raw.get("id"), str) else ""
    kind = raw.get("kind") if isinstance(raw.get("kind"), str) else "human"
    display_name = ""
    if kind == "human":
        try:
            room = repository.get_room(tenant_id, project_id)
            member = repository.visible_member(room, author_id)
            display_name = member.display_name if member is not None else ""
        except Exception:
            display_name = ""
    else:
        try:
            display_name = next((agent.name for agent in _mission_service().agents(tenant_id, project_id) if agent.id == author_id), "")
        except Exception:
            display_name = ""
    return {"id": author_id, "kind": kind, "display_name": display_name or ("A human" if kind == "human" else "Mission agent"), "avatar_url": None}


def _public_message(message: ConversationMessage, *, repository: JsonCollaborationRepository, tenant_id: str, project_id: str,
                    assignment: AssignmentResult | None = None, viewer_id: str | None = None,
                    service: CollaborationService | None = None,
                    reaction_projection: tuple[dict[str, Any], ...] | None = None,
                    view_projection: ConversationMessageView | None = None) -> dict[str, Any]:
    thread: dict[str, Any] | None = None
    reactions: tuple[dict[str, Any], ...] = ()
    saved = False
    if view_projection is not None:
        view = view_projection
        latest = [
            _public_message(
                reply, repository=repository, tenant_id=tenant_id, project_id=project_id,
                viewer_id=viewer_id, service=service,
            )
            for reply in view.thread["latest_replies"]
        ]
        thread = {"reply_count": view.thread["reply_count"], "latest_replies": latest}
        reactions = view.reactions
        saved = view.saved
    elif viewer_id is not None:
        service = service or CollaborationService(repository)
        view = service.conversation_message_view(tenant_id, project_id, message.id, viewer_id)
        latest = [
            _public_message(
                reply, repository=repository, tenant_id=tenant_id, project_id=project_id,
                viewer_id=viewer_id, service=service,
            )
            for reply in view.thread["latest_replies"]
        ]
        thread = {"reply_count": view.thread["reply_count"], "latest_replies": latest}
        reactions = view.reactions
        saved = view.saved
    if reaction_projection is not None:
        reactions = reaction_projection
    public_message = replace(
        message, author=_message_author(message, repository, tenant_id, project_id),
    )
    row = serialize_conversation_message(public_message, thread=thread, reactions=reactions, saved=saved)
    if assignment is not None:
        row["links"] = {"work_item_id": assignment.task_id, "run_id": assignment.run_id, "output_id": None}
    return row


def _public_work_item(*, task: Task, assignment: AssignmentResult, service: MissionService) -> dict[str, Any]:
    agents = {agent.id: agent for agent in service.agents(task.tenant_id, task.project_id)}
    run = next((item for item in service.runs(task.tenant_id, task.project_id) if item.id == assignment.run_id), None)
    primary = next(iter(run.assigned_agent_ids), None) if run is not None else None
    agent = agents.get(primary) if primary else None
    assignee = None if agent is None else {"id": agent.id, "kind": "agent", "display_name": agent.name, "avatar_url": None}
    return serialize_work_item(WorkItem(
        source_type="assignment", source_id=task.id, mission_id=task.project_id, revision=task.revision,
        title=task.title, summary=task.objective, state=task.state.value, assignee=assignee,
        created_at=task.created_at, updated_at=task.updated_at, allowed_actions=[],
    ))


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _cursor_encode(*, tenant_id: str, project_id: str, before: tuple[str, str], scope: str = "conversation") -> str:
    payload = json.dumps({"tenant": tenant_id, "project": project_id, "scope": scope, "before": before}, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature = hmac.new(_cursor_secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def _cursor_decode(value: str, *, tenant_id: str, project_id: str, scope: str = "conversation") -> tuple[str, str]:
    try:
        encoded, encoded_signature = value.split(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        signature = base64.urlsafe_b64decode(encoded_signature + "=" * (-len(encoded_signature) % 4))
        expected = hmac.new(_cursor_secret.encode("utf-8"), payload, hashlib.sha256).digest()
        decoded = json.loads(payload)
        before = decoded.get("before") if isinstance(decoded, dict) else None
        if (not hmac.compare_digest(signature, expected) or decoded.get("tenant") != tenant_id
                or decoded.get("project") != project_id or not isinstance(before, list) or len(before) != 2
                or decoded.get("scope") != scope
                or not all(isinstance(item, str) and item for item in before)):
            raise ValueError("invalid")
        return before[0], before[1]
    except Exception as exc:
        raise _error(400, "cursor_invalid", _CURSOR_MESSAGE) from exc


def _visible_messages(*, tenant_id: str, project_id: str, repository: JsonCollaborationRepository,
                      coordinator: AssignmentCoordinator, service: CollaborationService) -> list[tuple[ConversationMessage, AssignmentResult | None]]:
    rows: list[tuple[ConversationMessage, AssignmentResult | None]] = []
    for message in service.conversation_roots(tenant_id, project_id):
        transaction_id = message.links.get("transaction_id") if isinstance(message.links, Mapping) else None
        if isinstance(transaction_id, str):
            result = coordinator.visible_result(tenant_id=tenant_id, project_id=project_id, transaction_id=transaction_id)
            if result is None:
                continue
            rows.append((message, result))
        else:
            rows.append((message, None))
    return sorted(rows, key=lambda item: (item[0].created_at, item[0].id))


def _assignment_for_message(message: ConversationMessage, *, tenant_id: str, project_id: str,
                            coordinator: AssignmentCoordinator) -> AssignmentResult | None:
    transaction_id = message.links.get("transaction_id") if isinstance(message.links, Mapping) else None
    if not isinstance(transaction_id, str):
        return None
    return coordinator.visible_result(tenant_id=tenant_id, project_id=project_id, transaction_id=transaction_id)


@router.get("")
def get_conversation(
    project_id: str, before: str | None = None, limit: int = Query(50),
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise _error(400, "cursor_invalid", _CURSOR_MESSAGE)
    repository = _collaboration()
    _member_role(repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id)
    coordinator = _coordinator_for(project_id)
    service = CollaborationService(repository)
    rows = _visible_messages(tenant_id=ctx.tenant_id, project_id=project_id, repository=repository, coordinator=coordinator, service=service)
    if before:
        boundary = _cursor_decode(before, tenant_id=ctx.tenant_id, project_id=project_id)
        rows = [item for item in rows if (item[0].created_at, item[0].id) < boundary]
    page = rows[-limit:]
    next_before = _cursor_encode(tenant_id=ctx.tenant_id, project_id=project_id, before=(page[0][0].created_at, page[0][0].id)) if len(rows) > len(page) and page else None
    # The first membership check prevents unauthorized detail reads.  This
    # second check is the publication boundary: a revoked human never receives
    # an already-built conversation page during a concurrent room update.
    with repository.room_lock(ctx.tenant_id, project_id) as room:
        _require_current_member_locked(repository, room, ctx.user.id)
        items = [
            _public_message(
                message, repository=repository, tenant_id=ctx.tenant_id, project_id=project_id,
                assignment=assignment, viewer_id=ctx.user.id, service=service,
            )
            for message, assignment in page
        ]
    return {
        "items": items,
        "next_before": next_before,
    }


@router.get("/messages/{message_id}/replies")
def get_replies(
    project_id: str, message_id: str, before: str | None = None, limit: int = Query(50),
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise _error(400, "cursor_invalid", _CURSOR_MESSAGE)
    repository = _collaboration()
    _member_role(repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id)
    service = CollaborationService(repository)
    try:
        rows = service.conversation_replies(ctx.tenant_id, project_id, message_id)
    except NotFoundError as exc:
        raise _error(404, "conversation_unavailable", _NOT_FOUND_MESSAGE) from exc
    all_messages = service.conversation_messages(ctx.tenant_id, project_id)
    parent = next((item for item in all_messages if item.id == message_id), None)
    if parent is None:
        raise _error(404, "conversation_unavailable", _NOT_FOUND_MESSAGE)
    root_id = parent.root_message_id or parent.id
    cursor_scope = f"replies:{root_id}"
    if before:
        boundary = _cursor_decode(
            before, tenant_id=ctx.tenant_id, project_id=project_id, scope=cursor_scope,
        )
        rows = [item for item in rows if (item.created_at, item.id) < boundary]
    page = rows[-limit:]
    next_before = _cursor_encode(
        tenant_id=ctx.tenant_id, project_id=project_id,
        before=(page[0].created_at, page[0].id), scope=cursor_scope,
    ) if len(rows) > len(page) and page else None
    with repository.room_lock(ctx.tenant_id, project_id) as room:
        _require_current_member_locked(repository, room, ctx.user.id)
        items = [
            _public_message(
                item, repository=repository, tenant_id=ctx.tenant_id, project_id=project_id,
                viewer_id=ctx.user.id, service=service,
            )
            for item in page
        ]
    return {"items": items, "next_before": next_before}


@router.post("/messages")
def post_message(
    project_id: str, body: Annotated[Any, Body()] = None,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    body = _parse_body(ConversationCreateBody, body)
    repository = _collaboration()
    _require_message_member(repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id)
    conversation = CollaborationService(repository)
    if body.mode == "message":
        if body.assignee_agent_ids or body.reviewer_human_ids:
            raise _error(400, "conversation_invalid", _INVALID_MESSAGE)
        try:
            _validate_source_message(conversation, tenant_id=ctx.tenant_id, project_id=project_id, source_message_id=body.source_message_id)
            message = conversation.create_conversation_message(
                tenant_id=ctx.tenant_id, project_id=project_id, authenticated_human_actor_id=ctx.user.id,
                client_request_id=body.client_request_id, body=body.body, source_message_id=body.source_message_id,
            )
        except (AuthorizationError, PermissionError) as exc:
            raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE) from exc
        except (ConversationConflictError, MissionConflictError) as exc:
            raise _error(409, "idempotency_mismatch", _CONFLICT_MESSAGE) from exc
        except (ValidationError, ValueError) as exc:
            raise _error(400, "conversation_invalid", _INVALID_MESSAGE) from exc
        return {"message": _public_message(
            message, repository=repository, tenant_id=ctx.tenant_id, project_id=project_id,
            viewer_id=ctx.user.id, service=conversation,
        ), "work_item": None}

    title, objective, criteria = _assignment_fields(body.body)
    coordinator = _coordinator_for(project_id)
    service = _mission_service()
    try:
        result = coordinator.replay_if_exists(
            tenant_id=ctx.tenant_id, project_id=project_id, authenticated_human_actor_id=ctx.user.id,
            client_request_id=body.client_request_id, body=body.body, title=title, objective=objective,
            acceptance_criteria=criteria, assigned_agent_ids=body.assignee_agent_ids,
            reviewer_human_ids=body.reviewer_human_ids, source_message_id=body.source_message_id,
        )
        if result is None:
            _validate_source_message(conversation, tenant_id=ctx.tenant_id, project_id=project_id, source_message_id=body.source_message_id)
            _require_agents(service, tenant_id=ctx.tenant_id, project_id=project_id, agent_ids=body.assignee_agent_ids)
            _require_reviewer_members(repository, tenant_id=ctx.tenant_id, project_id=project_id, reviewer_ids=body.reviewer_human_ids)
            revision = _current_approved_revision(project_id, ctx.tenant_id)
            result = coordinator.assign(
                tenant_id=ctx.tenant_id, project_id=project_id, authenticated_human_actor_id=ctx.user.id,
                client_request_id=body.client_request_id, body=body.body, title=title, objective=objective,
                acceptance_criteria=criteria, assigned_agent_ids=body.assignee_agent_ids, graph_revision=revision,
                reviewer_human_ids=body.reviewer_human_ids, source_message_id=body.source_message_id,
            )
        if result.state != "COMPLETE":
            raise AssignmentError("assignment_unavailable")
        message = next((item for item in conversation.conversation_messages(ctx.tenant_id, project_id) if item.id == result.message_id), None)
        task = next((item for item in repository.list_tasks(ctx.tenant_id, project_id) if item.id == result.task_id), None)
        if message is None or task is None or coordinator.visible_result(
            tenant_id=ctx.tenant_id, project_id=project_id, transaction_id=result.transaction_id,
        ) is None:
            raise AssignmentError("assignment_unavailable")
        # The assignment may have finished while a room administrator removed
        # the caller.  Its durable retry record remains intact, but no result
        # is published across that current-membership boundary.
        _require_current_member_at_publication(
            repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id,
        )
        # The assignment may have finished while a room administrator removed
        # the caller.  Its durable retry record remains intact, but no result
        # is published across that current-membership boundary.
    except AssignmentError as exc:
        code = str(exc)
        if code == "idempotency_mismatch":
            raise _error(409, "idempotency_mismatch", _CONFLICT_MESSAGE) from exc
        if code == "assignment_invalid":
            raise _error(400, "conversation_invalid", _INVALID_MESSAGE) from exc
        if code == "transaction_aborted":
            raise _error(409, "assignment_unavailable", _CONFLICT_MESSAGE) from exc
        raise _error(409, "assignment_unavailable", _CONFLICT_MESSAGE) from exc
    except (AuthorizationError, PermissionError) as exc:
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE) from exc
    except (MissionConflictError, ValueError) as exc:
        raise _error(409, "assignment_unavailable", _CONFLICT_MESSAGE) from exc
    return {
        "message": _public_message(
            message, repository=repository, tenant_id=ctx.tenant_id, project_id=project_id,
            assignment=result, viewer_id=ctx.user.id, service=conversation,
        ),
        "work_item": _public_work_item(task=task, assignment=result, service=service),
    }


@router.post("/messages/{message_id}/replies")
def post_reply(
    project_id: str, message_id: str, body: Annotated[Any, Body()] = None,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    body = _parse_body(ConversationReplyBody, body)
    repository = _collaboration()
    _require_message_member(
        repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id,
    )
    service = CollaborationService(repository)
    try:
        message = service.reply_to_conversation_message(
            tenant_id=ctx.tenant_id, project_id=project_id,
            authenticated_human_actor_id=ctx.user.id, parent_message_id=message_id,
            client_request_id=body.client_request_id, body=body.body,
        )
        response_view = service.conversation_reply_response_view(
            tenant_id=ctx.tenant_id, project_id=project_id,
            authenticated_human_actor_id=ctx.user.id, parent_message_id=message_id,
            client_request_id=body.client_request_id, body=body.body,
        )
        _require_current_member_at_publication(
            repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id,
        )
    except ConversationConflictError as exc:
        code = "idempotency_mismatch" if str(exc) == "idempotency_mismatch" else "conversation_unavailable"
        status = 409 if code == "idempotency_mismatch" else 404
        raise _error(status, code, _CONFLICT_MESSAGE if status == 409 else _NOT_FOUND_MESSAGE) from exc
    except (AuthorizationError, PermissionError) as exc:
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE) from exc
    except NotFoundError as exc:
        raise _error(404, "conversation_unavailable", _NOT_FOUND_MESSAGE) from exc
    except (ValidationError, ValueError) as exc:
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE) from exc
    return {
        "message": _public_message(
            message, repository=repository, tenant_id=ctx.tenant_id, project_id=project_id,
            viewer_id=ctx.user.id, service=service, view_projection=response_view,
        ),
    }


def _change_reaction_route(
    *, method: str, project_id: str, message_id: str, reaction: str,
    body: Any, ctx: AuthContext,
) -> dict[str, Any]:
    body = _parse_body(ConversationActionBody, body)
    repository = _collaboration()
    _require_message_member(
        repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id,
    )
    service = CollaborationService(repository)
    try:
        command = service.put_conversation_reaction if method == "PUT" else service.delete_conversation_reaction
        result = command(
            tenant_id=ctx.tenant_id, project_id=project_id,
            authenticated_human_actor_id=ctx.user.id, message_id=message_id,
            reaction=reaction, client_request_id=body.client_request_id,
        )
        _require_current_member_at_publication(
            repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id,
        )
    except ConversationConflictError as exc:
        code = "idempotency_mismatch" if str(exc) == "idempotency_mismatch" else "conversation_unavailable"
        status = 409 if code == "idempotency_mismatch" else 404
        raise _error(status, code, _CONFLICT_MESSAGE if status == 409 else _NOT_FOUND_MESSAGE) from exc
    except (AuthorizationError, PermissionError) as exc:
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE) from exc
    except NotFoundError as exc:
        raise _error(404, "conversation_unavailable", _NOT_FOUND_MESSAGE) from exc
    except (ValidationError, ValueError) as exc:
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE) from exc
    return {
        "message": _public_message(
            result.message, repository=repository, tenant_id=ctx.tenant_id, project_id=project_id,
            viewer_id=ctx.user.id, service=service,
            view_projection=ConversationMessageView(
                result.message, result.thread, result.reactions, result.saved,
            ),
        ),
    }


@router.put("/messages/{message_id}/reactions/{reaction}")
def put_reaction(
    project_id: str, message_id: str, reaction: str, body: Annotated[Any, Body()] = None,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    return _change_reaction_route(
        method="PUT", project_id=project_id, message_id=message_id,
        reaction=reaction, body=body, ctx=ctx,
    )


@router.delete("/messages/{message_id}/reactions/{reaction}")
def delete_reaction(
    project_id: str, message_id: str, reaction: str, body: Annotated[Any, Body()] = None,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    return _change_reaction_route(
        method="DELETE", project_id=project_id, message_id=message_id,
        reaction=reaction, body=body, ctx=ctx,
    )


def _change_saved_route(*, saved: bool, project_id: str, message_id: str, body: Any, ctx: AuthContext) -> dict[str, bool]:
    body = _parse_body(ConversationActionBody, body)
    repository = _collaboration()
    _require_message_member(
        repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id,
    )
    service = CollaborationService(repository)
    try:
        command = service.put_saved_conversation_message if saved else service.delete_saved_conversation_message
        result = command(
            tenant_id=ctx.tenant_id, project_id=project_id,
            authenticated_human_actor_id=ctx.user.id, message_id=message_id,
            client_request_id=body.client_request_id,
        )
        _require_current_member_at_publication(
            repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id,
        )
    except ConversationConflictError as exc:
        code = "idempotency_mismatch" if str(exc) == "idempotency_mismatch" else "conversation_unavailable"
        status = 409 if code == "idempotency_mismatch" else 404
        raise _error(status, code, _CONFLICT_MESSAGE if status == 409 else _NOT_FOUND_MESSAGE) from exc
    except (AuthorizationError, PermissionError) as exc:
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE) from exc
    except NotFoundError as exc:
        raise _error(404, "conversation_unavailable", _NOT_FOUND_MESSAGE) from exc
    except (ValidationError, ValueError) as exc:
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE) from exc
    return {"saved": result.saved}


@router.put("/messages/{message_id}/saved")
def put_saved(
    project_id: str, message_id: str, body: Annotated[Any, Body()] = None,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict[str, bool]:
    return _change_saved_route(saved=True, project_id=project_id, message_id=message_id, body=body, ctx=ctx)


@router.delete("/messages/{message_id}/saved")
def delete_saved(
    project_id: str, message_id: str, body: Annotated[Any, Body()] = None,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict[str, bool]:
    return _change_saved_route(saved=False, project_id=project_id, message_id=message_id, body=body, ctx=ctx)


@router.patch("/messages/{message_id}")
def patch_message(
    project_id: str, message_id: str, body: Annotated[Any, Body()] = None,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    body = _parse_body(ConversationPatchBody, body)
    repository = _collaboration()
    _require_message_member(repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id)
    try:
        message = CollaborationService(repository).edit_conversation_message(
            tenant_id=ctx.tenant_id, project_id=project_id, authenticated_human_actor_id=ctx.user.id,
            message_id=message_id, client_request_id=body.client_request_id,
            expected_revision=body.expected_revision, body=body.body,
        )
    except ConversationConflictError as exc:
        code = "revision_conflict" if str(exc) == "revision_conflict" else "idempotency_mismatch"
        raise _error(409, code, _CONFLICT_MESSAGE) from exc
    except (AuthorizationError, PermissionError) as exc:
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE) from exc
    except NotFoundError as exc:
        raise _error(404, "conversation_unavailable", _NOT_FOUND_MESSAGE) from exc
    except (ValidationError, ValueError) as exc:
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE) from exc
    return {
        "message": _public_message(
            message, repository=repository, tenant_id=ctx.tenant_id, project_id=project_id,
            viewer_id=ctx.user.id,
            assignment=_assignment_for_message(
                message, tenant_id=ctx.tenant_id, project_id=project_id, coordinator=_coordinator_for(project_id),
            ),
        ),
    }


@router.delete("/messages/{message_id}")
def delete_message(
    project_id: str, message_id: str, body: Annotated[Any, Body()] = None,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict[str, Any]:
    body = _parse_body(ConversationDeleteBody, body)
    repository = _collaboration()
    _require_message_member(repository, tenant_id=ctx.tenant_id, project_id=project_id, human_id=ctx.user.id)
    try:
        message = CollaborationService(repository).delete_conversation_message(
            tenant_id=ctx.tenant_id, project_id=project_id, authenticated_human_actor_id=ctx.user.id,
            message_id=message_id, client_request_id=body.client_request_id, expected_revision=body.expected_revision,
        )
    except ConversationConflictError as exc:
        code = "revision_conflict" if str(exc) == "revision_conflict" else "idempotency_mismatch"
        raise _error(409, code, _CONFLICT_MESSAGE) from exc
    except (AuthorizationError, PermissionError) as exc:
        raise _error(403, "conversation_forbidden", _FORBIDDEN_MESSAGE) from exc
    except NotFoundError as exc:
        raise _error(404, "conversation_unavailable", _NOT_FOUND_MESSAGE) from exc
    except (ValidationError, ValueError) as exc:
        raise _error(400, "conversation_invalid", _INVALID_MESSAGE) from exc
    return {
        "message": _public_message(
            message, repository=repository, tenant_id=ctx.tenant_id, project_id=project_id,
            viewer_id=ctx.user.id,
            assignment=_assignment_for_message(
                message, tenant_id=ctx.tenant_id, project_id=project_id, coordinator=_coordinator_for(project_id),
            ),
        ),
    }
