"""Authorized workspace wake-up stream backed only by durable records."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import os
import re
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.responses import StreamingResponse

from apps.api.security import get_auth
from simulacra.collaboration import JsonCollaborationRepository
from simulacra.demo.identity import AuthContext
from simulacra.demo.paths import RUNS_DIR


router = APIRouter(tags=["workplace-events"])
_collaboration_root = RUNS_DIR / ".cmul8-control"
_cursor_secret = os.environ.get(
    "SIMULACRA_WORKPLACE_CURSOR_SECRET", "simulacra-workplace-development-cursor-key",
)
HEARTBEAT_SECONDS = 20
POLL_SECONDS = 1
_INVALID_RESUME_MESSAGE = "Live updates could not resume. Refresh and try again."
_FORBIDDEN_MESSAGE = "You do not have access to live Mission updates."
_EVENT_ID_RE = re.compile(r"^evt_[A-Za-z0-9_.-]{1,127}$")


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"code": code, "message": message})


def _get_sse_auth(
    authorization: Annotated[str | None, Header()] = None,
    x_tenant_id: Annotated[str | None, Header()] = None,
) -> AuthContext:
    """Use the existing resolver with URL credentials forcibly disabled."""
    return get_auth(
        authorization=authorization, x_tenant_id=x_tenant_id, token=None, tenant=None,
    )

def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _cursor_encode(*, tenant_id: str, order: tuple[str, str, str]) -> str:
    payload = json.dumps(
        {"tenant": tenant_id, "order": list(order)}, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    signature = hmac.new(_cursor_secret.encode("utf-8"), payload, hashlib.sha256).digest()
    return f"wke_{_b64(payload)}.{_b64(signature)}"


def _cursor_decode(value: str, *, tenant_id: str) -> tuple[str, str, str]:
    try:
        if not isinstance(value, str) or not value.startswith("wke_"):
            raise ValueError("invalid")
        encoded, encoded_signature = value[4:].split(".", 1)
        payload = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        signature = base64.urlsafe_b64decode(
            encoded_signature + "=" * (-len(encoded_signature) % 4),
        )
        expected = hmac.new(_cursor_secret.encode("utf-8"), payload, hashlib.sha256).digest()
        decoded = json.loads(payload)
        order = decoded.get("order") if isinstance(decoded, dict) else None
        if (
            not hmac.compare_digest(signature, expected)
            or decoded.get("tenant") != tenant_id
            or not isinstance(order, list)
            or len(order) != 3
            or not all(isinstance(item, str) for item in order)
        ):
            raise ValueError("invalid")
        return order[0], order[1], order[2]
    except Exception as exc:
        raise _error(400, "event_cursor_invalid", _INVALID_RESUME_MESSAGE) from exc


def _domain_type(action: str) -> str:
    if action.startswith(("task.", "review.")):
        return "work.changed"
    if action.startswith(("room.", "member.")):
        return "mission.changed"
    return "conversation.changed"


def _visible_member_snapshot(
    repository: JsonCollaborationRepository, tenant_id: str, project_id: str, actor_id: str,
) -> Any | None:
    try:
        return repository.visible_member(repository.get_room(tenant_id, project_id), actor_id)
    except Exception:
        return None


def _collect_authorized_candidates(
    repository: JsonCollaborationRepository, ctx: AuthContext,
) -> list[dict[str, Any]]:
    project_ids = repository.member_project_ids(ctx.tenant_id, ctx.user.id)
    if not project_ids:
        raise _error(403, "events_forbidden", _FORBIDDEN_MESSAGE)
    candidates: list[dict[str, Any]] = []
    for project_id in project_ids:
        member = _visible_member_snapshot(repository, ctx.tenant_id, project_id, ctx.user.id)
        with repository.room_lock(ctx.tenant_id, project_id) as room:
            if member is None or member not in room.members:
                continue
            events = repository.list_events(ctx.tenant_id, project_id)
            state = repository.conversation_state(ctx.tenant_id, project_id)
        for event in events:
            source_id = f"domain:{event.id}"
            candidates.append({
                "order": (event.timestamp, project_id, source_id),
                "type": _domain_type(event.action),
                "mission_id": project_id,
                "occurred_at": event.timestamp,
            })
        wake_events = state.get("wake_events")
        if not isinstance(wake_events, Mapping):
            continue
        for raw_id, raw in wake_events.items():
            if not isinstance(raw_id, str) or not _EVENT_ID_RE.fullmatch(raw_id) or not isinstance(raw, Mapping):
                continue
            recipient = raw.get("recipient_human_id")
            if recipient is not None and recipient != ctx.user.id:
                continue
            occurred_at = raw.get("occurred_at")
            event_type = raw.get("type")
            mission_id = raw.get("mission_id")
            if (
                mission_id != project_id
                or not isinstance(occurred_at, str)
                or event_type not in {"conversation.changed", "saved.changed"}
            ):
                continue
            source_id = f"conversation:{raw_id}"
            candidates.append({
                "order": (occurred_at, project_id, source_id),
                "type": event_type,
                "mission_id": project_id,
                "occurred_at": occurred_at,
            })
    candidates.sort(key=lambda item: item["order"])
    return candidates


def _public_wakeup(tenant_id: str, candidate: Mapping[str, Any]) -> dict[str, str]:
    order = candidate["order"]
    return {
        "id": _cursor_encode(tenant_id=tenant_id, order=order),
        "type": str(candidate["type"]),
        "mission_id": str(candidate["mission_id"]),
        "occurred_at": str(candidate["occurred_at"]),
    }


def _authorized_wakeups(
    repository: JsonCollaborationRepository, ctx: AuthContext, last_event_id: str | None,
) -> list[dict[str, str]]:
    candidates = _collect_authorized_candidates(repository, ctx)
    if last_event_id is None:
        return [_public_wakeup(ctx.tenant_id, item) for item in candidates]
    boundary = _cursor_decode(last_event_id, tenant_id=ctx.tenant_id)
    if boundary[2].startswith("reset:"):
        # Reset cursors are real workspace positions.  They reconcile once,
        # then advance normally instead of producing a reset on every poll.
        return [
            _public_wakeup(ctx.tenant_id, item)
            for item in candidates
            if item["order"] > boundary
        ]
    if not any(item["order"] == boundary for item in candidates):
        # A valid but expired/inaccessible cursor requests a workspace-wide
        # durable reconciliation without identifying the unavailable Mission.
        stamp = datetime.now(UTC).isoformat()
        reset_order = (stamp, "", f"reset:{hashlib.sha256(last_event_id.encode()).hexdigest()}")
        return [{
            "id": _cursor_encode(tenant_id=ctx.tenant_id, order=reset_order),
            "type": "workspace.reset",
            "mission_id": "",
            "occurred_at": stamp,
        }]
    return [
        _public_wakeup(ctx.tenant_id, item)
        for item in candidates
        if item["order"] > boundary
    ]


def _event_is_publishable(
    repository: JsonCollaborationRepository, ctx: AuthContext, event: Mapping[str, Any],
) -> bool:
    mission_id = event.get("mission_id")
    current = repository.member_project_ids(ctx.tenant_id, ctx.user.id)
    if event.get("type") == "workspace.reset" and mission_id == "":
        return bool(current)
    if not isinstance(mission_id, str) or mission_id not in current:
        return False
    member = _visible_member_snapshot(repository, ctx.tenant_id, mission_id, ctx.user.id)
    try:
        with repository.room_lock(ctx.tenant_id, mission_id) as room:
            return member is not None and member in room.members
    except Exception:
        return False


def _encode_event(event: Mapping[str, Any]) -> str:
    public = {key: event[key] for key in ("id", "type", "mission_id", "occurred_at")}
    return f"id: {public['id']}\nevent: wakeup\ndata: {json.dumps(public, sort_keys=True, separators=(',', ':'))}\n\n"


class _WorkspaceEventStream(AsyncIterator[str]):
    """Async wake-up iterator whose publication decision shares the room lock."""

    def __init__(
        self, repository: JsonCollaborationRepository, ctx: AuthContext, *,
        last_event_id: str | None, initial_events: list[dict[str, str]] | None,
        max_cycles: int | None, poll_seconds: float,
    ) -> None:
        self.repository = repository
        self.ctx = ctx
        self.cursor = last_event_id
        self.pending = initial_events
        self.max_cycles = max_cycles
        self.poll_seconds = poll_seconds
        self.elapsed = 0.0
        self.cycles = 0
        self.events: list[dict[str, str]] = []
        self.index = 0
        self._publication_lock: Any = None
        self._closed = False

    def __aiter__(self) -> "_WorkspaceEventStream":
        return self

    def _release_publication_lock(self) -> None:
        if self._publication_lock is not None:
            lock, self._publication_lock = self._publication_lock, None
            lock.__exit__(None, None, None)

    async def aclose(self) -> None:
        self._closed = True
        self._release_publication_lock()

    async def __anext__(self) -> str:
        self._release_publication_lock()
        if self._closed:
            raise StopAsyncIteration
        while True:
            while self.index < len(self.events):
                event = self.events[self.index]
                self.index += 1
                mission_id = event.get("mission_id")
                if event.get("type") == "workspace.reset" and mission_id == "":
                    project_ids = self.repository.member_project_ids(
                        self.ctx.tenant_id, self.ctx.user.id,
                    )
                    publication_project = project_ids[0] if project_ids else None
                else:
                    publication_project = mission_id if isinstance(mission_id, str) else None
                if publication_project is None:
                    continue
                member = _visible_member_snapshot(
                    self.repository, self.ctx.tenant_id, publication_project, self.ctx.user.id,
                )
                lock = self.repository.room_lock(self.ctx.tenant_id, publication_project)
                room = lock.__enter__()
                if member is None or member not in room.members:
                    lock.__exit__(None, None, None)
                    continue
                # This lock remains held until the consumer requests the next
                # chunk (or closes), serializing the last authorization check
                # with the publication of this exact wake-up.
                self._publication_lock = lock
                self.cursor = str(event["id"])
                return _encode_event(event)
            if self.max_cycles is not None and self.cycles >= self.max_cycles:
                self._closed = True
                raise StopAsyncIteration
            self.cycles += 1
            try:
                self.events = (
                    self.pending if self.pending is not None
                    else _authorized_wakeups(self.repository, self.ctx, self.cursor)
                )
            except HTTPException as exc:
                if exc.status_code == 403:
                    self._closed = True
                    raise StopAsyncIteration from exc
                raise
            self.pending = None
            self.index = 0
            if self.events:
                continue
            await asyncio.sleep(self.poll_seconds)
            self.elapsed += self.poll_seconds
            if self.elapsed >= HEARTBEAT_SECONDS:
                self.elapsed = 0.0
                return ": heartbeat\n\n"

    def __del__(self) -> None:
        self._release_publication_lock()


def _stream_workspace_events(
    repository: JsonCollaborationRepository, ctx: AuthContext, *, last_event_id: str | None,
    initial_events: list[dict[str, str]] | None = None, max_cycles: int | None = None,
    poll_seconds: float = POLL_SECONDS,
) -> AsyncIterator[str]:
    return _WorkspaceEventStream(
        repository, ctx, last_event_id=last_event_id, initial_events=initial_events,
        max_cycles=max_cycles, poll_seconds=poll_seconds,
    )


@router.get("/workspace/events")
def get_workspace_events(
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ctx: Annotated[AuthContext, Depends(_get_sse_auth)] = None,  # type: ignore[assignment]
) -> StreamingResponse:
    repository = JsonCollaborationRepository(_collaboration_root)
    initial = _authorized_wakeups(repository, ctx, last_event_id)
    return StreamingResponse(
        _stream_workspace_events(
            repository, ctx, last_event_id=last_event_id, initial_events=initial,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-store",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
