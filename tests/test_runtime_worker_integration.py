from __future__ import annotations

from pathlib import Path
import copy

import pytest

from simulacra.observability import JsonlTelemetryRepository, TelemetryQuery
from simulacra.operation_graph import OperationGraphStore, load_operation_graph
from simulacra.runtime import ActionExecutionError, CredentialPolicyError, RuntimeAuthorizationError, RuntimePlane, RuntimeWorker


ROOT = Path(__file__).resolve().parents[1]


def _plane(tmp_path: Path, *, observability_repository: object | None = None) -> tuple[RuntimePlane, JsonlTelemetryRepository]:
	project = tmp_path / "project"
	project.mkdir()
	store = OperationGraphStore(project, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_revision(load_operation_graph(ROOT / "schemas" / "operation-graph.v0.yaml"), expected_revision_hash=None)
	store.approve_revision(revision.revision_hash, actor_id="reviewer")
	telemetry = JsonlTelemetryRepository(tmp_path / "telemetry")
	return (
		RuntimePlane.from_approved_revision(
			tmp_path / "runtime", store, revision.revision_hash, environment_id="env_prod",
			observability_repository=observability_repository if observability_repository is not None else telemetry,
		),
		telemetry,
	)


def test_durable_worker_executes_only_enveloped_approved_jobs_and_emits_console_trace(tmp_path: Path):
	plane, telemetry = _plane(tmp_path)
	workflow = plane.workflows.start("workflow_resolve_case")
	job = plane.scheduler.enqueue(
		"workflow.transition",
		{
			"instance_id": workflow.id,
			"target_state": "triaged",
			"expected_state": "new",
			"expected_revision": 0,
			"idempotency_key": "worker-transition-1",
		},
		idempotency_key="durable-job-1",
	)
	assert job.operation_graph_version == plane.policy.revision_hash
	assert job.payload["_cmul8"] == {
		"job_id": job.id,
		"tenant_id": "tenant_acme",
		"environment_id": "env_prod",
		"project_id": "project_support",
		"operation_graph_revision": plane.policy.revision_hash,
	}

	worker = RuntimeWorker(plane, "worker_integration", queue_reachable=lambda: True)
	assert worker.readiness()["status"] == "ready"
	completed = worker.run_once()
	assert completed is not None and completed.status == "succeeded"
	assert plane.workflows.get(workflow.id).state == "triaged"

	events = telemetry.query(TelemetryQuery(tenant_id="tenant_acme", trace_id=f"trace_{job.id}"))
	assert len(events) == 1
	assert events[0].application_id == "project_support"
	assert events[0].status.value == "succeeded"
	assert events[0].attributes["job_id"] == job.id

	with pytest.raises(CredentialPolicyError):
		plane.scheduler.enqueue("workflow.transition", {"authorization": "Bearer secret"})


def test_unsupported_job_is_dead_lettered_without_dispatching(tmp_path: Path):
	plane, telemetry = _plane(tmp_path)
	job = plane.scheduler.enqueue("source.edit", {"path": "simulacra/runtime/worker.py"}, max_attempts=1)
	failed = RuntimeWorker(plane, "worker_integration").run_once()
	assert failed is not None and failed.id == job.id and failed.status == "dead_letter"
	assert failed.last_error == "RuntimeAuthorizationError: runtime job execution failed"
	events = telemetry.query(TelemetryQuery(tenant_id="tenant_acme", trace_id=f"trace_{job.id}"))
	assert events[0].status.value == "failed"
	assert events[0].attributes["error_type"] == "RuntimeAuthorizationError"


class _ThrowingObservabilityRepository:
	def append(self, _event: object) -> None:
		raise OSError("telemetry unavailable")


def test_worker_persists_terminal_success_when_observability_append_fails(tmp_path: Path):
	plane, _ = _plane(tmp_path, observability_repository=_ThrowingObservabilityRepository())
	workflow = plane.workflows.start("workflow_resolve_case")
	job = plane.scheduler.enqueue("workflow.transition", {
		"instance_id": workflow.id, "target_state": "triaged", "expected_state": "new", "expected_revision": workflow.revision,
	})

	completed = RuntimeWorker(plane, "worker_telemetry_success").run_once()

	assert completed is not None and completed.id == job.id and completed.status == "succeeded"
	assert plane.scheduler.get(job.id).status == "succeeded"
	assert plane.workflows.get(workflow.id).state == "triaged"
	assert RuntimeWorker(plane, "worker_telemetry_success").run_once() is None


def test_worker_persists_retry_state_when_failure_telemetry_append_fails(tmp_path: Path):
	plane, _ = _plane(tmp_path, observability_repository=_ThrowingObservabilityRepository())
	job = plane.scheduler.enqueue("source.edit", {"path": "no-source-writes"}, max_attempts=2)

	failed = RuntimeWorker(plane, "worker_telemetry_failure").run_once()

	assert failed is not None and failed.id == job.id and failed.status == "queued"
	assert plane.scheduler.get(job.id).status == "queued"
	assert failed.last_error == "RuntimeAuthorizationError: runtime job execution failed"


def test_workers_claim_only_jobs_bound_to_their_exact_graph_revision(tmp_path: Path):
	project = tmp_path / "project"
	project.mkdir()
	store = OperationGraphStore(project, tenant_id="tenant_acme", project_id="project_support")
	first_graph = load_operation_graph(ROOT / "schemas/operation-graph.v0.yaml")
	first = store.create_revision(first_graph, expected_revision_hash=None)
	store.approve_revision(first.revision_hash, actor_id="reviewer")
	second_graph = copy.deepcopy(first_graph)
	second_graph["metadata"]["version"] = 1
	second_graph["entities"][0]["fields"].append({"name": "priority", "type": "integer"})
	second = store.create_revision(second_graph, expected_revision_hash=first.revision_hash)
	store.approve_revision(second.revision_hash, actor_id="reviewer")
	repository_root = tmp_path / "runtime"
	first_plane = RuntimePlane.from_approved_revision(repository_root, store, first.revision_hash, environment_id="env_prod")
	second_plane = RuntimePlane.from_approved_revision(repository_root, store, second.revision_hash, environment_id="env_prod")
	first_job = first_plane.scheduler.enqueue("source.edit", {"path": "first"}, max_attempts=1)
	second_job = second_plane.scheduler.enqueue("source.edit", {"path": "second"}, max_attempts=1)

	second_result = RuntimeWorker(second_plane, "worker_second").run_once()
	first_result = RuntimeWorker(first_plane, "worker_first").run_once()

	assert second_result is not None and second_result.id == second_job.id
	assert first_result is not None and first_result.id == first_job.id


def test_scheduler_does_not_reclaim_an_expired_lease_from_another_graph_revision(tmp_path: Path):
	project = tmp_path / "project"
	project.mkdir()
	store = OperationGraphStore(project, tenant_id="tenant_acme", project_id="project_support")
	first_graph = load_operation_graph(ROOT / "schemas/operation-graph.v0.yaml")
	first = store.create_revision(first_graph, expected_revision_hash=None)
	store.approve_revision(first.revision_hash, actor_id="reviewer")
	second_graph = copy.deepcopy(first_graph)
	second_graph["metadata"]["version"] = 1
	second = store.create_revision(second_graph, expected_revision_hash=first.revision_hash)
	store.approve_revision(second.revision_hash, actor_id="reviewer")
	first_plane = RuntimePlane.from_approved_revision(tmp_path / "runtime", store, first.revision_hash, environment_id="env_prod")
	second_plane = RuntimePlane.from_approved_revision(tmp_path / "runtime", store, second.revision_hash, environment_id="env_prod")
	from datetime import UTC, datetime, timedelta
	current = datetime(2026, 8, 23, tzinfo=UTC)
	clock = lambda: current.isoformat().replace("+00:00", "Z")
	first_plane.scheduler.clock = clock
	second_plane.scheduler.clock = clock
	first_job = first_plane.scheduler.enqueue("source.edit", {"path": "first"}, max_attempts=2)
	assert first_plane.scheduler.claim("first-worker", lease_seconds=30) is not None
	second_job = second_plane.scheduler.enqueue("source.edit", {"path": "second"}, max_attempts=2)
	claimed_second = second_plane.scheduler.claim("second-worker", lease_seconds=30)
	assert claimed_second is not None and claimed_second.id == second_job.id
	second_plane.scheduler.complete(second_job.id, worker_id="second-worker")

	current += timedelta(seconds=31)
	assert second_plane.scheduler.claim("second-recovery") is None
	foreign = first_plane.repository.get_job("tenant_acme", "env_prod", "project_support", first_job.id)
	assert foreign.status == "running" and foreign.attempts == 0
	assert first_plane.scheduler.claim("first-recovery") is None
	recovered = first_plane.repository.get_job("tenant_acme", "env_prod", "project_support", first_job.id)
	assert recovered.status == "queued" and recovered.attempts == 1


def test_cross_revision_worker_jobs_dead_letter_without_mutating_workflows_or_actions(tmp_path: Path):
	project = tmp_path / "project"
	project.mkdir()
	store = OperationGraphStore(project, tenant_id="tenant_acme", project_id="project_support")
	first_graph = load_operation_graph(ROOT / "schemas/operation-graph.v0.yaml")
	first_graph["connectors"][0]["operations"].append("write")
	first = store.create_revision(first_graph, expected_revision_hash=None)
	store.approve_revision(first.revision_hash, actor_id="reviewer")
	second_graph = copy.deepcopy(first_graph)
	second_graph["metadata"]["version"] = 1
	second = store.create_revision(second_graph, expected_revision_hash=first.revision_hash)
	store.approve_revision(second.revision_hash, actor_id="reviewer")

	repository_root = tmp_path / "runtime"
	first_plane = RuntimePlane.from_approved_revision(repository_root, store, first.revision_hash, environment_id="env_prod")
	second_plane = RuntimePlane.from_approved_revision(repository_root, store, second.revision_hash, environment_id="env_prod")
	workflow = first_plane.workflows.start("workflow_resolve_case")
	action = first_plane.actions.submit(
		"connector_support", "write", {"message": "approved graph A only"},
		requester_id="alice", idempotency_key="revision-a-action",
	)
	assert action.status == "pending_approval"
	with pytest.raises(ActionExecutionError):
		first_plane.actions.submit(
			"connector_support", "read", {}, requester_id="alice",
			idempotency_key="revision-a-retry", max_attempts=2,
		)
	retry_action = next(
		record for record in first_plane.repository.list_actions("tenant_acme", "env_prod", "project_support")
		if record.idempotency_key == "revision-a-retry"
	)
	assert retry_action.status == "retry_wait"
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		second_plane.workflows.get(workflow.id)
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		second_plane.workflows.transition(workflow.id, "triaged", expected_state="new", expected_revision=0)
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		second_plane.actions.execute_approved(action.id)
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		second_plane.actions.retry(retry_action.id)

	workflow_job = second_plane.scheduler.enqueue(
		"workflow.transition",
		{
			"instance_id": workflow.id,
			"target_state": "triaged",
			"expected_state": "new",
			"expected_revision": 0,
		},
		max_attempts=1,
	)
	action_job = second_plane.scheduler.enqueue("action.execute", {"action_id": action.id}, max_attempts=1)
	retry_job = second_plane.scheduler.enqueue("action.retry", {"action_id": retry_action.id}, max_attempts=1)
	worker = RuntimeWorker(second_plane, "worker_revision_b")
	assert worker.run_once().id == workflow_job.id  # type: ignore[union-attr]
	assert worker.run_once().id == action_job.id  # type: ignore[union-attr]
	assert worker.run_once().id == retry_job.id  # type: ignore[union-attr]

	assert {job.id for job in second_plane.scheduler.dead_letters()} == {workflow_job.id, action_job.id, retry_job.id}
	workflow_after = first_plane.repository.get_workflow("tenant_acme", "env_prod", "project_support", workflow.id)
	action_after = first_plane.repository.get_action("tenant_acme", "env_prod", "project_support", action.id)
	retry_after = first_plane.repository.get_action("tenant_acme", "env_prod", "project_support", retry_action.id)
	assert (workflow_after.state, workflow_after.revision, workflow_after.operation_graph_version) == ("new", 0, first.revision_hash)
	assert (action_after.status, action_after.revision, action_after.operation_graph_version) == ("pending_approval", 0, first.revision_hash)
	assert retry_after == retry_action


def test_scheduler_and_human_tasks_fail_closed_across_graph_revisions(tmp_path: Path):
	project = tmp_path / "project"
	project.mkdir()
	store = OperationGraphStore(project, tenant_id="tenant_acme", project_id="project_support")
	first_graph = load_operation_graph(ROOT / "schemas/operation-graph.v0.yaml")
	first = store.create_revision(first_graph, expected_revision_hash=None)
	store.approve_revision(first.revision_hash, actor_id="reviewer")
	second_graph = copy.deepcopy(first_graph)
	second_graph["metadata"]["version"] = 1
	second = store.create_revision(second_graph, expected_revision_hash=first.revision_hash)
	store.approve_revision(second.revision_hash, actor_id="reviewer")

	repository_root = tmp_path / "runtime"
	first_plane = RuntimePlane.from_approved_revision(repository_root, store, first.revision_hash, environment_id="env_prod")
	second_plane = RuntimePlane.from_approved_revision(repository_root, store, second.revision_hash, environment_id="env_prod")
	running_job = first_plane.scheduler.enqueue("source.edit", {"path": "first"}, max_attempts=1)
	assert first_plane.scheduler.claim("worker_a") is not None
	before_running = first_plane.repository.get_job("tenant_acme", "env_prod", "project_support", running_job.id)
	queued_job = first_plane.scheduler.enqueue("source.edit", {"path": "cancel"}, max_attempts=1)

	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		second_plane.scheduler.get(running_job.id)
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		second_plane.scheduler.complete(running_job.id, worker_id="worker_a")
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		second_plane.scheduler.fail(running_job.id, worker_id="worker_a", error="foreign")
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		second_plane.scheduler.cancel(queued_job.id)
	assert second_plane.scheduler.list() == []
	assert second_plane.scheduler.dead_letters() == []
	assert first_plane.repository.get_job("tenant_acme", "env_prod", "project_support", running_job.id) == before_running
	assert first_plane.repository.get_job("tenant_acme", "env_prod", "project_support", queued_job.id).status == "queued"

	first_plane.scheduler.fail(running_job.id, worker_id="worker_a", error="expected")
	assert second_plane.scheduler.dead_letters() == []

	def erase_job_revision(state: dict) -> None:
		state["jobs"][queued_job.id]["operation_graph_version"] = ""

	first_plane.repository.mutate_project("tenant_acme", "env_prod", "project_support", erase_job_revision)
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		first_plane.scheduler.get(queued_job.id)
	assert queued_job.id not in {job.id for job in first_plane.scheduler.list()}

	task = first_plane.human_tasks.create("Review revision A", assignee_id="alice")
	before_task = first_plane.repository.get_human_task("tenant_acme", "env_prod", "project_support", task.id)
	assert second_plane.human_tasks.queue() == []
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		second_plane.human_tasks.get(task.id)
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		second_plane.human_tasks.complete(task.id, expected_revision=0)
	assert first_plane.repository.get_human_task("tenant_acme", "env_prod", "project_support", task.id) == before_task

	def erase_task_revision(state: dict) -> None:
		state["human_tasks"][task.id]["operation_graph_version"] = ""

	first_plane.repository.mutate_project("tenant_acme", "env_prod", "project_support", erase_task_revision)
	assert first_plane.human_tasks.queue() == []
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		first_plane.human_tasks.get(task.id)
	with pytest.raises(RuntimeAuthorizationError, match="different Operation Graph revision"):
		first_plane.human_tasks.complete(task.id, expected_revision=0)
