"""Authorized public aggregate routes for the default-off workplace shell."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from apps.api.security import get_auth
from simulacra.collaboration import JsonCollaborationRepository
from simulacra.demo.identity import AuthContext
from simulacra.demo.paths import RUNS_DIR
from simulacra.demo.runs import project_dir
from simulacra.missions import JsonMissionRepository
from simulacra.missions.projections import (
    AttentionRevisionConflict,
    CursorInvalidError,
    mark_attention_read,
    paginate,
    paginate_attention,
    project_attention_items,
    project_mission_summaries,
)


router = APIRouter(tags=["workplace-summary"])
_mission_root = RUNS_DIR / ".mission-control"
_collaboration_root = RUNS_DIR / ".cmul8-control"
_cursor_secret = os.environ.get("SIMULACRA_WORKPLACE_CURSOR_SECRET", "simulacra-workplace-development-cursor-key")
_CURSOR_MESSAGE = "This list changed. Refresh and try again."
_REVISION_MESSAGE = "This item changed. Refresh and try again."


class AttentionReadBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=0)


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"code": code, "message": message})


@router.get("/missions")
def missions(
    state: str = Query("active"), cursor: str | None = None, limit: int = Query(50),
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict:
    if state not in {"active", "all"} or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise _error(400, "cursor_invalid", _CURSOR_MESSAGE)
    repository = JsonMissionRepository(_mission_root)
    collaboration = JsonCollaborationRepository(_collaboration_root)
    rows = project_mission_summaries(repository, collaboration, tenant_id=ctx.tenant_id, human_id=ctx.user.id, state=state)
    try:
        page, next_cursor = paginate(
            rows, endpoint="missions", scope=f"{ctx.tenant_id}:{ctx.user.id}:{state}", cursor=cursor, limit=limit, secret=_cursor_secret,
        )
    except (CursorInvalidError, ValueError) as exc:
        raise _error(400, "cursor_invalid", _CURSOR_MESSAGE) from exc
    return {"items": page, "next_cursor": next_cursor}


@router.get("/workspace/attention")
def workspace_attention(
    filter: str = Query("actionable"), cursor: str | None = None, limit: int = Query(50),
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict:
    if filter not in {"actionable", "all"} or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise _error(400, "cursor_invalid", _CURSOR_MESSAGE)
    repository = JsonMissionRepository(_mission_root)
    collaboration = JsonCollaborationRepository(_collaboration_root)
    rows = project_attention_items(repository, collaboration, tenant_id=ctx.tenant_id, human_id=ctx.user.id, workspace_for_project=project_dir)
    unread_count = sum(1 for row in rows if not row["read"])
    actionable_count = sum(1 for row in rows if row["actionable"])
    selected = [row for row in rows if filter == "all" or row["actionable"]]
    try:
        page, next_cursor = paginate_attention(
            selected, endpoint="workspace_attention", scope=f"{ctx.tenant_id}:{ctx.user.id}:{filter}", cursor=cursor, limit=limit, secret=_cursor_secret,
        )
    except (CursorInvalidError, ValueError) as exc:
        raise _error(400, "cursor_invalid", _CURSOR_MESSAGE) from exc
    return {"items": page, "next_cursor": next_cursor, "unread_count": unread_count, "actionable_count": actionable_count}


@router.post("/workspace/attention/read")
def read_attention(
    body: AttentionReadBody, ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict:
    collaboration = JsonCollaborationRepository(_collaboration_root)
    repository = JsonMissionRepository(_mission_root)
    rows = project_attention_items(repository, collaboration, tenant_id=ctx.tenant_id, human_id=ctx.user.id, workspace_for_project=project_dir)
    source = next((row for row in rows if row["id"] == body.event_id), None)
    if source is None:
        raise _error(404, "attention_unavailable", "This attention item is unavailable.")
    try:
        receipt = mark_attention_read(
            collaboration, tenant_id=ctx.tenant_id, project_id=source["mission_id"], human_id=ctx.user.id,
            event_id=body.event_id, expected_revision=body.expected_revision,
            clock=lambda: datetime.now(UTC).isoformat(),
        )
    except AttentionRevisionConflict as exc:
        raise _error(409, "revision_conflict", _REVISION_MESSAGE) from exc
    except (PermissionError, ValueError) as exc:
        raise _error(404, "attention_unavailable", "This attention item is unavailable.") from exc
    item = dict(source)
    item.update({"read": receipt["read"], "revision": receipt["revision"], "updated_at": receipt["read_at"]})
    return {"item": item}
