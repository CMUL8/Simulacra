from __future__ import annotations

import asyncio
import json
import logging
import queue
from typing import Any

from fastapi import FastAPI, File, Header, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from simulacra.env import load_dotenv
from simulacra.demo.checkpoints import list_checkpoints
from simulacra.demo.design_brief import merge_brief, update_project_brief
from simulacra.demo.duckdb_engine import query
from simulacra.demo.events import list_events, subscribe, unsubscribe
from simulacra.demo.governance import governance_overview
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
from simulacra.demo.tenants import (
	admin_overview,
	create_tenant,
	default_tenant_id,
	get_tenant,
	list_tenants,
	update_tenant,
)
from simulacra.resolve import resolve_prime_agent

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger("simulacra.api")

app = FastAPI(title="Simulacra API", version="0.4.0")
app.add_middleware(
	CORSMiddleware,
	allow_origins=["*"],
	allow_credentials=True,
	allow_methods=["*"],
	allow_headers=["*"],
)


def _tenant_from_header(x_tenant_id: str | None) -> str:
	return (x_tenant_id or default_tenant_id()).strip() or default_tenant_id()


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


@app.get("/health")
def health() -> dict[str, Any]:
	from simulacra.demo.prime_hook import prime_enabled

	sb = sandbox_status()
	return {
		"status": "ok",
		"product": "simulacra",
		"prime": "enabled" if prime_enabled() else "off",
		"sandbox": sb.get("active"),
		"version": "0.4.0",
	}


@app.get("/ready")
def ready() -> dict[str, Any]:
	checks: dict[str, bool] = {}
	checks["runs_dir"] = RUNS_DIR.exists() or True
	try:
		resolve_prime_agent(prefer_source=True)
		checks["prime_binary"] = True
	except Exception:
		checks["prime_binary"] = False
	from simulacra.demo.prime_hook import prime_enabled

	checks["dotenv"] = True
	checks["prime_flag"] = prime_enabled()
	sb = sandbox_status()
	ok = checks["runs_dir"]
	return {"ready": ok, "checks": checks, "sandbox": sb}


@app.get("/admin")
def get_admin() -> dict:
	return admin_overview()


@app.get("/admin/sandbox")
def get_sandbox() -> dict:
	return sandbox_status()


@app.get("/tenants")
def get_tenants() -> dict:
	return {"tenants": [t.to_dict() for t in list_tenants()]}


@app.post("/tenants")
def post_tenant(body: CreateTenantBody) -> dict:
	tenant = create_tenant(body.name, policy=body.policy, notes=body.notes)
	log.info("tenant_created id=%s", tenant.id)
	return {"tenant": tenant.to_dict()}


@app.patch("/tenants/{tenant_id}")
def patch_tenant(tenant_id: str, body: UpdateTenantBody) -> dict:
	try:
		tenant = update_tenant(
			tenant_id,
			name=body.name,
			status=body.status,
			policy=body.policy,
			notes=body.notes,
		)
		return {"tenant": tenant.to_dict()}
	except KeyError as exc:
		raise HTTPException(404, str(exc)) from exc


@app.get("/tenants/{tenant_id}")
def get_one_tenant(tenant_id: str) -> dict:
	try:
		return {"tenant": get_tenant(tenant_id).to_dict()}
	except KeyError as exc:
		raise HTTPException(404, str(exc)) from exc


@app.get("/governance")
def get_governance() -> dict:
	return governance_overview()


@app.get("/fixtures/data-room")
def fixture_files() -> dict:
	if not FIXTURES.exists():
		return {"files": []}
	files = [
		{"name": p.name, "size": p.stat().st_size, "type": p.suffix.lstrip(".")}
		for p in sorted(FIXTURES.iterdir())
		if p.is_file()
	]
	return {"files": files, "path": str(FIXTURES)}


@app.get("/projects/{project_id}/files")
def project_files(project_id: str) -> dict:
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
def get_events(project_id: str) -> dict:
	try:
		load_state(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc
	return {"events": list_events(project_id)}


@app.get("/projects/{project_id}/events/stream")
async def stream_events(project_id: str) -> StreamingResponse:
	try:
		load_state(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc

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
def project_audit(project_id: str) -> dict:
	root = project_dir(project_id) / "audit"
	out: dict = {}
	for name in ("gates.json", "deploy.json", "policy_snapshot.json"):
		path = root / name
		if path.exists():
			out[name.replace(".json", "")] = json.loads(path.read_text())
	manifest = project_dir(project_id) / "outputs" / "manifest.json"
	if manifest.exists():
		out["manifest"] = json.loads(manifest.read_text())
	out["checkpoints"] = list_checkpoints(project_id)
	return out


@app.get("/projects/{project_id}/audit/export")
def export_audit(project_id: str) -> FileResponse:
	try:
		load_state(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc
	path = export_audit_zip(project_id)
	return FileResponse(path, filename=path.name, media_type="application/zip")


@app.get("/projects")
def get_projects(x_tenant_id: str | None = Header(default=None)) -> dict:
	tid = _tenant_from_header(x_tenant_id)
	# Admin view: pass X-Tenant-Id: * to list all
	if tid == "*":
		return {"projects": [p.to_dict() for p in list_projects()], "tenant_id": "*"}
	return {"projects": [p.to_dict() for p in list_projects(tenant_id=tid)], "tenant_id": tid}


@app.post("/projects")
def post_project(
	body: CreateProjectBody,
	x_tenant_id: str | None = Header(default=None),
) -> dict:
	try:
		brief = merge_brief(None, body.design_brief) if body.design_brief else None
		tid = body.tenant_id or _tenant_from_header(x_tenant_id)
		state = create_project(
			body.prompt,
			use_fixture=body.use_fixture,
			goal=body.goal,
			design_brief=brief,
			tenant_id=tid,
		)
		state = init_plan(state)
		log.info("project_created id=%s tenant=%s", state.id, state.tenant_id)
		return project_snapshot(state.id)
	except PermissionError as exc:
		raise HTTPException(403, str(exc)) from exc
	except KeyError as exc:
		raise HTTPException(404, str(exc)) from exc
	except Exception as exc:
		raise HTTPException(500, f"Failed to create project: {exc}") from exc


@app.post("/projects/{project_id}/plan")
def post_plan(project_id: str, body: ChatBody) -> dict:
	try:
		plan_chat(project_id, body.message)
		return project_snapshot(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc
	except Exception as exc:
		raise HTTPException(500, f"Plan chat failed: {exc}") from exc


@app.get("/projects/{project_id}")
def get_project(project_id: str) -> dict:
	try:
		return project_snapshot(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc


@app.get("/projects/{project_id}/job")
def get_project_job(project_id: str) -> dict:
	try:
		load_state(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc
	live = get_job(project_id)
	return {"job": job_snapshot(project_id), "live": live is not None and live.status == "running"}


@app.post("/projects/{project_id}/cancel")
def post_cancel(project_id: str) -> dict:
	try:
		load_state(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc
	result = cancel_job(project_id)
	if not result.get("ok"):
		raise HTTPException(409, result.get("error") or "no_running_job")
	log.info("job_cancelled project=%s", project_id)
	return {**project_snapshot(project_id), "cancelled": True}


@app.patch("/projects/{project_id}/design-brief")
def patch_design_brief(project_id: str, body: DesignBriefBody) -> dict:
	try:
		update_project_brief(project_id, body.design_brief)
		return project_snapshot(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc


@app.post("/projects/{project_id}/approve", status_code=202)
def post_approve(project_id: str) -> dict:
	try:
		result = start_approve_build(project_id)
		log.info("approve_started project=%s job=%s", project_id, result.get("job_id"))
		return result
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc
	except ValueError as exc:
		raise HTTPException(409 if "already" in str(exc).lower() else 400, str(exc)) from exc
	except Exception as exc:
		raise HTTPException(500, f"Build failed: {exc}") from exc


@app.post("/projects/{project_id}/build")
def post_build(project_id: str) -> dict:
	try:
		state = load_state(project_id)
		build_project(state)
		return project_snapshot(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc


@app.post("/projects/{project_id}/chat")
def post_chat(project_id: str, body: ChatBody) -> dict:
	try:
		result = start_follow_up(project_id, body.message)
		# If background job started, treat as 202 semantics (same payload)
		return result
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc
	except ValueError as exc:
		raise HTTPException(409 if "already" in str(exc).lower() else 400, str(exc)) from exc


@app.post("/projects/{project_id}/rollback")
def post_rollback(project_id: str, body: RollbackBody | None = None) -> dict:
	try:
		ck = body.checkpoint_id if body else None
		rollback_project(project_id, ck)
		return project_snapshot(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc


@app.post("/projects/{project_id}/query")
def post_query(project_id: str, body: QueryBody) -> dict:
	try:
		return query(project_id, body.sql)
	except Exception as exc:
		raise HTTPException(400, str(exc)) from exc


@app.post("/projects/{project_id}/deploy")
def post_deploy(project_id: str) -> dict:
	try:
		approve_deploy(project_id)
		return project_snapshot(project_id)
	except ValueError as exc:
		raise HTTPException(400, str(exc)) from exc
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc


@app.post("/projects/{project_id}/upload")
async def upload_files(project_id: str, files: list[UploadFile] = File(...)) -> dict:
	try:
		state = load_state(project_id)
	except FileNotFoundError as exc:
		raise HTTPException(404, "Project not found") from exc

	dest = project_dir(project_id) / "inputs" / "data-room"
	dest.mkdir(parents=True, exist_ok=True)
	for f in files:
		path = dest / (f.filename or "upload.bin")
		path.write_bytes(await f.read())
	state.status = "uploaded"
	save_state(state)
	return {"uploaded": len(files), "project_id": project_id}
