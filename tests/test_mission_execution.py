from __future__ import annotations

from pathlib import Path

import hashlib
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest

from simulacra.harnesses import AgentRunResult, TerminalStatus
from simulacra.harnesses.codex import CodexIsolationSpec
from simulacra.missions import JsonMissionRepository, MissionConflictError, MissionService, MissionWorker
import simulacra.missions.worker as worker_module
from simulacra.missions.artifacts import artifact_bytes, artifact_evidence
from simulacra.operation_graph import OperationGraphStore, load_operation_graph


class _Harness:
    def __init__(self, seen: list[str]): self.seen = seen
    async def run(self, request):
        self.seen.append(request.role)
        return AgentRunResult(harness="codex", provider="openai", model_id="default", session_id="session-1",
            status=TerminalStatus.SUCCEEDED, response="token=sk-not-a-real-secret", structured_output={"token": "sk-nope"},
            changed_files=(), events=(), duration_seconds=0, usage={"steps": 1})


def _approved_workspace(path: Path) -> str:
    store = OperationGraphStore(path, tenant_id="tenant_1", project_id="project_1")
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"]["tenant_id"] = "tenant_1"; graph["metadata"]["project_id"] = "project_1"
    revision = store.create_revision(graph, expected_revision_hash=None); store.approve_revision(revision.revision_hash, actor_id="owner")
    return revision.revision_hash


def _unapproved_workspace(path: Path) -> tuple[OperationGraphStore, str]:
    store = OperationGraphStore(path, tenant_id="tenant_1", project_id="project_1")
    graph = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    graph["metadata"]["tenant_id"] = "tenant_1"; graph["metadata"]["project_id"] = "project_1"
    revision = store.create_revision(graph, expected_revision_hash=None)
    return store, revision.revision_hash


def _record_mission_contract(service: MissionService, revision: str) -> None:
    def mutate(records):
        records["mission"]["approved_contract_revision"] = revision
    service.repository.mutate("tenant_1", "project_1", mutate)


def _force_trigger_due(service: MissionService, trigger_id: str, due_at: str) -> None:
    service.repository.mutate(
        "tenant_1", "project_1",
        lambda records: records["triggers"][trigger_id].update({"next_due_at": due_at}),
    )


def test_automatic_cron_is_once_only_across_ticks_and_worker_replicas(tmp_path: Path):
    revision = _approved_workspace(tmp_path)
    root = tmp_path / "control"
    first_service = MissionService(JsonMissionRepository(root))
    first_service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    _record_mission_contract(first_service, revision)
    cron = first_service.add_trigger(
        "tenant_1", "project_1", {"type": "cron", "cron": "*/5 * * * *"},
    )
    condition = first_service.add_trigger(
        "tenant_1", "project_1",
        {"type": "condition", "condition": {"fact": "ready", "operator": "ne", "value": True}},
    )
    _force_trigger_due(first_service, cron.id, "2020-01-01T00:00:00+00:00")

    second_service = MissionService(JsonMissionRepository(root))
    first_worker = MissionWorker(first_service, tmp_path, "cron-1")
    second_worker = MissionWorker(second_service, tmp_path, "cron-2")
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(
            lambda worker: worker.schedule_due_cron("tenant_1", "project_1"),
            (first_worker, second_worker),
        ))
    assert sorted(len(outcome) for outcome in outcomes) == [0, 1]
    assert first_worker.schedule_due_cron("tenant_1", "project_1") == []

    runs = first_service.runs("tenant_1", "project_1")
    assert len(runs) == 1 and runs[0].contract_revision == revision
    triggers = {item.id: item for item in first_service.triggers("tenant_1", "project_1")}
    assert len(triggers[cron.id].handled_occurrences) == 1
    assert triggers[condition.id].handled_occurrences == {}


def test_automatic_cron_defers_disabled_not_due_and_unapproved_without_consuming(tmp_path: Path):
    graph_store, revision = _unapproved_workspace(tmp_path)
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    due = service.add_trigger(
        "tenant_1", "project_1", {"type": "cron", "cron": "*/5 * * * *"},
    )
    disabled = service.add_trigger(
        "tenant_1", "project_1", {"type": "cron", "cron": "*/5 * * * *", "enabled": False},
    )
    future = service.add_trigger(
        "tenant_1", "project_1", {"type": "cron", "cron": "*/5 * * * *"},
    )
    _force_trigger_due(service, due.id, "2020-01-01T00:00:00+00:00")
    _force_trigger_due(service, disabled.id, "2020-01-01T00:00:00+00:00")
    _force_trigger_due(service, future.id, "2099-01-01T00:00:00+00:00")
    before = {item.id: item.next_due_at for item in service.triggers("tenant_1", "project_1")}
    worker = MissionWorker(service, tmp_path, "cron")

    # A current but unapproved graph cannot consume the due occurrence.
    assert worker.schedule_due_cron("tenant_1", "project_1") == []
    deferred = {item.id: item for item in service.triggers("tenant_1", "project_1")}
    assert deferred[due.id].next_due_at == before[due.id]
    assert deferred[due.id].handled_occurrences == {}

    graph_store.approve_revision(revision, actor_id="owner")
    assert len(worker.schedule_due_cron("tenant_1", "project_1")) == 1
    assert service.mission("tenant_1", "project_1").approved_contract_revision == revision
    after = {item.id: item for item in service.triggers("tenant_1", "project_1")}
    assert after[disabled.id].next_due_at == before[disabled.id]
    assert after[disabled.id].handled_occurrences == {}
    assert after[future.id].next_due_at == before[future.id]
    assert after[future.id].handled_occurrences == {}


def test_condition_evaluation_requires_nonempty_fact_event(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    service.add_trigger(
        "tenant_1", "project_1",
        {"type": "condition", "condition": {"fact": "ready", "operator": "ne", "value": True}},
    )
    assert service.evaluate_due("tenant_1", "project_1", {}) == []
    assert service.evaluate_due("tenant_1", "project_1", {"other": False}) == []
    assert len(service.evaluate_due("tenant_1", "project_1", {"ready": False})) == 1


def test_worker_advances_agents_and_screens_response(tmp_path: Path):
    revision = _approved_workspace(tmp_path)
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    service.add_agent("tenant_1", "project_1", {"name": "B", "role": "r", "mandate": "m"})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision=revision)
    seen: list[str] = []
    worker = MissionWorker(service, tmp_path, "worker", lambda _config, **_kw: _Harness(seen))
    assert worker.run_once("tenant_1", "project_1").status == "queued"
    assert worker.run_once("tenant_1", "project_1").status == "succeeded"
    assert len(seen) == 2 and seen[0] != seen[1]
    assert "sk-not-a-real-secret" not in str(service.events("tenant_1", "project_1"))
    assert service.runs("tenant_1", "project_1")[0].id == run.id


def test_relative_artifact_is_evidenced_and_versioned(tmp_path: Path):
    revision = _approved_workspace(tmp_path); service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m", "autonomy": "execute_safely", "tools": ["artifact.write"]})
    class Writer:
        async def run(self, request):
            target = request.write_paths[0] / "result.py"; target.write_text("x = 1\n")
            assert artifact_bytes(request.workspace, target.relative_to(request.workspace).as_posix()) == b"x = 1\n"
            assert artifact_evidence(request.workspace, target.relative_to(request.workspace).as_posix())[1]["size"] == 6
            return AgentRunResult("codex", "openai", "model", "s", TerminalStatus.SUCCEEDED, "ok", {}, (target.relative_to(request.workspace),), (), 0, {})
    worker = MissionWorker(service, tmp_path, "worker", lambda _config, **_kw: Writer())
    for _ in range(2):
        service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision=revision)
        worker.run_once("tenant_1", "project_1")
    assert all(item.status == "succeeded" for item in service.runs("tenant_1", "project_1")), [(item.status, item.error) for item in service.runs("tenant_1", "project_1")]
    items = sorted(service.deliverables("tenant_1", "project_1"), key=lambda item: item.version)
    assert [item.version for item in items] == [1, 2] and items[-1].supersedes_id == items[0].id
    assert items[0].content_hash == hashlib.sha256(b"x = 1\n").hexdigest()


@pytest.mark.parametrize("outcome, expected_candidates", [("partial", 1), ("none", 0), ("outside", 0)])
def test_failed_budget_turn_keeps_only_confined_artifact_candidates_for_verification(tmp_path: Path, outcome: str, expected_candidates: int):
    """Budget termination cannot make completed tool writes invisible to humans."""
    revision = _approved_workspace(tmp_path)
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    service.add_agent("tenant_1", "project_1", {
        "name": "A", "role": "r", "mandate": "m", "autonomy": "execute_safely", "tools": ["artifact.write"],
    })
    run = service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision=revision)

    class BudgetStop:
        async def run(self, request):
            if outcome == "partial":
                target = request.write_paths[0] / "partial.md"
                target.write_text("partial evidence", encoding="utf-8")
                changed = (target.relative_to(request.workspace),)
            elif outcome == "outside":
                changed = (tmp_path.parent / "outside.md",)
            else:
                changed = ()
            return AgentRunResult(
                "codex", "openai", "model", "session", TerminalStatus.FAILED, None, {}, changed, (), 0,
                {"steps": 2}, {"code": "step_budget_exceeded"},
            )

    completed = MissionWorker(service, tmp_path, "worker", lambda _config, **_kw: BudgetStop()).run_once("tenant_1", "project_1")
    assert completed is not None and completed.status == "failed" and completed.id == run.id
    candidates = service.deliverables("tenant_1", "project_1")
    assert len(candidates) == expected_candidates
    if outcome == "partial":
        candidate = candidates[0]
        assert candidate.state == "awaiting_verification"
        assert candidate.source_ref == f"mission/run/{run.id}/failed-agent/{candidate.producer_id}"
        assert candidate.validation_evidence[0]["run_id"] == run.id
        assert candidate.validation_evidence[0]["run_status"] == "failed"


def test_code_staging_is_attempt_unique_and_preserves_prior_failed_candidate(tmp_path: Path):
    revision = _approved_workspace(tmp_path)
    (tmp_path / "app").mkdir()
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    service.add_agent("tenant_1", "project_1", {
        "name": "A", "role": "r", "mandate": "m", "autonomy": "execute_safely", "tools": ["code.write"],
    })
    run = service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision=revision)
    staging_roots: list[Path] = []
    class RetryingWriter:
        async def run(self, request):
            staging_roots.append(request.write_paths[0])
            target = request.write_paths[0] / "index.html"
            body = "first failed candidate" if len(staging_roots) == 1 else "second verified candidate"
            target.write_text(body, encoding="utf-8")
            return AgentRunResult(
                "codex", "openai", "model", "session", TerminalStatus.FAILED if len(staging_roots) == 1 else TerminalStatus.SUCCEEDED,
                None, {}, (target.relative_to(request.workspace),), (), 0, {},
            )
    worker = MissionWorker(service, tmp_path, "worker", lambda _config, **_kw: RetryingWriter())
    failed = worker.run_once("tenant_1", "project_1")
    assert failed is not None and failed.status == "failed"
    first = service.deliverables("tenant_1", "project_1")[0]
    first_bytes = artifact_bytes(tmp_path, str(first.artifact_ref))
    service.retry_run("tenant_1", "project_1", run.id, failed.revision, revision)
    succeeded = worker.run_once("tenant_1", "project_1")
    candidates = sorted(service.deliverables("tenant_1", "project_1"), key=lambda item: item.version)
    assert succeeded is not None and succeeded.status == "succeeded"
    assert len(staging_roots) == 2 and staging_roots[0] != staging_roots[1]
    assert artifact_bytes(tmp_path, str(first.artifact_ref)) == first_bytes == b"first failed candidate"
    assert [item.version for item in candidates] == [1, 2]


def test_approval_cas_and_expired_invocation_recovery(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m", "autonomy": "operate_with_checkpoints"})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    claimed = service.claim_next("tenant_1", "project_1", "one")
    gated = service.gate("tenant_1", "project_1", claimed.id, "checkpoint_required", "approve", lease_owner="one", agent_id=agent.id)
    approval = service.approvals("tenant_1", "project_1")[0]
    with pytest.raises(MissionConflictError): service.checkpoint_decision("tenant_1", "project_1", approval["id"], "owner", "approve", approval["revision"], gated.revision + 1)
    approved = service.checkpoint_decision("tenant_1", "project_1", approval["id"], "owner", "approve", approval["revision"], gated.revision)
    assert approved.status == "queued"
    service.cancel_run("tenant_1", "project_1", run.id, approved.revision)
    assert service.approvals("tenant_1", "project_1")[0]["status"] == "superseded"


def test_artifact_reader_rejects_relative_escape_and_symlink(tmp_path: Path):
    (tmp_path / "ok.txt").write_text("ok")
    (tmp_path / "link").symlink_to(tmp_path / "ok.txt")
    assert artifact_bytes(tmp_path, "ok.txt") == b"ok"
    for value in ("../ok.txt", "link", "C:\\nope", "a/../../b"):
        with pytest.raises(ValueError): artifact_bytes(tmp_path, value)


def test_live_lease_and_recovery_approval_is_singleton(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"}); run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    claimed = service.claim_next("tenant_1", "project_1", "one"); assert service.claim_next("tenant_1", "project_1", "two") is None
    service.repository.mutate("tenant_1", "project_1", lambda state: state["runs"][run.id].update({"lease_until": (datetime.now(UTC)-timedelta(seconds=1)).isoformat()}))
    assert service.claim_next("tenant_1", "project_1", "two").lease_owner == "two"
    started = service.mark_agent_started("tenant_1", "project_1", run.id, agent.id, "two")
    service.repository.mutate("tenant_1", "project_1", lambda state: state["runs"][run.id].update({"lease_until": (datetime.now(UTC)-timedelta(seconds=1)).isoformat()}))
    assert service.claim_next("tenant_1", "project_1", "three") is None
    approvals = service.approvals("tenant_1", "project_1"); assert len(approvals) == 1 and approvals[0]["code"] == "recovery_retry"
    assert service.claim_next("tenant_1", "project_1", "four") is None and len(service.approvals("tenant_1", "project_1")) == 1
    recovered = service.runs("tenant_1", "project_1")[0]
    assert service.checkpoint_decision("tenant_1", "project_1", approvals[0]["id"], "owner", "reject", approvals[0]["revision"], recovered.revision).status == "cancelled"


def test_checkpoint_actor_stale_cancel_and_retry(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"}); run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    service.claim_next("tenant_1", "project_1", "w"); gated = service.gate("tenant_1", "project_1", run.id, "checkpoint_required", "x", lease_owner="w", agent_id=agent.id); approval = service.approvals("tenant_1", "project_1")[0]
    with pytest.raises(PermissionError): service.checkpoint_decision("tenant_1", "project_1", approval["id"], agent.id, "approve", 1, gated.revision)
    cancelled = service.cancel_run("tenant_1", "project_1", run.id, gated.revision)
    with pytest.raises(MissionConflictError): service.checkpoint_decision("tenant_1", "project_1", approval["id"], "owner", "approve", 1, cancelled.revision)


@pytest.mark.parametrize("needle", ["prose sk-abcdefghijkl", "sk_live_abcdefghijkl", "sk_test_abcdefghijkl", "ghp_abcdefghijk", "github_pat_abcdefghijk", "xoxb-abcdefghijk", "xoxp-abcdefghijk", "xoxa-abcdefghijk", "AKIAABCDEFGHIJKLMNOP", "npm_abcdefghijk", "Bearer abcdefghijk", "eyJabcdefgh.abcdefgh.abcdefgh", "https://u:p@example/x", "?token=abcdefghi"])
def test_secret_matrix_never_persists_raw(tmp_path: Path, needle: str):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"}); run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    service.repository.mutate("tenant_1", "project_1", lambda records: service._event(records, run, "safe", {"response":needle, "usage":{"x":needle}}))
    state = (tmp_path / "control" / "tenant_1" / "project_1" / "missions" / "state.json").read_text()
    assert needle not in state
    assert needle not in str(service.trajectory_export("tenant_1", "project_1")["events"])


def test_provider_output_redacts_query_secret_variants_before_results_events_and_export(tmp_path: Path):
    """Provider output is durable training data, so URL credentials never survive it."""
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    service.claim_next("tenant_1", "project_1", "worker")
    service.mark_agent_started("tenant_1", "project_1", run.id, agent.id, "worker")

    raw_values = (
        "access_underscore_value", "access_hyphen_value", "api_underscore_value", "api_hyphen_value",
        "generic_token_value", "generic_key_value", "generic_secret_value", "generic_password_value",
        "generic_authorization_value", "generic_credential_value", "alice:provider-password",
    )
    provider_text = (
        "report completed safely "
        "https://example.test/?access_token=access_underscore_value&access-token=access_hyphen_value"
        "&api_key=api_underscore_value&api-key=api_hyphen_value&token=generic_token_value"
        "&key=generic_key_value&secret=generic_secret_value&password=generic_password_value"
        "&authorization=generic_authorization_value&credential=generic_credential_value "
        "https://alice:provider-password@example.test/private"
    )
    completed = service.record_result(
        "tenant_1", "project_1", run.id, "worker", agent.id,
        {"status": "succeeded", "response": provider_text,
         "structured_output": {"note": provider_text}, "events": [{"message": provider_text}],
         "usage": {"summary": "report completed safely"}},
        [],
    )
    service.repository.mutate(
        "tenant_1", "project_1",
        lambda records: service._event(records, completed, "provider_event", {"message": provider_text}),
    )

    persisted = (tmp_path / "control" / "tenant_1" / "project_1" / "missions" / "state.json").read_text()
    exported = str(service.trajectory_export("tenant_1", "project_1"))
    for raw in raw_values:
        assert raw not in persisted
        assert raw not in exported
    assert "report completed safely" in persisted
    assert "report completed safely" in exported


def test_no_crew_and_graph_missing_never_call_harness(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"}); run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    calls: list[int] = []
    worker = MissionWorker(service, tmp_path, "w", lambda *_args, **_kw: calls.append(1))
    assert worker.run_once("tenant_1", "project_1").error["code"] == "operation_graph_required" and not calls


def test_default_codex_requires_isolation_before_start(monkeypatch, tmp_path: Path):
    revision = _approved_workspace(tmp_path); service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision=revision)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("CMUL8_MISSION_ISOLATION_LAUNCHER", raising=False)
    result = MissionWorker(service, tmp_path, "worker").run_once("tenant_1", "project_1")
    assert result.error["code"] == "sandbox_unavailable" and result.invocation_id is None and result.invocation_started_at is None and result.current_agent_id is None
    assert not any(event["type"] == "agent_started" for event in service.events("tenant_1", "project_1"))


def _test_isolation_seam(monkeypatch, tmp_path: Path) -> Path:
    launcher = tmp_path / "mission-sandbox"; launcher.write_text("#!/bin/sh\n", encoding="utf-8"); launcher.chmod(0o555)
    runtime = tmp_path / "opt" / "codex"; (runtime / "bin").mkdir(parents=True)
    executable = runtime / "bin" / "codex"; executable.write_text("#!/bin/sh\n", encoding="utf-8"); executable.chmod(0o555)
    monkeypatch.setattr(worker_module, "_BAKED_LAUNCHER", launcher)
    monkeypatch.setattr(worker_module, "_CODEX_RUNTIME_ROOT", runtime)
    monkeypatch.setattr(worker_module, "_trusted_launcher", lambda path, configured: path == launcher and configured == str(launcher))
    monkeypatch.setenv("CMUL8_MISSION_ISOLATION_LAUNCHER", str(launcher))
    monkeypatch.setenv("CMUL8_MISSION_RUNTIME_ROOT", str(tmp_path / "mission-runtime"))
    original = CodexIsolationSpec.from_files.__func__
    monkeypatch.setattr(worker_module.CodexIsolationSpec, "from_files", classmethod(lambda cls, **kwargs: original(cls, allow_test_launcher=True, **kwargs)))
    return runtime


def test_worker_one_shot_manifest_is_private_random_and_cleaned(monkeypatch, tmp_path: Path):
    _test_isolation_seam(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"; workspace.mkdir(); read = workspace / "read"; write = workspace / "write"; read.mkdir(); write.mkdir()
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    worker = MissionWorker(service, workspace, "worker")
    first = worker._isolation(run, agent, (read,), (write,)); assert first is not None
    second = worker._isolation(run, agent, (read,), (write,)); assert second is not None
    assert first.manifest != second.manifest and first.manifest.parent == tmp_path / "mission-runtime"
    assert first.manifest.stat().st_mode & 0o777 == 0o600 and first.temp_root.stat().st_mode & 0o777 == 0o700
    payload = __import__("json").loads(first.manifest.read_text(encoding="utf-8"))
    assert payload["workspace"] == str(workspace.resolve()) and payload["run_id"] == run.id and payload["agent_id"] == agent.id
    assert payload["temp_root"] == str(first.temp_root.resolve()) and str(workspace) not in str(first.manifest)
    second_payload = __import__("json").loads(second.manifest.read_text(encoding="utf-8"))
    assert payload["codex_home"] == second_payload["codex_home"] and run.mission_id in payload["codex_home"] and agent.id in payload["codex_home"]
    first.cleanup(); second.cleanup()
    assert not first.manifest.exists() and not first.temp_root.exists() and not second.manifest.exists()


def test_codex_state_home_is_stable_per_mission_agent_and_isolated(monkeypatch, tmp_path: Path):
    _test_isolation_seam(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"; workspace.mkdir(); read = workspace / "read"; write = workspace / "write"; read.mkdir(); write.mkdir()
    service = MissionService(JsonMissionRepository(tmp_path / "control")); mission = service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    other_agent = service.add_agent("tenant_1", "project_1", {"name": "B", "role": "r", "mandate": "m"})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    resumed = type(run).from_dict({**run.to_dict(), "id": "run_resumed", "invocation_id": "invocation_new"})
    other_mission = type(run).from_dict({**run.to_dict(), "id": "run_other", "mission_id": "mission_other", "invocation_id": "invocation_other"})
    first = MissionWorker(service, workspace)._isolation(run, agent, (read,), (write,))
    second = MissionWorker(service, workspace)._isolation(resumed, agent, (read,), (write,))
    third = MissionWorker(service, workspace)._isolation(other_mission, agent, (read,), (write,))
    fourth = MissionWorker(service, workspace)._isolation(run, other_agent, (read,), (write,))
    assert all(item is not None for item in (first, second, third, fourth))
    homes = [__import__("json").loads(item.manifest.read_text())["codex_home"] for item in (first, second, third, fourth)]
    assert homes[0] == homes[1] and homes[0] != homes[2] and homes[0] != homes[3]
    assert mission.id in homes[0] and agent.id in homes[0]
    for item in (first, second, third, fourth): item.cleanup()


def test_worker_cleans_isolation_on_harness_factory_failure(monkeypatch, tmp_path: Path):
    _test_isolation_seam(monkeypatch, tmp_path)
    workspace = tmp_path / "workspace"; workspace.mkdir()
    revision = _approved_workspace(workspace)
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision=revision)
    captured = []
    original = MissionWorker._isolation
    def capture(self, *args):
        resource = original(self, *args); captured.append(resource); return resource
    def failing_factory(*args, **kwargs): raise RuntimeError("factory failed")
    monkeypatch.setattr(MissionWorker, "_isolation", capture)
    monkeypatch.setattr(worker_module, "create_harness", failing_factory)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    result = MissionWorker(service, workspace, "worker", failing_factory).run_once("tenant_1", "project_1")
    assert result.status == "failed" and captured and captured[0] is not None
    assert not captured[0].manifest.exists() and not captured[0].temp_root.exists()


def test_trigger_prompt_and_trajectory_recursively_redact_contextual_and_pem(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    secret = "token: actual-secret-value -----BEGIN PRIVATE KEY-----\nprivate-material\n-----END PRIVATE KEY----- sk_live_abcdefghijk"
    with pytest.raises(ValueError): service.create_run("tenant_1", "project_1", {"type": "manual", "nested": {"value": secret, "token": "plain-looking-secret-value"}})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    service.repository.mutate("tenant_1", "project_1", lambda records: service._event(records, run, "secret", {"deep": [{"value": secret}]}))
    persisted = (tmp_path / "control" / "tenant_1" / "project_1" / "missions" / "state.json").read_text()
    assert "actual-secret-value" not in persisted and "private-material" not in persisted and "sk_live_abcdefghijk" not in persisted and "plain-looking-secret-value" not in persisted
    assert "actual-secret-value" not in str(service.trajectory_export("tenant_1", "project_1"))


@pytest.mark.parametrize("payload", [
    {"title": "safe", "objective": "ship with sk_live_abcdefghijk embedded"},
    {"name": "A", "role": "r", "mandate": "Bearer abcdefghijk inside prose"},
    {"type": "condition", "condition": {"fact": "x", "operator": "eq", "value": "https://u:p@example.test/x"}},
])
def test_public_mission_inputs_reject_mid_string_secrets(tmp_path: Path, payload: dict[str, object]):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    with pytest.raises(ValueError):
        if payload.get("type") == "condition": service.add_trigger("tenant_1", "project_1", payload)
        elif "name" in payload: service.add_agent("tenant_1", "project_1", payload)
        else: service.update_mission("tenant_1", "project_1", payload, 1)


def test_worker_never_admits_codex_project_scope(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    with pytest.raises(ValueError, match="data scope"):
        service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m", "data_scope": [".codex"]})


def test_output_symlink_never_causes_outside_creation(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m", "autonomy": "execute_safely", "tools": ["artifact.write"]})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    outside = tmp_path / "outside"; outside.mkdir(); (tmp_path / "outputs").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="output directory"):
        MissionWorker(service, tmp_path)._paths(agent, run)
    assert not (outside / "missions").exists()


def test_default_worker_gates_missing_openai_credential(monkeypatch, tmp_path: Path):
    revision = _approved_workspace(tmp_path); service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision=revision)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert MissionWorker(service, tmp_path, "worker").run_once("tenant_1", "project_1").error["code"] == "credential_unavailable"


def test_execution_binding_rejects_prompt_or_capability_tamper(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision="a" * 64)
    service.claim_next("tenant_1", "project_1", "worker")
    prompt = "safe"
    binding = {"operation_graph_revision": 1, "operation_graph_hash": "a" * 64,
               "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "role": f"mission:{run.mission_id}:agent:{agent.id}",
               "tools": [], "autonomy": "assist", "execution_profile": run.execution_profile}
    binding["tools"] = ["code.write"]
    with pytest.raises(MissionConflictError, match="binding"):
        service.mark_agent_started("tenant_1", "project_1", run.id, agent.id, "worker", prompt, binding)


@pytest.mark.parametrize("budget", [
    {"unknown": 1}, {"max_steps": True}, {"max_steps": "10"}, {"max_steps": 0}, {"max_steps": 101},
    {"wall_timeout_seconds": 0}, {"wall_timeout_seconds": 601}, {"wall_timeout_seconds": 1.5},
])
def test_mission_and_agent_budget_schema_rejects_unknown_and_non_integer_limits(tmp_path: Path, budget: dict[str, object]):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    with pytest.raises(ValueError, match="budget"):
        service.bootstrap("tenant_1", "project_1", "owner", {"title": "x", "budget": budget})
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    with pytest.raises(ValueError, match="budget"):
        service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m", "budget": budget})


def test_effective_mission_budget_is_bound_prompted_and_wired_to_codex_request(tmp_path: Path):
    revision = _approved_workspace(tmp_path)
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {
        "title": "x", "budget": {"max_steps": 90, "wall_timeout_seconds": 500},
    })
    agent = service.add_agent("tenant_1", "project_1", {
        "name": "A", "role": "r", "mandate": "m", "budget": {"max_steps": 10, "wall_timeout_seconds": 120},
    })
    run = service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision=revision)
    captured: dict[str, object] = {}
    class BudgetHarness:
        async def run(self, request):
            captured["step_budget"] = request.step_budget
            captured["wall_timeout_seconds"] = request.wall_timeout_seconds
            captured["prompt"] = request.prompt
            captured["binding"] = service.runs("tenant_1", "project_1")[0].execution_binding
            return AgentRunResult("codex", "openai", "model", "session", TerminalStatus.SUCCEEDED, "ok", {}, (), (), 0, {})
    completed = MissionWorker(service, tmp_path, "worker", lambda _config, **_kw: BudgetHarness()).run_once("tenant_1", "project_1")
    expected = {"max_steps": 10, "wall_timeout_seconds": 120}
    assert completed is not None and completed.status == "succeeded"
    assert captured["step_budget"] == 10 and captured["wall_timeout_seconds"] == 120
    assert f"at most 10 tool actions and 120 seconds" in captured["prompt"]
    assert captured["binding"]["effective_budget"] == expected
    started_event = next(event for event in service.events("tenant_1", "project_1") if event["type"] == "agent_started")
    assert started_event["payload"]["effective_budget"] == expected


def test_execution_binding_rejects_effective_budget_tamper(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x", "budget": {"max_steps": 20, "wall_timeout_seconds": 300}})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m", "budget": {"max_steps": 10}})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision="a" * 64)
    service.claim_next("tenant_1", "project_1", "worker")
    prompt = "safe"
    binding = {
        "operation_graph_revision": 1, "operation_graph_hash": "a" * 64,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(), "role": f"mission:{run.mission_id}:agent:{agent.id}",
        "tools": [], "autonomy": "assist", "execution_profile": run.execution_profile,
        "effective_budget": {"max_steps": 11, "wall_timeout_seconds": 300},
    }
    with pytest.raises(MissionConflictError, match="binding"):
        service.mark_agent_started("tenant_1", "project_1", run.id, agent.id, "worker", prompt, binding)


def test_event_retention_pagination_and_overview_cap(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"})
    service.repository.mutate("tenant_1", "project_1", lambda records: [service._event(records, run, "event", {"n": n}) for n in range(2000)])
    assert not service.trajectory_page("tenant_1", "project_1", None, 1)["retention"]["truncated"]
    service.repository.mutate("tenant_1", "project_1", lambda records: service._event(records, run, "event", {"n": 2000}))
    assert len(service.events("tenant_1", "project_1")) == 2000 and len(service.events("tenant_1", "project_1", 100)) == 100
    first = service.trajectory_page("tenant_1", "project_1", None, 500)
    second = service.trajectory_page("tenant_1", "project_1", first["next_cursor"], 500)
    assert len(first["events"]) == len(second["events"]) == 500 and first["next_cursor"] and first["retention"]["truncated"] and first["retention"]["dropped_events"] == 1
    with pytest.raises(ValueError, match="cursor"): service.trajectory_page("tenant_1", "project_1", "expired", 10)


def test_approval_retention_prunes_closed_and_orders_recent_overview(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    def fill(records):
        for index in range(501):
            records["approvals"][f"closed-{index:03d}"] = {"id": f"closed-{index:03d}", "status": "consumed", "created_at": f"2026-01-01T00:00:{index:03d}", "updated_at": f"2026-01-01T00:00:{index:03d}"}
        service._cap_approvals(records)
    service.repository.mutate("tenant_1", "project_1", fill)
    values = service.repository.list_collection("tenant_1", "project_1", "approvals")
    assert len(values) == 500 and "closed-000" not in values
    assert [item["id"] for item in service.approvals("tenant_1", "project_1")] == [f"closed-{index:03d}" for index in range(1, 501)]


def test_approval_overview_keeps_old_actionable_record_and_worker_reads_exact_approval(tmp_path: Path):
    revision = _approved_workspace(tmp_path)
    service = MissionService(JsonMissionRepository(tmp_path / "control"))
    service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m", "autonomy": "operate_with_checkpoints"})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"}, verified_contract_revision=revision)
    claimed = service.claim_next("tenant_1", "project_1", "worker")
    gated = service.gate("tenant_1", "project_1", claimed.id, "checkpoint_required", "approve", lease_owner="worker", agent_id=agent.id)
    pending = service.approval("tenant_1", "project_1", gated.active_approval_id)
    assert pending is not None and pending["status"] == "pending"

    # Fill the durable response capacity with newer closed history. The old
    # pending record must remain in the Work/overview response, not fall below
    # a fixed newest-100 slice.
    def add_closed(records):
        for index in range(499):
            records["approvals"][f"closed-{index:03d}"] = {
                "id": f"closed-{index:03d}", "status": "consumed",
                "created_at": f"2027-01-01T00:00:{index:03d}", "updated_at": f"2027-01-01T00:00:{index:03d}",
            }
    service.repository.mutate("tenant_1", "project_1", add_closed)
    overview = service.approvals("tenant_1", "project_1")
    assert len(overview) == 500 and any(item["id"] == pending["id"] and item["status"] == "pending" for item in overview)
    assert len([item for item in overview if item["status"] != "pending"]) == 499

    approved = service.checkpoint_decision("tenant_1", "project_1", pending["id"], "owner", "approve", pending["revision"], gated.revision)
    assert service.approval("tenant_1", "project_1", pending["id"])["status"] == "approved"
    seen: list[str] = []
    class ApprovedHarness:
        async def run(self, request):
            seen.append(request.role)
            return AgentRunResult("codex", "openai", "model", "session", TerminalStatus.SUCCEEDED, "ok", {}, (), (), 0, {})
    completed = MissionWorker(service, tmp_path, "worker-2", lambda _config, **_kw: ApprovedHarness()).run_once("tenant_1", "project_1")
    assert completed is not None and completed.id == approved.id and completed.status == "succeeded" and seen


def test_approval_active_quota_gate_and_recovery_rollback_without_eviction(tmp_path: Path):
    service = MissionService(JsonMissionRepository(tmp_path / "control")); service.bootstrap("tenant_1", "project_1", "owner", {"title": "x"})
    agent = service.add_agent("tenant_1", "project_1", {"name": "A", "role": "r", "mandate": "m"})
    run = service.create_run("tenant_1", "project_1", {"type": "manual"}); claimed = service.claim_next("tenant_1", "project_1", "worker")
    def active(records):
        for index in range(500): records["approvals"][f"active-{index:03d}"] = {"id": f"active-{index:03d}", "run_id": "other", "status": "pending", "created_at": str(index), "updated_at": str(index)}
    service.repository.mutate("tenant_1", "project_1", active)
    state_path = tmp_path / "control" / "tenant_1" / "project_1" / "missions" / "state.json"; before = state_path.read_bytes()
    with pytest.raises(MissionConflictError): service.gate("tenant_1", "project_1", claimed.id, "checkpoint_required", "x", lease_owner="worker", agent_id=agent.id)
    assert state_path.read_bytes() == before and len(service.repository.list_collection("tenant_1", "project_1", "approvals")) == 500
    started = service.mark_agent_started("tenant_1", "project_1", run.id, agent.id, "worker")
    service.repository.mutate("tenant_1", "project_1", lambda records: records["runs"][run.id].update({"lease_until": (datetime.now(UTC)-timedelta(seconds=1)).isoformat()}))
    before = state_path.read_bytes()
    with pytest.raises(MissionConflictError): service.claim_next("tenant_1", "project_1", "other")
    assert state_path.read_bytes() == before and len(service.repository.list_collection("tenant_1", "project_1", "approvals")) == 500
