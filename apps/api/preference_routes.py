"""Current-human-only workplace preference routes."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from apps.api.security import get_auth
from simulacra.collaboration import JsonCollaborationRepository
from simulacra.demo.identity import AuthContext
from simulacra.demo.paths import RUNS_DIR
from simulacra.workplace.preferences import (
    JsonWorkplacePreferenceRepository,
    PreferenceValidationError,
    RevisionConflict,
)


router = APIRouter(tags=["workplace-preferences"])
_preferences_root = RUNS_DIR / ".workplace-control" / "preferences"
_collaboration_root = RUNS_DIR / ".cmul8-control"


class PublicBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkViewPreferenceBody(PublicBody):
    expected_revision: int = Field(ge=0)
    scope: str = Field(min_length=1, max_length=128)
    view: str
    filters: dict[str, Any]


class NotificationPreferenceBody(PublicBody):
    expected_revision: int = Field(ge=0)
    event_selection: str
    channels: list[str] = Field(max_length=8)
    digest: str
    muted_mission_ids: list[str] = Field(max_length=100)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"code": code, "message": message})


def _repository() -> JsonWorkplacePreferenceRepository:
    return JsonWorkplacePreferenceRepository(_preferences_root)


@router.get("/workspace/preferences")
def workspace_preferences(
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict:
    try:
        result = _repository().get(ctx.tenant_id, ctx.user.id)
        current_missions = set(
            JsonCollaborationRepository(_collaboration_root).member_project_ids(ctx.tenant_id, ctx.user.id)
        )
        notification = dict(result["notification_preference"])
        notification["muted_mission_ids"] = [
            mission_id for mission_id in notification.get("muted_mission_ids", [])
            if mission_id in current_missions
        ]
        return {"work_view_preferences": result["work_view_preferences"], "notification_preference": notification}
    except PreferenceValidationError as exc:
        raise _error(503, "preferences_unavailable", "Preferences are temporarily unavailable.") from exc


@router.put("/workspace/preferences/work-view")
def put_work_view_preference(
    body: WorkViewPreferenceBody,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict:
    try:
        result = _repository().put_work_view(
            ctx.tenant_id,
            ctx.user.id,
            expected_revision=body.expected_revision,
            scope=body.scope,
            view=body.view,
            filters=body.filters,
        )
    except RevisionConflict as exc:
        raise _error(409, "revision_conflict", "These preferences changed. Refresh and try again.") from exc
    except PreferenceValidationError as exc:
        raise _error(400, "preference_invalid", "Choose valid Work preferences.") from exc
    return {"work_view_preference": result}


@router.put("/workspace/preferences/notifications")
def put_notification_preference(
    body: NotificationPreferenceBody,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict:
    collaboration = JsonCollaborationRepository(_collaboration_root)
    try:
        # Hold every relevant room lock through the private preference replace,
        # so a removed human cannot persist a newly unauthorized Mission mute.
        visible_members: dict[str, Any] = {}
        for mission_id in sorted(set(body.muted_mission_ids)):
            member = collaboration.visible_member(
                collaboration.get_room(ctx.tenant_id, mission_id), ctx.user.id,
            )
            if member is None:
                raise PreferenceValidationError("notification preference is invalid")
            visible_members[mission_id] = member
        with ExitStack() as stack:
            for mission_id in sorted(set(body.muted_mission_ids)):
                room = stack.enter_context(collaboration.room_lock(ctx.tenant_id, mission_id))
                if visible_members[mission_id] not in room.members:
                    raise PreferenceValidationError("notification preference is invalid")
            result = _repository().put_notification(
                ctx.tenant_id,
                ctx.user.id,
                expected_revision=body.expected_revision,
                event_selection=body.event_selection,
                channels=body.channels,
                digest=body.digest,
                muted_mission_ids=body.muted_mission_ids,
            )
    except RevisionConflict as exc:
        raise _error(409, "revision_conflict", "These preferences changed. Refresh and try again.") from exc
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise _error(400, "preference_invalid", "Choose valid notification preferences.") from exc
    return {"notification_preference": result}
