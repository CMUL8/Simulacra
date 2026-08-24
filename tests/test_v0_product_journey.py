"""Neutral, durable V0 product journey across the CMUL8 planes."""

from __future__ import annotations

from pathlib import Path

import pytest

from deploy.bundle import REQUIRED_REFERENCES, build_bundle, verify_bundle
from deploy.release import create_rollback_manifest
from simulacra.collaboration import (
	ActivityInbox,
	CollaborationService,
	CommentTargetType,
	JsonCollaborationRepository,
	PresenceRegistry,
	ReviewDecision,
	TaskState,
)
from simulacra.demo.builder_harness import run_app_builder
from simulacra.demo.checkpoints import rollback, save_checkpoint
from simulacra.demo.operation_graph_builder import approved_graph_path, propose_operation_graph
from simulacra.demo.runs import create_project, project_dir
from simulacra.observability import JsonlTelemetryRepository, ObservabilityQueries, TelemetryQuery
from simulacra.operation_graph import OperationGraphStore, UnapprovedRevisionError
from simulacra.runtime import RuntimePlane, RuntimeWorker
from simulacra.harnesses import FakeHarness, TerminalStatus


ROOT = Path(__file__).resolve().parents[1]


def _bundle_references() -> dict[str, str]:
	filenames = {"operation_graph": "operation-graph.json", "runtime_agent": "runtime-agent.txt"}
	return {
		name: filenames.get(name, f"{name}.txt" if name in {"app", "api", "worker", "migrations", "tests"} else f"{name}.json")
		for name in REQUIRED_REFERENCES
	}


class _AppArtifactFakeHarness(FakeHarness):
	"""Deterministic CI builder that respects the application's write boundary."""

	async def _run_provider(self, request, session):  # type: ignore[no-untyped-def]
		del session
		target = request.write_paths[0] / "src" / "App.tsx"
		target.parent.mkdir(parents=True, exist_ok=True)
		target.write_text("export const journey = 'approved';\n", encoding="utf-8")
		return {
			"status": TerminalStatus.SUCCEEDED,
			"response": "deterministic application artifact",
			"changed_files": [target],
			"events": [{"type": "fake_build", "status": "completed"}],
			"steps": 1,
		}


def test_neutral_v0_product_journey_is_durable_and_gated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
	"""Exercise the selected Compose-ready V0 path without asserting deployment happened."""
	import simulacra.demo.runs as runs
	import simulacra.demo.tenants as tenants
	import simulacra.demo.mutation_authorization as auth_mod

	monkeypatch.setattr(runs, "RUNS_DIR", tmp_path / "runs")
	monkeypatch.setattr(runs, "ensure_runs_dir", lambda: runs.RUNS_DIR.mkdir(parents=True, exist_ok=True) or runs.RUNS_DIR)
	monkeypatch.setattr(tenants, "assert_under_project_quota", lambda _tenant_id: None)
	monkeypatch.setattr(auth_mod, "_collaboration_root", tmp_path / "collaboration")
	monkeypatch.setenv("CMUL8_AGENT_HARNESS", "fake")

	# Create project and ask the architect for a neutral graph proposal. The fake
	# harness intentionally exercises the durable safe-scaffold fallback in CI.
	state = create_project(
		"Coordinate editorial record review across a small internal team",
		goal="Make review decisions visible and auditable",
		tenant_id="tenant_journey",
	)
	root = project_dir(state.id)
	# Establish the durable owner before the architect can persist a proposal.
	collaboration_root = tmp_path / "collaboration"
	collaboration_repository = JsonCollaborationRepository(collaboration_root)
	collaboration = CollaborationService(collaboration_repository)
	room = collaboration.create_room(tenant_id=state.tenant_id, project_id=state.id, creator_id="alice")
	graph = propose_operation_graph(state, actor_id="alice")
	assert graph["metadata"]["project_id"] == state.id
	assert "vendor" not in str(graph).lower() and "onboarding" not in str(graph).lower()

	store = OperationGraphStore(root, tenant_id=state.tenant_id, project_id=state.id)
	revision = store.current_revision()
	assert revision is not None
	with pytest.raises(UnapprovedRevisionError):
		approved_graph_path(state)
	with pytest.raises(PermissionError, match="exactly approved"):
		run_app_builder(root / "app", "Build the approved workspace", project_id=state.id, row_count=0, kind="build_run")

	store.approve_revision(revision.revision_hash, actor_id="owner_alice")
	approved_path = approved_graph_path(state)
	assert approved_path is not None and approved_path.is_file()
	import simulacra.demo.builder_harness as builder_harness
	monkeypatch.setattr(builder_harness, "create_harness", lambda _config, **_adapters: _AppArtifactFakeHarness())
	built = run_app_builder(
		root / "app", "Build the approved workspace", project_id=state.id,
		row_count=0, kind="build_run", operation_graph_path=approved_path,
	)
	assert built["ok"] and built["source"] == "fake" and built["files_changed"]

	# Two members see the same durable Project Room, task state, comment and
	# inbox position. Presence is intentionally an expiring projection, not a
	# fabricated deployment signal.
	room = collaboration.add_member(
		tenant_id=state.tenant_id, project_id=state.id, actor_id="alice", member_id="bob",
		role="reviewer", expected_revision=room.revision,
	)
	presence = PresenceRegistry(ttl_seconds=60)
	presence.heartbeat(tenant_id=state.tenant_id, project_id=state.id, actor_id="alice")
	presence.heartbeat(tenant_id=state.tenant_id, project_id=state.id, actor_id="bob")
	assert {item.actor_id for item in presence.list_active(tenant_id=state.tenant_id, project_id=state.id)} == {"alice", "bob"}

	task = collaboration.create_task(
		tenant_id=state.tenant_id, project_id=state.id, actor_id="alice", title="Review the proposal",
		objective="Move the approved record workflow through review", acceptance_criteria=["Decision recorded"],
		operation_graph_version=revision.revision_hash,
	)
	claimed = collaboration.claim_task(
		tenant_id=state.tenant_id, project_id=state.id, task_id=task.id, actor_id="bob", expected_revision=task.revision,
	)
	working = collaboration.transition_task(
		tenant_id=state.tenant_id, project_id=state.id, task_id=task.id, actor_id="bob",
		to_state=TaskState.WORKING, expected_revision=claimed.revision,
	)
	in_review = collaboration.transition_task(
		tenant_id=state.tenant_id, project_id=state.id, task_id=task.id, actor_id="bob",
		to_state=TaskState.IN_REVIEW, expected_revision=working.revision,
	)
	_review, completed_task = collaboration.review_task(
		tenant_id=state.tenant_id, project_id=state.id, task_id=task.id, reviewer_id="alice",
		decision=ReviewDecision.APPROVE, expected_revision=in_review.revision, body="Reviewed against the exact approved graph.",
	)
	assert completed_task.state is TaskState.DONE
	comment = collaboration.add_comment(
		tenant_id=state.tenant_id, project_id=state.id, author_id="alice", body="@bob approval recorded",
		target_type=CommentTargetType.PROJECT, mentions=["@bob"],
	)
	assert comment.mentions[0].ref_id == "bob"
	inbox = ActivityInbox(collaboration_repository)
	unread = inbox.query(tenant_id=state.tenant_id, project_id=state.id, actor_id="bob", unread_only=True)
	assert unread
	assert inbox.mark_read(tenant_id=state.tenant_id, project_id=state.id, actor_id="bob", event_id=unread[0].event.id)["last_read_position"] >= 1

	# The worker claims the durable, graph-bound job and emits into the same JSONL
	# telemetry shape queried by the observability console.
	telemetry = JsonlTelemetryRepository(tmp_path / "telemetry")
	plane = RuntimePlane.from_approved_revision(
		tmp_path / "runtime", store, revision.revision_hash, environment_id="env_compose",
		observability_repository=telemetry,
	)
	workflow_id = graph["workflows"][0]["id"]
	workflow = plane.workflows.start(workflow_id)
	job = plane.scheduler.enqueue(
		"workflow.transition",
		{
			"instance_id": workflow.id,
			"target_state": graph["workflows"][0]["states"][1],
			"expected_state": workflow.state,
			"expected_revision": workflow.revision,
			"idempotency_key": "journey-workflow-transition",
		},
		idempotency_key="journey-runtime-job",
	)
	completed_job = RuntimeWorker(plane, "worker_journey", queue_reachable=lambda: True).run_once()
	assert completed_job is not None and completed_job.id == job.id and completed_job.status == "succeeded"
	observability = ObservabilityQueries(telemetry)
	trace = observability.events(TelemetryQuery(tenant_id=state.tenant_id, trace_id=f"trace_{job.id}"))
	assert len(trace) == 1 and trace[0].application_id == state.id and trace[0].status.value == "succeeded"

	# Version rollback is performed against real checkpoint files. The deployment
	# artifact evidence is a verified bundle and Compose is inspected only as the
	# selected target contract; this test never claims a deployment occurred.
	app_file = root / "app" / "src" / "App.tsx"
	app_file.parent.mkdir(parents=True, exist_ok=True)
	app_file.write_text("export const version = 'one';\n", encoding="utf-8")
	first_version = save_checkpoint(state, "First build")
	app_file.write_text("export const version = 'two';\n", encoding="utf-8")
	save_checkpoint(state, "Second build")
	rollback(state.id, first_version["id"])
	assert app_file.read_text(encoding="utf-8") == "export const version = 'one';\n"

	deployment_fixture = ROOT / "tests" / "fixtures" / "deployment"
	first_bundle = build_bundle(deployment_fixture, _bundle_references(), tmp_path / "bundle-one", release="journey-v0.1")
	second_bundle = build_bundle(deployment_fixture, _bundle_references(), tmp_path / "bundle-two", release="journey-v0.2")
	first_verified, second_verified = verify_bundle(first_bundle), verify_bundle(second_bundle)
	rollback_record = create_rollback_manifest(second_verified.bundle_hash, first_verified.bundle_hash, migration_compatible=True)
	assert rollback_record["to_bundle"] == first_verified.bundle_hash and rollback_record["requires_operator_approval"] is True
	compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
	assert 'command: ["api"]' in compose and 'command: ["worker"]' in compose
