from __future__ import annotations

import hashlib
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import HTTPException

from apps.api import file_routes, work_routes
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.collaboration.models import Member, Task, TaskState
from simulacra.demo.identity import AuthContext, User
from simulacra.demo.sources import SourceFile
from simulacra.missions import JsonMissionRepository, MissionService
from simulacra.missions.projections import project_work_items


def _ctx(human_id: str, role: str = "member") -> AuthContext:
    return AuthContext(User(human_id, f"{human_id}@example.test", human_id, "unused"), "tenant_1", role, "test")


def _mission(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(work_routes, "_mission_root", tmp_path / "missions")
    monkeypatch.setattr(work_routes, "_collaboration_root", tmp_path / "rooms")
    monkeypatch.setattr(file_routes, "_mission_root", tmp_path / "missions")
    monkeypatch.setattr(file_routes, "_collaboration_root", tmp_path / "rooms")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(file_routes, "project_dir", lambda _project_id: workspace)
    collaboration = JsonCollaborationRepository(tmp_path / "rooms")
    CollaborationService(collaboration).create_room(tenant_id="tenant_1", project_id="mission_1", creator_id="owner")
    missions = MissionService(JsonMissionRepository(tmp_path / "missions"))
    missions.bootstrap("tenant_1", "mission_1", "owner", {"title": "Close", "objective": "Close", "verifier_ids": ["owner"]})
    return collaboration, missions, workspace


def _add_recoverable_member(
    repository: JsonCollaborationRepository, actor_id: str, *, complete: bool,
) -> None:
    transaction_id = f"invite_accept_{actor_id}"
    room = repository.get_room("tenant_1", "mission_1")
    repository.save_room(replace(
        room,
        members=[*room.members, Member(
            actor_id=actor_id, role="reviewer", display_name=f"{actor_id} secret name",
            transaction_id=transaction_id,
            visibility_state="committed" if complete else "pending_commit",
        )],
        revision=room.revision + 1,
    ), room.revision)
    if complete:
        journal = (
            repository.root / ".invitation-acceptance" / "tenant_1" / "mission_1"
            / f"{transaction_id}.json"
        )
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            '{"project_id":"mission_1","state":"COMPLETE","tenant_id":"tenant_1",'
            f'"transaction_id":"{transaction_id}"}}',
            encoding="utf-8",
        )


def test_files_require_complete_visible_membership_and_never_leak_pending_names(monkeypatch, tmp_path: Path):
    collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    (workspace / "outputs").mkdir()
    content = b"Mission result"
    (workspace / "outputs" / "result.md").write_bytes(content)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1",
        {"type": "report", "name": "result.md", "source_ref": "mission/agent", "artifact_ref": "outputs/result.md"},
        producer_id="agent_1", artifact_bytes=content,
    )
    file_id = file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1")
    _add_recoverable_member(collaboration, "pending_human", complete=False)
    _add_recoverable_member(collaboration, "complete_human", complete=True)

    for operation in (
        lambda: file_routes.mission_files("mission_1", ctx=_ctx("pending_human")),
        lambda: file_routes.file_metadata("mission_1", file_id, ctx=_ctx("pending_human")),
        lambda: file_routes.file_content("mission_1", file_id, ctx=_ctx("pending_human")),
    ):
        with pytest.raises(HTTPException) as denied:
            operation()
        assert denied.value.status_code == 404

    assert file_routes.file_content("mission_1", file_id, ctx=_ctx("owner")).body == content
    assert file_routes.file_content("mission_1", file_id, ctx=_ctx("complete_human")).body == content
    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["deliverables"][deliverable.id].update({
            "verified_by": "pending_human", "state": "verified",
        }),
    )
    owner_payload = file_routes.file_metadata("mission_1", file_id, ctx=_ctx("owner"))
    assert owner_payload["file"]["verifier"] is None
    assert "pending_human secret name" not in str(owner_payload)

    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["deliverables"][deliverable.id].update({"verified_by": "complete_human"}),
    )
    complete_payload = file_routes.file_metadata("mission_1", file_id, ctx=_ctx("owner"))["file"]
    assert complete_payload["verifier"] == {
        "id": "complete_human", "display_name": "complete_human secret name",
    }
    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["deliverables"][deliverable.id].update({"verified_by": "owner"}),
    )
    legacy_payload = file_routes.file_metadata("mission_1", file_id, ctx=_ctx("owner"))["file"]
    assert legacy_payload["verifier"] == {"id": "owner", "display_name": "Mission human"}


def test_file_visibility_precheck_never_holds_room_lock(monkeypatch, tmp_path: Path):
    collaboration, _missions, _workspace = _mission(tmp_path, monkeypatch)
    entered, release = threading.Event(), threading.Event()
    original = JsonCollaborationRepository.visible_member
    blocked = False

    def gated(self, room, actor_id):
        nonlocal blocked
        if actor_id == "owner" and not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        return original(self, room, actor_id)

    monkeypatch.setattr(JsonCollaborationRepository, "visible_member", gated)

    def probe_room() -> str:
        with collaboration.room_lock("tenant_1", "mission_1") as room:
            return room.project_id

    with ThreadPoolExecutor(max_workers=2) as pool:
        authorization = pool.submit(
            file_routes._require_member, collaboration, "tenant_1", "mission_1", "owner",
        )
        assert entered.wait(timeout=5)
        probe = pool.submit(probe_room)
        assert probe.result(timeout=5) == "mission_1"
        release.set()
        authorization.result(timeout=5)


def test_work_route_computes_allowed_actions_server_side(monkeypatch, tmp_path: Path):
    collaboration, _missions, _workspace = _mission(tmp_path, monkeypatch)
    task = CollaborationService(collaboration).create_task(
        tenant_id="tenant_1", project_id="mission_1", actor_id="owner", title="Claim me", objective="Do it", acceptance_criteria=["Done"],
    )
    owner = work_routes.workspace_work(ctx=_ctx("owner", "owner"))
    assert next(row for row in owner["items"] if row["source_id"] == task.id)["allowed_actions"] == ["open", "claim_work"]
    viewer_room = CollaborationService(collaboration).add_member(
        tenant_id="tenant_1", project_id="mission_1", actor_id="owner", member_id="viewer", role="viewer", expected_revision=1,
    )
    viewer = work_routes.workspace_work(ctx=_ctx("viewer", "viewer"))
    assert next(row for row in viewer["items"] if row["source_id"] == task.id)["allowed_actions"] == ["open"]
    assert next(row for row in viewer["items"] if row["source_id"] == task.id)["action_targets"] == {}


def test_output_file_verification_action_is_exact_and_designation_scoped(monkeypatch, tmp_path: Path):
    collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    service = CollaborationService(collaboration)
    room = service.add_member(
        tenant_id="tenant_1", project_id="mission_1", actor_id="owner", member_id="viewer",
        role="viewer", expected_revision=1,
    )
    service.add_member(
        tenant_id="tenant_1", project_id="mission_1", actor_id="owner", member_id="producer",
        role="member", expected_revision=room.revision,
    )
    (workspace / "outputs").mkdir()
    content = b"Awaiting human verification"
    (workspace / "outputs" / "report.md").write_bytes(content)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1",
        {"type": "report", "name": "report.md", "source_ref": "mission/agent", "artifact_ref": "outputs/report.md"},
        producer_id="producer", artifact_bytes=content,
    )
    file_id = file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1")

    owner = file_routes.file_metadata("mission_1", file_id, ctx=_ctx("owner", "owner"))["file"]
    assert owner["allowed_actions"] == ["verify_output"]
    assert owner["action_targets"] == {
        "verify_output": {"kind": "output", "id": deliverable.id, "revision": deliverable.version},
    }
    assert owner["id"] == file_id

    for human_id, role in (("producer", "member"), ("viewer", "viewer")):
        item = file_routes.file_metadata("mission_1", file_id, ctx=_ctx(human_id, role))["file"]
        assert item["allowed_actions"] == []
        assert item["action_targets"] == {}

    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["deliverables"][deliverable.id].update({"state": "verified"}),
    )
    verified = file_routes.file_metadata("mission_1", file_id, ctx=_ctx("owner", "owner"))["file"]
    assert verified["allowed_actions"] == []
    assert verified["action_targets"] == {}


def test_work_verify_target_links_to_exact_opaque_file_review_surface(monkeypatch, tmp_path: Path):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    (workspace / "outputs").mkdir()
    content = b"Review this exact output"
    (workspace / "outputs" / "review.md").write_bytes(content)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1",
        {"type": "report", "name": "review.md", "source_ref": "mission/agent", "artifact_ref": "outputs/review.md"},
        producer_id="agent_1", artifact_bytes=content,
    )
    expected_file_id = file_routes.output_file_id(
        deliverable.id, tenant_id="tenant_1", project_id="mission_1",
    )

    row = next(
        item for item in work_routes.workspace_work(ctx=_ctx("owner", "owner"))["items"]
        if item["source_type"] == "output" and item["source_id"] == deliverable.id
    )
    assert row["action_targets"]["verify_output"] == {
        "kind": "output", "id": deliverable.id, "revision": deliverable.version,
        "file_id": expected_file_id,
    }


@pytest.mark.parametrize(
    "actions,targets",
    [
        (["verify_output"], {"verify_output": {"kind": "source", "id": "deliverable_1", "revision": 1}}),
        (["verify_output"], {"verify_output": {"kind": "output", "id": "../bad", "revision": 1}}),
        (["verify_output"], {"verify_output": {"kind": "output", "id": "deliverable_1", "revision": 0}}),
        (["verify_output"], {"verify_output": {"kind": "output", "id": "deliverable_1", "revision": 1, "path": "/private"}}),
        (["verify_output", "download"], {"verify_output": {"kind": "output", "id": "deliverable_1", "revision": 1}}),
    ],
)
def test_file_action_sanitizer_rejects_malformed_or_mismatched_actions(actions, targets):
    assert file_routes._sanitize_file_actions(actions, targets) == ([], {})


@pytest.mark.parametrize("version", [None, 0, True, "1", {"revision": 1}])
def test_output_file_verification_action_sanitizes_malformed_revision_closed(monkeypatch, tmp_path: Path, version):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    (workspace / "outputs").mkdir()
    content = b"Awaiting human verification"
    (workspace / "outputs" / "report.md").write_bytes(content)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1",
        {"type": "report", "name": "report.md", "source_ref": "mission/agent", "artifact_ref": "outputs/report.md"},
        producer_id="agent_1", artifact_bytes=content,
    )
    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["deliverables"][deliverable.id].update({"version": version}),
    )
    file_id = file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1")
    item = file_routes.file_metadata("mission_1", file_id, ctx=_ctx("owner", "owner"))["file"]
    assert item["allowed_actions"] == []
    assert item["action_targets"] == {}


def test_file_content_rejects_path_escape_and_hash_change(monkeypatch, tmp_path: Path):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    (workspace / "outputs").mkdir()
    output = workspace / "outputs" / "report.md"
    output.write_bytes(b"version one")
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1", {"type": "report", "name": "report.md", "source_ref": "mission/agent", "artifact_ref": "outputs/report.md"},
        producer_id="agent_1", artifact_bytes=b"version one",
    )
    file_id = file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1")
    metadata = file_routes.file_metadata("mission_1", file_id, ctx=_ctx("owner", "owner"))
    assert metadata["file"]["id"] == file_id
    assert "artifact_ref" not in str(metadata)
    output.write_bytes(b"version two")
    with pytest.raises(HTTPException) as exc:
        file_routes.file_content("mission_1", file_id, ctx=_ctx("owner", "owner"))
    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "file_changed"

    def escaped(_tenant: str, _project: str, _file: str):
        return {"kind": "output", "name": "bad", "media_type": "text/plain", "size": 1, "sha256": hashlib.sha256(b"x").hexdigest(), "artifact_ref": "../escape", "state": "verified", "type": "report"}
    monkeypatch.setattr(file_routes, "_resolve_file", escaped)
    with pytest.raises(HTTPException) as escaped_error:
        file_routes.file_content("mission_1", "file_bad", ctx=_ctx("owner", "owner"))
    assert escaped_error.value.detail["code"] == "file_unavailable"


def test_staged_code_is_not_previewable(monkeypatch, tmp_path: Path):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    (workspace / "work" / "agent-staging").mkdir(parents=True)
    staged = workspace / "work" / "agent-staging" / "index.html"
    staged.write_text("<script>window.top.location='https://example.test'</script>")
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1", {"type": "code", "name": "index.html", "source_ref": "mission/agent", "artifact_ref": "work/agent-staging/index.html"},
        producer_id="agent_1", artifact_bytes=staged.read_bytes(),
    )
    file_id = file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1")
    metadata = file_routes.file_metadata("mission_1", file_id, ctx=_ctx("owner", "owner"))["file"]
    assert metadata["previewable"] is False
    with pytest.raises(HTTPException) as exc:
        file_routes.file_content("mission_1", file_id, disposition="inline", ctx=_ctx("owner", "owner"))
    assert exc.value.detail["code"] == "file_preview_unavailable"

    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["deliverables"][deliverable.id].update({"state": "verified"}),
    )
    verified = file_routes.file_metadata("mission_1", file_id, ctx=_ctx("owner", "owner"))["file"]
    assert verified["previewable"] is True
    with pytest.raises(HTTPException) as verified_inline:
        file_routes.file_content("mission_1", file_id, disposition="inline", ctx=_ctx("owner", "owner"))
    assert verified_inline.value.detail["code"] == "file_preview_unavailable"


def test_file_list_is_opaque_authorized_ordered_and_legacy_compatible(monkeypatch, tmp_path: Path):
    collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    source_bytes = b"invoice,total\nINV-1,42\n"
    source = workspace / "inputs" / "data-room" / "invoices.csv"
    source.parent.mkdir(parents=True)
    source.write_bytes(source_bytes)
    monkeypatch.setattr(file_routes, "list_source_files", lambda _project: [SourceFile(
        name="invoices.csv", size=len(source_bytes), type="csv", sha256=hashlib.sha256(source_bytes).hexdigest(),
        status="extractable", detail="Ready", row_count=1,
    )])
    (workspace / "outputs").mkdir()
    output = workspace / "outputs" / "close.md"
    output.write_bytes(b"Close complete")
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1",
        {"type": "report", "name": "close.md", "source_ref": "mission/agent", "artifact_ref": "outputs/close.md"},
        producer_id="agent_1", artifact_bytes=output.read_bytes(),
    )
    payload = file_routes.mission_files("mission_1", kind="all", ctx=_ctx("owner", "owner"))
    assert [item["kind"] for item in payload["items"]] == ["output", "source"]
    assert payload["files"] == [{
        "name": "invoices.csv", "size": len(source_bytes), "type": "csv", "status": "extractable",
        "detail": "Ready", "row_count": 1,
    }]
    assert all(item["id"].startswith("file_") for item in payload["items"])
    output_item = next(item for item in payload["items"] if item["kind"] == "output")
    assert output_item["action_targets"] == {
        "verify_output": {"kind": "output", "id": deliverable.id, "revision": deliverable.version},
    }
    assert deliverable.id not in str({**output_item, "action_targets": {}})
    forbidden = ("artifact_ref", "source_ref", "path", "runtime", "provider", "model", "host")
    assert not any(word in str(payload).lower() for word in forbidden)

    room = collaboration.get_room("tenant_1", "mission_1")
    collaboration.save_room(replace(room, members=[], revision=room.revision + 1), room.revision)
    with pytest.raises(HTTPException) as denied:
        file_routes.mission_files("mission_1", kind="all", ctx=_ctx("owner", "owner"))
    assert denied.value.status_code == 404


def test_file_inventory_exposes_safe_run_and_opaque_source_provenance(monkeypatch, tmp_path: Path):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    source_bytes = b"invoice,total\nINV-1,42\n"
    source = workspace / "inputs" / "data-room" / "invoices.csv"
    source.parent.mkdir(parents=True)
    source.write_bytes(source_bytes)
    source_record = SourceFile(
        name="invoices.csv", size=len(source_bytes), type="csv",
        sha256=hashlib.sha256(source_bytes).hexdigest(), status="extractable", detail="Ready", row_count=1,
    )
    monkeypatch.setattr(file_routes, "list_source_files", lambda _project: [source_record])
    source_id = file_routes.mission_files("mission_1", kind="source", ctx=_ctx("owner", "owner"))["items"][0]["id"]
    run = missions.create_run("tenant_1", "mission_1", {"type": "manual"})
    (workspace / "outputs").mkdir()
    output_bytes = b"Close complete"
    evidence_bytes = b'{"checks":"passed"}'
    (workspace / "outputs" / "close.md").write_bytes(output_bytes)
    (workspace / "outputs" / "evidence.json").write_bytes(evidence_bytes)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1",
        {
            "type": "report", "name": "close.md", "source_ref": f"mission/run/{run.id}",
            "artifact_ref": "outputs/close.md",
            "validation_evidence": [{
                "evidence_ref": "outputs/evidence.json", "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
                "run_id": run.id, "source_ids": [source_id],
            }],
        },
        producer_id="agent_1", artifact_bytes=output_bytes,
    )

    payload = file_routes.mission_files("mission_1", kind="all", ctx=_ctx("owner", "owner"))
    output = next(item for item in payload["items"] if item["kind"] == "output")
    evidence = next(item for item in payload["items"] if item["kind"] == "evidence")
    assert output["run_id"] == run.id and output["source_ids"] == [source_id]
    assert evidence["run_id"] == run.id and evidence["source_ids"] == [source_id]
    assert output["action_targets"] == {
        "verify_output": {"kind": "output", "id": deliverable.id, "revision": deliverable.version},
    }
    assert evidence["allowed_actions"] == [] and evidence["action_targets"] == {}
    assert deliverable.id not in str({**output, "action_targets": {}})
    assert not any(word in str(payload).lower() for word in ("source_ref", "artifact_ref", "path"))

    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["deliverables"][deliverable.id].update({
            "source_ref": "mission/run/../../outside",
            "validation_evidence": [{**state["deliverables"][deliverable.id]["validation_evidence"][0], "run_id": "run_external"}],
        }),
    )
    rebound = file_routes.file_metadata(
        "mission_1", file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1"),
        ctx=_ctx("owner", "owner"),
    )["file"]
    assert rebound["run_id"] is None
    assert "outside" not in str(rebound) and "run_external" not in str(rebound)


def test_file_items_bind_evidence_to_exact_opaque_parent_output(monkeypatch, tmp_path: Path):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    (workspace / "outputs").mkdir()
    output_bytes, evidence_bytes = b"Report", b'{"verified":true}'
    (workspace / "outputs" / "report.md").write_bytes(output_bytes)
    (workspace / "outputs" / "evidence.json").write_bytes(evidence_bytes)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1", {
            "type": "report", "name": "report.md", "source_ref": "mission/agent", "artifact_ref": "outputs/report.md",
            "validation_evidence": [{"evidence_ref": "outputs/evidence.json", "sha256": hashlib.sha256(evidence_bytes).hexdigest()}],
        }, producer_id="agent_1", artifact_bytes=output_bytes,
    )
    payload = file_routes.mission_files("mission_1", kind="all", ctx=_ctx("owner", "owner"))
    output = next(item for item in payload["items"] if item["kind"] == "output")
    evidence = next(item for item in payload["items"] if item["kind"] == "evidence")
    assert output["parent_output_id"] is None
    assert evidence["parent_output_id"] == output["id"]
    assert deliverable.id not in str(evidence)


def test_file_items_resolve_public_producer_and_verifier_attribution(monkeypatch, tmp_path: Path):
    collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    agent = missions.add_agent("tenant_1", "mission_1", {
        "name": "Fin", "role": "Analyst", "mandate": "Prepare the report", "autonomy": "assist",
    })
    room = collaboration.get_room("tenant_1", "mission_1")
    collaboration.save_room(replace(
        room,
        members=[replace(member, display_name="Ada") if member.actor_id == "owner" else member for member in room.members],
        revision=room.revision + 1,
    ), room.revision)
    (workspace / "outputs").mkdir()
    content = b"Verified report"
    (workspace / "outputs" / "report.md").write_bytes(content)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1",
        {"type": "report", "name": "report.md", "source_ref": "mission/agent", "artifact_ref": "outputs/report.md"},
        producer_id=agent.id, artifact_bytes=content,
    )
    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["deliverables"][deliverable.id].update({"verified_by": "owner", "state": "verified"}),
    )
    item = file_routes.mission_files("mission_1", kind="output", ctx=_ctx("owner", "owner"))["items"][0]
    assert item["producer"] == {"id": agent.id, "display_name": "Fin"}
    assert item["verifier"] == {"id": "owner", "display_name": "Ada"}


def test_file_items_hide_missing_public_producer_identity(monkeypatch, tmp_path: Path):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    (workspace / "outputs").mkdir()
    content = b"Report with a retired producer"
    (workspace / "outputs" / "report.md").write_bytes(content)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1",
        {"type": "report", "name": "report.md", "source_ref": "mission/agent", "artifact_ref": "outputs/report.md"},
        producer_id="agent_retired", artifact_bytes=content,
    )

    item = file_routes.file_metadata(
        "mission_1",
        file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1"),
        ctx=_ctx("owner", "owner"),
    )["file"]

    assert item["producer_id"] is None
    assert item["producer"] is None
    assert "agent_retired" not in str(item)


def test_final_output_exposes_ordered_public_crew_contributors(monkeypatch, tmp_path: Path):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    researcher = missions.add_agent("tenant_1", "mission_1", {
        "name": "Rhea", "role": "Researcher", "mandate": "Find the exception", "autonomy": "assist",
    })
    reviewer = missions.add_agent("tenant_1", "mission_1", {
        "name": "Fin", "role": "Reviewer", "mandate": "Prepare the final report", "autonomy": "assist",
    })
    run = missions.create_run(
        "tenant_1", "mission_1", {"type": "manual"},
        assigned_agent_ids=[researcher.id, reviewer.id],
    )
    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["runs"][run.id].update({
            "completed_agent_ids": [researcher.id, reviewer.id],
            "status": "succeeded",
        }),
    )
    (workspace / "outputs").mkdir()
    content = b"Final crew report"
    (workspace / "outputs" / "report.md").write_bytes(content)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1", {
            "type": "report", "name": "report.md", "source_ref": f"mission/run/{run.id}",
            "artifact_ref": "outputs/report.md", "validation_evidence": [{"run_id": run.id}],
        }, producer_id=reviewer.id, artifact_bytes=content,
    )

    item = file_routes.file_metadata(
        "mission_1",
        file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1"),
        ctx=_ctx("owner", "owner"),
    )["file"]

    assert item["producer"] == {"id": reviewer.id, "display_name": "Fin"}
    assert item["contributors"] == [
        {"id": researcher.id, "display_name": "Rhea"},
        {"id": reviewer.id, "display_name": "Fin"},
    ]


def test_final_output_excludes_invalid_or_unproven_crew_contributors(monkeypatch, tmp_path: Path):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    researcher = missions.add_agent("tenant_1", "mission_1", {
        "name": "Rhea", "role": "Researcher", "mandate": "Find the exception", "autonomy": "assist",
    })
    reviewer = missions.add_agent("tenant_1", "mission_1", {
        "name": "Fin", "role": "Reviewer", "mandate": "Prepare the final report", "autonomy": "assist",
    })
    observer = missions.add_agent("tenant_1", "mission_1", {
        "name": "Mira", "role": "Observer", "mandate": "Observe only", "autonomy": "assist",
    })
    retired = missions.add_agent("tenant_1", "mission_1", {
        "name": "Sol", "role": "Retired specialist", "mandate": "Supply a prior finding", "autonomy": "assist",
    })
    run = missions.create_run(
        "tenant_1", "mission_1", {"type": "manual"},
        assigned_agent_ids=[researcher.id, reviewer.id, retired.id],
    )
    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: (
            state["runs"][run.id].update({
                "completed_agent_ids": [researcher.id, observer.id, retired.id],
                "status": "succeeded",
            }),
            state["agents"].pop(retired.id),
        ),
    )
    (workspace / "outputs").mkdir()
    content = b"Partial crew report"
    (workspace / "outputs" / "report.md").write_bytes(content)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1", {
            "type": "report", "name": "report.md", "source_ref": f"mission/run/{run.id}",
            "artifact_ref": "outputs/report.md", "validation_evidence": [{"run_id": run.id}],
        }, producer_id=researcher.id, artifact_bytes=content,
    )

    item = file_routes.file_metadata(
        "mission_1",
        file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1"),
        ctx=_ctx("owner", "owner"),
    )["file"]

    assert item["contributors"] == [{"id": researcher.id, "display_name": "Rhea"}]
    assert reviewer.id not in str(item["contributors"])
    assert observer.id not in str(item["contributors"])
    assert retired.id not in str(item["contributors"])

    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["runs"][run.id].update({
            "assigned_agent_ids": [researcher.id, researcher.id],
            "completed_agent_ids": [researcher.id, researcher.id],
        }),
    )
    corrupt_item = file_routes.file_metadata(
        "mission_1",
        file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1"),
        ctx=_ctx("owner", "owner"),
    )["file"]
    assert corrupt_item["contributors"] == []


def test_file_metadata_screens_paths_internal_vocabulary_and_malformed_values(monkeypatch, tmp_path: Path):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    (workspace / "outputs").mkdir()
    content = b"Report"
    (workspace / "outputs" / "report.md").write_bytes(content)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1",
        {"type": "report", "name": "report.md", "source_ref": "mission/agent", "artifact_ref": "outputs/report.md"},
        producer_id="agent_1", artifact_bytes=content,
    )
    missions.repository.mutate("tenant_1", "mission_1", lambda state: state["deliverables"][deliverable.id].update({
        "name": "../../runtime-provider-path.log", "state": "worker_failed", "version": "../host",
        "content_hash": "model-secret", "producer_id": "../codex", "verified_by": "provider/key",
        "created_at": "../../private", "updated_at": {"runtime": "hidden"},
    }))
    file_id = file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1")
    item = file_routes.file_metadata("mission_1", file_id, ctx=_ctx("owner", "owner"))["file"]
    assert item["name"] == "Mission output"
    assert item["state"] == "draft"
    assert item["version"] == 1
    assert item["sha256"] == ""
    assert item["producer"] is None and item["verifier"] is None
    assert item["created_at"] is None and item["updated_at"] is None
    assert not any(word in str(item).lower() for word in (
        "runtime", "provider", "worker", "model", "codex", "host", "path", "../../", "artifact_ref", "source_ref",
    ))


def test_file_inventory_rejects_symlinked_source_ancestor_leaf_and_project_root(monkeypatch, tmp_path: Path):
    _collaboration, _missions, _workspace = _mission(tmp_path, monkeypatch)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.csv").write_text("secret,value\nkey,42\n")

    ancestor_project = tmp_path / "ancestor-project"
    ancestor_project.mkdir()
    (ancestor_project / "inputs").symlink_to(outside, target_is_directory=True)
    leaf_project = tmp_path / "leaf-project"
    (leaf_project / "inputs" / "data-room").mkdir(parents=True)
    (leaf_project / "inputs" / "data-room" / "secret.csv").symlink_to(outside / "secret.csv")
    real_project = tmp_path / "real-project"
    (real_project / "inputs" / "data-room").mkdir(parents=True)
    root_link = tmp_path / "project-link"
    root_link.symlink_to(real_project, target_is_directory=True)

    for unsafe_root in (ancestor_project, leaf_project, root_link):
        monkeypatch.setattr(file_routes, "project_dir", lambda _project, root=unsafe_root: root)
        with pytest.raises(HTTPException) as exc:
            file_routes.mission_files("mission_1", kind="source", ctx=_ctx("owner", "owner"))
        assert exc.value.status_code == 404
        assert exc.value.detail["code"] == "file_unavailable"
        assert "secret" not in str(exc.value.detail).lower()


def test_file_content_supports_one_bounded_range_only_for_immutable_outputs(monkeypatch, tmp_path: Path):
    _collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    (workspace / "outputs").mkdir()
    content = b"0123456789"
    (workspace / "outputs" / "report.txt").write_bytes(content)
    deliverable = missions.create_deliverable(
        "tenant_1", "mission_1",
        {"type": "report", "name": "report.txt", "source_ref": "mission/agent", "artifact_ref": "outputs/report.txt"},
        producer_id="agent_1", artifact_bytes=content,
    )
    file_id = file_routes.output_file_id(deliverable.id, tenant_id="tenant_1", project_id="mission_1")
    response = file_routes.file_content(
        "mission_1", file_id, disposition="inline", range_header="bytes=2-5", ctx=_ctx("owner", "owner"),
    )
    assert response.status_code == 206 and response.body == b"2345"
    assert response.headers["content-range"] == "bytes 2-5/10"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == "4"
    assert response.headers["cache-control"] == "private, no-store"
    for invalid in ("bytes=0-1,3-4", "bytes=-3", "bytes=3-", "items=0-1", "bytes=99-100"):
        with pytest.raises(HTTPException) as exc:
            file_routes.file_content("mission_1", file_id, range_header=invalid, ctx=_ctx("owner", "owner"))
        assert exc.value.status_code == 416
        assert exc.value.detail["code"] == "file_range_unavailable"

    source_bytes = b"source"
    source = workspace / "inputs" / "data-room" / "source.txt"
    source.parent.mkdir(parents=True)
    source.write_bytes(source_bytes)
    monkeypatch.setattr(file_routes, "list_source_files", lambda _project: [SourceFile(
        name="source.txt", size=len(source_bytes), type="text", sha256=hashlib.sha256(source_bytes).hexdigest(),
        status="extractable", detail="Ready", row_count=None,
    )])
    source_id = file_routes.mission_files("mission_1", kind="source", ctx=_ctx("owner", "owner"))["items"][0]["id"]
    with pytest.raises(HTTPException) as source_error:
        file_routes.file_content("mission_1", source_id, range_header="bytes=0-1", ctx=_ctx("owner", "owner"))
    assert source_error.value.status_code == 416


def test_real_service_successful_assignment_output_unifies_work_and_file_provenance(monkeypatch, tmp_path: Path):
    collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    agent = missions.add_agent("tenant_1", "mission_1", {
        "name": "Fin", "role": "Analyst", "mandate": "Produce the close report", "autonomy": "assist",
    })
    task = Task(
        id="task_real_output", tenant_id="tenant_1", project_id="mission_1", title="Close", objective="Close",
        acceptance_criteria=["Report ready"], owner_id="owner", state=TaskState.WORKING,
        activity=[{"transaction_id": "txn_real_output"}],
    )
    collaboration.create_task(task)
    run = missions.create_assignment_pending_run(
        "tenant_1", "mission_1", run_id="run_real_output", transaction_id="txn_real_output",
        trigger={"type": "conversation_assignment", "task_id": task.id}, graph_revision="revision_real",
        assigned_agent_ids=[agent.id],
    )
    missions.activate_assignment_run(
        "tenant_1", "mission_1", run_id=run.id, transaction_id="txn_real_output",
    )
    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["runs"][run.id].update({
            "status": "running", "lease_owner": "worker_real", "current_agent_id": agent.id,
        }),
    )
    (workspace / "outputs").mkdir()
    content = b"Verified close report"
    (workspace / "outputs" / "close.md").write_bytes(content)
    missions.record_result(
        "tenant_1", "mission_1", run.id, "worker_real", agent.id,
        {"status": "succeeded"},
        [{"artifact_ref": "outputs/close.md", "sha256": hashlib.sha256(content).hexdigest()}],
    )
    admitted = type("Admission", (), {
        "transaction_id": "txn_real_output", "task_id": task.id, "run_id": run.id,
    })()
    rows = project_work_items(
        missions.repository, collaboration, tenant_id="tenant_1", human_id="owner",
        assignment_visible=lambda _project, transaction, _run: admitted if transaction == "txn_real_output" else None,
    )
    assert [(row["source_type"], row["source_id"], row["state"]) for row in rows] == [
        ("task", task.id, "ready_for_review"),
    ]
    deliverable = missions.deliverables("tenant_1", "mission_1")[0]
    assert deliverable.validation_evidence[0]["run_id"] == run.id
    output = file_routes.mission_files("mission_1", kind="output", ctx=_ctx("owner", "owner"))["items"][0]
    assert output["run_id"] == run.id


@pytest.mark.parametrize("malformed", [7, {"unexpected": "value"}, "not-a-collection"])
def test_public_work_and_file_routes_screen_malformed_collection_shapes(monkeypatch, tmp_path: Path, malformed):
    collaboration, missions, workspace = _mission(tmp_path, monkeypatch)
    task = Task(
        id="task_malformed", tenant_id="tenant_1", project_id="mission_1", title="Safe", objective="Safe",
        acceptance_criteria=["Done"], owner_id="owner", state=TaskState.WORKING,
        activity=[{"transaction_id": "txn_malformed"}],
    )
    collaboration.create_task(task)
    run = missions.create_run("tenant_1", "mission_1", {"type": "manual"})
    (workspace / "outputs").mkdir()
    content = b"Safe output"
    (workspace / "outputs" / "safe.md").write_bytes(content)
    output = missions.create_deliverable(
        "tenant_1", "mission_1",
        {"type": "report", "name": "safe.md", "source_ref": f"mission/run/{run.id}", "artifact_ref": "outputs/safe.md"},
        producer_id="agent_safe", artifact_bytes=content,
    )
    missions.repository.mutate("tenant_1", "mission_1", lambda state: (
        state["runs"][run.id].update({
            "assignment_transaction_id": "txn_malformed", "assigned_agent_ids": malformed,
        }),
        state["deliverables"][output.id].update({"validation_evidence": malformed}),
    ))
    admitted = type("Admission", (), {
        "transaction_id": "txn_malformed", "task_id": task.id, "run_id": run.id,
    })()
    rows = project_work_items(
        missions.repository, collaboration, tenant_id="tenant_1", human_id="owner",
        assignment_visible=lambda _project, transaction, _run: admitted if transaction == "txn_malformed" else None,
    )
    assert len(rows) == 1 and rows[0]["source_id"] == task.id
    assert rows[0]["assignee"] is None
    payload = file_routes.mission_files("mission_1", kind="all", ctx=_ctx("owner", "owner"))
    assert [item["kind"] for item in payload["items"]] == ["output"]
    assert payload["items"][0]["source_ids"] == []
    missions.repository.mutate(
        "tenant_1", "mission_1",
        lambda state: state["deliverables"][output.id].update({"validation_evidence": [{"source_ids": malformed}]}),
    )
    rebound = file_routes.mission_files("mission_1", kind="output", ctx=_ctx("owner", "owner"))
    assert rebound["items"][0]["source_ids"] == []
