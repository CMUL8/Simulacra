from __future__ import annotations

import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi import HTTPException

from apps.api import cmul8_routes, main as api_main
from simulacra.demo.identity import AuthContext, User
from simulacra.operation_graph import load_operation_graph


def _context() -> AuthContext:
	return AuthContext(
		user=User(id="user_owner", email="owner@example.test", name="Owner", password_hash="unused"),
		tenant_id="tenant_api", role="owner", auth_via="test",
	)


def _member_context() -> AuthContext:
	return AuthContext(
		user=User(id="user_member", email="member@example.test", name="Member", password_hash="unused"),
		tenant_id="tenant_api", role="member", auth_via="test",
	)


def _admin_context() -> AuthContext:
	return AuthContext(
		user=User(id="user_admin", email="admin@example.test", name="Admin", password_hash="unused"),
		tenant_id="tenant_api", role="admin", auth_via="test",
	)


def _room_context(user_id: str) -> AuthContext:
	return AuthContext(
		user=User(id=user_id, email=f"{user_id}@example.test", name=user_id, password_hash="unused"),
		tenant_id="tenant_api", role="member", auth_via="test",
	)


def _prepare(monkeypatch, tmp_path: Path) -> Path:
	project_root = tmp_path / "project_api"
	project_root.mkdir()
	monkeypatch.setattr(cmul8_routes, "_collaboration_root", tmp_path / "control")
	monkeypatch.setattr(api_main, "_collaboration_root", tmp_path / "control")
	monkeypatch.setattr(cmul8_routes, "_telemetry_root", tmp_path / "telemetry")
	monkeypatch.setattr(cmul8_routes, "_runtime_root", tmp_path / "runtime")
	monkeypatch.setattr(cmul8_routes, "project_dir", lambda _project_id: project_root)
	monkeypatch.setattr(
		cmul8_routes, "load_state",
		lambda _project_id: SimpleNamespace(
			app_config=SimpleNamespace(title="Support operations"),
			goal="Coordinate case resolution",
			prompt="",
		),
	)
	monkeypatch.setattr(cmul8_routes, "audit_request", lambda *args, **kwargs: None)
	return project_root


def test_router_is_mounted_with_tenant_scoped_contracts():
	app = FastAPI()
	app.include_router(cmul8_routes.router)
	paths = {route.path for route in cmul8_routes.router.routes if hasattr(route, "path")}
	assert "/projects/{project_id}/cmul8/room" in paths
	assert "/projects/{project_id}/cmul8/tasks" in paths
	assert "/projects/{project_id}/cmul8/comments" in paths
	assert "/projects/{project_id}/cmul8/operation-graph/revisions" in paths


def test_room_task_and_graph_are_durable_not_synthesized(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	created = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, ctx)
	assert created["room"]["tenant_id"] == "tenant_api"
	assert created["room"]["members"][0]["actor_id"] == "user_owner"

	task = cmul8_routes.create_task(
		"project_api",
		cmul8_routes.TaskCreateBody(
			title="Review case", objective="Independently review the case",
			acceptance_criteria=["Decision recorded"], owner_id="user_owner",
		),
		request, ctx,
	)
	assert task["owner_id"] == "user_owner"
	loaded = cmul8_routes.get_room("project_api", ctx)
	assert [item["id"] for item in loaded["tasks"]] == [task["id"]]
	proposed = cmul8_routes.create_task(
		"project_api", cmul8_routes.TaskCreateBody(
			title="Claim me", objective="Atomically assign work", acceptance_criteria=["Owner recorded"],
		), request, ctx,
	)
	claimed = cmul8_routes.claim_task("project_api", proposed["id"], proposed["revision"], request, ctx)
	assert claimed["owner_id"] == "user_owner" and claimed["state"] == "ready"

	graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	graph["metadata"]["tenant_id"] = "tenant_api"
	graph["metadata"]["project_id"] = "project_api"
	revision = cmul8_routes.create_graph_revision(
		"project_api", cmul8_routes.GraphRevisionBody(graph=graph), request, ctx,
	)
	approval = cmul8_routes.approve_graph_revision("project_api", revision["revision_hash"], request, ctx)
	assert approval["actor_id"] == "user_owner"
	assert cmul8_routes.get_room("project_api", ctx)["operation_graph"]["revision_hash"] == revision["revision_hash"]


def test_room_payload_does_not_invent_deployment_health(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	payload = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, ctx)
	assert "deployments" not in payload
	assert payload["permissions"]["review_graph"] is True
	cmul8_routes.heartbeat_presence("project_api", cmul8_routes.PresenceBody(), ctx)
	assert cmul8_routes.get_room("project_api", ctx)["presence"][0]["actor_id"] == "user_owner"


def test_member_cannot_bootstrap_an_ownerless_room(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, _member_context())
	assert denied.value.status_code == 403
	created = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, _context())
	assert created["room"]["members"][0]["role"] == "owner"


def test_tenant_admin_bootstrap_is_seeded_as_the_initial_room_owner(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	created = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, _admin_context())

	assert [(member["actor_id"], member["role"]) for member in created["room"]["members"]] == [("user_admin", "owner")]


def test_project_room_roles_control_mutations_reviews_and_durable_review_roles(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	owner, request = _context(), SimpleNamespace(url=SimpleNamespace(path="/test"))
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	for member_id, role in (("viewer", "viewer"), ("reviewer", "reviewer"), ("approver", "approver")):
		room = cmul8_routes.add_room_member(
			"project_api", cmul8_routes.RoomMemberBody(member_id=member_id, role=role, expected_revision=room["revision"]), request, owner,
		)

	with pytest.raises(HTTPException) as denied:
		cmul8_routes.create_task(
			"project_api", cmul8_routes.TaskCreateBody(title="Viewer task", objective="must fail", acceptance_criteria=["no write"]),
			request, _room_context("viewer"),
		)
	assert denied.value.status_code == 403
	unowned = cmul8_routes.create_task(
		"project_api", cmul8_routes.TaskCreateBody(title="Unowned", objective="claim control", acceptance_criteria=["owner"]),
		request, owner,
	)
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.claim_task("project_api", unowned["id"], unowned["revision"], request, _room_context("viewer"))
	assert denied.value.status_code == 403
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.transition_task(
			"project_api", unowned["id"],
			cmul8_routes.TaskTransitionBody(state="ready", expected_revision=unowned["revision"]), request, _room_context("viewer"),
		)
	assert denied.value.status_code == 403
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.create_comment(
			"project_api", cmul8_routes.CommentCreateBody(body="viewer write"), request, _room_context("viewer"),
		)
	assert denied.value.status_code == 403

	def task_in_review(title: str) -> dict:
		task = cmul8_routes.create_task(
			"project_api", cmul8_routes.TaskCreateBody(
				title=title, objective="Review durable room authority", acceptance_criteria=["Decision"], owner_id="user_owner",
			), request, owner,
		)
		task = cmul8_routes.transition_task(
			"project_api", task["id"], cmul8_routes.TaskTransitionBody(state="working", expected_revision=task["revision"]), request, owner,
		)
		return cmul8_routes.transition_task(
			"project_api", task["id"], cmul8_routes.TaskTransitionBody(state="in_review", expected_revision=task["revision"]), request, owner,
		)

	for member_id, expected_role in (("reviewer", "reviewer"), ("approver", "approver")):
		task = task_in_review(f"{expected_role} review")
		result = cmul8_routes.review_task(
			"project_api", task["id"],
			cmul8_routes.TaskReviewBody(decision="approve", expected_revision=task["revision"]),
			request, _room_context(member_id),
		)
		assert result["review"]["reviewer_role"] == expected_role
		assert result["task"]["state"] == "done"
		event = [event for event in cmul8_routes.get_room("project_api", owner)["events"] if event["action"] == "task.reviewed"][-1]
		assert event["payload"]["reviewer_role"] == expected_role

	task = task_in_review("nonmember review")
	with pytest.raises(HTTPException) as denied:
		cmul8_routes.review_task(
			"project_api", task["id"],
			cmul8_routes.TaskReviewBody(decision="approve", expected_revision=task["revision"]),
			request, _room_context("nonmember"),
		)
	assert denied.value.status_code == 403


def test_operation_graph_mutations_require_durable_room_owner_or_admin(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	owner, request = _context(), SimpleNamespace(url=SimpleNamespace(path="/test"))
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	for member_id, role in (("room_admin", "admin"), ("viewer", "viewer")):
		room = cmul8_routes.add_room_member(
			"project_api", cmul8_routes.RoomMemberBody(member_id=member_id, role=role, expected_revision=room["revision"]), request, owner,
		)
	graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	graph["metadata"]["tenant_id"] = "tenant_api"
	graph["metadata"]["project_id"] = "project_api"

	for actor_id in ("viewer", "nonmember"):
		with pytest.raises(HTTPException) as denied:
			cmul8_routes.create_graph_revision(
				"project_api", cmul8_routes.GraphRevisionBody(graph=graph), request, _room_context(actor_id),
			)
		assert denied.value.status_code == 403

	owner_revision = cmul8_routes.create_graph_revision(
		"project_api", cmul8_routes.GraphRevisionBody(graph=graph), request, _room_context("user_owner"),
	)
	for actor_id in ("viewer", "nonmember"):
		with pytest.raises(HTTPException) as denied:
			cmul8_routes.approve_graph_revision("project_api", owner_revision["revision_hash"], request, _room_context(actor_id))
		assert denied.value.status_code == 403
	owner_approval = cmul8_routes.approve_graph_revision(
		"project_api", owner_revision["revision_hash"], request, _room_context("user_owner"),
	)
	assert owner_approval["actor_id"] == "user_owner"

	admin_graph = copy.deepcopy(graph)
	admin_graph["metadata"]["version"] = 1
	admin_revision = cmul8_routes.create_graph_revision(
		"project_api",
		cmul8_routes.GraphRevisionBody(graph=admin_graph, expected_revision_hash=owner_revision["revision_hash"]),
		request, _room_context("room_admin"),
	)
	admin_approval = cmul8_routes.approve_graph_revision(
		"project_api", admin_revision["revision_hash"], request, _room_context("room_admin"),
	)
	assert admin_approval["actor_id"] == "room_admin"


def test_legacy_graph_approval_and_build_require_the_same_durable_room_role(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	owner, request = _context(), SimpleNamespace(url=SimpleNamespace(path="/test"))
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	for member_id, role in (("room_admin", "admin"), ("viewer", "viewer")):
		room = cmul8_routes.add_room_member(
			"project_api", cmul8_routes.RoomMemberBody(member_id=member_id, role=role, expected_revision=room["revision"]), request, owner,
		)

	# Owners and room admins retain the legacy product flow with only basic
	# tenant project access; viewers and nonmembers cannot reach its approval
	# or build side effects.
	api_main._require_room_graph_authority("project_api", _room_context("user_owner"))
	api_main._require_room_graph_authority("project_api", _room_context("room_admin"))
	for ctx in (_room_context("viewer"), _room_context("nonmember")):
		with pytest.raises(HTTPException) as denied_approve:
			api_main.post_approve("project_api", request, ctx)
		assert denied_approve.value.status_code == 403
		with pytest.raises(HTTPException) as denied_build:
			api_main.post_build("project_api", ctx)
		assert denied_build.value.status_code == 403


def test_project_bootstrap_creates_an_owner_room_before_initial_plan(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/projects"))
	state = SimpleNamespace(id="project_bootstrap", tenant_id="tenant_api")
	created: list[str] = []
	monkeypatch.setattr(api_main, "create_project", lambda *_args, **_kwargs: created.append("project") or state)
	monkeypatch.setattr(api_main, "audit_request", lambda *_args, **_kwargs: None)
	monkeypatch.setattr(api_main, "project_snapshot", lambda project_id: {"id": project_id})

	def initial_plan(initial_state, *, actor_id):
		room = cmul8_routes.JsonCollaborationRepository(tmp_path / "control").get_room(
			initial_state.tenant_id, initial_state.id,
		)
		assert [(member.actor_id, member.role) for member in room.members] == [(actor_id, "owner")]
		created.append("plan")
		return initial_state

	monkeypatch.setattr(api_main, "init_plan", initial_plan)
	body = api_main.CreateProjectBody(prompt="Create a controlled project")
	with pytest.raises(HTTPException) as denied:
		api_main.post_project(body, request, _member_context())
	assert denied.value.status_code == 403
	assert created == []

	result = api_main.post_project(body, request, _context())
	assert result["id"] == "project_bootstrap"
	assert created == ["project", "plan"]


def test_project_bootstrap_hides_storage_paths(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	request = SimpleNamespace(url=SimpleNamespace(path="/projects"))
	secret_path = "/app/runs/proj_secret"
	monkeypatch.setattr(
		api_main,
		"create_project",
		lambda *_args, **_kwargs: (_ for _ in ()).throw(
			PermissionError(13, "Permission denied", secret_path),
		),
	)
	body = api_main.CreateProjectBody(prompt="Create a project")
	with pytest.raises(HTTPException) as unavailable:
		api_main.post_project(body, request, _context())
	assert unavailable.value.status_code == 503
	assert unavailable.value.detail == "Project storage is temporarily unavailable. Please try again."
	assert secret_path not in unavailable.value.detail


def test_main_mutation_endpoints_carry_room_authority_and_preserve_read_only_chat(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	owner, request = _context(), SimpleNamespace(url=SimpleNamespace(path="/test"))
	room = cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, owner)["room"]
	for member_id, role in (("room_admin", "admin"), ("viewer", "viewer")):
		room = cmul8_routes.add_room_member(
			"project_api", cmul8_routes.RoomMemberBody(member_id=member_id, role=role, expected_revision=room["revision"]), request, owner,
		)

	marker = tmp_path / "project_api" / "app-source.txt"
	marker.write_text("unchanged")
	chat_actors: list[str | None] = []
	monkeypatch.setattr(
		api_main, "start_follow_up",
		lambda _project_id, _message, *, chat_id=None, actor_id=None: chat_actors.append(actor_id) or {"status": "running"},
	)
	for ctx in (_room_context("user_owner"), _room_context("room_admin"), _room_context("viewer"), _room_context("nonmember")):
		assert api_main.post_plan("project_api", api_main.ChatBody(message="Discuss the brief"), ctx)["status"] == "running"
		assert api_main.post_chat("project_api", api_main.ChatBody(message="Discuss the brief"), ctx)["status"] == "running"
	assert chat_actors == ["user_owner", "user_owner", "room_admin", "room_admin", "viewer", "viewer", "nonmember", "nonmember"]

	for ctx in (_room_context("viewer"), _room_context("nonmember")):
		with pytest.raises(HTTPException) as denied_approve:
			api_main.post_approve("project_api", request, ctx)
		assert denied_approve.value.status_code == 403
		with pytest.raises(HTTPException) as denied_build:
			api_main.post_build("project_api", ctx)
		assert denied_build.value.status_code == 403
	assert marker.read_text() == "unchanged"

	state = SimpleNamespace(tenant_id="tenant_api")
	class Store:
		def __init__(self, *_args, **_kwargs):
			pass
		def current_revision(self):
			return SimpleNamespace(revision_hash="approved-revision")
		def require_approved_revision(self, _revision_hash):
			return object()
	monkeypatch.setattr(api_main, "load_state", lambda _project_id: state)
	monkeypatch.setattr(api_main, "OperationGraphStore", Store)
	monkeypatch.setattr(api_main, "approved_graph_path", lambda _state: tmp_path / "approved.json")
	monkeypatch.setattr(api_main, "audit_request", lambda *_args, **_kwargs: None)
	approved_by: list[str | None] = []
	built_by: list[str | None] = []
	monkeypatch.setattr(
		api_main, "start_approve_build",
		lambda _project_id, *, actor_id=None: approved_by.append(actor_id) or {"job_id": "job_build", "status": "running"},
	)
	monkeypatch.setattr(api_main, "build_project", lambda _state, *, actor_id=None: built_by.append(actor_id))
	monkeypatch.setattr(api_main, "project_snapshot", lambda _project_id: {"id": "project_api"})
	for ctx in (_room_context("user_owner"), _room_context("room_admin")):
		assert api_main.post_approve("project_api", request, ctx)["job_id"] == "job_build"
		assert api_main.post_build("project_api", ctx)["id"] == "project_api"
	assert approved_by == ["user_owner", "room_admin"]
	assert built_by == ["user_owner", "room_admin"]


def test_observability_is_project_and_tenant_scoped(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	body = cmul8_routes.TelemetryEventBody(
		id="evt_api_1", entity_kind="workflow", entity_id="wf_case", entity_name="Case resolution",
		signal="workflow.completed", status="succeeded", started_at="2026-08-23T10:00:00+00:00",
		duration_ms=125, trace_id="trace_api_1", workflow_id="wf_case",
	)
	cmul8_routes.ingest_telemetry("project_api", body, request, ctx)
	other = body.model_copy(update={"id": "evt_api_2"})
	cmul8_routes.ingest_telemetry("project_other", other, request, ctx)
	payload = cmul8_routes.get_observability("project_api", ctx)
	assert payload["overview"]["runs"] == 1
	assert payload["inventories"]["workflow"][0]["id"] == "wf_case"
	detail = cmul8_routes.get_observability_detail("project_api", "workflow", "wf_case", ctx)
	assert detail["recent_events"][0]["trace_id"] == "trace_api_1"

	with pytest.raises(Exception, match="raw credential-like field"):
		cmul8_routes.ingest_telemetry(
			"project_api", body.model_copy(update={"id": "evt_secret", "attributes": {"auth": {"token": "raw"}}}),
			request, ctx,
		)


def test_runtime_job_http_read_rejects_a_foreign_graph_revision(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	cmul8_routes.create_room("project_api", cmul8_routes.RoomCreateBody(), request, ctx)
	graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
	graph["metadata"]["tenant_id"] = "tenant_api"
	graph["metadata"]["project_id"] = "project_api"
	first = cmul8_routes.create_graph_revision("project_api", cmul8_routes.GraphRevisionBody(graph=graph), request, ctx)
	cmul8_routes.approve_graph_revision("project_api", first["revision_hash"], request, ctx)
	job = cmul8_routes.enqueue_runtime_job(
		"project_api",
		cmul8_routes.RuntimeJobBody(revision_hash=first["revision_hash"], kind="workflow.transition", payload={
			"instance_id": "workflow_foreign", "target_state": "triaged", "expected_state": "new", "expected_revision": 0,
		}),
		request, ctx,
	)
	second_graph = copy.deepcopy(graph)
	second_graph["metadata"]["version"] = 1
	second = cmul8_routes.create_graph_revision(
		"project_api", cmul8_routes.GraphRevisionBody(graph=second_graph, expected_revision_hash=first["revision_hash"]), request, ctx,
	)
	cmul8_routes.approve_graph_revision("project_api", second["revision_hash"], request, ctx)

	with pytest.raises(HTTPException) as denied:
		cmul8_routes.get_runtime_job("project_api", job["id"], second["revision_hash"], ctx)
	assert denied.value.status_code == 403
