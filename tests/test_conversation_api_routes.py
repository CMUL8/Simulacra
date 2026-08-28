from __future__ import annotations

from pathlib import Path
from copy import deepcopy
from contextlib import contextmanager
from dataclasses import replace
import json

import pytest
from fastapi import FastAPI, HTTPException

from apps.api import conversation_routes
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.collaboration.models import Member
from simulacra.demo.identity import AuthContext, User
from simulacra.missions import JsonMissionRepository, MissionService
from simulacra.operation_graph import OperationGraphStore, load_operation_graph


def _context(actor_id: str = "human_1") -> AuthContext:
    return AuthContext(
        user=User(id=actor_id, email=f"{actor_id}@example.test", name="Avery" if actor_id == "human_1" else "Riley", password_hash="unused"),
        tenant_id="tenant_demo", role="member", auth_via="test",
    )


@pytest.fixture()
def conversation_api(monkeypatch, tmp_path: Path):
    collaboration = JsonCollaborationRepository(tmp_path / "rooms")
    rooms = CollaborationService(collaboration)
    room = rooms.create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="human_1", creator_name="Avery")
    rooms.add_member(
        tenant_id="tenant_demo", project_id="project_demo", actor_id="human_1", member_id="human_2",
        role="reviewer", member_name="Riley", expected_revision=room.revision,
    )
    mission = MissionService(JsonMissionRepository(tmp_path / "missions"))
    mission.bootstrap("tenant_demo", "project_demo", "human_1", {"title": "Demo", "objective": "A reviewable outcome"})
    agent = mission.add_agent(
        "tenant_demo", "project_demo", {"name": "Reconciliation analyst", "role": "Finance", "mandate": "Prepare the review pack"},
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    graph = OperationGraphStore(workspace, tenant_id="tenant_demo", project_id="project_demo")
    raw = load_operation_graph(Path(__file__).parents[1] / "schemas/operation-graph.v0.yaml")
    raw["metadata"].update({"tenant_id": "tenant_demo", "project_id": "project_demo"})
    revision = graph.create_revision(raw, expected_revision_hash=None)
    graph.approve_revision(revision.revision_hash, actor_id="human_1")
    monkeypatch.setattr(conversation_routes, "_collaboration_root", tmp_path / "rooms")
    monkeypatch.setattr(conversation_routes, "_mission_root", tmp_path / "missions")
    monkeypatch.setattr(conversation_routes, "_runs_root", tmp_path / "runs")
    monkeypatch.setattr(conversation_routes, "project_dir", lambda project_id: workspace)
    return {"collaboration": collaboration, "mission": mission, "agent": agent, "workspace": workspace}


def _message_body(request_id: str = "message_1") -> conversation_routes.ConversationCreateBody:
    return conversation_routes.ConversationCreateBody(
        client_request_id=request_id, body="Please prepare the review pack.", mode="message",
        assignee_agent_ids=[], reviewer_human_ids=[], source_message_id=None,
    )


def test_message_mode_never_creates_run(conversation_api):
    result = conversation_routes.post_message("project_demo", _message_body(), _context())

    assert result["work_item"] is None
    assert result["message"]["kind"] == "human_message"
    assert conversation_api["mission"].runs("tenant_demo", "project_demo") == []
    assert set(result["message"]) == {"id", "mission_id", "kind", "author", "body", "created_at", "edited_at", "thread", "reactions", "saved", "links"}


def test_pending_member_cannot_read_or_mutate_conversation_until_acceptance_complete(conversation_api):
    repository = conversation_api["collaboration"]
    root = conversation_routes.post_message("project_demo", _message_body("visible_root"), _context())["message"]
    room = repository.get_room("tenant_demo", "project_demo")
    repository.save_room(replace(
        room,
        members=[*room.members, Member(
            actor_id="pending_admin", role="admin", transaction_id="txn_pending_route",
            visibility_state="pending_commit",
        )],
        revision=room.revision + 1,
    ), room.revision)
    pending = _context("pending_admin")

    actions = [
        lambda: conversation_routes.get_conversation("project_demo", before=None, limit=50, ctx=pending),
        lambda: conversation_routes.post_message("project_demo", _message_body("pending_send"), pending),
        lambda: conversation_routes.patch_message(
            "project_demo", root["id"],
            conversation_routes.ConversationPatchBody(
                client_request_id="pending_edit", expected_revision=1, body="Must not edit",
            ), pending,
        ),
        lambda: conversation_routes.put_reaction(
            "project_demo", root["id"], "check",
            conversation_routes.ConversationActionBody(client_request_id="pending_reaction"), pending,
        ),
        lambda: conversation_routes.put_saved(
            "project_demo", root["id"],
            conversation_routes.ConversationActionBody(client_request_id="pending_save"), pending,
        ),
    ]
    for action in actions:
        with pytest.raises(HTTPException) as denied:
            action()
        assert denied.value.status_code == 403

    journal = repository.root / ".invitation-acceptance" / "tenant_demo" / "project_demo" / "txn_pending_route.json"
    journal.parent.mkdir(parents=True, exist_ok=True)
    journal.write_text(json.dumps({
        "state": "COMPLETE", "transaction_id": "txn_pending_route",
        "tenant_id": "tenant_demo", "project_id": "project_demo",
    }))
    room = repository.get_room("tenant_demo", "project_demo")
    repository.save_room(replace(
        room,
        members=[replace(member, visibility_state="committed") if member.transaction_id == "txn_pending_route" else member for member in room.members],
        revision=room.revision + 1,
    ), room.revision)
    assert conversation_routes.get_conversation("project_demo", before=None, limit=50, ctx=pending)["items"]
    created = conversation_routes.post_message("project_demo", _message_body("accepted_send"), pending)
    assert created["message"]["body"] == "Please prepare the review pack."


def test_hidden_pending_member_name_is_not_projected_as_public_author(conversation_api):
    repository = conversation_api["collaboration"]
    room = repository.get_room("tenant_demo", "project_demo")
    repository.save_room(replace(
        room,
        members=[*room.members, Member(
            actor_id="pending_human", role="member", display_name="Hidden Invitee",
            transaction_id="txn_hidden_author", visibility_state="pending_commit",
        )],
        revision=room.revision + 1,
    ), room.revision)
    author = conversation_routes._message_author(
        type("Message", (), {"author": {"id": "pending_human", "kind": "human"}})(),
        repository, "tenant_demo", "project_demo",
    )
    assert author == {
        "id": "pending_human", "kind": "human", "display_name": "A human", "avatar_url": None,
    }


def test_conversation_post_uses_stable_member_ids_not_display_names(conversation_api):
    agent = conversation_api["agent"]
    body = conversation_routes.ConversationCreateBody(
        client_request_id="assignment_1", body="@not-a-real-handle prepare the review pack.", mode="assignment",
        assignee_agent_ids=[agent.id], reviewer_human_ids=["human_2"], source_message_id=None,
    )
    first = conversation_routes.post_message("project_demo", body, _context())
    replay = conversation_routes.post_message("project_demo", body, _context())

    assert replay == first
    assert first["message"]["links"]["work_item_id"] == first["work_item"]["source_id"]
    assert first["message"]["links"]["run_id"]
    run = conversation_api["mission"].runs("tenant_demo", "project_demo")[0]
    assert run.assigned_agent_ids == [agent.id]
    task = conversation_api["collaboration"].list_tasks("tenant_demo", "project_demo")[0]
    assert task.collaborator_ids == ["human_2"]
    assert set(first["work_item"]) == {
        "source_type", "source_id", "mission_id", "revision", "title", "summary", "state", "assignee", "created_at", "updated_at", "allowed_actions",
        "action_targets",
    }
    assert first["work_item"]["action_targets"] == {}
    assert not any(term in str(first).lower() for term in ("codex", "provider", "runtime", "graph", "path", "exception"))


def test_assignment_retry_keeps_its_committed_identity_after_plan_changes(conversation_api):
    agent = conversation_api["agent"]
    body = conversation_routes.ConversationCreateBody(
        client_request_id="assignment_retry", body="Prepare the review pack.", mode="assignment",
        assignee_agent_ids=[agent.id], reviewer_human_ids=["human_2"], source_message_id=None,
    )
    first = conversation_routes.post_message("project_demo", body, _context())
    graph = OperationGraphStore(conversation_api["workspace"], tenant_id="tenant_demo", project_id="project_demo")
    changed = deepcopy(graph.current_revision().graph)
    changed["metadata"]["version"] = int(changed["metadata"].get("version", 1)) + 1
    next_revision = graph.create_revision(changed, expected_revision_hash=graph.current_revision().revision_hash)
    graph.approve_revision(next_revision.revision_hash, actor_id="human_1")

    assert conversation_routes.post_message("project_demo", body, _context()) == first
    assert len(conversation_api["mission"].runs("tenant_demo", "project_demo")) == 1


def test_assignment_message_edit_and_delete_keep_work_links(conversation_api):
    agent = conversation_api["agent"]
    created = conversation_routes.post_message(
        "project_demo",
        conversation_routes.ConversationCreateBody(
            client_request_id="assignment_links", body="Prepare the review pack.", mode="assignment",
            assignee_agent_ids=[agent.id], reviewer_human_ids=[], source_message_id=None,
        ),
        _context(),
    )["message"]
    edited = conversation_routes.patch_message(
        "project_demo", created["id"],
        conversation_routes.ConversationPatchBody(client_request_id="edit_assignment", expected_revision=1, body="Prepare the updated review pack."),
        _context(),
    )["message"]
    deleted = conversation_routes.delete_message(
        "project_demo", created["id"],
        conversation_routes.ConversationDeleteBody(client_request_id="delete_assignment", expected_revision=2), _context(),
    )["message"]

    assert edited["links"] == created["links"]
    assert deleted["links"] == created["links"]
    assert deleted["body"] is None


def test_patch_stale_revision_and_delete_replay(conversation_api):
    created = conversation_routes.post_message("project_demo", _message_body(), _context())["message"]
    edited = conversation_routes.patch_message(
        "project_demo", created["id"],
        conversation_routes.ConversationPatchBody(client_request_id="edit_1", expected_revision=1, body="Updated review request."),
        _context(),
    )
    with pytest.raises(HTTPException) as stale:
        conversation_routes.patch_message(
            "project_demo", created["id"],
            conversation_routes.ConversationPatchBody(client_request_id="edit_stale", expected_revision=1, body="Stale edit."),
            _context(),
        )
    deleted = conversation_routes.delete_message(
        "project_demo", created["id"],
        conversation_routes.ConversationDeleteBody(client_request_id="delete_1", expected_revision=2), _context(),
    )
    replay = conversation_routes.delete_message(
        "project_demo", created["id"],
        conversation_routes.ConversationDeleteBody(client_request_id="delete_1", expected_revision=2), _context(),
    )

    assert edited["message"]["body"] == "Updated review request."
    assert stale.value.status_code == 409
    assert stale.value.detail["code"] == "revision_conflict"
    assert deleted == replay
    assert deleted["message"]["body"] is None
    assert deleted["message"]["links"] == {"work_item_id": None, "run_id": None, "output_id": None}


def test_conversation_get_is_oldest_to_newest_with_safe_cursor(conversation_api):
    for request_id, body in (("message_1", "First"), ("message_2", "Second"), ("message_3", "Third")):
        conversation_routes.post_message(
            "project_demo",
            conversation_routes.ConversationCreateBody(
                client_request_id=request_id, body=body, mode="message", assignee_agent_ids=[], reviewer_human_ids=[], source_message_id=None,
            ),
            _context(),
        )
    newest = conversation_routes.get_conversation("project_demo", before=None, limit=2, ctx=_context())
    older = conversation_routes.get_conversation("project_demo", before=newest["next_before"], limit=2, ctx=_context())

    assert [item["body"] for item in newest["items"]] == ["Second", "Third"]
    assert [item["body"] for item in older["items"]] == ["First"]
    with pytest.raises(HTTPException) as bad_cursor:
        conversation_routes.get_conversation("project_demo", before="not-a-cursor", limit=50, ctx=_context())
    assert bad_cursor.value.status_code == 400
    assert bad_cursor.value.detail == {"code": "cursor_invalid", "message": conversation_routes._CURSOR_MESSAGE}


def test_conversation_get_rechecks_membership_at_publication_boundary(conversation_api, monkeypatch):
    repository = conversation_api["collaboration"]
    monkeypatch.setattr(conversation_routes, "_collaboration", lambda: repository)
    conversation_routes.post_message("project_demo", _message_body(), _context())
    original_lock = repository.room_lock

    @contextmanager
    def revoke_then_lock(tenant_id: str, project_id: str):
        room = repository.get_room(tenant_id, project_id)
        room.members = [member for member in room.members if member.actor_id != "human_1"]
        room.revision += 1
        repository.save_room(room, expected_revision=room.revision - 1)
        with original_lock(tenant_id, project_id) as locked:
            yield locked

    monkeypatch.setattr(repository, "room_lock", revoke_then_lock)
    with pytest.raises(HTTPException) as denied:
        conversation_routes.get_conversation("project_demo", before=None, limit=50, ctx=_context())
    assert denied.value.status_code == 403
    assert denied.value.detail == {"code": "conversation_forbidden", "message": conversation_routes._FORBIDDEN_MESSAGE}


def test_conversation_request_validation_uses_fixed_public_envelope(conversation_api):
    with pytest.raises(HTTPException) as invalid:
        conversation_routes.post_message(
            "project_demo",
            {"client_request_id": "bad_1", "body": "Hello", "mode": "message", "unexpected": True},
            _context(),
        )

    assert invalid.value.status_code == 400
    assert invalid.value.detail == {"code": "conversation_invalid", "message": conversation_routes._INVALID_MESSAGE}


def test_conversation_mutations_declare_real_json_request_bodies():
    app = FastAPI()
    app.include_router(conversation_routes.router)
    routes = {route.path: route for route in conversation_routes.router.routes if hasattr(route, "dependant")}
    for path in (
        "/projects/{project_id}/conversation/messages",
        "/projects/{project_id}/conversation/messages/{message_id}",
    ):
        assert routes[path].dependant.body_params
    schema = app.openapi()
    messages = schema["paths"]["/projects/{project_id}/conversation/messages"]
    item = schema["paths"]["/projects/{project_id}/conversation/messages/{message_id}"]
    assert "requestBody" in messages["post"]
    assert "requestBody" in item["patch"]
    assert "requestBody" in item["delete"]


def test_assignment_response_rechecks_membership_before_publication(conversation_api, monkeypatch):
    repository = conversation_api["collaboration"]
    monkeypatch.setattr(conversation_routes, "_collaboration", lambda: repository)
    original_lock = repository.room_lock
    lock_count = 0

    @contextmanager
    def revoke_only_at_response_publication(tenant_id: str, project_id: str):
        nonlocal lock_count
        lock_count += 1
        # The coordinator's authoritative commit takes the first room lock.
        # The route's second lock is its response-publication boundary.
        if lock_count == 2:
            room = repository.get_room(tenant_id, project_id)
            room.members = [member for member in room.members if member.actor_id != "human_1"]
            room.revision += 1
            repository.save_room(room, expected_revision=room.revision - 1)
        with original_lock(tenant_id, project_id) as room:
            yield room

    monkeypatch.setattr(repository, "room_lock", revoke_only_at_response_publication)
    with pytest.raises(HTTPException) as denied:
        conversation_routes.post_message(
            "project_demo",
            conversation_routes.ConversationCreateBody(
                client_request_id="assignment_revoked", body="Prepare the review pack.", mode="assignment",
                assignee_agent_ids=[conversation_api["agent"].id], reviewer_human_ids=[], source_message_id=None,
            ),
            _context(),
        )
    assert denied.value.status_code == 403
    assert denied.value.detail == {"code": "conversation_forbidden", "message": conversation_routes._FORBIDDEN_MESSAGE}
    # The work remains durably recoverable for a current collaborator, but no
    # result crossed the response boundary to the removed human.
    assert len(conversation_api["mission"].runs("tenant_demo", "project_demo")) == 1


def test_assignment_replay_does_not_publish_to_a_human_removed_after_recovery(conversation_api, monkeypatch):
    repository = conversation_api["collaboration"]
    monkeypatch.setattr(conversation_routes, "_collaboration", lambda: repository)
    body = conversation_routes.ConversationCreateBody(
        client_request_id="assignment_replay_revoked", body="Prepare the review pack.", mode="assignment",
        assignee_agent_ids=[conversation_api["agent"].id], reviewer_human_ids=[], source_message_id=None,
    )
    first = conversation_routes.post_message("project_demo", body, _context())
    original_replay = conversation_routes.AssignmentCoordinator.replay_if_exists
    replayed = False

    def record_replay(self, **kwargs):
        nonlocal replayed
        replayed = True
        return original_replay(self, **kwargs)

    monkeypatch.setattr(conversation_routes.AssignmentCoordinator, "replay_if_exists", record_replay)
    original_lock = repository.room_lock

    @contextmanager
    def revoke_at_replay_response_boundary(tenant_id: str, project_id: str):
        room = repository.get_room(tenant_id, project_id)
        room.members = [member for member in room.members if member.actor_id != "human_1"]
        room.revision += 1
        repository.save_room(room, expected_revision=room.revision - 1)
        with original_lock(tenant_id, project_id) as locked:
            yield locked

    monkeypatch.setattr(repository, "room_lock", revoke_at_replay_response_boundary)
    with pytest.raises(HTTPException) as denied:
        conversation_routes.post_message("project_demo", body, _context())
    assert replayed
    assert denied.value.status_code == 403
    assert denied.value.detail == {"code": "conversation_forbidden", "message": conversation_routes._FORBIDDEN_MESSAGE}
    assert len(conversation_api["mission"].runs("tenant_demo", "project_demo")) == 1
    assert first["message"]["links"]["work_item_id"]


def test_reply_route_keeps_one_level_and_returns_new_reply(conversation_api):
    root = conversation_routes.post_message("project_demo", _message_body(), _context())["message"]
    first = conversation_routes.post_reply(
        "project_demo", root["id"],
        conversation_routes.ConversationReplyBody(client_request_id="route_reply_1", body="First reply"),
        _context(),
    )["message"]
    nested = conversation_routes.post_reply(
        "project_demo", first["id"],
        conversation_routes.ConversationReplyBody(client_request_id="route_reply_2", body="Nested reply"),
        _context(),
    )["message"]
    assert first["id"] != root["id"]
    assert nested["id"] != first["id"]
    page = conversation_routes.get_conversation("project_demo", before=None, limit=50, ctx=_context())
    assert len(page["items"]) == 1
    assert page["items"][0]["thread"]["reply_count"] == 2
    assert [item["id"] for item in page["items"][0]["thread"]["latest_replies"]] == [first["id"], nested["id"]]


def test_reply_read_resolves_root_or_direct_reply_and_paginates(conversation_api):
    root = conversation_routes.post_message("project_demo", _message_body(), _context())["message"]
    reply_ids = []
    parent_id = root["id"]
    for index in range(4):
        reply = conversation_routes.post_reply(
            "project_demo", parent_id,
            conversation_routes.ConversationReplyBody(client_request_id=f"page_reply_{index}", body=f"Reply {index}"),
            _context(),
        )["message"]
        reply_ids.append(reply["id"])
        parent_id = reply["id"]
    newest = conversation_routes.get_replies(
        "project_demo", reply_ids[0], before=None, limit=2, ctx=_context(),
    )
    older = conversation_routes.get_replies(
        "project_demo", root["id"], before=newest["next_before"], limit=2, ctx=_context(),
    )
    assert [item["id"] for item in newest["items"]] == reply_ids[2:]
    assert [item["id"] for item in older["items"]] == reply_ids[:2]
    root_page = conversation_routes.get_conversation("project_demo", before=None, limit=50, ctx=_context())
    assert root_page["items"][0]["thread"]["reply_count"] == 4
    assert [item["id"] for item in root_page["items"][0]["thread"]["latest_replies"]] == reply_ids[-3:]


def test_reply_read_requires_current_membership(conversation_api):
    root = conversation_routes.post_message("project_demo", _message_body(), _context())["message"]
    repository = conversation_api["collaboration"]
    room = repository.get_room("tenant_demo", "project_demo")
    room.members = [member for member in room.members if member.actor_id != "human_1"]
    room.revision += 1
    repository.save_room(room, room.revision - 1)
    with pytest.raises(HTTPException) as denied:
        conversation_routes.get_replies("project_demo", root["id"], before=None, limit=50, ctx=_context())
    assert denied.value.status_code == 403


def test_reaction_put_route_replays_and_rejects_hash_mismatch(conversation_api):
    first_message = conversation_routes.post_message("project_demo", _message_body("reaction_root_1"), _context())["message"]
    second_message = conversation_routes.post_message("project_demo", _message_body("reaction_root_2"), _context())["message"]
    body = conversation_routes.ConversationActionBody(client_request_id="reaction_route_put")
    first = conversation_routes.put_reaction("project_demo", first_message["id"], "check", body, _context())
    assert conversation_routes.put_reaction("project_demo", first_message["id"], "check", body, _context()) == first
    assert first["message"]["reactions"] == [{"reaction": "check", "count": 1, "reacted": True}]
    with pytest.raises(HTTPException) as mismatch:
        conversation_routes.put_reaction("project_demo", second_message["id"], "check", body, _context())
    assert mismatch.value.status_code == 409
    assert mismatch.value.detail["code"] == "idempotency_mismatch"


def test_reaction_delete_route_replays_and_rejects_hash_mismatch(conversation_api):
    first_message = conversation_routes.post_message("project_demo", _message_body("reaction_delete_root_1"), _context())["message"]
    second_message = conversation_routes.post_message("project_demo", _message_body("reaction_delete_root_2"), _context())["message"]
    conversation_routes.put_reaction(
        "project_demo", first_message["id"], "check",
        conversation_routes.ConversationActionBody(client_request_id="prepare_delete"), _context(),
    )
    body = conversation_routes.ConversationActionBody(client_request_id="reaction_route_delete")
    first = conversation_routes.delete_reaction("project_demo", first_message["id"], "check", body, _context())
    assert conversation_routes.delete_reaction("project_demo", first_message["id"], "check", body, _context()) == first
    assert first["message"]["reactions"] == []
    with pytest.raises(HTTPException) as mismatch:
        conversation_routes.delete_reaction("project_demo", second_message["id"], "check", body, _context())
    assert mismatch.value.status_code == 409
    assert mismatch.value.detail["code"] == "idempotency_mismatch"


def test_saved_put_route_replays_and_rejects_hash_mismatch(conversation_api):
    first_message = conversation_routes.post_message("project_demo", _message_body("save_root_1"), _context())["message"]
    second_message = conversation_routes.post_message("project_demo", _message_body("save_root_2"), _context())["message"]
    body = conversation_routes.ConversationActionBody(client_request_id="saved_route_put")
    first = conversation_routes.put_saved("project_demo", first_message["id"], body, _context())
    assert first == {"saved": True}
    assert conversation_routes.put_saved("project_demo", first_message["id"], body, _context()) == first
    with pytest.raises(HTTPException) as mismatch:
        conversation_routes.put_saved("project_demo", second_message["id"], body, _context())
    assert mismatch.value.status_code == 409
    assert mismatch.value.detail["code"] == "idempotency_mismatch"


def test_saved_delete_route_replays_and_rejects_hash_mismatch(conversation_api):
    first_message = conversation_routes.post_message("project_demo", _message_body("unsave_root_1"), _context())["message"]
    second_message = conversation_routes.post_message("project_demo", _message_body("unsave_root_2"), _context())["message"]
    conversation_routes.put_saved(
        "project_demo", first_message["id"],
        conversation_routes.ConversationActionBody(client_request_id="prepare_unsave"), _context(),
    )
    body = conversation_routes.ConversationActionBody(client_request_id="saved_route_delete")
    first = conversation_routes.delete_saved("project_demo", first_message["id"], body, _context())
    assert first == {"saved": False}
    assert conversation_routes.delete_saved("project_demo", first_message["id"], body, _context()) == first
    with pytest.raises(HTTPException) as mismatch:
        conversation_routes.delete_saved("project_demo", second_message["id"], body, _context())
    assert mismatch.value.status_code == 409
    assert mismatch.value.detail["code"] == "idempotency_mismatch"


def test_extension_routes_use_fixed_errors_and_real_json_bodies(conversation_api):
    root = conversation_routes.post_message("project_demo", _message_body(), _context())["message"]
    with pytest.raises(HTTPException) as invalid:
        conversation_routes.put_reaction(
            "project_demo", root["id"], "arbitrary",
            conversation_routes.ConversationActionBody(client_request_id="invalid_reaction"), _context(),
        )
    assert invalid.value.status_code == 400
    assert invalid.value.detail == {"code": "conversation_invalid", "message": conversation_routes._INVALID_MESSAGE}

    app = FastAPI()
    app.include_router(conversation_routes.router)
    schema = app.openapi()["paths"]
    assert "requestBody" in schema["/projects/{project_id}/conversation/messages/{message_id}/replies"]["post"]
    reaction = schema["/projects/{project_id}/conversation/messages/{message_id}/reactions/{reaction}"]
    saved = schema["/projects/{project_id}/conversation/messages/{message_id}/saved"]
    assert "requestBody" in reaction["put"] and "requestBody" in reaction["delete"]
    assert "requestBody" in saved["put"] and "requestBody" in saved["delete"]


def test_removed_human_cannot_reply_react_save_or_read_private_state(conversation_api):
    root = conversation_routes.post_message("project_demo", _message_body(), _context())["message"]
    repository = conversation_api["collaboration"]
    room = repository.get_room("tenant_demo", "project_demo")
    room.members = [member for member in room.members if member.actor_id != "human_1"]
    room.revision += 1
    repository.save_room(room, room.revision - 1)
    calls = (
        lambda: conversation_routes.post_reply("project_demo", root["id"], conversation_routes.ConversationReplyBody(client_request_id="denied_reply", body="No"), _context()),
        lambda: conversation_routes.put_reaction("project_demo", root["id"], "check", conversation_routes.ConversationActionBody(client_request_id="denied_reaction"), _context()),
        lambda: conversation_routes.put_saved("project_demo", root["id"], conversation_routes.ConversationActionBody(client_request_id="denied_save"), _context()),
    )
    for call in calls:
        with pytest.raises(HTTPException) as denied:
            call()
        assert denied.value.status_code == 403
        assert denied.value.detail == {"code": "conversation_forbidden", "message": conversation_routes._FORBIDDEN_MESSAGE}


def test_private_save_get_is_per_human_and_removal_before_publication_is_denied(conversation_api, monkeypatch):
    root = conversation_routes.post_message("project_demo", _message_body(), _context())["message"]
    conversation_routes.put_saved(
        "project_demo", root["id"], conversation_routes.ConversationActionBody(client_request_id="human_1_save"), _context(),
    )
    human_1_page = conversation_routes.get_conversation("project_demo", before=None, limit=50, ctx=_context())
    human_2_page = conversation_routes.get_conversation("project_demo", before=None, limit=50, ctx=_context("human_2"))
    assert human_1_page["items"][0]["saved"] is True
    assert human_2_page["items"][0]["saved"] is False
    repository = conversation_api["collaboration"]
    original_publication = conversation_routes._require_current_member_at_publication

    def revoke_then_publish(repo, *, tenant_id, project_id, human_id):
        room = repo.get_room(tenant_id, project_id)
        room.members = [member for member in room.members if member.actor_id != human_id]
        room.revision += 1
        repo.save_room(room, room.revision - 1)
        original_publication(repo, tenant_id=tenant_id, project_id=project_id, human_id=human_id)

    monkeypatch.setattr(conversation_routes, "_require_current_member_at_publication", revoke_then_publish)
    with pytest.raises(HTTPException) as denied:
        conversation_routes.delete_saved(
            "project_demo", root["id"], conversation_routes.ConversationActionBody(client_request_id="human_1_unsave"), _context(),
        )
    assert denied.value.status_code == 403


def test_reply_and_reaction_route_replay_returns_exact_prior_public_response_after_later_changes(conversation_api):
    root = conversation_routes.post_message("project_demo", _message_body(), _context())["message"]
    reply_body = conversation_routes.ConversationReplyBody(client_request_id="exact_reply", body="First reply")
    first_reply = conversation_routes.post_reply("project_demo", root["id"], reply_body, _context())
    conversation_routes.put_reaction(
        "project_demo", first_reply["message"]["id"], "check",
        conversation_routes.ConversationActionBody(client_request_id="later_reply_reaction"), _context(),
    )
    assert conversation_routes.post_reply("project_demo", root["id"], reply_body, _context()) == first_reply

    reaction_body = conversation_routes.ConversationActionBody(client_request_id="exact_reaction")
    first_reaction = conversation_routes.put_reaction(
        "project_demo", root["id"], "check", reaction_body, _context(),
    )
    conversation_routes.post_reply(
        "project_demo", root["id"],
        conversation_routes.ConversationReplyBody(client_request_id="later_root_reply", body="Later reply"), _context(),
    )
    assert conversation_routes.put_reaction(
        "project_demo", root["id"], "check", reaction_body, _context(),
    ) == first_reaction
