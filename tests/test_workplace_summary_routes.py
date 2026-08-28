from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.api import workplace_summary_routes
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.demo.identity import AuthContext, User
from simulacra.missions import JsonMissionRepository, MissionService


def _context(actor_id: str) -> AuthContext:
    return AuthContext(
        user=User(id=actor_id, email=f"{actor_id}@example.test", name=actor_id, password_hash="unused"),
        tenant_id="tenant_demo", role="member", auth_via="test",
    )


def test_attention_read_does_not_resolve_source(monkeypatch, tmp_path):
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(repository)
    room = service.create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner")
    service.add_member(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", member_id="reader", role="member", expected_revision=room.revision)
    task = service.create_task(
        tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", title="Review", objective="Review", acceptance_criteria=["Done"], owner_id="reader",
    )
    monkeypatch.setattr(workplace_summary_routes, "_collaboration_root", tmp_path / "rooms")
    monkeypatch.setattr(workplace_summary_routes, "_mission_root", tmp_path / "missions")
    MissionService(JsonMissionRepository(tmp_path / "missions")).bootstrap("tenant_demo", "project_demo", "owner", {"title": "Demo"})
    listed = workplace_summary_routes.workspace_attention(filter="all", cursor=None, limit=50, ctx=_context("reader"))
    item = next(row for row in listed["items"] if row["type"] == "assignment")
    before = repository.get_task("tenant_demo", "project_demo", task.id).to_dict()

    result = workplace_summary_routes.read_attention(
        workplace_summary_routes.AttentionReadBody(event_id=item["id"], expected_revision=0), _context("reader"),
    )
    assert result["item"]["read"] is True and result["item"]["revision"] == 1
    assert repository.get_task("tenant_demo", "project_demo", task.id).to_dict() == before
    with pytest.raises(HTTPException) as stale:
        workplace_summary_routes.read_attention(
            workplace_summary_routes.AttentionReadBody(event_id=item["id"], expected_revision=0), _context("reader"),
        )
    assert stale.value.status_code == 409


def test_summary_routes_bind_cursors_and_keep_receipts_per_human(monkeypatch, tmp_path):
    repository = JsonCollaborationRepository(tmp_path / "rooms")
    service = CollaborationService(repository)
    room = service.create_room(tenant_id="tenant_demo", project_id="project_demo", creator_id="owner")
    for human in ("reader", "other"):
        room = service.add_member(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", member_id=human, role="member", expected_revision=room.revision)
    missions = JsonMissionRepository(tmp_path / "missions")
    MissionService(missions).bootstrap("tenant_demo", "project_demo", "owner", {"title": "Demo"})
    for human in ("reader", "reader", "other"):
        service.create_task(tenant_id="tenant_demo", project_id="project_demo", actor_id="owner", title=f"Review {human}", objective="Review", acceptance_criteria=["Done"], owner_id=human)
    monkeypatch.setattr(workplace_summary_routes, "_collaboration_root", tmp_path / "rooms")
    monkeypatch.setattr(workplace_summary_routes, "_mission_root", tmp_path / "missions")
    mine = workplace_summary_routes.workspace_attention(filter="all", cursor=None, limit=1, ctx=_context("reader"))
    other = workplace_summary_routes.workspace_attention(filter="all", cursor=None, limit=50, ctx=_context("other"))
    mine_item = next(item for item in mine["items"] if item["type"] == "assignment")
    workplace_summary_routes.read_attention(workplace_summary_routes.AttentionReadBody(event_id=mine_item["id"], expected_revision=0), _context("reader"))
    assert next(item for item in workplace_summary_routes.workspace_attention(filter="all", cursor=None, limit=50, ctx=_context("reader"))["items"] if item["id"] == mine_item["id"])["read"] is True
    assert all(not item["read"] for item in other["items"])
    assert mine["next_cursor"] is not None
    with pytest.raises(HTTPException) as wrong_human:
        workplace_summary_routes.workspace_attention(filter="all", cursor=mine["next_cursor"], limit=1, ctx=_context("other"))
    assert wrong_human.value.status_code == 400
    assert wrong_human.value.detail == {"code": "cursor_invalid", "message": workplace_summary_routes._CURSOR_MESSAGE}


def test_summary_route_public_errors_are_fixed(monkeypatch, tmp_path):
    monkeypatch.setattr(workplace_summary_routes, "_collaboration_root", tmp_path / "rooms")
    monkeypatch.setattr(workplace_summary_routes, "_mission_root", tmp_path / "missions")
    with pytest.raises(HTTPException) as invalid:
        workplace_summary_routes.missions(state="internal/path", cursor=None, limit=50, ctx=_context("reader"))
    assert invalid.value.detail == {"code": "cursor_invalid", "message": workplace_summary_routes._CURSOR_MESSAGE}
    assert not any(word in str(invalid.value.detail).lower() for word in ("path", "runtime", "exception", "provider"))
