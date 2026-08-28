"""Recoverable, visibility-gated invitation acceptance.

The identity and room stores intentionally remain separate.  This coordinator
never claims a distributed transaction: it records its decision durably and
uses the journal as the sole publication gate for tagged membership rows.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import secrets
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterator

from .errors import ConflictError, NotFoundError
from .models import Invitation, Member, iso_now, new_id, validate_scope_id
from .repository import JsonCollaborationRepository


class InvitationUnavailable(NotFoundError):
    """Private marker translated to the single anti-enumerating public error."""


_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_LOCK_DEPTH = threading.local()


def _default_root() -> Path:
    configured = os.environ.get("SIMULACRA_INVITATION_ACCEPTANCE_ROOT")
    if configured:
        return Path(configured).resolve()
    from simulacra.demo.paths import RUNS_DIR
    return (RUNS_DIR / ".cmul8-control").resolve()


def _journal_path(root: Path, tenant_id: str, project_id: str, transaction_id: str) -> Path:
    for value, label in ((tenant_id, "tenant_id"), (project_id, "project_id"), (transaction_id, "transaction_id")):
        validate_scope_id(value, label)
    directory = (root / ".invitation-acceptance" / tenant_id / project_id).resolve()
    if directory != root and root not in directory.parents:
        raise ValueError("acceptance journal unavailable")
    return directory / f"{transaction_id}.json"


def is_acceptance_complete(tenant_id: str, project_id: str, transaction_id: str, *, root: Path | None = None) -> bool:
    """Return true only for the exact complete journal, failing closed.

    Readers share the tenant coordinator lock with admission.  That means a
    reader observes either the prior durable journal or the fully replaced
    journal, never a half-written acceptance decision.
    """
    resolved_root = (root or _default_root()).resolve()
    try:
        path = _journal_path(resolved_root, tenant_id, project_id, transaction_id)
        lock_dir = resolved_root / ".invitation-acceptance-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        lock_path = lock_dir / f"{tenant_id}.lock"
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        lock_fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_SH)
            descriptor = os.open(path, flags)
            try:
                value = json.loads(os.read(descriptor, 1024 * 1024).decode("utf-8"))
            finally:
                os.close(descriptor)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
            os.close(lock_fd)
        return isinstance(value, dict) and value.get("state") == "COMPLETE" and value.get("transaction_id") == transaction_id and value.get("tenant_id") == tenant_id and value.get("project_id") == project_id
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def is_acceptance_complete_for_tenant(tenant_id: str, transaction_id: str) -> bool:
    """Identity memberships are tenant-scoped, so locate their one journal safely."""
    try:
        root = _default_root().resolve()
        validate_scope_id(tenant_id, "tenant_id")
        validate_scope_id(transaction_id, "transaction_id")
        base = root / ".invitation-acceptance" / tenant_id
        if not base.is_dir():
            return False
        return any(is_acceptance_complete(tenant_id, child.name, transaction_id, root=root)
                   for child in base.iterdir() if child.is_dir() and not child.is_symlink())
    except (OSError, ValueError):
        return False


class InvitationAcceptanceCoordinator:
    """Single-use invitation admission with tenant -> identity -> room lock order."""

    def __init__(self, repository: JsonCollaborationRepository, *, root: str | Path | None = None) -> None:
        self.repository = repository
        self.root = Path(root).resolve() if root is not None else repository.root
        self.fault_injector: Any = None
        self._accept_scope = threading.local()

    @contextmanager
    def _tenant_lock(self, tenant_id: str) -> Iterator[None]:
        validate_scope_id(tenant_id, "tenant_id")
        lock_dir = self.root / ".invitation-acceptance-locks"
        lock_dir.mkdir(parents=True, exist_ok=True)
        path = lock_dir / f"{tenant_id}.lock"
        key = str(path)
        depths = getattr(_LOCK_DEPTH, "depths", {})
        if depths.get(key, 0):
            depths[key] += 1
            _LOCK_DEPTH.depths = depths
            try:
                yield
            finally:
                depths[key] -= 1
            return
        with _LOCKS_GUARD:
            local = _LOCKS.setdefault(key, threading.RLock())
        with local:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                depths[key] = 1
                _LOCK_DEPTH.depths = depths
                try:
                    yield
                finally:
                    depths.pop(key, None)
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)

    def _load(self, tenant_id: str, project_id: str, transaction_id: str) -> dict[str, Any] | None:
        path = _journal_path(self.root, tenant_id, project_id, transaction_id)
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ConflictError("acceptance journal is unavailable") from exc

    def _save(self, row: dict[str, Any]) -> None:
        path = _journal_path(self.root, row["tenant_id"], row["project_id"], row["transaction_id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        payload = json.dumps(row, sort_keys=True, separators=(",", ":"))
        with open(temporary, "x", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        if self.fault_injector:
            self.fault_injector(row["state"])

    def _checkpoint(self, boundary: str) -> None:
        if self.fault_injector:
            self.fault_injector(boundary)

    def _find_replay(self, tenant_id: str, project_id: str, actor_id: str,
                     client_request_id: str) -> dict[str, Any] | None:
        probe = _journal_path(self.root, tenant_id, project_id, new_id("invite_accept")).parent
        if not probe.exists():
            return None
        for candidate in sorted(probe.glob("*.json"), key=lambda item: item.name):
            try:
                prior = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if prior.get("actor_id") == actor_id and prior.get("client_request_id") == client_request_id:
                return prior
        return None

    @staticmethod
    def _membership_payload(membership: Any) -> dict[str, Any]:
        return {
            "tenant_id": membership.tenant_id,
            "user_id": membership.user_id,
            "role": membership.role,
            "transaction_id": membership.transaction_id,
            "visibility_state": membership.visibility_state,
        }

    @staticmethod
    def _membership_matches(membership: Any, intended: dict[str, Any], *, committed: bool) -> bool:
        if membership is None:
            return False
        expected_visibility = "committed" if committed else intended["visibility_state"]
        return (
            membership.tenant_id == intended["tenant_id"]
            and membership.user_id == intended["user_id"]
            and membership.role == intended["role"]
            and membership.transaction_id == intended["transaction_id"]
            and membership.visibility_state == expected_visibility
        )

    @staticmethod
    def _room_member(room: Any, actor_id: str, transaction_id: str) -> Member | None:
        return next(
            (
                item for item in room.members
                if item.actor_id == actor_id and item.transaction_id == transaction_id
            ),
            None,
        )

    @staticmethod
    def _room_member_matches(member: Member | None, intended: dict[str, Any], *, committed: bool) -> bool:
        if member is None:
            return False
        expected_visibility = "committed" if committed else intended["visibility_state"]
        return (
            member.actor_id == intended["actor_id"]
            and member.role == intended["role"]
            and member.transaction_id == intended["transaction_id"]
            and member.visibility_state == expected_visibility
        )

    @staticmethod
    def _invitation_matches(invitation: Invitation, journal: dict[str, Any]) -> bool:
        intended_member = journal.get("room_member") or {}
        return (
            invitation.id == journal.get("invitation_id")
            and invitation.tenant_id == journal.get("tenant_id")
            and invitation.project_id == journal.get("project_id")
            and invitation.invitee_email == journal.get("verified_email")
            and invitation.requested_role == intended_member.get("role")
            and secrets.compare_digest(
                invitation.accept_token_digest,
                str(journal.get("invitation_digest", "")),
            )
            and invitation.revision == int(journal.get("invitation_revision", -2)) + 1
            and invitation.status == "accepted"
            and invitation.accepted_actor_id == journal.get("actor_id")
        )

    def _complete_result(self, journal: dict[str, Any]) -> tuple[Invitation, Member]:
        """Re-verify both committed children before honoring a COMPLETE replay."""
        from simulacra.demo.identity import get_membership_record, membership_store_lock

        tenant_id = str(journal["tenant_id"])
        project_id = str(journal["project_id"])
        actor_id = str(journal["actor_id"])
        transaction_id = str(journal["transaction_id"])
        intended_membership = journal.get("tenant_membership")
        intended_member = journal.get("room_member")
        if not isinstance(intended_membership, dict) or not isinstance(intended_member, dict):
            raise InvitationUnavailable("invitation unavailable")
        with membership_store_lock(tenant_id, actor_id):
            membership = get_membership_record(tenant_id, actor_id)
            if not self._membership_matches(membership, intended_membership, committed=True):
                raise InvitationUnavailable("invitation unavailable")
            with self.repository.room_lock(tenant_id, project_id) as room:
                member = self._room_member(room, actor_id, transaction_id)
                if not self._room_member_matches(member, intended_member, committed=True):
                    raise InvitationUnavailable("invitation unavailable")
                try:
                    invitation = self.repository.get_invitation(
                        tenant_id, project_id, str(journal["invitation_id"]),
                    )
                except NotFoundError as exc:
                    raise InvitationUnavailable("invitation unavailable") from exc
                if not self._invitation_matches(invitation, journal):
                    raise InvitationUnavailable("invitation unavailable")
                return invitation, member

    def accept(self, *, tenant_id: str, project_id: str, invitation_id: str, actor_id: str,
               verified_email: str, client_request_id: str, token: str) -> tuple[Invitation, Member]:
        """Commit tagged rows, then make them visible only after COMPLETE."""
        if not getattr(self._accept_scope, "active", False):
            from simulacra.demo.identity import membership_store_lock
            # Keep the binding identity row stable from precheck through the
            # collaboration write and final COMPLETE publication.
            with self._tenant_lock(tenant_id):
                with membership_store_lock(tenant_id, actor_id):
                    self._accept_scope.active = True
                    try:
                        return self.accept(
                            tenant_id=tenant_id,
                            project_id=project_id,
                            invitation_id=invitation_id,
                            actor_id=actor_id,
                            verified_email=verified_email,
                            client_request_id=client_request_id,
                            token=token,
                        )
                    finally:
                        self._accept_scope.active = False
        with self._tenant_lock(tenant_id):
            normalized_email = verified_email.strip().lower()
            journal = self._find_replay(tenant_id, project_id, actor_id, client_request_id)
            transaction_id = str(journal["transaction_id"]) if journal else new_id("invite_accept")
            if journal is not None and journal.get("invitation_id") != invitation_id:
                raise InvitationUnavailable("invitation unavailable")
            if journal is not None and (
                journal.get("verified_email") != normalized_email
                or not secrets.compare_digest(
                    str(journal.get("invitation_digest", "")),
                    hashlib.sha256(token.encode()).hexdigest(),
                )
            ):
                raise InvitationUnavailable("invitation unavailable")
            if journal is not None and journal.get("state") == "COMPLETE":
                return self._complete_result(journal)
            try:
                invitation = self.repository.get_invitation(tenant_id, project_id, invitation_id)
            except NotFoundError as exc:
                raise InvitationUnavailable("invitation unavailable") from exc
            now = datetime.now(UTC)
            if journal is None:
                if (invitation.status != "pending" or invitation.tenant_id != tenant_id or invitation.project_id != project_id
                        or invitation.invitee_email != normalized_email or not invitation.token_matches(token)
                        or datetime.fromisoformat(invitation.expires_at).astimezone(UTC) <= now):
                    raise InvitationUnavailable("invitation unavailable")
                journal = {
                    "tenant_id": tenant_id,
                    "project_id": project_id,
                    "transaction_id": transaction_id,
                    "actor_id": actor_id,
                    "operation": "invitation_accept",
                    "client_request_id": client_request_id,
                    "invitation_id": invitation_id,
                    "invitation_digest": invitation.accept_token_digest,
                    "invitation_revision": invitation.revision,
                    "invitation_expires_at": invitation.expires_at,
                    "verified_email": normalized_email,
                    "room_member": {
                        "actor_id": actor_id,
                        "role": invitation.requested_role,
                        "transaction_id": transaction_id,
                        "visibility_state": "pending_commit",
                    },
                    "state": "PREPARED",
                    "created_at": iso_now(),
                    "updated_at": iso_now(),
                }
                self._save(journal)
            elif (journal.get("actor_id") != actor_id or journal.get("verified_email") not in {None, normalized_email}
                  or not secrets.compare_digest(str(journal.get("invitation_digest", "")), invitation.accept_token_digest)
                  or not invitation.token_matches(token)):
                raise InvitationUnavailable("invitation unavailable")

            from simulacra.demo.identity import (
                add_membership,
                get_membership_record,
                membership_store_lock,
                update_membership_visibility,
            )

            if journal["state"] == "PREPARED":
                if (
                    invitation.status != "pending"
                    or invitation.revision != journal.get("invitation_revision")
                    or invitation.expires_at != journal.get("invitation_expires_at")
                    or datetime.fromisoformat(invitation.expires_at).astimezone(UTC) <= now
                ):
                    journal["state"] = "ABORTED"
                    journal["updated_at"] = iso_now()
                    self._save(journal)
                    raise InvitationUnavailable("invitation unavailable")
                with membership_store_lock(tenant_id, actor_id):
                    existing = get_membership_record(tenant_id, actor_id)
                    if existing is None:
                        journal["tenant_membership"] = {
                            "tenant_id": tenant_id,
                            "user_id": actor_id,
                            "role": "member",
                            "transaction_id": transaction_id,
                            "visibility_state": "pending_commit",
                        }
                        journal["tenant_membership_mode"] = "created"
                    else:
                        journal["tenant_membership"] = self._membership_payload(existing)
                        journal["tenant_membership_mode"] = "existing"
                    journal["state"] = "COMMIT_DECIDED"
                    journal["updated_at"] = iso_now()
                    self._save(journal)

            if journal["state"] == "COMMIT_DECIDED":
                intended_membership = journal.get("tenant_membership")
                intended_member = journal.get("room_member")
                if not isinstance(intended_membership, dict) or not isinstance(intended_member, dict):
                    raise InvitationUnavailable("invitation unavailable")
                with membership_store_lock(tenant_id, actor_id):
                    existing = get_membership_record(tenant_id, actor_id)
                    if journal.get("tenant_membership_mode") == "created":
                        if existing is None:
                            add_membership(
                                tenant_id,
                                actor_id,
                                "member",
                                transaction_id=transaction_id,
                                visibility_state="pending_commit",
                            )
                            existing = get_membership_record(tenant_id, actor_id)
                        if not self._membership_matches(existing, intended_membership, committed=False):
                            raise InvitationUnavailable("invitation unavailable")
                    elif not self._membership_matches(existing, intended_membership, committed=False):
                        raise InvitationUnavailable("invitation unavailable")
                    self._checkpoint("IDENTITY_CHILD_DURABLE")
                    with self.repository.room_lock(tenant_id, project_id) as room:
                        member = self._room_member(room, actor_id, transaction_id)
                        if member is None:
                            member = Member(**intended_member)
                            updated = replace(
                                room,
                                members=[*room.members, member],
                                revision=room.revision + 1,
                                updated_at=iso_now(),
                            )
                            self.repository.save_room(updated, room.revision)
                        elif not self._room_member_matches(member, intended_member, committed=False):
                            raise InvitationUnavailable("invitation unavailable")
                        self._checkpoint("ROOM_CHILD_DURABLE")
                        current_invitation = self.repository.get_invitation(
                            tenant_id, project_id, invitation_id,
                        )
                        if current_invitation.status == "pending":
                            if current_invitation.revision != journal.get("invitation_revision"):
                                raise InvitationUnavailable("invitation unavailable")
                            accepted = replace(
                                current_invitation,
                                status="accepted",
                                accepted_actor_id=actor_id,
                                revision=current_invitation.revision + 1,
                                updated_at=iso_now(),
                            )
                            self.repository.save_invitation(accepted, current_invitation.revision)
                        elif (
                            current_invitation.status == "accepted"
                            and current_invitation.accepted_actor_id == actor_id
                        ):
                            accepted = current_invitation
                        else:
                            raise InvitationUnavailable("invitation unavailable")
                        self._checkpoint("INVITATION_CHILD_DURABLE")
                        room = self.repository.get_room(tenant_id, project_id)
                        member = self._room_member(room, actor_id, transaction_id)
                        if not self._room_member_matches(member, intended_member, committed=False):
                            raise InvitationUnavailable("invitation unavailable")
                        existing = get_membership_record(tenant_id, actor_id)
                        if not self._membership_matches(existing, intended_membership, committed=False):
                            raise InvitationUnavailable("invitation unavailable")
                        journal["state"] = "STORES_DURABLE"
                        journal["updated_at"] = iso_now()
                        self._save(journal)

            if journal["state"] == "STORES_DURABLE":
                intended_membership = journal.get("tenant_membership")
                intended_member = journal.get("room_member")
                if not isinstance(intended_membership, dict) or not isinstance(intended_member, dict):
                    raise InvitationUnavailable("invitation unavailable")
                with membership_store_lock(tenant_id, actor_id):
                    existing = get_membership_record(tenant_id, actor_id)
                    if journal.get("tenant_membership_mode") == "created":
                        if existing is None or existing.transaction_id != transaction_id or existing.role != "member":
                            raise InvitationUnavailable("invitation unavailable")
                        if existing.visibility_state != "committed":
                            update_membership_visibility(tenant_id, actor_id, transaction_id, "committed")
                        existing = get_membership_record(tenant_id, actor_id)
                    if not self._membership_matches(existing, intended_membership, committed=True):
                        raise InvitationUnavailable("invitation unavailable")
                    self._checkpoint("IDENTITY_CHILD_COMMITTED")
                    with self.repository.room_lock(tenant_id, project_id) as room:
                        member = self._room_member(room, actor_id, transaction_id)
                        if member is None or member.role != intended_member["role"]:
                            raise InvitationUnavailable("invitation unavailable")
                        if member.visibility_state != "committed":
                            replacement = replace(member, visibility_state="committed")
                            updated = replace(
                                room,
                                members=[replacement if item == member else item for item in room.members],
                                revision=room.revision + 1,
                                updated_at=iso_now(),
                            )
                            self.repository.save_room(updated, room.revision)
                            member = replacement
                        self._checkpoint("ROOM_CHILD_COMMITTED")
                        current_invitation = self.repository.get_invitation(
                            tenant_id, project_id, invitation_id,
                        )
                        if not self._invitation_matches(current_invitation, journal):
                            raise InvitationUnavailable("invitation unavailable")
                        room = self.repository.get_room(tenant_id, project_id)
                        member = self._room_member(room, actor_id, transaction_id)
                        existing = get_membership_record(tenant_id, actor_id)
                        if (
                            not self._membership_matches(existing, intended_membership, committed=True)
                            or not self._room_member_matches(member, intended_member, committed=True)
                        ):
                            raise InvitationUnavailable("invitation unavailable")
                        journal["state"] = "COMPLETE"
                        journal["completed_at"] = iso_now()
                        journal["updated_at"] = journal["completed_at"]
                        self._save(journal)
                        return current_invitation, member

            if journal["state"] == "COMPLETE":
                return self._complete_result(journal)
            raise InvitationUnavailable("invitation unavailable")
