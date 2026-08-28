from __future__ import annotations

import inspect
import asyncio
import threading
import json
from dataclasses import replace
from pathlib import Path

import pytest
from fastapi import HTTPException

from apps.api import workplace_event_routes
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.collaboration.events import make_domain_event
from simulacra.collaboration.models import ActorType, Member
from simulacra.demo.identity import AuthContext, User


def _context(actor_id: str = "human_1") -> AuthContext:
    return AuthContext(
        user=User(id=actor_id, email=f"{actor_id}@example.test", name="Avery", password_hash="unused"),
        tenant_id="tenant_1", role="member", auth_via="test",
    )


@pytest.fixture()
def event_repository(monkeypatch, tmp_path: Path):
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(
        repository,
        conversation_clock=lambda: "2026-01-02T09:00:00+00:00",
        conversation_id_factory=(lambda sequence=iter(range(1, 1000)): lambda prefix: f"{prefix}_{next(sequence)}")(),
    )
    service.create_room(tenant_id="tenant_1", project_id="project_1", creator_id="human_1")
    monkeypatch.setattr(workplace_event_routes, "_collaboration_root", tmp_path / "rooms")
    return repository, service


def _add_event_member(
    repository: JsonCollaborationRepository, project_id: str, actor_id: str, *, complete: bool,
) -> None:
    transaction_id = f"invite_accept_{project_id}_{actor_id}"
    room = repository.get_room("tenant_1", project_id)
    repository.save_room(replace(
        room,
        members=[*room.members, Member(
            actor_id=actor_id, role="member", display_name=f"{actor_id} pending name",
            transaction_id=transaction_id,
            visibility_state="committed" if complete else "pending_commit",
        )], revision=room.revision + 1,
    ), room.revision)
    if complete:
        journal = repository.root / ".invitation-acceptance" / "tenant_1" / project_id / f"{transaction_id}.json"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(json.dumps({
            "project_id": project_id, "state": "COMPLETE", "tenant_id": "tenant_1",
            "transaction_id": transaction_id,
        }), encoding="utf-8")


def test_sse_complete_gates_pending_members_at_collection_and_publication(event_repository):
    repository, service = event_repository
    service.create_room(tenant_id="tenant_1", project_id="project_complete", creator_id="owner")
    service.create_room(tenant_id="tenant_1", project_id="project_pending", creator_id="owner")
    _add_event_member(repository, "project_1", "invited_human", complete=False)
    _add_event_member(repository, "project_complete", "invited_human", complete=True)
    _add_event_member(repository, "project_pending", "invited_human", complete=False)
    for project_id in ("project_complete", "project_pending"):
        repository.append_event(make_domain_event(
            tenant_id="tenant_1", project_id=project_id, actor_type=ActorType.HUMAN,
            actor_id="owner", action="task.created", result="succeeded", event_id=f"evt_{project_id}",
        ))

    collected = workplace_event_routes._authorized_wakeups(repository, _context("invited_human"), None)
    assert {event["mission_id"] for event in collected} == {"project_complete"}

    buffered = collected
    room = repository.get_room("tenant_1", "project_complete")
    repository.save_room(replace(
        room,
        members=[replace(member, visibility_state="pending_commit") if member.actor_id == "invited_human" else member for member in room.members],
        revision=room.revision + 1,
    ), room.revision)

    async def collect():
        return [item async for item in workplace_event_routes._stream_workspace_events(
            repository, _context("invited_human"), last_event_id=None, initial_events=buffered,
            max_cycles=1, poll_seconds=0,
        )]

    assert asyncio.run(collect()) == []


def test_sse_visibility_precheck_never_holds_room_lock(event_repository, monkeypatch):
    repository, _service = event_repository
    entered, release = threading.Event(), threading.Event()
    original = JsonCollaborationRepository.visible_member
    blocked = False

    def gated(self, room, actor_id):
        nonlocal blocked
        if actor_id == "human_1" and not blocked:
            blocked = True
            entered.set()
            assert release.wait(timeout=5)
        return original(self, room, actor_id)

    monkeypatch.setattr(JsonCollaborationRepository, "visible_member", gated)
    thread_result: list[object] = []

    def authorize():
        thread_result.append(workplace_event_routes._authorized_wakeups(repository, _context(), None))

    thread = threading.Thread(target=authorize)
    thread.start()
    assert entered.wait(timeout=5)
    with repository.room_lock("tenant_1", "project_1") as room:
        assert room.project_id == "project_1"
    release.set()
    thread.join(timeout=5)
    assert not thread.is_alive() and thread_result


def test_sse_resume_is_wakeup_only(event_repository):
    repository, service = event_repository
    message = service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="message_1", body="A durable message",
    )
    service.put_conversation_reaction(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=message.id, reaction="check", client_request_id="reaction_1",
    )
    service.put_saved_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=message.id, client_request_id="save_1",
    )
    all_events = workplace_event_routes._authorized_wakeups(repository, _context(), last_event_id=None)
    resumed = workplace_event_routes._authorized_wakeups(repository, _context(), last_event_id=all_events[0]["id"])

    assert resumed == all_events[1:]
    assert all(set(item) == {"id", "type", "mission_id", "occurred_at"} for item in all_events)
    rendered = workplace_event_routes._encode_event(all_events[-1])
    assert "A durable message" not in rendered
    assert "human_1" not in rendered
    assert "provider" not in rendered and "runtime" not in rendered and "path" not in rendered


def test_sse_filters_inaccessible_events_and_rechecks_membership(event_repository):
    repository, service = event_repository
    message = service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="message_1", body="Hidden after removal",
    )
    events = workplace_event_routes._authorized_wakeups(repository, _context(), last_event_id=None)
    assert events
    room = repository.get_room("tenant_1", "project_1")
    room.members = []
    room.revision += 1
    repository.save_room(room, room.revision - 1)
    assert workplace_event_routes._event_is_publishable(repository, _context(), events[0]) is False
    with pytest.raises(HTTPException) as denied:
        workplace_event_routes.get_workspace_events(last_event_id=None, ctx=_context())
    assert denied.value.status_code == 403
    assert message.id


def test_sse_domain_payload_is_never_public(event_repository):
    repository, _ = event_repository
    repository.append_event(make_domain_event(
        tenant_id="tenant_1", project_id="project_1", actor_type=ActorType.HUMAN,
        actor_id="human_1", action="task.created", result="succeeded",
        payload={"body": "secret body", "runtime": "hidden", "path": "/private"},
        event_id="evt_domain_1",
    ))
    event = next(
        item for item in workplace_event_routes._authorized_wakeups(repository, _context(), last_event_id=None)
        if item["type"] == "work.changed"
    )
    assert event["type"] == "work.changed"
    assert event["mission_id"] == "project_1"
    assert event["occurred_at"] == next(
        item.timestamp for item in repository.list_events("tenant_1", "project_1") if item.id == "evt_domain_1"
    )
    assert event["id"].startswith("wke_") and event["id"] != "evt_domain_1"


def test_sse_invalid_and_unknown_resume_are_safe(event_repository):
    repository, service = event_repository
    service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="message_1", body="A durable message",
    )
    with pytest.raises(HTTPException) as malformed:
        workplace_event_routes._authorized_wakeups(repository, _context(), last_event_id="not valid")
    assert malformed.value.status_code == 400
    assert malformed.value.detail == {
        "code": "event_cursor_invalid",
        "message": workplace_event_routes._INVALID_RESUME_MESSAGE,
    }
    unknown = workplace_event_routes._cursor_encode(
        tenant_id="tenant_1", order=("2020-01-01T00:00:00+00:00", "project_missing", "domain:evt_missing"),
    )
    reset = workplace_event_routes._authorized_wakeups(repository, _context(), last_event_id=unknown)
    assert reset[0]["type"] == "workspace.reset"
    assert reset[0]["mission_id"] == ""
    assert set(reset[0]) == {"id", "type", "mission_id", "occurred_at"}


def test_sse_auth_dependency_is_header_only():
    parameters = inspect.signature(workplace_event_routes._get_sse_auth).parameters
    assert set(parameters) == {"authorization", "x_tenant_id"}
    with pytest.raises(HTTPException) as unauthenticated:
        workplace_event_routes._get_sse_auth(authorization=None, x_tenant_id=None)
    assert unauthenticated.value.status_code == 401


def test_sse_response_contract_and_heartbeat_interval(event_repository):
    _, service = event_repository
    service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="message_1", body="A durable message",
    )
    response = workplace_event_routes.get_workspace_events(last_event_id=None, ctx=_context())
    assert response.media_type == "text/event-stream"
    assert workplace_event_routes.HEARTBEAT_SECONDS == 20
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-accel-buffering"] == "no"


def test_workspace_cursor_disambiguates_same_source_id_across_projects(event_repository):
    repository, service = event_repository
    service.create_room(tenant_id="tenant_1", project_id="project_2", creator_id="human_1")
    for project_id in ("project_1", "project_2"):
        repository.append_event(make_domain_event(
            tenant_id="tenant_1", project_id=project_id, actor_type=ActorType.HUMAN,
            actor_id="human_1", action="task.created", result="succeeded", event_id="evt_same",
        ))
    events = workplace_event_routes._authorized_wakeups(repository, _context(), last_event_id=None)
    same_source = [event for event in events if event["type"] == "work.changed"]
    assert len(same_source) == 2
    assert same_source[0]["id"] != same_source[1]["id"]
    assert workplace_event_routes._authorized_wakeups(
        repository, _context(), last_event_id=same_source[0]["id"],
    ) == [same_source[1]]


def test_buffered_event_is_not_published_after_member_removal(event_repository):
    repository, service = event_repository
    service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="message_1", body="A durable message",
    )
    buffered = workplace_event_routes._authorized_wakeups(repository, _context(), last_event_id=None)
    room = repository.get_room("tenant_1", "project_1")
    room.members = []
    room.revision += 1
    repository.save_room(room, room.revision - 1)

    async def collect():
        return [item async for item in workplace_event_routes._stream_workspace_events(
            repository, _context(), last_event_id=None, initial_events=buffered,
            max_cycles=1, poll_seconds=0,
        )]

    assert asyncio.run(collect()) == []


def test_saved_wakeup_is_recipient_only(event_repository):
    repository, service = event_repository
    room = repository.get_room("tenant_1", "project_1")
    service.add_member(
        tenant_id="tenant_1", project_id="project_1", actor_id="human_1",
        member_id="human_2", role="member", expected_revision=room.revision,
    )
    message = service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="message_1", body="A durable message",
    )
    before_human_1 = workplace_event_routes._authorized_wakeups(repository, _context("human_1"), None)
    before_human_2 = workplace_event_routes._authorized_wakeups(repository, _context("human_2"), None)
    service.put_saved_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        message_id=message.id, client_request_id="private_save",
    )
    after_human_1 = workplace_event_routes._authorized_wakeups(repository, _context("human_1"), None)
    after_human_2 = workplace_event_routes._authorized_wakeups(repository, _context("human_2"), None)
    assert len(after_human_1) == len(before_human_1) + 1
    added = [event for event in after_human_1 if event["id"] not in {item["id"] for item in before_human_1}]
    assert [event["type"] for event in added] == ["saved.changed"]
    assert after_human_2 == before_human_2


def test_workspace_reset_cursor_advances_without_repeating_after_next_poll(event_repository):
    repository, service = event_repository
    service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="message_1", body="Durable",
    )
    unknown = workplace_event_routes._cursor_encode(
        tenant_id="tenant_1", order=("2020-01-01T00:00:00+00:00", "project_missing", "domain:evt_missing"),
    )
    reset = workplace_event_routes._authorized_wakeups(repository, _context(), unknown)
    assert [item["type"] for item in reset] == ["workspace.reset"]
    assert workplace_event_routes._authorized_wakeups(repository, _context(), reset[0]["id"]) == []


def test_sse_publication_membership_check_is_serialized_with_concurrent_removal(event_repository):
    repository, service = event_repository
    service.create_conversation_message(
        tenant_id="tenant_1", project_id="project_1", authenticated_human_actor_id="human_1",
        client_request_id="message_1", body="Durable",
    )
    buffered = workplace_event_routes._authorized_wakeups(repository, _context(), None)
    stream = workplace_event_routes._stream_workspace_events(
        repository, _context(), last_event_id=None, initial_events=buffered,
        max_cycles=1, poll_seconds=0,
    )

    async def first_chunk():
        return await stream.__anext__()

    chunk = asyncio.run(first_chunk())
    assert "event: wakeup" in chunk
    removal_started = threading.Event()
    removal_finished = threading.Event()

    def remove_member():
        removal_started.set()
        room = repository.get_room("tenant_1", "project_1")
        room.members = []
        room.revision += 1
        repository.save_room(room, room.revision - 1)
        removal_finished.set()

    thread = threading.Thread(target=remove_member, daemon=True)
    thread.start()
    assert removal_started.wait(timeout=2)
    assert not removal_finished.wait(timeout=0.1)
    asyncio.run(stream.aclose())
    assert removal_finished.wait(timeout=2)
    thread.join(timeout=2)
