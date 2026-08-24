from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
import json

import pytest

from simulacra.missions import JsonMissionRepository, MissionConflictError, MissionService
import simulacra.missions.service as mission_service_module


def test_mission_vertical_slice_is_durable_and_deterministic(tmp_path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    mission = service.bootstrap("tenant_1", "project_1", "owner_1", {
        "title": "Launch", "objective": "Ship", "verifier_ids": ["reviewer_1"],
    })
    agent = service.add_agent("tenant_1", "project_1", {
        "name": "Builder", "role": "Engineer", "mandate": "Implement safely",
        "autonomy": "execute_safely",
    })
    assert agent.name == "Builder"
    run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    assert run.execution_profile["runtime"] == "codex"
    assert "provider" not in run.execution_profile

    first = service.create_deliverable("tenant_1", "project_1", {
        "type": "report", "name": "release", "source_ref": "room/release.md",
    }, "owner_1", b"v1")
    with pytest.raises(PermissionError):
        service.verify_deliverable("tenant_1", "project_1", first.id, "owner_1", first.content_hash, first.revision)
    verified = service.verify_deliverable("tenant_1", "project_1", first.id, "reviewer_1", first.content_hash, first.revision)
    assert verified.state == "verified"
    second = service.create_deliverable("tenant_1", "project_1", {
        "type": "report", "name": "release", "source_ref": "room/release.md",
    }, "owner_1", b"v2")
    assert second.version == 2 and second.state != "verified"
    with pytest.raises(MissionConflictError):
        service.verify_deliverable("tenant_1", "project_1", second.id, "reviewer_1", first.content_hash, second.revision)

    service.add_trigger("tenant_1", "project_1", {
        "type": "condition", "condition": {"fact": "approved", "operator": "eq", "value": True},
    })
    assert len(service.evaluate_due("tenant_1", "project_1", {"approved": True})) == 1
    assert len(service.evaluate_due("tenant_1", "project_1", {"approved": True})) == 1


def test_mission_rejects_runtime_control_fields(tmp_path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    mission = service.bootstrap("tenant_1", "project_1", "owner_1", {"title": "Launch"})
    assert mission.objective == "Launch"
    assert "human verification" in mission.definition_of_done
    with pytest.raises(ValueError, match="server-controlled"):
        service.add_agent("tenant_1", "project_1", {
            "name": "Nope", "role": "Engineer", "mandate": "Nope", "model": "user-choice",
        })


def test_repository_rejects_mission_scope_tampering_on_full_state_load(tmp_path):
    root = tmp_path / "control"
    service = MissionService(JsonMissionRepository(root))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    state_path = root / "tenant_1" / "project_1" / "missions" / "state.json"
    state = json.loads(state_path.read_text())
    state["mission"]["tenant_id"] = "tenant_other"
    state_path.write_text(json.dumps(state))
    with pytest.raises(ValueError, match="mission scope"):
        service.mission("tenant_1", "project_1")


def test_mission_transactions_screen_secrets_and_do_not_lose_agents(tmp_path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner_1", {"title": "Launch"})
    with pytest.raises(ValueError, match="credential material"):
        service.add_agent("tenant_1", "project_1", {
            "name": "Secret", "role": "Engineer", "mandate": "Nope",
            "budget": {"nested": {"token": "do-not-persist"}},
        })
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda index: service.add_agent("tenant_1", "project_1", {
            "name": f"Agent {index}", "role": "Engineer", "mandate": "Work safely",
        }), range(8)))
    assert len(service.agents("tenant_1", "project_1")) == 8
    persisted = (tmp_path / "control" / "tenant_1" / "project_1" / "missions" / "state.json").read_text()
    assert "do-not-persist" not in persisted


@pytest.mark.parametrize("policy, expected_count, expected_status", [
    ("queue", 2, "queued"), ("skip", 1, "queued"), ("replace", 2, "cancelled"), ("merge", 1, "queued"),
])
def test_trigger_concurrency_policies_and_idempotence(tmp_path, policy, expected_count, expected_status):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner_1", {"title": "Launch"})
    service.create_run("tenant_1", "project_1", {"type": "manual"})
    service.add_trigger("tenant_1", "project_1", {
        "type": "condition", "concurrency_policy": policy,
        "condition": {"fact": "ready", "operator": "eq", "value": True},
    })
    first = service.evaluate_due("tenant_1", "project_1", {"ready": True})
    second = service.evaluate_due("tenant_1", "project_1", {"ready": True})
    runs = service.runs("tenant_1", "project_1")
    assert len(runs) == expected_count
    if first and second:
        assert first[0].id == second[0].id
    assert ("cancelled" if policy == "replace" else runs[0].status) == expected_status


def test_cron_uses_sunday_zero_and_advances_revision(tmp_path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner_1", {"title": "Launch"})
    trigger = service.add_trigger("tenant_1", "project_1", {
        "type": "cron", "cron": "0 0 * * 0", "timezone": "UTC",
    })
    trigger.next_due_at = "2026-08-23T00:00:00+00:00"  # Sunday
    service.repository.mutate("tenant_1", "project_1", lambda records: records["triggers"].update({trigger.id: trigger.to_dict()}))
    service.evaluate_due("tenant_1", "project_1", at=datetime(2026, 8, 23, 0, 1, tzinfo=UTC))
    advanced = service.triggers("tenant_1", "project_1")[0]
    assert advanced.revision == 2
    assert datetime.fromisoformat(advanced.next_due_at).weekday() == 6


def test_automatic_cron_history_is_bounded_and_continues_past_retention(tmp_path, monkeypatch):
    occurrence_bound, run_bound = 8, 12
    monkeypatch.setattr(mission_service_module, "TRIGGER_OCCURRENCE_RETENTION", occurrence_bound)
    monkeypatch.setattr(mission_service_module, "RUN_HISTORY_RETENTION", run_bound)
    root = tmp_path / "control"
    service = MissionService(JsonMissionRepository(root))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "bounded"})
    trigger = service.add_trigger("tenant_1", "project_1", {"type": "cron", "cron": "* * * * *"})
    start = datetime(2025, 1, 1, tzinfo=UTC)
    service.repository.mutate(
        "tenant_1", "project_1",
        lambda records: records["triggers"][trigger.id].update({"next_due_at": start.isoformat()}),
    )
    iterations = occurrence_bound + run_bound + 12
    referenced_run_ids: list[str] = []
    for index in range(iterations):
        created = service.evaluate_cron_due(
            "tenant_1", "project_1", at=start.replace() + timedelta(minutes=index),
            verified_contract_revision="approved_graph",
        )
        assert len(created) == 1
        def finish(records, run_id=created[0].id, position=index):
            records["runs"][run_id].update({
                "status": "succeeded", "completed_at": datetime.now(UTC).isoformat(),
            })
            if position == 0:
                records["events"]["audit_reference"] = {"run_id": run_id}
                referenced_run_ids.append(run_id)
            elif position == 1:
                records["approvals"]["approval_reference"] = {"run_id": run_id, "status": "closed"}
                referenced_run_ids.append(run_id)
            elif position == 2:
                records["deliverables"]["deliverable_reference"] = {
                    "validation_evidence": [{"run_id": run_id}],
                }
                referenced_run_ids.append(run_id)
        service.repository.mutate("tenant_1", "project_1", finish)
    retained_trigger = service.triggers("tenant_1", "project_1")[0]
    assert len(retained_trigger.handled_occurrences) == occurrence_bound
    retained_runs = service.runs("tenant_1", "project_1")
    assert len(retained_runs) <= occurrence_bound + run_bound + len(referenced_run_ids)
    assert set(referenced_run_ids) <= {run.id for run in retained_runs}
    state_path = root / "tenant_1" / "project_1" / "missions" / "state.json"
    assert state_path.stat().st_size < 8 * 1024 * 1024
    assert len(service.evaluate_cron_due(
        "tenant_1", "project_1", at=start + timedelta(minutes=iterations),
        verified_contract_revision="approved_graph",
    )) == 1


def test_automatic_cron_applies_queue_backpressure_without_consuming_due_slot(tmp_path, monkeypatch):
    monkeypatch.setattr(mission_service_module, "AUTOMATIC_ACTIVE_RUN_LIMIT", 2)
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "bounded queue"})
    trigger = service.add_trigger("tenant_1", "project_1", {"type": "cron", "cron": "* * * * *"})
    start = datetime(2025, 1, 1, tzinfo=UTC)
    service.repository.mutate(
        "tenant_1", "project_1",
        lambda records: records["triggers"][trigger.id].update({"next_due_at": start.isoformat()}),
    )
    assert len(service.evaluate_cron_due("tenant_1", "project_1", at=start, verified_contract_revision="graph")) == 1
    assert len(service.evaluate_cron_due("tenant_1", "project_1", at=start + timedelta(minutes=1), verified_contract_revision="graph")) == 1
    blocked_due = service.triggers("tenant_1", "project_1")[0].next_due_at
    assert service.evaluate_cron_due("tenant_1", "project_1", at=start + timedelta(minutes=2), verified_contract_revision="graph") == []
    deferred = service.triggers("tenant_1", "project_1")[0]
    assert deferred.next_due_at == blocked_due and len(deferred.handled_occurrences) == 2
    first = service.runs("tenant_1", "project_1")[0]
    service.repository.mutate("tenant_1", "project_1", lambda records: records["runs"][first.id].update({"status": "succeeded"}))
    assert len(service.evaluate_cron_due("tenant_1", "project_1", at=start + timedelta(minutes=2), verified_contract_revision="graph")) == 1


def test_skip_due_cron_advances_without_creating_stale_run(tmp_path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner_1", {"title": "Launch"})
    service.create_run("tenant_1", "project_1", {"type": "manual"})
    trigger = service.add_trigger("tenant_1", "project_1", {
        "type": "cron", "cron": "*/5 * * * *", "timezone": "UTC", "concurrency_policy": "skip",
    })
    trigger.next_due_at = "2026-08-23T00:00:00+00:00"
    service.repository.mutate("tenant_1", "project_1", lambda records: records["triggers"].update({trigger.id: trigger.to_dict()}))
    assert service.evaluate_due("tenant_1", "project_1", at=datetime(2026, 8, 23, 0, 1, tzinfo=UTC)) == []
    advanced = service.triggers("tenant_1", "project_1")[0]
    assert advanced.revision == 2 and advanced.next_due_at == "2026-08-23T00:05:00+00:00"


def test_state_snapshot_is_crash_atomic_before_replace(tmp_path, monkeypatch):
    root = tmp_path / "control"
    service = MissionService(JsonMissionRepository(root))
    service.bootstrap("tenant_1", "project_1", "owner_1", {"title": "Launch"})
    trigger = service.add_trigger("tenant_1", "project_1", {"type": "cron", "cron": "*/5 * * * *"})
    before = (root / "tenant_1" / "project_1" / "missions" / "state.json").read_text()
    original = service.repository._replace_state
    def interrupted(*args, **kwargs):
        raise OSError("interrupted before replace")
    monkeypatch.setattr(service.repository, "_replace_state", interrupted)
    with pytest.raises(OSError):
        service.evaluate_due("tenant_1", "project_1", at=datetime(2099, 1, 1, tzinfo=UTC), verified_contract_revision="graph_b")
    reopened = MissionService(JsonMissionRepository(root))
    assert (root / "tenant_1" / "project_1" / "missions" / "state.json").read_text() == before
    assert reopened.runs("tenant_1", "project_1") == []
    assert reopened.triggers("tenant_1", "project_1")[0].next_due_at == trigger.next_due_at
    monkeypatch.setattr(service.repository, "_replace_state", original)


def test_server_contract_revision_is_bound_with_each_run_snapshot(tmp_path):
    root = tmp_path / "control"
    service = MissionService(JsonMissionRepository(root))
    service.bootstrap("tenant_1", "project_1", "owner_1", {"title": "Launch"})
    first = service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision="graph_a")
    assert first.contract_revision == service.mission("tenant_1", "project_1").approved_contract_revision == "graph_a"
    service.add_trigger("tenant_1", "project_1", {"type": "condition", "condition": {"fact": "ready", "operator": "eq", "value": True}})
    second = service.evaluate_due("tenant_1", "project_1", {"ready": True}, verified_contract_revision="graph_b")[0]
    state = json.loads((root / "tenant_1" / "project_1" / "missions" / "state.json").read_text())
    assert second.contract_revision == state["mission"]["approved_contract_revision"] == "graph_b"


def test_vixie_cron_dom_dow_matching_rules():
    from simulacra.missions.models import next_cron_due
    # Both restricted: DOM match alone (Aug 2, 2025 is Saturday) must run.
    assert next_cron_due("0 0 2 * 0", "UTC", datetime(2025, 8, 1, tzinfo=UTC)).date().isoformat() == "2025-08-02"
    # Both restricted: DOW match alone (Aug 3, 2025 is Sunday) must run.
    assert next_cron_due("0 0 2 * 0", "UTC", datetime(2025, 8, 2, tzinfo=UTC)).date().isoformat() == "2025-08-03"
    # DOM-only skips Sunday and reaches the 2nd.
    assert next_cron_due("0 0 2 * *", "UTC", datetime(2026, 8, 1, tzinfo=UTC)).date().isoformat() == "2026-08-02"
    # DOW-only Sunday=0.
    assert next_cron_due("0 0 * * 0", "UTC", datetime(2026, 8, 1, tzinfo=UTC)).date().isoformat() == "2026-08-02"


def test_handled_condition_skip_replay_is_inert(tmp_path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    active = service.create_run("tenant_1", "project_1", {"type": "manual"})
    trigger = service.add_trigger("tenant_1", "project_1", {"type": "condition", "concurrency_policy": "skip", "condition": {"fact": "ok", "operator": "eq", "value": True}})
    assert service.evaluate_due("tenant_1", "project_1", {"ok": True}) == []
    rows = service.repository.list_collection("tenant_1", "project_1", "runs"); rows[active.id]["status"] = "succeeded"
    service.repository.mutate("tenant_1", "project_1", lambda state: state["runs"].update(rows))
    assert service.evaluate_due("tenant_1", "project_1", {"ok": True}) == []
    assert len(service.runs("tenant_1", "project_1")) == 1
    assert list(service.triggers("tenant_1", "project_1")[0].handled_occurrences.values())[0]["outcome"] == "skipped"


def test_handled_condition_merge_replay_is_inert(tmp_path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    original = service.create_run("tenant_1", "project_1", {"type": "manual"})
    service.add_trigger("tenant_1", "project_1", {"type": "condition", "concurrency_policy": "merge", "condition": {"fact": "ok", "operator": "eq", "value": True}})
    merged = service.evaluate_due("tenant_1", "project_1", {"ok": True})[0]
    assert merged.id == original.id
    service.repository.mutate("tenant_1", "project_1", lambda state: state["runs"][original.id].update({"status": "succeeded"}))
    other = service.create_run("tenant_1", "project_1", {"type": "manual"})
    replay = service.evaluate_due("tenant_1", "project_1", {"ok": True})[0]
    assert replay.id == original.id and len(service.runs("tenant_1", "project_1")) == 2 and replay.id != other.id


@pytest.mark.parametrize("policy", ["replace", "merge"])
def test_live_trigger_never_mutates_or_overlaps_invocation(tmp_path, policy):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    original = service.create_run("tenant_1", "project_1", {"type": "manual"})
    service.claim_next("tenant_1", "project_1", "worker"); started = service.mark_agent_started("tenant_1", "project_1", original.id, agent.id, "worker")
    before, binding = started.trigger_snapshot.copy(), dict(started.execution_binding or {})
    service.add_trigger("tenant_1", "project_1", {"type": "condition", "concurrency_policy": policy, "condition": {"fact": "ready", "operator": "eq", "value": True}})
    outcome = service.evaluate_due("tenant_1", "project_1", {"ready": True, "nonce": 1}); runs = service.runs("tenant_1", "project_1")
    live = next(run for run in runs if run.id == original.id)
    assert live.status == "running" and live.trigger_snapshot == before and live.execution_binding == binding
    assert len(outcome) == 1 and outcome[0].id != original.id and outcome[0].status == "queued" and len(runs) == 2
    assert outcome[0].trigger_snapshot["facts"] == {"ready": True, "nonce": 1}
    handled = service.triggers("tenant_1", "project_1")[0].handled_occurrences
    handled_value = next(iter(handled.values()))
    assert handled_value["outcome"] == ("queued_after_live" if policy == "replace" else "deferred_live_run")
    assert handled_value["run_id"] == outcome[0].id and handled_value["handled_at"]
    assert service.evaluate_due("tenant_1", "project_1", {"ready": True, "nonce": 1})[0].id == outcome[0].id and len(service.runs("tenant_1", "project_1")) == 2


@pytest.mark.parametrize("policy", ["replace", "merge"])
def test_queued_trigger_retains_replace_and_merge_behavior(tmp_path, policy):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    original = service.create_run("tenant_1", "project_1", {"type": "manual"})
    service.add_trigger("tenant_1", "project_1", {"type": "condition", "concurrency_policy": policy, "condition": {"fact": "ready", "operator": "eq", "value": True}})
    outcome = service.evaluate_due("tenant_1", "project_1", {"ready": True, "nonce": 1}); runs = service.runs("tenant_1", "project_1")
    if policy == "replace":
        assert len(outcome) == 1 and len(runs) == 2 and next(run for run in runs if run.id == original.id).status == "cancelled"
    else:
        assert outcome[0].id == original.id and len(runs) == 1 and outcome[0].trigger_snapshot.get("merged_occurrences")


def test_claim_next_allows_only_one_live_mission_run_across_workers(tmp_path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    first = service.create_run("tenant_1", "project_1", {"type": "manual"})
    second = service.create_run("tenant_1", "project_1", {"type": "manual"})

    claimed = service.claim_next("tenant_1", "project_1", "worker_1")
    assert claimed is not None and claimed.id in {first.id, second.id} and claimed.lease_owner == "worker_1"
    assert service.claim_next("tenant_1", "project_1", "worker_2") is None

    # A checkpoint/release ends the worker-owned live execution, allowing the
    # other queued run to be claimed by another replica.
    service.gate("tenant_1", "project_1", claimed.id, "needs_human", "release", lease_owner="worker_1")
    next_run = service.claim_next("tenant_1", "project_1", "worker_2")
    assert next_run is not None and next_run.id in {first.id, second.id} - {claimed.id} and next_run.lease_owner == "worker_2"


def test_numeric_cron_forms_and_invalid_inputs():
    from simulacra.missions.models import next_cron_due
    start = datetime(2025, 1, 1, tzinfo=UTC)
    assert next_cron_due("0 0 * * 1-5", "UTC", start).weekday() in range(5)
    assert next_cron_due("0 0 1-10/3 * *", "UTC", start).day in {1, 4, 7, 10}
    assert next_cron_due("0 0 2,4 * *", "UTC", start).day in {2, 4}
    assert next_cron_due("0 0 * * 7", "UTC", datetime(2025, 8, 2, tzinfo=UTC)).weekday() == 6
    assert next_cron_due("0 0 29 2 *", "UTC", datetime(2025, 1, 1, tzinfo=UTC)).date().isoformat() == "2028-02-29"
    for expression in ("0 0 5-2 * *", "0 0 32 * *", "*/0 * * * *", "0 0 1- * *"):
        with pytest.raises(ValueError): next_cron_due(expression, "UTC", start)


def test_handled_cron_skip_replay_is_inert(tmp_path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    active = service.create_run("tenant_1", "project_1", {"type": "manual"})
    trigger = service.add_trigger("tenant_1", "project_1", {"type": "cron", "cron": "*/5 * * * *", "concurrency_policy": "skip"})
    due = "2026-08-23T00:00:00+00:00"
    trigger.next_due_at = due
    service.repository.mutate("tenant_1", "project_1", lambda state: state["triggers"].update({trigger.id: trigger.to_dict()}))
    assert service.evaluate_due("tenant_1", "project_1", at=datetime(2026, 8, 23, 0, 1, tzinfo=UTC)) == []
    advanced = service.triggers("tenant_1", "project_1")[0]
    ledger = dict(advanced.handled_occurrences)
    assert list(ledger.values())[0]["outcome"] == "skipped" and advanced.next_due_at != due
    service.repository.mutate("tenant_1", "project_1", lambda state: (state["runs"][active.id].update({"status": "succeeded"}), state["triggers"][trigger.id].update({"next_due_at": due})))
    assert service.evaluate_due("tenant_1", "project_1", at=datetime(2026, 8, 23, 0, 1, tzinfo=UTC)) == []
    replayed = service.triggers("tenant_1", "project_1")[0]
    assert len(service.runs("tenant_1", "project_1")) == 1 and replayed.handled_occurrences == ledger


def test_new_deliverable_always_awaits_verification(tmp_path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x", "verifier_ids": ["reviewer"]})
    payload = {"type": "report", "name": "R", "source_ref": "r.md", "artifact_ref": "r.md"}
    item = service.create_deliverable("tenant_1", "project_1", payload, "owner", b"v1")
    assert item.state == "awaiting_verification" and item.verified_by is None and item.verified_hash is None and item.verified_at is None
    for state in ("verified", "published"):
        with pytest.raises(ValueError): service.create_deliverable("tenant_1", "project_1", {**payload, "state": state}, "owner", b"v2")
    assert service.verify_deliverable("tenant_1", "project_1", item.id, "reviewer", item.content_hash, item.revision).state == "verified"


def test_legacy_split_files_import_once_then_state_is_authoritative(tmp_path):
    root = tmp_path / "control"; seeded = MissionService(JsonMissionRepository(root))
    seeded.bootstrap("tenant_1", "project_1", "owner", {"title": "legacy"})
    seeded.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    seeded.create_run("tenant_1", "project_1", {"type": "manual"})
    directory = root / "tenant_1" / "project_1" / "missions"
    state = json.loads((directory / "state.json").read_text())
    for name in ("mission", "agents", "runs", "triggers", "deliverables"):
        (directory / f"{name}.json").write_text(json.dumps(state[name]))
    (directory / "state.json").unlink()
    service = MissionService(JsonMissionRepository(root))
    assert service.mission("tenant_1", "project_1").title == "legacy" and len(service.agents("tenant_1", "project_1")) == 1
    service.add_agent("tenant_1", "project_1", {"name": "B", "role": "r", "mandate": "m"})
    assert (directory / "state.json").exists()
    (directory / "agents.json").write_text("{}")
    assert len(MissionService(JsonMissionRepository(root)).agents("tenant_1", "project_1")) == 2
