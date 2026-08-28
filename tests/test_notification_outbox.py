from __future__ import annotations

import fcntl
import json
import multiprocessing
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from simulacra.collaboration import (
    CollaborationService,
    DeterministicNotificationAdapter,
    JsonCollaborationRepository,
    NotificationOutbox,
    make_domain_event,
)
from simulacra.deploy_process import _configured_notification_adapter
from simulacra.workplace.preferences import JsonWorkplacePreferenceRepository


TENANT_ID = "tenant_1"
PROJECT_ID = "project_1"


class MutableClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    def __call__(self) -> str:
        return self.value.isoformat()

    def advance(self, seconds: float) -> None:
        self.value += timedelta(seconds=seconds)


class RecordingAdapter:
    def __init__(self, *, receipt: dict[str, str] | None = None, fail: bool = False) -> None:
        self.receipt = receipt
        self.fail = fail
        self.delivery_ids: list[str] = []

    def deliver(self, *, delivery_id: str, recipient_id: str, channel: str, payload: dict) -> dict | None:
        self.delivery_ids.append(delivery_id)
        if self.fail:
            raise RuntimeError("provider unavailable")
        return self.receipt


def _seed_actionable_mission(root: Path, *, include_member: bool = False) -> tuple[JsonCollaborationRepository, JsonWorkplacePreferenceRepository]:
    repo = JsonCollaborationRepository(root / "collaboration")
    service = CollaborationService(repo)
    service.create_room(tenant_id=TENANT_ID, project_id=PROJECT_ID, creator_id="owner")
    if include_member:
        service.add_member(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_id="owner",
            member_id="member",
            role="member",
            expected_revision=1,
        )
    service.add_comment(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        author_id="owner",
        body="Please review",
        target_type="project",
        mentions=["@member" if include_member else "@owner"],
    )
    prefs = JsonWorkplacePreferenceRepository(root / "prefs")
    for actor_id in (["owner", "member"] if include_member else ["owner"]):
        prefs.put_notification(
            TENANT_ID,
            actor_id,
            expected_revision=0,
            event_selection="all_actionable",
            channels=["email"],
            digest="off",
            muted_mission_ids=[],
        )
    return repo, prefs


def _hold_delivery_claim(
    repository_root: str,
    preferences_root: str,
    outbox_root: str,
    ready,
    release,
    finished,
) -> None:
    outbox = NotificationOutbox(outbox_root)

    def hold(stage: str) -> None:
        if stage == "before_delivery_claim_replace":
            ready.set()
            if not release.wait(10):
                raise RuntimeError("test release signal not received")

    outbox.fault_injector = hold
    outbox.deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=DeterministicNotificationAdapter(),
        repository=JsonCollaborationRepository(repository_root),
        preferences=JsonWorkplacePreferenceRepository(preferences_root),
    )
    finished.set()


def _project_in_process(repository_root: str, preferences_root: str, outbox_root: str, started, finished) -> None:
    started.set()
    NotificationOutbox(outbox_root).project(
        JsonCollaborationRepository(repository_root),
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        preferences=JsonWorkplacePreferenceRepository(preferences_root),
    )
    finished.set()


class SignallingAdapter:
    def __init__(self, called, returned, release=None) -> None:
        self.called = called
        self.returned = returned
        self.release = release

    def deliver(self, *, delivery_id: str, recipient_id: str, channel: str, payload: dict) -> dict:
        self.called.set()
        if self.release is not None and not self.release.wait(10):
            raise RuntimeError("provider release signal not received")
        self.returned.set()
        return {"provider_delivery_id": delivery_id}


def _deliver_with_signals(
    repository_root: str,
    preferences_root: str,
    outbox_root: str,
    provider_called,
    provider_returned,
    provider_release,
    result,
) -> None:
    delivered = NotificationOutbox(outbox_root).deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=SignallingAdapter(provider_called, provider_returned, provider_release),
        repository=JsonCollaborationRepository(repository_root),
        preferences=JsonWorkplacePreferenceRepository(preferences_root),
    )
    result.put(delivered)


def _remove_member_with_order_proof(
    repository_root: str,
    started,
    committed,
    provider_returned,
    result,
) -> None:
    repository = JsonCollaborationRepository(repository_root)
    started.set()
    room = repository.visible_room(TENANT_ID, PROJECT_ID)
    CollaborationService(repository).remove_member(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="owner",
        member_id="member",
        client_request_id="linearized_member_removal",
        expected_room_revision=room.revision,
    )
    result.put(provider_returned.is_set())
    committed.set()


def test_projector_repairs_event_before_outbox_and_outbox_before_cursor(tmp_path):
    repo, prefs = _seed_actionable_mission(tmp_path)
    outbox = NotificationOutbox(tmp_path / "outbox")

    assert outbox.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 1
    first = outbox.state(TENANT_ID, PROJECT_ID)
    delivery_id = first["outbox"][0]["id"]

    state_path = tmp_path / "outbox" / TENANT_ID / PROJECT_ID / "notification_outbox.json"
    state_path.write_text(json.dumps({**first, "cursor": 0}), encoding="utf-8")
    restarted = NotificationOutbox(tmp_path / "outbox")
    assert restarted.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 0
    repaired = restarted.state(TENANT_ID, PROJECT_ID)
    assert repaired["cursor"] == len(repo.list_events(TENANT_ID, PROJECT_ID))
    assert [row["id"] for row in repaired["outbox"]] == [delivery_id]


def test_two_process_projector_and_delivery_locks(tmp_path):
    repo, prefs = _seed_actionable_mission(tmp_path)
    outbox_root = tmp_path / "outbox"
    outbox = NotificationOutbox(outbox_root)
    assert outbox.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 1

    repo.append_event(
        make_domain_event(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_type="human",
            actor_id="owner",
            action="decision.requested",
            result="pending",
            payload={"category": "decision"},
        )
    )

    context = multiprocessing.get_context("spawn")
    claim_ready = context.Event()
    release_claim = context.Event()
    delivery_finished = context.Event()
    projection_started = context.Event()
    projection_finished = context.Event()
    delivery = context.Process(
        target=_hold_delivery_claim,
        args=(
            str(tmp_path / "collaboration"),
            str(tmp_path / "prefs"),
            str(outbox_root),
            claim_ready,
            release_claim,
            delivery_finished,
        ),
    )
    delivery.start()
    assert claim_ready.wait(10)

    projection = context.Process(
        target=_project_in_process,
        args=(str(tmp_path / "collaboration"), str(tmp_path / "prefs"), str(outbox_root), projection_started, projection_finished),
    )
    projection.start()
    assert projection_started.wait(10)

    lock_path = outbox_root / TENANT_ID / PROJECT_ID / ".state.lock"
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        with pytest.raises(BlockingIOError):
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(descriptor)

    release_claim.set()
    delivery.join(10)
    projection.join(10)
    assert delivery.exitcode == 0
    assert projection.exitcode == 0
    assert delivery_finished.is_set()
    assert projection_finished.is_set()

    state = outbox.state(TENANT_ID, PROJECT_ID)
    assert state["cursor"] == len(repo.list_events(TENANT_ID, PROJECT_ID))
    assert len(state["outbox"]) == 2
    assert len({row["dedupe_key"] for row in state["outbox"]}) == 2


def test_member_removal_and_provider_handoff_are_linearized_in_both_orderings(tmp_path):
    context = multiprocessing.get_context("spawn")

    # Ordering A: delivery owns the room boundary first. Provider handoff returns
    # before removal can commit.
    first_root = tmp_path / "delivery_first"
    first_repo, first_prefs = _seed_actionable_mission(first_root, include_member=True)
    first_outbox_root = first_root / "outbox"
    first_outbox = NotificationOutbox(first_outbox_root)
    assert first_outbox.project(
        first_repo,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        preferences=first_prefs,
    ) == 1
    provider_called = context.Event()
    provider_returned = context.Event()
    provider_release = context.Event()
    delivery_result = context.Queue()
    delivery = context.Process(
        target=_deliver_with_signals,
        args=(
            str(first_root / "collaboration"),
            str(first_root / "prefs"),
            str(first_outbox_root),
            provider_called,
            provider_returned,
            provider_release,
            delivery_result,
        ),
    )
    delivery.start()
    assert provider_called.wait(10)

    room_lock_path = first_root / "collaboration" / ".collaboration-locks" / TENANT_ID / f"{PROJECT_ID}.lock"
    descriptor = os.open(room_lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        room_boundary_owned = False
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            room_boundary_owned = True
        else:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)

    removal_started = context.Event()
    removal_committed = context.Event()
    removal_result = context.Queue()
    remover = context.Process(
        target=_remove_member_with_order_proof,
        args=(
            str(first_root / "collaboration"),
            removal_started,
            removal_committed,
            provider_returned,
            removal_result,
        ),
    )
    remover.start()
    assert removal_started.wait(10)
    provider_release.set()
    delivery.join(10)
    remover.join(10)
    assert room_boundary_owned
    assert delivery.exitcode == 0 and delivery_result.get(timeout=2) == 1
    assert remover.exitcode == 0 and removal_committed.is_set()
    assert removal_result.get(timeout=2) is True

    # Ordering B: removal commits first. The later delivery suppresses the row
    # and never reaches the provider.
    second_root = tmp_path / "removal_first"
    second_repo, second_prefs = _seed_actionable_mission(second_root, include_member=True)
    second_outbox_root = second_root / "outbox"
    second_outbox = NotificationOutbox(second_outbox_root)
    assert second_outbox.project(
        second_repo,
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        preferences=second_prefs,
    ) == 1
    removal_started = context.Event()
    removal_committed = context.Event()
    removal_result = context.Queue()
    already_returned = context.Event()
    already_returned.set()
    remover = context.Process(
        target=_remove_member_with_order_proof,
        args=(
            str(second_root / "collaboration"),
            removal_started,
            removal_committed,
            already_returned,
            removal_result,
        ),
    )
    remover.start()
    assert removal_started.wait(10) and removal_committed.wait(10)
    remover.join(10)
    assert remover.exitcode == 0

    provider_called = context.Event()
    provider_returned = context.Event()
    delivery_result = context.Queue()
    delivery = context.Process(
        target=_deliver_with_signals,
        args=(
            str(second_root / "collaboration"),
            str(second_root / "prefs"),
            str(second_outbox_root),
            provider_called,
            provider_returned,
            None,
            delivery_result,
        ),
    )
    delivery.start()
    delivery.join(10)
    assert delivery.exitcode == 0 and delivery_result.get(timeout=2) == 0
    assert not provider_called.is_set()
    suppressed = second_outbox.state(TENANT_ID, PROJECT_ID)["outbox"][0]
    assert suppressed["status"] == "dead_letter"
    assert suppressed["failure_code"] == "recipient_no_longer_authorized"


def test_notification_dead_letter_after_lease_retries(tmp_path):
    repo, prefs = _seed_actionable_mission(tmp_path)
    clock = MutableClock()
    outbox = NotificationOutbox(
        tmp_path / "outbox",
        max_attempts=3,
        retry_base_seconds=2,
        retry_max_seconds=5,
        clock=clock,
    )
    assert outbox.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 1
    adapter = RecordingAdapter(fail=True)

    assert outbox.deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=adapter,
        repository=repo,
        preferences=prefs,
    ) == 0
    first = outbox.state(TENANT_ID, PROJECT_ID)["outbox"][0]
    assert first["status"] == "pending"
    assert first["attempt_count"] == 1
    assert first["next_attempt_at"] == (clock.value + timedelta(seconds=2)).isoformat()
    assert outbox.deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=adapter,
        repository=repo,
        preferences=prefs,
    ) == 0
    assert len(adapter.delivery_ids) == 1

    clock.advance(2)
    assert outbox.deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=adapter,
        repository=repo,
        preferences=prefs,
    ) == 0
    second = outbox.state(TENANT_ID, PROJECT_ID)["outbox"][0]
    assert second["next_attempt_at"] == (clock.value + timedelta(seconds=4)).isoformat()
    clock.advance(4)
    assert outbox.deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=adapter,
        repository=repo,
        preferences=prefs,
    ) == 0
    terminal = outbox.state(TENANT_ID, PROJECT_ID)["outbox"][0]
    assert terminal["status"] == "dead_letter"
    assert terminal["attempt_count"] == 3
    assert terminal["next_attempt_at"] is None


def test_mute_suppresses_delivery_while_attention_remains_actionable(tmp_path):
    repo, prefs = _seed_actionable_mission(tmp_path)
    prefs.put_notification(
        TENANT_ID,
        "owner",
        expected_revision=1,
        event_selection="all_actionable",
        channels=["email"],
        digest="off",
        muted_mission_ids=[PROJECT_ID],
    )
    outbox = NotificationOutbox(tmp_path / "outbox")
    source_events = repo.list_events(TENANT_ID, PROJECT_ID)
    actionable_event_ids = [event.id for event in source_events if event.payload.get("category") == "mentions"]

    assert outbox.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 0
    assert outbox.state(TENANT_ID, PROJECT_ID)["outbox"] == []
    assert actionable_event_ids
    assert actionable_event_ids == [
        event.id for event in repo.list_events(TENANT_ID, PROJECT_ID) if event.payload.get("category") == "mentions"
    ]


def test_event_selection_projector_filter_matrix_and_orthogonal_mission_mute(tmp_path):
    repo, prefs = _seed_actionable_mission(tmp_path, include_member=True)
    repo.append_event(
        make_domain_event(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_type="human",
            actor_id="owner",
            action="decision.requested",
            result="pending",
            payload={"category": "decision"},
        )
    )
    repo.append_event(
        make_domain_event(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_type="human",
            actor_id="owner",
            action="task.assigned",
            result="pending",
            payload={"category": "assigned", "assignee_id": "member"},
        )
    )
    prefs.put_notification(
        TENANT_ID,
        "owner",
        expected_revision=1,
        event_selection="off",
        channels=["email"],
        digest="off",
        muted_mission_ids=[],
    )
    prefs.put_notification(
        TENANT_ID,
        "member",
        expected_revision=1,
        event_selection="mentions_and_decisions",
        channels=["email"],
        digest="off",
        muted_mission_ids=[],
    )
    filtered = NotificationOutbox(tmp_path / "filtered")
    assert filtered.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 1
    assert {row["payload"]["action"] for row in filtered.state(TENANT_ID, PROJECT_ID)["outbox"]} == {
        "comment.created",
    }

    prefs.put_notification(
        TENANT_ID,
        "member",
        expected_revision=2,
        event_selection="all_actionable",
        channels=["email"],
        digest="off",
        muted_mission_ids=[],
    )
    all_actionable = NotificationOutbox(tmp_path / "all")
    assert all_actionable.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 2

    prefs.put_notification(
        TENANT_ID,
        "member",
        expected_revision=3,
        event_selection="all_actionable",
        channels=["email"],
        digest="off",
        muted_mission_ids=[PROJECT_ID],
    )
    muted = NotificationOutbox(tmp_path / "muted")
    assert muted.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 0
    assert muted.state(TENANT_ID, PROJECT_ID)["outbox"] == []
    assert len(repo.list_events(TENANT_ID, PROJECT_ID)) >= 3


def test_provider_idempotency_key_and_crash_redelivery_contract(tmp_path):
    repo, prefs = _seed_actionable_mission(tmp_path)
    clock = MutableClock()
    outbox = NotificationOutbox(tmp_path / "outbox", clock=clock)
    assert outbox.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 1
    adapter = RecordingAdapter(receipt={"provider_delivery_id": "accepted"})

    def crash_after_handoff(stage: str) -> None:
        if stage == "after_provider_before_delivered":
            raise RuntimeError("worker stopped after provider handoff")

    outbox.fault_injector = crash_after_handoff
    with pytest.raises(RuntimeError, match="worker stopped"):
        outbox.deliver(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            adapter=adapter,
            repository=repo,
            preferences=prefs,
            lease_seconds=10,
        )
    leased = outbox.state(TENANT_ID, PROJECT_ID)["outbox"][0]
    assert leased["status"] == "leased"
    delivery_id = leased["id"]

    clock.advance(11)
    outbox.fault_injector = None
    assert outbox.deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=adapter,
        repository=repo,
        preferences=prefs,
        lease_seconds=10,
    ) == 1
    assert adapter.delivery_ids == [delivery_id, delivery_id]
    delivered = outbox.state(TENANT_ID, PROJECT_ID)["outbox"][0]
    assert delivered["status"] == "delivered"
    assert delivered["provider_receipt"] == {"provider_delivery_id": "accepted"}


def test_provider_must_return_acceptance_receipt_before_delivered(tmp_path):
    repo, prefs = _seed_actionable_mission(tmp_path)
    outbox = NotificationOutbox(tmp_path / "outbox")
    assert outbox.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 1

    assert outbox.deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=RecordingAdapter(receipt=None),
        repository=repo,
        preferences=prefs,
    ) == 0
    row = outbox.state(TENANT_ID, PROJECT_ID)["outbox"][0]
    assert row["status"] == "pending"
    assert row["provider_receipt"] is None


def test_post_projection_member_revocation_suppresses_external_delivery(tmp_path):
    repo, prefs = _seed_actionable_mission(tmp_path, include_member=True)
    outbox = NotificationOutbox(tmp_path / "outbox")
    assert outbox.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 1
    service = CollaborationService(repo)
    service.remove_member(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        actor_id="owner",
        member_id="member",
        client_request_id="remove_member_notification_test",
        expected_room_revision=2,
    )
    adapter = RecordingAdapter(receipt={"provider_delivery_id": "must_not_send"})

    assert outbox.deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=adapter,
        repository=repo,
        preferences=prefs,
    ) == 0
    assert adapter.delivery_ids == []
    row = outbox.state(TENANT_ID, PROJECT_ID)["outbox"][0]
    assert row["status"] == "dead_letter"
    assert row["failure_code"] == "recipient_no_longer_authorized"
    assert any(event.payload.get("category") == "mentions" for event in repo.list_events(TENANT_ID, PROJECT_ID))


@pytest.mark.parametrize(
    ("preference_update", "failure_code"),
    [
        ({"event_selection": "off", "channels": ["email"], "muted_mission_ids": []}, "delivery_disabled"),
        ({"event_selection": "all_actionable", "channels": ["email"], "muted_mission_ids": [PROJECT_ID]}, "mission_muted"),
        ({"event_selection": "all_actionable", "channels": [], "muted_mission_ids": []}, "channel_disabled"),
    ],
    ids=["event-selection-off", "mission-muted", "channel-removed"],
)
def test_post_projection_preference_change_suppresses_external_delivery(
    tmp_path,
    preference_update,
    failure_code,
):
    repo, prefs = _seed_actionable_mission(tmp_path)
    outbox = NotificationOutbox(tmp_path / "outbox")
    assert outbox.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 1
    prefs.put_notification(
        TENANT_ID,
        "owner",
        expected_revision=1,
        digest="off",
        **preference_update,
    )
    adapter = RecordingAdapter(receipt={"provider_delivery_id": "must_not_send"})

    assert outbox.deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=adapter,
        repository=repo,
        preferences=prefs,
    ) == 0
    assert adapter.delivery_ids == []
    row = outbox.state(TENANT_ID, PROJECT_ID)["outbox"][0]
    assert row["status"] == "dead_letter"
    assert row["failure_code"] == failure_code
    assert any(event.payload.get("category") == "mentions" for event in repo.list_events(TENANT_ID, PROJECT_ID))


@pytest.mark.parametrize("unavailable_dependency", ["room", "preferences"])
def test_delivery_authorization_lookup_failure_retries_without_provider_handoff(
    tmp_path,
    unavailable_dependency,
):
    repo, prefs = _seed_actionable_mission(tmp_path)
    outbox = NotificationOutbox(tmp_path / "outbox", retry_base_seconds=2)
    assert outbox.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 1

    class UnavailableRoom:
        def visible_room(self, tenant_id, project_id):
            raise OSError("room unavailable")

    class UnavailablePreferences:
        def get(self, tenant_id, human_id):
            raise OSError("preferences unavailable")

    adapter = RecordingAdapter(receipt={"provider_delivery_id": "must_not_send"})
    assert outbox.deliver(
        tenant_id=TENANT_ID,
        project_id=PROJECT_ID,
        adapter=adapter,
        repository=UnavailableRoom() if unavailable_dependency == "room" else repo,
        preferences=UnavailablePreferences() if unavailable_dependency == "preferences" else prefs,
    ) == 0
    assert adapter.delivery_ids == []
    row = outbox.state(TENANT_ID, PROJECT_ID)["outbox"][0]
    assert row["status"] == "pending"
    assert row["failure_code"] == "delivery_authorization_unavailable"
    assert row["next_attempt_at"] is not None


def test_multi_human_typed_recipient_routing(tmp_path):
    repo = JsonCollaborationRepository(tmp_path / "collaboration")
    service = CollaborationService(repo)
    service.create_room(tenant_id=TENANT_ID, project_id=PROJECT_ID, creator_id="owner")
    revision = 1
    for actor_id, role in (("admin", "admin"), ("member", "member"), ("verifier", "reviewer")):
        service.add_member(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_id="owner",
            member_id=actor_id,
            role=role,
            expected_revision=revision,
        )
        revision += 1
    prefs = JsonWorkplacePreferenceRepository(tmp_path / "prefs")
    for actor_id in ("owner", "admin", "member", "verifier"):
        prefs.put_notification(
            TENANT_ID,
            actor_id,
            expected_revision=0,
            event_selection="all_actionable",
            channels=["email"],
            digest="off",
            muted_mission_ids=[],
        )

    events = [
        make_domain_event(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_type="human",
            actor_id="owner",
            action="comment.created",
            result="succeeded",
            payload={"category": "mentions", "mention_ids": ["member"]},
        ),
        make_domain_event(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_type="human",
            actor_id="owner",
            action="task.assigned",
            result="pending",
            payload={"category": "assigned", "assignee_id": "verifier"},
        ),
        make_domain_event(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_type="system",
            actor_id="system",
            action="task.unassigned",
            result="pending",
            payload={"category": "attention", "attention_type": "unassigned_work"},
        ),
        make_domain_event(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_type="system",
            actor_id="system",
            action="approval.required",
            result="pending",
            payload={"category": "decision", "attention_type": "decision_required"},
        ),
        make_domain_event(
            tenant_id=TENANT_ID,
            project_id=PROJECT_ID,
            actor_type="system",
            actor_id="system",
            action="output.verification_required",
            result="pending",
            payload={
                "category": "attention",
                "attention_type": "output_verification",
                "mission_owner_id": "owner",
                "verifier_ids": ["verifier"],
            },
        ),
    ]
    for event in events:
        repo.append_event(event)

    outbox = NotificationOutbox(tmp_path / "outbox")
    assert outbox.project(repo, tenant_id=TENANT_ID, project_id=PROJECT_ID, preferences=prefs) == 8
    routed = {
        (row["payload"]["action"], row["recipient_id"])
        for row in outbox.state(TENANT_ID, PROJECT_ID)["outbox"]
    }
    assert routed == {
        ("comment.created", "member"),
        ("task.assigned", "verifier"),
        ("task.unassigned", "owner"),
        ("task.unassigned", "admin"),
        ("approval.required", "owner"),
        ("approval.required", "admin"),
        ("output.verification_required", "owner"),
        ("output.verification_required", "verifier"),
    }


def test_deploy_without_configured_provider_never_uses_deterministic_adapter(monkeypatch):
    monkeypatch.delenv("SIMULACRA_NOTIFICATION_ADAPTER_FACTORY", raising=False)
    assert _configured_notification_adapter() is None

    monkeypatch.setenv(
        "SIMULACRA_NOTIFICATION_ADAPTER_FACTORY",
        "simulacra.collaboration.notifications:DeterministicNotificationAdapter",
    )
    assert _configured_notification_adapter() is None
