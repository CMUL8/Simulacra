"""Mission route contract tests using the same durable Project Room authority."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import hashlib
import threading
from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.api import mission_routes
from apps.api import main as api_main
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.collaboration.models import Member
from simulacra.demo import mutation_authorization
from simulacra.demo.identity import AuthContext, User
from simulacra.operation_graph import OperationGraphStore, load_operation_graph
from simulacra.harnesses import AgentRunResult, TerminalStatus
from simulacra.missions import MissionConflictError, MissionWorker
from deploy.environment import validate_environment


def test_preview_origin_environment_fails_closed_without_distinct_hostname():
    base = {
        "CMUL8_DEPLOYMENT_MODE": "private_cloud", "CMUL8_TENANT_ID": "tenant_api", "CMUL8_ENVIRONMENT": "production",
        "CMUL8_POSTGRES_URL": "postgresql://db.example.test/runtime", "CMUL8_REDIS_URL": "rediss://queue.example.test/0",
        "CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1": "true", "CONTROL_ORIGIN": "https://app.example.test",
        "PREVIEW_ORIGIN": "https://app.example.test", "PREVIEW_REGISTRABLE_DOMAIN": "example.test",
        "CMUL8_PREVIEW_EXCHANGE_SECRET": "test-secret",
    }
    result = validate_environment(base)
    assert not result.ok
    assert "same-site distinct hostname" in " ".join(result.errors)


def test_preview_origin_environment_requires_https_on_both_hosts():
    environment = {
        "CMUL8_DEPLOYMENT_MODE": "private_cloud", "CMUL8_TENANT_ID": "tenant_api", "CMUL8_ENVIRONMENT": "production",
        "CMUL8_POSTGRES_URL": "postgresql://db.example.test/runtime", "CMUL8_REDIS_URL": "rediss://queue.example.test/0",
        "CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1": "true", "CONTROL_ORIGIN": "https://app.example.test",
        "PREVIEW_ORIGIN": "http://preview.example.test", "PREVIEW_REGISTRABLE_DOMAIN": "example.test",
        "CMUL8_PREVIEW_EXCHANGE_SECRET": "test-secret",
    }
    result = validate_environment(environment)
    assert not result.ok
    assert "HTTPS" in " ".join(result.errors)


def test_legacy_project_files_delegates_to_authorized_inventory(monkeypatch):
    context = _context("viewer", "viewer")
    observed = {}
    monkeypatch.setattr(
        api_main,
        "authorized_file_inventory",
        lambda project_id, *, kind, ctx: observed.update(project_id=project_id, kind=kind, ctx=ctx) or {"items": [], "files": []},
    )
    assert api_main.project_files("project_api", context) == {"items": [], "files": []}
    assert observed == {"project_id": "project_api", "kind": "all", "ctx": context}


def test_preference_routes_are_reachable_and_legacy_sources_use_authorized_inventory(monkeypatch):
    route_paths = {getattr(route, "path", None) for route in api_main.app.routes}
    for included in api_main.app.routes:
        original = getattr(included, "original_router", None)
        if original is not None:
            route_paths.update(getattr(route, "path", None) for route in original.routes)
    assert "/workspace/preferences" in route_paths
    assert "/workspace/preferences/work-view" in route_paths
    assert "/workspace/preferences/notifications" in route_paths

    context = _context("viewer", "viewer")
    observed = {}
    monkeypatch.setattr(
        api_main,
        "authorized_file_inventory",
        lambda project_id, *, kind, ctx: observed.update(project_id=project_id, kind=kind, ctx=ctx)
        or {"items": [], "files": [{"name": "source.csv", "size": 12, "type": "text/csv", "status": "ok", "detail": "", "row_count": 1}]},
    )
    monkeypatch.setattr(
        api_main,
        "load_state",
        lambda _project_id: SimpleNamespace(plan_preview={}, row_count=1),
    )
    result = api_main.get_sources("project_api", context)
    assert result["files"] == [{"name": "source.csv", "size": 12, "type": "text/csv", "status": "ok", "detail": "", "row_count": 1}]
    assert observed == {"project_id": "project_api", "kind": "source", "ctx": context}


def test_legacy_sources_rechecks_membership_after_detail_load(monkeypatch):
    context = _context("viewer", "viewer")
    calls = 0

    def authorized(project_id, *, kind, ctx):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise HTTPException(404, {"code": "file_unavailable", "message": "Mission files are unavailable."})
        return {"items": [], "files": []}

    monkeypatch.setattr(api_main, "authorized_file_inventory", authorized)
    monkeypatch.setattr(api_main, "load_state", lambda _project_id: SimpleNamespace(plan_preview={}, row_count=1))

    with pytest.raises(HTTPException) as denied:
        api_main.get_sources("project_api", context)
    assert denied.value.status_code == 404
    assert calls == 2


def _context(user_id: str, role: str) -> AuthContext:
    return AuthContext(
        user=User(id=user_id, email=f"{user_id}@example.test", name=user_id, password_hash="unused"),
        tenant_id="tenant_api", role=role, auth_via="test",
    )


def test_pending_room_admin_cannot_build_or_authorize_legacy_mutations_until_acceptance_complete(monkeypatch, tmp_path):
    root = tmp_path / "rooms"
    repository = JsonCollaborationRepository(root)
    rooms = CollaborationService(repository)
    rooms.create_room(tenant_id="tenant_api", project_id="project_api", creator_id="legacy_owner")
    room = repository.get_room("tenant_api", "project_api")
    repository.save_room(replace(
        room,
        members=[*room.members, Member(
            actor_id="pending_admin", role="admin", transaction_id="txn_pending_legacy",
            visibility_state="pending_commit",
        )],
        revision=room.revision + 1,
    ), room.revision)
    monkeypatch.setattr(api_main, "_collaboration_root", root)
    monkeypatch.setattr(mutation_authorization, "_collaboration_root", root)
    pending = _context("pending_admin", "admin")

    with pytest.raises(HTTPException) as denied:
        api_main.post_build("project_api", pending)
    assert denied.value.status_code == 403
    with pytest.raises(PermissionError):
        mutation_authorization.require_room_mutation_authority(
            "project_api", tenant_id="tenant_api", actor_id="pending_admin",
        )
    with pytest.raises(PermissionError):
        with mutation_authorization.room_mutation_commit(
            "project_api", tenant_id="tenant_api", actor_id="pending_admin",
        ):
            pytest.fail("a pending invitee reached a consequential mutation")

    journal = root / ".invitation-acceptance" / "tenant_api" / "project_api" / "txn_pending_legacy.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({
        "state": "COMPLETE", "transaction_id": "txn_pending_legacy",
        "tenant_id": "tenant_api", "project_id": "project_api",
    }))
    room = repository.get_room("tenant_api", "project_api")
    repository.save_room(replace(
        room,
        members=[replace(member, visibility_state="committed") if member.transaction_id == "txn_pending_legacy" else member for member in room.members],
        revision=room.revision + 1,
    ), room.revision)
    mutation_authorization.require_room_mutation_authority(
        "project_api", tenant_id="tenant_api", actor_id="pending_admin",
    )
    with mutation_authorization.room_mutation_commit(
        "project_api", tenant_id="tenant_api", actor_id="pending_admin",
    ):
        pass
    # Untagged committed membership remains compatible.
    mutation_authorization.require_room_mutation_authority(
        "project_api", tenant_id="tenant_api", actor_id="legacy_owner",
    )

    monkeypatch.setattr(api_main, "load_state", lambda _project_id: SimpleNamespace())
    monkeypatch.setattr(api_main, "approved_graph_path", lambda _state: tmp_path / "approved.json")
    monkeypatch.setattr(api_main, "build_project", lambda _state, actor_id: None)
    monkeypatch.setattr(api_main, "project_snapshot", lambda _project_id: {"id": "project_api"})
    assert api_main.post_build("project_api", pending) == {"id": "project_api"}


def test_public_mission_views_remove_execution_data_without_mutating_business_output():
    run = mission_routes._public_run({
        "id": "run_1", "mission_id": "mission_1", "status": "failed", "revision": 3,
        "trigger_snapshot": {"type": "manual", "note": "CoDeX waits for the OPERATION GRAPH."},
        "lease_owner": "worker", "invocation_id": "invoke_1", "execution_binding": {"model": "private"},
        "execution_profile": {"runtime": "Codex"}, "session_ids": {"agent_1": "session_1"},
        "usage": {"steps": 9}, "runtime_config": {"mode": "private"}, "model_route": "internal",
        "error": {"code": "provider_failed", "message": "CoDeX provider failed after the OPERATION GRAPH changed."},
    })
    event = mission_routes._public_event({
        "id": "event_1", "run_id": "run_1", "mission_id": "mission_1", "type": "agent_completed", "timestamp": "now",
        "correlation_id": "invoke_1",
        "payload": {
            "session_id": "session_1", "model_id": "private", "usage": {"steps": 9},
            "execution_profile": {"runtime": "Codex"}, "runtime_config": {"mode": "private"},
            "model_route": "internal", "codex_profile": "managed", "invocation_id": "invoke_1", "provider_name": "private",
            "trace_id": "trace_1", "environment_id": "environment_1", "events": [{"secret": "hidden"}],
            "response": "CoDeX completed the OPERATION GRAPH review.",
            "structured_output": {"model": "forecast-v2", "usage": "daily", "note": "CoDeX checked the OPERATION GRAPH."},
        },
    })
    serialized_run = str(run).lower()
    for forbidden in ("lease_owner", "invocation_id", "execution_binding", "execution_profile", "session_ids", "usage", "runtime_config", "model_route", "codex"):
        assert forbidden not in serialized_run
    assert run["error"] == {"code": "agent_failed", "message": "An agent could not continue. Review the Mission plan or try again."}
    assert run["trigger_snapshot"]["note"] == "agent waits for the Mission plan."
    assert "correlation_id" not in event
    assert "response" not in event["payload"]
    assert event["payload"]["structured_output"] == {
        "model": "forecast-v2", "usage": "daily", "note": "agent checked the Mission plan.",
    }
    for forbidden in ("response", "session_id", "model_id", "execution_profile", "runtime_config", "model_route", "codex_profile", "invocation_id", "provider_name", "trace_id", "environment_id", "events"):
        assert forbidden not in event["payload"]
    deliverable = mission_routes._public_deliverable({
        "id": "deliverable_1", "name": "Brief", "producer_id": "agent_1", "version": 2,
        "content_hash": "a" * 64, "source_ref": "mission/agent", "artifact_ref": "outputs/brief.md",
        "state": "awaiting_verification", "revision": 4,
        "validation_evidence": [{"provider": "private", "runtime": "managed", "model": "hidden", "session_id": "s_1", "usage": {"steps": 2}}],
    })
    assert "validation_evidence" not in deliverable
    assert deliverable["state"] == "awaiting_verification"
    assert not {"content_hash", "verified_hash", "source_ref", "artifact_ref", "revision"} & set(deliverable)
    with pytest.raises(ValidationError):
        mission_routes.DeliverableBody(
            type="report", name="Brief", source_ref="mission/agent", artifact_ref="outputs/brief.md",
            validation_evidence=[{"provider": "private", "runtime": "managed", "model": "hidden", "session_id": "s_1", "usage": {"steps": 2}}],
        )


@pytest.mark.parametrize(
    ("stored_code", "public_code", "public_message"),
    (
        ("checkpoint_required", "checkpoint_required", "A human decision is needed before this Mission can continue."),
        ("recovery_retry", "recovery_retry", "This Mission needs another attempt before it can continue."),
        ("crew_required", "crew_required", "Add an assigned agent before this Mission can continue."),
        ("crew_changed", "crew_changed", "The Mission team changed. Review the work before continuing."),
        ("checkpoint_rejected", "checkpoint_rejected", "A human decision sent this Mission back for revision."),
        ("agent_failed", "agent_failed", "An agent could not continue. Review the Mission plan or try again."),
        ("private_database_failure", "agent_failed", "An agent could not continue. Review the Mission plan or try again."),
    ),
)
def test_public_error_codes_use_fixed_messages_and_discard_stored_details(
    stored_code: str, public_code: str, public_message: str,
):
    private_details = (
        "/private/tenant/acme/state.json",
        r"C:\tenant\state.json",
        "SELECT password FROM tenant_records WHERE id = 'acme'",
        "https://internal.example/run Traceback (most recent call last)",
        "unrecognized exception detail from a persisted Mission record",
    )
    for detail in private_details:
        public = mission_routes._public_error({"code": stored_code, "message": detail})
        assert public == {"code": public_code, "message": public_message}
        assert detail not in json.dumps(public)


def test_mission_routes_owner_member_and_verification_contract(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    monkeypatch.setattr(mission_routes, "audit_request", lambda *args, **kwargs: None)
    workspace = tmp_path / "project"
    workspace.mkdir()
    artifact_path = workspace / "release.md"
    artifact_path.write_bytes(b"v1")
    monkeypatch.setattr(mission_routes, "project_dir", lambda _project_id: workspace)
    owner, member, reviewer = _context("owner", "owner"), _context("member", "member"), _context("reviewer", "member")
    room = CollaborationService(JsonCollaborationRepository(mission_routes._rooms)).create_room(
        tenant_id="tenant_api", project_id="project_api", creator_id="owner",
    )
    repo = JsonCollaborationRepository(mission_routes._rooms)
    service = CollaborationService(repo)
    room = service.add_member(tenant_id="tenant_api", project_id="project_api", actor_id="owner", member_id="member", role="member", expected_revision=room.revision)
    service.add_member(tenant_id="tenant_api", project_id="project_api", actor_id="owner", member_id="reviewer", role="reviewer", expected_revision=room.revision)
    request = SimpleNamespace(url=SimpleNamespace(path="/test"))

    mission = mission_routes.bootstrap("project_api", mission_routes.BootstrapBody(title="Launch", verifier_ids=["reviewer"]), request, owner)
    assert mission["owner_id"] == "owner"
    overview = mission_routes.overview("project_api", member)
    assert overview["mission"]["id"] == mission["id"]
    assert overview["mission"]["objective"] == "Launch"
    assert "human verification" in overview["mission"]["definition_of_done"]
    assert overview["readiness"] == {
        "graph": {"status": "missing", "revision": None},
        "crew_count": 0,
    }
    assert [item["slug"] for item in overview["crew_recommendations"]] == ["source-review", "product-builder"]
    assert all("codex" not in str(item).lower() for item in overview["crew_recommendations"])
    with pytest.raises(HTTPException) as denied:
        mission_routes.add_agent("project_api", mission_routes.AgentBody(name="A", role="Engineer", mandate="Work"), request, member)
    assert denied.value.status_code == 403
    first = overview["crew_recommendations"][0]
    added = mission_routes.add_agent("project_api", mission_routes.AgentBody(
        name=first["name"], role=first["role"], mandate=first["mandate"],
        scope=first["scope"], autonomy=first["autonomy"],
    ), request, owner)
    assert "runtime" not in added and "provider" not in added and "model" not in added
    remaining = mission_routes.overview("project_api", owner)["crew_recommendations"]
    assert [item["slug"] for item in remaining] == ["product-builder"]
    second = remaining[0]
    mission_routes.add_agent("project_api", mission_routes.AgentBody(
        name="Custom product teammate", role=second["role"], mandate="Own the product outcome.",
        scope=second["scope"], autonomy=second["autonomy"],
    ), request, owner)
    assert mission_routes.overview("project_api", owner)["crew_recommendations"] == []
    with pytest.raises(ValidationError):
        mission_routes.AgentBody.model_validate({"name": "A", "role": "Engineer", "mandate": "Work", "provider": "x"})
    with pytest.raises(ValidationError):
        mission_routes.AgentBody.model_validate({"name": "A", "role": "Engineer", "mandate": "Work", "tools": ["code.write"]})
    with pytest.raises(ValidationError):
        mission_routes.MissionPatch.model_validate({"expected_revision": 1, "approved_contract_revision": "spoof"})
    with pytest.raises(ValidationError):
        mission_routes.RunBody.model_validate({"trigger_note": "work", "agent_ids": [f"agent_{index}" for index in range(33)]})
    for budget in ({"max_steps": True}, {"max_steps": 101}, {"wall_timeout_seconds": "30"}, {"unknown": 1}):
        with pytest.raises(ValidationError):
            mission_routes.BootstrapBody.model_validate({"title": "Launch", "budget": budget})
        with pytest.raises(ValidationError):
            mission_routes.MissionPatch.model_validate({"expected_revision": 1, "budget": budget})
    with pytest.raises(ValidationError):
        mission_routes.DeliverableBody.model_validate({"artifact_ref": "release.md"})

    run = mission_routes.create_run("project_api", mission_routes.RunBody(), request, owner)
    assert "execution_profile" not in run
    assert "runtime" not in overview
    public_overview = mission_routes.overview("project_api", owner)
    assert "runtime" not in public_overview
    assert all("execution_profile" not in item for item in public_overview["runs"])
    public_text = str({"run": run, "overview": public_overview}).lower()
    for forbidden in ("codex", "operation graph", "lease_owner", "invocation_id", "execution_binding", "execution_profile", "session_ids", "usage", "model_id", "session_id", "runtime_config", "model_route"):
        assert forbidden not in public_text
    mission_routes._service().repository.mutate("tenant_api", "project_api", lambda records: records["events"].update({"event_public": {
        "id": "event_public", "run_id": run["id"], "mission_id": mission["id"], "type": "agent_completed", "timestamp": "now",
        "correlation_id": "invoke_1", "payload": {
            "agent_id": added["id"], "response": "Completed", "provider_name": "private", "trace_id": "trace_1",
            "environment_id": "environment_1", "events": [{"internal": True}],
            "structured_output": {"model": "forecast-v2", "usage": "daily", "result": "ready"},
        },
    }}))
    public_event = next(item for item in mission_routes.overview("project_api", owner)["events"] if item["id"] == "event_public")
    assert public_event["payload"] == {
        "agent_id": added["id"],
        "structured_output": {"model": "forecast-v2", "usage": "daily", "result": "ready"},
    }
    with pytest.raises(HTTPException) as retired_create:
        mission_routes.create_deliverable("project_api", mission_routes.DeliverableBody(), request, owner)
    assert retired_create.value.status_code == 410
    stored_artifact = mission_routes._service().create_deliverable(
        "tenant_api", "project_api", {"type": "report", "name": "R", "source_ref": "room/r.md", "artifact_ref": "release.md"}, owner.user.id, b"v1",
    )
    mission_routes._service().repository.mutate("tenant_api", "project_api", lambda records: records["deliverables"][stored_artifact.id].update({"validation_evidence": [{"provider": "private", "runtime": "managed", "model": "hidden", "session_id": "s_1", "usage": {"steps": 2}}]}))
    public_artifact = next(item for item in mission_routes.overview("project_api", owner)["deliverables"] if item["id"] == stored_artifact.id)
    assert "validation_evidence" not in public_artifact
    assert public_artifact["state"] == stored_artifact.state
    assert not {"content_hash", "verified_hash", "source_ref", "artifact_ref", "revision"} & set(public_artifact)
    with pytest.raises(HTTPException) as self_verify:
        mission_routes.verify_deliverable("project_api", stored_artifact.id, mission_routes.VerifyBody(expected_version=stored_artifact.version), request, owner)
    assert self_verify.value.status_code == 403
    artifact_path.write_bytes(b"mutated")
    with pytest.raises(HTTPException) as changed:
        mission_routes.verify_deliverable("project_api", stored_artifact.id, mission_routes.VerifyBody(expected_version=stored_artifact.version), request, reviewer)
    assert changed.value.status_code == 409
    artifact_path.write_bytes(b"v1")
    verified = mission_routes.verify_deliverable("project_api", stored_artifact.id, mission_routes.VerifyBody(expected_version=stored_artifact.version), request, reviewer)
    assert verified["state"] == "verified"
    artifact_path.write_bytes(b"v2")
    second = mission_routes._service().create_deliverable(
        "tenant_api", "project_api", {"type": "report", "name": "R", "source_ref": "room/r.md", "artifact_ref": "release.md"}, owner.user.id, b"v2",
    )
    assert second.version == 2 and second.state != "verified"
    with pytest.raises(HTTPException) as stale:
        mission_routes.verify_deliverable("project_api", second.id, mission_routes.VerifyBody(expected_version=stored_artifact.version), request, reviewer)
    assert stale.value.status_code == 409


def test_mission_overview_reports_exact_graph_readiness(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    workspace = tmp_path / "project"; workspace.mkdir()
    monkeypatch.setattr(mission_routes, "project_dir", lambda _project_id: workspace)
    owner = _context("owner", "owner")
    CollaborationService(JsonCollaborationRepository(mission_routes._rooms)).create_room(
        tenant_id="tenant_api", project_id="project_api", creator_id="owner",
    )
    request = SimpleNamespace(url=SimpleNamespace(path="/test"))
    monkeypatch.setattr(mission_routes, "audit_request", lambda *args, **kwargs: None)
    mission_routes.bootstrap("project_api", mission_routes.BootstrapBody(title="Reconcile"), request, owner)
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"]["tenant_id"] = "tenant_api"; graph["metadata"]["project_id"] = "project_api"
    store = OperationGraphStore(workspace, tenant_id="tenant_api", project_id="project_api")
    revision = store.create_revision(graph, expected_revision_hash=None)

    pending = mission_routes.overview("project_api", owner)["readiness"]["graph"]
    assert pending == {
        "status": "pending_approval",
        "revision": revision.revision,
    }
    store.approve_revision(revision.revision_hash, actor_id="owner")
    approved = mission_routes.overview("project_api", owner)["readiness"]["graph"]
    assert approved == {**pending, "status": "approved"}


def test_code_agent_stages_until_exact_verifier_promotes(monkeypatch, tmp_path: Path):
    """Unverified code never reaches the canonical app/preview tree."""
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    monkeypatch.setattr(mission_routes, "audit_request", lambda *args, **kwargs: None)
    workspace = tmp_path / "project"; (workspace / "app").mkdir(parents=True); (workspace / "source").mkdir()
    (workspace / "app" / "index.html").write_text("verified old app", encoding="utf-8")
    (workspace / "source" / "secret.txt").write_text("source-secret", encoding="utf-8")
    monkeypatch.setattr(mission_routes, "project_dir", lambda _project_id: workspace)
    owner, reviewer = _context("owner", "owner"), _context("reviewer", "member")
    room_service = CollaborationService(JsonCollaborationRepository(mission_routes._rooms))
    room = room_service.create_room(tenant_id="tenant_api", project_id="project_api", creator_id="owner")
    room_service.add_member(tenant_id="tenant_api", project_id="project_api", actor_id="owner", member_id="reviewer", role="reviewer", expected_revision=room.revision)
    request = SimpleNamespace(url=SimpleNamespace(path="/test"))
    mission_routes.bootstrap("project_api", mission_routes.BootstrapBody(title="Launch", verifier_ids=["reviewer"]), request, owner)
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"]["tenant_id"] = "tenant_api"; graph["metadata"]["project_id"] = "project_api"
    store = OperationGraphStore(workspace, tenant_id="tenant_api", project_id="project_api")
    revision = store.create_revision(graph, expected_revision_hash=None); store.approve_revision(revision.revision_hash, actor_id="owner")
    service = mission_routes._service()
    service.add_agent("tenant_api", "project_api", {
        "name": "Code", "role": "Engineer", "mandate": "Produce candidate code", "autonomy": "execute_safely",
        "tools": ["code.write"], "data_scope": ["source"],
    })
    service.create_run("tenant_api", "project_api", {"type": "manual"}, verified_contract_revision=revision.revision_hash)
    class Writer:
        async def run(self, run_request):
            assert all(path != workspace / "app" for path in run_request.write_paths)
            staged = run_request.write_paths[0] / "index.html"
            staged.write_text("unverified source-secret", encoding="utf-8")
            return AgentRunResult("codex", "openai", "model", "session", TerminalStatus.SUCCEEDED, "ok", {}, (staged.relative_to(workspace),), (), 0, {})
    completed = MissionWorker(service, workspace, "worker", lambda _config, **_kw: Writer()).run_once("tenant_api", "project_api")
    assert completed is not None and completed.status == "succeeded"
    item = service.deliverables("tenant_api", "project_api")[0]
    assert item.state == "awaiting_verification" and "code-staging" in str(item.artifact_ref)
    assert item.validation_evidence[0]["intended_target"] == "app/index.html"
    assert (workspace / "app" / "index.html").read_text() == "verified old app"
    assert "source-secret" not in (workspace / "app" / "index.html").read_text()

    with pytest.raises(HTTPException) as stale_version:
        mission_routes.verify_deliverable("project_api", item.id, mission_routes.VerifyBody(expected_version=item.version + 1), request, reviewer)
    assert stale_version.value.status_code == 409 and (workspace / "app" / "index.html").read_text() == "verified old app"
    staged_path = workspace / str(item.artifact_ref); original = staged_path.read_text()
    staged_path.write_text("tampered", encoding="utf-8")
    with pytest.raises(HTTPException) as tampered:
        mission_routes.verify_deliverable("project_api", item.id, mission_routes.VerifyBody(expected_version=item.version), request, reviewer)
    assert tampered.value.status_code == 409 and (workspace / "app" / "index.html").read_text() == "verified old app"
    staged_path.write_text(original, encoding="utf-8")

    # Crash after the descriptor-safe replacement but before final Mission state
    # persistence: recovery sees the exact target digest and finalizes safely.
    monkeypatch.setattr(mission_routes, "_after_staged_promotion", lambda: (_ for _ in ()).throw(RuntimeError("fault after replace")))
    with pytest.raises(HTTPException) as interrupted:
        mission_routes.verify_deliverable("project_api", item.id, mission_routes.VerifyBody(expected_version=item.version), request, reviewer)
    assert interrupted.value.status_code == 400 and (workspace / "app" / "index.html").read_text() == original
    assert service.deliverables("tenant_api", "project_api")[0].state == "awaiting_verification"
    monkeypatch.setattr(mission_routes, "_after_staged_promotion", lambda: None)
    mission_routes.overview("project_api", reviewer)
    assert service.deliverables("tenant_api", "project_api")[0].state == "verified"
    assert (workspace / "app" / "index.html").read_text() == original
    assert "promotion_intents" not in str(mission_routes.overview("project_api", reviewer))

    # A failure before replacement leaves the same intent, but no live code.
    retry_ref = "code-staging/retry/index.html"
    retry_path = workspace / retry_ref; retry_path.parent.mkdir(parents=True); retry_path.write_text("retry candidate", encoding="utf-8")
    retry = service.create_deliverable("tenant_api", "project_api", {
        "type": "code", "name": "Retry candidate", "source_ref": "mission/agent", "artifact_ref": retry_ref,
        "validation_evidence": [{"staged_artifact_ref": retry_ref, "intended_target": "app/index.html"}],
    }, "writer", retry_path.read_bytes())
    original_promote = mission_routes._promote_staged_code
    monkeypatch.setattr(mission_routes, "_promote_staged_code", lambda *_args: (_ for _ in ()).throw(MissionConflictError("before replace")))
    with pytest.raises(HTTPException) as before_replace:
        mission_routes.verify_deliverable("project_api", retry.id, mission_routes.VerifyBody(expected_version=retry.version), request, reviewer)
    assert before_replace.value.status_code == 409 and (workspace / "app" / "index.html").read_text() == original
    monkeypatch.setattr(mission_routes, "_promote_staged_code", original_promote)
    retry_path.unlink()
    for _ in range(2):
        with pytest.raises(HTTPException) as unresolved:
            mission_routes.overview("project_api", reviewer)
        assert unresolved.value.status_code == 409
    retry_path.write_text("retry candidate", encoding="utf-8")
    mission_routes.overview("project_api", reviewer)
    assert next(value for value in service.deliverables("tenant_api", "project_api") if value.id == retry.id).state == "verified"
    assert (workspace / "app" / "index.html").read_text() == "retry candidate"

    # A directory durability error can occur after os.replace. The recovery
    # path re-reads the target and finalizes only because its exact bytes exist.
    fsync_ref = "code-staging/fsync/index.html"
    fsync_path = workspace / fsync_ref; fsync_path.parent.mkdir(parents=True); fsync_path.write_text("sync candidate", encoding="utf-8")
    fsync_item = service.create_deliverable("tenant_api", "project_api", {
        "type": "code", "name": "Sync candidate", "source_ref": "mission/agent", "artifact_ref": fsync_ref,
        "validation_evidence": [{"staged_artifact_ref": fsync_ref, "intended_target": "app/index.html"}],
    }, "writer", fsync_path.read_bytes())
    original_directory_sync = mission_routes._fsync_promotion_directory
    monkeypatch.setattr(mission_routes, "_fsync_promotion_directory", lambda _fd: (_ for _ in ()).throw(OSError("directory sync fault")))
    with pytest.raises(HTTPException) as sync_fault:
        mission_routes.verify_deliverable("project_api", fsync_item.id, mission_routes.VerifyBody(expected_version=fsync_item.version), request, reviewer)
    assert sync_fault.value.status_code == 409 and (workspace / "app" / "index.html").read_text() == "sync candidate"
    assert next(value for value in service.deliverables("tenant_api", "project_api") if value.id == fsync_item.id).state == "awaiting_verification"
    monkeypatch.setattr(mission_routes, "_fsync_promotion_directory", original_directory_sync)
    mission_routes.overview("project_api", reviewer)
    assert next(value for value in service.deliverables("tenant_api", "project_api") if value.id == fsync_item.id).state == "verified"

    # A new canonical parent also needs a child-and-parent directory barrier
    # before replacement. Its failed creation sync is retried before finalizing.
    nested_ref = "code-staging/nested/index.html"
    nested_path = workspace / nested_ref; nested_path.parent.mkdir(parents=True); nested_path.write_text("nested candidate", encoding="utf-8")
    nested = service.create_deliverable("tenant_api", "project_api", {
        "type": "code", "name": "Nested candidate", "source_ref": "mission/agent", "artifact_ref": nested_ref,
        "validation_evidence": [{"staged_artifact_ref": nested_ref, "intended_target": "app/new-parent/index.html"}],
    }, "writer", nested_path.read_bytes())
    sync_calls = 0
    def fail_first_directory_sync(_fd: int) -> None:
        nonlocal sync_calls
        sync_calls += 1
        if sync_calls == 1:
            raise OSError("new parent sync fault")
        original_directory_sync(_fd)
    monkeypatch.setattr(mission_routes, "_fsync_promotion_directory", fail_first_directory_sync)
    with pytest.raises(HTTPException) as parent_fault:
        mission_routes.verify_deliverable("project_api", nested.id, mission_routes.VerifyBody(expected_version=nested.version), request, reviewer)
    assert parent_fault.value.status_code == 409
    assert next(value for value in service.deliverables("tenant_api", "project_api") if value.id == nested.id).state == "awaiting_verification"
    assert not (workspace / "app" / "new-parent" / "index.html").exists()
    monkeypatch.setattr(mission_routes, "_fsync_promotion_directory", original_directory_sync)
    mission_routes.overview("project_api", reviewer)
    assert next(value for value in service.deliverables("tenant_api", "project_api") if value.id == nested.id).state == "verified"
    assert (workspace / "app" / "new-parent" / "index.html").read_text() == "nested candidate"

    # On a fresh workspace, both the new app directory and its workspace-root
    # entry must survive their own sync barriers before verification can settle.
    def verify_missing_app_with_sync_fault(label: str, fail_call: int) -> None:
        fresh = tmp_path / f"fresh-{label}"; fresh.mkdir()
        staged_ref = "code-staging/candidate/index.html"
        staged = fresh / staged_ref; staged.parent.mkdir(parents=True); staged.write_text(f"{label} candidate", encoding="utf-8")
        monkeypatch.setattr(mission_routes, "project_dir", lambda _project_id: fresh)
        candidate = service.create_deliverable("tenant_api", "project_api", {
            "type": "code", "name": f"{label} durability", "source_ref": "mission/agent", "artifact_ref": staged_ref,
            "validation_evidence": [{"staged_artifact_ref": staged_ref, "intended_target": "app/index.html"}],
        }, "writer", staged.read_bytes())
        calls = 0
        def fail_created_app_barrier(_fd: int) -> None:
            nonlocal calls
            calls += 1
            if calls == fail_call:
                raise OSError(f"{label} sync fault")
            original_directory_sync(_fd)
        monkeypatch.setattr(mission_routes, "_fsync_promotion_directory", fail_created_app_barrier)
        with pytest.raises(HTTPException) as failed:
            mission_routes.verify_deliverable("project_api", candidate.id, mission_routes.VerifyBody(expected_version=candidate.version), request, reviewer)
        assert failed.value.status_code == 409
        assert next(value for value in service.deliverables("tenant_api", "project_api") if value.id == candidate.id).state == "awaiting_verification"
        assert not (fresh / "app" / "index.html").exists()
        retry_calls = 0
        def record_retry_barrier(_fd: int) -> None:
            nonlocal retry_calls
            retry_calls += 1
            original_directory_sync(_fd)
        monkeypatch.setattr(mission_routes, "_fsync_promotion_directory", record_retry_barrier)
        mission_routes.overview("project_api", reviewer)
        assert retry_calls >= 2  # app and the workspace-root app entry
        assert next(value for value in service.deliverables("tenant_api", "project_api") if value.id == candidate.id).state == "verified"
        assert (fresh / "app" / "index.html").read_text() == f"{label} candidate"

    verify_missing_app_with_sync_fault("app-directory", 1)
    verify_missing_app_with_sync_fault("workspace-parent", 2)


def test_platform_admin_without_room_membership_is_denied(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    owner = _context("owner", "owner")
    admin = _context("platform", "admin")
    admin.user.is_platform_admin = True
    CollaborationService(JsonCollaborationRepository(mission_routes._rooms)).create_room(tenant_id="tenant_api", project_id="project_api", creator_id="owner")
    request = SimpleNamespace(url=SimpleNamespace(path="/test"))
    mission_routes.bootstrap("project_api", mission_routes.BootstrapBody(title="Launch"), request, owner)
    with pytest.raises(HTTPException) as denied:
        mission_routes.overview("project_api", admin)
    assert denied.value.status_code == 403
    with pytest.raises(HTTPException) as verification_denied:
        mission_routes.verify_deliverable(
            "project_api", "deliverable_missing",
            mission_routes.VerifyBody(expected_version=1),
            request, admin,
        )
    assert verification_denied.value.status_code == 403


def test_pending_tagged_owner_has_no_mission_authority_until_acceptance_is_complete(monkeypatch, tmp_path: Path):
    """A room row is not public authority until its acceptance journal commits."""
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    monkeypatch.setattr(mission_routes, "audit_request", lambda *args, **kwargs: None)
    workspace = tmp_path / "project"; workspace.mkdir()
    monkeypatch.setattr(mission_routes, "project_dir", lambda _project_id: workspace)
    legacy_owner = _context("legacy_owner", "owner")
    pending_owner = _context("pending_owner", "owner")
    repository = JsonCollaborationRepository(mission_routes._rooms)
    room_service = CollaborationService(repository)
    room = room_service.create_room(
        tenant_id="tenant_api", project_id="project_api", creator_id="legacy_owner",
    )
    transaction_id = "txn_pending_owner"
    repository.save_room(replace(
        room,
        members=[*room.members, Member(
            actor_id="pending_owner", role="owner", transaction_id=transaction_id,
            visibility_state="committed",
        )],
        revision=room.revision + 1,
    ), room.revision)
    request = SimpleNamespace(url=SimpleNamespace(path="/test"))
    mission_routes.bootstrap(
        "project_api", mission_routes.BootstrapBody(title="Protected Mission"), request, legacy_owner,
    )

    protected_actions = (
        lambda: mission_routes.overview("project_api", pending_owner),
        lambda: mission_routes.add_agent(
            "project_api", mission_routes.AgentBody(name="A", role="Analyst", mandate="Review"),
            request, pending_owner,
        ),
        lambda: mission_routes.create_run("project_api", mission_routes.RunBody(), request, pending_owner),
        lambda: mission_routes.decide_checkpoint(
            "project_api", "approval_missing",
            mission_routes.ApprovalDecisionBody(
                decision="approve", expected_revision=1, expected_run_revision=1,
            ),
            request, pending_owner,
        ),
        lambda: mission_routes.create_trigger(
            "project_api", mission_routes.TriggerBody(type="cron", cron="0 9 * * 1"),
            request, pending_owner,
        ),
        lambda: mission_routes.verify_deliverable(
            "project_api", "deliverable_missing", mission_routes.VerifyBody(expected_version=1),
            request, pending_owner,
        ),
    )
    for action in protected_actions:
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 403

    journal = (
        mission_routes._rooms / ".invitation-acceptance" / "tenant_api" / "project_api"
        / f"{transaction_id}.json"
    )
    journal.parent.mkdir(parents=True)
    journal.write_text(json.dumps({
        "state": "COMPLETE", "transaction_id": transaction_id,
        "tenant_id": "tenant_api", "project_id": "project_api",
    }))

    assert mission_routes.overview("project_api", pending_owner)["mission"]["objective"] == "Protected Mission"
    assert mission_routes.add_agent(
        "project_api", mission_routes.AgentBody(name="A", role="Analyst", mandate="Review"),
        request, pending_owner,
    )["name"] == "A"
    assert mission_routes.create_run("project_api", mission_routes.RunBody(), request, pending_owner)["status"] == "queued"
    assert mission_routes.create_trigger(
        "project_api", mission_routes.TriggerBody(type="cron", cron="0 9 * * 1"),
        request, pending_owner,
    )["type"] == "cron"
    for allowed_missing_item in protected_actions[3::2]:
        with pytest.raises(HTTPException) as missing:
            allowed_missing_item()
        assert missing.value.status_code != 403


def _remove_room_member(repository: JsonCollaborationRepository, actor_id: str) -> None:
    room = repository.get_room("tenant_api", "project_api")
    repository.save_room(replace(
        room,
        members=[member for member in room.members if member.actor_id != actor_id],
        revision=room.revision + 1,
    ), room.revision)


def test_mission_mutation_and_member_removal_have_one_durable_order(monkeypatch, tmp_path: Path):
    """An authorized mutation commits before a removal waiting on its boundary."""
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    monkeypatch.setattr(mission_routes, "audit_request", lambda *args, **kwargs: None)
    workspace = tmp_path / "project"; workspace.mkdir()
    monkeypatch.setattr(mission_routes, "project_dir", lambda _project_id: workspace)
    actor = _context("owner", "owner")
    repository = JsonCollaborationRepository(mission_routes._rooms)
    CollaborationService(repository).create_room(
        tenant_id="tenant_api", project_id="project_api", creator_id="owner",
    )
    request = SimpleNamespace(url=SimpleNamespace(path="/test"))
    mission_routes.bootstrap("project_api", mission_routes.BootstrapBody(title="Mission"), request, actor)
    actual_service = mission_routes._service()
    boundary_locked = threading.Event()
    action_entered = threading.Event()
    allow_action = threading.Event()
    removal_started = threading.Event()
    order: list[str] = []

    class PausedService:
        repository = actual_service.repository

        def __getattr__(self, name):
            return getattr(actual_service, name)

        def mission(self, *args, **kwargs):
            return actual_service.mission(*args, **kwargs)

        def add_agent(self, *args, **kwargs):
            action_entered.set()
            assert allow_action.wait(5)
            result = actual_service.add_agent(*args, **kwargs)
            order.append("action")
            return result

    class TrackingRepository(JsonCollaborationRepository):
        @contextmanager
        def room_lock(self, tenant_id, project_id):
            with super().room_lock(tenant_id, project_id) as room:
                boundary_locked.set()
                yield room

    monkeypatch.setattr(mission_routes, "_service", lambda: PausedService())
    monkeypatch.setattr(mission_routes, "JsonCollaborationRepository", TrackingRepository)
    action_result: list[Any] = []
    action = threading.Thread(target=lambda: action_result.append(mission_routes.add_agent(
        "project_api", mission_routes.AgentBody(name="A", role="Analyst", mandate="Review"),
        request, actor,
    )))

    def remove() -> None:
        removal_started.set()
        _remove_room_member(repository, "owner")
        order.append("removal")

    removal = threading.Thread(target=remove)
    action.start(); assert boundary_locked.wait(5); assert action_entered.wait(5)
    removal.start(); assert removal_started.wait(5)
    allow_action.set()
    action.join(5); removal.join(5)
    assert not action.is_alive() and not removal.is_alive()
    assert action_result[0]["name"] == "A"
    assert order == ["action", "removal"]

    with pytest.raises(HTTPException) as denied_after_removal:
        mission_routes.add_agent(
            "project_api", mission_routes.AgentBody(name="B", role="Analyst", mandate="Review"),
            request, actor,
        )
    assert denied_after_removal.value.status_code == 403


def test_mission_read_publishes_before_waiting_removal_or_is_denied_after_it(monkeypatch, tmp_path: Path):
    """A Mission response cannot be assembled from data read after membership removal."""
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    workspace = tmp_path / "project"; workspace.mkdir()
    monkeypatch.setattr(mission_routes, "project_dir", lambda _project_id: workspace)
    actor = _context("owner", "owner")
    repository = JsonCollaborationRepository(mission_routes._rooms)
    CollaborationService(repository).create_room(
        tenant_id="tenant_api", project_id="project_api", creator_id="owner",
    )
    request = SimpleNamespace(url=SimpleNamespace(path="/test"))
    monkeypatch.setattr(mission_routes, "audit_request", lambda *args, **kwargs: None)
    mission_routes.bootstrap("project_api", mission_routes.BootstrapBody(title="Mission"), request, actor)
    original_public_mission = mission_routes._public_mission
    boundary_locked = threading.Event()
    read_entered = threading.Event()
    allow_publication = threading.Event()
    removal_started = threading.Event()
    order: list[str] = []

    def paused_public_mission(value):
        read_entered.set()
        assert allow_publication.wait(5)
        result = original_public_mission(value)
        order.append("read")
        return result

    monkeypatch.setattr(mission_routes, "_public_mission", paused_public_mission)
    class TrackingRepository(JsonCollaborationRepository):
        @contextmanager
        def room_lock(self, tenant_id, project_id):
            with super().room_lock(tenant_id, project_id) as room:
                boundary_locked.set()
                yield room
    monkeypatch.setattr(mission_routes, "JsonCollaborationRepository", TrackingRepository)
    read_result: list[Any] = []
    reader = threading.Thread(target=lambda: read_result.append(mission_routes.overview("project_api", actor)))

    def remove() -> None:
        removal_started.set()
        _remove_room_member(repository, "owner")
        order.append("removal")

    removal = threading.Thread(target=remove)
    reader.start(); assert boundary_locked.wait(5); assert read_entered.wait(5)
    removal.start(); assert removal_started.wait(5)
    allow_publication.set()
    reader.join(5); removal.join(5)
    assert not reader.is_alive() and not removal.is_alive()
    assert read_result[0]["mission"]["objective"] == "Mission"
    assert order == ["read", "removal"]
    with pytest.raises(HTTPException) as denied_after_removal:
        mission_routes.overview("project_api", actor)
    assert denied_after_removal.value.status_code == 403


def test_mission_api_trajectory_paginates_and_overview_is_bounded(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    monkeypatch.setattr(mission_routes, "audit_request", lambda *args, **kwargs: None)
    workspace = tmp_path / "project"; workspace.mkdir(); monkeypatch.setattr(mission_routes, "project_dir", lambda _id: workspace)
    owner = _context("owner", "owner"); request = SimpleNamespace(url=SimpleNamespace(path="/test"))
    CollaborationService(JsonCollaborationRepository(mission_routes._rooms)).create_room(tenant_id="tenant_api", project_id="project_api", creator_id="owner")
    mission_routes.bootstrap("project_api", mission_routes.BootstrapBody(title="x"), request, owner)
    service = mission_routes._service(); run = service.create_run("tenant_api", "project_api", {"type": "manual"})
    service.repository.mutate("tenant_api", "project_api", lambda records: [service._event(records, run, "event", {"n": n}) for n in range(2001)])
    first = mission_routes.trajectory("project_api", owner, None, 50)
    second = mission_routes.trajectory("project_api", owner, first["next_cursor"], 50)
    assert len(first["events"]) == len(second["events"]) == 50 and first["next_cursor"]
    assert not {item["id"] for item in first["events"]} & {item["id"] for item in second["events"]}
    assert first["retention"]["dropped_events"] == 1 and first["retention"]["truncated"] and first["retention"]["retained"] == 2000
    assert "schema_version" not in first
    with pytest.raises(HTTPException) as invalid: mission_routes.trajectory("project_api", owner, "expired", 50)
    assert invalid.value.status_code == 400
    overview = mission_routes.overview("project_api", owner)
    assert len(overview["events"]) == 100
    assert overview["events"] == [mission_routes._public_event(item) for item in service.events("tenant_api", "project_api", 100)]
    assert set(first).isdisjoint({"all_events", "unpaged_events"})


def test_actual_approved_graph_revisions_bind_manual_and_due_runs(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    monkeypatch.setattr(mission_routes, "audit_request", lambda *args, **kwargs: None)
    workspace = tmp_path / "project"; workspace.mkdir()
    monkeypatch.setattr(mission_routes, "project_dir", lambda _id: workspace)
    owner = _context("owner", "owner"); request = SimpleNamespace(url=SimpleNamespace(path="/test"))
    CollaborationService(JsonCollaborationRepository(mission_routes._rooms)).create_room(tenant_id="tenant_api", project_id="project_api", creator_id="owner")
    mission_routes.bootstrap("project_api", mission_routes.BootstrapBody(title="Launch"), request, owner)
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"]["tenant_id"] = "tenant_api"; graph["metadata"]["project_id"] = "project_api"
    store = OperationGraphStore(workspace, tenant_id="tenant_api", project_id="project_api")
    a = store.create_revision(graph, expected_revision_hash=None); store.approve_revision(a.revision_hash, actor_id="owner")
    manual = mission_routes.create_run("project_api", mission_routes.RunBody(), request, owner)
    assert "contract_revision" not in manual
    assert mission_routes._service().mission("tenant_api", "project_api").approved_contract_revision == a.revision_hash
    graph["metadata"]["description"] = "Revision B"
    b = store.create_revision(graph, expected_revision_hash=a.revision_hash); store.approve_revision(b.revision_hash, actor_id="owner")
    mission_routes.create_trigger("project_api", mission_routes.TriggerBody(type="condition", condition={"fact":"ready","operator":"eq","value":True}), request, owner)
    due = mission_routes.evaluate_due("project_api", mission_routes.DueBody(facts={"ready": True}), request, owner)["runs"][0]
    assert "contract_revision" not in due
    assert mission_routes._service().mission("tenant_api", "project_api").approved_contract_revision == b.revision_hash


def test_fact_event_endpoint_never_consumes_cron_and_defers_until_graph_approval(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    monkeypatch.setattr(mission_routes, "audit_request", lambda *args, **kwargs: None)
    workspace = tmp_path / "project"; workspace.mkdir()
    monkeypatch.setattr(mission_routes, "project_dir", lambda _id: workspace)
    owner = _context("owner", "owner"); request = SimpleNamespace(url=SimpleNamespace(path="/test"))
    CollaborationService(JsonCollaborationRepository(mission_routes._rooms)).create_room(
        tenant_id="tenant_api", project_id="project_api", creator_id="owner",
    )
    mission_routes.bootstrap("project_api", mission_routes.BootstrapBody(title="Launch"), request, owner)
    cron = mission_routes.create_trigger(
        "project_api", mission_routes.TriggerBody(type="cron", cron="*/5 * * * *"), request, owner,
    )
    condition = mission_routes.create_trigger(
        "project_api", mission_routes.TriggerBody(type="condition", condition={"fact": "ready", "operator": "eq", "value": True}), request, owner,
    )
    service = mission_routes._service()
    service.repository.mutate(
        "tenant_api", "project_api",
        lambda records: records["triggers"][cron["id"]].update({"next_due_at": "2020-01-01T00:00:00+00:00"}),
    )
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"]["tenant_id"] = "tenant_api"; graph["metadata"]["project_id"] = "project_api"
    store = OperationGraphStore(workspace, tenant_id="tenant_api", project_id="project_api")
    revision = store.create_revision(graph, expected_revision_hash=None)

    assert mission_routes.evaluate_due(
        "project_api", mission_routes.DueBody(facts={"ready": True}), request, owner,
    ) == {"runs": []}
    before = {item.id: item for item in service.triggers("tenant_api", "project_api")}
    assert before[cron["id"]].next_due_at == "2020-01-01T00:00:00+00:00"
    assert before[cron["id"]].handled_occurrences == {}
    assert before[condition["id"]].handled_occurrences == {}

    store.approve_revision(revision.revision_hash, actor_id="owner")
    fired = mission_routes.evaluate_due(
        "project_api", mission_routes.DueBody(facts={"ready": True}), request, owner,
    )["runs"]
    assert len(fired) == 1 and fired[0]["trigger_snapshot"]["type"] == "condition"
    after = {item.id: item for item in service.triggers("tenant_api", "project_api")}
    assert after[cron["id"]].next_due_at == "2020-01-01T00:00:00+00:00"
    assert after[cron["id"]].handled_occurrences == {}
    assert len(after[condition["id"]].handled_occurrences) == 1


def test_artifact_descriptor_confinement_cases(monkeypatch, tmp_path: Path):
    workspace = tmp_path / "project"; workspace.mkdir()
    nested = workspace / "nested"; nested.mkdir(); payload = nested / "ok.bin"; payload.write_bytes(b"exact")
    monkeypatch.setattr(mission_routes, "project_dir", lambda _id: workspace)
    assert mission_routes._artifact_bytes("project", "nested/ok.bin") == b"exact"
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "x").write_bytes(b"x")
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    (workspace / "link.bin").symlink_to(payload)
    (workspace / "directory").mkdir()
    large = workspace / "large.bin"
    with large.open("wb") as handle:
        handle.truncate(16 * 1024 * 1024 + 1)
    for ref in ("escape/x", "link.bin", "directory", "large.bin"):
        with pytest.raises(ValueError):
            mission_routes._artifact_bytes("project", ref)


def test_graph_admission_rejects_unapproved_and_corrupt_without_mutation(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(mission_routes, "_root", tmp_path / "missions")
    monkeypatch.setattr(mission_routes, "_rooms", tmp_path / "rooms")
    monkeypatch.setattr(mission_routes, "audit_request", lambda *args, **kwargs: None)
    workspace = tmp_path / "project"; workspace.mkdir()
    monkeypatch.setattr(mission_routes, "project_dir", lambda _id: workspace)
    owner = _context("owner", "owner"); request = SimpleNamespace(url=SimpleNamespace(path="/test"))
    CollaborationService(JsonCollaborationRepository(mission_routes._rooms)).create_room(tenant_id="tenant_api", project_id="project_api", creator_id="owner")
    mission_routes.bootstrap("project_api", mission_routes.BootstrapBody(title="Launch"), request, owner)
    no_graph = mission_routes.create_run("project_api", mission_routes.RunBody(), request, owner)
    assert "contract_revision" not in no_graph
    assert mission_routes._service().mission("tenant_api", "project_api").approved_contract_revision is None
    mission_routes.create_trigger("project_api", mission_routes.TriggerBody(type="condition", condition={"fact":"ready","operator":"eq","value":True}), request, owner)
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"]["tenant_id"] = "tenant_api"; graph["metadata"]["project_id"] = "project_api"
    store = OperationGraphStore(workspace, tenant_id="tenant_api", project_id="project_api")
    revision = store.create_revision(graph, expected_revision_hash=None)
    before = mission_routes._service().mission("tenant_api", "project_api"); count = len(mission_routes._service().runs("tenant_api", "project_api"))
    with pytest.raises(HTTPException):
        mission_routes.create_run("project_api", mission_routes.RunBody(), request, owner)
    assert mission_routes.evaluate_due("project_api", mission_routes.DueBody(facts={"ready": True}), request, owner) == {"runs": []}
    after = mission_routes._service().mission("tenant_api", "project_api")
    assert len(mission_routes._service().runs("tenant_api", "project_api")) == count and after.revision == before.revision and after.approved_contract_revision is None
    (store._revisions / f"{revision.revision_hash}.json").write_text("{")
    with pytest.raises(HTTPException):
        mission_routes.create_run("project_api", mission_routes.RunBody(), request, owner)
    assert len(mission_routes._service().runs("tenant_api", "project_api")) == count
