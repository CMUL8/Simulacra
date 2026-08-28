"""Tenant-scoped Mission V0 API.  Public bodies deliberately exclude runtime controls."""

from __future__ import annotations
import os
import hashlib
import re
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from fastapi import APIRouter, Depends, HTTPException, Request, Query
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
from simulacra.missions.artifacts import artifact_bytes
from simulacra.missions.models import Deliverable, hash_artifact, now
from simulacra.operation_graph import OperationGraphStore
from simulacra.operation_graph.errors import UnapprovedRevisionError

router = APIRouter(prefix="/projects/{project_id}/mission", tags=["missions-v0"])
_root = RUNS_DIR / ".mission-control"
_rooms = RUNS_DIR / ".cmul8-control"


def _service():
    return MissionService(JsonMissionRepository(_root))


def _err(exc: Exception):
    if isinstance(exc, MissionNotFoundError):
        return HTTPException(404, {"code": "mission_not_found", "message": "The requested Mission item was not found."})
    if isinstance(exc, (MissionConflictError, KeyError)):
        return HTTPException(409, {"code": "mission_conflict", "message": "That Mission item changed. Refresh and try again."})
    if isinstance(exc, PermissionError):
        return HTTPException(403, {"code": "mission_forbidden", "message": "You do not have permission for that Mission action."})
    return HTTPException(400, {"code": "mission_invalid", "message": "That Mission request could not be completed."})


@contextmanager
def _mission_access(ctx: AuthContext, project_id: str, *, roles: set[str] | None = None):
    """Linearize a complete-visible membership snapshot with a Mission operation.

    COMPLETE visibility is resolved before taking the room lock so the
    invitation coordinator's tenant->room lock order is never inverted.  The
    exact visible row is then matched again under the room lock.  Membership
    removal or role changes therefore commit either before this boundary (and
    deny it) or after the protected Mission read/write has completed.
    """
    repository = JsonCollaborationRepository(_rooms)
    try:
        visible_room = repository.visible_room(ctx.tenant_id, project_id)
    except CollaborationError as exc:
        raise HTTPException(403, "project room membership required") from exc
    visible = next(
        (member for member in visible_room.members if member.actor_id == ctx.user.id),
        None,
    )
    if visible is None or (roles is not None and visible.role not in roles):
        raise HTTPException(403, "project room membership required")
    with repository.room_lock(ctx.tenant_id, project_id) as current_room:
        current = next(
            (member for member in current_room.members if member.actor_id == ctx.user.id),
            None,
        )
        if current != visible or (roles is not None and current.role not in roles):
            raise HTTPException(403, "project room membership required")
        service = _service()
        try:
            service.mission(ctx.tenant_id, project_id)
        except MissionNotFoundError:
            yield visible.role
            return
        try:
            _recover_promotion_intents(service, ctx.tenant_id, project_id)
        except MissionConflictError as exc:
            raise HTTPException(409, "Mission output needs recovery before verification can continue") from exc
        yield visible.role


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
            return {"status": "missing", "revision": None}
        if graph_root.is_symlink() or not graph_root.is_dir():
            return {"status": "invalid", "revision": None}
        store = OperationGraphStore(
            workspace, tenant_id=tenant_id, project_id=project_id,
        )
        current = store.current_revision()
        if current is None:
            return {"status": "missing", "revision": None}
        try:
            store.require_approved_revision(current.revision_hash)
        except UnapprovedRevisionError:
            return {
                "status": "pending_approval",
                "revision": current.revision,
            }
        return {
            "status": "approved",
            "revision": current.revision,
        }
    except Exception:
        # Corrupt or unsafe graph state remains fail-closed. The UI needs a stable
        # recovery state, not raw filesystem or validation details.
        return {"status": "invalid", "revision": None}


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


def _target_parts(target_ref: str) -> tuple[str, ...]:
    target = Path(target_ref)
    if (
        target.is_absolute() or len(target.parts) < 2 or target.parts[0] != "app"
        or "\\" in target_ref or any(part in {"", ".", ".."} or any(ord(char) < 32 for char in part) for part in target.parts)
    ):
        raise MissionConflictError("invalid staged code promotion target")
    return target.parts


def _durabilize_promoted_target(project_id: str, target_ref: str) -> None:
    """Fsync the replaced file and every canonical parent through ``app`` safely."""
    target = _target_parts(target_ref)
    workspace = project_dir(project_id).resolve(strict=True)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    root_fd = os.open(workspace, flags)
    directories: list[int] = []
    try:
        for part in target[:-1]:
            parent_fd = directories[-1] if directories else root_fd
            child_fd = os.open(part, flags, dir_fd=parent_fd)
            info = os.fstat(child_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child_fd)
                raise MissionConflictError("unsafe staged code promotion target")
            directories.append(child_fd)
        parent_fd = directories[-1]
        file_fd = os.open(target[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=parent_fd)
        try:
            _fsync_promotion_file(file_fd)
        finally:
            os.close(file_fd)
        # Deepest first preserves the parent-entry chain all the way to app.
        for descriptor in reversed(directories):
            _fsync_promotion_directory(descriptor)
        # ``app`` itself is an entry in the workspace root. Always sync that
        # parent entry too, including retry paths where app already exists.
        _fsync_promotion_directory(root_fd)
    except OSError as exc:
        raise MissionConflictError("Mission output durability needs recovery before verification can continue") from exc
    finally:
        for descriptor in reversed(directories):
            os.close(descriptor)
        os.close(root_fd)


def _promote_staged_code(project_id: str, staged_ref: str, target_ref: str, expected_hash: str) -> None:
    """Descriptor-safe, durable single-file promotion after exact human verification."""
    source = _artifact_bytes(project_id, staged_ref)
    if hashlib.sha256(source).hexdigest() != expected_hash:
        raise MissionConflictError("staged code changed; register a new deliverable version before verification")
    target = _target_parts(target_ref)
    workspace = project_dir(project_id).resolve(strict=True)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    root_fd = os.open(workspace, flags)
    directories: list[int] = []
    try:
        for part in target[:-1]:
            parent_fd = directories[-1] if directories else root_fd
            created = False
            try:
                os.mkdir(part, 0o755, dir_fd=parent_fd)
                created = True
            except FileExistsError:
                pass
            child_fd = os.open(part, flags, dir_fd=parent_fd)
            info = os.fstat(child_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child_fd)
                raise MissionConflictError("unsafe staged code promotion target")
            if created:
                # Persist both the newly-created directory and its parent entry
                # before descending into it.
                _fsync_promotion_directory(child_fd)
                _fsync_promotion_directory(parent_fd)
            directories.append(child_fd)
        current_fd = directories[-1]
        temporary = f".{target[-1]}.{uuid.uuid4().hex}.tmp"
        file_fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=current_fd)
        try:
            view = memoryview(source)
            while view:
                written = os.write(file_fd, view)
                view = view[written:]
            _fsync_promotion_file(file_fd)
        finally:
            os.close(file_fd)
        try:
            os.replace(temporary, target[-1], src_dir_fd=current_fd, dst_dir_fd=current_fd)
            _durabilize_promoted_target(project_id, target_ref)
        except Exception:
            try: os.unlink(temporary, dir_fd=current_fd)
            except FileNotFoundError: pass
            raise
    except OSError as exc:
        raise MissionConflictError("Mission output durability needs recovery before verification can continue") from exc
    finally:
        for descriptor in reversed(directories):
            os.close(descriptor)
        os.close(root_fd)


def _after_staged_promotion() -> None:
    """Fault-injection seam between a durable promotion and state finalization."""


def _fsync_promotion_directory(descriptor: int) -> None:
    """Keep the promotion durability step testable without weakening it."""
    os.fsync(descriptor)


def _fsync_promotion_file(descriptor: int) -> None:
    """Test seam for the file durability barrier."""
    os.fsync(descriptor)


def _intent_key(deliverable_id: str) -> str:
    return f"promotion:{deliverable_id}"


def _intent_target_matches(project_id: str, intent: dict[str, Any]) -> bool:
    try:
        return hash_artifact(_artifact_bytes(project_id, str(intent["target_ref"]))) == str(intent["expected_hash"])
    except Exception:
        return False


def _mark_promotion_recovery_required(
    service: MissionService, tenant_id: str, project_id: str, intent: dict[str, Any],
) -> None:
    def mutate(records: dict[str, Any]) -> None:
        stored = records.setdefault("promotion_intents", {}).get(_intent_key(str(intent["deliverable_id"])))
        if isinstance(stored, dict):
            stored["status"] = "recovery_required"
    service.repository.mutate(tenant_id, project_id, mutate)


def _finalize_promotion_intent(
    service: MissionService, tenant_id: str, project_id: str, intent: dict[str, Any],
) -> Deliverable:
    """Make the already-promoted output and durable verification agree."""
    key = _intent_key(str(intent["deliverable_id"]))

    def mutate(records: dict[str, Any]) -> Deliverable:
        intents = records.setdefault("promotion_intents", {})
        raw = records["deliverables"].get(intent["deliverable_id"])
        if not isinstance(raw, dict):
            raise MissionNotFoundError("deliverable not found")
        item = Deliverable.from_dict(raw)
        if item.state == "verified" and item.version == intent["version"] and item.verified_hash == intent["expected_hash"]:
            intents.pop(key, None)
            return item
        if (
            item.version != intent["version"] or item.state != "awaiting_verification"
            or item.content_hash != intent["expected_hash"]
        ):
            stored = intents.get(key)
            if isinstance(stored, dict):
                stored["status"] = "recovery_required"
            raise MissionConflictError("Mission output needs recovery before verification can continue")
        item.state = "verified"
        item.verified_by = str(intent["actor_id"])
        item.verified_hash = item.content_hash
        item.verified_at = now()
        item.revision += 1
        item.updated_at = now()
        records["deliverables"][item.id] = item.to_dict()
        intents.pop(key, None)
        return item

    return service.repository.mutate(tenant_id, project_id, mutate)


def _recover_promotion_intents(service: MissionService, tenant_id: str, project_id: str) -> None:
    """Idempotently reconcile a crash between code promotion and verification state."""
    def read(records: dict[str, Any]) -> list[dict[str, Any]]:
        intents = records.setdefault("promotion_intents", {})
        return [
            dict(value) for value in intents.values()
            if isinstance(value, dict) and value.get("status") in {"pending", "recovery_required"}
        ]

    for intent in service.repository.mutate(tenant_id, project_id, read):
        required = {"deliverable_id", "version", "expected_hash", "staged_ref", "target_ref", "actor_id"}
        if not required <= set(intent):
            raise MissionConflictError("Mission output needs recovery before verification can continue")
        if _intent_target_matches(project_id, intent):
            try:
                _durabilize_promoted_target(project_id, str(intent["target_ref"]))
            except Exception as exc:
                _mark_promotion_recovery_required(service, tenant_id, project_id, intent)
                raise MissionConflictError("Mission output needs recovery before verification can continue") from exc
            _finalize_promotion_intent(service, tenant_id, project_id, intent)
            continue
        try:
            _promote_staged_code(
                project_id, str(intent["staged_ref"]), str(intent["target_ref"]), str(intent["expected_hash"]),
            )
        except Exception as exc:
            # A replacement may have succeeded before a directory durability
            # failure. Matching bytes are not enough: retry the full barrier.
            if _intent_target_matches(project_id, intent):
                try:
                    _durabilize_promoted_target(project_id, str(intent["target_ref"]))
                except Exception:
                    pass
                else:
                    _finalize_promotion_intent(service, tenant_id, project_id, intent)
                    continue
            _mark_promotion_recovery_required(service, tenant_id, project_id, intent)
            raise MissionConflictError("Mission output needs recovery before verification can continue") from exc
        _finalize_promotion_intent(service, tenant_id, project_id, intent)


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


class AgentBody(PublicBody):
    name: str = Field(min_length=1, max_length=120)
    role: str = Field(min_length=1, max_length=120)
    mandate: str = Field(min_length=1, max_length=4000)
    scope: str = Field(default="documents", pattern="^(sources|documents|app)$")
    autonomy: str = "assist"


class RunBody(PublicBody):
    trigger_note: str = ""
    agent_ids: list[str] = Field(default_factory=list, max_length=32)


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
    """Retired public creation body; Mission workers register staged outputs internally."""
    pass


class VerifyBody(PublicBody):
    expected_version: int = Field(ge=1)
    decision: str = Field(default="verify", pattern="^verify$")


class RunActionBody(PublicBody):
    expected_revision: int = Field(ge=1)


class ApprovalDecisionBody(PublicBody):
    decision: str = Field(pattern="^(approve|reject)$")
    expected_revision: int = Field(ge=1)
    expected_run_revision: int = Field(ge=1)


def _crew_recommendations(mission: Any, agents: list[Any]) -> list[dict[str, Any]]:
    """Stable, durable-objective based starter crew; never an execution setting."""
    objective = str(getattr(mission, "objective", "") or "").lower()
    done = str(getattr(mission, "definition_of_done", "") or "").lower()
    intent = f"{objective} {done}"
    common = {"autonomy": "operate_with_checkpoints"}
    research = {
        **common, "slug": "source-review", "name": "Source reviewer", "role": "Research specialist",
        "mandate": "Review Mission sources, identify material facts and gaps, and return concise evidence for human review.",
        "scope": "sources",
        "rationale": "Ground the Mission in the available source material first.",
    }
    builder = {
        **common, "slug": "deliverable-builder", "name": "Deliverable builder", "role": "Mission specialist",
        "mandate": "Turn the approved Mission outcome and source material into a reviewable deliverable, surfacing exceptions for a human.",
        "scope": "documents",
        "rationale": "Prepare the requested outcome for human review.",
    }
    product = {
        **common, "slug": "product-builder", "name": "Product builder", "role": "Product builder",
        "mandate": "Turn approved requirements and Mission sources into a working result for human verification.",
        "scope": "app",
        "rationale": "Build the requested product while keeping humans at key checkpoints.",
    }
    if any(word in intent for word in ("app", "dashboard", "tool", "build", "website")):
        candidates = [research, product]
    elif any(word in intent for word in ("research", "brief", "report", "source", "evidence", "analy")):
        candidates = [research, builder]
    else:
        candidates = [builder]

    def represented(candidate: dict[str, Any]) -> bool:
        candidate_name = str(candidate["name"]).casefold()
        candidate_role = str(candidate["role"]).casefold()
        candidate_scope = str(candidate["scope"])
        for agent in agents:
            if str(getattr(agent, "name", "")).casefold() == candidate_name:
                return True
            if _agent_scope(agent) == candidate_scope:
                return True
            if str(getattr(agent, "role", "")).casefold() == candidate_role and _agent_scope(agent) == candidate_scope:
                return True
        return False

    return [candidate for candidate in candidates if not represented(candidate)]


def _public_text(value: Any) -> Any:
    """Normalize product copy without changing durable Mission records."""
    if isinstance(value, str):
        value = re.sub(r"operation\s+graph", "Mission plan", value, flags=re.IGNORECASE)
        return re.sub(r"codex", "agent", value, flags=re.IGNORECASE)
    if isinstance(value, list):
        return [_public_text(item) for item in value]
    if isinstance(value, dict):
        return {key: _public_text(item) for key, item in value.items()}
    return value


_PUBLIC_ERROR_MESSAGES: dict[str, str] = {
    "checkpoint_required": "A human decision is needed before this Mission can continue.",
    "recovery_retry": "This Mission needs another attempt before it can continue.",
    "crew_required": "Add an assigned agent before this Mission can continue.",
    "crew_changed": "The Mission team changed. Review the work before continuing.",
    "checkpoint_rejected": "A human decision sent this Mission back for revision.",
    "agent_failed": "An agent could not continue. Review the Mission plan or try again.",
}


def _public_error(value: Any) -> dict[str, str] | None:
    if not isinstance(value, dict):
        return None
    code = value.get("code")
    if not isinstance(code, str) or code not in _PUBLIC_ERROR_MESSAGES:
        code = "agent_failed"
    return {"code": code, "message": _PUBLIC_ERROR_MESSAGES[code]}


def _fields(value: dict[str, Any], allowed: set[str]) -> dict[str, Any]:
    return {key: value[key] for key in allowed if key in value}


def _public_mission(value: dict[str, Any]) -> dict[str, Any]:
    return _public_text(_fields(value, {
        "id", "title", "objective", "definition_of_done", "template", "owner_id",
        "verifier_ids", "status", "priority", "risk_level", "deadline", "revision",
        "created_at", "updated_at",
    }))


_AGENT_SCOPE_CONFIG = {
    "sources": {"data_scope": ["sources"], "tools": ["document.read", "artifact.write"], "responsibilities": ["Review Mission sources", "Return grounded findings with evidence"]},
    "documents": {"data_scope": ["sources", "outputs"], "tools": ["document.read", "artifact.write"], "responsibilities": ["Prepare a reviewable deliverable", "Flag material exceptions"]},
    "app": {"data_scope": ["sources", "app"], "tools": ["document.read", "code.write"], "responsibilities": ["Build the requested result", "Prepare it for human verification"]},
}


def _agent_scope(value: Any) -> str:
    scopes = list(getattr(value, "data_scope", []) or []) if not isinstance(value, dict) else list(value.get("data_scope", []) or [])
    return "app" if "app" in scopes else "sources" if scopes == ["sources"] else "documents"


def _agent_service_data(body: AgentBody) -> dict[str, Any]:
    """Translate human choices into bounded server-managed capabilities."""
    config = _AGENT_SCOPE_CONFIG[body.scope]
    return {
        "name": body.name, "role": body.role, "mandate": body.mandate,
        "autonomy": body.autonomy,
        "responsibilities": config["responsibilities"], "data_scope": config["data_scope"],
        "tools": config["tools"], "escalation_actor_id": None, "budget": {},
    }


def _public_agent(value: dict[str, Any]) -> dict[str, Any]:
    public = _fields(value, {
        "id", "mission_id", "name", "role", "mandate", "responsibilities",
        "autonomy", "state", "revision", "created_at", "updated_at",
    })
    public.pop("responsibilities", None)
    public["scope"] = _agent_scope(value)
    return _public_text(public)


def _public_trigger_snapshot(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return _public_text(_fields(value, {"type", "note"}))


def _public_run(value: dict[str, Any]) -> dict[str, Any]:
    """Expose a product run summary; execution bookkeeping never leaves HTTP."""
    public = _fields(value, {
        "id", "mission_id", "status", "assigned_agent_ids", "completed_agent_ids",
        "current_agent_id", "active_approval_id", "revision", "started_at", "completed_at",
        "created_at", "updated_at",
    })
    public["trigger_snapshot"] = _public_trigger_snapshot(value.get("trigger_snapshot"))
    error = _public_error(value.get("error"))
    if error:
        public["error"] = error
    return _public_text(public)


_EVENT_PAYLOAD_FIELDS = {
    "agent_started": frozenset({"agent_id"}),
    "agent_completed": frozenset({"agent_id", "structured_output", "artifacts"}),
    "agent_failed": frozenset({"code", "message", "artifact_candidates"}),
    "gate": frozenset({"code", "message"}),
    "recovery_required": frozenset({"code", "message"}),
    "checkpoint_approved": frozenset({"approval_id"}),
    "checkpoint_rejected": frozenset({"approval_id"}),
}


def _public_event_payload(event_type: str, value: Any) -> dict[str, Any]:
    """Expose only product event fields; business output stays under its own key."""
    if not isinstance(value, dict):
        return {}
    allowed = _EVENT_PAYLOAD_FIELDS.get(event_type, frozenset())
    public: dict[str, Any] = {}
    for key in allowed:
        if key not in value:
            continue
        item = value[key]
        if key == "structured_output":
            # This is the Mission's business result, not execution metadata.
            public[key] = _public_text(item)
        elif key == "artifacts" and isinstance(item, list):
            # Chat only needs a count; file names and validation live in the
            # public deliverable list rather than this execution event.
            public[key] = [{} for _ in item]
        elif key in {"agent_id", "approval_id", "code", "message", "artifact_candidates"}:
            public[key] = _public_text(item)
    return public


def _public_event(value: dict[str, Any]) -> dict[str, Any]:
    public = _fields(value, {"id", "run_id", "mission_id", "type", "timestamp"})
    payload = _public_event_payload(str(value.get("type") or ""), value.get("payload"))
    if isinstance(payload, dict) and "code" in payload:
        payload.update(_public_error(payload) or {})
    public["payload"] = payload
    return _public_text(public)


def _public_approval(value: dict[str, Any]) -> dict[str, Any]:
    public = _fields(value, {
        "id", "run_id", "agent_id", "status", "revision", "created_at", "updated_at",
        "actor_id", "superseded_reason",
    })
    if value.get("code") or value.get("message"):
        public.update(_public_error({"code": value.get("code"), "message": value.get("message")} ) or {})
    return _public_text(public)


def _public_deliverable(value: dict[str, Any]) -> dict[str, Any]:
    return _public_text(_fields(value, {
        "id", "mission_id", "type", "name", "producer_id", "version", "state", "verified_by",
        "verified_at", "supersedes_id", "created_at", "updated_at",
    }))


def _public_trigger(value: dict[str, Any]) -> dict[str, Any]:
    return _public_text(_fields(value, {
        "id", "mission_id", "type", "cron", "condition", "timezone", "concurrency_policy",
        "enabled", "next_due_at", "revision", "created_at", "updated_at",
    }))


@router.get("")
def overview(
    project_id: str,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
):
    with _mission_access(ctx, project_id):
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
                "readiness": {"graph": graph, "crew_count": 0},
                "crew_recommendations": [],
            }
        agents = svc.agents(ctx.tenant_id, project_id)
        return {
            "mission": _public_mission(mission.to_dict()),
            "agents": [_public_agent(x.to_dict()) for x in agents],
            "runs": [_public_run(x.to_dict()) for x in svc.runs(ctx.tenant_id, project_id)],
            "triggers": [_public_trigger(x.to_dict()) for x in svc.triggers(ctx.tenant_id, project_id)],
            "deliverables": [_public_deliverable(x.to_dict()) for x in svc.deliverables(ctx.tenant_id, project_id)],
            "events": [_public_event(x) for x in svc.events(ctx.tenant_id, project_id, 100)],
            "approvals": [_public_approval(x) for x in svc.approvals(ctx.tenant_id, project_id)],
            "readiness": {"graph": graph, "crew_count": len(agents)},
            "crew_recommendations": _crew_recommendations(mission, agents),
        }


@router.post("")
def bootstrap(
    project_id: str,
    body: BootstrapBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    with _mission_access(ctx, project_id, roles={"owner", "admin"}):
        try:
            result = _service().bootstrap(
                ctx.tenant_id, project_id, ctx.user.id, body.model_dump(exclude_none=True)
            )
        except Exception as exc:
            raise _err(exc)
    audit_request(request, ctx, "mission.bootstrap", project_id=project_id)
    return _public_mission(result.to_dict())


@router.patch("")
def patch(
    project_id: str,
    body: MissionPatch,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    with _mission_access(ctx, project_id, roles={"owner", "admin"}):
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
    return _public_mission(result.to_dict())


@router.post("/agents")
def add_agent(
    project_id: str,
    body: AgentBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    with _mission_access(ctx, project_id, roles={"owner", "admin"}):
        try:
            result = _service().add_agent(ctx.tenant_id, project_id, _agent_service_data(body))
        except Exception as exc:
            raise _err(exc)
    audit_request(
        request, ctx, "mission.agent.create", project_id=project_id, agent_id=result.id
    )
    return _public_agent(result.to_dict())


@router.post("/runs")
def create_run(
    project_id: str,
    body: RunBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    with _mission_access(ctx, project_id, roles={"owner", "admin"}):
        try:
            service = _service()
            revision = _approved_contract_revision(project_id, ctx.tenant_id)
            result = service.create_run(
                ctx.tenant_id,
                project_id,
                {"type": "manual", "actor_id": ctx.user.id, "note": body.trigger_note},
                verified_contract_revision=revision,
                assigned_agent_ids=body.agent_ids,
            )
        except Exception as exc:
            raise _err(exc)
    audit_request(
        request, ctx, "mission.run.manual", project_id=project_id, run_id=result.id,
        agent_ids=result.assigned_agent_ids,
    )
    return _public_run(result.to_dict())


@router.get("/trajectory")
def trajectory(project_id: str, ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))], cursor: str | None = None, limit: int = Query(100, ge=1, le=500)):
    with _mission_access(ctx, project_id):
        try:
            export = _service().trajectory_export(ctx.tenant_id, project_id, include_events=False)
            page = _service().trajectory_page(ctx.tenant_id, project_id, cursor, limit)
        except ValueError as exc: raise _err(exc)
        return {
            "mission": _public_mission(export["mission"]),
            "agents": [_public_agent(item) for item in export["agents"]],
            "runs": [_public_run(item) for item in export["runs"]],
            "approvals": [_public_approval(item) for item in export["approvals"]],
            "deliverables": [_public_deliverable(item) for item in export["deliverables"]],
            "events": [_public_event(item) for item in page["events"]],
            "next_cursor": page["next_cursor"],
            "retention": _public_text(page["retention"]),
        }


@router.post("/runs/{run_id}/retry")
def retry_run(project_id: str, run_id: str, body: RunActionBody, request: Request,
              ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))]):
    with _mission_access(ctx, project_id, roles={"owner", "admin"}):
        try:
            result = _service().retry_run(ctx.tenant_id, project_id, run_id, body.expected_revision,
                _approved_contract_revision(project_id, ctx.tenant_id))
        except Exception as exc: raise _err(exc)
    audit_request(request, ctx, "mission.run.retry", project_id=project_id, run_id=run_id)
    return _public_run(result.to_dict())


@router.post("/runs/{run_id}/cancel")
def cancel_run(project_id: str, run_id: str, body: RunActionBody, request: Request,
               ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))]):
    with _mission_access(ctx, project_id, roles={"owner", "admin"}):
        try: result = _service().cancel_run(ctx.tenant_id, project_id, run_id, body.expected_revision)
        except Exception as exc: raise _err(exc)
    audit_request(request, ctx, "mission.run.cancel", project_id=project_id, run_id=run_id)
    return _public_run(result.to_dict())


@router.post("/approvals/{approval_id}")
def decide_checkpoint(project_id: str, approval_id: str, body: ApprovalDecisionBody, request: Request,
                      ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))]):
    with _mission_access(ctx, project_id, roles={"owner", "admin"}):
        try: result = _service().checkpoint_decision(ctx.tenant_id, project_id, approval_id, ctx.user.id, body.decision, body.expected_revision, body.expected_run_revision)
        except Exception as exc: raise _err(exc)
    audit_request(request, ctx, "mission.checkpoint.decision", project_id=project_id, approval_id=approval_id)
    return _public_run(result.to_dict())


@router.post("/automation")
def create_trigger(
    project_id: str,
    body: TriggerBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    with _mission_access(ctx, project_id, roles={"owner", "admin"}):
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
    return _public_trigger(result.to_dict())


@router.post("/automation/evaluate-due")
def evaluate_due(
    project_id: str,
    body: DueBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    with _mission_access(ctx, project_id, roles={"owner", "admin"}):
        try:
            at = datetime.fromisoformat(body.at) if body.at else None
            result = _evaluate_condition_event(
                _service(), project_id, ctx.tenant_id, body.facts, at,
            )
        except Exception as exc:
            raise _err(exc)
    audit_request(request, ctx, "mission.trigger.evaluate", project_id=project_id)
    return {"runs": [_public_run(x.to_dict()) for x in result]}


@router.post("/deliverables")
def create_deliverable(
    project_id: str,
    body: DeliverableBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
):
    del body, request, ctx
    # Outputs are registered by the Mission worker after it stages an authorized
    # artifact. A browser must never nominate a filesystem reference or bytes.
    raise HTTPException(410, "Mission outputs are created by Mission work.")


def _verify_staged_deliverable(
    service: MissionService,
    tenant_id: str,
    project_id: str,
    deliverable_id: str,
    actor_id: str,
    expected_version: int,
) -> Deliverable:
    """Verify a durable output without a crash window around staged code promotion."""
    _recover_promotion_intents(service, tenant_id, project_id)

    def prepare(records: dict[str, Any]) -> tuple[Deliverable, dict[str, Any] | None]:
        mission = records.get("mission")
        if not isinstance(mission, dict):
            raise MissionNotFoundError("mission not found")
        raw = records["deliverables"].get(deliverable_id)
        if not isinstance(raw, dict):
            raise MissionNotFoundError("deliverable not found")
        item = Deliverable.from_dict(raw)
        if actor_id == item.producer_id:
            raise PermissionError("producer cannot verify a deliverable")
        if actor_id not in mission.get("verifier_ids", []) and actor_id != mission.get("owner_id"):
            raise PermissionError("designated verifier required")
        if item.version != expected_version or item.state != "awaiting_verification":
            raise MissionConflictError("deliverable version changed")
        if not item.artifact_ref:
            raise MissionConflictError("deliverable is not available for verification")
        # The artifact reference and its exact digest are durable worker-side
        # data. Re-read them while the deliverable state is locked; nothing from
        # the browser participates in this integrity comparison.
        actual_hash = hash_artifact(_artifact_bytes(project_id, item.artifact_ref))
        if actual_hash != item.content_hash:
            raise MissionConflictError("output changed; register a new deliverable version before verification")
        target = _staged_code_target(item)
        if target is None:
            return item, None
        intent = {
            "deliverable_id": item.id,
            "version": item.version,
            "expected_hash": item.content_hash,
            "staged_ref": item.artifact_ref,
            "target_ref": target,
            "actor_id": actor_id,
            "decision": "verified",
            "status": "pending",
        }
        intents = records.setdefault("promotion_intents", {})
        existing = intents.get(_intent_key(item.id))
        if existing is not None:
            raise MissionConflictError("Mission output needs recovery before verification can continue")
        # Persist exact private promotion intent before any canonical file changes.
        intents[_intent_key(item.id)] = intent
        return item, intent

    item, intent = service.repository.mutate(tenant_id, project_id, prepare)
    if intent is not None:
        try:
            _promote_staged_code(project_id, str(intent["staged_ref"]), str(intent["target_ref"]), str(intent["expected_hash"]))
        except Exception:
            _mark_promotion_recovery_required(service, tenant_id, project_id, intent)
            raise
        # A process loss here leaves a durable intent; the next explicit recovery
        # sees the exact target bytes and finalizes instead of exposing ambiguity.
        _after_staged_promotion()
        return _finalize_promotion_intent(service, tenant_id, project_id, intent)

    def finalize_without_promotion(records: dict[str, Any]) -> Deliverable:
        raw = records["deliverables"].get(deliverable_id)
        if not isinstance(raw, dict):
            raise MissionNotFoundError("deliverable not found")
        item = Deliverable.from_dict(raw)
        if item.version != expected_version or item.state != "awaiting_verification":
            raise MissionConflictError("deliverable version changed")
        item.state = "verified"
        item.verified_by = actor_id
        item.verified_hash = item.content_hash
        item.verified_at = now()
        item.revision += 1
        item.updated_at = now()
        records["deliverables"][item.id] = item.to_dict()
        return item

    return service.repository.mutate(tenant_id, project_id, finalize_without_promotion)


@router.post("/deliverables/{deliverable_id}/verify")
def verify_deliverable(
    project_id: str,
    deliverable_id: str,
    body: VerifyBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
):
    with _mission_access(ctx, project_id):
        try:
            result = _verify_staged_deliverable(
                _service(), ctx.tenant_id, project_id, deliverable_id, ctx.user.id,
                body.expected_version,
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
    return _public_deliverable(result.to_dict())
