from __future__ import annotations

import hashlib
import json
import multiprocessing
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from simulacra.collaboration import CollaborationService, Invitation, JsonCollaborationRepository
from simulacra.collaboration.invitation_acceptance import InvitationAcceptanceCoordinator, InvitationUnavailable
from simulacra.collaboration.models import Member


def _prepare(monkeypatch, tmp_path: Path, *, requested_role: str = "member"):
    """Build an isolated owner, invitee, room, and single-use invitation."""
    from simulacra.demo import identity, tenants
    data = tmp_path / "data"
    monkeypatch.setenv("SIMULACRA_INVITATION_ACCEPTANCE_ROOT", str(tmp_path / "control"))
    monkeypatch.setattr(identity, "DATA_DIR", data)
    monkeypatch.setattr(identity, "USERS_PATH", data / "users.json")
    monkeypatch.setattr(identity, "MEMBERSHIPS_PATH", data / "memberships.json")
    monkeypatch.setattr(tenants, "TENANTS_PATH", data / "tenants.json")
    tenant = tenants.create_tenant("Invite test")
    owner = identity.create_user("owner@example.test", "password12345")
    recipient = identity.create_user("recipient@example.test", "password12345")
    identity.add_membership(tenant.id, owner.id, "owner")
    repo = JsonCollaborationRepository(tmp_path / "control")
    CollaborationService(repo).create_room(tenant_id=tenant.id, project_id="project_1", creator_id=owner.id)
    token = "x" * 32
    invitation = Invitation(id="invite_1", tenant_id=tenant.id, project_id="project_1", invited_by=owner.id,
        invitee_email="recipient@example.test", requested_role=requested_role, accept_token_digest=hashlib.sha256(token.encode()).hexdigest(),
        status="pending", expires_at=(datetime.now(UTC) + timedelta(hours=1)).isoformat())
    repo.create_invitation(invitation)
    return tenant, owner, recipient, repo, token, invitation


def _accept(coordinator, tenant, recipient, invitation, token, request_id="request_1"):
    return coordinator.accept(tenant_id=tenant.id, project_id="project_1", invitation_id=invitation.id,
        actor_id=recipient.id, verified_email="recipient@example.test", client_request_id=request_id, token=token)


def test_stolen_token_wrong_verified_email_is_unavailable(tmp_path, monkeypatch):
    tenant, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    with pytest.raises(InvitationUnavailable):
        InvitationAcceptanceCoordinator(repo).accept(tenant_id=tenant.id, project_id="project_1", invitation_id=invitation.id,
            actor_id=recipient.id, verified_email="wrong@example.test", client_request_id="request_1", token=token)


def test_cross_tenant_token_use_is_unavailable(tmp_path, monkeypatch):
    _, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    with pytest.raises(InvitationUnavailable):
        InvitationAcceptanceCoordinator(repo).accept(tenant_id="tenant_other", project_id="project_1", invitation_id=invitation.id,
            actor_id=recipient.id, verified_email="recipient@example.test", client_request_id="request_1", token=token)


def test_revoked_or_expired_invitation_is_unavailable(tmp_path, monkeypatch):
    tenant, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    from dataclasses import replace
    revoked = replace(invitation, status="revoked", revision=2)
    repo.save_invitation(revoked, invitation.revision)
    with pytest.raises(InvitationUnavailable): _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, revoked, token)
    expired = replace(revoked, status="pending", revision=3, expires_at=(datetime.now(UTC) - timedelta(seconds=1)).isoformat())
    repo.save_invitation(expired, revoked.revision)
    with pytest.raises(InvitationUnavailable): _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, expired, token)


def test_concurrent_accept_is_single_use_and_enrolls_once(tmp_path, monkeypatch):
    tenant, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    coordinator, results = InvitationAcceptanceCoordinator(repo), []
    def accept(request_id: str):
        try: _accept(coordinator, tenant, recipient, invitation, token, request_id); results.append("accepted")
        except InvitationUnavailable: results.append("unavailable")
    threads = [threading.Thread(target=accept, args=(request_id,)) for request_id in ("one", "two")]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert results.count("accepted") == 1 and results.count("unavailable") == 1
    assert [member.actor_id for member in repo.visible_room(tenant.id, "project_1").members].count(recipient.id) == 1


@pytest.mark.parametrize("mission_role", ["owner", "admin", "reviewer", "approver"])
def test_mission_role_never_escalates_tenant_membership(tmp_path, monkeypatch, mission_role):
    tenant, _, recipient, repo, token, invitation = _prepare(
        monkeypatch, tmp_path, requested_role=mission_role,
    )

    _, member = _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token)

    from simulacra.demo.identity import get_membership
    tenant_membership = get_membership(tenant.id, recipient.id)
    assert tenant_membership is not None
    assert tenant_membership.role == "member"
    assert member.role == mission_role


def test_existing_tenant_authority_is_not_rewritten_by_mission_invitation(tmp_path, monkeypatch):
    tenant, _, recipient, repo, token, invitation = _prepare(
        monkeypatch, tmp_path, requested_role="reviewer",
    )
    from simulacra.demo.identity import add_membership, get_membership
    add_membership(tenant.id, recipient.id, "owner")

    _, member = _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token)

    tenant_membership = get_membership(tenant.id, recipient.id)
    assert tenant_membership is not None
    assert tenant_membership.role == "owner"
    assert tenant_membership.transaction_id is None
    assert member.role == "reviewer"


def test_membership_writer_cannot_change_authority_between_precheck_and_room_write(tmp_path, monkeypatch):
    tenant, _, recipient, repo, token, invitation = _prepare(
        monkeypatch, tmp_path, requested_role="reviewer",
    )
    from simulacra.demo import identity
    identity.add_membership(tenant.id, recipient.id, "member")
    original_membership_lock = identity.membership_store_lock
    writer_attempting = threading.Event()
    writer_finished = threading.Event()
    room_write_started = threading.Event()
    allow_room_write = threading.Event()

    @contextmanager
    def observed_membership_lock(tenant_id, user_id):
        if threading.current_thread().name == "membership-writer":
            writer_attempting.set()
        with original_membership_lock(tenant_id, user_id):
            yield

    original_save_room = repo.save_room
    def held_room_write(room, expected_revision):
        if any(item.actor_id == recipient.id for item in room.members):
            room_write_started.set()
            assert allow_room_write.wait(timeout=5)
        return original_save_room(room, expected_revision)

    monkeypatch.setattr(identity, "membership_store_lock", observed_membership_lock)
    monkeypatch.setattr(repo, "save_room", held_room_write)
    acceptance_result = []
    accept_thread = threading.Thread(
        target=lambda: acceptance_result.append(
            _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token),
        ),
        name="acceptance-writer",
    )
    accept_thread.start()
    assert room_write_started.wait(timeout=5)

    writer_thread = threading.Thread(
        target=lambda: (
            identity.add_membership(tenant.id, recipient.id, "owner"),
            writer_finished.set(),
        ),
        name="membership-writer",
    )
    writer_thread.start()
    assert writer_attempting.wait(timeout=5)
    assert not writer_finished.is_set()

    allow_room_write.set()
    accept_thread.join(timeout=5)
    writer_thread.join(timeout=5)
    assert not accept_thread.is_alive() and not writer_thread.is_alive()
    assert acceptance_result[0][1].role == "reviewer"
    assert writer_finished.is_set()


@pytest.mark.parametrize(
    "boundary",
    [
        "PREPARED",
        "COMMIT_DECIDED",
        "IDENTITY_CHILD_DURABLE",
        "ROOM_CHILD_DURABLE",
        "INVITATION_CHILD_DURABLE",
        "STORES_DURABLE",
        "IDENTITY_CHILD_COMMITTED",
        "ROOM_CHILD_COMMITTED",
        "COMPLETE",
    ],
)
def test_accept_crash_before_and_after_each_identity_room_journal_boundary(tmp_path, monkeypatch, boundary):
    tenant, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    coordinator, tripped = InvitationAcceptanceCoordinator(repo), {"value": False}
    def crash(state: str):
        if state == boundary and not tripped["value"]:
            tripped["value"] = True
            raise RuntimeError("simulated process stop")
    coordinator.fault_injector = crash
    with pytest.raises(RuntimeError): _accept(coordinator, tenant, recipient, invitation, token)
    accepted, member = _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token)
    assert accepted.status == "accepted" and member.actor_id == recipient.id
    assert [item.actor_id for item in repo.visible_room(tenant.id, "project_1").members].count(recipient.id) == 1
    from simulacra.demo.identity import get_membership
    membership = get_membership(tenant.id, recipient.id)
    assert membership is not None
    assert membership.transaction_id == member.transaction_id


def test_concurrent_readers_never_observe_tenant_membership_without_room_membership(tmp_path, monkeypatch):
    tenant, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    coordinator = InvitationAcceptanceCoordinator(repo)
    coordinator.fault_injector = lambda state: (_ for _ in ()).throw(RuntimeError("stop")) if state == "COMMIT_DECIDED" else None
    with pytest.raises(RuntimeError): _accept(coordinator, tenant, recipient, invitation, token)
    from simulacra.demo.identity import get_membership
    assert get_membership(tenant.id, recipient.id) is None
    assert recipient.id not in {member.actor_id for member in repo.visible_room(tenant.id, "project_1").members}
    _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token)
    assert get_membership(tenant.id, recipient.id) is not None
    assert recipient.id in {member.actor_id for member in repo.visible_room(tenant.id, "project_1").members}


def test_pending_transaction_rows_are_hidden_from_identity_and_room_readers(tmp_path, monkeypatch):
    tenant, _, recipient, repo, _, _ = _prepare(monkeypatch, tmp_path)
    from simulacra.demo.identity import add_membership, get_membership
    from dataclasses import replace
    transaction_id = "invite_accept_hidden"
    add_membership(tenant.id, recipient.id, "member", transaction_id=transaction_id, visibility_state="pending_commit")
    room = repo.get_room(tenant.id, "project_1")
    repo.save_room(replace(room, members=[*room.members, Member(recipient.id, "member", transaction_id=transaction_id, visibility_state="pending_commit")], revision=room.revision + 1), room.revision)
    assert get_membership(tenant.id, recipient.id) is None
    assert recipient.id not in {member.actor_id for member in repo.visible_room(tenant.id, "project_1").members}
    assert recipient.id in {member.actor_id for member in repo.get_room(tenant.id, "project_1").members}


def test_complete_transaction_rows_become_visible_after_restart(tmp_path, monkeypatch):
    tenant, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token)
    from simulacra.demo.identity import get_membership
    assert get_membership(tenant.id, recipient.id) is not None
    assert recipient.id in {member.actor_id for member in JsonCollaborationRepository(repo.root).visible_room(tenant.id, "project_1").members}


def test_complete_replay_requires_same_verified_principal_and_token(tmp_path, monkeypatch):
    tenant, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token)
    coordinator = InvitationAcceptanceCoordinator(repo)
    with pytest.raises(InvitationUnavailable):
        coordinator.accept(
            tenant_id=tenant.id,
            project_id="project_1",
            invitation_id=invitation.id,
            actor_id=recipient.id,
            verified_email="other@example.test",
            client_request_id="request_1",
            token=token,
        )
    with pytest.raises(InvitationUnavailable):
        _accept(coordinator, tenant, recipient, invitation, "y" * 32)


@pytest.mark.parametrize("missing_child", ["tenant", "room"])
def test_complete_replay_reverifies_committed_tenant_and_room_children(tmp_path, monkeypatch, missing_child):
    tenant, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token)
    if missing_child == "tenant":
        from simulacra.demo import identity
        raw = json.loads(identity.MEMBERSHIPS_PATH.read_text())
        raw["memberships"] = [
            item for item in raw["memberships"]
            if not (item["tenant_id"] == tenant.id and item["user_id"] == recipient.id)
        ]
        identity.MEMBERSHIPS_PATH.write_text(json.dumps(raw))
    else:
        room_path = repo.root / tenant.id / "project_1" / "collaboration" / "room.json"
        raw = json.loads(room_path.read_text())
        raw["members"] = [
            item for item in raw["members"] if item["actor_id"] != recipient.id
        ]
        room_path.write_text(json.dumps(raw))

    with pytest.raises(InvitationUnavailable):
        _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token)


def test_crash_after_room_commit_before_complete_recovers_same_membership(tmp_path, monkeypatch):
    tenant, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    original_save_room = repo.save_room
    crashed = {"value": False}

    def save_then_stop(room, expected_revision):
        result = original_save_room(room, expected_revision)
        accepted_member = next(
            (item for item in room.members if item.actor_id == recipient.id), None,
        )
        if (
            accepted_member is not None
            and accepted_member.visibility_state == "committed"
            and not crashed["value"]
        ):
            crashed["value"] = True
            raise RuntimeError("simulated process stop after room commit")
        return result

    monkeypatch.setattr(repo, "save_room", save_then_stop)
    with pytest.raises(RuntimeError):
        _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token)
    monkeypatch.setattr(repo, "save_room", original_save_room)

    accepted, member = _accept(
        InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token,
    )
    assert accepted.status == "accepted"
    assert member.actor_id == recipient.id
    assert member.visibility_state == "committed"
    assert [
        item.actor_id for item in repo.visible_room(tenant.id, "project_1").members
    ].count(recipient.id) == 1


def _ordinary_room_mutation(root: str, tenant_id: str) -> None:
    repo = JsonCollaborationRepository(root); service = CollaborationService(repo); room = repo.get_room(tenant_id, "project_1")
    service.add_member(tenant_id=tenant_id, project_id="project_1", actor_id=room.members[0].actor_id,
        member_id="ordinary_member", role="member", expected_revision=room.revision)


def test_concurrent_room_mutation_preserves_hidden_pending_member_rows_while_authorized_read_filters_them(tmp_path, monkeypatch):
    tenant, _, recipient, repo, _, _ = _prepare(monkeypatch, tmp_path)
    from dataclasses import replace
    room = repo.get_room(tenant.id, "project_1")
    pending = Member(recipient.id, "member", transaction_id="invite_accept_pending", visibility_state="pending_commit")
    repo.save_room(replace(room, members=[*room.members, pending], revision=room.revision + 1), room.revision)
    process = multiprocessing.Process(target=_ordinary_room_mutation, args=(str(repo.root), tenant.id))
    process.start(); process.join(timeout=10)
    assert process.exitcode == 0
    raw = json.loads((repo.root / tenant.id / "project_1" / "collaboration" / "room.json").read_text())
    assert any(item["actor_id"] == recipient.id and item["visibility_state"] == "pending_commit" for item in raw["members"])
    assert recipient.id not in {member.actor_id for member in repo.visible_room(tenant.id, "project_1").members}


def test_pg_identity_with_filesystem_room_crash_recovery_is_complete_gated(tmp_path, monkeypatch):
    # The publication gate is shared across database-backed identity and the
    # filesystem room.  The focused test uses a simulated database write seam.
    tenant, _, recipient, repo, token, invitation = _prepare(monkeypatch, tmp_path)
    coordinator = InvitationAcceptanceCoordinator(repo)
    coordinator.fault_injector = lambda state: (_ for _ in ()).throw(RuntimeError("stop")) if state == "STORES_DURABLE" else None
    with pytest.raises(RuntimeError): _accept(coordinator, tenant, recipient, invitation, token)
    assert recipient.id not in {member.actor_id for member in repo.visible_room(tenant.id, "project_1").members}
    _accept(InvitationAcceptanceCoordinator(repo), tenant, recipient, invitation, token)
    assert recipient.id in {member.actor_id for member in repo.visible_room(tenant.id, "project_1").members}
