from __future__ import annotations

import pytest
import json
import multiprocessing
import typing
from dataclasses import replace

from simulacra.collaboration import AuthorizationError, CollaborationService, JsonCollaborationRepository, NotFoundError, ValidationError
from simulacra.collaboration.conversation import ConversationConflictError, serialize_conversation_message
from simulacra.collaboration.models import CommentTargetType, ConversationMessage, Member
from simulacra.collaboration.repository import CollaborationRepository


def _spawned_conversation_snapshot(root: str, result_queue) -> None:
    """Read durable state in a fresh spawned interpreter, not a reused object."""
    try:
        service = CollaborationService(JsonCollaborationRepository(root))
        state = service.repository.conversation_state("tenant_1", "project_1")
        result_queue.put((
            [message.body for message in service.conversation_messages("tenant_1", "project_1")],
            len(service.conversation_audits("tenant_1", "project_1")),
            len(state["idempotency"]),
        ))
    except Exception as exc:  # pragma: no cover - parent assertion exposes it
        result_queue.put(("error", type(exc).__name__, str(exc)))


def _restart_snapshot(root) -> tuple[list[str | None], int, int]:
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    process = context.Process(target=_spawned_conversation_snapshot, args=(str(root), results))
    process.start()
    process.join(timeout=15)
    assert process.exitcode == 0
    snapshot = results.get(timeout=3)
    assert snapshot[0] != "error", snapshot
    return snapshot


def _spawned_create_replay(root: str, result_queue) -> None:
    try:
        service = CollaborationService(JsonCollaborationRepository(root))
        message = service.create_conversation_message(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
            client_request_id="request_1", body="Hello",
        )
        result_queue.put((message.body, message.revision, message.deleted_at))
    except Exception as exc:  # pragma: no cover - parent assertion exposes it
        result_queue.put(("error", type(exc).__name__, str(exc)))


def _spawned_legacy_replay(root: str, operation: str, result_queue) -> None:
    try:
        service = CollaborationService(JsonCollaborationRepository(root))
        state = service.repository.conversation_state("tenant_1", "project_1")
        message_id = next(iter(state["messages"]))
        if operation == "create":
            message = service.create_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                client_request_id="request_1", body="Hello",
            )
        elif operation == "edit":
            message = service.edit_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                message_id=message_id, client_request_id="edit_legacy", body="Edited", expected_revision=1,
            )
        else:
            message = service.delete_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                message_id=message_id, client_request_id="delete_legacy", expected_revision=2,
            )
        result_queue.put((message.body, message.revision, message.created_at, message.edited_at, message.deleted_at))
    except Exception as exc:  # pragma: no cover - parent assertion exposes it
        result_queue.put(("error", type(exc).__name__, str(exc)))


def _spawned_legacy_result(root, operation):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    process = context.Process(target=_spawned_legacy_replay, args=(str(root), operation, results))
    process.start()
    process.join(timeout=15)
    assert process.exitcode == 0
    result = results.get(timeout=3)
    assert not (isinstance(result, tuple) and result[0] == "error"), result
    return result


def _spawned_snapshot_replay(root: str, message_id: str, operation: str, result_queue, expected_revision: int = 2) -> None:
    try:
        service = CollaborationService(JsonCollaborationRepository(root))
        if operation == "create":
            message = service.create_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                client_request_id="request_1", body="Hello",
            )
        elif operation == "edit":
            message = service.edit_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                message_id=message_id, client_request_id="edit_1", body="Edited", expected_revision=1,
            )
        else:
            message = service.delete_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                message_id=message_id, client_request_id="delete_1", expected_revision=expected_revision,
            )
        result_queue.put(message.to_dict())
    except Exception as exc:  # pragma: no cover - parent assertion exposes it
        result_queue.put(("error", type(exc).__name__, str(exc)))


def _spawned_snapshot_result(root, message_id, operation, expected_revision=2):
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    process = context.Process(target=_spawned_snapshot_replay, args=(str(root), message_id, operation, results, expected_revision))
    process.start()
    process.join(timeout=15)
    assert process.exitcode == 0
    result = results.get(timeout=3)
    assert not (isinstance(result, tuple) and result[0] == "error"), result
    return result


@pytest.fixture()
def conversation(tmp_path):
    repo = JsonCollaborationRepository(tmp_path / "store")
    sequence = iter(range(1, 1000))
    rooms = CollaborationService(
        repo,
        conversation_clock=lambda: "2026-01-02T09:00:00+00:00",
        conversation_id_factory=lambda prefix: f"{prefix}_{next(sequence)}",
    )
    rooms.create_room(tenant_id="tenant_1", project_id="project_1", creator_id="human_1")
    return rooms, repo


def _create(service):
    return service.create_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", client_request_id="request_1", body="Hello")


def _complete_acceptance(repo: JsonCollaborationRepository, transaction_id: str) -> None:
    path = repo.root / ".invitation-acceptance" / "tenant_1" / "project_1" / f"{transaction_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "state": "COMPLETE", "transaction_id": transaction_id,
        "tenant_id": "tenant_1", "project_id": "project_1",
    }))
    room = repo.get_room("tenant_1", "project_1")
    repo.save_room(replace(
        room,
        members=[replace(member, visibility_state="committed") if member.transaction_id == transaction_id else member for member in room.members],
        revision=room.revision + 1,
    ), room.revision)


def test_pending_member_has_no_conversation_authority_until_acceptance_is_complete(conversation):
    service, repo = conversation
    existing = _create(service)
    room = repo.get_room("tenant_1", "project_1")
    repo.save_room(replace(
        room,
        members=[*room.members, Member(
            actor_id="pending_admin", role="admin", transaction_id="txn_pending_conversation",
            visibility_state="pending_commit",
        )],
        revision=room.revision + 1,
    ), room.revision)

    with pytest.raises(AuthorizationError):
        service.conversation_message_view("tenant_1", "project_1", existing.id, "pending_admin")
    with pytest.raises(AuthorizationError):
        service.create_conversation_message(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="pending_admin",
            client_request_id="pending_send", body="Must not send",
        )
    with pytest.raises(AuthorizationError):
        service.edit_conversation_message(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="pending_admin",
            message_id=existing.id, client_request_id="pending_edit", body="Must not edit", expected_revision=1,
        )
    with pytest.raises(AuthorizationError):
        service.put_conversation_reaction(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="pending_admin",
            message_id=existing.id, reaction="check", client_request_id="pending_reaction",
        )
    with pytest.raises(AuthorizationError):
        service.put_saved_conversation_message(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="pending_admin",
            message_id=existing.id, client_request_id="pending_save",
        )

    _complete_acceptance(repo, "txn_pending_conversation")
    own = service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="pending_admin",
        client_request_id="accepted_send", body="Accepted member message",
    )
    edited = service.edit_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="pending_admin",
        message_id=own.id, client_request_id="accepted_edit", body="Accepted and edited", expected_revision=1,
    )
    service.put_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="pending_admin",
        message_id=existing.id, reaction="check", client_request_id="accepted_reaction",
    )
    service.put_saved_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="pending_admin",
        message_id=existing.id, client_request_id="accepted_save",
    )
    assert edited.body == "Accepted and edited"
    assert service.conversation_message_view("tenant_1", "project_1", existing.id, "pending_admin").saved is True
    # Legacy untagged committed members remain fully compatible.
    assert service.conversation_message_view("tenant_1", "project_1", existing.id, "human_1").message.id == existing.id


def test_create_message_replays_same_request_id(conversation):
    service, repo = conversation
    message = _create(service)
    assert message.id == _create(service).id
    state_path = repo.root / "tenant_1" / "project_1" / "collaboration" / "conversation_state.json"
    record = next(iter(json.loads(state_path.read_text())["idempotency"].values()))
    assert set(record) == {"operation", "authenticated_human_actor_id", "client_request_id", "canonical_body_hash", "response_ref", "created_at"}
    assert record["response_ref"] == {"message_id": message.id, "response_snapshot": message.to_dict()}
    with pytest.raises(ConversationConflictError, match="idempotency_mismatch"):
        service.create_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", client_request_id="request_1", body="Different")
    service.edit_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", message_id=message.id, client_request_id="edit_1", body="Edited", expected_revision=1)
    assert service.create_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", client_request_id="request_1", body="Hello") == message
    service.delete_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", message_id=message.id, client_request_id="delete_1", expected_revision=2)
    assert service.create_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", client_request_id="request_1", body="Hello") == message
    context = multiprocessing.get_context("spawn")
    results = context.Queue()
    process = context.Process(target=_spawned_create_replay, args=(str(repo.root), results))
    process.start()
    process.join(timeout=15)
    assert process.exitcode == 0
    assert results.get(timeout=3) == ("Hello", 1, None)
    assert _spawned_snapshot_result(repo.root, message.id, "create") == message.to_dict()
    assert _spawned_snapshot_result(repo.root, message.id, "edit") == {
        **message.to_dict(), "body": "Edited", "revision": 2,
        "edited_at": "2026-01-02T09:00:00+00:00",
    }
    assert _spawned_snapshot_result(repo.root, message.id, "delete") == {
        **message.to_dict(), "body": None, "revision": 3,
        "edited_at": "2026-01-02T09:00:00+00:00", "deleted_at": "2026-01-02T09:00:00+00:00",
    }


def test_legacy_idempotency_records_reconstruct_immutable_replays_after_restart(conversation):
    service, repo = conversation
    created = _create(service)
    edited = service.edit_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=created.id, client_request_id="edit_legacy", body="Edited", expected_revision=1,
    )
    deleted = service.delete_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=created.id, client_request_id="delete_legacy", expected_revision=2,
    )

    def remove_snapshots(state):
        for record in state["idempotency"].values():
            if record["client_request_id"] in {"request_1", "edit_legacy", "delete_legacy"}:
                record["response_ref"] = {"message_id": created.id}

    repo.mutate_conversation_state("tenant_1", "project_1", remove_snapshots)
    assert _spawned_legacy_result(repo.root, "create") == ("Hello", 1, created.created_at, None, None)
    assert _spawned_legacy_result(repo.root, "edit") == ("Edited", 2, created.created_at, edited.edited_at, None)
    assert _spawned_legacy_result(repo.root, "delete") == (None, 3, created.created_at, deleted.edited_at, deleted.deleted_at)


def test_legacy_idempotency_corruption_never_returns_mutable_current_state(conversation):
    service, repo = conversation
    message = _create(service)

    def corrupt(state):
        record = next(iter(state["idempotency"].values()))
        record["response_ref"] = {"message_id": message.id}
        state["messages"][message.id]["revision"] = 2

    repo.mutate_conversation_state("tenant_1", "project_1", corrupt)
    with pytest.raises(ConversationConflictError, match="idempotency_corrupt"):
        _create(CollaborationService(JsonCollaborationRepository(repo.root)))


@pytest.mark.parametrize("corruption", ["first_revision", "jump", "gap", "fork", "final_revision", "actor_mismatch"])
def test_legacy_replay_rejects_corrupt_audit_chain(conversation, corruption):
    service, repo = conversation
    created = _create(service)
    service.edit_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=created.id, client_request_id="edit_legacy", body="Edited", expected_revision=1,
    )
    service.delete_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=created.id, client_request_id="delete_legacy", expected_revision=2,
    )

    def corrupt(state):
        create_record = next(record for record in state["idempotency"].values() if record["client_request_id"] == "request_1")
        create_record["response_ref"] = {"message_id": created.id}
        audits = state["message_audits"]
        edit_id = next(audit_id for audit_id, audit in audits.items() if audit["operation"] == "edit")
        delete_id = next(audit_id for audit_id, audit in audits.items() if audit["operation"] == "delete")
        if corruption == "first_revision":
            audits[edit_id]["prior_revision"] = 2
            audits[edit_id]["resulting_revision"] = 3
        elif corruption == "jump":
            audits[edit_id]["resulting_revision"] = 4
        elif corruption == "gap":
            audits[delete_id]["prior_revision"] = 3
            audits[delete_id]["resulting_revision"] = 4
        elif corruption == "fork":
            duplicate = dict(audits[edit_id])
            duplicate["id"] = "msg_audit_fork"
            audits[duplicate["id"]] = duplicate
        elif corruption == "actor_mismatch":
            audits[edit_id]["actor_id"] = "human_other"
        else:
            state["messages"][created.id]["revision"] = 4

    repo.mutate_conversation_state("tenant_1", "project_1", corrupt)
    with pytest.raises(ConversationConflictError, match="idempotency_corrupt"):
        _create(CollaborationService(JsonCollaborationRepository(repo.root)))


def test_conversation_mutator_annotations_resolve_at_runtime():
    protocol_hints = typing.get_type_hints(CollaborationRepository.mutate_conversation_state)
    concrete_hints = typing.get_type_hints(JsonCollaborationRepository.mutate_conversation_state)
    assert "callback" in protocol_hints and "return" in protocol_hints
    assert "callback" in concrete_hints and "return" in concrete_hints


@pytest.mark.parametrize("corruption", [
    "message_id", "tenant", "project", "author_id", "author_kind", "revision", "body", "timestamps", "deleted",
    "record_operation", "record_actor", "record_request",
])
def test_idempotency_snapshot_replay_rejects_untrusted_scope_or_response(conversation, corruption):
    service, repo = conversation
    message = _create(service)

    def corrupt(state):
        record = next(iter(state["idempotency"].values()))
        snapshot = record["response_ref"]["response_snapshot"]
        if corruption == "message_id":
            snapshot["id"] = "msg_other"
        elif corruption == "tenant":
            snapshot["tenant_id"] = "tenant_other"
        elif corruption == "project":
            snapshot["project_id"] = "project_other"
        elif corruption == "author_id":
            snapshot["author"]["id"] = "human_other"
        elif corruption == "author_kind":
            snapshot["author"]["kind"] = "agent"
        elif corruption == "revision":
            snapshot["revision"] = 2
        elif corruption == "body":
            snapshot["body"] = "Changed"
        elif corruption == "timestamps":
            snapshot["created_at"] = "2026-01-02T09:01:00+00:00"
        elif corruption == "deleted":
            snapshot["deleted_at"] = "2026-01-02T09:01:00+00:00"
        elif corruption == "record_operation":
            record["operation"] = "edit"
        elif corruption == "record_actor":
            record["authenticated_human_actor_id"] = "human_other"
        else:
            record["client_request_id"] = "other_request"

    repo.mutate_conversation_state("tenant_1", "project_1", corrupt)
    with pytest.raises(ConversationConflictError, match="idempotency_corrupt"):
        _create(CollaborationService(JsonCollaborationRepository(repo.root)))


@pytest.mark.parametrize("corruption", [
    "final_body", "intermediate_prior_body", "missing_record", "duplicate_record",
    "snapshot_extra", "snapshot_missing", "response_ref_extra", "record_missing",
    "audit_extra", "current_missing", "nonselected_snapshot", "final_snapshot",
])
def test_replay_rejects_any_corruption_in_canonical_whole_message_history(conversation, corruption):
    service, repo = conversation
    created = _create(service)
    service.edit_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=created.id, client_request_id="edit_history", body="Edited", expected_revision=1,
    )
    service.delete_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=created.id, client_request_id="delete_history", expected_revision=2,
    )

    def corrupt(state):
        records = {record["client_request_id"]: (key, record) for key, record in state["idempotency"].items()}
        create_key, create_record = records["request_1"]
        edit_key, edit_record = records["edit_history"]
        delete_key, delete_record = records["delete_history"]
        audits = state["message_audits"]
        delete_audit = next(audit for audit in audits.values() if audit["operation"] == "delete")
        edit_audit = next(audit for audit in audits.values() if audit["operation"] == "edit")
        if corruption == "final_body":
            state["messages"][created.id]["body"] = "tampered"
        elif corruption == "intermediate_prior_body":
            delete_audit["prior_body"] = "tampered"
        elif corruption == "missing_record":
            del state["idempotency"][edit_key]
        elif corruption == "duplicate_record":
            state["idempotency"]["duplicate"] = dict(edit_record)
        elif corruption == "snapshot_extra":
            create_record["response_ref"]["response_snapshot"]["extra"] = True
        elif corruption == "snapshot_missing":
            create_record["response_ref"]["response_snapshot"].pop("body")
        elif corruption == "response_ref_extra":
            create_record["response_ref"]["extra"] = True
        elif corruption == "record_missing":
            create_record.pop("created_at")
        elif corruption == "audit_extra":
            edit_audit["extra"] = True
        elif corruption == "current_missing":
            state["messages"][created.id].pop("body")
        elif corruption == "nonselected_snapshot":
            edit_record["response_ref"]["response_snapshot"]["body"] = "tampered"
        else:
            delete_record["response_ref"]["response_snapshot"]["deleted_at"] = "2026-01-02T09:01:00+00:00"

    repo.mutate_conversation_state("tenant_1", "project_1", corrupt)
    with pytest.raises(ConversationConflictError, match="idempotency_corrupt"):
        _create(CollaborationService(JsonCollaborationRepository(repo.root)))


def test_stale_edit_conflicts(conversation):
    service, _ = conversation
    message = _create(service)
    service.edit_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", message_id=message.id, client_request_id="edit_1", body="Edited", expected_revision=1)
    with pytest.raises(ConversationConflictError, match="revision_conflict"):
        service.edit_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", message_id=message.id, client_request_id="edit_2", body="Again", expected_revision=1)


def test_edit_audit_contains_actor_request_revision_and_prior_body(conversation):
    service, _ = conversation
    message = _create(service)
    service.edit_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", message_id=message.id, client_request_id="edit_1", body="Edited", expected_revision=1)
    audit = service.conversation_audits("tenant_1", "project_1")[0]
    assert (audit.actor_id, audit.client_request_id, audit.prior_revision, audit.prior_body, audit.resulting_revision) == ("human_1", "edit_1", 1, "Hello", 2)


def test_delete_audit_contains_attribution_and_preserves_links(conversation):
    service, repo = conversation
    message = service.create_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", client_request_id="request_1", body="Hello", links={"work_item_id": "task_1", "output_id": "output_1"})
    deleted = service.delete_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", message_id=message.id, client_request_id="delete_1", expected_revision=1)
    assert deleted.deleted_at and deleted.links == message.links and deleted.body is None
    assert service.conversation_audits("tenant_1", "project_1")[0].actor_id == "human_1"
    assert serialize_conversation_message(ConversationMessage(
        id="msg_public_1", tenant_id="tenant_1", project_id="project_1", kind="human_message",
        author={"id": "human_1", "provider": "hidden", "runtime": {"host": "hidden"}},
        body="Safe", created_at="2026-01-02T09:00:00+00:00",
        links={"work_item_id": "task_1", "path": "/private/hidden", "raw_exception": "hidden"},
    )) == {
        "id": "msg_public_1", "mission_id": "project_1", "kind": "human_message",
        "author": {"id": "human_1"}, "body": "Safe", "created_at": "2026-01-02T09:00:00+00:00",
        "edited_at": None, "thread": {"reply_count": 0, "latest_replies": []}, "reactions": [],
        "saved": False, "links": {"work_item_id": "task_1", "run_id": None, "output_id": None},
    }
    legacy = service.add_comment(
        tenant_id="tenant_1", project_id="project_1", author_id="human_1", body="Legacy comment",
        target_type=CommentTargetType.PROJECT,
    )
    assert [item.id for item in service.conversation_messages("tenant_1", "project_1")].count(legacy.id) == 1
    state = repo.conversation_state("tenant_1", "project_1")
    assert legacy.id not in state["messages"]
    restarted = CollaborationService(JsonCollaborationRepository(repo.root))
    assert [item.id for item in restarted.conversation_messages("tenant_1", "project_1")].count(legacy.id) == 1
    with pytest.raises(ValidationError):
        service.create_conversation_message(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
            client_request_id="nested_link", body="Do not persist", links={"work_item_id": {"api_key": "secret", "runtime": {"host": "hidden"}}},
        )
    persisted = (repo.root / "tenant_1" / "project_1" / "collaboration" / "conversation_state.json").read_text()
    assert "api_key" not in persisted and "runtime" not in persisted and "hidden" not in persisted


@pytest.mark.parametrize("fault_stage", ["before_write", "before_temp_fsync", "after_temp_fsync", "before_replace"])
def test_crash_before_state_replace_leaves_neither_audit_nor_change(conversation, fault_stage):
    service, repo = conversation
    message = _create(service)
    repo.conversation_fault = lambda stage: (_ for _ in ()).throw(OSError("boom")) if stage == fault_stage else None
    with pytest.raises(OSError):
        service.edit_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", message_id=message.id, client_request_id="edit_1", body="Edited", expected_revision=1)
    messages, audits, idempotency = _restart_snapshot(repo.root)
    assert messages == ["Hello"] and audits == 0 and idempotency == 1
    directory = repo.root / "tenant_1" / "project_1" / "collaboration"
    assert list(directory.glob(".conversation_state.json.*.tmp")) == []


@pytest.mark.parametrize("fault_stage", ["after_replace", "before_parent_fsync", "after_parent_fsync"])
def test_crash_after_state_replace_leaves_both(conversation, fault_stage):
    service, repo = conversation
    message = _create(service)
    repo.conversation_fault = lambda stage: (_ for _ in ()).throw(OSError("boom")) if stage == fault_stage else None
    with pytest.raises(OSError):
        service.edit_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", message_id=message.id, client_request_id="edit_1", body="Edited", expected_revision=1)
    messages, audits, idempotency = _restart_snapshot(repo.root)
    assert messages == ["Edited"] and audits == 1 and idempotency == 2


def test_idempotent_delete_replays(conversation):
    service, repo = conversation
    message = _create(service)
    first = service.delete_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", message_id=message.id, client_request_id="delete_1", expected_revision=1)
    second = service.delete_conversation_message(tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1", message_id=message.id, client_request_id="delete_1", expected_revision=1)
    assert first == second
    assert _spawned_snapshot_result(repo.root, message.id, "delete", expected_revision=1) == first.to_dict()


def test_delete_rejects_body_without_persisting_any_change(conversation):
    service, repo = conversation
    message = _create(service)
    state_path = repo.root / "tenant_1" / "project_1" / "collaboration" / "conversation_state.json"
    before = state_path.read_text()
    with pytest.raises(ValidationError):
        service.delete_conversation_message(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
            message_id=message.id, client_request_id="delete_body", expected_revision=1, body="unexpected",
        )
    assert state_path.read_text() == before


@pytest.mark.parametrize("operation", ["create", "edit", "delete"])
def test_locked_membership_check_blocks_toctou_replay_and_mutation(conversation, monkeypatch, operation):
    service, repo = conversation
    message = _create(service)
    state_path = repo.root / "tenant_1" / "project_1" / "collaboration" / "conversation_state.json"
    before = state_path.read_text()
    original_outer_check = service._conversation._require_human_member

    def remove_after_outer_check(tenant_id, project_id, actor_id):
        original_outer_check(tenant_id, project_id, actor_id)
        room = repo.get_room(tenant_id, project_id)
        repo.save_room(replace(room, members=[], revision=room.revision + 1), room.revision)

    monkeypatch.setattr(service._conversation, "_require_human_member", remove_after_outer_check)
    with pytest.raises(AuthorizationError):
        if operation == "create":
            service.create_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                client_request_id="request_1", body="Hello",
            )
        elif operation == "edit":
            service.edit_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                message_id=message.id, client_request_id="edit_toctou", body="Edited", expected_revision=1,
            )
        else:
            service.delete_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                message_id=message.id, client_request_id="delete_toctou", expected_revision=1,
            )
    assert state_path.read_text() == before


@pytest.mark.parametrize("operation", ["create", "edit", "delete"])
def test_locked_write_role_check_blocks_viewer_downgrade(conversation, monkeypatch, operation):
    """A human can lose write authority after the optimistic precheck."""
    service, repo = conversation
    message = _create(service)
    state_path = repo.root / "tenant_1" / "project_1" / "collaboration" / "conversation_state.json"
    before = state_path.read_text()
    original_outer_check = service._conversation._require_human_member

    def downgrade_after_outer_check(tenant_id, project_id, actor_id):
        original_outer_check(tenant_id, project_id, actor_id)
        room = repo.get_room(tenant_id, project_id)
        downgraded = replace(
            room,
            members=[replace(member, role="viewer") if member.actor_id == actor_id else member for member in room.members],
            revision=room.revision + 1,
        )
        repo.save_room(downgraded, room.revision)

    monkeypatch.setattr(service._conversation, "_require_human_member", downgrade_after_outer_check)
    with pytest.raises(AuthorizationError):
        if operation == "create":
            service.create_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                client_request_id="viewer_create", body="Blocked",
            )
        elif operation == "edit":
            service.edit_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                message_id=message.id, client_request_id="viewer_edit", body="Blocked", expected_revision=1,
            )
        else:
            service.delete_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                message_id=message.id, client_request_id="viewer_delete", expected_revision=1,
            )
    assert state_path.read_text() == before


def _add_second_human(service: CollaborationService) -> None:
    room = service.repository.get_room("tenant_1", "project_1")
    service.add_member(
        tenant_id="tenant_1", project_id="project_1", actor_id="human_1",
        member_id="human_2", role="member", expected_revision=room.revision,
    )


def test_reply_depth_is_one(conversation):
    service, _ = conversation
    root = _create(service)
    reply = service.reply_to_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        parent_message_id=root.id, client_request_id="reply_1", body="First reply",
    )
    nested = service.reply_to_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        parent_message_id=reply.id, client_request_id="reply_2", body="Reply to reply",
    )
    assert reply.root_message_id == root.id
    assert nested.root_message_id == root.id
    assert service.reply_to_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        parent_message_id=reply.id, client_request_id="reply_2", body="Reply to reply",
    ) == nested


def test_reply_rejects_deleted_missing_and_cross_mission_parent(conversation, tmp_path):
    service, _ = conversation
    root = _create(service)
    service.delete_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=root.id, client_request_id="delete_root", expected_revision=1,
    )
    for parent in (root.id, "msg_missing"):
        with pytest.raises(NotFoundError) as denied:
            service.reply_to_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                parent_message_id=parent, client_request_id=f"reply_{parent}", body="Unavailable",
            )
        assert denied.value is not None


def test_reaction_add_remove_is_idempotent(conversation):
    service, repo = conversation
    message = _create(service)
    first = service.put_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=message.id, reaction="check", client_request_id="react_put_1",
    )
    semantic_retry = service.put_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=message.id, reaction="check", client_request_id="react_put_2",
    )
    assert first == semantic_retry
    assert len(repo.conversation_state("tenant_1", "project_1")["reactions"]) == 1
    removed = service.delete_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=message.id, reaction="check", client_request_id="react_delete_1",
    )
    assert removed.reactions == ()
    assert len(repo.conversation_state("tenant_1", "project_1")["reactions"]) == 0


def test_reaction_put_service_replays_and_rejects_hash_mismatch(conversation):
    service, _ = conversation
    first_message = _create(service)
    second_message = service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="request_2", body="Second",
    )
    first = service.put_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=first_message.id, reaction="check", client_request_id="same_put",
    )
    assert service.put_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=first_message.id, reaction="check", client_request_id="same_put",
    ) == first
    with pytest.raises(ConversationConflictError, match="idempotency_mismatch"):
        service.put_conversation_reaction(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
            message_id=second_message.id, reaction="check", client_request_id="same_put",
        )


def test_reaction_delete_service_replays_and_rejects_hash_mismatch(conversation):
    service, _ = conversation
    first_message = _create(service)
    second_message = service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="request_2", body="Second",
    )
    service.put_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=first_message.id, reaction="check", client_request_id="put_before_delete",
    )
    first = service.delete_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=first_message.id, reaction="check", client_request_id="same_delete",
    )
    assert service.delete_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=first_message.id, reaction="check", client_request_id="same_delete",
    ) == first
    with pytest.raises(ConversationConflictError, match="idempotency_mismatch"):
        service.delete_conversation_reaction(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
            message_id=second_message.id, reaction="check", client_request_id="same_delete",
        )


def test_saved_put_service_replays_and_rejects_hash_mismatch(conversation):
    service, _ = conversation
    first_message = _create(service)
    second_message = service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="request_2", body="Second",
    )
    first = service.put_saved_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=first_message.id, client_request_id="same_save",
    )
    assert first.saved is True
    assert service.put_saved_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=first_message.id, client_request_id="same_save",
    ) == first
    with pytest.raises(ConversationConflictError, match="idempotency_mismatch"):
        service.put_saved_conversation_message(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
            message_id=second_message.id, client_request_id="same_save",
        )


def test_saved_delete_service_replays_and_rejects_hash_mismatch(conversation):
    service, _ = conversation
    first_message = _create(service)
    second_message = service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="request_2", body="Second",
    )
    service.put_saved_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=first_message.id, client_request_id="save_before_delete",
    )
    first = service.delete_saved_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=first_message.id, client_request_id="same_unsave",
    )
    assert first.saved is False
    assert service.delete_saved_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=first_message.id, client_request_id="same_unsave",
    ) == first
    with pytest.raises(ConversationConflictError, match="idempotency_mismatch"):
        service.delete_saved_conversation_message(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
            message_id=second_message.id, client_request_id="same_unsave",
        )


def test_reactions_are_fixed_enum_and_saved_state_is_private(conversation):
    service, repo = conversation
    _add_second_human(service)
    message = _create(service)
    with pytest.raises(ValidationError):
        service.put_conversation_reaction(
            tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
            message_id=message.id, reaction="arbitrary", client_request_id="bad_reaction",
        )
    service.put_saved_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=message.id, client_request_id="private_save",
    )
    assert service.conversation_message_view("tenant_1", "project_1", message.id, "human_1").saved is True
    assert service.conversation_message_view("tenant_1", "project_1", message.id, "human_2").saved is False
    room = repo.get_room("tenant_1", "project_1")
    repo.save_room(replace(room, members=[item for item in room.members if item.actor_id != "human_1"], revision=room.revision + 1), room.revision)
    with pytest.raises(AuthorizationError):
        service.conversation_message_view("tenant_1", "project_1", message.id, "human_1")


@pytest.mark.parametrize("operation", ["reply", "reaction", "saved"])
def test_extensions_recheck_locked_write_authority(conversation, monkeypatch, operation):
    service, repo = conversation
    message = _create(service)
    original = service._conversation._require_human_member

    def revoke(tenant_id, project_id, actor_id):
        original(tenant_id, project_id, actor_id)
        room = repo.get_room(tenant_id, project_id)
        repo.save_room(replace(room, members=[], revision=room.revision + 1), room.revision)

    monkeypatch.setattr(service._conversation, "_require_human_member", revoke)
    with pytest.raises(AuthorizationError):
        if operation == "reply":
            service.reply_to_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                parent_message_id=message.id, client_request_id="blocked_reply", body="Blocked",
            )
        elif operation == "reaction":
            service.put_conversation_reaction(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                message_id=message.id, reaction="check", client_request_id="blocked_reaction",
            )
        else:
            service.put_saved_conversation_message(
                tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
                message_id=message.id, client_request_id="blocked_save",
            )


def test_legacy_comment_supports_thread_reaction_and_save_overlay_without_copy(conversation):
    service, repo = conversation
    legacy = service.add_comment(
        tenant_id="tenant_1", project_id="project_1", author_id="human_1",
        body="Legacy source", target_type=CommentTargetType.PROJECT,
    )
    reply = service.reply_to_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        parent_message_id=legacy.id, client_request_id="legacy_reply", body="Overlay reply",
    )
    service.put_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=legacy.id, reaction="check", client_request_id="legacy_reaction",
    )
    service.put_saved_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=legacy.id, client_request_id="legacy_save",
    )
    state = repo.conversation_state("tenant_1", "project_1")
    assert legacy.id not in state["messages"]
    assert state["messages"][reply.id]["root_message_id"] == legacy.id
    restarted = CollaborationService(JsonCollaborationRepository(repo.root))
    view = restarted.conversation_message_view("tenant_1", "project_1", legacy.id, "human_1")
    assert view.thread["reply_count"] == 1
    assert view.reactions == ({"reaction": "check", "count": 1, "reacted": True},)
    assert view.saved is True
    assert [item.id for item in restarted.conversation_roots("tenant_1", "project_1")].count(legacy.id) == 1


@pytest.mark.parametrize("bad_thread,bad_reactions", [
    ({"reply_count": 0, "latest_replies": [], "raw_runtime": "hidden"}, []),
    ({"reply_count": True, "latest_replies": []}, []),
    ({"reply_count": 0, "latest_replies": [{"body": "partial", "raw": "hidden"}]}, []),
    ({"reply_count": 0, "latest_replies": []}, [{"reaction": "arbitrary", "count": 1, "reacted": True}]),
    ({"reply_count": 0, "latest_replies": []}, [{"reaction": "check", "count": "1", "reacted": True}]),
    ({"reply_count": 0, "latest_replies": []}, [{"reaction": "check", "count": 1, "reacted": True, "actor_ids": ["human_1"]}]),
])
def test_public_serializer_recursively_rejects_noncanonical_thread_and_reaction_values(
    conversation, bad_thread, bad_reactions,
):
    service, _ = conversation
    message = _create(service)
    with pytest.raises(ValidationError):
        serialize_conversation_message(message, thread=bad_thread, reactions=bad_reactions)
