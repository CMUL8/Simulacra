from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import replace
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import pytest

from simulacra.missions.projections import (
    CursorInvalidError,
    MissionSummary,
    decode_cursor,
    mark_attention_read,
    paginate,
    project_attention_items,
    project_mission_summaries,
    project_work_items,
    serialize_attention_item,
    serialize_mission_summary,
    serialize_work_item,
)
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository, make_domain_event
from simulacra.collaboration.models import ActorType, DomainEvent, Member
from simulacra.collaboration.models import Task, TaskState
from simulacra.missions import JsonMissionRepository, MissionRun, MissionService
from simulacra.operation_graph import OperationGraphStore, load_operation_graph


def _opaque_output_file_id(_project_id: str, _output_id: str) -> str:
    return "file_" + "a" * 40


def _complete_invitation_membership(
    repository: JsonCollaborationRepository, *, tenant_id: str, project_id: str, transaction_id: str,
) -> None:
    path = repository.root / ".invitation-acceptance" / tenant_id / project_id / f"{transaction_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "state": "COMPLETE",
        "transaction_id": transaction_id,
        "tenant_id": tenant_id,
        "project_id": project_id,
    }), encoding="utf-8")


def test_pending_invitation_is_invisible_across_mission_work_and_attention(tmp_path: Path):
    tenant, project = "tenant_visibility", "project_visibility"
    pending, accepted, owner = "human_pending", "human_accepted", "human_owner"
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(repository)
    room = service.create_room(
        tenant_id=tenant, project_id=project, creator_id=owner, creator_name="Owner Human",
    )
    repository.save_room(
        replace(room, members=[
            *room.members,
            Member(
                actor_id=pending, role="member", display_name="Pending Secret Name",
                transaction_id="txn_pending", visibility_state="committed",
            ),
            Member(
                actor_id=accepted, role="member", display_name="Accepted Human",
                transaction_id="txn_accepted", visibility_state="committed",
            ),
        ], revision=room.revision + 1),
        room.revision,
    )
    _complete_invitation_membership(
        repository, tenant_id=tenant, project_id=project, transaction_id="txn_accepted",
    )
    missions = JsonMissionRepository(tmp_path / "missions")
    MissionService(missions).bootstrap(tenant, project, owner, {"title": "Visible Mission"})
    task = service.create_task(
        tenant_id=tenant, project_id=project, actor_id=owner,
        title="Prepare evidence", objective="Prepare evidence", acceptance_criteria=["Evidence attached"],
    )
    task = repository.save_task(replace(task, owner_id=pending, revision=task.revision + 1), task.revision)
    repository.append_event(make_domain_event(
        event_id="evt_visibility", actor_type=ActorType.HUMAN, actor_id=pending,
        tenant_id=tenant, project_id=project, action="comment.created", result="succeeded",
        timestamp="2026-08-28T10:00:00+00:00",
        payload={"mention_ids": [pending, accepted], "body": "Please review"},
    ))

    assert project_mission_summaries(missions, repository, tenant_id=tenant, human_id=pending) == []
    assert project_work_items(missions, repository, tenant_id=tenant, human_id=pending) == []
    assert project_attention_items(missions, repository, tenant_id=tenant, human_id=pending) == []
    with pytest.raises(PermissionError, match="membership_required"):
        mark_attention_read(
            repository, tenant_id=tenant, project_id=project, human_id=pending,
            event_id="attention_pending", expected_revision=0,
        )

    accepted_summaries = project_mission_summaries(
        missions, repository, tenant_id=tenant, human_id=accepted,
    )
    owner_summaries = project_mission_summaries(
        missions, repository, tenant_id=tenant, human_id=owner,
    )
    assert [row["id"] for row in accepted_summaries] == [project]
    assert [row["id"] for row in owner_summaries] == [project]
    assert owner_summaries[0]["human_count"] == 2
    owner_work = project_work_items(missions, repository, tenant_id=tenant, human_id=owner)
    pending_owner_work = next(row for row in owner_work if row["source_id"] == task.id)
    assert pending_owner_work["assignee"] is None
    assert pending_owner_work["state"] == "needs_you"
    assert pending_owner_work["allowed_actions"] == ["open", "claim_work"]
    assert "Pending Secret Name" not in str(owner_work)
    unassigned = next(
        row for row in project_attention_items(missions, repository, tenant_id=tenant, human_id=owner)
        if row["type"] == "unassigned_work" and row["subject_id"] == task.id
    )
    assert unassigned["actionable"] is True
    assert unassigned["allowed_actions"] == ["open", "claim_work"]
    accepted_attention = project_attention_items(
        missions, repository, tenant_id=tenant, human_id=accepted,
    )
    mention = next(row for row in accepted_attention if row["type"] == "mention")
    assert mention["title"] == "A human mentioned you"
    assert "Pending Secret Name" not in str(accepted_attention)
    mark_attention_read(
        repository, tenant_id=tenant, project_id=project, human_id=accepted,
        event_id="attention_accepted", expected_revision=0,
    )

    _complete_invitation_membership(
        repository, tenant_id=tenant, project_id=project, transaction_id="txn_pending",
    )
    completed_owner_work = next(
        row for row in project_work_items(missions, repository, tenant_id=tenant, human_id=owner)
        if row["source_id"] == task.id
    )
    assert completed_owner_work["assignee"] == {
        "id": pending, "kind": "human", "display_name": "Pending Secret Name",
    }
    assert completed_owner_work["state"] == "in_progress"
    assert completed_owner_work["allowed_actions"] == ["open"]
    completed_unassigned = next(
        row for row in project_attention_items(missions, repository, tenant_id=tenant, human_id=owner)
        if row["id"] == unassigned["id"]
    )
    assert completed_unassigned["actionable"] is False
    assert completed_unassigned["allowed_actions"] == ["open"]
    assert any(
        row["type"] == "assignment" and row["subject_id"] == task.id
        for row in project_attention_items(missions, repository, tenant_id=tenant, human_id=pending)
    )


def test_work_projection_has_one_record_per_source_and_server_actions(tmp_path: Path):
    tenant, project, human = "tenant_work", "project_work", "human_work"
    collaboration = JsonCollaborationRepository(tmp_path / "collaboration")
    collaboration_service = CollaborationService(collaboration)
    room = collaboration_service.create_room(tenant_id=tenant, project_id=project, creator_id=human)
    mission_repository = JsonMissionRepository(tmp_path / "missions")
    mission_service = MissionService(mission_repository)
    mission_service.bootstrap(
        tenant, project, human,
        {"title": "Quarterly close", "objective": "Close accurately", "verifier_ids": [human]},
    )
    task = collaboration_service.create_task(
        tenant_id=tenant,
        project_id=project,
        actor_id=human,
        title="Check exceptions",
        objective="Resolve exceptions",
        acceptance_criteria=["Every exception has evidence"],
    )
    run = mission_service.create_run(tenant, project, {"type": "manual"})
    mission_service.gate(tenant, project, run.id, "checkpoint_required", "Review this decision")
    mission_service.create_deliverable(
        tenant,
        project,
        {"type": "report", "name": "close.md", "source_ref": f"mission/run/{run.id}"},
        producer_id="agent_finance",
        artifact_bytes=b"verified close",
    )

    rows = project_work_items(
        mission_repository,
        collaboration,
        tenant_id=tenant,
        human_id=human,
        output_file_identity=_opaque_output_file_id,
    )

    identities = [(row["source_type"], row["source_id"]) for row in rows]
    assert len(identities) == len(set(identities))
    assert {kind for kind, _ in identities} == {"task", "run", "approval", "output"}
    assert next(row for row in rows if row["source_id"] == task.id)["allowed_actions"] == ["open", "claim_work"]
    assert next(row for row in rows if row["source_type"] == "approval")["allowed_actions"] == ["open", "decide_checkpoint"]
    assert next(row for row in rows if row["source_type"] == "output")["allowed_actions"] == ["open", "verify_output"]
    task_row = next(row for row in rows if row["source_id"] == task.id)
    approval_row = next(row for row in rows if row["source_type"] == "approval")
    output_row = next(row for row in rows if row["source_type"] == "output")
    approval_raw = mission_repository.get_collection_item(tenant, project, "approvals", approval_row["source_id"])
    output_raw = mission_repository.get_collection_item(tenant, project, "deliverables", output_row["source_id"])
    current_run = mission_repository.get_collection_item(tenant, project, "runs", run.id)
    assert task_row["action_targets"] == {
        "claim_work": {"kind": "task", "id": task.id, "revision": task.revision},
    }
    assert approval_row["action_targets"] == {
        "decide_checkpoint": {
            "kind": "approval", "id": approval_row["source_id"], "revision": approval_raw["revision"],
            "run_revision": current_run["revision"],
        },
    }
    assert output_row["action_targets"] == {
        "verify_output": {
            "kind": "output", "id": output_row["source_id"], "revision": output_raw["version"],
            "file_id": _opaque_output_file_id(project, output_row["source_id"]),
        },
    }
    assert not any(word in str(rows).lower() for word in ("execution_profile", "artifact_ref", "provider", "runtime"))


def test_complete_assignment_projects_one_task_and_suppresses_its_linked_run(tmp_path: Path):
    tenant, project, human = "tenant_assignment", "project_assignment", "human_assignment"
    collaboration = JsonCollaborationRepository(tmp_path / "collaboration")
    CollaborationService(collaboration).create_room(tenant_id=tenant, project_id=project, creator_id=human)
    missions = JsonMissionRepository(tmp_path / "missions")
    MissionService(missions).bootstrap(tenant, project, human, {"title": "Close", "objective": "Close"})
    task = Task(
        id="task_assignment", tenant_id=tenant, project_id=project, title="Reconcile", objective="Reconcile invoices",
        acceptance_criteria=["Evidence attached"], owner_id=human, state=TaskState.WORKING,
        activity=[{"transaction_id": "txn_assignment"}],
    )
    collaboration.create_task(task)
    linked = MissionRun(
        id="run_assignment", tenant_id=tenant, project_id=project,
        mission_id=missions.get_mission(tenant, project)["id"],
        trigger_snapshot={"type": "conversation_assignment", "transaction_id": "txn_assignment", "task_id": task.id},
        contract_revision="revision_current", status="running", assignment_transaction_id="txn_assignment",
        progress={"completed": 1, "total": 2},
    )
    standalone = MissionRun(
        id="run_manual", tenant_id=tenant, project_id=project, mission_id=linked.mission_id,
        trigger_snapshot={"type": "manual"}, contract_revision="revision_current", status="running",
    )
    missions.mutate(
        tenant, project,
        lambda state: state["runs"].update({linked.id: linked.to_dict(), standalone.id: standalone.to_dict()}),
    )

    rows = project_work_items(
        missions, collaboration, tenant_id=tenant, human_id=human,
        assignment_visible=lambda _project, transaction, _run_id: SimpleNamespace(
            transaction_id=transaction, task_id=task.id, run_id=linked.id,
        ) if transaction == "txn_assignment" else None,
    )

    assert ("task", task.id) in {(row["source_type"], row["source_id"]) for row in rows}
    assert ("run", linked.id) not in {(row["source_type"], row["source_id"]) for row in rows}
    assert ("run", standalone.id) in {(row["source_type"], row["source_id"]) for row in rows}
    assert missions.get_collection_item(tenant, project, "runs", linked.id)["progress"] == {"completed": 1, "total": 2}


def test_complete_assignment_requires_exact_reserved_task_and_run_and_suppresses_linked_children(tmp_path: Path):
    tenant, project, human = "tenant_bound", "project_bound", "human_bound"
    collaboration = JsonCollaborationRepository(tmp_path / "collaboration")
    CollaborationService(collaboration).create_room(tenant_id=tenant, project_id=project, creator_id=human)
    missions = JsonMissionRepository(tmp_path / "missions")
    service = MissionService(missions)
    mission = service.bootstrap(tenant, project, human, {"title": "Bound", "objective": "Bound"})
    wrong = Task(
        id="task_wrong", tenant_id=tenant, project_id=project, title="Wrong", objective="Wrong",
        acceptance_criteria=["Done"], owner_id=human, state=TaskState.WORKING,
        activity=[{"transaction_id": "txn_bound"}],
    )
    right = replace(wrong, id="task_right", title="Right")
    collaboration.create_task(wrong)
    collaboration.create_task(right)
    linked = MissionRun(
        id="run_linked", tenant_id=tenant, project_id=project, mission_id=mission.id,
        trigger_snapshot={"type": "conversation_assignment", "transaction_id": "txn_bound", "task_id": right.id},
        contract_revision="revision_current", status="awaiting_approval", assignment_transaction_id="txn_bound",
    )
    standalone = MissionRun(
        id="run_standalone", tenant_id=tenant, project_id=project, mission_id=mission.id,
        trigger_snapshot={"type": "manual"}, contract_revision="revision_current", status="awaiting_approval",
    )
    missions.mutate(tenant, project, lambda state: state.update({
        "runs": {linked.id: linked.to_dict(), standalone.id: standalone.to_dict()},
        "approvals": {
            "approval_linked": {"id": "approval_linked", "run_id": linked.id, "status": "pending", "revision": 1},
            "approval_standalone": {"id": "approval_standalone", "run_id": standalone.id, "status": "pending", "revision": 1},
        },
        "deliverables": {
            "deliverable_linked": {"id": "deliverable_linked", "name": "Linked", "source_ref": f"mission/run/{linked.id}", "state": "awaiting_verification", "revision": 1},
            "deliverable_standalone": {"id": "deliverable_standalone", "name": "Standalone", "source_ref": f"mission/run/{standalone.id}", "state": "awaiting_verification", "revision": 1},
        },
    }))
    result = SimpleNamespace(transaction_id="txn_bound", task_id=right.id, run_id=linked.id)

    rows = project_work_items(
        missions, collaboration, tenant_id=tenant, human_id=human,
        assignment_visible=lambda _project, transaction, _run: result if transaction == "txn_bound" else None,
    )
    identities = {(row["source_type"], row["source_id"]) for row in rows}
    assert ("task", wrong.id) not in identities
    assert ("task", right.id) in identities
    assert ("run", linked.id) not in identities
    assert ("approval", "approval_linked") not in identities
    assert ("output", "deliverable_linked") not in identities
    assert ("run", standalone.id) in identities
    assert ("approval", "approval_standalone") in identities
    assert ("output", "deliverable_standalone") in identities


def test_complete_assignment_task_reflects_linked_run_state_assignee_and_actions(tmp_path: Path):
    tenant, project, human = "tenant_enriched", "project_enriched", "human_enriched"
    collaboration = JsonCollaborationRepository(tmp_path / "collaboration")
    CollaborationService(collaboration).create_room(tenant_id=tenant, project_id=project, creator_id=human)
    missions = JsonMissionRepository(tmp_path / "missions")
    service = MissionService(missions)
    mission = service.bootstrap(tenant, project, human, {
        "title": "Enriched", "objective": "Enriched", "verifier_ids": [human],
    })
    agent = service.add_agent(tenant, project, {
        "name": "Fin", "role": "Analyst", "mandate": "Finish the work", "autonomy": "assist",
    })
    task = Task(
        id="task_enriched", tenant_id=tenant, project_id=project, title="Reconcile", objective="Reconcile",
        acceptance_criteria=["Done"], owner_id=human, state=TaskState.WORKING,
        activity=[{"transaction_id": "txn_enriched"}],
    )
    collaboration.create_task(task)
    run = MissionRun(
        id="run_enriched", tenant_id=tenant, project_id=project, mission_id=mission.id,
        trigger_snapshot={"type": "conversation_assignment", "transaction_id": "txn_enriched", "task_id": task.id},
        contract_revision="revision_current", status="running", assignment_transaction_id="txn_enriched",
        assigned_agent_ids=[agent.id], current_agent_id=agent.id,
    )
    missions.mutate(tenant, project, lambda state: (
        state["mission"].update({"approved_contract_revision": "revision_current"}),
        state["runs"].update({run.id: run.to_dict()}),
    ))
    admitted = SimpleNamespace(transaction_id="txn_enriched", task_id=task.id, run_id=run.id)

    def projected() -> dict:
        rows = project_work_items(
            missions, collaboration, tenant_id=tenant, human_id=human,
            assignment_visible=lambda _project, transaction, _run: admitted if transaction == "txn_enriched" else None,
            output_file_identity=_opaque_output_file_id,
        )
        assert [(row["source_type"], row["source_id"]) for row in rows] == [("task", task.id)]
        return rows[0]

    running = projected()
    assert (running["state"], running["assignee"], running["allowed_actions"]) == (
        "in_progress", {"id": agent.id, "display_name": "Fin", "kind": "agent"}, ["open"],
    )

    missions.mutate(tenant, project, lambda state: (
        state["runs"][run.id].update({"status": "awaiting_approval"}),
        state["approvals"].update({"approval_enriched": {
            "id": "approval_enriched", "run_id": run.id, "status": "pending", "revision": 3,
        }}),
    ))
    awaiting_decision = projected()
    assert (awaiting_decision["state"], awaiting_decision["assignee"]["id"], awaiting_decision["allowed_actions"]) == (
        "needs_you", agent.id, ["open", "decide_checkpoint"],
    )
    assert awaiting_decision["action_targets"] == {
        "decide_checkpoint": {
            "kind": "approval", "id": "approval_enriched", "revision": 3, "run_revision": run.revision,
        },
    }

    missions.mutate(tenant, project, lambda state: (
        state["runs"][run.id].update({"status": "failed"}),
        state["approvals"]["approval_enriched"].update({"status": "rejected"}),
    ))
    failed = projected()
    assert (failed["state"], failed["assignee"]["id"], failed["allowed_actions"]) == (
        "stopped", agent.id, ["open", "retry_work"],
    )
    assert failed["action_targets"] == {
        "retry_work": {"kind": "run", "id": run.id, "revision": run.revision},
    }

    missions.mutate(tenant, project, lambda state: (
        state["runs"][run.id].update({"status": "succeeded", "current_agent_id": None}),
        state["deliverables"].update({"deliverable_enriched": {
            "id": "deliverable_enriched", "name": "Close report", "source_ref": f"mission/run/{run.id}",
            "state": "awaiting_verification", "producer_id": agent.id, "version": 1, "revision": 1,
        }}),
    ))
    awaiting = projected()
    assert (awaiting["state"], awaiting["assignee"]["id"], awaiting["allowed_actions"]) == (
        "ready_for_review", agent.id, ["open", "verify_output"],
    )
    assert awaiting["action_targets"] == {
        "verify_output": {
            "kind": "output", "id": "deliverable_enriched", "revision": 1,
            "file_id": _opaque_output_file_id(project, "deliverable_enriched"),
        },
    }

    missions.mutate(tenant, project, lambda state: state["deliverables"]["deliverable_enriched"].update({"state": "verified"}))
    completed = projected()
    assert (completed["state"], completed["assignee"]["id"], completed["allowed_actions"]) == (
        "done", agent.id, ["open"],
    )


def test_failed_run_retry_requires_exact_current_approved_revision(tmp_path: Path):
    tenant, project, human = "tenant_retry", "project_retry", "human_retry"
    collaboration = JsonCollaborationRepository(tmp_path / "collaboration")
    CollaborationService(collaboration).create_room(tenant_id=tenant, project_id=project, creator_id=human)
    missions = JsonMissionRepository(tmp_path / "missions")
    mission = MissionService(missions).bootstrap(tenant, project, human, {"title": "Retry", "objective": "Retry safely"})
    current = MissionRun(
        id="run_current", tenant_id=tenant, project_id=project, mission_id=mission.id,
        trigger_snapshot={"type": "manual"}, contract_revision="revision_current", status="failed",
    )
    stale = MissionRun(
        id="run_stale", tenant_id=tenant, project_id=project, mission_id=mission.id,
        trigger_snapshot={"type": "manual"}, contract_revision="revision_old", status="failed",
    )
    def seed(state):
        state["mission"]["approved_contract_revision"] = "revision_current"
        state["runs"].update({current.id: current.to_dict(), stale.id: stale.to_dict()})
    missions.mutate(tenant, project, seed)
    current_mission = missions.get_mission(tenant, project)

    rows = project_work_items(missions, collaboration, tenant_id=tenant, human_id=human)
    actions = {row["source_id"]: row["allowed_actions"] for row in rows if row["source_type"] == "run"}
    targets = {row["source_id"]: row["action_targets"] for row in rows if row["source_type"] == "run"}
    assert actions[current.id] == ["open", "retry_work"]
    assert actions[stale.id] == ["open", "review_plan"]
    assert targets[current.id] == {"retry_work": {"kind": "run", "id": current.id, "revision": current.revision}}
    assert targets[stale.id] == {
        "review_plan": {"kind": "plan", "id": stale.contract_revision, "revision": current_mission["revision"]},
    }


def test_task_update_and_review_actions_expose_only_exact_legal_next_states(tmp_path: Path):
    tenant, project, human = "tenant_task_actions", "project_task_actions", "human_task_actions"
    collaboration = JsonCollaborationRepository(tmp_path / "collaboration")
    CollaborationService(collaboration).create_room(tenant_id=tenant, project_id=project, creator_id=human)
    missions = JsonMissionRepository(tmp_path / "missions")
    MissionService(missions).bootstrap(tenant, project, human, {"title": "Review", "objective": "Review"})
    ready = Task(
        id="task_ready", tenant_id=tenant, project_id=project, title="Ready", objective="Ready",
        acceptance_criteria=["Done"], owner_id=human, state=TaskState.READY,
    )
    working = Task(
        id="task_working", tenant_id=tenant, project_id=project, title="Work", objective="Work",
        acceptance_criteria=["Done"], owner_id=human, state=TaskState.WORKING,
    )
    reviewing = replace(working, id="task_reviewing", state=TaskState.IN_REVIEW, revision=4)
    collaboration.create_task(ready)
    collaboration.create_task(working)
    collaboration.create_task(reviewing)

    rows = project_work_items(missions, collaboration, tenant_id=tenant, human_id=human)
    by_id = {row["source_id"]: row for row in rows}
    assert by_id[ready.id]["action_targets"] == {
        "update_work": {
            "kind": "task", "id": ready.id, "revision": ready.revision,
            "next_states": ["working", "blocked", "cancelled"],
        },
    }
    assert by_id[working.id]["action_targets"] == {
        "update_work": {
            "kind": "task", "id": working.id, "revision": working.revision,
            "next_states": ["in_review", "blocked", "failed", "cancelled"],
        },
    }
    assert by_id[reviewing.id]["action_targets"] == {
        "update_work": {
            "kind": "task", "id": reviewing.id, "revision": reviewing.revision,
            "next_states": ["working", "failed", "cancelled"],
        },
    }


def test_in_review_task_owner_never_receives_review_work_while_distinct_reviewer_does(tmp_path: Path):
    tenant, project = "tenant_distinct_reviewer", "project_distinct_reviewer"
    owner, reviewer = "human_task_owner", "human_task_reviewer"
    collaboration = JsonCollaborationRepository(tmp_path / "collaboration")
    service = CollaborationService(collaboration)
    room = service.create_room(tenant_id=tenant, project_id=project, creator_id=owner)
    service.add_member(
        tenant_id=tenant,
        project_id=project,
        actor_id=owner,
        member_id=reviewer,
        role="reviewer",
        expected_revision=room.revision,
    )
    missions = JsonMissionRepository(tmp_path / "missions")
    MissionService(missions).bootstrap(tenant, project, owner, {"title": "Review", "objective": "Review"})
    task = Task(
        id="task_distinct_reviewer",
        tenant_id=tenant,
        project_id=project,
        title="Review safely",
        objective="Require a second human",
        acceptance_criteria=["The owner cannot review their own work"],
        owner_id=owner,
        state=TaskState.IN_REVIEW,
        revision=4,
    )
    collaboration.create_task(task)

    owner_row = next(
        row for row in project_work_items(missions, collaboration, tenant_id=tenant, human_id=owner)
        if row["source_id"] == task.id
    )
    reviewer_row = next(
        row for row in project_work_items(missions, collaboration, tenant_id=tenant, human_id=reviewer)
        if row["source_id"] == task.id
    )

    assert owner_row["allowed_actions"] == ["open", "update_work"]
    assert owner_row["action_targets"] == {
        "update_work": {
            "kind": "task",
            "id": task.id,
            "revision": task.revision,
            "next_states": ["working", "failed", "cancelled"],
        },
    }
    assert reviewer_row["allowed_actions"] == ["open", "review_work"]
    assert reviewer_row["action_targets"] == {
        "review_work": {"kind": "task", "id": task.id, "revision": task.revision},
    }


@pytest.mark.parametrize(
    "target",
    [
        {"kind": "task", "id": "task_safe", "revision": 2},
        {"kind": "task", "id": "task_safe", "revision": 2, "next_states": "working"},
        {"kind": "task", "id": "task_safe", "revision": 2, "next_states": ["working", "root_access"]},
        {"kind": "task", "id": "task_safe", "revision": 2, "next_states": ["working", "working"]},
        {"kind": "task", "id": "task_safe", "revision": 2, "next_states": ["done"]},
    ],
)
def test_work_serializer_screens_malformed_or_non_executable_next_states(target):
    screened = serialize_work_item({
        "source_type": "task", "source_id": "task_safe", "revision": 2, "mission_id": "mission_safe",
        "summary": "Safe", "title": "Safe", "state": "in_progress", "assignee": None,
        "created_at": "2026-01-02T09:00:00+00:00", "updated_at": "2026-01-02T09:00:00+00:00",
        "allowed_actions": ["open", "update_work"],
        "action_targets": {"update_work": target},
    })
    assert screened["allowed_actions"] == ["open"]
    assert screened["action_targets"] == {}


@pytest.mark.parametrize(
    "file_id",
    [None, "deliverable_1", "file_short", "file_" + "g" * 40, "file_" + "a" * 40 + "extra"],
)
def test_work_serializer_screens_malformed_verify_file_identity(file_id):
    target = {"kind": "output", "id": "deliverable_1", "revision": 2}
    if file_id is not None:
        target["file_id"] = file_id
    screened = serialize_work_item({
        "source_type": "output", "source_id": "deliverable_1", "revision": 2, "mission_id": "mission_safe",
        "summary": "Safe", "title": "Safe", "state": "ready_for_review", "assignee": None,
        "created_at": "2026-01-02T09:00:00+00:00", "updated_at": "2026-01-02T09:00:00+00:00",
        "allowed_actions": ["open", "verify_output"],
        "action_targets": {"verify_output": target},
    })
    assert screened["allowed_actions"] == ["open"]
    assert screened["action_targets"] == {}


def test_malformed_approval_run_id_and_action_target_are_screened(tmp_path: Path):
    tenant, project, human = "tenant_bad_target", "project_bad_target", "human_bad_target"
    collaboration = JsonCollaborationRepository(tmp_path / "collaboration")
    CollaborationService(collaboration).create_room(tenant_id=tenant, project_id=project, creator_id=human)
    missions = JsonMissionRepository(tmp_path / "missions")
    MissionService(missions).bootstrap(tenant, project, human, {"title": "Safe", "objective": "Safe"})
    missions.mutate(tenant, project, lambda state: state["approvals"].update({
        "approval_bad": {"id": "approval_bad", "run_id": ["run_bad"], "status": "pending", "revision": 1},
    }))
    rows = project_work_items(missions, collaboration, tenant_id=tenant, human_id=human)
    assert len(rows) == 1
    assert rows[0]["allowed_actions"] == ["open"]
    assert rows[0]["action_targets"] == {}

    screened = serialize_work_item({
        "source_type": "run", "source_id": "run_safe", "revision": 1, "mission_id": project,
        "summary": "Safe", "title": "Safe", "state": "stopped", "assignee": None,
        "created_at": "2026-01-02T09:00:00+00:00", "updated_at": "2026-01-02T09:00:00+00:00",
        "allowed_actions": ["open", "retry_work", "review_plan"],
        "action_targets": {
            "retry_work": {"kind": "run", "id": "../bad", "revision": 0},
            "not_allowed": {"kind": "run", "id": "run_safe", "revision": 1},
        },
    })
    assert screened["allowed_actions"] == ["open"]
    assert screened["action_targets"] == {}


def test_workplace_public_serializers_allow_only_contract_fields():
    summary = serialize_mission_summary(MissionSummary(
        id="mission_1", title="Ship", outcome_summary="Release", public_state="active",
        updated_at="2026-01-02T09:00:00+00:00", human_count=1, agent_count=2,
        active_work_count=1, needs_human_count=0, verified_output_count=1,
        current_human_permissions=["read", "approve", "api_key=secret", {"runtime": "hidden"}],
    ))
    work = serialize_work_item({"source_type": "task", "id": "task_1", "revision": 2,
        "mission_id": "mission_1", "summary": "Needs review", "title": "Review", "state": "ready", "assignee": {"id": "human_1", "provider": "nope", "display_name": {"api_key": "secret", "runtime": {"host": "nope"}}},
        "created_at": "2026-01-02T09:00:00+00:00", "updated_at": "2026-01-02T09:01:00+00:00",
        "allowed_actions": ["approve", "reject", "api_key=secret", {"runtime": "hidden"}], "action_targets": {}, "provider": "nope", "runtime": {"host": "nope"}})
    attention = serialize_attention_item({"id": "attention_1", "mission_id": "mission_1", "type": "decision_required", "title": "Review needed", "summary": "Approve the output", "source_event_id": "evt_1", "subject_id": "task_1",
        "priority": 1, "actionable": True, "read": False, "revision": 1,
        "created_at": "2026-01-02T09:00:00+00:00", "updated_at": "2026-01-02T09:01:00+00:00",
        "deep_link": "/missions/mission_1", "allowed_actions": ["approve", "mark_read", "api_key=secret", {"runtime": "hidden"}], "raw_exception": "nope"})
    assert set(summary) == set(MissionSummary.__dataclass_fields__)
    assert set(work) == {"source_type", "source_id", "revision", "mission_id", "summary", "title", "state", "assignee", "created_at", "updated_at", "allowed_actions", "action_targets"}
    assert set(attention) == {"id", "mission_id", "type", "title", "summary", "source_event_id", "subject_id", "priority", "actionable", "read", "revision", "created_at", "updated_at", "deep_link", "allowed_actions"}
    assert work["assignee"] == {"id": "human_1"}
    assert summary["current_human_permissions"] == ["read", "approve"]
    assert work["allowed_actions"] == []
    assert work["action_targets"] == {}
    assert attention["allowed_actions"] == ["approve", "mark_read"]
    assert not any(word in str([summary, work, attention]).lower() for word in ("provider", "runtime", "host", "raw_exception"))


def test_cursor_rejects_tamper_and_preserves_page_boundary():
    rows = [{"id": f"mission_{index}", "updated_at": f"2026-01-02T09:0{9-index}:00+00:00"} for index in range(5)]
    first, cursor = paginate(rows, endpoint="missions", scope="tenant_1", limit=2, secret=b"test-secret")
    assert [row["id"] for row in first] == ["mission_0", "mission_1"]
    with pytest.raises(CursorInvalidError, match="cursor_invalid"):
        decode_cursor(cursor + "x", endpoint="missions", scope="tenant_1", secret=b"test-secret")
    for noncanonical in (cursor + "!!!!", cursor + "A", cursor + "=", cursor.replace(".", "=.", 1), cursor[:-1]):
        with pytest.raises(CursorInvalidError, match="cursor_invalid"):
            decode_cursor(noncanonical, endpoint="missions", scope="tenant_1", secret=b"test-secret")
    assert decode_cursor(cursor, endpoint="missions", scope="tenant_1", secret=b"test-secret") == ("2026-01-02T09:08:00+00:00", "mission_1")
    rows.append({"id": "mission_late", "updated_at": "2026-01-02T09:30:00+00:00"})
    second, _ = paginate(rows, endpoint="missions", scope="tenant_1", limit=2, cursor=cursor, secret=b"test-secret")
    assert [row["id"] for row in second] == ["mission_2", "mission_3"]
    assert set(row["id"] for row in first).isdisjoint(row["id"] for row in second)


def test_cursor_rejects_empty_secrets_and_signed_noncanonical_json():
    with pytest.raises(ValueError):
        from simulacra.missions.projections import encode_cursor
        encode_cursor(endpoint="missions", scope="tenant_1", sort_key="stamp", item_id="mission_1", secret=" ")
    for secret in ("", " ", b"", b" ", None, 1):
        with pytest.raises(CursorInvalidError, match="cursor_invalid"):
            decode_cursor("e30.e30", endpoint="missions", scope="tenant_1", secret=secret)

    def forge(payload: bytes) -> str:
        signature = hmac.new(b"test-secret", payload, hashlib.sha256).digest()
        encode = lambda value: base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")
        return f"{encode(payload)}.{encode(signature)}"

    canonical = b'{"e":"missions","i":"mission_1","k":"stamp","s":"tenant_1","v":1}'
    for payload in (
        b'{"e":"missions", "i":"mission_1","k":"stamp","s":"tenant_1","v":1}',
        b'{"v":1,"e":"missions","i":"mission_1","k":"stamp","s":"tenant_1"}',
        b'{"e":"missions","e":"missions","i":"mission_1","k":"stamp","s":"tenant_1","v":1}',
        b'{"e":"missions","i":"mission_1","k":"stamp","s":"tenant_1","v":1,"x":1}',
    ):
        with pytest.raises(CursorInvalidError, match="cursor_invalid"):
            decode_cursor(forge(payload), endpoint="missions", scope="tenant_1", secret=b"test-secret")
    assert decode_cursor(forge(canonical), endpoint="missions", scope="tenant_1", secret=b"test-secret") == ("stamp", "mission_1")


def test_mission_summary_pagination_is_membership_filtered(tmp_path, monkeypatch):
    mission_repository = JsonMissionRepository(tmp_path / "missions")
    collaboration_repository = JsonCollaborationRepository(tmp_path / "rooms")
    missions = MissionService(mission_repository)
    rooms = CollaborationService(collaboration_repository)
    for project_id, title in (("project_old", "Older"), ("project_new", "Newer"), ("project_hidden", "Hidden")):
        rooms.create_room(tenant_id="tenant_demo", project_id=project_id, creator_id="owner")
        missions.bootstrap("tenant_demo", project_id, "owner", {"title": title})
    for project_id in ("project_old", "project_new"):
        room = collaboration_repository.get_room("tenant_demo", project_id)
        rooms.add_member(tenant_id="tenant_demo", project_id=project_id, actor_id="owner", member_id="reader", role="member", expected_revision=room.revision)
    mission_repository.mutate("tenant_demo", "project_old", lambda state: state["mission"].update({"updated_at": "2026-01-02T09:00:00+00:00"}))
    mission_repository.mutate("tenant_demo", "project_new", lambda state: state["mission"].update({"updated_at": "2026-01-02T10:00:00+00:00"}))
    mission_repository.mutate("tenant_demo", "project_hidden", lambda state: state["mission"].update({"updated_at": "2026-01-02T09:30:00+00:00"}))

    loaded: list[str] = []
    original_get_mission = mission_repository.get_mission
    def get_mission(tenant_id: str, project_id: str):
        loaded.append(project_id)
        return original_get_mission(tenant_id, project_id)
    monkeypatch.setattr(mission_repository, "get_mission", get_mission)

    rows = project_mission_summaries(
        mission_repository, collaboration_repository, tenant_id="tenant_demo", human_id="reader",
    )
    page, cursor = paginate(rows, endpoint="missions", scope="tenant_demo:reader:active", limit=1, secret=b"test-secret")
    assert [row["id"] for row in page] == ["project_new"]
    assert cursor is not None
    second, _ = paginate(rows, endpoint="missions", scope="tenant_demo:reader:active", limit=1, cursor=cursor, secret=b"test-secret")
    assert [row["id"] for row in second] == ["project_old"]
    assert "project_hidden" not in {row["id"] for row in rows}
    assert "project_hidden" not in loaded

    current = collaboration_repository.get_room("tenant_demo", "project_new")
    collaboration_repository.save_room(
        replace(current, members=[member for member in current.members if member.actor_id != "reader"], revision=current.revision + 1),
        current.revision,
    )
    after_revocation = project_mission_summaries(
        mission_repository, collaboration_repository, tenant_id="tenant_demo", human_id="reader",
    )
    assert [row["id"] for row in after_revocation] == ["project_old"]


def test_attention_history_keeps_logical_identity_and_private_receipt(tmp_path):
    mission_repository = JsonMissionRepository(tmp_path / "missions")
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(repository)
    room = service.create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner")
    service.add_member(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", member_id="reader", role="member", expected_revision=room.revision)
    MissionService(mission_repository).bootstrap("tenant_demo", "project_demo", "owner", {"title": "Demo"})
    task = service.create_task(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", title="Review", objective="Review", acceptance_criteria=["Done"], owner_id="reader")
    first = next(item for item in project_attention_items(mission_repository, repository, tenant_id="tenant_demo", human_id="reader") if item["type"] == "assignment")
    mark_attention_read(repository, tenant_id="tenant_demo", project_id="project_demo", human_id="reader", event_id=first["id"], expected_revision=0, clock=lambda: "2026-01-02T10:00:00+00:00")
    service.transition_task(tenant_id="tenant_demo", project_id="project_demo", task_id=task.id, actor_id="reader", to_state="working", expected_revision=task.revision)
    after = next(item for item in project_attention_items(mission_repository, repository, tenant_id="tenant_demo", human_id="reader") if item["type"] == "assignment")
    assert after["id"] == first["id"] and after["read"] is True and after["revision"] == 1


def test_attention_reassignment_keeps_event_history_but_only_current_owner_is_actionable(tmp_path):
    missions = JsonMissionRepository(tmp_path / "missions")
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(repository)
    room = service.create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner")
    for human in ("reader", "other"):
        room = service.add_member(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", member_id=human, role="member", expected_revision=room.revision)
    MissionService(missions).bootstrap("tenant_demo", "project_demo", "owner", {"title": "Demo"})
    task = service.create_task(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", title="Review", objective="Review", acceptance_criteria=["Done"], owner_id="reader")
    # The current Task owner is deliberately not the source of history: two
    # assignment events for the same human must remain distinct rows.
    for event_id, assignee, stamp in (
        ("evt_assign_away", "other", "2026-01-02T10:00:00+00:00"),
        ("evt_assign_back", "reader", "2026-01-02T11:00:00+00:00"),
    ):
        repository.append_event(DomainEvent(
            id=event_id, actor_type=ActorType.HUMAN, actor_id="owner", tenant_id="tenant_demo", project_id="project_demo",
            task_id=task.id, operation_graph_version=None, application_version=None, environment_id=None,
            action="task.claimed", result="ok", timestamp=stamp, correlation_id=None, trace_id=None,
            payload={"assignee_id": assignee, "target_type": "task", "target_id": task.id},
        ))
    rows = [row for row in project_attention_items(missions, repository, tenant_id="tenant_demo", human_id="reader") if row["type"] == "assignment"]
    assert len(rows) == 2
    assert len({row["id"] for row in rows}) == 2
    assert sum(row["actionable"] for row in rows) == 1
    assert {row["source_event_id"] for row in rows} == {"evt_assign_back", next(event.id for event in repository.list_events("tenant_demo", "project_demo") if event.action == "task.created")}


def test_attention_all_retains_resolved_history_but_skips_successful_runs(tmp_path):
    mission_repository = JsonMissionRepository(tmp_path / "missions")
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(repository)
    service.create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner")
    MissionService(mission_repository).bootstrap("tenant_demo", "project_demo", "owner", {"title": "Demo"})
    mission_repository.mutate("tenant_demo", "project_demo", lambda state: state.update({
        "approvals": {"approval_done": {"status": "approved", "created_at": "2026-01-02T09:00:00+00:00"}},
        "deliverables": {"output_done": {"state": "verified", "name": "Output", "created_at": "2026-01-02T09:00:00+00:00"}},
        "runs": {
            "run_success": {"status": "completed", "created_at": "2026-01-02T09:00:00+00:00"},
            "run_error_only": {"status": "completed", "error": {"code": "failed"}, "created_at": "2026-01-02T09:00:00+00:00"},
        },
    }))
    rows = project_attention_items(mission_repository, repository, tenant_id="tenant_demo", human_id="owner")
    assert {row["type"] for row in rows} >= {"decision_required", "output_verification"}
    assert not any(row["source_event_id"] == "run:run_success" for row in rows)
    assert not any(row["source_event_id"] == "run:run_error_only" for row in rows)


def test_retry_history_uses_durable_failure_event_after_retry_clears_error(tmp_path):
    mission_repository = JsonMissionRepository(tmp_path / "missions")
    collaboration_repository = JsonCollaborationRepository(tmp_path / "rooms")
    CollaborationService(collaboration_repository).create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner")
    service = MissionService(mission_repository)
    service.bootstrap("tenant_demo", "project_demo", "owner", {"title": "Demo"})
    agent = service.add_agent("tenant_demo", "project_demo", {"name": "Agent", "role": "builder", "mandate": "Build"})
    run = service.create_run("tenant_demo", "project_demo", {"type": "manual"})
    claimed = service.claim_next("tenant_demo", "project_demo", "worker")
    assert claimed is not None and claimed.id == run.id
    service.mark_agent_started("tenant_demo", "project_demo", run.id, agent.id, "worker")
    failed = service.record_result("tenant_demo", "project_demo", run.id, "worker", agent.id, {"status": "failed"}, [])
    retried = service.retry_run("tenant_demo", "project_demo", run.id, failed.revision, "revision_approved")
    assert retried.error is None and retried.status == "queued"
    rows = project_attention_items(mission_repository, collaboration_repository, tenant_id="tenant_demo", human_id="owner")
    retry = next(row for row in rows if row["source_event_id"] == f"run:{run.id}")
    assert retry["type"] == "retry_required" and retry["actionable"] is False
    # A newly completed run has no durable failure event and cannot appear.
    service.cancel_run("tenant_demo", "project_demo", run.id, retried.revision)
    succeeded = service.create_run("tenant_demo", "project_demo", {"type": "manual"})
    assert service.claim_next("tenant_demo", "project_demo", "worker").id == succeeded.id
    service.mark_agent_started("tenant_demo", "project_demo", succeeded.id, agent.id, "worker")
    service.record_result("tenant_demo", "project_demo", succeeded.id, "worker", agent.id, {"status": "succeeded"}, [])
    rows = project_attention_items(mission_repository, collaboration_repository, tenant_id="tenant_demo", human_id="owner")
    assert not any(row["source_event_id"] == f"run:{succeeded.id}" for row in rows)


def test_legacy_unassigned_work_synthesizes_only_after_unassigned_origin(tmp_path, monkeypatch):
    missions = JsonMissionRepository(tmp_path / "missions")
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(repository)
    service.create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner")
    MissionService(missions).bootstrap("tenant_demo", "project_demo", "owner", {"title": "Demo"})
    unassigned = service.create_task(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", title="Legacy", objective="Work", acceptance_criteria=["Done"])
    service.claim_task(tenant_id="tenant_demo", project_id="project_demo", task_id=unassigned.id, actor_id="owner", expected_revision=unassigned.revision)
    assigned = service.create_task(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", title="Always assigned", objective="Work", acceptance_criteria=["Done"], owner_id="owner")
    monkeypatch.setattr(repository, "list_events", lambda *_: [])
    rows = project_attention_items(missions, repository, tenant_id="tenant_demo", human_id="owner")
    legacy = next(row for row in rows if row["subject_id"] == unassigned.id and row["type"] == "unassigned_work")
    assert legacy["source_event_id"] == f"task:{unassigned.id}" and legacy["actionable"] is False
    assert not any(row["subject_id"] == assigned.id and row["type"] == "unassigned_work" for row in rows)


@pytest.mark.parametrize("unsafe", [
    "/Users/alice/state.json", "/home/alice", "/etc/passwd", r"C:\\tenant\\state.json", "C:/tenant/state.json",
    "Traceback (most recent call last)", "Exception: failed", "Errno 2", "http://localhost:8080", "internal runtime host",
    "Codex model provider runtime MCP graph worker path raw tool raw exception",
])
def test_public_text_uses_fixed_fallback_for_internal_values(unsafe):
    from simulacra.missions.projections import _safe_text
    assert _safe_text(unsafe, "Safe Mission update") == "Safe Mission update"


def test_attention_receipts_are_private_and_cursor_scope_is_bound(tmp_path):
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(repository)
    room = service.create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner")
    for human in ("reader", "other"):
        room = service.add_member(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", member_id=human, role="member", expected_revision=room.revision)
    mark_attention_read(repository, tenant_id="tenant_demo", project_id="project_demo", human_id="reader", event_id="attention_receipt", expected_revision=0, clock=lambda: "2026-01-02T10:00:00+00:00")
    state = repository.conversation_state("tenant_demo", "project_demo")
    assert set(state["attention_receipts"]) == {"attention_receipt:reader"}
    rows = [
        {"id": "attention_b", "priority": 1, "created_at": "2026-01-02T11:00:00+00:00"},
        {"id": "attention_a", "priority": 1, "created_at": "2026-01-02T10:00:00+00:00"},
    ]
    from simulacra.missions.projections import paginate_attention
    page, cursor = paginate_attention(rows, endpoint="workspace_attention", scope="tenant_demo:reader:all", limit=1, secret=b"test-secret")
    assert [row["id"] for row in page] == ["attention_b"]
    with pytest.raises(CursorInvalidError):
        paginate_attention(rows, endpoint="workspace_attention", scope="tenant_demo:other:all", limit=1, cursor=cursor, secret=b"test-secret")
    with pytest.raises(CursorInvalidError):
        paginate_attention(rows, endpoint="workspace_attention", scope="tenant_demo:reader:actionable", limit=1, cursor=cursor, secret=b"test-secret")


def test_attention_all_retains_approved_and_superseded_plan_revisions(tmp_path):
    mission_repository = JsonMissionRepository(tmp_path / "missions")
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    CollaborationService(repository).create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner")
    MissionService(mission_repository).bootstrap("tenant_demo", "project_demo", "owner", {"title": "Demo"})
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    store = OperationGraphStore(workspace, tenant_id="tenant_demo", project_id="project_demo")
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"].update({"tenant_id": "tenant_demo", "project_id": "project_demo"})
    first = store.create_revision(graph, expected_revision_hash=None)
    store.approve_revision(first.revision_hash, actor_id="owner")
    changed = deepcopy(graph)
    changed["metadata"]["description"] = "A newer Mission plan"
    second = store.create_revision(changed, expected_revision_hash=first.revision_hash)
    rows = [row for row in project_attention_items(mission_repository, repository, tenant_id="tenant_demo", human_id="owner", workspace_for_project=lambda _: workspace) if row["type"] == "plan_approval"]
    assert len(rows) == 2
    approved = next(row for row in rows if row["subject_id"] == str(first.revision))
    current = next(row for row in rows if row["subject_id"] == str(second.revision))
    assert approved["actionable"] is False
    assert current["actionable"] is True


def test_summary_rechecks_membership_after_detail_load(tmp_path, monkeypatch):
    missions = JsonMissionRepository(tmp_path / "missions")
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(repository)
    room = service.create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner")
    room = service.add_member(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", member_id="reader", role="member", expected_revision=room.revision)
    MissionService(missions).bootstrap("tenant_demo", "project_demo", "owner", {"title": "Demo"})
    original = missions.get_mission
    def revoke_after_load(tenant_id, project_id):
        value = original(tenant_id, project_id)
        current = repository.get_room(tenant_id, project_id)
        repository.save_room(replace(current, members=[member for member in current.members if member.actor_id != "reader"], revision=current.revision + 1), current.revision)
        return value
    monkeypatch.setattr(missions, "get_mission", revoke_after_load)
    assert project_mission_summaries(missions, repository, tenant_id="tenant_demo", human_id="reader") == []


def test_attention_uses_authorized_room_name_and_refuses_unsafe_detail_leaf(tmp_path):
    missions = JsonMissionRepository(tmp_path / "missions")
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(repository)
    room = service.create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner", creator_name="Jordan")
    service.add_member(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", member_id="reader", role="member", expected_revision=room.revision)
    MissionService(missions).bootstrap("tenant_demo", "project_demo", "owner", {"title": "Demo"})
    service.add_comment(tenant_id="tenant_demo", project_id="project_demo", author_id="owner", body="Please review", target_type="project", mentions=["reader"])
    rows = project_attention_items(missions, repository, tenant_id="tenant_demo", human_id="reader")
    assert next(row for row in rows if row["type"] == "mention")["title"] == "Jordan mentioned you"
    task_path = repository.root / "tenant_demo" / "project_demo" / "collaboration" / "tasks.json"
    task_path.write_text("x" * (16 * 1_048_576 + 1), encoding="utf-8")
    assert project_attention_items(missions, repository, tenant_id="tenant_demo", human_id="reader") == []


def test_detail_preflight_refuses_symlink_and_nonregular_leaves(tmp_path):
    from simulacra.missions.projections import _safe_detail_leaf
    directory = tmp_path / "root" / "tenant_demo" / "project_demo" / "missions"
    directory.mkdir(parents=True)
    leaf = directory / "state.json"
    target = tmp_path / "outside.json"
    target.write_text("{}", encoding="utf-8")
    leaf.symlink_to(target)
    assert not _safe_detail_leaf(tmp_path / "root", ("tenant_demo", "project_demo", "missions", "state.json"))
    leaf.unlink()
    leaf.mkdir()
    assert not _safe_detail_leaf(tmp_path / "root", ("tenant_demo", "project_demo", "missions", "state.json"))
