"""Mission route contract tests using the same durable Project Room authority."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
import hashlib

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from apps.api import mission_routes
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.demo.identity import AuthContext, User
from simulacra.operation_graph import OperationGraphStore, load_operation_graph


def _context(user_id: str, role: str) -> AuthContext:
    return AuthContext(
        user=User(id=user_id, email=f"{user_id}@example.test", name=user_id, password_hash="unused"),
        tenant_id="tenant_api", role=role, auth_via="test",
    )


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
    assert mission_routes.overview("project_api", member)["mission"]["id"] == mission["id"]
    with pytest.raises(HTTPException) as denied:
        mission_routes.add_agent("project_api", mission_routes.AgentBody(name="A", role="Engineer", mandate="Work"), request, member)
    assert denied.value.status_code == 403
    with pytest.raises(ValidationError):
        mission_routes.AgentBody.model_validate({"name": "A", "role": "Engineer", "mandate": "Work", "provider": "x"})
    with pytest.raises(ValidationError):
        mission_routes.MissionPatch.model_validate({"expected_revision": 1, "approved_contract_revision": "spoof"})
    for state in ("verified", "published"):
        with pytest.raises(ValidationError):
            mission_routes.DeliverableBody.model_validate({"type": "report", "name": "R", "source_ref": "x", "artifact_ref": "release.md", "state": state})

    run = mission_routes.create_run("project_api", mission_routes.RunBody(), request, owner)
    assert run["execution_profile"]["runtime"] == "codex"
    artifact = mission_routes.create_deliverable("project_api", mission_routes.DeliverableBody(type="report", name="R", source_ref="room/r.md", artifact_ref="release.md"), request, owner)
    assert artifact["content_hash"] == hashlib.sha256(b"v1").hexdigest()
    with pytest.raises(HTTPException) as self_verify:
        mission_routes.verify_deliverable("project_api", artifact["id"], mission_routes.VerifyBody(content_hash=artifact["content_hash"], expected_revision=artifact["revision"]), request, owner)
    assert self_verify.value.status_code == 403
    artifact_path.write_bytes(b"mutated")
    with pytest.raises(HTTPException) as changed:
        mission_routes.verify_deliverable("project_api", artifact["id"], mission_routes.VerifyBody(content_hash=artifact["content_hash"], expected_revision=artifact["revision"]), request, reviewer)
    assert changed.value.status_code == 409
    artifact_path.write_bytes(b"v1")
    verified = mission_routes.verify_deliverable("project_api", artifact["id"], mission_routes.VerifyBody(content_hash=artifact["content_hash"], expected_revision=artifact["revision"]), request, reviewer)
    assert verified["state"] == "verified"
    artifact_path.write_bytes(b"v2")
    second = mission_routes.create_deliverable("project_api", mission_routes.DeliverableBody(type="report", name="R", source_ref="room/r.md", artifact_ref="release.md"), request, owner)
    assert second["version"] == 2 and second["state"] != "verified"
    with pytest.raises(HTTPException) as stale:
        mission_routes.verify_deliverable("project_api", second["id"], mission_routes.VerifyBody(content_hash=artifact["content_hash"], expected_revision=second["revision"]), request, reviewer)
    assert stale.value.status_code == 409
    with pytest.raises(HTTPException) as traversal:
        mission_routes.create_deliverable("project_api", mission_routes.DeliverableBody(type="report", name="bad", source_ref="x", artifact_ref="../escape"), request, owner)
    assert traversal.value.status_code == 400


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
            mission_routes.VerifyBody(content_hash="0" * 64, expected_revision=1),
            request, admin,
        )
    assert verification_denied.value.status_code == 403


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
    assert manual["contract_revision"] == mission_routes._service().mission("tenant_api", "project_api").approved_contract_revision == a.revision_hash
    graph["metadata"]["description"] = "Revision B"
    b = store.create_revision(graph, expected_revision_hash=a.revision_hash); store.approve_revision(b.revision_hash, actor_id="owner")
    mission_routes.create_trigger("project_api", mission_routes.TriggerBody(type="condition", condition={"fact":"ready","operator":"eq","value":True}), request, owner)
    due = mission_routes.evaluate_due("project_api", mission_routes.DueBody(facts={"ready": True}), request, owner)["runs"][0]
    assert due["contract_revision"] == mission_routes._service().mission("tenant_api", "project_api").approved_contract_revision == b.revision_hash


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
    assert no_graph["contract_revision"] is None and mission_routes._service().mission("tenant_api", "project_api").approved_contract_revision is None
    mission_routes.create_trigger("project_api", mission_routes.TriggerBody(type="condition", condition={"fact":"ready","operator":"eq","value":True}), request, owner)
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"]["tenant_id"] = "tenant_api"; graph["metadata"]["project_id"] = "project_api"
    store = OperationGraphStore(workspace, tenant_id="tenant_api", project_id="project_api")
    revision = store.create_revision(graph, expected_revision_hash=None)
    before = mission_routes._service().mission("tenant_api", "project_api"); count = len(mission_routes._service().runs("tenant_api", "project_api"))
    for invoke in (lambda: mission_routes.create_run("project_api", mission_routes.RunBody(), request, owner), lambda: mission_routes.evaluate_due("project_api", mission_routes.DueBody(facts={"ready": True}), request, owner)):
        with pytest.raises(HTTPException): invoke()
        after = mission_routes._service().mission("tenant_api", "project_api")
        assert len(mission_routes._service().runs("tenant_api", "project_api")) == count and after.revision == before.revision and after.approved_contract_revision is None
    (store._revisions / f"{revision.revision_hash}.json").write_text("{")
    with pytest.raises(HTTPException):
        mission_routes.create_run("project_api", mission_routes.RunBody(), request, owner)
    assert len(mission_routes._service().runs("tenant_api", "project_api")) == count
