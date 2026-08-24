"""Tenant-scoped Mission V0 API.  Public bodies deliberately exclude runtime controls."""

from __future__ import annotations
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from apps.api.security import audit_request, require_project_access
from simulacra.collaboration import JsonCollaborationRepository
from simulacra.collaboration.errors import CollaborationError
from simulacra.demo.identity import AuthContext
from simulacra.demo.paths import RUNS_DIR
from simulacra.demo.runs import project_dir
from simulacra.missions import (
    JsonMissionRepository,
    MissionConflictError,
    MissionNotFoundError,
    MissionService,
)
from simulacra.operation_graph import OperationGraphStore

router = APIRouter(prefix="/projects/{project_id}/mission", tags=["missions-v0"])
_root = RUNS_DIR / ".mission-control"
_rooms = RUNS_DIR / ".cmul8-control"


def _service():
    return MissionService(JsonMissionRepository(_root))


def _err(exc: Exception):
    if isinstance(exc, MissionNotFoundError):
        return HTTPException(404, str(exc))
    if isinstance(exc, (MissionConflictError, KeyError)):
        return HTTPException(409, str(exc))
    if isinstance(exc, PermissionError):
        return HTTPException(403, str(exc))
    return HTTPException(400, str(exc))


def _role(ctx: AuthContext, project_id: str):
    try:
        room = JsonCollaborationRepository(_rooms).get_room(ctx.tenant_id, project_id)
    except CollaborationError as exc:
        raise HTTPException(403, "project room membership required") from exc
    return next((x.role for x in room.members if x.actor_id == ctx.user.id), None)


def _member(ctx, p):
    role = _role(ctx, p)
    if not role:
        raise HTTPException(403, "project room membership required")
    return role


def _mutator(ctx, p):
    role = _member(ctx, p)
    if role not in {"owner", "admin"}:
        raise HTTPException(403, "project room owner or admin required")


def _approved_contract_revision(project_id: str, tenant_id: str) -> str | None:
    """Return only the current exact graph revision after store verification."""
    store = OperationGraphStore(
        project_dir(project_id), tenant_id=tenant_id, project_id=project_id
    )
    current = store.current_revision()
    if current is None:
        return None
    return store.require_approved_revision(current.revision_hash).revision_hash


def _artifact_bytes(project_id: str, artifact_ref: str) -> bytes:
    relative = Path(artifact_ref)
    if relative.is_absolute() or not relative.parts or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("artifact_ref must be a relative project path")
    root_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    root_fd = os.open(project_dir(project_id), root_flags)
    current_fd = root_fd
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        for component in relative.parts[:-1]:
            child_fd = os.open(component, directory_flags, dir_fd=current_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = child_fd
        descriptor = os.open(relative.parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16 * 1024 * 1024:
                raise ValueError("artifact must be a regular file no larger than 16 MiB")
            chunks: list[bytes] = []
            remaining = metadata.st_size
            while remaining:
                chunk = os.read(descriptor, min(64 * 1024, remaining))
                if not chunk:
                    raise ValueError("artifact changed while being read")
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise ValueError("artifact changed while being read")
            return b"".join(chunks)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ValueError("artifact_ref must name a regular non-symlink project file") from exc
    finally:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)


class PublicBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BootstrapBody(PublicBody):
    title: str = Field(min_length=1, max_length=200)
    objective: str = ""
    definition_of_done: str = ""
    template: str = "custom"
    verifier_ids: list[str] = Field(default_factory=list)
    priority: str = "normal"
    risk_level: str = "medium"
    deadline: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)


class MissionPatch(PublicBody):
    expected_revision: int = Field(ge=1)
    title: str | None = None
    objective: str | None = None
    definition_of_done: str | None = None
    template: str | None = None
    verifier_ids: list[str] | None = None
    status: str | None = None
    priority: str | None = None
    risk_level: str | None = None
    deadline: str | None = None
    budget: dict[str, Any] | None = None


class AgentBody(PublicBody):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)
    mandate: str = Field(min_length=1, max_length=4000)
    responsibilities: list[str] = Field(default_factory=list)
    data_scope: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    autonomy: str = "assist"
    escalation_actor_id: str | None = None
    budget: dict[str, Any] = Field(default_factory=dict)


class RunBody(PublicBody):
    trigger_note: str = ""


class TriggerBody(PublicBody):
    type: str
    cron: str | None = None
    condition: dict[str, Any] | None = None
    timezone: str = "UTC"
    concurrency_policy: str = "queue"
    enabled: bool = True


class DueBody(PublicBody):
    facts: dict[str, Any] = Field(default_factory=dict)
    at: str | None = None


class DeliverableBody(PublicBody):
    type: str
    name: str = Field(min_length=1, max_length=200)
    source_ref: str = Field(min_length=1, max_length=2000)
    artifact_ref: str = Field(min_length=1, max_length=1000)
    producer_agent_id: str | None = None
    validation_evidence: list[dict[str, Any]] = Field(default_factory=list)


class VerifyBody(PublicBody):
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    expected_revision: int = Field(ge=1)


@router.get("")
def overview(
    project_id: str,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
):
    _member(ctx, project_id)
    svc = _service()
    try:
        mission = svc.mission(ctx.tenant_id, project_id)
    except MissionNotFoundError:
        return {
            "mission": None,
            "agents": [],
            "runs": [],
            "triggers": [],
            "deliverables": [],
            "runtime": "codex",
        }
    return {
        "mission": mission.to_dict(),
        "agents": [x.to_dict() for x in svc.agents(ctx.tenant_id, project_id)],
        "runs": [x.to_dict() for x in svc.runs(ctx.tenant_id, project_id)],
        "triggers": [x.to_dict() for x in svc.triggers(ctx.tenant_id, project_id)],
        "deliverables": [
            x.to_dict() for x in svc.deliverables(ctx.tenant_id, project_id)
        ],
        "runtime": "codex",
    }


@router.post("")
def bootstrap(
    project_id: str,
    body: BootstrapBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    _mutator(ctx, project_id)
    try:
        result = _service().bootstrap(
            ctx.tenant_id, project_id, ctx.user.id, body.model_dump()
        )
    except Exception as exc:
        raise _err(exc)
    audit_request(request, ctx, "mission.bootstrap", project_id=project_id)
    return result.to_dict()


@router.patch("")
def patch(
    project_id: str,
    body: MissionPatch,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    _mutator(ctx, project_id)
    try:
        result = _service().update_mission(
            ctx.tenant_id,
            project_id,
            body.model_dump(exclude_none=True, exclude={"expected_revision"}),
            body.expected_revision,
        )
    except Exception as exc:
        raise _err(exc)
    audit_request(request, ctx, "mission.update", project_id=project_id)
    return result.to_dict()


@router.post("/agents")
def add_agent(
    project_id: str,
    body: AgentBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    _mutator(ctx, project_id)
    try:
        result = _service().add_agent(ctx.tenant_id, project_id, body.model_dump())
    except Exception as exc:
        raise _err(exc)
    audit_request(
        request, ctx, "mission.agent.create", project_id=project_id, agent_id=result.id
    )
    return {**result.to_dict(), "runtime": "codex"}


@router.post("/runs")
def create_run(
    project_id: str,
    body: RunBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    _mutator(ctx, project_id)
    try:
        service = _service()
        revision = _approved_contract_revision(project_id, ctx.tenant_id)
        result = service.create_run(
            ctx.tenant_id,
            project_id,
            {"type": "manual", "actor_id": ctx.user.id, "note": body.trigger_note},
            verified_contract_revision=revision,
        )
    except Exception as exc:
        raise _err(exc)
    audit_request(
        request, ctx, "mission.run.manual", project_id=project_id, run_id=result.id
    )
    return result.to_dict()


@router.post("/automation")
def create_trigger(
    project_id: str,
    body: TriggerBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    _mutator(ctx, project_id)
    try:
        result = _service().add_trigger(ctx.tenant_id, project_id, body.model_dump())
    except Exception as exc:
        raise _err(exc)
    audit_request(
        request,
        ctx,
        "mission.trigger.create",
        project_id=project_id,
        trigger_id=result.id,
    )
    return result.to_dict()


@router.post("/automation/evaluate-due")
def evaluate_due(
    project_id: str,
    body: DueBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    _mutator(ctx, project_id)
    try:
        at = datetime.fromisoformat(body.at) if body.at else None
        result = _service().evaluate_due(
            ctx.tenant_id,
            project_id,
            body.facts,
            at,
            _approved_contract_revision(project_id, ctx.tenant_id),
        )
    except Exception as exc:
        raise _err(exc)
    audit_request(request, ctx, "mission.trigger.evaluate", project_id=project_id)
    return {"runs": [x.to_dict() for x in result]}


@router.post("/deliverables")
def create_deliverable(
    project_id: str,
    body: DeliverableBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    _mutator(ctx, project_id)
    try:
        artifact_bytes = _artifact_bytes(project_id, body.artifact_ref)
        result = _service().create_deliverable(
            ctx.tenant_id, project_id, body.model_dump(), ctx.user.id, artifact_bytes
        )
    except Exception as exc:
        raise _err(exc)
    audit_request(
        request,
        ctx,
        "mission.deliverable.create",
        project_id=project_id,
        deliverable_id=result.id,
    )
    return result.to_dict()


@router.post("/deliverables/{deliverable_id}/verify")
def verify_deliverable(
    project_id: str,
    deliverable_id: str,
    body: VerifyBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
):
    _member(ctx, project_id)
    try:
        service = _service()
        item = next(
            (
                row
                for row in service.deliverables(ctx.tenant_id, project_id)
                if row.id == deliverable_id
            ),
            None,
        )
        if item is None:
            raise MissionNotFoundError("deliverable not found")
        from simulacra.missions.models import hash_artifact

        if (
            item.artifact_ref is None
            or hash_artifact(_artifact_bytes(project_id, item.artifact_ref))
            != item.content_hash
        ):
            raise MissionConflictError(
                "artifact changed; register a new deliverable version before verification"
            )
        result = service.verify_deliverable(
            ctx.tenant_id,
            project_id,
            deliverable_id,
            ctx.user.id,
            body.content_hash,
            body.expected_revision,
        )
    except Exception as exc:
        raise _err(exc)
    audit_request(
        request,
        ctx,
        "mission.deliverable.verify",
        project_id=project_id,
        deliverable_id=deliverable_id,
    )
    return result.to_dict()
