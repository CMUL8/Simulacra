from __future__ import annotations

import json
import pytest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI

from apps.api import cmul8_routes
from simulacra.demo.identity import AuthContext, User


def _context() -> AuthContext:
	return AuthContext(
		user=User(id="user_owner", email="owner@example.test", name="Owner", password_hash="unused"),
		tenant_id="tenant_api", role="owner", auth_via="test",
	)


def _prepare(monkeypatch, tmp_path: Path) -> Path:
	project_root = tmp_path / "project_api"
	project_root.mkdir()
	monkeypatch.setattr(cmul8_routes, "_collaboration_root", tmp_path / "control")
	monkeypatch.setattr(cmul8_routes, "_telemetry_root", tmp_path / "telemetry")
	monkeypatch.setattr(cmul8_routes, "project_dir", lambda _project_id: project_root)
	monkeypatch.setattr(
		cmul8_routes, "load_state",
		lambda _project_id: SimpleNamespace(app_config=SimpleNamespace(title="Vendor onboarding"), goal="Review vendors", prompt=""),
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
			title="Review risk", objective="Independently review the vendor",
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

	graph = json.loads((Path(__file__).parents[1] / "examples/vendor-onboarding/operation-graph.json").read_text())
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


def test_observability_is_project_and_tenant_scoped(monkeypatch, tmp_path):
	_prepare(monkeypatch, tmp_path)
	ctx = _context()
	request = SimpleNamespace(url=SimpleNamespace(path="/test"))
	body = cmul8_routes.TelemetryEventBody(
		id="evt_api_1", entity_kind="workflow", entity_id="wf_vendor", entity_name="Vendor review",
		signal="workflow.completed", status="succeeded", started_at="2026-08-23T10:00:00+00:00",
		duration_ms=125, trace_id="trace_api_1", workflow_id="wf_vendor",
	)
	cmul8_routes.ingest_telemetry("project_api", body, request, ctx)
	other = body.model_copy(update={"id": "evt_api_2"})
	cmul8_routes.ingest_telemetry("project_other", other, request, ctx)
	payload = cmul8_routes.get_observability("project_api", ctx)
	assert payload["overview"]["runs"] == 1
	assert payload["inventories"]["workflow"][0]["id"] == "wf_vendor"
	detail = cmul8_routes.get_observability_detail("project_api", "workflow", "wf_vendor", ctx)
	assert detail["recent_events"][0]["trace_id"] == "trace_api_1"

	with pytest.raises(Exception, match="raw credential-like field"):
		cmul8_routes.ingest_telemetry(
			"project_api", body.model_copy(update={"id": "evt_secret", "attributes": {"auth": {"token": "raw"}}}),
			request, ctx,
		)
