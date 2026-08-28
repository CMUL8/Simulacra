"""Durable, replayable external-notification projection and delivery outbox."""

from __future__ import annotations

import fcntl
import json
import os
import threading
import uuid
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Protocol

from .models import validate_scope_id
from .repository import JsonCollaborationRepository


_LOCKS_GUARD = threading.Lock()
_STATE_LOCKS: dict[str, threading.RLock] = {}
_OWNER_ADMIN_ROLES = frozenset({"owner", "admin"})
_OWNER_ADMIN_ATTENTION = frozenset(
    {"unassigned_work", "decision_required", "plan_approval", "retry_required"}
)
_DECISION_NOTIFICATION_TYPES = frozenset(
    {"decision_required", "output_verification", "plan_approval"}
)


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _scope_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item or item in result:
            continue
        try:
            validate_scope_id(item, "recipient_id")
        except (TypeError, ValueError):
            continue
        result.append(item)
    return result


def _event_recipient_rules(payload: dict[str, Any], members: dict[str, Any]) -> list[tuple[str, str, str]]:
    """Return frozen attention recipients as (actor, rule, notification type)."""
    category = str(payload.get("category") or "")
    attention_type = str(payload.get("attention_type") or payload.get("type") or "")
    candidates: list[tuple[str, str, str]] = []
    if category == "mentions":
        candidates.extend((actor_id, "designated_snapshot", "mention") for actor_id in _scope_ids(payload.get("mention_ids")))
    elif category == "assigned":
        assignee_id = payload.get("assignee_id")
        if isinstance(assignee_id, str) and assignee_id:
            candidates.append((assignee_id, "designated_snapshot", "assignment"))
    elif category == "decision" or attention_type in _OWNER_ADMIN_ATTENTION:
        notification_type = (
            attention_type if attention_type in _OWNER_ADMIN_ATTENTION else "decision_required"
        )
        candidates.extend(
            (actor_id, "owner_admin", notification_type)
            for actor_id, member in members.items()
            if getattr(member, "role", "") in _OWNER_ADMIN_ROLES
        )
    elif attention_type == "output_verification":
        designated = _scope_ids(payload.get("verifier_ids"))
        mission_owner_id = payload.get("mission_owner_id") or payload.get("owner_id")
        if isinstance(mission_owner_id, str) and mission_owner_id not in designated:
            designated.append(mission_owner_id)
        candidates.extend(
            (actor_id, "mission_owner_verifier", "output_verification")
            for actor_id in designated
        )
    elif attention_type == "workspace_action":
        designated = payload.get("designated_human_id") or payload.get("recipient_id")
        if isinstance(designated, str) and designated:
            candidates.append((designated, "designated_snapshot", "workspace_action"))
    return [item for item in candidates if item[0] in members]


def _preference_suppression(
    preference: dict[str, Any],
    *,
    project_id: str,
    channel: str,
    notification_type: str,
) -> str | None:
    selection = preference.get("event_selection", "all_actionable")
    if project_id in preference.get("muted_mission_ids", []):
        return "mission_muted"
    if channel not in preference.get("channels", []):
        return "channel_disabled"
    if selection == "off":
        return "delivery_disabled"
    if selection == "mentions_and_decisions" and notification_type not in {
        "mention",
        *_DECISION_NOTIFICATION_TYPES,
    }:
        return "event_selection_disabled"
    if selection not in {"all_actionable", "mentions_and_decisions"}:
        raise ValueError("notification preference is unavailable")
    return None


class ExternalNotificationAdapter(Protocol):
    def deliver(
        self,
        *,
        delivery_id: str,
        recipient_id: str,
        channel: str,
        payload: dict[str, Any],
    ) -> dict[str, Any] | None: ...


class DeterministicNotificationAdapter:
    """Network-free test adapter that deduplicates by durable delivery ID."""

    def __init__(self) -> None:
        self.sent: dict[str, dict[str, Any]] = {}
        self.failures_remaining = 0

    def deliver(
        self,
        *,
        delivery_id: str,
        recipient_id: str,
        channel: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        if self.failures_remaining:
            self.failures_remaining -= 1
            raise RuntimeError("deterministic delivery failure")
        self.sent.setdefault(
            delivery_id,
            {"recipient_id": recipient_id, "channel": channel, "payload": dict(payload)},
        )
        return {"provider_delivery_id": delivery_id}


class NotificationOutbox:
    """Project-scoped durable cursor and leased external-delivery rows.

    Cursor projection, lease claims, and delivery finalization all mutate the same
    JSON record under the same process-safe lock. Provider handoff occurs outside
    that lock; a lease token prevents a stale worker from finalizing a reclaimed row.
    """

    def __init__(
        self,
        root: str | Path,
        *,
        max_attempts: int = 5,
        retry_base_seconds: float = 5.0,
        retry_max_seconds: float = 300.0,
        clock=_now,
    ) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.max_attempts = max(1, int(max_attempts))
        self.retry_base_seconds = max(0.0, float(retry_base_seconds))
        self.retry_max_seconds = max(self.retry_base_seconds, float(retry_max_seconds))
        self.clock = clock
        self.fault_injector: Any = None
        with _LOCKS_GUARD:
            self._thread_lock = _STATE_LOCKS.setdefault(str(self.root), threading.RLock())

    def _project_dir(self, tenant_id: str, project_id: str) -> Path:
        validate_scope_id(tenant_id, "tenant_id")
        validate_scope_id(project_id, "project_id")
        path = (self.root / tenant_id / project_id).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("notification outbox path escapes repository root") from exc
        return path

    def _path(self, tenant_id: str, project_id: str) -> Path:
        return self._project_dir(tenant_id, project_id) / "notification_outbox.json"

    @staticmethod
    def _default_state() -> dict[str, Any]:
        return {"cursor": 0, "outbox": []}

    def _read_state(self, path: Path) -> dict[str, Any]:
        if not path.exists():
            return self._default_state()
        state = json.loads(path.read_text(encoding="utf-8"))
        state.setdefault("cursor", 0)
        state.setdefault("outbox", [])
        return state

    def _replace_state(self, path: Path, state: dict[str, Any], operation: str) -> None:
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                json.dump(state, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            if self.fault_injector:
                self.fault_injector(f"before_{operation}_replace")
            os.replace(temporary, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass

    @contextmanager
    def _locked(
        self,
        tenant_id: str,
        project_id: str,
        *,
        operation: str,
        write: bool = True,
    ) -> Iterator[dict[str, Any]]:
        project_dir = self._project_dir(tenant_id, project_id)
        project_dir.mkdir(parents=True, exist_ok=True)
        path = project_dir / "notification_outbox.json"
        lock_path = project_dir / ".state.lock"
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        with self._thread_lock:
            descriptor = os.open(lock_path, flags, 0o600)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                state = self._read_state(path)
                yield state
                if write:
                    self._replace_state(path, state, operation)
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)

    def project(
        self,
        repository: JsonCollaborationRepository,
        *,
        tenant_id: str,
        project_id: str,
        preferences: Any,
    ) -> int:
        """Replay authoritative source events and repair missing delivery rows."""
        events = repository.list_events(tenant_id, project_id)
        room = repository.visible_room(tenant_id, project_id)
        members = {member.actor_id: member for member in room.members}
        created = 0
        with self._locked(tenant_id, project_id, operation="projection") as state:
            cursor = max(0, int(state.get("cursor", 0)))
            existing_keys = {str(item.get("dedupe_key")) for item in state["outbox"]}
            for position, event in enumerate(events[cursor:], cursor + 1):
                payload = event.payload if isinstance(event.payload, dict) else {}
                recipient_rules = _event_recipient_rules(payload, members)
                if recipient_rules:
                    for recipient_id, recipient_rule, notification_type in recipient_rules:
                        preference = preferences.get(tenant_id, recipient_id).get(
                            "notification_preference", {}
                        )
                        for channel in preference.get("channels", []):
                            if _preference_suppression(
                                preference,
                                project_id=project_id,
                                channel=channel,
                                notification_type=notification_type,
                            ):
                                continue
                            key = f"{event.id}:{recipient_id}:{channel}"
                            if key in existing_keys:
                                continue
                            timestamp = self.clock()
                            state["outbox"].append(
                                {
                                    "id": f"delivery_{uuid.uuid4().hex}",
                                    "event_id": event.id,
                                    "recipient_id": recipient_id,
                                    "channel": channel,
                                    "dedupe_key": key,
                                    "recipient_rule": recipient_rule,
                                    "payload": {
                                        "event_id": event.id,
                                        "mission_id": project_id,
                                        "action": event.action,
                                        "category": payload.get("category"),
                                        "notification_type": notification_type,
                                        "designated_recipient_ids": [
                                            item[0] for item in recipient_rules
                                        ],
                                    },
                                    "status": "pending",
                                    "attempt_count": 0,
                                    "next_attempt_at": None,
                                    "lease_expires_at": None,
                                    "lease_id": None,
                                    "provider_receipt": None,
                                    "failure_code": None,
                                    "delivered_at": None,
                                    "dead_lettered_at": None,
                                    "created_at": timestamp,
                                    "updated_at": timestamp,
                                }
                            )
                            existing_keys.add(key)
                            created += 1
                state["cursor"] = position
        return created

    def _retry_delay(self, attempt_count: int) -> float:
        return min(
            self.retry_max_seconds,
            self.retry_base_seconds * (2 ** max(0, attempt_count - 1)),
        )

    def _delivery_authorization(
        self,
        *,
        tenant_id: str,
        project_id: str,
        row: dict[str, Any],
        repository: JsonCollaborationRepository,
        preferences: Any,
    ) -> tuple[str, str | None]:
        """Re-read current authorization and delivery preferences before handoff."""
        try:
            room = repository.visible_room(tenant_id, project_id)
            members = {member.actor_id: member for member in room.members}
            recipient_id = str(row["recipient_id"])
            member = members.get(recipient_id)
            if member is None:
                return "suppress", "recipient_no_longer_authorized"
            recipient_rule = row.get("recipient_rule")
            if recipient_rule == "owner_admin" and member.role not in _OWNER_ADMIN_ROLES:
                return "suppress", "recipient_no_longer_authorized"
            if recipient_rule == "mission_owner_verifier":
                designated = row.get("payload", {}).get("designated_recipient_ids", [])
                if recipient_id not in designated:
                    return "suppress", "recipient_no_longer_authorized"
            elif recipient_rule != "designated_snapshot" and recipient_rule != "owner_admin":
                return "retry", "delivery_authorization_unavailable"

            preference_record = preferences.get(tenant_id, recipient_id)
            preference = preference_record.get("notification_preference")
            if not isinstance(preference, dict):
                return "retry", "delivery_authorization_unavailable"
            notification_type = row.get("payload", {}).get("notification_type")
            channel = row.get("channel")
            if not isinstance(notification_type, str) or not isinstance(channel, str):
                return "retry", "delivery_authorization_unavailable"
            suppression = _preference_suppression(
                preference,
                project_id=project_id,
                channel=channel,
                notification_type=notification_type,
            )
            return ("suppress", suppression) if suppression else ("allow", None)
        except Exception:
            return "retry", "delivery_authorization_unavailable"

    def _finalize_without_provider(
        self,
        *,
        tenant_id: str,
        project_id: str,
        lease: dict[str, Any],
        now: datetime,
        failure_code: str,
        terminal: bool,
    ) -> None:
        with self._locked(tenant_id, project_id, operation="delivery_finalize") as state:
            row = next((item for item in state["outbox"] if item.get("id") == lease["id"]), None)
            if (
                row is None
                or row.get("status") != "leased"
                or row.get("lease_id") != lease.get("lease_id")
            ):
                return
            attempt_count = int(row.get("attempt_count", 0))
            exhausted = attempt_count >= self.max_attempts
            terminal = terminal or exhausted
            failed_at = self.clock()
            row.update(
                status="dead_letter" if terminal else "pending",
                next_attempt_at=(
                    None
                    if terminal
                    else (now + timedelta(seconds=self._retry_delay(attempt_count))).isoformat()
                ),
                lease_expires_at=None,
                lease_id=None,
                failure_code=failure_code,
                dead_lettered_at=failed_at if terminal else None,
                updated_at=failed_at,
            )

    def deliver(
        self,
        *,
        tenant_id: str,
        project_id: str,
        adapter: ExternalNotificationAdapter,
        repository: JsonCollaborationRepository,
        preferences: Any,
        lease_seconds: int = 60,
    ) -> int:
        """Lease one eligible row and finalize only an accepted provider handoff."""
        now = _timestamp(self.clock())
        if now is None:
            raise ValueError("notification clock must return a timezone-aware ISO timestamp")
        lease: dict[str, Any] | None = None
        with self._locked(tenant_id, project_id, operation="delivery_claim") as state:
            for row in state["outbox"]:
                status = row.get("status")
                lease_expired = status == "leased" and (
                    _timestamp(row.get("lease_expires_at")) or now
                ) <= now
                next_attempt = _timestamp(row.get("next_attempt_at"))
                retry_due = status == "pending" and (next_attempt is None or next_attempt <= now)
                if not (retry_due or lease_expired):
                    continue
                if int(row.get("attempt_count", 0)) >= self.max_attempts:
                    row.update(
                        status="dead_letter",
                        next_attempt_at=None,
                        lease_expires_at=None,
                        lease_id=None,
                        failure_code="provider_delivery_exhausted",
                        dead_lettered_at=self.clock(),
                        updated_at=self.clock(),
                    )
                    continue
                lease_id = f"lease_{uuid.uuid4().hex}"
                row.update(
                    status="leased",
                    lease_expires_at=(now + timedelta(seconds=max(1, lease_seconds))).isoformat(),
                    lease_id=lease_id,
                    next_attempt_at=None,
                    attempt_count=int(row.get("attempt_count", 0)) + 1,
                    updated_at=self.clock(),
                )
                lease = dict(row)
                break
        if lease is None:
            return 0

        authorization = "retry"
        authorization_code: str | None = "delivery_authorization_unavailable"
        receipt: dict[str, Any] | None = None
        try:
            # Member removal commits under this same durable room boundary. The
            # provider handoff therefore linearizes either wholly before or wholly
            # after removal, never after a committed removal using stale membership.
            with repository.room_lock(tenant_id, project_id):
                authorization, authorization_code = self._delivery_authorization(
                    tenant_id=tenant_id,
                    project_id=project_id,
                    row=lease,
                    repository=repository,
                    preferences=preferences,
                )
                if authorization == "allow":
                    try:
                        provider_result = adapter.deliver(
                            delivery_id=lease["id"],
                            recipient_id=lease["recipient_id"],
                            channel=lease["channel"],
                            payload=lease["payload"],
                        )
                        receipt = (
                            dict(provider_result)
                            if isinstance(provider_result, Mapping) and provider_result
                            else None
                        )
                    except Exception:
                        receipt = None
        except Exception:
            authorization = "retry"
            authorization_code = "delivery_authorization_unavailable"
        if authorization != "allow":
            self._finalize_without_provider(
                tenant_id=tenant_id,
                project_id=project_id,
                lease=lease,
                now=now,
                failure_code=authorization_code or "delivery_authorization_unavailable",
                terminal=authorization == "suppress",
            )
            return 0

        with self._locked(tenant_id, project_id, operation="delivery_finalize") as state:
            row = next((item for item in state["outbox"] if item.get("id") == lease["id"]), None)
            if row is None or row.get("status") == "delivered":
                return 1 if receipt else 0
            if row.get("status") != "leased" or row.get("lease_id") != lease.get("lease_id"):
                return 0
            if receipt:
                if self.fault_injector:
                    self.fault_injector("after_provider_before_delivered")
                delivered_at = self.clock()
                row.update(
                    status="delivered",
                    provider_receipt=receipt,
                    next_attempt_at=None,
                    lease_expires_at=None,
                    lease_id=None,
                    failure_code=None,
                    delivered_at=delivered_at,
                    updated_at=delivered_at,
                )
                return 1

            attempt_count = int(row.get("attempt_count", 0))
            terminal = attempt_count >= self.max_attempts
            failed_at = self.clock()
            row.update(
                status="dead_letter" if terminal else "pending",
                next_attempt_at=(
                    None
                    if terminal
                    else (now + timedelta(seconds=self._retry_delay(attempt_count))).isoformat()
                ),
                lease_expires_at=None,
                lease_id=None,
                failure_code=(
                    "provider_delivery_exhausted" if terminal else "provider_delivery_failed"
                ),
                dead_lettered_at=failed_at if terminal else None,
                updated_at=failed_at,
            )
        return 0

    def state(self, tenant_id: str, project_id: str) -> dict[str, Any]:
        with self._locked(
            tenant_id,
            project_id,
            operation="state_read",
            write=False,
        ) as state:
            return json.loads(json.dumps(state))
