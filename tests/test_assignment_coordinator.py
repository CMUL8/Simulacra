from __future__ import annotations

import json
import multiprocessing
import os
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import pytest

from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.collaboration.models import Member
from simulacra.missions import JsonMissionRepository, MissionService
from simulacra.missions import MissionWorker
from simulacra.operation_graph import OperationGraphStore, load_operation_graph
from simulacra.workplace.assignment_coordinator import AssignmentCoordinator, AssignmentError


def _clock() -> str:
    return "2026-01-02T09:00:00Z"


def _setup(tmp_path: Path, *, actor: str = "alice", actor_role: str = "owner", tenant_id: str = "tenant_1", project_id: str = "project_1", runs_root: Path | None = None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    collaboration = JsonCollaborationRepository(tmp_path / "collaboration")
    rooms = CollaborationService(collaboration)
    rooms.create_room(tenant_id=tenant_id, project_id=project_id, creator_id=actor, creator_role=actor_role)
    mission = MissionService(JsonMissionRepository(tmp_path / "missions"))
    mission.bootstrap(tenant_id, project_id, "owner", {"title": "mission"})
    agent = mission.add_agent(tenant_id, project_id, {"name": "Agent", "role": "builder", "mandate": "build"})
    graph = OperationGraphStore(tmp_path, tenant_id=tenant_id, project_id=project_id)
    raw = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    raw["metadata"].update({"tenant_id": tenant_id, "project_id": project_id})
    revision = graph.create_revision(raw, expected_revision_hash=None)
    graph.approve_revision(revision.revision_hash, actor_id="owner")
    coordinator = AssignmentCoordinator(collaboration, mission, tmp_path, runs_root=runs_root or tmp_path / "runs", clock=_clock)
    return coordinator, collaboration, mission, agent.id, revision.revision_hash


def _assign(coordinator, agent_id, revision, *, actor="alice", request="request_1", tenant_id="tenant_1", project_id="project_1"):
    return coordinator.assign(tenant_id=tenant_id, project_id=project_id, authenticated_human_actor_id=actor,
                              client_request_id=request, body="Please assign this", title="Build it", objective="Deliver it",
                              acceptance_criteria=["Verified"], assigned_agent_ids=[agent_id], graph_revision=revision)


def _retry_process(root: str, agent_id: str, revision: str, results) -> None:
    base = Path(root)
    coordinator = AssignmentCoordinator(
        JsonCollaborationRepository(base / "collaboration"),
        MissionService(JsonMissionRepository(base / "missions")), base,
        runs_root=base / "runs", clock=_clock,
    )
    try:
        result = _assign(coordinator, agent_id, revision)
        results.put((result.transaction_id, result.message_id, result.task_id, result.run_id, result.state))
    except Exception as exc:  # pragma: no cover - asserted as an unexpected process result
        results.put(("error", type(exc).__name__, str(exc)))


def _fault_assignment_process(root: str, agent_id: str, revision: str, boundary: str, results) -> None:
    base = Path(root)
    coordinator = AssignmentCoordinator(JsonCollaborationRepository(base / "collaboration"), MissionService(JsonMissionRepository(base / "missions")), base, runs_root=base / "runs", clock=_clock)
    seen: list[str] = []
    def fault(stage: str):
        if stage == boundary:
            seen.append(stage); raise RuntimeError(stage)
    coordinator.fault_injector = fault
    try:
        _assign(coordinator, agent_id, revision)
        results.put(("unexpected", seen))
    except RuntimeError:
        results.put(("fault", seen))


def _exit_after_prepared_temp_fsync_process(root: str, agent_id: str, revision: str) -> None:
    """Simulate a hard process death after the journal temp reaches disk."""
    base = Path(root)
    coordinator = AssignmentCoordinator(
        JsonCollaborationRepository(base / "collaboration"),
        MissionService(JsonMissionRepository(base / "missions")), base,
        runs_root=base / "runs", clock=_clock,
    )

    def die(stage: str) -> None:
        if stage == "after_PREPARED_temp_fsync":
            os._exit(0)

    coordinator.fault_injector = die
    _assign(coordinator, agent_id, revision)


def _recover_assignment_process(root: str, agent_id: str, revision: str, boundary: str, results) -> None:
    base = Path(root)
    repository = JsonCollaborationRepository(base / "collaboration")
    mission = MissionService(JsonMissionRepository(base / "missions"))
    coordinator = AssignmentCoordinator(repository, mission, base, runs_root=base / "runs", clock=_clock)
    try:
        outcome = _assign(coordinator, agent_id, revision) if boundary == "before_PREPARED_replace" else coordinator.recover(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1")
        results.put((outcome.transaction_id, outcome.message_id, outcome.task_id, outcome.run_id, outcome.state,
                     len(repository.conversation_state("tenant_1", "project_1")["messages"]),
                     len(repository.list_tasks("tenant_1", "project_1")), len(mission.runs("tenant_1", "project_1"))))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def _paused_assignment_process(root: str, agent_id: str, revision: str, entered, release, results) -> None:
    base = Path(root)
    coordinator = AssignmentCoordinator(JsonCollaborationRepository(base / "collaboration"), MissionService(JsonMissionRepository(base / "missions")), base, runs_root=base / "runs", clock=_clock)
    def pause(stage: str) -> None:
        if stage == "before_COMMIT_DECIDED":
            entered.set()
            if not release.wait(15): raise RuntimeError("release timeout")
    coordinator.fault_injector = pause
    try:
        outcome = _assign(coordinator, agent_id, revision)
        results.put(("complete", outcome.transaction_id, outcome.message_id, outcome.task_id, outcome.run_id))
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))


def _remove_alice_process(root: str, results, started, finished) -> None:
    repository = JsonCollaborationRepository(Path(root) / "collaboration")
    try:
        room = repository.get_room("tenant_1", "project_1")
        room.members = [member for member in room.members if member.actor_id != "alice"]
        room.revision += 1
        started.set()
        repository.save_room(room, expected_revision=room.revision - 1)
        results.put("removed")
    except Exception as exc:
        results.put(("error", type(exc).__name__, str(exc)))
    finally:
        finished.set()


def _reader_or_claim_process(root: str, transaction_id: str, mode: str, results) -> None:
    base = Path(root)
    coordinator = AssignmentCoordinator(JsonCollaborationRepository(base / "collaboration"), MissionService(JsonMissionRepository(base / "missions")), base, runs_root=base / "runs", clock=_clock)
    if mode == "reader":
        results.put(coordinator.visible_result(tenant_id="tenant_1", project_id="project_1", transaction_id=transaction_id) is not None)
    else:
        service = MissionService(JsonMissionRepository(base / "missions"))
        with coordinator.project_claim_guard("tenant_1", "project_1") as admission:
            claimed = service.claim_next("tenant_1", "project_1", "worker", assignment_admission=admission)
        results.put(None if claimed is None else claimed.id)


def _journal(tmp_path: Path):
    return next((tmp_path / "runs").glob(".workplace-control/tenant_1/project_1/assignment-transactions/*/conversation_assignment/*.json"))


def test_all_precomplete_states_are_hidden_and_nonclaimable(tmp_path: Path):
    coordinator, _collab, mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    path = _journal(tmp_path); row = json.loads(path.read_text())
    for state in ("PREPARED", "COMMIT_DECIDED", "STORES_DURABLE"):
        row["state"] = state; path.write_text(json.dumps(row))
        assert coordinator.visible_result(tenant_id="tenant_1", project_id="project_1", transaction_id=result.transaction_id) is None
        assert mission.claim_next("tenant_1", "project_1", "worker") is None
    row["state"] = "COMPLETE"; path.write_text(json.dumps(row))


def test_queued_before_complete_is_not_claimed(tmp_path: Path):
    coordinator, _collab, mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    path = _journal(tmp_path); row = json.loads(path.read_text()); row["state"] = "STORES_DURABLE"; path.write_text(json.dumps(row))
    assert mission.runs("tenant_1", "project_1")[0].status == "queued"
    assert mission.claim_next("tenant_1", "project_1", "worker") is None
    with coordinator.project_claim_guard("tenant_1", "project_1") as admission:
        assert mission.claim_next("tenant_1", "project_1", "worker", assignment_admission=admission) is None
    assert result.state == "COMPLETE"


def test_assignment_is_one_message_one_ordered_run(tmp_path: Path):
    coordinator, collaboration, mission, first_agent, revision = _setup(tmp_path)
    second_agent = mission.add_agent(
        "tenant_1", "project_1", {"name": "Second agent", "role": "reviewer", "mandate": "review"},
    )

    result = coordinator.assign(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice",
        client_request_id="ordered_handoff", body="Prepare the review pack.", title="Prepare review pack",
        objective="Prepare the review pack for a human review.", acceptance_criteria=["A human can review the result."],
        assigned_agent_ids=[first_agent, second_agent.id], graph_revision=revision,
    )

    assert result.state == "COMPLETE"
    assert list(collaboration.conversation_state("tenant_1", "project_1")["messages"]) == [result.message_id]
    assert [task.id for task in collaboration.list_tasks("tenant_1", "project_1")] == [result.task_id]
    runs = mission.runs("tenant_1", "project_1")
    assert [run.id for run in runs] == [result.run_id]
    assert runs[0].assigned_agent_ids == [first_agent, second_agent.id]


def test_lost_response_retry_recovers_same_transaction(tmp_path: Path):
    coordinator, collaboration, mission, agent, revision = _setup(tmp_path)
    first = _assign(coordinator, agent, revision, request="lost_response")
    retried = _assign(coordinator, agent, revision, request="lost_response")

    assert retried == first
    assert len(collaboration.conversation_state("tenant_1", "project_1")["messages"]) == 1
    assert len(collaboration.list_tasks("tenant_1", "project_1")) == 1
    assert len(mission.runs("tenant_1", "project_1")) == 1


def test_precomplete_assignment_is_not_projected_or_claimable(tmp_path: Path):
    coordinator, _collaboration, mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    path = _journal(tmp_path)
    row = json.loads(path.read_text())
    row["state"] = "PREPARED"
    path.write_text(json.dumps(row))

    assert coordinator.visible_result(
        tenant_id="tenant_1", project_id="project_1", transaction_id=result.transaction_id,
    ) is None
    with coordinator.project_claim_guard("tenant_1", "project_1") as admission:
        assert mission.claim_next("tenant_1", "project_1", "worker", assignment_admission=admission) is None


def test_reviewer_membership_is_rechecked_under_locked_assignment_decision(tmp_path: Path, monkeypatch):
    coordinator, collaboration, mission, agent, revision = _setup(tmp_path)
    rooms = CollaborationService(collaboration)
    room = collaboration.get_room("tenant_1", "project_1")
    rooms.add_member(
        tenant_id="tenant_1", project_id="project_1", actor_id="alice", member_id="reviewer_1",
        role="reviewer", expected_revision=room.revision,
    )
    original_lock = collaboration.room_lock

    @contextmanager
    def remove_reviewer_then_lock(tenant, project):
        current = collaboration.get_room(tenant, project)
        current.members = [member for member in current.members if member.actor_id != "reviewer_1"]
        current.revision += 1
        collaboration.save_room(current, expected_revision=current.revision - 1)
        with original_lock(tenant, project) as locked:
            yield locked

    monkeypatch.setattr(collaboration, "room_lock", remove_reviewer_then_lock)
    with pytest.raises(AssignmentError, match="transaction_aborted"):
        coordinator.assign(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice",
            client_request_id="reviewer_race", body="Prepare the review pack.", title="Review pack",
            objective="Prepare a reviewable pack.", acceptance_criteria=["A human can review it."],
            assigned_agent_ids=[agent], graph_revision=revision, reviewer_human_ids=["reviewer_1"],
        )
    assert collaboration.conversation_state("tenant_1", "project_1")["messages"] == {}
    assert mission.runs("tenant_1", "project_1") == []


def _add_tagged_member(repository: JsonCollaborationRepository, actor_id: str, role: str, transaction_id: str) -> None:
    room = repository.get_room("tenant_1", "project_1")
    repository.save_room(replace(
        room,
        members=[*room.members, Member(
            actor_id=actor_id, role=role, transaction_id=transaction_id,
            visibility_state="committed",
        )],
        revision=room.revision + 1,
    ), room.revision)


def _complete_member_acceptance(repository: JsonCollaborationRepository, transaction_id: str) -> None:
    journal = (
        repository.root / ".invitation-acceptance" / "tenant_1" / "project_1"
        / f"{transaction_id}.json"
    )
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({
        "state": "COMPLETE", "transaction_id": transaction_id,
        "tenant_id": "tenant_1", "project_id": "project_1",
    }))


def test_pending_tagged_owner_cannot_admit_assignment_until_acceptance_complete(tmp_path: Path):
    coordinator, collaboration, _mission, agent, revision = _setup(tmp_path)
    transaction_id = "txn_pending_assignment_owner"
    _add_tagged_member(collaboration, "pending_owner", "owner", transaction_id)

    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        _assign(coordinator, agent, revision, actor="pending_owner", request="pending_owner_denied")
    assert collaboration.conversation_state("tenant_1", "project_1")["messages"] == {}

    _complete_member_acceptance(collaboration, transaction_id)
    admitted = _assign(
        coordinator, agent, revision, actor="pending_owner", request="pending_owner_admitted",
    )
    assert admitted.state == "COMPLETE"


def test_pending_tagged_reviewer_cannot_join_assignment_until_acceptance_complete(tmp_path: Path):
    coordinator, collaboration, mission, agent, revision = _setup(tmp_path)
    transaction_id = "txn_pending_assignment_reviewer"
    _add_tagged_member(collaboration, "pending_reviewer", "reviewer", transaction_id)

    with pytest.raises(AssignmentError, match="transaction_aborted"):
        coordinator.assign(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice",
            client_request_id="pending_reviewer_denied", body="Prepare the review pack.", title="Review pack",
            objective="Prepare a reviewable pack.", acceptance_criteria=["A human can review it."],
            assigned_agent_ids=[agent], graph_revision=revision,
            reviewer_human_ids=["pending_reviewer"],
        )
    assert collaboration.conversation_state("tenant_1", "project_1")["messages"] == {}
    assert mission.runs("tenant_1", "project_1") == []

    _complete_member_acceptance(collaboration, transaction_id)
    admitted = coordinator.assign(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice",
        client_request_id="pending_reviewer_admitted", body="Prepare the review pack.", title="Review pack",
        objective="Prepare a reviewable pack.", acceptance_criteria=["A human can review it."],
        assigned_agent_ids=[agent], graph_revision=revision,
        reviewer_human_ids=["pending_reviewer"],
    )
    assert admitted.state == "COMPLETE"


def test_assignment_preserves_reviewer_order_in_committed_work(tmp_path: Path):
    coordinator, collaboration, _mission, agent, revision = _setup(tmp_path)
    rooms = CollaborationService(collaboration)
    room = collaboration.get_room("tenant_1", "project_1")
    room = rooms.add_member(
        tenant_id="tenant_1", project_id="project_1", actor_id="alice", member_id="reviewer_1",
        role="reviewer", expected_revision=room.revision,
    )
    rooms.add_member(
        tenant_id="tenant_1", project_id="project_1", actor_id="alice", member_id="reviewer_2",
        role="approver", expected_revision=room.revision,
    )
    result = coordinator.assign(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice",
        client_request_id="reviewer_order", body="Prepare the review pack.", title="Review pack",
        objective="Prepare a reviewable pack.", acceptance_criteria=["A human can review it."],
        assigned_agent_ids=[agent], graph_revision=revision, reviewer_human_ids=["reviewer_2", "reviewer_1"],
    )
    task = next(task for task in collaboration.list_tasks("tenant_1", "project_1") if task.id == result.task_id)
    assert task.collaborator_ids == ["reviewer_2", "reviewer_1"]


def test_complete_is_the_only_public_and_claimable_state(tmp_path: Path):
    coordinator, _collab, mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    assert coordinator.visible_result(tenant_id="tenant_1", project_id="project_1", transaction_id=result.transaction_id) == result
    with coordinator.project_claim_guard("tenant_1", "project_1") as admission:
        claimed = mission.claim_next("tenant_1", "project_1", "worker", assignment_admission=admission)
    assert claimed and claimed.id == result.run_id


def test_invalid_predecision_revalidation_writes_aborted(tmp_path: Path):
    coordinator, _collab, _mission, agent, revision = _setup(tmp_path)
    def fail(stage):
        if stage == "before_collaboration_message": raise RuntimeError("crash")
    coordinator.fault_injector = fail
    with pytest.raises(RuntimeError): _assign(coordinator, agent, revision)
    path = _journal(tmp_path); row = json.loads(path.read_text()); assert row["state"] == "PREPARED"
    row["intended_payloads"]["request"]["body"] = "tampered"; path.write_text(json.dumps(row))
    coordinator.fault_injector = None
    with pytest.raises(AssignmentError, match="transaction_aborted"):
        coordinator.recover(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1")
    assert json.loads(path.read_text())["state"] == "ABORTED"


def test_viewer_cannot_assign_under_the_locked_authority_check(tmp_path: Path):
    coordinator, collaboration, mission, agent, revision = _setup(tmp_path, actor="viewer", actor_role="viewer")
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        _assign(coordinator, agent, revision, actor="viewer")
    transactions = tmp_path / "runs" / ".workplace-control" / "tenant_1" / "project_1" / "assignment-transactions"
    assert not transactions.exists()
    assert collaboration.conversation_state("tenant_1", "project_1")["messages"] == {}
    assert collaboration.list_tasks("tenant_1", "project_1") == []
    assert mission.runs("tenant_1", "project_1") == []


def test_unapproved_plan_blocks_before_prepared_write(tmp_path: Path):
    coordinator, collaboration, mission, agent, revision = _setup(tmp_path)
    graph = OperationGraphStore(tmp_path, tenant_id="tenant_1", project_id="project_1")
    draft = deepcopy(graph.current_revision().graph)
    draft["metadata"]["version"] = 2
    unapproved = graph.create_revision(draft, expected_revision_hash=revision)
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        _assign(coordinator, agent, unapproved.revision_hash)
    transactions = tmp_path / "runs" / ".workplace-control" / "tenant_1" / "project_1" / "assignment-transactions"
    assert not transactions.exists()
    assert collaboration.conversation_state("tenant_1", "project_1")["messages"] == {}
    assert collaboration.list_tasks("tenant_1", "project_1") == []
    assert mission.runs("tenant_1", "project_1") == []


def test_mismatched_approved_plan_blocks_before_prepared_write(tmp_path: Path):
    coordinator, _collaboration, _mission, agent, _revision = _setup(tmp_path)
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        _assign(coordinator, agent, "different_approved_revision")
    assert not (tmp_path / "runs" / ".workplace-control" / "tenant_1" / "project_1" / "assignment-transactions").exists()


def test_hard_exit_after_temp_fsync_adopts_the_valid_prepared_journal(tmp_path: Path):
    _coordinator, _collaboration, mission, agent, revision = _setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    process = context.Process(target=_exit_after_prepared_temp_fsync_process, args=(str(tmp_path), agent, revision))
    process.start(); process.join(15)
    assert process.exitcode == 0
    operation = (tmp_path / "runs" / ".workplace-control" / "tenant_1" / "project_1" / "assignment-transactions" / "alice" / "conversation_assignment")
    assert list(operation.glob(".request_1.json.*.tmp")) and not (operation / "request_1.json").exists()
    recovered = AssignmentCoordinator(
        JsonCollaborationRepository(tmp_path / "collaboration"), MissionService(JsonMissionRepository(tmp_path / "missions")), tmp_path,
        runs_root=tmp_path / "runs", clock=_clock,
    ).recover(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1")
    assert recovered.state == "COMPLETE" and len(mission.runs("tenant_1", "project_1")) == 1


def test_recognized_partial_temp_is_discarded_without_blocking_recovery(tmp_path: Path):
    coordinator, _collaboration, _mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    temporary = _journal(tmp_path).parent / ".request_1.json.7.8.tmp"
    temporary.write_bytes(b'{"partial"')
    recovered = coordinator.recover(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1",
    )
    assert recovered == result and not temporary.exists()


def test_new_assignment_scope_errors_are_invalid_but_recovery_scope_errors_are_unavailable(tmp_path: Path):
    coordinator, _collaboration, _mission, agent, revision = _setup(tmp_path)
    with pytest.raises(AssignmentError, match="assignment_invalid"):
        _assign(coordinator, agent, revision, tenant_id="bad/id")
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        coordinator.recover(tenant_id="bad/id", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1")


@pytest.mark.parametrize("actor,request_id", [("mallory", "request_1"), ("alice", "request_other")])
def test_journal_actor_or_request_path_substitution_is_not_admitted(tmp_path: Path, actor: str, request_id: str):
    coordinator, _collaboration, _mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    original = _journal(tmp_path)
    replacement = original.parents[2] / actor / "conversation_assignment" / f"{request_id}.json"
    replacement.parent.mkdir(parents=True, exist_ok=True)
    original.replace(replacement)
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        coordinator.recover(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id=actor, client_request_id=request_id)
    assert coordinator.visible_result(tenant_id="tenant_1", project_id="project_1", transaction_id=result.transaction_id) is None
    with coordinator.project_claim_guard("tenant_1", "project_1") as admission:
        assert not admission.allows(result.transaction_id, result.run_id)


@pytest.mark.parametrize("mutate", [
    lambda row: row.update({"transaction_id": "bad/id"}),
    lambda row: row.update({"client_request_id": ""}),
])
def test_malformed_persisted_journal_returns_a_stable_assignment_error(tmp_path: Path, mutate):
    coordinator, _collaboration, _mission, agent, revision = _setup(tmp_path)
    _assign(coordinator, agent, revision)
    path = _journal(tmp_path); row = json.loads(path.read_text()); mutate(row); path.write_text(json.dumps(row))
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        coordinator.recover(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1")


def test_predecision_abort_after_pending_run_preserves_prior_mission_contract(tmp_path: Path):
    coordinator, _collaboration, mission, agent, revision = _setup(tmp_path)
    prior = "prior_contract"
    mission.repository.mutate("tenant_1", "project_1", lambda records: records["mission"].update({"approved_contract_revision": prior}))
    coordinator.fault_injector = lambda stage: (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_pending_run" else None
    with pytest.raises(RuntimeError, match="after_pending_run"):
        _assign(coordinator, agent, revision)
    coordinator.fault_injector = None
    path = _journal(tmp_path); row = json.loads(path.read_text()); row["intended_payloads"]["request"]["body"] = "tampered"; path.write_text(json.dumps(row))
    with pytest.raises(AssignmentError, match="transaction_aborted"):
        coordinator.recover(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1")
    assert mission.mission("tenant_1", "project_1").approved_contract_revision == prior


def test_postdecision_recovery_ignores_membership_and_head_changes(tmp_path: Path):
    coordinator, collaboration, _mission, agent, revision = _setup(tmp_path)
    coordinator.fault_injector = lambda stage: (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_COMMIT_DECIDED" else None
    with pytest.raises(RuntimeError, match="after_COMMIT_DECIDED"):
        _assign(coordinator, agent, revision)
    coordinator.fault_injector = None
    room = collaboration.get_room("tenant_1", "project_1")
    room.members = []; room.revision += 1
    collaboration.save_room(room, expected_revision=room.revision - 1)
    graph = OperationGraphStore(tmp_path, tenant_id="tenant_1", project_id="project_1")
    changed = deepcopy(graph.current_revision().graph); changed["metadata"]["version"] = 1
    replacement = graph.create_revision(changed, expected_revision_hash=revision)
    graph.approve_revision(replacement.revision_hash, actor_id="owner")
    # The graph remains locked for ordering, but its changed head and removed
    # human no longer re-decide a durable COMMIT_DECIDED transaction.
    assert coordinator.recover(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1").state == "COMPLETE"


def test_locked_membership_recheck_denies_before_prepared_publication(tmp_path: Path, monkeypatch):
    coordinator, collaboration, _mission, agent, revision = _setup(tmp_path)
    original = collaboration.room_lock
    @contextmanager
    def remove_then_lock(tenant, project):
        room = collaboration.get_room(tenant, project)
        room.members = []; room.revision += 1
        collaboration.save_room(room, expected_revision=room.revision - 1)
        with original(tenant, project) as locked:
            yield locked
    monkeypatch.setattr(collaboration, "room_lock", remove_then_lock)
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        _assign(coordinator, agent, revision)
    assert not list((tmp_path / "runs").glob(".workplace-control/**/request_1.json"))


def test_complete_recovers_missing_child_and_rejects_conflict(tmp_path: Path):
    coordinator, collaboration, _mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    collaboration.mutate_conversation_state("tenant_1", "project_1", lambda state: state["messages"].pop(result.message_id))
    assert coordinator.visible_result(tenant_id="tenant_1", project_id="project_1", transaction_id=result.transaction_id) is None
    assert coordinator.recover(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1") == result
    collaboration.mutate_conversation_state("tenant_1", "project_1", lambda state: state["messages"][result.message_id].update({"body": "tampered"}))
    assert coordinator.visible_result(tenant_id="tenant_1", project_id="project_1", transaction_id=result.transaction_id) is None
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        coordinator.recover(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1")


def test_assignment_run_is_pinned_to_approved_graph_and_worker_admits(tmp_path: Path):
    coordinator, _collab, mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    run = next(item for item in mission.runs("tenant_1", "project_1") if item.id == result.run_id)
    assert mission.mission("tenant_1", "project_1").approved_contract_revision == revision
    assert MissionWorker(mission, tmp_path)._admitted(run)[0] is True


def test_forged_run_with_complete_transaction_id_is_not_admitted(tmp_path: Path):
    coordinator, _collab, mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    def forge(records):
        records["runs"][result.run_id]["status"] = "succeeded"
        forged = dict(records["runs"][result.run_id]); forged["id"] = "run_forged"; forged["status"] = "queued"; forged["revision"] = 1
        records["runs"]["run_forged"] = forged
    mission.repository.mutate("tenant_1", "project_1", forge)
    with coordinator.project_claim_guard("tenant_1", "project_1") as admission:
        assert mission.claim_next("tenant_1", "project_1", "worker", assignment_admission=admission) is None


def test_claim_guard_snapshots_before_mission_mutation_without_inversion(tmp_path: Path, monkeypatch):
    coordinator, collaboration, mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    events: list[str] = []
    original_state = collaboration.conversation_state
    def state(*args, **kwargs):
        assert "mission_mutation" not in events; events.append("collaboration"); return original_state(*args, **kwargs)
    monkeypatch.setattr(collaboration, "conversation_state", state)
    original_runs = mission.runs
    def runs(*args, **kwargs):
        assert "mission_mutation" not in events; events.append("mission_read"); return original_runs(*args, **kwargs)
    monkeypatch.setattr(mission, "runs", runs)
    original_mutate = mission.repository.mutate
    def mutate(*args, **kwargs):
        events.append("mission_mutation"); return original_mutate(*args, **kwargs)
    monkeypatch.setattr(mission.repository, "mutate", mutate)
    with coordinator.project_claim_guard("tenant_1", "project_1") as admission:
        assert mission.claim_next("tenant_1", "project_1", "worker", assignment_admission=admission).id == result.run_id
    assert events.index("collaboration") < events.index("mission_read") < events.index("mission_mutation")


@pytest.mark.parametrize("field,value", [("contract_revision", "wrong_revision"), ("assigned_agent_ids", [])])
def test_existing_assignment_run_immutable_tamper_fails_closed(tmp_path: Path, field: str, value):
    coordinator, _collab, mission, agent, revision = _setup(tmp_path)
    result = _assign(coordinator, agent, revision)
    mission.repository.mutate("tenant_1", "project_1", lambda records: records["runs"][result.run_id].__setitem__(field, value))
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        coordinator.recover(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1")


@pytest.mark.parametrize("leaf", ["control", "transactions", "actor", "operation", "journal", "lock"])
def test_control_path_symlinks_are_rejected_without_escape(tmp_path: Path, leaf: str):
    outside = tmp_path / "outside"; outside.mkdir()
    root = tmp_path / "runs"; root.mkdir()
    if leaf == "control":
        os.symlink(outside, root / ".workplace-control")
    else:
        parent = root / ".workplace-control" / "tenant_1" / "project_1"; parent.mkdir(parents=True)
        if leaf == "transactions":
            os.symlink(outside, parent / "assignment-transactions")
        elif leaf == "lock":
            os.symlink(outside / "target", parent / ".assignment-coordinator.lock")
        else:
            transactions = parent / "assignment-transactions"; transactions.mkdir()
            if leaf == "actor": os.symlink(outside, transactions / "alice")
            else:
                actor = transactions / "alice"; actor.mkdir()
                if leaf == "operation": os.symlink(outside, actor / "conversation_assignment")
                else:
                    operation = actor / "conversation_assignment"; operation.mkdir()
                    os.symlink(outside / "target", operation / "request_1.json")
    coordinator, _collab, _mission, agent, revision = _setup(tmp_path, runs_root=root)
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        _assign(coordinator, agent, revision)
    assert list(outside.iterdir()) == []


def test_symlinked_runs_root_is_rejected_at_construction(tmp_path: Path):
    outside = tmp_path / "outside"; outside.mkdir()
    link = tmp_path / "runs"; os.symlink(outside, link)
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        AssignmentCoordinator(JsonCollaborationRepository(tmp_path / "collaboration"), MissionService(JsonMissionRepository(tmp_path / "missions")), tmp_path, runs_root=link, clock=_clock)


def test_symlinked_runs_root_ancestor_is_rejected_at_construction(tmp_path: Path):
    outside = tmp_path / "outside"; outside.mkdir()
    link = tmp_path / "linked-parent"; os.symlink(outside, link)
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        AssignmentCoordinator(JsonCollaborationRepository(tmp_path / "collaboration"), MissionService(JsonMissionRepository(tmp_path / "missions")), tmp_path, runs_root=link / "runs", clock=_clock)
    assert not (outside / "runs").exists()


def test_runs_root_is_opened_component_by_component_without_following_links(tmp_path: Path, monkeypatch):
    root = tmp_path / "nested" / "runs"
    root.parent.mkdir()
    collaboration = JsonCollaborationRepository(tmp_path / "collaboration")
    mission = MissionService(JsonMissionRepository(tmp_path / "missions"))
    import simulacra.workplace.assignment_coordinator as coordinator_module

    calls: list[tuple[str, int, int | None]] = []
    original_open = coordinator_module.os.open

    def traced_open(name, flags, mode=0o777, *, dir_fd=None):
        calls.append((os.fspath(name), flags, dir_fd))
        return original_open(name, flags, mode, dir_fd=dir_fd)

    monkeypatch.setattr(coordinator_module.os, "open", traced_open)
    AssignmentCoordinator(collaboration, mission, tmp_path, runs_root=root, clock=_clock)

    components = list(root.parts[1:])
    root_walk = [call for call in calls if call[0] == "/" or call[0] in components]
    # Creating the final leaf makes one failed no-follow open before mkdir and
    # one successful retry.  Every attempt still walks one component at a time.
    assert [name for name, _flags, _parent in root_walk][:len(components) + 1] == ["/", *components]
    assert all(flags & os.O_DIRECTORY for _name, flags, _parent in root_walk)
    assert all(flags & getattr(os, "O_NOFOLLOW", 0) for _name, flags, _parent in root_walk)
    assert root_walk[0][2] is None
    assert all(parent is not None for _name, _flags, parent in root_walk[1:])
    assert os.fspath(root) not in [name for name, _flags, _parent in calls]


def test_new_control_directories_fsync_their_parent(tmp_path: Path, monkeypatch):
    root = tmp_path / "runs"; root.mkdir()
    coordinator, _collaboration, _mission, agent, revision = _setup(tmp_path, runs_root=root)
    import simulacra.workplace.assignment_coordinator as coordinator_module
    events: list[tuple[str, int | None]] = []
    original_mkdir, original_fsync = coordinator_module.os.mkdir, coordinator_module.os.fsync
    def traced_mkdir(name, mode=0o777, *, dir_fd=None):
        result = original_mkdir(name, mode, dir_fd=dir_fd)
        events.append(("mkdir", dir_fd)); return result
    def traced_fsync(fd):
        events.append(("fsync", fd)); return original_fsync(fd)
    monkeypatch.setattr(coordinator_module.os, "mkdir", traced_mkdir)
    monkeypatch.setattr(coordinator_module.os, "fsync", traced_fsync)
    _assign(coordinator, agent, revision)
    created = [index for index, event in enumerate(events) if event[0] == "mkdir" and event[1] is not None]
    assert len(created) >= 6
    for index in created:
        assert events[index + 1] == ("fsync", events[index][1])


def test_recovery_enumeration_rejects_symlinked_actor(tmp_path: Path):
    coordinator, _collab, _mission, agent, revision = _setup(tmp_path)
    _assign(coordinator, agent, revision)
    outside = tmp_path / "outside"; outside.mkdir()
    transactions = _journal(tmp_path).parents[2]
    os.symlink(outside, transactions / "evil")
    with pytest.raises(AssignmentError, match="assignment_unavailable"):
        coordinator.recover_project("tenant_1", "project_1")


def test_concurrent_different_project_guards_do_not_cross_scope(tmp_path: Path):
    coordinator, collaboration, mission, agent, revision = _setup(tmp_path)
    first = _assign(coordinator, agent, revision)
    workspace = tmp_path / "project_2_graph"; workspace.mkdir()
    rooms = CollaborationService(collaboration)
    rooms.create_room(tenant_id="tenant_1", project_id="project_2", creator_id="alice")
    mission.bootstrap("tenant_1", "project_2", "owner", {"title": "second"})
    second_agent = mission.add_agent("tenant_1", "project_2", {"name": "Second", "role": "builder", "mandate": "build"})
    graph = OperationGraphStore(workspace, tenant_id="tenant_1", project_id="project_2")
    raw = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    raw["metadata"].update({"tenant_id": "tenant_1", "project_id": "project_2"})
    second_revision = graph.create_revision(raw, expected_revision_hash=None); graph.approve_revision(second_revision.revision_hash, actor_id="owner")
    second_coordinator = AssignmentCoordinator(collaboration, mission, workspace, runs_root=tmp_path / "runs", clock=_clock)
    second = _assign(second_coordinator, second_agent.id, second_revision.revision_hash, project_id="project_2")
    with coordinator.project_claim_guard("tenant_1", "project_1") as first_guard, coordinator.project_claim_guard("tenant_1", "project_2") as second_guard:
        assert first_guard.allows(first.transaction_id, first.run_id)
        assert second_guard.allows(second.transaction_id, second.run_id)
        assert not first_guard.allows(second.transaction_id, second.run_id)
        assert not second_guard.allows(first.transaction_id, first.run_id)


@pytest.mark.parametrize("body,agents", [
    ("Bearer sk-not-allowed-value", ["agent_x"]),
    ("ok", ["agent_x", {"nested": "bad"}]),
])
def test_prepared_input_screening_is_stable_and_leaves_no_journal(tmp_path: Path, body, agents):
    coordinator, _collab, _mission, _agent, revision = _setup(tmp_path)
    with pytest.raises(AssignmentError, match="assignment_invalid"):
        coordinator.assign(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1",
            body=body, title="title", objective="objective", acceptance_criteria=["criterion"], assigned_agent_ids=agents, graph_revision=revision,
        )
    assert not list((tmp_path / "runs").glob(".workplace-control/**/request_1.json"))


def test_oversized_assignment_is_rejected_before_journal(tmp_path: Path):
    coordinator, _collab, _mission, agent, revision = _setup(tmp_path)
    with pytest.raises(AssignmentError, match="assignment_invalid"):
        coordinator.assign(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_large",
            body="body", title="title", objective="objective", acceptance_criteria=["x" * 8000 for _ in range(128)],
            assigned_agent_ids=[agent], graph_revision=revision,
        )
    assert not list((tmp_path / "runs").glob(".workplace-control/**/request_large.json"))


@pytest.mark.parametrize("boundary", ["before_PREPARED_replace", "after_PREPARED_replace", "before_PREPARED_dir_fsync", "after_PREPARED_dir_fsync", "before_collaboration_message", "after_collaboration_message", "before_task", "after_task", "before_pending_run", "after_pending_run", "before_COMMIT_DECIDED", "after_COMMIT_DECIDED", "before_STORES_DURABLE", "after_STORES_DURABLE", "after_queued_before_COMPLETE", "before_COMPLETE", "after_COMPLETE"])
def test_replace_and_fsync_fault_injection_recovers_each_boundary(tmp_path: Path, boundary: str):
    _coordinator, _collab, _mission, agent, revision = _setup(tmp_path)
    context = multiprocessing.get_context("spawn"); faulted = context.Queue(); recovered = context.Queue()
    first = context.Process(target=_fault_assignment_process, args=(str(tmp_path), agent, revision, boundary, faulted)); first.start(); first.join(15)
    assert first.exitcode == 0 and faulted.get(timeout=2) == ("fault", [boundary])
    second = context.Process(target=_recover_assignment_process, args=(str(tmp_path), agent, revision, boundary, recovered)); second.start(); second.join(15)
    outcome = recovered.get(timeout=2)
    assert second.exitcode == 0 and outcome[-4:] == ("COMPLETE", 1, 1, 1)


def test_concurrent_reader_and_worker_claim_converge(tmp_path: Path):
    coordinator, _collab, _mission, agent, revision = _setup(tmp_path)
    coordinator.fault_injector = lambda stage: (_ for _ in ()).throw(RuntimeError(stage)) if stage == "after_queued_before_COMPLETE" else None
    with pytest.raises(RuntimeError): _assign(coordinator, agent, revision)
    tx = json.loads(_journal(tmp_path).read_text())["transaction_id"]
    context = multiprocessing.get_context("spawn"); early = context.Queue()
    reader = context.Process(target=_reader_or_claim_process, args=(str(tmp_path), tx, "reader", early))
    claimant = context.Process(target=_reader_or_claim_process, args=(str(tmp_path), tx, "claim", early))
    reader.start(); claimant.start(); reader.join(15); claimant.join(15)
    assert reader.exitcode == claimant.exitcode == 0
    assert {early.get(timeout=2), early.get(timeout=2)} == {False, None}
    coordinator.fault_injector = None
    result = coordinator.recover(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice", client_request_id="request_1")
    claims = context.Queue()
    workers = [context.Process(target=_reader_or_claim_process, args=(str(tmp_path), tx, "claim", claims)) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(15); assert worker.exitcode == 0
    assert sorted([claims.get(timeout=2), claims.get(timeout=2)], key=lambda item: item is not None) == [None, result.run_id]


def test_spawned_membership_removal_waits_for_durable_decision(tmp_path: Path):
    _coordinator, _collaboration, _mission, agent, revision = _setup(tmp_path)
    context = multiprocessing.get_context("spawn"); entered = context.Event(); release = context.Event(); assigned = context.Queue(); removed = context.Queue(); started = context.Event(); finished = context.Event()
    assignment = context.Process(target=_paused_assignment_process, args=(str(tmp_path), agent, revision, entered, release, assigned)); assignment.start()
    assert entered.wait(10)
    remover = context.Process(target=_remove_alice_process, args=(str(tmp_path), removed, started, finished)); remover.start()
    assert started.wait(10)
    assert not finished.wait(0.25)
    release.set()
    assignment.join(15); remover.join(15)
    assert assignment.exitcode == remover.exitcode == 0
    assert finished.wait(10)
    outcome = assigned.get(timeout=2); assert outcome[0] == "complete"
    assert removed.get(timeout=2) == "removed"
    coordinator = AssignmentCoordinator(JsonCollaborationRepository(tmp_path / "collaboration"), MissionService(JsonMissionRepository(tmp_path / "missions")), tmp_path, runs_root=tmp_path / "runs", clock=_clock)
    assert coordinator.visible_result(tenant_id="tenant_1", project_id="project_1", transaction_id=outcome[1]).run_id == outcome[4]


def test_spawned_removed_before_room_lock_denies_without_prepared_journal(tmp_path: Path):
    _coordinator, _collaboration, _mission, agent, revision = _setup(tmp_path)
    context = multiprocessing.get_context("spawn"); removed = context.Queue(); result = context.Queue(); started = context.Event(); finished = context.Event()
    remover = context.Process(target=_remove_alice_process, args=(str(tmp_path), removed, started, finished)); remover.start()
    assert started.wait(10) and finished.wait(10)
    remover.join(15)
    assert remover.exitcode == 0 and removed.get(timeout=2) == "removed"
    assignment = context.Process(target=_retry_process, args=(str(tmp_path), agent, revision, result)); assignment.start(); assignment.join(15)
    assert assignment.exitcode == 0 and result.get(timeout=2)[-1] == "assignment_unavailable"
    assert not list((tmp_path / "runs").glob(".workplace-control/**/request_1.json"))


def test_two_process_identical_retry_converges(tmp_path: Path):
    _coordinator, _collaboration, mission, agent, revision = _setup(tmp_path)
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    processes = [context.Process(target=_retry_process, args=(str(tmp_path), agent, revision, results)) for _ in range(2)]
    for process in processes:
        process.start()
    for process in processes:
        process.join(timeout=15)
        assert process.exitcode == 0
    outcomes = [results.get(timeout=2), results.get(timeout=2)]
    assert outcomes[0] == outcomes[1] and outcomes[0][-1] == "COMPLETE"
    assert len(mission.runs("tenant_1", "project_1")) == 1


def test_lock_order_is_coordinator_graph_collaboration_mission(tmp_path: Path, monkeypatch):
    coordinator, collaboration, mission, agent, revision = _setup(tmp_path)
    events: list[str] = []
    original_lock = coordinator._lock
    @contextmanager
    def traced_lock(*args, **kwargs):
        events.append("coordinator")
        with original_lock(*args, **kwargs) as value: yield value
    monkeypatch.setattr(coordinator, "_lock", traced_lock)
    original_graph = OperationGraphStore.locked_current_approved_revision
    @contextmanager
    def traced_graph(store):
        events.append("graph")
        with original_graph(store) as value: yield value
    monkeypatch.setattr(OperationGraphStore, "locked_current_approved_revision", traced_graph)
    original_room_lock = collaboration.room_lock
    @contextmanager
    def traced_room_lock(*args, **kwargs):
        events.append("collaboration")
        with original_room_lock(*args, **kwargs) as value: yield value
    monkeypatch.setattr(collaboration, "room_lock", traced_room_lock)
    original_run = mission.create_assignment_pending_run
    def traced_run(*args, **kwargs):
        events.append("mission"); return original_run(*args, **kwargs)
    monkeypatch.setattr(mission, "create_assignment_pending_run", traced_run)
    assert _assign(coordinator, agent, revision).state == "COMPLETE"
    assert events.index("coordinator") < events.index("graph") < events.index("collaboration") < events.index("mission")


def test_two_humans_same_client_request_id_are_isolated(tmp_path: Path):
    coordinator, collaboration, mission, agent, revision = _setup(tmp_path)
    CollaborationService(collaboration).add_member(tenant_id="tenant_1", project_id="project_1", actor_id="alice", member_id="bob", role="member", expected_revision=1)
    one = _assign(coordinator, agent, revision, actor="alice"); two = _assign(coordinator, agent, revision, actor="bob")
    assert one.transaction_id != two.transaction_id and len(mission.runs("tenant_1", "project_1")) == 2


def test_cross_operation_client_request_id_reuse_is_isolated(tmp_path: Path):
    coordinator, collaboration, _mission, agent, revision = _setup(tmp_path)
    normal = CollaborationService(collaboration).create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="alice",
        client_request_id="request_1", body="normal conversation",
    )
    assignment = _assign(coordinator, agent, revision)
    assert normal.id != assignment.message_id
    assert assignment.state == "COMPLETE" and _journal(tmp_path).parts[-2] == "conversation_assignment"


def test_cross_project_same_client_request_id_is_isolated(tmp_path: Path):
    first, _collab, first_mission, first_agent, first_revision = _setup(tmp_path)
    second, _collab, second_mission, second_agent, second_revision = _setup(tmp_path / "project_2_workspace", project_id="project_2", runs_root=tmp_path / "runs")
    left = _assign(first, first_agent, first_revision)
    right = _assign(second, second_agent, second_revision, project_id="project_2")
    assert left.run_id != right.run_id
    assert len(first_mission.runs("tenant_1", "project_1")) == len(second_mission.runs("tenant_1", "project_2")) == 1


def test_cross_tenant_same_client_request_id_is_isolated(tmp_path: Path):
    first, _collab, first_mission, first_agent, first_revision = _setup(tmp_path)
    second, _collab, second_mission, second_agent, second_revision = _setup(tmp_path / "tenant_2_workspace", tenant_id="tenant_2", runs_root=tmp_path / "runs")
    left = _assign(first, first_agent, first_revision)
    right = _assign(second, second_agent, second_revision, tenant_id="tenant_2")
    assert left.transaction_id != right.transaction_id
    assert len(first_mission.runs("tenant_1", "project_1")) == len(second_mission.runs("tenant_2", "project_1")) == 1
