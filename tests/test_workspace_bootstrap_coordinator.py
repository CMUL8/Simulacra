from __future__ import annotations

import threading
import multiprocessing
from pathlib import Path

import pytest
import simulacra.workplace.bootstrap_coordinator as bootstrap_module
from simulacra.operation_graph.errors import RevisionConflictError

from simulacra.demo.identity import ensure_bootstrap
from simulacra.demo.tenants import default_tenant_id
from simulacra.demo import runs
from simulacra.demo import sources as source_module
from simulacra.workplace.bootstrap_coordinator import WorkspaceBootstrapCoordinator
from simulacra.collaboration.errors import ConflictError
from simulacra.workplace.source_staging import SourceStaging


def _bootstrap_from_process(root: str, tenant: str, source_ref: str, barrier, results) -> None:
    """Fork-safe worker used by the cross-process reservation proof."""
    runs.RUNS_DIR = Path(root)
    coordinator = WorkspaceBootstrapCoordinator(runs_root=root)
    barrier.wait()
    record = coordinator.begin(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source_ref))
    results.put((record["reserved_project_id"], record["state"], record["graph_result_revision"]))


def _request(source_ref: str) -> dict:
    return {"client_request_id": "bootstrap_request", "prompt": "Create an operating report", "goal": "A reliable report",
            "design_brief": None, "artifact_kind": "report", "staged_source_refs": [source_ref]}


def _setup(tmp_path, monkeypatch):
    ensure_bootstrap(); tenant = default_tenant_id()
    monkeypatch.setattr(runs, "RUNS_DIR", tmp_path)
    coordinator = WorkspaceBootstrapCoordinator(runs_root=tmp_path)
    source = coordinator.sources.stage(tenant_id=tenant, actor_id="bootstrap_owner", client_request_id="source_request",
                                       filename="evidence.csv", media_type="text/csv", data=b"a,b\n1,2\n")
    return coordinator, tenant, source


def test_workspace_bootstrap_retry_returns_reserved_project_and_single_children(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    first = coordinator.begin(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    retry = coordinator.begin(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    assert first["reserved_project_id"] == retry["reserved_project_id"]
    assert retry["state"] == "COMPLETE"
    assert coordinator.public(retry)[0] == 200


def test_project_visibility_fails_closed_for_marked_mission_without_a_valid_complete_journal(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.begin(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    project_id = record["reserved_project_id"]
    assert coordinator.project_is_public(tenant_id=tenant, project_id=project_id)

    path = coordinator._path(tenant, "bootstrap_owner", "bootstrap_request")
    pending = coordinator._read(path)
    pending["state"] = "PREPARED"
    bootstrap_module._atomic_json(path, pending)
    assert not coordinator.project_is_public(tenant_id=tenant, project_id=project_id)

    # A retained marker without its matching journal may be an interrupted
    # setup; it must remain hidden instead of looking like a usable Mission.
    path.unlink()
    assert not coordinator.project_is_public(tenant_id=tenant, project_id=project_id)

    unrelated_request = {**_request(source.source_ref), "client_request_id": "other_bootstrap_request"}
    coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=unrelated_request)
    assert not coordinator.project_is_public(tenant_id=tenant, project_id=project_id)

    # A corrupt matching file is equally unsafe and cannot make the Mission
    # public just because a different valid journal is nearby.
    bootstrap_module._atomic_json(path, {"not": "a bootstrap journal"})
    assert not coordinator.project_is_public(tenant_id=tenant, project_id=project_id)

    # A legacy state has no marker and remains visible.
    state = runs.load_state(project_id)
    state.prime.pop("bootstrap_request_hash", None)
    runs.save_state(state)
    assert coordinator.project_is_public(tenant_id=tenant, project_id=project_id)


def test_concurrent_workspace_bootstrap_requests_converge_on_one_reserved_project(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    results = []
    def submit(): results.append(coordinator.begin(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref)))
    workers = [threading.Thread(target=submit) for _ in range(2)]
    [worker.start() for worker in workers]; [worker.join() for worker in workers]
    assert {result["reserved_project_id"] for result in results}.__len__() == 1


def test_barrier_coordinated_cross_process_bootstrap_converges_on_one_complete_child_set(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    context = multiprocessing.get_context("fork")
    barrier = context.Barrier(2)
    results = context.Queue()
    workers = [context.Process(target=_bootstrap_from_process, args=(str(tmp_path), tenant, source.source_ref, barrier, results)) for _ in range(2)]
    for worker in workers: worker.start()
    for worker in workers: worker.join(15)
    assert all(worker.exitcode == 0 for worker in workers)
    returned = [results.get(timeout=2) for _ in workers]
    assert len({item[0] for item in returned}) == len({item[2] for item in returned}) == 1
    complete = coordinator.begin(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    assert complete["state"] == "COMPLETE"
    assert coordinator.public(complete)[0] == 200


def test_bootstrap_status_is_actor_tenant_scoped_and_reports_provisioning_then_complete(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    assert coordinator.public(record)[0] == 202
    with pytest.raises(KeyError): coordinator.lookup(tenant_id=tenant, actor_id="other_human", transaction_id=record["transaction_id"])
    complete = coordinator.recover(record)
    assert coordinator.public(complete)[0] == 200


def test_workspace_bootstrap_fault_injection_recovers_each_reservation_and_child_boundary(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    assert record["state"] == "PREPARED"
    assert coordinator.recovery_tick() == 1
    recovered = coordinator.lookup(tenant_id=tenant, actor_id="bootstrap_owner", transaction_id=record["transaction_id"])
    assert recovered["state"] == "COMPLETE"


def test_bootstrap_recovery_tick_resumes_durable_graph_build_intent_after_restart(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    restarted = WorkspaceBootstrapCoordinator(runs_root=tmp_path)
    assert restarted.recovery_tick() == 1
    assert restarted.lookup(tenant_id=tenant, actor_id="bootstrap_owner", transaction_id=record["transaction_id"])["state"] == "COMPLETE"


def test_enabled_bootstrap_never_reports_complete_when_a_required_child_is_missing(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.begin(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    (runs.project_dir(record["reserved_project_id"]) / "inputs" / "data-room" / "evidence.csv").unlink()
    retrieved = coordinator.lookup(tenant_id=tenant, actor_id="bootstrap_owner", transaction_id=record["transaction_id"])
    assert coordinator.public(retrieved)[0] == 202
    assert coordinator.recover(record)["state"] == "COMPLETE"


def test_existing_room_with_another_owner_aborts_before_commit(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    coordinator.collaboration.create_room(tenant_id=tenant, project_id=record["reserved_project_id"], creator_id="other_owner")
    recovered = coordinator.recover(record)
    assert recovered["state"] == "ABORTED"


def test_unrelated_room_creation_failure_remains_recoverable(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    monkeypatch.setattr(coordinator.collaboration, "create_room", lambda **_kwargs: (_ for _ in ()).throw(ConflictError("another write conflict")))
    assert coordinator.recover(record)["state"] == "PREPARED"


def test_recovery_tick_skips_terminal_journals_before_its_bounded_work(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    for number in range(101):
        request = _request(source.source_ref)
        request["client_request_id"] = f"a_terminal_{number:03d}"
        record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=request)
        record["state"] = "COMPLETE"
        coordinator._write(coordinator._path(tenant, "bootstrap_owner", request["client_request_id"]), record)
    target = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    assert coordinator.recovery_tick(limit=1) == 1
    assert coordinator.lookup(tenant_id=tenant, actor_id="bootstrap_owner", transaction_id=target["transaction_id"])["state"] == "COMPLETE"


def test_failed_journal_fsync_never_returns_an_advanced_in_memory_state(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    original = bootstrap_module._atomic_json
    monkeypatch.setattr(bootstrap_module, "_atomic_json", lambda *_args: (_ for _ in ()).throw(OSError("sync failed")))
    result = coordinator.recover(record)
    assert result["state"] == "PREPARED"
    monkeypatch.setattr(bootstrap_module, "_atomic_json", original)
    assert coordinator.recover(record)["state"] == "COMPLETE"


@pytest.mark.parametrize("write_number", range(1, 7))
def test_restart_converges_after_every_journal_transition_write_failure(tmp_path, monkeypatch, write_number):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    original = bootstrap_module._atomic_json
    calls = 0

    def fail_one_transition(path, payload):
        nonlocal calls
        calls += 1
        if calls == write_number:
            raise OSError("simulated journal boundary loss")
        return original(path, payload)

    monkeypatch.setattr(bootstrap_module, "_atomic_json", fail_one_transition)
    interrupted = coordinator.recover(record)
    assert interrupted["state"] != "COMPLETE"
    monkeypatch.setattr(bootstrap_module, "_atomic_json", original)
    assert coordinator.recover(record)["state"] == "COMPLETE"


def test_post_replace_journal_sync_failure_returns_prior_state_until_retry_reestablishes_durability(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    original = bootstrap_module._fsync_dir
    calls = 0

    def fail_the_post_replace_sync(directory):
        nonlocal calls
        calls += 1
        # First sync trusts PREPARED; second is the project-contract replace.
        if calls == 2:
            raise OSError("post-replace sync failed")
        return original(directory)

    monkeypatch.setattr(bootstrap_module, "_fsync_dir", fail_the_post_replace_sync)
    uncertain = coordinator.recover(record)
    assert uncertain["state"] == "PREPARED"
    assert uncertain["_durability_uncertain"] is True
    assert coordinator.public(uncertain)[0] == 202
    monkeypatch.setattr(bootstrap_module, "_fsync_dir", original)
    assert coordinator.recover(record)["state"] == "COMPLETE"


def test_post_replace_project_state_sync_failure_stays_provisioning_until_retry(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    original = runs._fsync_dir
    monkeypatch.setattr(runs, "_fsync_dir", lambda _directory: (_ for _ in ()).throw(OSError("state sync failed")))
    interrupted = coordinator.recover(record)
    assert interrupted["state"] == "PREPARED"
    assert coordinator.public(interrupted)[0] == 202
    monkeypatch.setattr(runs, "_fsync_dir", original)
    assert coordinator.recover(record)["state"] == "COMPLETE"


def test_source_replace_sync_failure_stays_provisioning_until_retry(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    original = source_module.sync_data_room
    monkeypatch.setattr(source_module, "sync_data_room", lambda _project: (_ for _ in ()).throw(OSError("source sync failed")))
    interrupted = coordinator.recover(record)
    assert interrupted["state"] == "PREPARED"
    assert coordinator.public(interrupted)[0] == 202
    monkeypatch.setattr(source_module, "sync_data_room", original)
    assert coordinator.recover(record)["state"] == "COMPLETE"


def test_corrupt_or_relocated_journal_is_unavailable_before_it_can_create_children(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    path = coordinator._path(tenant, "bootstrap_owner", "bootstrap_request")
    corrupted = coordinator._read(path)
    corrupted["canonical_request_hash"] = "0" * 64
    bootstrap_module._atomic_json(path, corrupted)
    with pytest.raises(KeyError):
        coordinator.lookup(tenant_id=tenant, actor_id="bootstrap_owner", transaction_id=record["transaction_id"])
    assert not runs.project_dir(record["reserved_project_id"]).exists()

    relocated = coordinator._path(tenant, "other_human", "bootstrap_request")
    relocated.parent.mkdir(parents=True, exist_ok=True)
    bootstrap_module._atomic_json(relocated, record)
    with pytest.raises(KeyError):
        coordinator.lookup(tenant_id=tenant, actor_id="other_human", transaction_id=record["transaction_id"])


def test_existing_reserved_project_recovers_when_tenant_has_reached_quota(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    original_room = coordinator.collaboration.create_room
    monkeypatch.setattr(coordinator.collaboration, "create_room", lambda **_kwargs: (_ for _ in ()).throw(OSError("interrupted after project")))
    assert coordinator.recover(record)["state"] == "PREPARED"
    monkeypatch.setattr(coordinator.collaboration, "create_room", original_room)
    # A new allocation would now be rejected, but the reservation has already
    # created its project and must finish instead of being stranded.
    from simulacra.demo import tenants
    monkeypatch.setattr(tenants, "assert_under_project_quota", lambda _tenant: (_ for _ in ()).throw(AssertionError("quota must not run on retry")))
    assert coordinator.recover(record)["state"] == "COMPLETE"


@pytest.mark.parametrize("failure", [
    ValueError("graph scope mismatch"),
    ValueError("graph hash mismatch"),
    RevisionConflictError("competing graph head"),
])
def test_deterministic_predecision_graph_conflict_aborts_with_stable_public_result(tmp_path, monkeypatch, failure):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    monkeypatch.setattr(bootstrap_module, "build_bootstrap_graph", lambda *_args, **_kwargs: (_ for _ in ()).throw(failure))
    aborted = coordinator.recover(record)
    assert aborted["state"] == "ABORTED"
    assert coordinator.public(aborted)[0] == 409


def test_complete_requires_the_frozen_project_contract(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.begin(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    state = runs.load_state(record["reserved_project_id"])
    state.goal = "changed after reservation"
    runs.save_state(state)
    assert coordinator.public(record)[0] == 202


def test_complete_requires_project_to_carry_its_bootstrap_reservation_hash(tmp_path, monkeypatch):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.begin(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    state = runs.load_state(record["reserved_project_id"])
    state.prime.pop("bootstrap_request_hash", None)
    runs.save_state(state)
    assert coordinator.public(record)[0] == 202


@pytest.mark.parametrize("fault", ["missing_blob", "tampered_blob", "missing_record"])
def test_invalid_staged_source_before_commit_aborts_with_stable_public_result(tmp_path, monkeypatch, fault):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    if fault == "missing_blob":
        coordinator.sources._blob_path(source.canonical_content_sha256).unlink()
    elif fault == "tampered_blob":
        coordinator.sources._blob_path(source.canonical_content_sha256).write_bytes(b"tampered")
    else:
        coordinator.sources._record_path(tenant, "bootstrap_owner", "source_request").unlink()
    aborted = coordinator.recover(record)
    assert aborted["state"] == "ABORTED"
    status, payload = coordinator.public(aborted)
    assert status == 409
    assert payload["code"] == "bootstrap_aborted"


@pytest.mark.parametrize("target", ["control_root", "tenant_ancestor", "journal_leaf"])
def test_bootstrap_journal_paths_reject_symlink_matrix(tmp_path, monkeypatch, target):
    coordinator, tenant, source = _setup(tmp_path, monkeypatch)
    outside = tmp_path / "outside"; outside.mkdir()
    if target == "control_root":
        root = tmp_path / "linked-runs"; root.symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError, match="bootstrap storage unavailable"):
            WorkspaceBootstrapCoordinator(runs_root=root)
        return
    record = coordinator.reserve(tenant_id=tenant, actor_id="bootstrap_owner", request=_request(source.source_ref))
    path = coordinator._path(tenant, "bootstrap_owner", "bootstrap_request")
    if target == "tenant_ancestor":
        tenant_dir = coordinator.root / tenant
        moved = outside / tenant; tenant_dir.rename(moved)
        tenant_dir.symlink_to(moved, target_is_directory=True)
    else:
        path.unlink(); path.symlink_to(outside / "journal.json")
    with pytest.raises((ValueError, KeyError)):
        coordinator.lookup(tenant_id=tenant, actor_id="bootstrap_owner", transaction_id=record["transaction_id"])
