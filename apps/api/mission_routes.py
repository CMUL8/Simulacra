"""Tenant-scoped Mission V0 API.  Public bodies deliberately exclude runtime controls."""

from __future__ import annotations
import os
import hashlib
import stat
import uuid
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, ConfigDict, Field, StrictInt
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
from simulacra.missions.artifacts import artifact_bytes
from simulacra.operation_graph import OperationGraphStore
from simulacra.operation_graph.errors import UnapprovedRevisionError

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


def _graph_readiness(project_id: str, tenant_id: str) -> dict[str, Any]:
    """Return a small, user-facing admission state without exposing graph bytes."""
    try:
        workspace = project_dir(project_id)
        graph_root = workspace / ".simulacra" / "operation-graph"
        if not graph_root.exists():
            return {"status": "missing", "revision": None, "revision_hash": None}
        if graph_root.is_symlink() or not graph_root.is_dir():
            return {"status": "invalid", "revision": None, "revision_hash": None}
        store = OperationGraphStore(
            workspace, tenant_id=tenant_id, project_id=project_id,
        )
        current = store.current_revision()
        if current is None:
            return {"status": "missing", "revision": None, "revision_hash": None}
        try:
            store.require_approved_revision(current.revision_hash)
        except UnapprovedRevisionError:
            return {
                "status": "pending_approval",
                "revision": current.revision,
                "revision_hash": current.revision_hash,
            }
        return {
            "status": "approved",
            "revision": current.revision,
            "revision_hash": current.revision_hash,
        }
    except Exception:
        # Corrupt or unsafe graph state remains fail-closed. The UI needs a stable
        # recovery state, not raw filesystem or validation details.
        return {"status": "invalid", "revision": None, "revision_hash": None}


def _evaluate_condition_event(
    service: MissionService,
    project_id: str,
    tenant_id: str,
    facts: dict[str, Any],
    at: datetime | None,
):
    """Pin exact graph admission through the condition occurrence transaction."""
    store = OperationGraphStore(
        project_dir(project_id), tenant_id=tenant_id, project_id=project_id,
    )
    with store.locked_current_approved_revision() as current:
        if current is None:
            return []
        return service.evaluate_condition_due(
            tenant_id,
            project_id,
            facts,
            at=at,
            verified_contract_revision=current.revision_hash,
        )


def _artifact_bytes(project_id: str, artifact_ref: str) -> bytes:
    return artifact_bytes(project_dir(project_id), artifact_ref)


def _staged_code_target(item) -> str | None:
    for evidence in item.validation_evidence:
        if isinstance(evidence, dict) and evidence.get("staged_artifact_ref") == item.artifact_ref:
            target = evidence.get("intended_target")
            if isinstance(target, str):
                return target
    return None


def _promote_staged_code(project_id: str, staged_ref: str, target_ref: str, expected_hash: str) -> None:
    """Descriptor-safe, single-file promotion after exact human verification."""
    source = _artifact_bytes(project_id, staged_ref)
    if hashlib.sha256(source).hexdigest() != expected_hash:
        raise MissionConflictError("staged code changed; register a new deliverable version before verification")
    target = Path(target_ref)
    if (
        target.is_absolute() or len(target.parts) < 2 or target.parts[0] != "app"
        or "\\" in target_ref or any(part in {"", ".", ".."} or any(ord(char) < 32 for char in part) for part in target.parts)
    ):
        raise MissionConflictError("invalid staged code promotion target")
    workspace = project_dir(project_id).resolve(strict=True)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    root_fd = os.open(workspace, flags)
    current_fd = root_fd
    try:
        for part in target.parts[:-1]:
            try:
                os.mkdir(part, 0o755, dir_fd=current_fd)
            except FileExistsError:
                pass
            child_fd = os.open(part, flags, dir_fd=current_fd)
            info = os.fstat(child_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child_fd)
                raise MissionConflictError("unsafe staged code promotion target")
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = child_fd
        temporary = f".{target.name}.{uuid.uuid4().hex}.tmp"
        file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=current_fd)
        try:
            view = memoryview(source)
            while view:
                written = os.write(file_fd, view)
                view = view[written:]
            os.fsync(file_fd)
        finally:
            os.close(file_fd)
        try:
            os.replace(temporary, target.name, src_dir_fd=current_fd, dst_dir_fd=current_fd)
            os.fsync(current_fd)
        except Exception:
            try: os.unlink(temporary, dir_fd=current_fd)
            except FileNotFoundError: pass
            raise
    except OSError as exc:
        raise MissionConflictError("unsafe staged code promotion target") from exc
    finally:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)


class PublicBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BudgetBody(PublicBody):
    """Only bounded, server-enforced execution limits are public in V0."""
    max_steps: StrictInt | None = Field(default=None, ge=1, le=100)
    wall_timeout_seconds: StrictInt | None = Field(default=None, ge=1, le=600)


class BootstrapBody(PublicBody):
    title: str = Field(min_length=1, max_length=200)
    objective: str = ""
    definition_of_done: str = ""
    template: str = "custom"
    verifier_ids: list[str] = Field(default_factory=list)
    priority: str = "normal"
    risk_level: str = "medium"
    deadline: str | None = None
    budget: BudgetBody = Field(default_factory=BudgetBody)


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
    budget: BudgetBody | None = None


class AgentBody(PublicBody):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)
    mandate: str = Field(min_length=1, max_length=4000)
    responsibilities: list[str] = Field(default_factory=list)
    data_scope: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    autonomy: str = "assist"
    escalation_actor_id: str | None = None
    budget: BudgetBody = Field(default_factory=BudgetBody)


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


class RunActionBody(PublicBody):
    expected_revision: int = Field(ge=1)


class ApprovalDecisionBody(PublicBody):
    decision: str = Field(pattern="^(approve|reject)$")
    expected_revision: int = Field(ge=1)
    expected_run_revision: int = Field(ge=1)


@router.get("")
def overview(
    project_id: str,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
):
    _member(ctx, project_id)
    svc = _service()
    graph = _graph_readiness(project_id, ctx.tenant_id)
    try:
        mission = svc.mission(ctx.tenant_id, project_id)
    except MissionNotFoundError:
        return {
            "mission": None,
            "agents": [],
            "runs": [],
            "triggers": [],
            "deliverables": [],
            "events": [], "approvals": [],
            "runtime": "codex",
            "readiness": {"graph": graph, "crew_count": 0},
        }
    agents = svc.agents(ctx.tenant_id, project_id)
    return {
        "mission": mission.to_dict(),
        "agents": [x.to_dict() for x in agents],
        "runs": [x.to_dict() for x in svc.runs(ctx.tenant_id, project_id)],
        "triggers": [x.to_dict() for x in svc.triggers(ctx.tenant_id, project_id)],
        "deliverables": [
            x.to_dict() for x in svc.deliverables(ctx.tenant_id, project_id)
        ],
        "events": svc.events(ctx.tenant_id, project_id, 100),
        "approvals": svc.approvals(ctx.tenant_id, project_id),
        "runtime": "codex",
        "readiness": {"graph": graph, "crew_count": len(agents)},
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
            ctx.tenant_id, project_id, ctx.user.id, body.model_dump(exclude_none=True)
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
        result = _service().add_agent(ctx.tenant_id, project_id, body.model_dump(exclude_none=True))
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


@router.get("/trajectory")
def trajectory(project_id: str, ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))], cursor: str | None = None, limit: int = Query(100, ge=1, le=500)):
    _member(ctx, project_id)
    try:
        export = _service().trajectory_export(ctx.tenant_id, project_id, include_events=False)
        page = _service().trajectory_page(ctx.tenant_id, project_id, cursor, limit)
    except ValueError as exc: raise HTTPException(400, str(exc))
    export["events"] = page["events"]; export["next_cursor"] = page["next_cursor"]; export["retention"] = page["retention"]
    return export


@router.post("/runs/{run_id}/retry")
def retry_run(project_id: str, run_id: str, body: RunActionBody, request: Request,
              ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))]):
    _mutator(ctx, project_id)
    try:
        result = _service().retry_run(ctx.tenant_id, project_id, run_id, body.expected_revision,
            _approved_contract_revision(project_id, ctx.tenant_id))
    except Exception as exc: raise _err(exc)
    audit_request(request, ctx, "mission.run.retry", project_id=project_id, run_id=run_id)
    return result.to_dict()


@router.post("/runs/{run_id}/cancel")
def cancel_run(project_id: str, run_id: str, body: RunActionBody, request: Request,
               ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))]):
    _mutator(ctx, project_id)
    try: result = _service().cancel_run(ctx.tenant_id, project_id, run_id, body.expected_revision)
    except Exception as exc: raise _err(exc)
    audit_request(request, ctx, "mission.run.cancel", project_id=project_id, run_id=run_id)
    return result.to_dict()


@router.post("/approvals/{approval_id}")
def decide_checkpoint(project_id: str, approval_id: str, body: ApprovalDecisionBody, request: Request,
                      ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))]):
    _mutator(ctx, project_id)
    try: result = _service().checkpoint_decision(ctx.tenant_id, project_id, approval_id, ctx.user.id, body.decision, body.expected_revision, body.expected_run_revision)
    except Exception as exc: raise _err(exc)
    audit_request(request, ctx, "mission.checkpoint.decision", project_id=project_id, approval_id=approval_id)
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
        result = _evaluate_condition_event(
            _service(), project_id, ctx.tenant_id, body.facts, at,
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
        target = _staged_code_target(item)
        promote = None
        if target is not None:
            promote = lambda deliverable: _promote_staged_code(project_id, str(deliverable.artifact_ref), target, deliverable.content_hash)
        result = service.verify_deliverable(
            ctx.tenant_id,
            project_id,
            deliverable_id,
            ctx.user.id,
            body.content_hash,
            body.expected_revision,
            promote,
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
