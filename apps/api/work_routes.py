"""Authorized workspace Work aggregate routes."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException

from apps.api.security import get_auth
from apps.api.file_routes import output_file_id
from simulacra.collaboration import JsonCollaborationRepository
from simulacra.demo.identity import AuthContext
from simulacra.demo.paths import RUNS_DIR
from simulacra.demo.runs import project_dir
from simulacra.missions import JsonMissionRepository, MissionService
from simulacra.missions.projections import CursorInvalidError, paginate, project_work_items
from simulacra.workplace.assignment_coordinator import AssignmentCoordinator


router = APIRouter(tags=["workplace-work"])
_mission_root = RUNS_DIR / ".mission-control"
_collaboration_root = RUNS_DIR / ".cmul8-control"
_runs_root = RUNS_DIR
_cursor_secret = os.environ.get("SIMULACRA_WORKPLACE_CURSOR_SECRET", "simulacra-workplace-development-cursor-key")
_CURSOR_MESSAGE = "This list changed. Refresh and try again."


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"code": code, "message": message})


def _assignment_visible(tenant_id: str, project_id: str, transaction_id: str, run_id: str):
    collaboration = JsonCollaborationRepository(_collaboration_root)
    coordinator = AssignmentCoordinator(
        collaboration,
        MissionService(JsonMissionRepository(_mission_root)),
        project_dir(project_id),
        runs_root=_runs_root,
        clock=lambda: datetime.now(UTC).isoformat(),
    )
    result = coordinator.visible_result(
        tenant_id=tenant_id,
        project_id=project_id,
        transaction_id=transaction_id,
    )
    if result is None or (run_id and result.run_id != run_id):
        return None
    return result


@router.get("/workspace/work")
def workspace_work(
    bucket: str | None = None,
    mission_id: str | None = None,
    assignee_id: str | None = None,
    cursor: str | None = None,
    limit: int = 50,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict:
    allowed_buckets = {"needs_you", "in_progress", "ready_for_review", "done", "stopped"}
    if bucket is not None and bucket not in allowed_buckets:
        raise _error(400, "work_filter_invalid", "Choose a valid Work filter.")
    if isinstance(limit, bool) or not 1 <= limit <= 100:
        raise _error(400, "cursor_invalid", _CURSOR_MESSAGE)
    rows = project_work_items(
        JsonMissionRepository(_mission_root),
        JsonCollaborationRepository(_collaboration_root),
        tenant_id=ctx.tenant_id,
        human_id=ctx.user.id,
        assignment_visible=lambda project, transaction, run: _assignment_visible(
            ctx.tenant_id, project, transaction, run,
        ),
        output_file_identity=lambda project, output: output_file_id(
            output, tenant_id=ctx.tenant_id, project_id=project,
        ),
    )
    selected = [
        row for row in rows
        if (bucket is None or row["state"] == bucket)
        and (mission_id is None or row["mission_id"] == mission_id)
        and (assignee_id is None or (row.get("assignee") or {}).get("id") == assignee_id)
    ]
    scope = f"{ctx.tenant_id}:{ctx.user.id}:{bucket or ''}:{mission_id or ''}:{assignee_id or ''}"
    try:
        page, next_cursor = paginate(
            selected,
            endpoint="workspace_work",
            scope=scope,
            cursor=cursor,
            limit=limit,
            secret=_cursor_secret,
        )
    except (CursorInvalidError, ValueError) as exc:
        raise _error(400, "cursor_invalid", _CURSOR_MESSAGE) from exc
    return {"items": page, "next_cursor": next_cursor}
