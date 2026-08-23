from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
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
	InvalidTransitionError,
	JsonCollaborationRepository,
	PresenceRegistry,
	ScopeError,
	TaskState,
	ValidationError,
	make_domain_event,
	project_legacy_event,
)


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
	assert review.reviewer_role == "security_reviewer"
	assert review.actor_type == ActorType.HUMAN
	assert repository.list_reviews("tenant_a", "project_a", task.id) == [review]


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
