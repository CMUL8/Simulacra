from __future__ import annotations

import json
import multiprocessing
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from apps.api import preference_routes
from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.collaboration.models import Member
from simulacra.demo.identity import AuthContext, User
from simulacra.workplace.preferences import JsonWorkplacePreferenceRepository, PreferenceValidationError, RevisionConflict


def _ctx(human_id: str = "human_1") -> AuthContext:
    return AuthContext(User(human_id, f"{human_id}@example.test", human_id, "unused"), "tenant_1", "member", "test")


def _concurrent_preference_put(root: str, ready, start, results) -> None:
    repository = JsonWorkplacePreferenceRepository(root)
    ready.put(True)
    start.wait(10)
    try:
        value = repository.put_work_view(
            "tenant_1", "human_1", expected_revision=0, scope="workspace", view="list", filters={},
        )
        results.put(("ok", value["revision"]))
    except RevisionConflict:
        results.put(("conflict", None))


def _add_preference_member(
    repository: JsonCollaborationRepository, project_id: str, actor_id: str, *, complete: bool,
) -> None:
    transaction_id = f"invite_accept_{project_id}_{actor_id}"
    room = repository.get_room("tenant_1", project_id)
    repository.save_room(replace(
        room,
        members=[*room.members, Member(
            actor_id=actor_id, role="member", transaction_id=transaction_id,
            visibility_state="committed" if complete else "pending_commit",
        )],
        revision=room.revision + 1,
    ), room.revision)
    if complete:
        journal = repository.root / ".invitation-acceptance" / "tenant_1" / project_id / f"{transaction_id}.json"
        journal.parent.mkdir(parents=True, exist_ok=True)
        journal.write_text(
            f'{{"project_id":"{project_id}","state":"COMPLETE","tenant_id":"tenant_1",'
            f'"transaction_id":"{transaction_id}"}}', encoding="utf-8",
        )


def test_notification_mutes_require_complete_visible_membership(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(preference_routes, "_preferences_root", tmp_path / "preferences")
    monkeypatch.setattr(preference_routes, "_collaboration_root", tmp_path / "rooms")
    repository = JsonCollaborationRepository(preference_routes._collaboration_root)
    service = CollaborationService(repository)
    for project_id in ("legacy_mission", "complete_mission", "pending_mission"):
        service.create_room(tenant_id="tenant_1", project_id=project_id, creator_id="owner")
    _add_preference_member(repository, "legacy_mission", "human_1", complete=False)
    legacy = repository.get_room("tenant_1", "legacy_mission")
    repository.save_room(replace(
        legacy,
        members=[replace(member, transaction_id=None, visibility_state="committed") if member.actor_id == "human_1" else member for member in legacy.members],
        revision=legacy.revision + 1,
    ), legacy.revision)
    _add_preference_member(repository, "complete_mission", "human_1", complete=True)
    _add_preference_member(repository, "pending_mission", "human_1", complete=False)

    accepted = preference_routes.put_notification_preference(
        preference_routes.NotificationPreferenceBody(
            expected_revision=0, event_selection="all_actionable", channels=["browser"], digest="off",
            muted_mission_ids=["legacy_mission", "complete_mission"],
        ),
        ctx=_ctx(),
    )
    assert set(accepted["notification_preference"]["muted_mission_ids"]) == {"legacy_mission", "complete_mission"}

    with pytest.raises(HTTPException) as denied:
        preference_routes.put_notification_preference(
            preference_routes.NotificationPreferenceBody(
                expected_revision=1, event_selection="all_actionable", channels=["browser"], digest="off",
                muted_mission_ids=["pending_mission"],
            ),
            ctx=_ctx(),
        )
    assert denied.value.status_code == 400


def test_preference_visibility_precheck_never_holds_room_lock(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(preference_routes, "_preferences_root", tmp_path / "preferences")
    monkeypatch.setattr(preference_routes, "_collaboration_root", tmp_path / "rooms")
    repository = JsonCollaborationRepository(preference_routes._collaboration_root)
    CollaborationService(repository).create_room(
        tenant_id="tenant_1", project_id="mission_1", creator_id="human_1",
    )
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
    body = preference_routes.NotificationPreferenceBody(
        expected_revision=0, event_selection="all_actionable", channels=["browser"],
        digest="off", muted_mission_ids=["mission_1"],
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        authorization = pool.submit(preference_routes.put_notification_preference, body, _ctx())
        assert entered.wait(timeout=5)
        with repository.room_lock("tenant_1", "mission_1") as room:
            assert room.project_id == "mission_1"
        release.set()
        assert authorization.result(timeout=5)["notification_preference"]["revision"] == 1


def test_work_view_preference_cas_persists_across_service_restart(tmp_path: Path):
    root = tmp_path / "runs"
    first = JsonWorkplacePreferenceRepository(root)
    saved = first.put_work_view(
        "tenant_1", "human_1", expected_revision=0, scope="workspace", view="board",
        filters={"bucket": "in_progress", "mission_id": "mission_1"},
    )
    assert saved["revision"] == 1
    assert JsonWorkplacePreferenceRepository(root).get("tenant_1", "human_1")["work_view_preferences"] == [saved]
    with pytest.raises(RevisionConflict):
        first.put_work_view("tenant_1", "human_1", expected_revision=0, scope="workspace", view="list", filters={})


def test_work_view_rejects_filters_the_backend_does_not_execute(tmp_path: Path):
    repository = JsonWorkplacePreferenceRepository(tmp_path / "runs")
    for unsupported in ("state", "creator_id", "actor_kind", "needs_my_decision", "updated_since"):
        with pytest.raises(Exception, match="work filters are invalid"):
            repository.put_work_view(
                "tenant_1", "human_1", expected_revision=0, scope="workspace", view="list",
                filters={unsupported: "value"},
            )
    for nested in ({"bucket": {"bad": "value"}}, {"bucket": [["bad"]]}, {"mission_id": "x" * 129}):
        with pytest.raises(PreferenceValidationError, match="work filters are invalid"):
            repository.put_work_view(
                "tenant_1", "human_1", expected_revision=0, scope="workspace", view="list", filters=nested,
            )


def test_work_view_rejects_non_executable_filter_value_shapes(tmp_path: Path):
    repository = JsonWorkplacePreferenceRepository(tmp_path / "runs")
    for value in (None, True, False, ["in_progress"], ["mission_1", "mission_2"], {"id": "mission_1"}, ""):
        with pytest.raises(PreferenceValidationError, match="work filters are invalid"):
            repository.put_work_view(
                "tenant_1", "human_1", expected_revision=0, scope="workspace", view="list",
                filters={"mission_id": value},
            )


def test_notification_preference_cas_and_mute_suppresses_delivery_not_attention(tmp_path: Path):
    repository = JsonWorkplacePreferenceRepository(tmp_path / "runs")
    saved = repository.put_notification(
        "tenant_1", "human_1", expected_revision=0, event_selection="mentions_and_decisions",
        channels=["email"], digest="daily", muted_mission_ids=["mission_1"],
    )
    assert saved["revision"] == 1
    assert repository.allows_external(saved, event_type="decision_required", mission_id="mission_1") is False
    assert repository.allows_external(saved, event_type="decision_required", mission_id="mission_2") is True
    assert repository.allows_external(saved, event_type="assignment", mission_id="mission_2") is False
    with pytest.raises(RevisionConflict):
        repository.put_notification(
            "tenant_1", "human_1", expected_revision=0, event_selection="off", channels=[], digest="off", muted_mission_ids=[],
        )


def test_preference_routes_use_current_human_and_reject_nonmember_mute(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(preference_routes, "_preferences_root", tmp_path / "runs")
    monkeypatch.setattr(preference_routes, "_collaboration_root", tmp_path / "rooms")
    service = CollaborationService(JsonCollaborationRepository(preference_routes._collaboration_root))
    service.create_room(tenant_id="tenant_1", project_id="mission_allowed", creator_id="human_1")
    body = {
        "expected_revision": 0, "event_selection": "all_actionable", "channels": ["email"],
        "digest": "daily", "muted_mission_ids": ["mission_allowed"], "human_id": "other_human",
    }
    with pytest.raises(Exception):
        preference_routes.NotificationPreferenceBody.model_validate(body)
    response = preference_routes.put_notification_preference(
        preference_routes.NotificationPreferenceBody.model_validate({key: value for key, value in body.items() if key != "human_id"}),
        ctx=_ctx(),
    )
    assert response["notification_preference"]["muted_mission_ids"] == ["mission_allowed"]
    state = json.loads((tmp_path / "runs" / "tenant_1" / "human_1" / "state.json").read_text())
    assert "other_human" not in str(state)
    denied = preference_routes.NotificationPreferenceBody.model_validate({**{key: value for key, value in body.items() if key != "human_id"}, "expected_revision": 1, "muted_mission_ids": ["mission_denied"]})
    with pytest.raises(HTTPException) as exc:
        preference_routes.put_notification_preference(denied, ctx=_ctx())
    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "preference_invalid"


def test_preference_state_replace_is_crash_atomic(tmp_path: Path):
    repository = JsonWorkplacePreferenceRepository(tmp_path / "runs")
    original = repository.put_work_view("tenant_1", "human_1", expected_revision=0, scope="workspace", view="list", filters={})

    def fail_before_replace(stage: str) -> None:
        if stage == "before_replace":
            raise RuntimeError("simulated crash")

    repository.fault_injector = fail_before_replace
    with pytest.raises(RuntimeError):
        repository.put_work_view("tenant_1", "human_1", expected_revision=1, scope="workspace", view="board", filters={})
    assert JsonWorkplacePreferenceRepository(tmp_path / "runs").get("tenant_1", "human_1")["work_view_preferences"] == [original]


@pytest.mark.parametrize("stage,expects_new", [
    ("before_write", False), ("before_temp_fsync", False), ("after_temp_fsync", False),
    ("before_replace", False), ("after_replace", True), ("before_parent_fsync", True),
    ("after_parent_fsync", True),
])
def test_preference_replace_survives_every_crash_boundary(tmp_path: Path, stage: str, expects_new: bool):
    root = tmp_path / stage
    repository = JsonWorkplacePreferenceRepository(root)
    repository.put_work_view("tenant_1", "human_1", expected_revision=0, scope="workspace", view="list", filters={})
    repository.fault_injector = lambda point: (_ for _ in ()).throw(RuntimeError("crash")) if point == stage else None
    with pytest.raises(RuntimeError, match="crash"):
        repository.put_work_view("tenant_1", "human_1", expected_revision=1, scope="workspace", view="board", filters={})
    saved = JsonWorkplacePreferenceRepository(root).get("tenant_1", "human_1")["work_view_preferences"][0]
    assert saved["view"] == ("board" if expects_new else "list")


def test_preference_storage_rejects_ancestor_and_state_symlinks(tmp_path: Path):
    root = tmp_path / "prefs"
    outside = tmp_path / "outside"
    outside.mkdir()
    root.mkdir()
    os.symlink(outside, root / "tenant_1")
    with pytest.raises(PreferenceValidationError):
        JsonWorkplacePreferenceRepository(root).get("tenant_1", "human_1")

    root = tmp_path / "prefs_leaf"
    repository = JsonWorkplacePreferenceRepository(root)
    repository.put_work_view("tenant_1", "human_1", expected_revision=0, scope="workspace", view="list", filters={})
    state = root / "tenant_1" / "human_1" / "state.json"
    state.unlink()
    os.symlink(outside / "state.json", state)
    with pytest.raises(PreferenceValidationError):
        repository.get("tenant_1", "human_1")


def test_preference_cas_is_single_winner_across_processes(tmp_path: Path):
    # Initialize the private aggregate first; this test isolates concurrent CAS
    # behavior from first-use directory provisioning.
    JsonWorkplacePreferenceRepository(tmp_path / "prefs").put_work_view(
        "tenant_1", "human_1", expected_revision=0, scope="seed", view="list", filters={},
    )
    context = multiprocessing.get_context("spawn")
    ready, results, start = context.Queue(), context.Queue(), context.Event()
    processes = [context.Process(target=_concurrent_preference_put, args=(str(tmp_path / "prefs"), ready, start, results)) for _ in range(2)]
    for process in processes:
        process.start()
    assert ready.get(timeout=10) and ready.get(timeout=10)
    start.set()
    for process in processes:
        process.join(15)
        assert process.exitcode == 0
    assert sorted(results.get(timeout=5)[0] for _ in range(2)) == ["conflict", "ok"]


def test_preference_get_removes_mutes_for_missions_human_no_longer_belongs_to(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(preference_routes, "_preferences_root", tmp_path / "prefs")
    monkeypatch.setattr(preference_routes, "_collaboration_root", tmp_path / "rooms")
    collaboration = JsonCollaborationRepository(preference_routes._collaboration_root)
    service = CollaborationService(collaboration)
    service.create_room(tenant_id="tenant_1", project_id="mission_allowed", creator_id="human_1")
    service.create_room(tenant_id="tenant_1", project_id="mission_stale", creator_id="human_1")
    JsonWorkplacePreferenceRepository(preference_routes._preferences_root).put_notification(
        "tenant_1", "human_1", expected_revision=0, event_selection="all_actionable", channels=["browser"],
        digest="off", muted_mission_ids=["mission_allowed", "mission_stale"],
    )
    stale = collaboration.get_room("tenant_1", "mission_stale")
    collaboration.save_room(replace(stale, members=[], revision=stale.revision + 1), stale.revision)

    response = preference_routes.workspace_preferences(ctx=_ctx())
    assert response["notification_preference"]["muted_mission_ids"] == ["mission_allowed"]
