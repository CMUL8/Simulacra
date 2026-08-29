from __future__ import annotations

import asyncio
import fcntl
import hashlib
import io
import json
import logging
import os
import queue
import re
import stat
import uuid
import zipfile
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from apps.api.security import audit_request, get_auth, is_normal_project_visible, require_perm, require_project_access
from apps.api.file_routes import authorized_file_inventory
from apps.api.workplace_routes import router as workplace_router
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.demo.checkpoints import list_checkpoints
from simulacra.demo.design_brief import merge_brief, update_project_brief
from simulacra.demo.duckdb_engine import query
from simulacra.demo.enterprise_audit import list_audit
from simulacra.demo.events import list_events, subscribe, unsubscribe
from simulacra.demo.governance import governance_overview
from simulacra.demo.db import health as db_health, migrate
from simulacra.demo.identity import (
	AuthContext,
	add_membership,
	auth_required,
	create_api_key,
	ensure_bootstrap,
	list_api_keys,
	list_memberships,
	login_user,
	register_user,
	remove_membership,
	request_password_reset,
	reset_password_with_token,
	revoke_api_key,
	user_tenants,
)
from simulacra.demo.jobs import get_job, job_snapshot
from simulacra.demo.paths import RUNS_DIR
from simulacra.demo.pipeline import (
	approve_deploy,
	build_project,
	cancel_job,
	export_audit_zip,
	init_plan,
	project_snapshot,
	rollback_project,
	start_approve_build,
	start_follow_up,
)
from simulacra.demo.operation_graph_builder import approved_graph_path, propose_operation_graph
from simulacra.demo.runs import (
	activate_chat,
	chat_summaries,
	create_chat,
	create_project,
	delete_chat,
	list_projects,
	load_state,
	ProjectState,
	project_dir,
	save_state,
)
from simulacra.demo.sandbox import sandbox_status
from simulacra.demo.siem import download_filename, export_bundle, siem_status
from simulacra.demo.tenants import (
	admin_overview,
	create_tenant,
	default_tenant_id,
	get_tenant,
	list_tenants,
	update_tenant,
)
from simulacra.env import load_dotenv
from simulacra.resolve import resolve_prime_agent
from simulacra.workplace.config import workplace_flags_for_tenant
from simulacra.workplace.bootstrap_coordinator import WorkspaceBootstrapCoordinator
from simulacra.workplace.source_staging import SourceStaging
from simulacra.operation_graph import OperationGraphStore
from simulacra.operation_graph.errors import OperationGraphError, UnapprovedRevisionError
from simulacra.collaboration.errors import CollaborationError
from simulacra.missions import JsonMissionRepository, MissionService
from apps.api.cmul8_routes import router as cmul8_router
from apps.api.mission_routes import router as mission_router
from apps.api.preference_routes import router as preference_router
from apps.api.preview_routes import preview_origin_config, preview_origin_hostname, router as preview_router

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("simulacra.api")
_collaboration_root = RUNS_DIR / ".cmul8-control"
_mission_root = RUNS_DIR / ".mission-control"


def _public_text(value: Any) -> Any:
	if isinstance(value, str):
		value = re.sub(r"operation\s+graph", "Mission plan", value, flags=re.IGNORECASE)
		return re.sub(r"codex", "agent", value, flags=re.IGNORECASE)
	if isinstance(value, list):
		return [_public_text(item) for item in value]
	if isinstance(value, dict):
		return {key: _public_text(item) for key, item in value.items()}
	return value


def _public_job(value: Any) -> dict[str, Any] | None:
	if not isinstance(value, dict):
		return None
	return _public_text({key: value[key] for key in {"id", "kind", "status", "cancel_requested", "label"} if key in value})


def _public_source_file(value: Any) -> dict[str, Any]:
	if not isinstance(value, dict):
		return {}
	return {
		key: value[key]
		for key in {"name", "size", "type", "status", "detail", "row_count"}
		if key in value
	}


def _public_source_profile(value: Any) -> dict[str, Any] | None:
	if not isinstance(value, dict):
		return None
	return {
		key: value[key]
		for key in {
			"row_count", "columns", "vendors", "themes", "high_risk", "medium_risk", "low_risk",
			"regions", "owners", "source_files", "empty_room", "suggested_primary",
			"suggested_must_have", "nuance_notes",
		}
		if key in value
	}


def _public_extract(value: Any) -> dict[str, Any] | None:
	if not isinstance(value, dict):
		return None
	return {key: value[key] for key in {"row_count", "errors", "skipped", "ok_files"} if key in value}


def _public_plan_preview(value: Any) -> dict[str, Any]:
	if not isinstance(value, dict):
		return {}
	public = {key: value[key] for key in {
		"row_count", "high_risk", "medium_risk", "low_risk", "vendors", "themes",
		"summary", "sample_rows",
	} if key in value}
	if isinstance(value.get("files"), list):
		public["files"] = [_public_source_file(item) for item in value["files"] if isinstance(item, dict)]
	if (extract := _public_extract(value.get("extract"))) is not None:
		public["extract"] = extract
	if isinstance(value.get("source_room"), dict):
		public["source_room"] = {
			key: value["source_room"][key]
			for key in {"empty", "row_count", "file_count", "file_names", "vendors", "looks_like_vendor_sample"}
			if key in value["source_room"]
		}
	return _public_text(public)


def _public_project_snapshot(value: dict[str, Any]) -> dict[str, Any]:
	"""Project HTTP view: keep workspace state, never runner/session state."""
	if not isinstance(value.get("project"), dict) and isinstance(value.get("id"), str):
		# Compatibility for narrow internal callers that return only a project id.
		return {"id": value["id"]}
	raw_project = value.get("project") if isinstance(value.get("project"), dict) else {}
	project = {key: raw_project[key] for key in {
		"id", "prompt", "goal", "tenant_id", "phase", "plan_approved", "status", "artifact_kind",
		"gates_status", "deployed", "deploy_url", "chat", "active_chat_id", "chats", "chat_index",
		"app_config", "row_count", "checkpoints", "active_checkpoint", "design_brief", "created_at",
	} if key in raw_project}
	if isinstance(project.get("chat"), list):
		project["chat"] = [
			{key: item[key] for key in {"role", "content", "at"} if key in item}
			for item in project["chat"] if isinstance(item, dict)
		]
	for thread_key in ("chats", "chat_index"):
		if not isinstance(project.get(thread_key), list):
			continue
		project[thread_key] = [
			{
				**{key: item[key] for key in {"id", "title", "created_at", "updated_at", "message_count", "artifact_kind", "artifact_mode", "active"} if key in item},
				**({"messages": [{key: message[key] for key in {"role", "content", "at"} if key in message} for message in item["messages"] if isinstance(message, dict)]} if isinstance(item.get("messages"), list) else {}),
			}
			for item in project[thread_key] if isinstance(item, dict)
		]
	project["plan_preview"] = _public_plan_preview(raw_project.get("plan_preview"))
	public: dict[str, Any] = {
		"project": _public_text(project),
		"preview_data": _public_text(value.get("preview_data") if isinstance(value.get("preview_data"), dict) else {"columns": [], "rows": [], "row_count": 0}),
		"preview_url": _public_text(value.get("preview_url")) if value.get("preview_url") is not None else None,
	}
	for key in {"status", "cancelled", "already_idle"}:
		if key in value:
			public[key] = _public_text(value[key])
	return public


def _is_normal_project_viewable(state: ProjectState) -> bool:
	"""Keep recoverable bootstrap children out of normal Mission views.

	A legacy project has no bootstrap marker and remains visible.  Once a
	bootstrap reservation created a project, only its verified COMPLETE journal
	can expose it through the ordinary list/detail experience.
	"""
	# Keep the old injection point for compatibility tests while sharing the
	# exact predicate used by list and project-route authorization.
	return is_normal_project_visible(state, coordinator_factory=WorkspaceBootstrapCoordinator)


def _public_project_event(value: Any) -> dict[str, Any]:
	"""Safe event feed shape shared by JSON polling and SSE."""
	if not isinstance(value, dict):
		return {}
	event_type = str(value.get("type") or "phase")
	status = str(value.get("status") or "running")
	if event_type == "tool":
		return {"id": value.get("id"), "ts": value.get("ts"), "type": "phase", "label": "Mission work", "detail": "Progress update", "status": status if status in {"running", "done", "fail"} else "running"}
	label = str(_public_text(value.get("label") or "Mission update"))
	detail = str(_public_text(value.get("detail") or ""))
	if re.search(r"provider|runtime|model|session|tool|invocation|lease|sandbox", f"{label} {detail}", re.IGNORECASE):
		label, detail = "Mission update", "Progress update"
	return {"id": value.get("id"), "ts": value.get("ts"), "type": event_type if event_type in {"phase", "think", "gate", "message", "error", "done"} else "phase", "label": label, "detail": detail, "status": status if status in {"running", "done", "fail"} else "running"}


def _public_project_audit(project_id: str) -> dict[str, Any]:
	"""Audit evidence for humans; durable control-plane files never leave disk."""
	snapshot = _public_project_snapshot(project_snapshot(project_id))
	project = snapshot.get("project") if isinstance(snapshot.get("project"), dict) else {}
	project_evidence = {
		key: project[key] for key in {
			"id", "prompt", "goal", "phase", "plan_approved", "status", "artifact_kind",
			"gates_status", "deployed", "deploy_url", "app_config", "row_count", "created_at",
		} if key in project
	}
	checkpoints = [
		{key: item[key] for key in {"id", "label", "created_at", "current", "has_files"} if key in item}
		for item in list_checkpoints(project_id)
	]
	deliverables: list[dict[str, Any]] = []
	try:
		state = load_state(project_id)
		for item in MissionService(JsonMissionRepository(_mission_root)).deliverables(state.tenant_id, project_id):
			row = item.to_dict()
			deliverables.append({
				key: row[key] for key in {
					"id", "name", "type", "version", "state", "verified_by",
					"verified_at", "created_at", "updated_at",
				} if key in row
			})
	except Exception:
		# A project can predate Missions; its ordinary project evidence still exports.
		pass
	return {
		"project": _public_text(project_evidence),
		"events": [_public_project_event(item) for item in list_events(project_id)],
		"checkpoints": _public_text(checkpoints),
		"deliverables": _public_text(deliverables),
	}


def _require_room_graph_authority(project_id: str, ctx: AuthContext) -> None:
	"""Require current durable room ownership before graph approval or build."""
	tenant_id = ctx.tenant_id if ctx.tenant_id != "*" else load_state(project_id).tenant_id
	try:
		repository = JsonCollaborationRepository(_collaboration_root)
		room = repository.get_room(tenant_id, project_id)
	except CollaborationError as exc:
		raise HTTPException(403, "project room owner or admin role required for Operation Graph mutations") from exc
	member = repository.visible_member(room, ctx.user.id)
	if member is None or member.role not in {"owner", "admin"}:
		raise HTTPException(403, "project room owner or admin role required for Operation Graph mutations")


def _bootstrap_project_room(state: ProjectState, ctx: AuthContext) -> None:
	"""Create the initial durable room before starting any architect work."""
	if ctx.role not in {"owner", "admin"} and not ctx.user.is_platform_admin:
		raise HTTPException(403, "only tenant owners and admins can bootstrap a project")
	repository = JsonCollaborationRepository(_collaboration_root)
	service = CollaborationService(repository)
	try:
		service.create_room(
			tenant_id=state.tenant_id, project_id=state.id,
			creator_id=ctx.user.id, creator_role="owner", creator_name=ctx.user.name,
		)
	except CollaborationError as exc:
		# Project ids are normally fresh.  If a concurrent bootstrap already
		# created the room, still require the caller to be an owner/admin.
		try:
			_require_room_graph_authority(state.id, ctx)
		except HTTPException:
			raise HTTPException(409, "could not establish Project Room ownership") from exc


def _job_conflict_http(exc: Exception) -> HTTPException:
	"""Map job admission / in-progress conflicts to 409."""
	msg = str(exc)
	low = msg.lower()
	conflict = any(
		k in low
		for k in ("already", "busy", "limit reached", "concurrent", "workspace limit", "host busy")
	)
	return HTTPException(409 if conflict else 400, msg)


app = FastAPI(title="Simulacra API", version="0.8.0")
app.include_router(cmul8_router)
app.include_router(mission_router)
app.include_router(workplace_router)
app.include_router(preference_router)
app.include_router(preview_router)


@app.middleware("http")
async def _isolate_preview_origin(request: Request, call_next):
	"""The preview host is an untrusted app origin, never a control API host."""
	preview_host = preview_origin_hostname()
	if preview_host is not None and request.url.hostname == preview_host:
		path = request.url.path
		allowed = (
			(request.method in {"OPTIONS", "POST"} and path == "/preview/exchange")
			or (
				request.method == "GET"
				and bool(re.fullmatch(r"/projects/[A-Za-z0-9][A-Za-z0-9_.-]{0,127}/preview(?:/.*)?", path))
			)
		)
		if not allowed:
			return Response(
				content=json.dumps({"code": "preview_unavailable", "message": "Preview is unavailable."}),
				status_code=404,
				media_type="application/json",
			)
	return await call_next(request)


@app.on_event("startup")
def _startup() -> None:
	migrate()
	info = ensure_bootstrap()
	# Recover at most the bounded bootstrap batch; incomplete setup remains in its
	# provisioning state until all durable child records exist.
	from simulacra.deploy_process import bootstrap_recovery_tick
	bootstrap_recovery_tick()
	log.info("bootstrap %s auth_required=%s", info, auth_required())


class CreateProjectBody(BaseModel):
	prompt: str = Field(min_length=3)
	goal: str = ""
	design_brief: dict[str, Any] | None = None
	# Legacy clients without a request id retain the pre-bootstrap behavior while
	# every new Mission bootstrap is reserved/recoverable below.
	client_request_id: str | None = Field(default=None, min_length=1, max_length=128)
	staged_source_refs: list[str] = Field(default_factory=list)
	tenant_id: str | None = None
	artifact_kind: str | None = None


class ChatBody(BaseModel):
	message: str = Field(min_length=1)
	chat_id: str | None = None


class CreateChatBody(BaseModel):
	title: str | None = None
	prompt: str = ""
	artifact_kind: str | None = None
	artifact_mode: str = "shared"  # shared | own


class ActivateChatBody(BaseModel):
	chat_id: str = Field(min_length=1)


class RollbackBody(BaseModel):
	checkpoint_id: str | None = None


class QueryBody(BaseModel):
	sql: str = "SELECT * FROM findings ORDER BY risk_score DESC LIMIT 50"


class DesignBriefBody(BaseModel):
	design_brief: dict[str, Any]


class CreateTenantBody(BaseModel):
	name: str = Field(min_length=1)
	notes: str = ""
	policy: dict[str, Any] | None = None


class UpdateTenantBody(BaseModel):
	name: str | None = None
	status: str | None = None
	notes: str | None = None
	policy: dict[str, Any] | None = None


class RegisterBody(BaseModel):
	email: str = Field(min_length=3)
	password: str = Field(min_length=8)
	name: str = ""
	tenant_name: str | None = None


class LoginBody(BaseModel):
	email: str
	password: str


class ForgotPasswordBody(BaseModel):
	email: str = Field(min_length=3)


class ResetPasswordBody(BaseModel):
	token: str = Field(min_length=8)
	password: str = Field(min_length=8)


class InviteBody(BaseModel):
	email: str
	role: str = "member"
	# If user exists, add membership; else return invite stub for them to register
	password: str | None = None
	name: str = ""


class ApiKeyBody(BaseModel):
	name: str = "default"


@app.get("/health")
def health() -> dict[str, Any]:
	return {
		"status": "ok",
		"ready": True,
	}


@app.get("/ready")
def ready() -> dict[str, Any]:
	ensure_bootstrap()
	return {"ready": True}


@app.get("/healthz")
def liveness() -> dict[str, str]:
	return {"status": "live"}


@app.get("/readyz")
def readiness() -> dict[str, Any]:
	identity = db_health()
	if not identity.get("ok", identity.get("status") in {"ok", "healthy"}):
		raise HTTPException(503, "service is not ready")
	return {"status": "ready"}


# ── Auth ─────────────────────────────────────────────────────────────


@app.get("/auth/config")
def auth_config() -> dict:
	"""Public auth bootstrap for the console (publishable Clerk key is not secret)."""
	from simulacra.demo.clerk_auth import clerk_enabled

	pk = (
		os.environ.get("VITE_CLERK_PUBLISHABLE_KEY")
		or os.environ.get("CLERK_PUBLISHABLE_KEY")
		or ""
	).strip()
	return {
		"auth_required": auth_required(),
		"clerk_enabled": clerk_enabled() and bool(pk),
		"clerk_publishable_key": pk or None,
		"clerk_frontend_api": os.environ.get("CLERK_FRONTEND_API", "https://clerk.platform.cmul8.com"),
	}


@app.post("/auth/register")
def auth_register(body: RegisterBody, request: Request) -> dict:
	try:
		user, token = register_user(
			body.email,
			body.password,
			name=body.name,
			tenant_name=body.tenant_name,
		)
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc
	tenants = user_tenants(user)
	return {
		"token": token,
		"token_type": "bearer",
		"user": user.public(),
		"tenants": tenants,
		"tenant_id": tenants[0]["id"] if tenants else default_tenant_id(),
	}


@app.post("/auth/login")
def auth_login(body: LoginBody) -> dict:
	try:
		user, token = login_user(body.email, body.password)
	except PermissionError as exc:
		raise HTTPException(401, str(exc)) from exc
	tenants = user_tenants(user)
	return {
		"token": token,
		"token_type": "bearer",
		"user": user.public(),
		"tenants": tenants,
		"tenant_id": tenants[0]["id"] if tenants else default_tenant_id(),
	}


@app.post("/auth/forgot-password")
def auth_forgot_password(body: ForgotPasswordBody) -> dict:
	"""Start a password reset. Returns a one-time link while email delivery is unset."""
	return request_password_reset(body.email.strip())


@app.post("/auth/reset-password")
def auth_reset_password(body: ResetPasswordBody) -> dict:
	try:
		user = reset_password_with_token(body.token.strip(), body.password)
	except PermissionError as exc:
		raise HTTPException(400, str(exc)) from exc
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc
	except KeyError as exc:
		raise HTTPException(404, str(exc)) from exc
	return {"ok": True, "email": user.email}


@app.get("/auth/me")
def auth_me(ctx: Annotated[AuthContext, Depends(get_auth)]) -> dict:
	return {
		"user": ctx.user.public(),
		"tenant_id": ctx.tenant_id,
		"role": ctx.role,
		"auth_via": ctx.auth_via,
		"tenants": user_tenants(ctx.user),
		"workplace_flags": workplace_flags_for_tenant(ctx.tenant_id),
	}


@app.post("/auth/api-keys")
def auth_create_key(
	body: ApiKeyBody,
	ctx: Annotated[AuthContext, Depends(require_perm("project:write"))],
) -> dict:
	raw, meta = create_api_key(ctx.user.id, name=body.name, tenant_id=ctx.tenant_id)
	return {"api_key": raw, "key": {k: v for k, v in meta.items() if k != "hash"}}


@app.get("/auth/api-keys")
def auth_list_keys(ctx: Annotated[AuthContext, Depends(get_auth)]) -> dict:
	return {"keys": list_api_keys(ctx.user.id)}


@app.delete("/auth/api-keys/{key_id}")
def auth_revoke_key(key_id: str, ctx: Annotated[AuthContext, Depends(get_auth)]) -> dict:
	revoke_api_key(key_id, ctx.user.id)
	return {"revoked": True, "id": key_id}


# ── Admin / tenants ──────────────────────────────────────────────────


@app.get("/admin")
def get_admin(ctx: Annotated[AuthContext, Depends(require_perm("tenant:read"))]) -> dict:
	scope = "*" if ctx.user.is_platform_admin else ctx.tenant_id
	return admin_overview(for_tenant_id=scope)


@app.get("/admin/sandbox")
def get_sandbox(ctx: Annotated[AuthContext, Depends(require_perm("tenant:read"))]) -> dict:
	return sandbox_status()


@app.get("/admin/audit")
def get_platform_audit(
	ctx: Annotated[AuthContext, Depends(require_perm("tenant:manage"))],
	limit: int = 100,
) -> dict:
	tid = None if ctx.user.is_platform_admin else ctx.tenant_id
	return {"events": list_audit(tenant_id=tid, limit=limit), "siem": siem_status()}


@app.get("/admin/audit/export")
def export_platform_audit(
	ctx: Annotated[AuthContext, Depends(require_perm("tenant:manage"))],
	format: str = "json",
	limit: int = 500,
	flush: bool = False,
):
	tid = None if ctx.user.is_platform_admin else ctx.tenant_id
	events = list_audit(tenant_id=tid, limit=limit)
	bundle = export_bundle(events, fmt=format, flush=flush)
	return Response(
		content=bundle["body"],
		media_type=bundle["content_type"],
		headers={
			"Content-Disposition": f'attachment; filename="{download_filename(format)}"',
			"X-Simulacra-Audit-Count": str(bundle["count"]),
			"X-Simulacra-SIEM-Flushed": str(bundle.get("flushed", 0)),
		},
	)


@app.post("/admin/audit/siem/flush")
def flush_siem(
	request: Request,
	ctx: Annotated[AuthContext, Depends(require_perm("tenant:manage"))],
	limit: int = 100,
) -> dict:
	tid = None if ctx.user.is_platform_admin else ctx.tenant_id
	events = list_audit(tenant_id=tid, limit=limit)
	bundle = export_bundle(events, flush=True)
	audit_request(request, ctx, "siem.flush", count=bundle["count"], flushed=bundle.get("flushed", 0))
	return {
		"count": bundle["count"],
		"flushed": bundle.get("flushed", 0),
		"siem": siem_status(),
		"format": bundle["format"],
	}


@app.get("/tenants")
def get_tenants(ctx: Annotated[AuthContext, Depends(require_perm("tenant:read"))]) -> dict:
	if ctx.user.is_platform_admin:
		return {"tenants": [t.to_dict() for t in list_tenants()]}
	return {"tenants": user_tenants(ctx.user)}


@app.post("/tenants")
def post_tenant(
	body: CreateTenantBody,
	request: Request,
	ctx: Annotated[AuthContext, Depends(get_auth)],
) -> dict:
	# Any authenticated user can create a workspace; they become owner
	tenant = create_tenant(body.name, policy=body.policy, notes=body.notes)
	add_membership(tenant.id, ctx.user.id, "owner")
	audit_request(request, ctx, "tenant.create", tenant_id=tenant.id)
	log.info("tenant_created id=%s by=%s", tenant.id, ctx.user.id)
	return {"tenant": tenant.to_dict()}


@app.patch("/tenants/{tenant_id}")
def patch_tenant(
	tenant_id: str,
	body: UpdateTenantBody,
	request: Request,
	ctx: Annotated[AuthContext, Depends(get_auth)],
) -> dict:
	if not ctx.user.is_platform_admin:
		if ctx.tenant_id != tenant_id:
			raise HTTPException(403, "Cannot manage other tenants")
		try:
			ctx.require("tenant:manage")
		except PermissionError as exc:
			raise HTTPException(403, str(exc)) from exc
	try:
		tenant = update_tenant(
			tenant_id,
			name=body.name,
			status=body.status,
			policy=body.policy,
			notes=body.notes,
		)
		audit_request(request, ctx, "tenant.update", tenant_id=tenant_id)
		return {"tenant": tenant.to_dict()}
	except KeyError as exc:
		raise HTTPException(404, str(exc)) from exc


@app.get("/tenants/{tenant_id}")
def get_one_tenant(tenant_id: str, ctx: Annotated[AuthContext, Depends(get_auth)]) -> dict:
	if not ctx.user.is_platform_admin and ctx.tenant_id != tenant_id:
		# allow if member
		from simulacra.demo.identity import get_membership

		if not get_membership(tenant_id, ctx.user.id):
			raise HTTPException(404, "Tenant not found")
	try:
		return {"tenant": get_tenant(tenant_id).to_dict()}
	except KeyError as exc:
		raise HTTPException(404, str(exc)) from exc


@app.get("/tenants/{tenant_id}/members")
def get_members(tenant_id: str, ctx: Annotated[AuthContext, Depends(get_auth)]) -> dict:
	if not ctx.user.is_platform_admin:
		if ctx.tenant_id != tenant_id:
			raise HTTPException(403, "Forbidden")
		try:
			ctx.require("tenant:members")
		except PermissionError as exc:
			raise HTTPException(403, str(exc)) from exc
	from simulacra.demo.identity import get_user

	members = []
	for m in list_memberships(tenant_id=tenant_id):
		try:
			u = get_user(m.user_id)
			members.append({"user": u.public(), "role": m.role, "created_at": m.created_at})
		except KeyError:
			continue
	return {"members": members}


@app.post("/tenants/{tenant_id}/members")
def invite_member(
	tenant_id: str,
	body: InviteBody,
	request: Request,
	ctx: Annotated[AuthContext, Depends(get_auth)],
) -> dict:
	if not ctx.user.is_platform_admin:
		if ctx.tenant_id != tenant_id:
			raise HTTPException(403, "Forbidden")
		try:
			ctx.require("tenant:members")
		except PermissionError as exc:
			raise HTTPException(403, str(exc)) from exc
	from simulacra.demo.identity import create_user, get_user_by_email

	role = body.role if body.role in ("owner", "admin", "member", "viewer") else "member"
	user = get_user_by_email(body.email)
	created = False
	if user is None:
		if not body.password:
			raise HTTPException(400, "password required to provision new user")
		user = create_user(body.email, body.password, name=body.name or body.email.split("@")[0])
		created = True
	add_membership(tenant_id, user.id, role)  # type: ignore[arg-type]
	audit_request(request, ctx, "tenant.invite", tenant_id=tenant_id, email=body.email, role=role)
	return {"user": user.public(), "role": role, "created": created}


@app.delete("/tenants/{tenant_id}/members/{user_id}")
def delete_member(
	tenant_id: str,
	user_id: str,
	request: Request,
	ctx: Annotated[AuthContext, Depends(get_auth)],
) -> dict:
	if not ctx.user.is_platform_admin:
		if ctx.tenant_id != tenant_id:
			raise HTTPException(403, "Forbidden")
		try:
			ctx.require("tenant:members")
		except PermissionError as exc:
			raise HTTPException(403, str(exc)) from exc
	remove_membership(tenant_id, user_id)
	audit_request(request, ctx, "tenant.member_remove", tenant_id=tenant_id, user_id=user_id)
	return {"removed": True}


@app.get("/governance")
def get_governance(ctx: Annotated[AuthContext, Depends(require_perm("tenant:read"))]) -> dict:
	return governance_overview()


# ── Projects (tenant-scoped) ─────────────────────────────────────────


@app.get("/projects/{project_id}/files")
def project_files(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	# Keep legacy callers on the authorized Mission file projection.  The mounted
	# workplace route normally wins by registration order; this bridge remains
	# correct if that order changes and preserves the historical ``files`` key.
	return authorized_file_inventory(project_id, kind="all", ctx=ctx)


@app.get("/projects/{project_id}/sources")
def get_sources(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	inventory = authorized_file_inventory(project_id, kind="source", ctx=ctx)
	state = load_state(project_id)
	preview = state.plan_preview or {}
	# Loading legacy project details happens outside the file projection lock.
	# Re-authorize immediately before publishing either file or detail data so a
	# human removed during that load receives no stale Mission response.
	inventory = authorized_file_inventory(project_id, kind="source", ctx=ctx)
	return {
		"files": inventory["files"],
		"profile": _public_source_profile(preview.get("profile")),
		"extract": _public_extract(preview.get("extract")),
		"row_count": preview.get("row_count") or state.row_count,
	}


@app.delete("/projects/{project_id}/sources/{file_name:path}")
def delete_source(
	project_id: str,
	file_name: str,
	request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	from simulacra.demo.pipeline import start_reingest
	from simulacra.demo.sources import SourceError, remove_source

	try:
		remove_source(project_id, file_name)
	except SourceError as exc:
		raise HTTPException(400, str(exc)) from exc
	audit_request(request, ctx, "sources.remove", project_id=project_id, file=file_name)
	try:
		start_reingest(project_id)
	except Exception as exc:
		raise HTTPException(503, {"code": "source_unavailable", "message": "Mission sources are temporarily unavailable."}) from exc
	return _public_project_snapshot(project_snapshot(project_id))


@app.post("/projects/{project_id}/sources/reingest")
def post_reingest(
	project_id: str,
	request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	from simulacra.demo.pipeline import start_reingest

	try:
		start_reingest(project_id)
	except Exception as exc:
		raise HTTPException(503, {"code": "source_unavailable", "message": "Mission sources are temporarily unavailable."}) from exc
	audit_request(request, ctx, "sources.reingest", project_id=project_id)
	return _public_project_snapshot(project_snapshot(project_id))


@app.get("/projects/{project_id}/events")
def get_events(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	return {"events": [_public_project_event(item) for item in list_events(project_id)]}


@app.get("/projects/{project_id}/events/stream")
async def stream_events(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> StreamingResponse:
	async def generate():
		q = subscribe(project_id)
		try:
			while True:
				try:
					evt = await asyncio.to_thread(q.get, True, 25)
					yield f"data: {json.dumps(_public_project_event(evt), default=str)}\n\n"
				except queue.Empty:
					yield ": heartbeat\n\n"
		finally:
			unsubscribe(project_id, q)

	return StreamingResponse(
		generate(),
		media_type="text/event-stream",
		headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
	)


@app.get("/projects/{project_id}/audit")
def project_audit(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	return _public_project_audit(project_id)


@app.get("/projects/{project_id}/audit/export")
def export_audit(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> FileResponse:
	bundle = _public_project_audit(project_id)
	archive = io.BytesIO()
	with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
		zf.writestr("audit.json", json.dumps(bundle, sort_keys=True, default=str, indent=2))
		zf.writestr("events.json", json.dumps(bundle["events"], sort_keys=True, default=str, indent=2))
		zf.writestr("deliverables.json", json.dumps(bundle["deliverables"], sort_keys=True, default=str, indent=2))
	return Response(
		content=archive.getvalue(), media_type="application/zip",
		headers={"Content-Disposition": f'attachment; filename="{project_id}-audit.zip"'},
	)


@app.get("/projects")
def get_projects(ctx: Annotated[AuthContext, Depends(require_perm("project:read"))]) -> dict:
	def _list_item(p):
		d = _public_project_snapshot({"project": p.to_dict()})["project"]
		d["chat_index"] = chat_summaries(p)
		# Sidebar only needs chat metadata — full transcript loads with the project
		d["chat"] = []
		slim = []
		for c in d.get("chats") or []:
			if isinstance(c, dict):
				slim.append({k: v for k, v in c.items() if k != "messages"})
		d["chats"] = slim
		return d

	if ctx.tenant_id == "*":
		return {"projects": [_list_item(p) for p in list_projects() if _is_normal_project_viewable(p)], "tenant_id": "*"}
	return {
		"projects": [_list_item(p) for p in list_projects(tenant_id=ctx.tenant_id) if _is_normal_project_viewable(p)],
		"tenant_id": ctx.tenant_id,
	}


@app.get("/formats")
def list_formats() -> dict:
	from simulacra.demo.formats import formats_catalog

	return {"formats": formats_catalog()}


@app.post("/workspace/bootstrap/sources")
async def stage_bootstrap_source(
	file: UploadFile = File(...),
	client_request_id: str = Form(...),
	ctx: Annotated[AuthContext, Depends(require_perm("project:write"))] = None,  # type: ignore[assignment]
) -> dict:
	"""Stage one immutable source before a Mission ID exists."""
	try:
		data = await file.read()
		record = SourceStaging().stage(
			tenant_id=ctx.tenant_id, actor_id=ctx.user.id, client_request_id=client_request_id,
			filename=file.filename or "source", media_type=file.content_type or "application/octet-stream", data=data,
		)
		return record.public()
	except ValueError as exc:
		if str(exc) == "idempotency_mismatch":
			raise HTTPException(409, {"code": "idempotency_mismatch", "message": "This source request does not match its earlier upload."}) from exc
		raise HTTPException(400, {"code": "source_unavailable", "message": "This source could not be staged."}) from exc


@app.get("/projects/bootstrap/{transaction_id}")
def get_project_bootstrap(
	transaction_id: str,
	ctx: Annotated[AuthContext, Depends(require_perm("project:read"))],
) -> Response:
	try:
		record = WorkspaceBootstrapCoordinator().lookup(tenant_id=ctx.tenant_id, actor_id=ctx.user.id, transaction_id=transaction_id)
		status, payload = WorkspaceBootstrapCoordinator().public(record)
		return JSONResponse(payload, status_code=status)
	except KeyError as exc:
		raise HTTPException(404, {"code": "bootstrap_unavailable", "message": "Mission setup is unavailable."}) from exc


@app.post("/projects")
def post_project(
	body: CreateProjectBody,
	request: Request,
	response: Response,
	ctx: Annotated[AuthContext, Depends(require_perm("project:write"))],
) -> dict:
	try:
		if ctx.role not in {"owner", "admin"} and not ctx.user.is_platform_admin:
			raise HTTPException(403, "only tenant owners and admins can bootstrap a project")
		# New callers receive a recoverable reservation rather than a one-shot
		# project creation. Tenant and human identity always come from auth.
		if workplace_flags_for_tenant(ctx.tenant_id).get("workplace_bootstrap_v1", False):
			if not body.client_request_id:
				raise HTTPException(400, {"code": "invalid_request", "message": "Mission setup needs a request identifier."})
			if body.tenant_id is not None:
				raise HTTPException(400, {"code": "invalid_request", "message": "Mission setup uses the current workspace."})
			coordinator = WorkspaceBootstrapCoordinator()
			record = coordinator.begin(tenant_id=ctx.tenant_id, actor_id=ctx.user.id, request={
				"client_request_id": body.client_request_id, "prompt": body.prompt, "goal": body.goal,
				"design_brief": body.design_brief, "artifact_kind": body.artifact_kind,
				"staged_source_refs": body.staged_source_refs,
			})
			status, payload = coordinator.public(record)
			if status == 409:
				raise HTTPException(409, payload)
			if status == 202:
				response.status_code = 202
				return payload
			audit_request(request, ctx, "project.bootstrap", project_id=record["reserved_project_id"])
			return payload
		brief = merge_brief(None, body.design_brief) if body.design_brief else None
		tid = body.tenant_id or ctx.tenant_id
		if tid == "*":
			tid = default_tenant_id()
		if not ctx.user.is_platform_admin and tid != ctx.tenant_id:
			raise HTTPException(403, "Cannot create project in another tenant")
		state = create_project(
			body.prompt,
			goal=body.goal,
			design_brief=brief,
			tenant_id=tid,
			artifact_kind=body.artifact_kind,
		)
		_bootstrap_project_room(state, ctx)
		state = init_plan(state, actor_id=ctx.user.id)
		audit_request(request, ctx, "project.create", project_id=state.id)
		log.info("project_created id=%s tenant=%s user=%s", state.id, state.tenant_id, ctx.user.id)
		from simulacra.demo.observe import duplicate_project_warnings

		snap = project_snapshot(state.id)
		warnings = duplicate_project_warnings(tid, body.prompt, exclude_id=state.id)
		# Exclude the project we just created from soft-dup noise when it's the only match
		if warnings:
			snap["warnings"] = warnings
		return _public_project_snapshot(snap)
	except HTTPException:
		raise
	except ValueError as exc:
		if str(exc) == "idempotency_mismatch":
			raise HTTPException(409, {"code": "idempotency_mismatch", "message": "This Mission request does not match its earlier submission."}) from exc
		raise
	except PermissionError as exc:
		log.exception("project_storage_unavailable tenant=%s user=%s", ctx.tenant_id, ctx.user.id)
		raise HTTPException(
			503,
			"Project storage is temporarily unavailable. Please try again.",
		) from exc
	except KeyError as exc:
		raise HTTPException(404, str(exc)) from exc
	except Exception as exc:
		log.exception("project_create_failed tenant=%s user=%s", ctx.tenant_id, ctx.user.id)
		raise HTTPException(500, {"code": "project_unavailable", "message": "Mission setup is temporarily unavailable."}) from exc


@app.post("/projects/{project_id}/plan")
def post_plan(
	project_id: str,
	body: ChatBody,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	"""Compat alias for the provider-neutral main chat."""
	try:
		return _public_project_snapshot(start_follow_up(project_id, body.message, chat_id=body.chat_id, actor_id=ctx.user.id))
	except ValueError as exc:
		raise _job_conflict_http(exc) from exc
	except Exception as exc:
		raise HTTPException(500, f"Chat failed: {exc}") from exc


@app.get("/projects/{project_id}")
def get_project(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	# The normal HTTP dependency already loaded the project before this handler.
	# Keep direct compatibility callers (and their public-snapshot contract) from
	# manufacturing a second storage dependency solely for this visibility gate.
	try:
		state = load_state(project_id)
	except FileNotFoundError:
		state = None
	if state is not None and not _is_normal_project_viewable(state):
		raise HTTPException(404, "Project not found")
	return _public_project_snapshot(project_snapshot(project_id))


@app.get("/projects/{project_id}/job")
def get_project_job(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	raise HTTPException(410, "Mission progress is available in the workspace.")


@app.post("/projects/{project_id}/cancel")
def post_cancel(
	project_id: str,
	request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	"""Stop a running Prime job. Idempotent if already idle — always unlocks the UI."""
	result = cancel_job(project_id)
	if result.get("ok") and not result.get("already_idle"):
		audit_request(request, ctx, "project.cancel", project_id=project_id)
	# Never 409 for "nothing to cancel" — console treats Stop as always safe
	return {
		**_public_project_snapshot(project_snapshot(project_id)),
		"cancelled": bool(result.get("ok") and not result.get("already_idle")),
		"already_idle": bool(result.get("already_idle")),
	}


@app.patch("/projects/{project_id}/design-brief")
def patch_design_brief(
	project_id: str,
	body: DesignBriefBody,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	update_project_brief(project_id, body.design_brief)
	return _public_project_snapshot(project_snapshot(project_id))


@app.post("/projects/{project_id}/approve", status_code=202)
def post_approve(
	project_id: str,
	request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	raise HTTPException(410, "Review and approve the Mission plan in Work.")


@app.post("/projects/{project_id}/build")
def post_build(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	_require_room_graph_authority(project_id, ctx)
	state = load_state(project_id)
	try:
		approved_graph_path(state)
	except (OperationGraphError, PermissionError) as exc:
		log.exception("Mission plan build admission failed for %s", project_id)
		raise HTTPException(409, "The Mission plan is not ready to start. Review it and try again.") from exc
	build_project(state, actor_id=ctx.user.id)
	return _public_project_snapshot(project_snapshot(project_id))


@app.post("/projects/{project_id}/chat")
def post_chat(
	project_id: str,
	body: ChatBody,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	try:
		return _public_project_snapshot(start_follow_up(project_id, body.message, chat_id=body.chat_id, actor_id=ctx.user.id))
	except ValueError as exc:
		raise _job_conflict_http(exc) from exc


@app.get("/projects/{project_id}/chats")
def get_chats(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	state = load_state(project_id)
	return {"chats": chat_summaries(state), "active_chat_id": state.active_chat_id}


@app.post("/projects/{project_id}/chats", status_code=201)
def post_create_chat(
	project_id: str,
	body: CreateChatBody,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	try:
		create_chat(
			project_id,
			title=body.title,
			prompt=body.prompt,
			artifact_kind=body.artifact_kind,
			artifact_mode=body.artifact_mode,
		)
		return _public_project_snapshot(project_snapshot(project_id))
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc


@app.post("/projects/{project_id}/chats/activate")
def post_activate_chat(
	project_id: str,
	body: ActivateChatBody,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	try:
		activate_chat(project_id, body.chat_id)
		return _public_project_snapshot(project_snapshot(project_id))
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc


@app.delete("/projects/{project_id}/chats/{chat_id}")
def delete_project_chat(
	project_id: str,
	chat_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	try:
		delete_chat(project_id, chat_id)
		return _public_project_snapshot(project_snapshot(project_id))
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc
	except FileNotFoundError as exc:
		raise HTTPException(404, str(exc)) from exc
	except Exception as exc:  # noqa: BLE001
		log.exception("delete chat failed project=%s chat=%s", project_id, chat_id)
		raise HTTPException(500, "Could not delete chat") from exc

@app.post("/projects/{project_id}/rollback")
def post_rollback(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
	body: RollbackBody | None = None,
) -> dict:
	try:
		ck = body.checkpoint_id if body else None
		rollback_project(project_id, ck)
		return _public_project_snapshot(project_snapshot(project_id))
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc


@app.post("/projects/{project_id}/query")
def post_query(
	project_id: str,
	body: QueryBody,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	try:
		return query(project_id, body.sql)
	except Exception as exc:
		raise HTTPException(400, str(exc)) from exc


@app.post("/projects/{project_id}/deploy")
def post_deploy(
	project_id: str,
	request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:deploy"))],
) -> dict:
	try:
		# Prefer forwarded host (Railway / reverse proxy) so Ship chat is absolute
		proto = request.headers.get("x-forwarded-proto") or request.url.scheme
		host = request.headers.get("x-forwarded-host") or request.headers.get("host")
		base = f"{proto}://{host}".rstrip("/") if host else str(request.base_url).rstrip("/")
		approve_deploy(project_id, public_base=base)
		audit_request(request, ctx, "project.deploy", project_id=project_id)
		return _public_project_snapshot(project_snapshot(project_id))
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc


@app.post("/projects/{project_id}/upload")
async def upload_files(
	project_id: str,
	request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
	files: list[UploadFile] = File(...),
	client_request_id: str | None = Form(default=None),
	reingest: bool = True,
) -> dict:
	from simulacra.demo.pipeline import start_reingest
	from simulacra.demo.sources import SourceError, add_upload, list_source_files, safe_source_name, sync_data_room

	if not files:
		raise HTTPException(400, "No files uploaded")
	bootstrap_enabled = workplace_flags_for_tenant(ctx.tenant_id).get("workplace_bootstrap_v1", False)
	if bootstrap_enabled and not client_request_id:
		raise HTTPException(400, {"code": "invalid_request", "message": "Mission source uploads need a request identifier."})
	if client_request_id and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", client_request_id):
		raise HTTPException(400, {"code": "invalid_request", "message": "Mission source uploads need a valid request identifier."})
	uploaded: list[dict] = []
	errors: list[str] = []
	# Keep idempotency control data outside the human-visible data room.
	# It is opened only through checked directory descriptors below: a symlinked
	# control file or ancestor is never followed.
	ledger_name = "project-source-uploads.json"
	lock_name = ".project-source-uploads.lock"
	items: list[dict[str, Any]] = []
	try:
		for upload in files:
			data = await upload.read()
			items.append({
				"filename": safe_source_name(str(upload.filename or "")),
				"media_type": upload.content_type or "application/octet-stream",
				"sha256": hashlib.sha256(data).hexdigest(), "data": data,
			})
	except SourceError as exc:
		raise HTTPException(400, str(exc)) from exc
	if len({item["filename"] for item in items}) != len(items):
		raise HTTPException(400, {"code": "invalid_request", "message": "Each uploaded file needs a distinct name."})

	def unavailable() -> None:
		raise HTTPException(503, {"code": "source_unavailable", "message": "Mission sources are temporarily unavailable."})

	def open_control_dir() -> int:
		flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
		try:
			workspace_fd = os.open(project_dir(project_id), flags)
			try:
				if not stat.S_ISDIR(os.fstat(workspace_fd).st_mode):
					unavailable()
				try:
					os.mkdir(".workplace-control", 0o700, dir_fd=workspace_fd)
				except FileExistsError:
					pass
				control_fd = os.open(".workplace-control", flags, dir_fd=workspace_fd)
				if not stat.S_ISDIR(os.fstat(control_fd).st_mode):
					os.close(control_fd); unavailable()
				return control_fd
			finally:
				os.close(workspace_fd)
		except HTTPException:
			raise
		except OSError:
			unavailable()

	def read_ledger(control_fd: int) -> dict[str, Any]:
		def unavailable() -> None:
			raise HTTPException(503, {"code": "source_unavailable", "message": "Mission sources are temporarily unavailable."})

		def valid_key(value: object) -> bool:
			return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", value))

		def valid_hash(value: object) -> bool:
			return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))

		def valid_name(value: object) -> bool:
			if not isinstance(value, str) or not value:
				return False
			try:
				return safe_source_name(value) == value
			except SourceError:
				return False

		def valid_public_value(value: object, *, depth: int = 0) -> bool:
			"""Bound replay records to the public JSON shape, never opaque objects."""
			if depth > 8:
				return False
			if value is None or isinstance(value, (bool, int, float)):
				return True
			if isinstance(value, str):
				return len(value) <= 20_000
			if isinstance(value, list):
				return len(value) <= 1_000 and all(valid_public_value(item, depth=depth + 1) for item in value)
			if isinstance(value, dict):
				return len(value) <= 100 and all(isinstance(key, str) and len(key) <= 128 and valid_public_value(item, depth=depth + 1) for key, item in value.items())
			return False

		def valid_response(value: object, *, project_key: str, entry: dict[str, Any]) -> bool:
			if not isinstance(value, dict):
				return False
			allowed = {"project", "preview_data", "preview_url", "status", "cancelled", "already_idle", "uploaded", "files", "errors", "project_id", "processing"}
			if set(value) - allowed or {"uploaded", "files", "errors", "project_id"} - set(value):
				return False
			if value.get("project_id") != project_key or not isinstance(value.get("uploaded"), int) or value["uploaded"] < 0:
				return False
			if "processing" in value and value["processing"] != "pending":
				return False
			if not isinstance(value.get("files"), list) or not isinstance(value.get("errors"), list) or value["errors"]:
				return False
			if value["uploaded"] != len(value["files"]) or len(value["files"]) != len(entry["files"]):
				return False
			for public, persisted in zip(value["files"], entry["files"], strict=True):
				if not isinstance(public, dict) or set(public) - {"name", "size", "type", "status", "detail", "row_count"}:
					return False
				if public.get("name") != persisted["name"] or not valid_public_value(public):
					return False
			return all(valid_public_value(item) for item in value.values())

		try:
			descriptor = os.open(ledger_name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), dir_fd=control_fd)
		except FileNotFoundError:
			return {"schema_version": 1, "requests": {}}
		except OSError:
			unavailable()
		try:
			if not stat.S_ISREG(os.fstat(descriptor).st_mode):
				unavailable()
			with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
				value = json.load(handle)
		except (OSError, json.JSONDecodeError):
			unavailable()
		if not isinstance(value, dict) or value.get("schema_version") != 1 or not isinstance(value.get("requests"), dict):
			unavailable()
		for tenant_key, projects in value["requests"].items():
			if not valid_key(tenant_key) or not isinstance(projects, dict):
				unavailable()
			for project_key, humans in projects.items():
				if not valid_key(project_key) or not isinstance(humans, dict):
					unavailable()
				for human_key, requests in humans.items():
					if not valid_key(human_key) or not isinstance(requests, dict):
						unavailable()
					for request_key, entry in requests.items():
						if (
							not valid_key(request_key) or not isinstance(entry, dict)
							or set(entry) - {"manifest", "reingest", "files", "complete", "response", "reingest_state"}
							or not isinstance(entry.get("manifest"), list) or not isinstance(entry.get("files"), list)
							or not isinstance(entry.get("complete"), bool) or not isinstance(entry.get("reingest"), bool)
						):
							unavailable()
						if entry.get("complete") != ("response" in entry):
							unavailable()
						if "reingest_state" in entry and entry["reingest_state"] not in {"starting", "completed", "failed"}:
							unavailable()
						for item in entry["manifest"]:
							if (
								not isinstance(item, dict) or set(item) != {"filename", "media_type", "sha256"}
								or not valid_name(item.get("filename"))
								or not isinstance(item.get("media_type"), str) or not item["media_type"] or "\n" in item["media_type"]
								or not valid_hash(item.get("sha256"))
							):
								unavailable()
						for item in entry["files"]:
							if not isinstance(item, dict) or set(item) != {"name", "sha256"} or not valid_name(item.get("name")) or not valid_hash(item.get("sha256")):
								unavailable()
						if entry.get("complete") and not valid_response(entry["response"], project_key=project_key, entry=entry):
							unavailable()
		return value

	def publish_ledger(control_fd: int, value: dict[str, Any]) -> None:
		temp_name = f".{ledger_name}.{uuid.uuid4().hex}.tmp"
		descriptor = -1
		try:
			descriptor = os.open(temp_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=control_fd)
			with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
				descriptor = -1
				handle.write(json.dumps(value, sort_keys=True)); handle.flush(); os.fsync(handle.fileno())
			os.replace(temp_name, ledger_name, src_dir_fd=control_fd, dst_dir_fd=control_fd)
			os.fsync(control_fd)
		except OSError:
			unavailable()
		finally:
			if descriptor >= 0:
				os.close(descriptor)
			try:
				os.unlink(temp_name, dir_fd=control_fd)
			except FileNotFoundError:
				pass
			except OSError:
				unavailable()

	created_new = False
	if client_request_id:
		# A request-wide ledger is published before the first data-room write.
		# Therefore a process crash can only leave a resumable manifest, never an
		# untracked file that gets a new filename on retry.
		control_fd = open_control_dir()
		try:
			lock_fd = os.open(lock_name, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0), 0o600, dir_fd=control_fd)
			if not stat.S_ISREG(os.fstat(lock_fd).st_mode):
				os.close(lock_fd); unavailable()
			lock = os.fdopen(lock_fd, "a+b")
			fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
			try:
				ledger = read_ledger(control_fd)
				requests = (
					ledger.setdefault("requests", {})
					.setdefault(ctx.tenant_id, {})
					.setdefault(project_id, {})
					.setdefault(ctx.user.id, {})
				)
				manifest = [{key: item[key] for key in ("filename", "media_type", "sha256")} for item in items]
				entry = requests.get(client_request_id)
				if entry is not None and (entry.get("manifest") != manifest or entry.get("reingest") is not reingest):
					raise HTTPException(409, {"code": "idempotency_mismatch", "message": "This source request does not match its earlier upload."})
				if entry is not None and entry.get("complete"):
					return dict(entry["response"])
				if entry is None:
					entry = {"manifest": manifest, "reingest": reingest, "files": [], "complete": False}
					requests[client_request_id] = entry
					publish_ledger(control_fd, ledger)
				for item in items:
					existing = next((source for source in list_source_files(project_id) if source.name == item["filename"]), None)
					if existing is not None:
						try:
							sync_data_room(project_id)
						except OSError as exc:
							raise HTTPException(503, {"code": "source_unavailable", "message": "Mission sources are temporarily unavailable."}) from exc
						if existing.sha256 != item["sha256"]:
							raise HTTPException(409, {"code": "idempotency_mismatch", "message": "This source request does not match its earlier upload."})
						src = existing
					else:
						try:
							src = add_upload(project_id, filename=item["filename"], data=item["data"], overwrite=False)
						except OSError as exc:
							raise HTTPException(503, {"code": "source_unavailable", "message": "Mission sources are temporarily unavailable."}) from exc
						created_new = True
					if not any(value.get("name") == src.name for value in entry["files"]):
						entry["files"].append({"name": src.name, "sha256": src.sha256})
						publish_ledger(control_fd, ledger)
					uploaded.append(_public_source_file(src.to_dict()))
				state = load_state(project_id)
				state.status = "uploaded"
				save_state(state)
				snap: dict[str, Any] = {"uploaded": len(uploaded), "files": uploaded, "errors": errors, "project_id": project_id}
				if reingest:
					# Persist intent before invoking processing.  A retry after an
					# interruption returns a stable pending state rather than starting
					# an identical processing pass a second time.
					if entry.get("reingest_state") == "starting":
						snap["processing"] = "pending"
					elif entry.get("reingest_state") == "failed":
						raise HTTPException(503, {"code": "source_unavailable", "message": "Mission sources are temporarily unavailable."})
					else:
						entry["reingest_state"] = "starting"; publish_ledger(control_fd, ledger)
						try:
							start_reingest(project_id)
						except Exception:
							entry["reingest_state"] = "failed"; publish_ledger(control_fd, ledger)
							raise HTTPException(503, {"code": "source_unavailable", "message": "Mission sources are temporarily unavailable."})
						entry["reingest_state"] = "completed"
						snap = {**_public_project_snapshot(project_snapshot(project_id)), **snap}
				entry["complete"] = True
				entry["response"] = snap
				publish_ledger(control_fd, ledger)
			finally:
				fcntl.flock(lock.fileno(), fcntl.LOCK_UN)
				lock.close()
		except HTTPException:
			raise
		except OSError:
			unavailable()
		finally:
			os.close(control_fd)
	else:
		for item in items:
			try:
				src = add_upload(project_id, filename=item["filename"], data=item["data"], overwrite=True)
				uploaded.append(_public_source_file(src.to_dict()))
			except SourceError as exc:
				errors.append(str(exc))
	if not uploaded and errors:
		raise HTTPException(400, "; ".join(errors[:5]))
	if client_request_id:
		# The exact response above is durably recorded before this audit event.
		audit_request(request, ctx, "sources.upload", project_id=project_id, count=len(uploaded))
		return snap
	state = load_state(project_id)
	state.status = "uploaded"
	save_state(state)
	audit_request(request, ctx, "sources.upload", project_id=project_id, count=len(uploaded))
	snap: dict[str, Any] = {"uploaded": len(uploaded), "files": uploaded, "errors": errors, "project_id": project_id}
	if reingest and uploaded and (not client_request_id or created_new):
		try:
			start_reingest(project_id)
			snap = {**_public_project_snapshot(project_snapshot(project_id)), **snap}
		except Exception:
			raise HTTPException(503, {"code": "source_unavailable", "message": "Mission sources are temporarily unavailable."})
	return snap


# ── Console SPA (production / Docker) ────────────────────────────────

_CONSOLE_DIST = Path(__file__).resolve().parents[1] / "console" / "dist"
if _CONSOLE_DIST.is_dir():
	_assets = _CONSOLE_DIST / "assets"
	if _assets.is_dir():
		app.mount("/assets", StaticFiles(directory=_assets), name="console-assets")

	@app.get("/")
	def console_index() -> FileResponse:
		return FileResponse(_CONSOLE_DIST / "index.html")

	@app.get("/{full_path:path}")
	def console_spa(full_path: str) -> FileResponse:
		# Never steal API/auth/admin paths if routing order ever regresses
		api_prefixes = (
			"auth/",
			"admin/",
			"projects/",
			"tenants/",
			"formats",
			"governance",
			"health",
			"ready",
			"docs",
			"openapi",
		)
		if full_path == "health" or full_path.startswith(api_prefixes):
			# Let missing API routes surface as clear 404s (not SPA fallback)
			raise HTTPException(404, f"Not found: /{full_path}")
		candidate = _CONSOLE_DIST / full_path
		if candidate.is_file():
			return FileResponse(candidate)
		return FileResponse(_CONSOLE_DIST / "index.html")
