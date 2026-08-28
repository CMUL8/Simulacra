from __future__ import annotations

import json
import hashlib
import multiprocessing
import time
import threading
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from simulacra.collaboration import (
	ActivityInbox,
	ActorType,
	AuthorizationError,
	CollaborationService,
	CommentTargetType,
	ConflictError,
	DomainEvent,
	InvalidTransitionError,
	JsonCollaborationRepository,
	Member,
	PresenceRegistry,
	ScopeError,
	TaskState,
	ValidationError,
	make_domain_event,
	project_legacy_event,
)
from simulacra.collaboration.errors import NotFoundError
from simulacra.collaboration.models import Invitation


class _BarrierRepository(JsonCollaborationRepository):
	def __init__(self, root: str, ready, gate):
		super().__init__(root)
		self._ready = ready
		self._gate = gate

	def get_task(self, tenant_id: str, project_id: str, task_id: str):
		task = super().get_task(tenant_id, project_id, task_id)
		self._ready.put(task.revision)
		if not self._gate.wait(timeout=10):
			raise RuntimeError("claim race gate timed out")
		return task

	@staticmethod
	def _atomic_json(path: Path, value) -> None:
		if path.name == "tasks.json":
			time.sleep(0.2)
		JsonCollaborationRepository._atomic_json(path, value)


def _claim_in_process(root: str, task_id: str, actor_id: str, ready, gate, results) -> None:
	try:
		service = CollaborationService(_BarrierRepository(root, ready, gate))
		service.claim_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task_id,
			actor_id=actor_id, expected_revision=1,
		)
		results.put("success")
	except ConflictError:
		results.put("conflict")
	except Exception as exc:  # pragma: no cover - surfaced through the assertion in the parent
		results.put(f"error:{type(exc).__name__}:{exc}")


def _append_event_in_process(root: str, event_row: dict, gate, results) -> None:
	try:
		if not gate.wait(timeout=10):
			raise RuntimeError("event race gate timed out")
		JsonCollaborationRepository(root).append_event(DomainEvent.from_dict(event_row))
		results.put("success")
	except Exception as exc:  # pragma: no cover - surfaced through the assertion in the parent
		results.put(f"error:{type(exc).__name__}:{exc}")


def _review_in_process(root: str, task_id: str, gate, results) -> None:
	try:
		if not gate.wait(timeout=10):
			raise RuntimeError("review race gate timed out")
		review, task = CollaborationService(JsonCollaborationRepository(root)).review_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task_id,
			reviewer_id="bob", decision="approve", expected_revision=3,
		)
		results.put(("success", review.id, task.revision))
	except ConflictError:
		results.put(("conflict", None, None))
	except Exception as exc:  # pragma: no cover - surfaced through the assertion in the parent
		results.put((f"error:{type(exc).__name__}:{exc}", None, None))


@pytest.fixture()
def collaboration(tmp_path: Path) -> tuple[JsonCollaborationRepository, CollaborationService]:
	repository = JsonCollaborationRepository(tmp_path / "store")
	service = CollaborationService(repository)
	service.create_room(tenant_id="tenant_a", project_id="project_a", creator_id="alice")
	service.add_member(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", member_id="bob",
		role="reviewer", expected_revision=1,
	)
	return repository, service


def _task(service: CollaborationService):
	return service.create_task(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", title="Build approvals",
		objective="Gate consequential writes", acceptance_criteria=["Approval required", "Audit emitted"],
		source_message_id="msg_1", operation_graph_version="ogr_123", application_version="app_2",
	)


def test_one_room_scope_isolation_and_path_traversal(collaboration) -> None:
	repository, service = collaboration
	with pytest.raises(ConflictError):
		service.create_room(tenant_id="tenant_a", project_id="project_a", creator_id="alice")
	with pytest.raises(Exception):
		repository.get_room("../tenant_a", "project_a")
	with pytest.raises(Exception):
		repository.get_room("tenant_a", "../project_a")
	with pytest.raises(Exception):
		repository.get_room("tenant_b", "project_a")
	assert repository.get_room("tenant_a", "project_a").tenant_id == "tenant_a"


def test_atomic_single_owner_claim_and_stale_commands(collaboration) -> None:
	repository, service = collaboration
	task = _task(service)

	def claim(actor: str):
		try:
			return CollaborationService(JsonCollaborationRepository(repository.root)).claim_task(
				tenant_id="tenant_a", project_id="project_a", task_id=task.id,
				actor_id=actor, expected_revision=1,
			)
		except ConflictError:
			return None

	with ThreadPoolExecutor(max_workers=2) as pool:
		claimed = list(pool.map(claim, ["alice", "bob"]))
	winners = [item for item in claimed if item is not None]
	assert len(winners) == 1
	assert repository.get_task("tenant_a", "project_a", task.id).owner_id in {"alice", "bob"}
	with pytest.raises(ConflictError, match="stale"):
		service.transition_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			actor_id=winners[0].owner_id, to_state="working", expected_revision=1,
		)


def test_atomic_claim_is_safe_across_processes(collaboration) -> None:
	repository, service = collaboration
	task = _task(service)
	context = multiprocessing.get_context("spawn")
	ready = context.Queue()
	gate = context.Event()
	results = context.Queue()
	processes = [
		context.Process(
			target=_claim_in_process,
			args=(str(repository.root), task.id, actor_id, ready, gate, results),
		)
		for actor_id in ("alice", "bob")
	]
	for process in processes:
		process.start()
	assert [ready.get(timeout=10), ready.get(timeout=10)] == [1, 1]
	gate.set()
	for process in processes:
		process.join(timeout=10)
		assert process.exitcode == 0
	assert sorted([results.get(timeout=2), results.get(timeout=2)]) == ["conflict", "success"]
	stored = repository.get_task("tenant_a", "project_a", task.id)
	assert stored.revision == 2
	assert stored.owner_id in {"alice", "bob"}


def test_duplicate_event_is_idempotent_across_processes(collaboration) -> None:
	repository, _ = collaboration
	event = make_domain_event(
		event_id="evt_process_duplicate", tenant_id="tenant_a", project_id="project_a",
		actor_type="system", actor_id="system", action="task.process_test", result="succeeded",
		timestamp="2026-08-23T12:00:00+00:00",
	)
	context = multiprocessing.get_context("spawn")
	gate = context.Event()
	results = context.Queue()
	processes = [
		context.Process(
			target=_append_event_in_process,
			args=(str(repository.root), event.to_dict(), gate, results),
		)
		for _ in range(2)
	]
	for process in processes:
		process.start()
	gate.set()
	for process in processes:
		process.join(timeout=10)
		assert process.exitcode == 0
	assert [results.get(timeout=2), results.get(timeout=2)] == ["success", "success"]
	assert sum(
		stored.id == event.id for stored in repository.list_events("tenant_a", "project_a")
	) == 1
	lock_path = repository._lock_path("tenant_a", "project_a")
	assert lock_path.is_relative_to(repository.root)
	with pytest.raises(ValidationError):
		repository._lock_path("../tenant_b", "project_a")


def test_project_lock_rejects_symlink_escape(tmp_path: Path) -> None:
	root = tmp_path / "store"
	outside = tmp_path / "outside"
	root.mkdir()
	outside.mkdir()
	(root / ".collaboration-locks").symlink_to(outside, target_is_directory=True)
	repository = JsonCollaborationRepository(root)
	with pytest.raises(ScopeError, match="lock directory escapes"):
		repository._lock_path("tenant_a", "project_a")


def test_transition_rules_and_review_metadata(collaboration) -> None:
	repository, service = collaboration
	task = service.create_task(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", owner_id="alice",
		title="Review me", objective="Demonstrate lifecycle", acceptance_criteria=["done"],
	)
	assert task.state == TaskState.READY
	with pytest.raises(InvalidTransitionError):
		service.transition_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			actor_id="alice", to_state="done", expected_revision=1,
		)
	task = service.transition_task(
		tenant_id="tenant_a", project_id="project_a", task_id=task.id,
		actor_id="alice", to_state="working", expected_revision=1,
	)
	task = service.transition_task(
		tenant_id="tenant_a", project_id="project_a", task_id=task.id,
		actor_id="alice", to_state="in_review", expected_revision=2,
	)
	with pytest.raises(AuthorizationError, match="own work"):
		service.review_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			reviewer_id="alice", decision="approve", expected_revision=3,
		)
	review, task = service.review_task(
		tenant_id="tenant_a", project_id="project_a", task_id=task.id,
		reviewer_id="bob", reviewer_role="security_reviewer", actor_type="human",
		decision="approve", expected_revision=3,
	)
	assert task.state == TaskState.DONE
	assert review.reviewer_role == "reviewer"
	assert review.actor_type == ActorType.HUMAN
	assert repository.list_reviews("tenant_a", "project_a", task.id) == [review]


def test_in_review_requires_distinct_reviewer_approval_to_complete(collaboration) -> None:
	repository, service = collaboration
	task = service.create_task(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", owner_id="alice",
		title="Require review", objective="Keep completion behind review", acceptance_criteria=["approved"],
	)
	task = service.transition_task(
		tenant_id="tenant_a", project_id="project_a", task_id=task.id,
		actor_id="alice", to_state=TaskState.WORKING, expected_revision=task.revision,
	)
	task = service.transition_task(
		tenant_id="tenant_a", project_id="project_a", task_id=task.id,
		actor_id="alice", to_state=TaskState.IN_REVIEW, expected_revision=task.revision,
	)

	with pytest.raises(InvalidTransitionError, match="in_review -> done"):
		service.transition_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			actor_id="alice", to_state=TaskState.DONE, expected_revision=task.revision,
		)
	stored = repository.get_task("tenant_a", "project_a", task.id)
	assert stored.state == TaskState.IN_REVIEW
	assert stored.revision == task.revision

	review, completed = service.review_task(
		tenant_id="tenant_a", project_id="project_a", task_id=task.id,
		reviewer_id="bob", decision="approve", expected_revision=task.revision,
	)
	assert review.reviewer_id == "bob"
	assert completed.state == TaskState.DONE


def test_review_service_has_no_self_review_override(collaboration) -> None:
	repository, service = collaboration
	task = service.create_task(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", owner_id="alice",
		title="No review override", objective="Require independent approval", acceptance_criteria=["approved"],
	)
	for state in (TaskState.WORKING, TaskState.IN_REVIEW):
		task = service.transition_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			actor_id="alice", to_state=state, expected_revision=task.revision,
		)

	with pytest.raises(TypeError, match="allow_self_review"):
		service.review_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			reviewer_id="alice", decision="approve", expected_revision=task.revision,
			allow_self_review=True,
		)
	stored = repository.get_task("tenant_a", "project_a", task.id)
	assert stored.state == TaskState.IN_REVIEW
	assert stored.revision == task.revision

	_, completed = service.review_task(
		tenant_id="tenant_a", project_id="project_a", task_id=task.id,
		reviewer_id="bob", decision="approve", expected_revision=task.revision,
	)
	assert completed.state == TaskState.DONE


def test_review_service_rejects_non_human_actor_without_mutating_task(collaboration) -> None:
	repository, service = collaboration
	task = service.create_task(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", owner_id="alice",
		title="Human review only", objective="Reject non-human decisions", acceptance_criteria=["human approved"],
	)
	for state in (TaskState.WORKING, TaskState.IN_REVIEW):
		task = service.transition_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			actor_id="alice", to_state=state, expected_revision=task.revision,
		)

	with pytest.raises(AuthorizationError, match="human"):
		service.review_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			reviewer_id="bob", actor_type=ActorType.RUNTIME_AGENT,
			decision="approve", expected_revision=task.revision,
		)
	stored = repository.get_task("tenant_a", "project_a", task.id)
	assert stored.state == TaskState.IN_REVIEW
	assert stored.revision == task.revision

	_, completed = service.review_task(
		tenant_id="tenant_a", project_id="project_a", task_id=task.id,
		reviewer_id="bob", actor_type=ActorType.HUMAN,
		decision="approve", expected_revision=task.revision,
	)
	assert completed.state == TaskState.DONE


@pytest.mark.parametrize(
	("failure_stage", "completes"),
	[
		("before_intent", False),
		("after_intent", False),
		("after_review", True),
		("after_task", True),
		("after_cleanup", True),
	],
)
def test_review_completion_recovers_the_exact_durable_human_decision(
	collaboration, failure_stage: str, completes: bool,
) -> None:
	repository, service = collaboration
	task = service.create_task(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", owner_id="alice",
		title="Recover a review", objective="Keep the human decision durable", acceptance_criteria=["approved"],
	)
	for state in (TaskState.WORKING, TaskState.IN_REVIEW):
		task = service.transition_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			actor_id="alice", to_state=state, expected_revision=task.revision,
		)

	def fault(stage: str) -> None:
		if stage == failure_stage:
			raise RuntimeError(f"injected {stage}")

	repository.review_commit_fault = fault
	with pytest.raises(RuntimeError, match=failure_stage):
		service.review_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			reviewer_id="bob", decision="approve", expected_revision=task.revision,
		)

	# A fresh repository executes the same recovery path as later reads and writes.
	restarted = JsonCollaborationRepository(repository.root)
	recovered_task = restarted.get_task("tenant_a", "project_a", task.id)
	reviews = restarted.list_reviews("tenant_a", "project_a", task.id)
	if completes:
		assert recovered_task.state == TaskState.DONE
		assert recovered_task.revision == task.revision + 1
		assert len(reviews) == 1
		assert reviews[0].reviewer_id == "bob"
		assert reviews[0].task_revision == task.revision
	else:
		assert recovered_task.state == TaskState.IN_REVIEW
		assert recovered_task.revision == task.revision
		assert reviews == []


@pytest.mark.parametrize(
	"target_field",
	[
		"state", "owner_id", "collaborator_ids", "title", "objective", "acceptance_criteria",
		"result", "source_message_id", "activity_removed", "activity_injected", "revision", "updated_at",
	],
)
def test_review_recovery_rejects_a_corrupted_task_target_until_the_exact_journal_is_restored(
	collaboration, target_field: str,
) -> None:
	repository, service = collaboration
	task = service.create_task(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", owner_id="alice",
		title="Validate recovery result", objective="Only publish the reviewed result", acceptance_criteria=["approved"],
	)
	for state in (TaskState.WORKING, TaskState.IN_REVIEW):
		task = service.transition_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			actor_id="alice", to_state=state, expected_revision=task.revision,
		)

	def stop_after_review(stage: str) -> None:
		if stage == "after_review":
			raise RuntimeError("injected after_review")

	repository.review_commit_fault = stop_after_review
	with pytest.raises(RuntimeError, match="after_review"):
		service.review_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			reviewer_id="bob", decision="approve", expected_revision=task.revision,
		)
	journal_path = repository.root / "tenant_a" / "project_a" / "collaboration" / "review_transactions.json"
	exact_journal = json.loads(journal_path.read_text(encoding="utf-8"))
	corrupted = deepcopy(exact_journal)
	target = next(iter(corrupted.values()))["task"]
	if target_field == "state":
		target["state"] = "failed"
	elif target_field == "owner_id":
		target["owner_id"] = "mallory"
	elif target_field == "collaborator_ids":
		target["collaborator_ids"] = ["mallory"]
	elif target_field == "title":
		target["title"] = "Tampered title"
	elif target_field == "objective":
		target["objective"] = "Tampered objective"
	elif target_field == "acceptance_criteria":
		target["acceptance_criteria"] = ["Tampered criterion"]
	elif target_field == "result":
		target["result"] = {"published": "without review"}
	elif target_field == "source_message_id":
		target["source_message_id"] = "msg_tampered"
	elif target_field == "activity_removed":
		target["activity"] = target["activity"][:-1]
	elif target_field == "activity_injected":
		target["activity"].append({"action": "reviewed", "actor_id": "mallory"})
	elif target_field == "revision":
		target["revision"] += 1
	elif target_field == "updated_at":
		target["updated_at"] = "2000-01-01T00:00:00+00:00"
	repository._atomic_json(journal_path, corrupted)

	with pytest.raises((ConflictError, ScopeError)):
		JsonCollaborationRepository(repository.root).get_task("tenant_a", "project_a", task.id)
	tasks_path = repository.root / "tenant_a" / "project_a" / "collaboration" / "tasks.json"
	assert json.loads(tasks_path.read_text(encoding="utf-8"))[task.id] == task.to_dict()

	repository._atomic_json(journal_path, exact_journal)
	recovered = JsonCollaborationRepository(repository.root)
	assert recovered.get_task("tenant_a", "project_a", task.id).state == TaskState.DONE
	reviews = recovered.list_reviews("tenant_a", "project_a", task.id)
	assert len(reviews) == 1
	assert sum(item.get("action") == "reviewed" for item in recovered.get_task("tenant_a", "project_a", task.id).activity) == 1


def test_review_rollback_requires_done_work_and_recovers_the_durable_decision(collaboration) -> None:
	repository, service = collaboration
	task = service.create_task(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", owner_id="alice",
		title="Recover rollback", objective="Require a recorded rollback decision", acceptance_criteria=["reopened"],
	)
	for state in (TaskState.WORKING, TaskState.IN_REVIEW):
		task = service.transition_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			actor_id="alice", to_state=state, expected_revision=task.revision,
		)
	with pytest.raises(InvalidTransitionError, match="rollback requires a done task"):
		service.review_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			reviewer_id="bob", decision="rollback", expected_revision=task.revision,
		)
	_, task = service.review_task(
		tenant_id="tenant_a", project_id="project_a", task_id=task.id,
		reviewer_id="bob", decision="approve", expected_revision=task.revision,
	)

	def stop_after_review(stage: str) -> None:
		if stage == "after_review":
			raise RuntimeError("injected after_review")

	repository.review_commit_fault = stop_after_review
	with pytest.raises(RuntimeError, match="after_review"):
		service.review_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			reviewer_id="bob", decision="rollback", expected_revision=task.revision,
		)
	recovered = JsonCollaborationRepository(repository.root)
	rolled_back = recovered.get_task("tenant_a", "project_a", task.id)
	assert rolled_back.state == TaskState.WORKING
	assert rolled_back.revision == task.revision + 1
	assert len(recovered.list_reviews("tenant_a", "project_a", task.id)) == 2
	assert sum(item.get("action") == "reviewed" for item in rolled_back.activity) == 2


def test_review_commit_serializes_membership_change_after_the_human_decision(collaboration) -> None:
	repository, service = collaboration
	task = service.create_task(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", owner_id="alice",
		title="Review authority stays current", objective="Do not admit a removed reviewer", acceptance_criteria=["approved"],
	)
	for state in (TaskState.WORKING, TaskState.IN_REVIEW):
		task = service.transition_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			actor_id="alice", to_state=state, expected_revision=task.revision,
		)
	decision_checked = threading.Event()
	allow_decision = threading.Event()

	def hook(stage: str) -> None:
		if stage == "after_authority_recheck":
			decision_checked.set()
			assert allow_decision.wait(timeout=10)

	repository.review_commit_hook = hook
	review_result: list[object] = []

	def commit_review() -> None:
		review_result.append(service.review_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			reviewer_id="bob", decision="approve", expected_revision=task.revision,
		))

	review_thread = threading.Thread(target=commit_review)
	review_thread.start()
	assert decision_checked.wait(timeout=10)
	room = repository.get_room("tenant_a", "project_a")
	removal_finished = threading.Event()

	def remove_reviewer() -> None:
		repository.save_room(
			replace(room, members=[member for member in room.members if member.actor_id != "bob"],
				revision=room.revision + 1),
			room.revision,
		)
		removal_finished.set()

	removal_thread = threading.Thread(target=remove_reviewer)
	removal_thread.start()
	allow_decision.set()
	review_thread.join(timeout=10)
	removal_thread.join(timeout=10)
	assert not review_thread.is_alive() and not removal_thread.is_alive()
	assert removal_finished.is_set()
	assert review_result[0][1].state == TaskState.DONE
	assert len(repository.list_reviews("tenant_a", "project_a", task.id)) == 1
	assert all(member.actor_id != "bob" for member in repository.get_room("tenant_a", "project_a").members)


def test_concurrent_review_retries_commit_one_decision_across_processes(collaboration) -> None:
	repository, service = collaboration
	task = service.create_task(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", owner_id="alice",
		title="One approval", objective="Converge concurrent review retries", acceptance_criteria=["approved"],
	)
	for state in (TaskState.WORKING, TaskState.IN_REVIEW):
		task = service.transition_task(
			tenant_id="tenant_a", project_id="project_a", task_id=task.id,
			actor_id="alice", to_state=state, expected_revision=task.revision,
		)
	context = multiprocessing.get_context("spawn")
	gate = context.Event()
	results = context.Queue()
	processes = [
		context.Process(target=_review_in_process, args=(str(repository.root), task.id, gate, results))
		for _ in range(2)
	]
	for process in processes:
		process.start()
	gate.set()
	for process in processes:
		process.join(timeout=10)
		assert process.exitcode == 0
	assert sorted(result[0] for result in (results.get(timeout=2), results.get(timeout=2))) == ["conflict", "success"]
	assert repository.get_task("tenant_a", "project_a", task.id).state == TaskState.DONE
	assert len(repository.list_reviews("tenant_a", "project_a", task.id)) == 1


def test_graph_comment_coordinates_and_normalized_mentions(collaboration) -> None:
	_, service = collaboration
	comment = service.add_comment(
		tenant_id="tenant_a", project_id="project_a", author_id="alice", body="Please inspect",
		target_type=CommentTargetType.GRAPH_ELEMENT, graph_path="/workflows/ship/steps/0",
		graph_revision="ogr_deadbeef", mentions=["@Bob", "actor:bob", {"type": "actor", "id": "ALICE"}],
	)
	assert comment.graph_path == "/workflows/ship/steps/0"
	assert [(item.ref_type, item.ref_id) for item in comment.mentions] == [
		("actor", "alice"), ("actor", "bob")
	]
	with pytest.raises(ValidationError, match="exact path and revision"):
		service.add_comment(
			tenant_id="tenant_a", project_id="project_a", author_id="alice", body="bad",
			target_type="graph_element", graph_path="/workflows/ship",
		)


def test_lists_return_every_record_and_validate_every_scope(collaboration) -> None:
	repository, service = collaboration
	tasks = [
		service.create_task(
			tenant_id="tenant_a", project_id="project_a", actor_id="alice", title=f"Task {index}",
			objective=f"Objective {index}", acceptance_criteria=["Complete"],
		)
		for index in (1, 2)
	]
	comments = [
		service.add_comment(
			tenant_id="tenant_a", project_id="project_a", author_id="alice", body=f"Comment {index}",
			target_type="project",
		)
		for index in (1, 2)
	]
	assert [task.id for task in repository.list_tasks("tenant_a", "project_a")] == sorted(
		task.id for task in tasks
	)
	assert [comment.id for comment in repository.list_comments("tenant_a", "project_a")] == sorted(
		comment.id for comment in comments
	)

	# A corrupted later row must not be hidden by an early return after validating the first row.
	tasks_path = repository.root / "tenant_a" / "project_a" / "collaboration" / "tasks.json"
	rows = json.loads(tasks_path.read_text(encoding="utf-8"))
	second_id = sorted(rows)[1]
	rows[second_id]["tenant_id"] = "tenant_b"
	repository._atomic_json(tasks_path, rows)
	with pytest.raises(ScopeError, match="scope mismatch"):
		repository.list_tasks("tenant_a", "project_a")


def test_event_contract_idempotency_conflict_and_legacy_projection(collaboration) -> None:
	repository, _ = collaboration
	event = make_domain_event(
		event_id="evt_fixed", tenant_id="tenant_a", project_id="project_a", actor_type="system",
		actor_id="system", action="deployment.completed", result="succeeded",
		payload={"category": "deployments", "label": "Deployed", "detail": "v2"},
	)
	repository.append_event(event)
	before = event.to_dict()
	assert repository.append_event(event) == event
	assert event.to_dict() == before
	assert sum(item.id == "evt_fixed" for item in repository.list_events("tenant_a", "project_a")) == 1
	conflict = make_domain_event(
		event_id="evt_fixed", tenant_id="tenant_a", project_id="project_a", actor_type="system",
		actor_id="system", action="deployment.completed", result="failed",
	)
	with pytest.raises(ConflictError, match="different content"):
		repository.append_event(conflict)
	legacy = project_legacy_event(event)
	assert legacy["type"] == "deployment"
	assert legacy["label"] == "Deployed"
	assert legacy["detail"] == "v2"
	assert legacy["status"] == "success"
	assert set(event.to_dict()) == {
		"id", "actor_type", "actor_id", "tenant_id", "project_id", "task_id",
		"operation_graph_version", "application_version", "environment_id", "action", "result",
		"timestamp", "correlation_id", "trace_id", "payload",
	}


def test_inbox_unread_persists_and_away_summary_has_deep_links(collaboration) -> None:
	repository, service = collaboration
	service.add_comment(
		tenant_id="tenant_a", project_id="project_a", author_id="alice", body="Ping",
		target_type="project", mentions=["@bob"],
	)
	inbox = ActivityInbox(repository)
	unread = inbox.query(
		tenant_id="tenant_a", project_id="project_a", actor_id="bob",
		categories=["mentions"], unread_only=True,
	)
	assert len(unread) == 1
	assert unread[0].deep_link["target_type"] == "project"
	summary = inbox.while_you_were_away(tenant_id="tenant_a", project_id="project_a", actor_id="bob")
	assert summary.counts["mentions"] == 1
	inbox.mark_read(
		tenant_id="tenant_a", project_id="project_a", actor_id="bob", event_id=unread[0].event.id,
	)
	reloaded = ActivityInbox(JsonCollaborationRepository(repository.root))
	assert reloaded.query(
		tenant_id="tenant_a", project_id="project_a", actor_id="bob",
		categories=["mentions"], unread_only=True,
	) == []
	state_path = repository.root / "tenant_a" / "project_a" / "collaboration" / "inbox_state.json"
	assert json.loads(state_path.read_text())["bob"]["last_read_position"] > 0


def test_presence_is_ephemeral_scoped_and_expires() -> None:
	registry = PresenceRegistry(ttl_seconds=5)
	now = datetime(2026, 8, 23, tzinfo=UTC)
	registry.heartbeat(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", location="graph",
		now=now,
	)
	assert [item.actor_id for item in registry.list_active(
		tenant_id="tenant_a", project_id="project_a", now=now + timedelta(seconds=4)
	)] == ["alice"]
	assert registry.list_active(
		tenant_id="tenant_a", project_id="project_a", now=now + timedelta(seconds=6)
	) == []
	assert not hasattr(registry, "repository")


def test_presence_threshold_boundaries_are_server_derived() -> None:
	registry = PresenceRegistry(ttl_seconds=181)
	now = datetime(2026, 8, 23, tzinfo=UTC)
	registry.heartbeat(tenant_id="tenant_a", project_id="project_a", actor_id="alice", status="offline", location="private", now=now)
	assert registry.list_active(tenant_id="tenant_a", project_id="project_a", now=now + timedelta(seconds=45))[0].status == "online"
	assert registry.list_active(tenant_id="tenant_a", project_id="project_a", now=now + timedelta(seconds=45, microseconds=1))[0].status == "away"
	assert registry.list_active(tenant_id="tenant_a", project_id="project_a", now=now + timedelta(seconds=180))[0].status == "away"
	assert registry.list_active(tenant_id="tenant_a", project_id="project_a", now=now + timedelta(seconds=180, microseconds=1))[0].status == "offline"


def test_presence_restart_returns_offline_without_authorization_effect(collaboration) -> None:
	repository, service = collaboration
	registry = PresenceRegistry(ttl_seconds=181)
	registry.heartbeat(tenant_id="tenant_a", project_id="project_a", actor_id="alice")
	assert PresenceRegistry(ttl_seconds=181).list_active(tenant_id="tenant_a", project_id="project_a") == []
	# Presence is a display hint, not a permission check: the owner can still
	# make a durable Mission change after the process-local registry restarts.
	created = service.create_task(tenant_id="tenant_a", project_id="project_a", actor_id="alice", title="Review", objective="Review evidence", acceptance_criteria=["Evidence is checked"])
	assert created.id.startswith("task_")


def test_legacy_room_member_without_transaction_remains_visible(tmp_path) -> None:
	repository = JsonCollaborationRepository(tmp_path / "store")
	service = CollaborationService(repository)
	service.create_room(tenant_id="tenant_a", project_id="project_a", creator_id="alice")
	room = repository.visible_room("tenant_a", "project_a")
	assert [(member.actor_id, member.transaction_id, member.visibility_state) for member in room.members] == [("alice", None, "committed")]


def test_pending_invitation_revoke_is_idempotent(collaboration) -> None:
	repository, service = collaboration
	invitation = Invitation(
		id="invite_pending", tenant_id="tenant_a", project_id="project_a", invited_by="alice",
		invitee_email="new-human@example.test", requested_role="member",
		accept_token_digest=hashlib.sha256(b"one-time-token").hexdigest(), status="pending",
		expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
	)
	other = replace(invitation, id="invite_other", invitee_email="other-human@example.test")
	repository.create_invitation(invitation)
	repository.create_invitation(other)

	first = service.revoke_invitation(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice",
		invitation_id=invitation.id, client_request_id="revoke_once", expected_revision=1,
	)
	replayed = service.revoke_invitation(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice",
		invitation_id=invitation.id, client_request_id="revoke_once", expected_revision=1,
	)
	assert first == replayed
	assert first.status == "revoked" and first.revision == 2
	assert repository.get_invitation("tenant_a", "project_a", invitation.id).revision == 2

	with pytest.raises(ConflictError, match="idempotency_mismatch"):
		service.revoke_invitation(
			tenant_id="tenant_a", project_id="project_a", actor_id="alice",
			invitation_id=other.id, client_request_id="revoke_once", expected_revision=1,
		)
	with pytest.raises(NotFoundError, match="pending invitation"):
		service.revoke_invitation(
			tenant_id="tenant_a", project_id="project_a", actor_id="alice",
			invitation_id=invitation.id, client_request_id="different_request", expected_revision=2,
		)


def test_member_remove_owner_admin_only_and_keeps_last_owner(collaboration) -> None:
	repository, service = collaboration
	room = repository.get_room("tenant_a", "project_a")
	room = service.add_member(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", member_id="carol",
		role="admin", expected_revision=room.revision,
	)
	room = service.add_member(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", member_id="dave",
		role="member", expected_revision=room.revision,
	)

	with pytest.raises(AuthorizationError, match="owners and admins"):
		service.remove_member(
			tenant_id="tenant_a", project_id="project_a", actor_id="bob", member_id="dave",
			client_request_id="reviewer_remove", expected_room_revision=room.revision,
		)
	with pytest.raises(AuthorizationError, match="last owner"):
		service.remove_member(
			tenant_id="tenant_a", project_id="project_a", actor_id="carol", member_id="alice",
			client_request_id="last_owner", expected_room_revision=room.revision,
		)

	removed = service.remove_member(
		tenant_id="tenant_a", project_id="project_a", actor_id="carol", member_id="dave",
		client_request_id="remove_dave", expected_room_revision=room.revision,
	)
	replayed = service.remove_member(
		tenant_id="tenant_a", project_id="project_a", actor_id="carol", member_id="dave",
		client_request_id="remove_dave", expected_room_revision=room.revision,
	)
	assert removed.to_dict() == replayed.to_dict()
	assert all(member.actor_id != "dave" for member in removed.members)
	assert repository.get_room("tenant_a", "project_a").revision == room.revision + 1
	with pytest.raises(ConflictError, match="idempotency_mismatch"):
		service.remove_member(
			tenant_id="tenant_a", project_id="project_a", actor_id="carol", member_id="bob",
			client_request_id="remove_dave", expected_room_revision=room.revision,
		)


def test_removed_member_loses_mission_work_attention_conversation_file_access(collaboration) -> None:
	repository, service = collaboration
	room = repository.get_room("tenant_a", "project_a")
	room = service.add_member(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", member_id="charlie",
		role="member", expected_revision=room.revision,
	)
	service.create_conversation_message(
		tenant_id="tenant_a", project_id="project_a", authenticated_human_actor_id="charlie",
		client_request_id="before_removal", body="I can work in this Mission",
	)
	assert repository.member_project_ids("tenant_a", "charlie") == ["project_a"]

	service.remove_member(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", member_id="charlie",
		client_request_id="remove_charlie", expected_room_revision=room.revision,
	)
	# Mission, Work, Attention, and Files all project from this current-member
	# index; conversation mutations independently recheck the same room record.
	assert repository.member_project_ids("tenant_a", "charlie") == []
	with pytest.raises(AuthorizationError):
		service.create_task(
			tenant_id="tenant_a", project_id="project_a", actor_id="charlie",
			title="Should fail", objective="No Mission access remains", acceptance_criteria=["denied"],
		)
	with pytest.raises(AuthorizationError):
		service.create_conversation_message(
			tenant_id="tenant_a", project_id="project_a", authenticated_human_actor_id="charlie",
			client_request_id="after_removal", body="This must be denied",
		)


def test_hidden_pending_admin_never_authorizes_room_or_service_mutation(collaboration) -> None:
	repository, service = collaboration
	raw = repository.get_room("tenant_a", "project_a")
	repository.save_room(
		replace(
			raw,
			members=[*raw.members, Member(
				actor_id="pending_admin", role="admin", transaction_id="txn_pending_admin",
				visibility_state="pending_commit",
			)],
			revision=raw.revision + 1,
		),
		raw.revision,
	)

	with pytest.raises(AuthorizationError):
		service.create_task(
			tenant_id="tenant_a", project_id="project_a", actor_id="pending_admin",
			title="Hidden authority", objective="Must not mutate", acceptance_criteria=["denied"],
		)
	with pytest.raises(AuthorizationError):
		with service.mutation_authority_lock(
			tenant_id="tenant_a", project_id="project_a", actor_id="pending_admin",
		):
			pytest.fail("a hidden pending admin reached the consequential mutation boundary")
	with pytest.raises(AuthorizationError):
		service.add_member(
			tenant_id="tenant_a", project_id="project_a", actor_id="pending_admin",
			member_id="new_member", role="member",
			expected_revision=repository.get_room("tenant_a", "project_a").revision,
		)
	with pytest.raises(AuthorizationError):
		ActivityInbox(repository).mark_read(
			tenant_id="tenant_a", project_id="project_a", actor_id="pending_admin", position=0,
		)


def test_activity_inbox_uses_only_complete_visible_members(collaboration) -> None:
	repository, _ = collaboration
	raw = repository.get_room("tenant_a", "project_a")
	repository.save_room(
		replace(raw, members=[
			*raw.members,
			Member(
				actor_id="pending_human", role="member", transaction_id="txn_pending_human",
				visibility_state="committed",
			),
			Member(
				actor_id="accepted_human", role="member", transaction_id="txn_accepted_human",
				visibility_state="committed",
			),
		], revision=raw.revision + 1),
		raw.revision,
	)
	journal = (
		repository.root / ".invitation-acceptance" / "tenant_a" / "project_a"
		/ "txn_accepted_human.json"
	)
	journal.parent.mkdir(parents=True, exist_ok=True)
	journal.write_text(json.dumps({
		"state": "COMPLETE", "transaction_id": "txn_accepted_human",
		"tenant_id": "tenant_a", "project_id": "project_a",
	}), encoding="utf-8")
	event = make_domain_event(
		event_id="evt_inbox_visibility", tenant_id="tenant_a", project_id="project_a",
		actor_type="human", actor_id="alice", action="comment.mentioned", result="succeeded",
		payload={"mention_ids": ["pending_human", "accepted_human", "bob"]},
	)
	repository.append_event(event)
	inbox = ActivityInbox(repository)

	with pytest.raises(AuthorizationError, match="project room member"):
		inbox.query(tenant_id="tenant_a", project_id="project_a", actor_id="pending_human")
	with pytest.raises(AuthorizationError, match="project room member"):
		inbox.mark_read(
			tenant_id="tenant_a", project_id="project_a", actor_id="pending_human",
			event_id=event.id,
		)
	assert [item.event.id for item in inbox.query(
		tenant_id="tenant_a", project_id="project_a", actor_id="accepted_human",
		categories=["mentions"],
	)] == [event.id]
	assert [item.event.id for item in inbox.query(
		tenant_id="tenant_a", project_id="project_a", actor_id="bob", categories=["mentions"],
	)] == [event.id]
	assert inbox.mark_read(
		tenant_id="tenant_a", project_id="project_a", actor_id="accepted_human", event_id=event.id,
	)["last_read_position"] > 0


def test_activity_inbox_query_rechecks_membership_after_event_load(collaboration) -> None:
	repository, service = collaboration
	service.add_comment(
		tenant_id="tenant_a", project_id="project_a", author_id="alice", body="Please review",
		target_type="project", mentions=["bob"],
	)
	events_loaded = threading.Event()
	allow_publication = threading.Event()
	original_list_events = repository.list_events
	blocked = {"value": False}

	def list_events_then_wait(tenant_id: str, project_id: str):
		events = original_list_events(tenant_id, project_id)
		if not blocked["value"]:
			blocked["value"] = True
			events_loaded.set()
			assert allow_publication.wait(timeout=5)
		return events

	repository.list_events = list_events_then_wait  # type: ignore[method-assign]
	result: list[object] = []

	def query() -> None:
		try:
			result.append(ActivityInbox(repository).query(
				tenant_id="tenant_a", project_id="project_a", actor_id="bob",
			))
		except Exception as exc:
			result.append(exc)

	reader = threading.Thread(target=query)
	reader.start()
	assert events_loaded.wait(timeout=5)
	room = repository.get_room("tenant_a", "project_a")
	service.remove_member(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", member_id="bob",
		client_request_id="remove_during_inbox_query", expected_room_revision=room.revision,
	)
	allow_publication.set()
	reader.join(timeout=5)
	assert not reader.is_alive()
	assert len(result) == 1
	assert isinstance(result[0], AuthorizationError)


def test_member_removal_counts_and_targets_only_visible_committed_members(collaboration) -> None:
	repository, service = collaboration
	raw = repository.get_room("tenant_a", "project_a")
	raw = service.add_member(
		tenant_id="tenant_a", project_id="project_a", actor_id="alice", member_id="carol",
		role="admin", expected_revision=raw.revision,
	)
	repository.save_room(
		replace(
			raw,
			members=[*raw.members, Member(
				actor_id="pending_owner", role="owner", transaction_id="txn_pending_owner",
				visibility_state="pending_commit",
			)],
			revision=raw.revision + 1,
		),
		raw.revision,
	)
	current_revision = repository.get_room("tenant_a", "project_a").revision

	with pytest.raises(AuthorizationError, match="last owner"):
		service.remove_member(
			tenant_id="tenant_a", project_id="project_a", actor_id="carol", member_id="alice",
			client_request_id="keep_visible_owner", expected_room_revision=current_revision,
		)
	with pytest.raises(NotFoundError, match="Mission member"):
		service.remove_member(
			tenant_id="tenant_a", project_id="project_a", actor_id="carol", member_id="pending_owner",
			client_request_id="hidden_target", expected_room_revision=current_revision,
		)
	stored = repository.get_room("tenant_a", "project_a")
	assert stored.revision == current_revision
	assert any(member.actor_id == "pending_owner" for member in stored.members)
	assert [member.actor_id for member in repository.visible_room("tenant_a", "project_a").members] == ["alice", "bob", "carol"]
