from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from apps.api.security import audit_request, get_auth, require_perm, require_project_access
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
	revoke_api_key,
	user_tenants,
)
from simulacra.demo.jobs import get_job, job_snapshot
from simulacra.demo.paths import FIXTURES, RUNS_DIR
from simulacra.demo.pipeline import (
	approve_deploy,
	build_project,
	cancel_job,
	export_audit_zip,
	init_plan,
	plan_chat,
	project_snapshot,
	rollback_project,
	start_approve_build,
	start_follow_up,
)
from simulacra.demo.runs import create_project, list_projects, load_state, project_dir, save_state
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

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("simulacra.api")

app = FastAPI(title="Simulacra API", version="0.7.0")
app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


@app.on_event("startup")
def _startup() -> None:
	migrate()
	info = ensure_bootstrap()
	log.info("bootstrap %s auth_required=%s", info, auth_required())


class CreateProjectBody(BaseModel):
	prompt: str = Field(min_length=3)
	goal: str = ""
	use_fixture: bool = True
	design_brief: dict[str, Any] | None = None
	tenant_id: str | None = None


class ChatBody(BaseModel):
	message: str = Field(min_length=1)


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
	from simulacra.demo.clerk_auth import clerk_enabled
	from simulacra.demo.prime_hook import prime_enabled

	sb = sandbox_status()
	return {
		"status": "ok",
		"product": "simulacra",
		"prime": "enabled" if prime_enabled() else "off",
		"sandbox": sb.get("active"),
		"auth_required": auth_required(),
		"identity": db_health(),
		"siem": siem_status(),
		"clerk": clerk_enabled(),
		"version": "0.7.0",
	}


@app.get("/ready")
def ready() -> dict[str, Any]:
	checks: dict[str, bool] = {"runs_dir": True}
	try:
		resolve_prime_agent(prefer_source=True)
		checks["prime_binary"] = True
	except Exception:
		checks["prime_binary"] = False
	from simulacra.demo.prime_hook import prime_enabled

	checks["prime_flag"] = prime_enabled()
	checks["bootstrap"] = True
	ensure_bootstrap()
	return {"ready": True, "checks": checks, "sandbox": sandbox_status()}


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


@app.get("/auth/me")
def auth_me(ctx: Annotated[AuthContext, Depends(get_auth)]) -> dict:
	return {
		"user": ctx.user.public(),
		"tenant_id": ctx.tenant_id,
		"role": ctx.role,
		"auth_via": ctx.auth_via,
		"tenants": user_tenants(ctx.user),
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


@app.get("/fixtures/data-room")
def fixture_files(ctx: Annotated[AuthContext, Depends(require_perm("project:read"))]) -> dict:
	if not FIXTURES.exists():
		return {"files": []}
	files = [
		{"name": p.name, "size": p.stat().st_size, "type": p.suffix.lstrip(".")}
		for p in sorted(FIXTURES.iterdir())
		if p.is_file()
	]
	return {"files": files, "path": str(FIXTURES)}


# ── Projects (tenant-scoped) ─────────────────────────────────────────


@app.get("/projects/{project_id}/files")
def project_files(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	root = project_dir(project_id) / "inputs" / "data-room"
	if not root.exists():
		raise HTTPException(404, "Data room not found")
	files = [
		{"name": str(p.relative_to(root)), "size": p.stat().st_size, "type": p.suffix.lstrip(".")}
		for p in sorted(root.rglob("*"))
		if p.is_file()
	]
	return {"files": files}


@app.get("/projects/{project_id}/events")
def get_events(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	return {"events": list_events(project_id)}


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
					yield f"data: {json.dumps(evt, default=str)}\n\n"
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
	root = project_dir(project_id) / "audit"
	out: dict = {}
	for name in ("gates.json", "deploy.json", "policy_snapshot.json", "sandbox.json"):
		path = root / name
		if path.exists():
			out[name.replace(".json", "")] = json.loads(path.read_text())
	manifest = project_dir(project_id) / "outputs" / "manifest.json"
	if manifest.exists():
		out["manifest"] = json.loads(manifest.read_text())
	out["checkpoints"] = list_checkpoints(project_id)
	return out


@app.get("/projects/{project_id}/audit/export")
def export_audit(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> FileResponse:
	path = export_audit_zip(project_id)
	return FileResponse(path, filename=path.name, media_type="application/zip")


@app.get("/projects")
def get_projects(ctx: Annotated[AuthContext, Depends(require_perm("project:read"))]) -> dict:
	if ctx.tenant_id == "*":
		return {"projects": [p.to_dict() for p in list_projects()], "tenant_id": "*"}
	return {
		"projects": [p.to_dict() for p in list_projects(tenant_id=ctx.tenant_id)],
		"tenant_id": ctx.tenant_id,
	}


@app.post("/projects")
def post_project(
	body: CreateProjectBody,
	request: Request,
	ctx: Annotated[AuthContext, Depends(require_perm("project:write"))],
) -> dict:
	try:
		brief = merge_brief(None, body.design_brief) if body.design_brief else None
		tid = body.tenant_id or ctx.tenant_id
		if tid == "*":
			tid = default_tenant_id()
		if not ctx.user.is_platform_admin and tid != ctx.tenant_id:
			raise HTTPException(403, "Cannot create project in another tenant")
		state = create_project(
			body.prompt,
			use_fixture=body.use_fixture,
			goal=body.goal,
			design_brief=brief,
			tenant_id=tid,
		)
		state = init_plan(state)
		audit_request(request, ctx, "project.create", project_id=state.id)
		log.info("project_created id=%s tenant=%s user=%s", state.id, state.tenant_id, ctx.user.id)
		return project_snapshot(state.id)
	except PermissionError as exc:
		raise HTTPException(403, str(exc)) from exc
	except KeyError as exc:
		raise HTTPException(404, str(exc)) from exc
	except Exception as exc:
		raise HTTPException(500, f"Failed to create project: {exc}") from exc


@app.post("/projects/{project_id}/plan")
def post_plan(
	project_id: str,
	body: ChatBody,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	try:
		plan_chat(project_id, body.message)
		return project_snapshot(project_id)
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc
	except Exception as exc:
		raise HTTPException(500, f"Plan chat failed: {exc}") from exc


@app.get("/projects/{project_id}")
def get_project(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	return project_snapshot(project_id)


@app.get("/projects/{project_id}/job")
def get_project_job(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict:
	live = get_job(project_id)
	return {"job": job_snapshot(project_id), "live": live is not None and live.status == "running"}


@app.post("/projects/{project_id}/cancel")
def post_cancel(
	project_id: str,
	request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	result = cancel_job(project_id)
	if not result.get("ok"):
		raise HTTPException(409, result.get("error") or "no_running_job")
	audit_request(request, ctx, "project.cancel", project_id=project_id)
	return {**project_snapshot(project_id), "cancelled": True}


@app.patch("/projects/{project_id}/design-brief")
def patch_design_brief(
	project_id: str,
	body: DesignBriefBody,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	update_project_brief(project_id, body.design_brief)
	return project_snapshot(project_id)


@app.post("/projects/{project_id}/approve", status_code=202)
def post_approve(
	project_id: str,
	request: Request,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:approve"))],
) -> dict:
	try:
		result = start_approve_build(project_id)
		audit_request(request, ctx, "project.approve", project_id=project_id, job_id=result.get("job_id"))
		return result
	except ValueError as exc:
		raise HTTPException(409 if "already" in str(exc).lower() else 400, str(exc)) from exc
	except Exception as exc:
		raise HTTPException(500, f"Build failed: {exc}") from exc


@app.post("/projects/{project_id}/build")
def post_build(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:approve"))],
) -> dict:
	state = load_state(project_id)
	build_project(state)
	return project_snapshot(project_id)


@app.post("/projects/{project_id}/chat")
def post_chat(
	project_id: str,
	body: ChatBody,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
) -> dict:
	try:
		return start_follow_up(project_id, body.message)
	except ValueError as exc:
		raise HTTPException(409 if "already" in str(exc).lower() else 400, str(exc)) from exc


@app.post("/projects/{project_id}/rollback")
def post_rollback(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
	body: RollbackBody | None = None,
) -> dict:
	try:
		ck = body.checkpoint_id if body else None
		rollback_project(project_id, ck)
		return project_snapshot(project_id)
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
		approve_deploy(project_id)
		audit_request(request, ctx, "project.deploy", project_id=project_id)
		return project_snapshot(project_id)
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc


@app.post("/projects/{project_id}/upload")
async def upload_files(
	project_id: str,
	ctx: Annotated[AuthContext, Depends(require_project_access("project:write"))],
	files: list[UploadFile] = File(...),
) -> dict:
	state = load_state(project_id)
	dest = project_dir(project_id) / "inputs" / "data-room"
	dest.mkdir(parents=True, exist_ok=True)
	for f in files:
		path = dest / (f.filename or "upload.bin")
		path.write_bytes(await f.read())
	state.status = "uploaded"
	save_state(state)
	return {"uploaded": len(files), "project_id": project_id}


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
			"governance",
			"fixtures/",
			"health",
			"ready",
			"docs",
			"openapi",
		)
		if full_path == "health" or full_path.startswith(api_prefixes):
			raise HTTPException(404, "Not found")
		candidate = _CONSOLE_DIST / full_path
		if candidate.is_file():
			return FileResponse(candidate)
		return FileResponse(_CONSOLE_DIST / "index.html")
