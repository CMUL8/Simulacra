from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from simulacra.operation_graph import OperationGraphStore, UnapprovedRevisionError, load_operation_graph
from simulacra.runtime import (
	AuditEvent,
	ApprovalRequiredError,
	InvalidTransitionError,
	RuntimeAuthorizationError,
	RuntimeConflictError,
	RuntimePlane,
	RuntimeScopeError,
	TelemetryEvent,
)

ROOT = Path(__file__).resolve().parents[1]


def graph() -> dict:
	value = load_operation_graph(ROOT / "schemas" / "operation-graph.v0.yaml")
	value["connectors"][0]["operations"].append("write")
	value["agents"][0]["tools"] = ["case.lookup"]
	value["permissions"][0]["actions"].append("case.lookup")
	return value


def approved_plane(tmp_path: Path, *, executors=None, tools=None) -> RuntimePlane:
	project = tmp_path / "project"; project.mkdir()
	store = OperationGraphStore(project, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_revision(graph(), expected_revision_hash=None)
	store.approve_revision(revision.revision_hash, actor_id="reviewer")
	return RuntimePlane.from_approved_revision(tmp_path / "runtime", store, revision.revision_hash, environment_id="env_prod", connector_executors=executors, agent_tools=tools)


def test_approved_gate_and_control_plane_absence(tmp_path: Path):
	project = tmp_path / "project"; project.mkdir()
	store = OperationGraphStore(project, tenant_id="tenant_acme", project_id="project_support")
	revision = store.create_revision(graph(), expected_revision_hash=None)
	with pytest.raises(UnapprovedRevisionError):
		RuntimePlane.from_approved_revision(tmp_path / "runtime", store, revision.revision_hash, environment_id="env_prod")
	store.approve_revision(revision.revision_hash, actor_id="reviewer")
	plane = RuntimePlane.from_approved_revision(tmp_path / "runtime", store, revision.revision_hash, environment_id="env_prod")
	del store
	assert plane.entities.create("entity_case", {"status": "new"}).data["status"] == "new"
	assert plane.health.liveness()["status"] == "live"
	assert plane.health.readiness()["status"] == "ready"


def test_durable_entity_workflow_restart_and_conflicts(tmp_path: Path):
	plane = approved_plane(tmp_path)
	entity = plane.entities.create("entity_case", {"status": "new"})
	updated = plane.entities.update(entity.id, {"status": "triaged"}, expected_revision=0)
	with pytest.raises(RuntimeConflictError): plane.entities.update(entity.id, {}, expected_revision=0)
	flow = plane.workflows.start("workflow_resolve_case", entity_record_id=entity.id)
	flow = plane.workflows.transition(flow.id, "triaged", expected_state="new", expected_revision=0, idempotency_key="transition-1")
	assert plane.workflows.transition(flow.id, "triaged", expected_state="new", expected_revision=0, idempotency_key="transition-1") == flow
	with pytest.raises(InvalidTransitionError): plane.workflows.transition(flow.id, "new", expected_state="triaged", expected_revision=1)
	restarted = RuntimePlane(plane.repository.__class__(plane.repository.root), plane.policy, "env_prod")
	assert restarted.entities.get(updated.id).revision == 1
	assert restarted.workflows.get(flow.id).state == "triaged"


def test_consequential_actions_are_idempotent_and_cannot_bypass_approval(tmp_path: Path):
	calls: list[dict] = []
	def execute(connector, operation, payload): calls.append(payload); return {"sent": True}
	plane = approved_plane(tmp_path, executors={"connector_support": execute})
	action = plane.actions.submit("connector_support", "write", {"message": "hi"}, requester_id="alice", idempotency_key="reply-1")
	duplicate = plane.actions.submit("connector_support", "write", {"message": "hi"}, requester_id="alice", idempotency_key="reply-1")
	assert duplicate.id == action.id and calls == [] and action.status == "pending_approval"
	with pytest.raises(ApprovalRequiredError): plane.actions.execute_approved(action.id)
	with pytest.raises(RuntimeAuthorizationError): plane.approvals.decide(action.approval_id, actor_id="alice", decision="approved")
	plane.approvals.decide(action.approval_id, actor_id="bob", decision="approved")
	done = plane.actions.execute_approved(action.id)
	assert done.status == "succeeded" and len(calls) == 1
	assert plane.actions.execute_approved(action.id).id == done.id and len(calls) == 1


def test_action_retry_backoff_and_dead_letter(tmp_path: Path):
	def offline(connector, operation, payload): raise OSError("offline")
	plane = approved_plane(tmp_path, executors={"connector_support": offline})
	current = datetime(2026, 8, 23, tzinfo=UTC)
	plane.actions.clock = lambda: current.isoformat().replace("+00:00", "Z")
	with pytest.raises(Exception): plane.actions.submit("connector_support", "read", {}, requester_id="alice", idempotency_key="read-1", max_attempts=2)
	action = plane.repository.list_actions("tenant_acme", "env_prod", "project_support")[0]
	assert action.status == "retry_wait"
	with pytest.raises(RuntimeConflictError): plane.actions.retry(action.id)
	current += timedelta(seconds=1)
	with pytest.raises(Exception): plane.actions.retry(action.id)
	assert plane.actions.dead_letters()[0].status == "dead_letter"


def test_runtime_agent_graph_tool_source_and_secret_boundaries(tmp_path: Path):
	plane = approved_plane(tmp_path, tools={"case.lookup": lambda payload: payload["id"]})
	assert plane.agents.invoke("agent_triage", "case.lookup", {"id": "case_1"}) == "case_1"
	with pytest.raises(RuntimeAuthorizationError): plane.agents.invoke("agent_triage", "source.write", {})
	with pytest.raises(RuntimeAuthorizationError): plane.agents.invoke("agent_triage", "case.lookup", {"token": "raw"})
	with pytest.raises(RuntimeAuthorizationError): plane.agents.invoke("agent_triage", "unknown.tool", {})


def test_scheduler_retry_backoff_dead_letter_and_restart(tmp_path: Path):
	plane = approved_plane(tmp_path)
	current = datetime(2026, 8, 23, tzinfo=UTC)
	plane.scheduler.clock = lambda: current.isoformat().replace("+00:00", "Z")
	job = plane.scheduler.enqueue("deliver", {}, max_attempts=2, idempotency_key="event-1")
	assert plane.scheduler.enqueue("deliver", {}, max_attempts=2, idempotency_key="event-1").id == job.id
	claimed = plane.scheduler.claim("worker"); assert claimed is not None
	retried = plane.scheduler.fail(claimed.id, worker_id="worker", error="offline")
	assert retried.status == "queued" and datetime.fromisoformat(retried.run_at.replace("Z", "+00:00")) > current
	assert plane.scheduler.claim("worker") is None
	current += timedelta(seconds=1)
	claimed = plane.scheduler.claim("worker"); assert claimed is not None
	dead = plane.scheduler.fail(claimed.id, worker_id="worker", error="offline")
	assert dead.status == "dead_letter" and plane.scheduler.dead_letters() == [dead]


def test_human_tasks_approvals_audit_telemetry_and_duplicate_event(tmp_path: Path):
	plane = approved_plane(tmp_path)
	task = plane.human_tasks.create("Review reply", assignee_id="bob")
	assert plane.human_tasks.queue(assignee_id="bob") == [task]
	assert plane.human_tasks.complete(task.id, expected_revision=0).status == "completed"
	approval = plane.approvals.request("custom.action", "alice")
	assert plane.approvals.decide(approval.id, actor_id="bob", decision="approved").status == "approved"
	event = plane.audit.record("case.created", "alice", "ok", event_id="evt_fixed")
	assert plane.audit.record("case.created", "alice", "ok", event_id="evt_fixed") == event
	assert len(plane.audit.list()) == 1
	plane.telemetry.emit("runtime.action", attributes={"result": "ok"})
	assert len(plane.telemetry.list()) == 1 and plane.health.health()["status"] == "healthy"


def test_tenant_environment_isolation_and_path_escape(tmp_path: Path):
	plane = approved_plane(tmp_path)
	entity = plane.entities.create("entity_case", {"status": "new"})
	other = RuntimePlane(plane.repository, plane.policy, "env_stage")
	assert other.entities.query() == []
	with pytest.raises(Exception): other.entities.get(entity.id)
	with pytest.raises(RuntimeScopeError):
		plane.repository.read_project("../tenant", "env_prod", "project_support")
	outside = tmp_path / "outside"; outside.mkdir()
	root = tmp_path / "malicious"; root.mkdir(); (root / "tenant_acme").symlink_to(outside, target_is_directory=True)
	from simulacra.runtime import JsonRuntimeRepository
	with pytest.raises(RuntimeScopeError): JsonRuntimeRepository(root).read_project("tenant_acme", "env_prod", "project_support")


def test_repository_lists_all_records_in_order_and_scope_checks_every_row(tmp_path: Path):
	plane = approved_plane(tmp_path)
	repository = plane.repository
	first = plane.entities.create("entity_case", {"status": "new"}, record_id="entity_a")
	second = plane.entities.create("entity_case", {"status": "triaged"}, record_id="entity_z")
	repository.append_audit(AuditEvent("evt_z", "tenant_acme", "env_prod", "project_support", "case.updated", "alice", "ok"))
	repository.append_audit(AuditEvent("evt_a", "tenant_acme", "env_prod", "project_support", "case.created", "alice", "ok"))
	repository.append_telemetry(TelemetryEvent("metric_z", "tenant_acme", "env_prod", "project_support", "runtime.z"))
	repository.append_telemetry(TelemetryEvent("metric_a", "tenant_acme", "env_prod", "project_support", "runtime.a"))

	assert repository.query_entities("tenant_acme", "env_prod", "project_support") == [first, second]
	assert [event.id for event in repository.list_audit("tenant_acme", "env_prod", "project_support")] == ["evt_a", "evt_z"]
	assert [event.id for event in repository.list_telemetry("tenant_acme", "env_prod", "project_support")] == ["metric_a", "metric_z"]

	def corrupt_second_entity(state: dict) -> None:
		state["entities"]["entity_z"]["tenant_id"] = "tenant_other"

	repository.mutate_project("tenant_acme", "env_prod", "project_support", corrupt_second_entity)
	with pytest.raises(RuntimeScopeError, match="scope mismatch"):
		repository.query_entities("tenant_acme", "env_prod", "project_support")
