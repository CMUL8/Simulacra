"""Public Mission read models and deterministic opaque cursor helpers.

The route layer intentionally receives only these allow-list projections.  This
keeps persisted execution detail from becoming an accidental public contract.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from .models import clean_public_mapping


_MAX_DISCOVERY_BYTES = 1_048_576
# Detail leaves are deliberately bounded before invoking the legacy repository
# readers.  16 MiB leaves are substantially larger than a public page while
# preventing one malformed project file from turning an aggregate request into
# unbounded allocation; absent optional/legacy leaves remain valid.
_MAX_DETAIL_LEAF_BYTES = 16 * 1_048_576
_ACTIVE_MISSION_STATES = frozenset({"draft", "ready", "running", "waiting_for_human", "paused", "blocked", "failed"})
_PUBLIC_MISSION_STATES = {
    "draft": "draft", "ready": "ready", "running": "active",
    "waiting_for_human": "needs_human", "paused": "paused", "blocked": "needs_human",
    "completed": "completed", "failed": "stopped", "archived": "archived",
}
_ROLE_PERMISSIONS = {
    "viewer": ("view_mission",),
    "member": ("view_mission", "message", "assign_work"),
    "reviewer": ("view_mission", "message", "assign_work", "review_work"),
    "approver": ("view_mission", "message", "assign_work", "review_work"),
    "owner": ("view_mission", "message", "assign_work", "review_work", "decide_checkpoint", "manage_mission", "manage_crew", "manage_automation", "approve_plan"),
    "admin": ("view_mission", "message", "assign_work", "review_work", "decide_checkpoint", "manage_mission", "manage_crew", "manage_automation", "approve_plan"),
}
_TERMINAL_TASK_STATES = frozenset({"done", "failed", "cancelled"})
_TASK_NEXT_STATES: dict[str, tuple[str, ...]] = {
    "proposed": ("ready", "cancelled"),
    "ready": ("working", "blocked", "cancelled"),
    "working": ("in_review", "blocked", "failed", "cancelled"),
    "in_review": ("working", "failed", "cancelled"),
    "blocked": ("ready", "working", "failed", "cancelled"),
    "failed": ("ready", "cancelled"),
}
_OPAQUE_FILE_ID = re.compile(r"^file_[0-9a-f]{40}$")


class AttentionRevisionConflict(ValueError):
    """Stable private-receipt CAS conflict for the route boundary."""


def _safe_text(value: Any, fallback: str = "") -> str:
    if not isinstance(value, str):
        return fallback
    if re.search(
        r"(?:https?://|localhost|[A-Za-z]:[\\/]|/(?:private|tmp|var|Users|home|etc|app)/|"
        r"traceback|stack trace|\berrno\b|\b(?:exception|error|codex|model|provider|runtime|mcp|graph|worker|host|path)\b|raw\s+(?:tool|exception))",
        value,
        re.IGNORECASE,
    ):
        return fallback
    try:
        clean_public_mapping({"value": value})
    except ValueError:
        return fallback
    return value[:400]


def _fd_flags(*, directory: bool = False) -> int:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory:
        flags |= getattr(os, "O_DIRECTORY", 0)
    return flags


def _read_regular_json(parent_fd: int, name: str) -> Mapping[str, Any] | None:
    """Read one bounded, no-follow JSON leaf; malformed discovery is absent."""
    try:
        descriptor = os.open(name, _fd_flags(), dir_fd=parent_fd)
    except OSError:
        return None
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_DISCOVERY_BYTES:
            return None
        chunks: list[bytes] = []
        remaining = _MAX_DISCOVERY_BYTES + 1
        while remaining:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if remaining == 0:
            return None
        value = json.loads(b"".join(chunks).decode("utf-8"))
        return value if isinstance(value, Mapping) else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        os.close(descriptor)


def _safe_detail_leaf(root: str | Path, parts: Sequence[str]) -> bool:
    """Accept only an absent or bounded regular no-follow detail leaf.

    The repositories remain the authoritative parsers.  This is a preflight
    for aggregate publication, so malformed/symlink/nonregular detail leaves
    fail closed without teaching the projection about their data formats.
    """
    if not parts or any(not _valid_scope_id(part) and part not in {
        "missions", "collaboration", "state.json", "room.json", "tasks.json", "events.jsonl", "conversation_state.json",
    } for part in parts):
        return False
    try:
        descriptor = os.open(Path(root), _fd_flags(directory=True))
    except OSError:
        return False
    try:
        for part in parts[:-1]:
            child = _open_child_dir(descriptor, part)
            if child is None:
                # An absent legacy detail directory means no aggregate source.
                return False
            os.close(descriptor)
            descriptor = child
        try:
            leaf = os.open(parts[-1], _fd_flags(), dir_fd=descriptor)
        except FileNotFoundError:
            return True
        except OSError:
            return False
        try:
            info = os.fstat(leaf)
            return stat.S_ISREG(info.st_mode) and info.st_size <= _MAX_DETAIL_LEAF_BYTES
        except OSError:
            return False
        finally:
            os.close(leaf)
    finally:
        os.close(descriptor)


def _open_child_dir(parent_fd: int, name: str) -> int | None:
    try:
        descriptor = os.open(name, _fd_flags(directory=True), dir_fd=parent_fd)
    except OSError:
        return None
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            return None
    except OSError:
        os.close(descriptor)
        return None
    return descriptor


def _valid_scope_id(value: Any) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value)) and value not in {".", ".."}


def discover_authorized_rooms(collaboration_repository: Any, *, tenant_id: str, human_id: str) -> list[tuple[str, str, int]]:
    """Discover current room membership before any Mission detail is loaded.

    This deliberately uses descriptor-relative no-follow reads: unsafe,
    malformed, oversized, or scope-mismatched discovery leaves are simply not
    aggregate candidates.
    """
    if not _valid_scope_id(tenant_id) or not _valid_scope_id(human_id):
        return []
    collaboration_root = collaboration_repository.root
    try:
        root_fd = os.open(Path(collaboration_root), _fd_flags(directory=True))
    except OSError:
        return []
    try:
        tenant_fd = _open_child_dir(root_fd, tenant_id)
        if tenant_fd is None:
            return []
        try:
            candidates = sorted(name for name in os.listdir(tenant_fd) if _valid_scope_id(name))
            result: list[tuple[str, str, int]] = []
            for project_id in candidates:
                project_fd = _open_child_dir(tenant_fd, project_id)
                if project_fd is None:
                    continue
                try:
                    collaboration_fd = _open_child_dir(project_fd, "collaboration")
                    if collaboration_fd is None:
                        continue
                    try:
                        room = _read_regular_json(collaboration_fd, "room.json")
                    finally:
                        os.close(collaboration_fd)
                    if (not isinstance(room, Mapping) or room.get("tenant_id") != tenant_id
                            or room.get("project_id") != project_id or not isinstance(room.get("members"), list)):
                        continue
                    try:
                        visible_room = collaboration_repository.visible_room(tenant_id, project_id)
                    except Exception:
                        continue
                    member = collaboration_repository.visible_member(visible_room, human_id)
                    role = member.role if member is not None else None
                    if role not in _ROLE_PERMISSIONS:
                        continue
                    result.append((project_id, role, len(visible_room.members)))
                finally:
                    os.close(project_fd)
            return result
        finally:
            os.close(tenant_fd)
    except OSError:
        return []
    finally:
        os.close(root_fd)


def _permissions(role: str, mission: Mapping[str, Any], human_id: str) -> list[str]:
    permissions = list(_ROLE_PERMISSIONS.get(role, ()))
    if human_id == mission.get("owner_id") or human_id in mission.get("verifier_ids", []):
        permissions.append("verify_output")
    return permissions


def project_mission_summaries(
    mission_repository: Any, collaboration_repository: Any, *, tenant_id: str, human_id: str, state: str = "active",
) -> list[dict[str, Any]]:
    """Return only room-authorized aggregate summaries, newest first."""
    if state not in {"active", "all"}:
        raise ValueError("invalid mission state")
    rows: list[dict[str, Any]] = []
    # Discovery is the authorization boundary. Only these projects may load
    # Mission state or its count collections.
    for project_id, role, human_count in discover_authorized_rooms(
        collaboration_repository, tenant_id=tenant_id, human_id=human_id,
    ):
        if not (
            _safe_detail_leaf(mission_repository.root, (tenant_id, project_id, "missions", "state.json"))
            and _safe_detail_leaf(collaboration_repository.root, (tenant_id, project_id, "collaboration", "room.json"))
            and _safe_detail_leaf(collaboration_repository.root, (tenant_id, project_id, "collaboration", "tasks.json"))
        ):
            continue
        try:
            mission = mission_repository.get_mission(tenant_id, project_id)
            persisted = mission.get("status")
            public_state = _PUBLIC_MISSION_STATES.get(persisted)
            if public_state is None or (state == "active" and persisted not in _ACTIVE_MISSION_STATES):
                continue
            room = collaboration_repository.get_room(tenant_id, project_id)
            member = collaboration_repository.visible_member(room, human_id)
            if member is None or member.role != role:
                continue
            tasks = collaboration_repository.list_tasks(tenant_id, project_id)
            agents = mission_repository.list_collection(tenant_id, project_id, "agents")
            deliverables = mission_repository.list_collection(tenant_id, project_id, "deliverables")
            approvals = mission_repository.list_collection(tenant_id, project_id, "approvals")
        except Exception:
            continue
        # Membership is rechecked under the room lock immediately before this
        # public row is published.  The descriptor pass remains deliberately
        # before Mission detail loading, so a revoked human never retains a
        # stale aggregate during a concurrent room update.
        try:
            with collaboration_repository.room_lock(tenant_id, project_id) as current_room:
                current_member = collaboration_repository.visible_member(current_room, human_id)
                if current_member is None or current_member.role != role:
                    continue
                human_count = len(collaboration_repository.visible_members(current_room))
        except Exception:
            continue
        rows.append(serialize_mission_summary({
            "id": project_id,
            "title": _safe_text(mission.get("title"), "Mission"),
            "outcome_summary": _safe_text(mission.get("objective"), "Mission outcome"),
            "public_state": public_state,
            "updated_at": _safe_text(mission.get("updated_at")),
            "human_count": human_count,
            "agent_count": len(agents),
            "active_work_count": sum(1 for task in tasks if str(getattr(task, "state", "")) not in _TERMINAL_TASK_STATES),
            "needs_human_count": sum(1 for value in approvals.values() if value.get("status") == "pending")
                + sum(1 for value in deliverables.values() if value.get("state") == "awaiting_verification"),
            "verified_output_count": sum(1 for value in deliverables.values() if value.get("state") == "verified"),
            "current_human_permissions": _permissions(role, mission, human_id),
        }))
    return _ordered(rows, "updated_at")


def _work_state(source_type: str, value: Mapping[str, Any]) -> str:
    state = str(value.get("state") or value.get("status") or "")
    if source_type == "task":
        if state == "blocked" or (state in {"proposed", "ready"} and not value.get("owner_id")):
            return "needs_you"
        if state in {"proposed", "ready", "working"}:
            return "in_progress"
        if state == "in_review":
            return "ready_for_review"
        if state == "done":
            return "done"
        return "stopped"
    if source_type == "run":
        if state == "awaiting_approval":
            return "needs_you"
        if state in {"queued", "preparing", "running", "verifying"}:
            return "in_progress"
        if state == "succeeded":
            return "done"
        return "stopped"
    if source_type == "approval":
        if state == "pending":
            return "needs_you"
        if state in {"approved", "consumed"}:
            return "done"
        return "stopped"
    if source_type == "output":
        if state == "awaiting_verification":
            return "ready_for_review"
        if state in {"verified", "published"}:
            return "done"
        if state == "changes_requested":
            return "stopped"
        return "in_progress"
    return "stopped"


def _work_actions(
    source_type: str, value: Mapping[str, Any], *, role: str, human_id: str, mission: Mapping[str, Any],
) -> list[str]:
    actions = ["open"]
    state = str(value.get("state") or value.get("status") or "")
    if role == "viewer":
        return actions
    if source_type == "task":
        if value.get("owner_id") is None and state not in _TERMINAL_TASK_STATES and role in {"owner", "admin"}:
            actions.append("claim_work")
        elif value.get("owner_id") == human_id and state not in _TERMINAL_TASK_STATES:
            actions.append("update_work")
        if (
            state == "in_review"
            and value.get("owner_id") != human_id
            and role in {"reviewer", "approver", "owner", "admin"}
        ):
            actions.append("review_work")
    elif source_type == "approval" and state == "pending" and role in {"owner", "admin"}:
        actions.append("decide_checkpoint")
    elif source_type == "output" and state == "awaiting_verification":
        if value.get("producer_id") != human_id and (
            human_id == mission.get("owner_id") or human_id in mission.get("verifier_ids", [])
        ):
            actions.append("verify_output")
    elif source_type == "run" and state == "failed" and role in {"owner", "admin"}:
        approved = mission.get("approved_contract_revision")
        actions.append(
            "retry_work"
            if isinstance(approved, str) and approved and value.get("contract_revision") == approved
            else "review_plan"
        )
    return actions


_ACTION_TARGET_KINDS = {
    "claim_work": "task",
    "update_work": "task",
    "review_work": "task",
    "decide_checkpoint": "approval",
    "verify_output": "output",
    "retry_work": "run",
    "review_plan": "plan",
}


def _positive_revision(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        return None
    return value


def _action_target(
    *, kind: str, source_id: Any, revision: Any, run_revision: Any | None = None,
    next_states: Any | None = None, file_id: Any | None = None,
) -> dict[str, Any] | None:
    if kind not in set(_ACTION_TARGET_KINDS.values()) or not _valid_scope_id(source_id):
        return None
    safe_revision = _positive_revision(revision)
    if safe_revision is None:
        return None
    target: dict[str, Any] = {"kind": kind, "id": source_id, "revision": safe_revision}
    if run_revision is not None:
        safe_run_revision = _positive_revision(run_revision)
        if safe_run_revision is None:
            return None
        target["run_revision"] = safe_run_revision
    if next_states is not None:
        if not isinstance(next_states, (list, tuple)):
            return None
        safe_next_states = tuple(next_states)
        if safe_next_states not in set(_TASK_NEXT_STATES.values()):
            return None
        target["next_states"] = list(safe_next_states)
    if file_id is not None:
        if not isinstance(file_id, str) or not _OPAQUE_FILE_ID.fullmatch(file_id):
            return None
        target["file_id"] = file_id
    return target


def _work_action_targets(
    actions: Sequence[str], *, source_type: str, source_raw: Mapping[str, Any],
    effective_type: str, effective_raw: Mapping[str, Any], runs: Mapping[str, Any],
    mission: Mapping[str, Any], verify_file_id: str | None = None,
) -> dict[str, dict[str, Any]]:
    """Bind every executable action to its exact public record revision."""
    targets: dict[str, dict[str, Any]] = {}
    for action in actions:
        target: dict[str, Any] | None = None
        if action in {"claim_work", "update_work", "review_work"} and source_type == "task":
            target = _action_target(
                kind="task", source_id=source_raw.get("id"), revision=source_raw.get("revision"),
                next_states=(
                    _TASK_NEXT_STATES.get(str(source_raw.get("state") or ""))
                    if action == "update_work" else None
                ),
            )
        elif action == "decide_checkpoint" and effective_type == "approval":
            run_id = effective_raw.get("run_id")
            linked_run = runs.get(run_id) if isinstance(run_id, str) and _valid_scope_id(run_id) else None
            if isinstance(linked_run, Mapping):
                target = _action_target(
                    kind="approval", source_id=effective_raw.get("id"),
                    revision=effective_raw.get("revision"), run_revision=linked_run.get("revision"),
                )
        elif action == "verify_output" and effective_type == "output":
            target = _action_target(
                kind="output", source_id=effective_raw.get("id"),
                revision=effective_raw.get("version"),
                file_id=verify_file_id,
            )
        elif action == "retry_work" and effective_type == "run":
            target = _action_target(
                kind="run", source_id=effective_raw.get("id"), revision=effective_raw.get("revision"),
            )
        elif action == "review_plan" and effective_type == "run":
            target = _action_target(
                kind="plan", source_id=effective_raw.get("contract_revision"), revision=mission.get("revision"),
            )
        if target is not None:
            targets[action] = target
    return targets


def project_work_items(
    mission_repository: Any,
    collaboration_repository: Any,
    *,
    tenant_id: str,
    human_id: str,
    assignment_visible: Callable[[str, str, str], Any] | None = None,
    output_file_identity: Callable[[str, str], str | None] | None = None,
) -> list[dict[str, Any]]:
    """Project each authorized source record once with server-owned actions.

    ``assignment_visible`` is the coordinator's COMPLETE-journal admission
    check.  Ordinary tasks and runs have no transaction tag and remain
    backwards compatible.
    """
    result: list[dict[str, Any]] = []
    for project_id, discovered_role, _ in discover_authorized_rooms(
        collaboration_repository, tenant_id=tenant_id, human_id=human_id,
    ):
        if not (
            _safe_detail_leaf(mission_repository.root, (tenant_id, project_id, "missions", "state.json"))
            and _safe_detail_leaf(collaboration_repository.root, (tenant_id, project_id, "collaboration", "room.json"))
            and _safe_detail_leaf(collaboration_repository.root, (tenant_id, project_id, "collaboration", "tasks.json"))
        ):
            continue
        try:
            room = collaboration_repository.get_room(tenant_id, project_id)
            member = collaboration_repository.visible_member(room, human_id)
            if member is None or member.role != discovered_role:
                continue
            mission = mission_repository.get_mission(tenant_id, project_id)
            tasks = collaboration_repository.list_tasks(tenant_id, project_id)
            runs = mission_repository.list_collection(tenant_id, project_id, "runs")
            approvals = mission_repository.list_collection(tenant_id, project_id, "approvals")
            outputs = mission_repository.list_collection(tenant_id, project_id, "deliverables")
            agents = mission_repository.list_collection(tenant_id, project_id, "agents")
        except Exception:
            continue
        visible_members = collaboration_repository.visible_members(room)
        member_names = {item.actor_id: item.display_name for item in visible_members}
        agent_names = {key: str(value.get("name") or "Mission agent") for key, value in agents.items() if isinstance(value, Mapping)}

        assignment_results: dict[str, Any] = {}

        def assignment_result(transaction_id: Any, run_id: str = "") -> Any | None:
            if not isinstance(transaction_id, str) or not transaction_id or assignment_visible is None:
                return None
            cache_key = f"{transaction_id}\x1f{run_id}"
            if cache_key not in assignment_results:
                assignment_results[cache_key] = assignment_visible(project_id, transaction_id, run_id)
            admitted = assignment_results[cache_key]
            if not (
                admitted is not None
                and getattr(admitted, "transaction_id", None) == transaction_id
                and _valid_scope_id(str(getattr(admitted, "task_id", "")))
                and _valid_scope_id(str(getattr(admitted, "run_id", "")))
                and (not run_id or getattr(admitted, "run_id", None) == run_id)
            ):
                return None
            return admitted

        def linked_run_id(raw: Mapping[str, Any]) -> str | None:
            candidates: set[str] = set()
            source_ref = raw.get("source_ref")
            if isinstance(source_ref, str):
                match = re.fullmatch(r"mission/run/([A-Za-z0-9][A-Za-z0-9_.-]{0,127})(?:/[^/]+/[A-Za-z0-9][A-Za-z0-9_.-]{0,127})?", source_ref)
                if match and match.group(1) in runs:
                    candidates.add(match.group(1))
            evidence_values = raw.get("validation_evidence")
            for evidence in evidence_values if isinstance(evidence_values, (list, tuple)) else ():
                if isinstance(evidence, Mapping):
                    evidence_run = evidence.get("run_id")
                    if isinstance(evidence_run, str) and evidence_run in runs and _valid_scope_id(evidence_run):
                        candidates.add(evidence_run)
            return next(iter(candidates)) if len(candidates) == 1 else None

        rows: list[tuple[str, Mapping[str, Any]]] = []
        admitted_run_ids: set[str] = set()
        task_run_ids: dict[str, str] = {}
        for task in tasks:
            raw = task.to_dict()
            activity_values = raw.get("activity")
            transaction_id = next(
                (
                    item.get("transaction_id") for item in (
                        activity_values if isinstance(activity_values, (list, tuple)) else ()
                    ) if isinstance(item, Mapping) and item.get("transaction_id")
                ),
                None,
            )
            if not transaction_id:
                rows.append(("task", raw))
                continue
            admitted = assignment_result(transaction_id)
            if admitted is not None and getattr(admitted, "task_id", None) == task.id:
                rows.append(("task", raw))
                admitted_run_id = str(getattr(admitted, "run_id"))
                admitted_run_ids.add(admitted_run_id)
                task_run_ids[task.id] = admitted_run_id
        for source_id, raw in runs.items():
            if not isinstance(raw, Mapping) or raw.get("status") == "pending_commit":
                continue
            transaction_id = raw.get("assignment_transaction_id")
            if transaction_id:
                # A COMPLETE conversation assignment has one top-level Work
                # identity: its collaboration Task.  The linked Run remains
                # durable for the Work detail/history drawer, but projecting it
                # here would manufacture a duplicate assignment card.
                if assignment_result(transaction_id, str(source_id)) is None:
                    continue
                continue
            rows.append(("run", raw))
        rows.extend(
            ("approval", raw) for raw in approvals.values()
            if isinstance(raw, Mapping) and not (
                isinstance(raw.get("run_id"), str)
                and _valid_scope_id(raw.get("run_id"))
                and raw.get("run_id") in admitted_run_ids
            )
        )
        rows.extend(
            ("output", raw) for raw in outputs.values()
            if isinstance(raw, Mapping) and linked_run_id(raw) not in admitted_run_ids
        )

        project_rows: list[dict[str, Any]] = []
        task_rows = {task.id: task.to_dict() for task in tasks}
        for source_type, raw in rows:
            source_id = str(raw.get("id") or "")
            if not _valid_scope_id(source_id):
                continue
            effective_type = source_type
            effective_raw = raw
            linked_run = runs.get(task_run_ids.get(source_id, "")) if source_type == "task" else None
            public_task_raw = raw
            if source_type == "task" and raw.get("owner_id") not in member_names:
                public_task_raw = {**raw, "owner_id": None}
                effective_raw = public_task_raw
            if isinstance(linked_run, Mapping):
                linked_outputs = [
                    value for value in outputs.values()
                    if isinstance(value, Mapping) and linked_run_id(value) == linked_run.get("id")
                ]
                linked_approvals = [
                    value for value in approvals.values()
                    if isinstance(value, Mapping) and value.get("run_id") == linked_run.get("id")
                ]
                awaiting_output = next(
                    (value for value in linked_outputs if value.get("state") == "awaiting_verification"), None,
                )
                verified_output = next(
                    (value for value in linked_outputs if value.get("state") in {"verified", "published"}), None,
                )
                pending_approval = next(
                    (value for value in linked_approvals if value.get("status") == "pending"), None,
                )
                if linked_run.get("status") == "failed":
                    effective_type, effective_raw = "run", linked_run
                elif awaiting_output is not None:
                    effective_type, effective_raw = "output", awaiting_output
                elif pending_approval is not None:
                    effective_type, effective_raw = "approval", pending_approval
                elif linked_run.get("status") == "succeeded" and verified_output is not None:
                    effective_type, effective_raw = "output", verified_output
                else:
                    effective_type, effective_raw = "run", linked_run
            if source_type == "task":
                title = public_task_raw.get("title")
                summary = public_task_raw.get("objective")
                assignee_id = public_task_raw.get("owner_id")
                assignee_kind = "human"
                if isinstance(linked_run, Mapping):
                    assigned_values = linked_run.get("assigned_agent_ids")
                    assigned_ids = assigned_values if isinstance(assigned_values, (list, tuple)) else ()
                    assignee_id = linked_run.get("current_agent_id") or next(
                        (item for item in assigned_ids if isinstance(item, str)), None,
                    )
                    assignee_kind = "agent"
            elif source_type == "run":
                title, summary = "Mission work", "Agents are carrying this Mission forward."
                assigned_values = raw.get("assigned_agent_ids")
                assigned_ids = assigned_values if isinstance(assigned_values, (list, tuple)) else ()
                assignee_id = raw.get("current_agent_id") or next(
                    (item for item in assigned_ids if isinstance(item, str)), None,
                )
                assignee_kind = "agent"
            elif source_type == "approval":
                title, summary, assignee_id = "Decision needed", "A human decision is needed before work continues.", None
                assignee_kind = "human"
            else:
                title, summary, assignee_id = raw.get("name"), "Output ready for review." if raw.get("state") == "awaiting_verification" else "Mission output", raw.get("producer_id")
                assignee_kind = "agent"
            assignee = None
            if isinstance(assignee_id, str) and _valid_scope_id(assignee_id):
                if assignee_kind != "human" or assignee_id in member_names:
                    display = member_names.get(assignee_id) if assignee_kind == "human" else agent_names.get(assignee_id)
                    assignee = {"id": assignee_id, "kind": assignee_kind, "display_name": _safe_text(display, "")}
            allowed_actions = _work_actions(
                effective_type, effective_raw, role=member.role, human_id=human_id, mission=mission,
            )
            verify_file_id: str | None = None
            if "verify_output" in allowed_actions and effective_type == "output" and output_file_identity is not None:
                output_id = effective_raw.get("id")
                if isinstance(output_id, str) and _valid_scope_id(output_id):
                    try:
                        verify_file_id = output_file_identity(project_id, output_id)
                    except Exception:
                        verify_file_id = None
            projected = serialize_work_item({
                "source_type": source_type,
                "source_id": source_id,
                "mission_id": project_id,
                "revision": raw.get("revision", 1),
                "title": _safe_text(title, "Mission work"),
                "summary": _safe_text(summary, "Mission work"),
                "state": _work_state(effective_type, effective_raw),
                "assignee": assignee,
                "created_at": _safe_text(raw.get("created_at")),
                "updated_at": _safe_text(
                    effective_raw.get("updated_at") or raw.get("updated_at") or raw.get("created_at"),
                ),
                "allowed_actions": allowed_actions,
                "action_targets": _work_action_targets(
                    allowed_actions,
                    source_type=source_type,
                    source_raw=raw,
                    effective_type=effective_type,
                    effective_raw=effective_raw,
                    runs=runs,
                    mission=mission,
                    verify_file_id=verify_file_id,
                ),
            })
            project_rows.append(projected)

        # Recheck under the room lock immediately before public publication.
        try:
            with collaboration_repository.room_lock(tenant_id, project_id) as current_room:
                current_member = collaboration_repository.visible_member(current_room, human_id)
                if current_member is None or current_member.role != discovered_role:
                    continue
                current_human_names = {
                    item.actor_id: item.display_name
                    for item in collaboration_repository.visible_members(current_room)
                }
                current_human_ids = set(current_human_names)
                for projected in project_rows:
                    source_id = projected.get("source_id")
                    task_raw = task_rows.get(source_id) if isinstance(source_id, str) else None
                    if isinstance(task_raw, Mapping) and source_id not in task_run_ids:
                        public_task = task_raw if task_raw.get("owner_id") in current_human_ids else {
                            **task_raw, "owner_id": None,
                        }
                        actions = _work_actions(
                            "task", public_task, role=current_member.role,
                            human_id=human_id, mission=mission,
                        )
                        projected["state"] = _work_state("task", public_task)
                        projected["allowed_actions"] = actions
                        owner_id = public_task.get("owner_id")
                        projected["assignee"] = (
                            {
                                "id": owner_id,
                                "kind": "human",
                                "display_name": _safe_text(current_human_names.get(owner_id, ""), ""),
                            }
                            if isinstance(owner_id, str) and _valid_scope_id(owner_id)
                            else None
                        )
                        projected["action_targets"] = _work_action_targets(
                            actions,
                            source_type="task",
                            source_raw=task_raw,
                            effective_type="task",
                            effective_raw=public_task,
                            runs=runs,
                            mission=mission,
                        )
                    assignee = projected.get("assignee")
                    if (
                        isinstance(assignee, Mapping)
                        and assignee.get("kind") == "human"
                        and assignee.get("id") not in current_human_ids
                    ):
                        projected["assignee"] = None
                result.extend(project_rows)
        except Exception:
            continue
    return _ordered(result, "updated_at")


def mark_attention_read(
    repository: Any, *, tenant_id: str, project_id: str, human_id: str, event_id: str, expected_revision: int,
    clock: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """CAS-mark only one human's private receipt without touching its source."""
    if not _valid_scope_id(event_id) or isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise ValueError("invalid attention receipt")
    timestamp = (clock or (lambda: datetime.now(UTC).isoformat()))()
    def mutate(state: dict[str, Any]) -> dict[str, Any]:
        room = repository.get_room(tenant_id, project_id)
        if repository.visible_member(room, human_id) is None:
            raise PermissionError("membership_required")
        receipts = state.setdefault("attention_receipts", {})
        key = f"{event_id}:{human_id}"
        prior = receipts.get(key)
        actual = int(prior.get("revision", 0)) if isinstance(prior, Mapping) else 0
        if actual != expected_revision:
            raise AttentionRevisionConflict("revision_conflict")
        result = {"event_id": event_id, "human_id": human_id, "revision": actual + 1, "read_at": timestamp}
        receipts[key] = result
        return {"event_id": event_id, "read": True, "revision": actual + 1, "read_at": timestamp}
    return repository.mutate_conversation_state(tenant_id, project_id, mutate)


def _attention_id(kind: str, source_event_id: str, human_id: str) -> str:
    digest = hashlib.sha256(f"{kind}\x1f{source_event_id}\x1f{human_id}".encode("utf-8")).hexdigest()[:32]
    return f"attention_{digest}"


def _receipt(state: Mapping[str, Any], attention_id: str, human_id: str) -> tuple[bool, int, str | None]:
    row = state.get("attention_receipts", {}).get(f"{attention_id}:{human_id}") if isinstance(state.get("attention_receipts"), Mapping) else None
    if not isinstance(row, Mapping):
        return False, 0, None
    revision = row.get("revision")
    return True, revision if isinstance(revision, int) and revision >= 0 else 0, row.get("read_at") if isinstance(row.get("read_at"), str) else None


def _attention_item(
    *, mission_id: str, human_id: str, kind: str, source_event_id: str, subject_id: str, priority: int,
    actionable: bool, title: str, summary: str, created_at: str, updated_at: str, deep_link: str, allowed_actions: Sequence[str],
    receipts: Mapping[str, Any],
) -> dict[str, Any]:
    attention_id = _attention_id(kind, source_event_id, human_id)
    read, revision, read_at = _receipt(receipts, attention_id, human_id)
    value = serialize_attention_item({
        "id": attention_id, "mission_id": mission_id, "type": kind, "title": title,
        "summary": summary, "source_event_id": source_event_id, "subject_id": subject_id,
        "priority": priority, "actionable": actionable, "read": read, "revision": revision,
        "created_at": created_at, "updated_at": read_at or updated_at, "deep_link": deep_link,
        "allowed_actions": list(allowed_actions),
    })
    return value


def project_attention_items(
    mission_repository: Any, collaboration_repository: Any, *, tenant_id: str, human_id: str,
    workspace_for_project: Callable[[str], str | Path] | None = None,
) -> list[dict[str, Any]]:
    """Project the W2 attention sources for current room members only."""
    result: list[dict[str, Any]] = []
    for project_id, role, _ in discover_authorized_rooms(collaboration_repository, tenant_id=tenant_id, human_id=human_id):
        if not (
            _safe_detail_leaf(mission_repository.root, (tenant_id, project_id, "missions", "state.json"))
            and _safe_detail_leaf(collaboration_repository.root, (tenant_id, project_id, "collaboration", "room.json"))
            and _safe_detail_leaf(collaboration_repository.root, (tenant_id, project_id, "collaboration", "tasks.json"))
            and _safe_detail_leaf(collaboration_repository.root, (tenant_id, project_id, "collaboration", "events.jsonl"))
            and _safe_detail_leaf(collaboration_repository.root, (tenant_id, project_id, "collaboration", "conversation_state.json"))
        ):
            continue
        try:
            # This is an authorization snapshot for both the human-facing
            # display name and the publication recheck below.  Discovery is
            # intentionally separate because it must happen before Mission
            # detail loading.
            room = collaboration_repository.get_room(tenant_id, project_id)
            member = collaboration_repository.visible_member(room, human_id)
            if member is None or member.role != role:
                continue
            visible_members = collaboration_repository.visible_members(room)
            mission = mission_repository.get_mission(tenant_id, project_id)
            tasks = collaboration_repository.list_tasks(tenant_id, project_id)
            events = collaboration_repository.list_events(tenant_id, project_id)
            approvals = mission_repository.list_collection(tenant_id, project_id, "approvals")
            deliverables = mission_repository.list_collection(tenant_id, project_id, "deliverables")
            runs = mission_repository.list_collection(tenant_id, project_id, "runs")
            mission_events = mission_repository.list_collection(tenant_id, project_id, "events")
            receipts = collaboration_repository.conversation_state(tenant_id, project_id)
        except Exception:
            continue
        project_row_start = len(result)
        mention_actor_ids: dict[str, str] = {}
        task_events: dict[str, list[Any]] = {}
        visible_member_ids = {member.actor_id for member in visible_members}
        for event in events:
            if event.action in {"task.created", "task.claimed"} and event.task_id:
                task_events.setdefault(event.task_id, []).append(event)
            if event.action == "comment.created" and human_id in (event.payload.get("mention_ids") or []):
                source = event.id
                actor_name = _safe_text(next(
                    (member.display_name for member in visible_members if member.actor_id == event.actor_id),
                    "",
                )) or "A human"
                projected_mention = _attention_item(
                    mission_id=project_id, human_id=human_id, kind="mention", source_event_id=source,
                    subject_id=str(event.payload.get("target_id") or project_id), priority=50, actionable=False,
                    title=f"{actor_name} mentioned you",
                    summary=_safe_text(event.payload.get("body"), "You were mentioned in this Mission."),
                    created_at=event.timestamp, updated_at=event.timestamp,
                    deep_link=f"/missions/{project_id}?tab=conversation&attention={_attention_id('mention', source, human_id)}",
                    allowed_actions=("open",), receipts=receipts,
                )
                result.append(projected_mention)
                if isinstance(event.actor_id, str):
                    mention_actor_ids[projected_mention["id"]] = event.actor_id
        for task in tasks:
            task_id = task.id
            state = str(task.state)
            closed = state in _TERMINAL_TASK_STATES
            public_owner_id = task.owner_id if task.owner_id in visible_member_ids else None
            assignments = [event for event in task_events.get(task_id, []) if event.payload.get("assignee_id") == human_id]
            assignments.sort(key=lambda event: (event.timestamp, event.id))
            if assignments or public_owner_id == human_id:
                # Each durable source event is a separate historic assignment;
                # only the latest event matching current ownership is live.
                latest = sorted(task_events.get(task_id, []), key=lambda item: (item.timestamp, item.id))[-1] if task_events.get(task_id) else None
                for event in assignments or [None]:
                    source = event.id if event else f"task:{task_id}"
                    current_assignment = public_owner_id == human_id and (event is None or latest is event)
                    result.append(_attention_item(
                        mission_id=project_id, human_id=human_id, kind="assignment", source_event_id=source, subject_id=task_id,
                        priority=30, actionable=current_assignment and not closed, title="Work assigned to you", summary=_safe_text(task.title, "Mission work"),
                        created_at=event.timestamp if event else task.created_at, updated_at=task.updated_at,
                        deep_link=f"/missions/{project_id}?tab=work&item={task_id}",
                        allowed_actions=("open", "update_work") if current_assignment and not closed else ("open",), receipts=receipts,
                    ))
            if role in {"owner", "admin"}:
                created = next((event for event in task_events.get(task_id, []) if event.action == "task.created"), None)
                activity = getattr(task, "activity", [])
                legacy_claimed = isinstance(activity, list) and any(isinstance(item, Mapping) and item.get("action") == "claimed" for item in activity)
                was_unassigned = (
                    (created is not None and created.payload.get("assignee_id") is None)
                    or public_owner_id is None
                    or (created is None and legacy_claimed)
                )
                if was_unassigned:
                    source = created.id if created is not None else f"task:{task_id}"
                    created_at = created.timestamp if created is not None else task.created_at
                    result.append(_attention_item(
                        mission_id=project_id, human_id=human_id, kind="unassigned_work", source_event_id=source, subject_id=task_id,
                        priority=20, actionable=public_owner_id is None and not closed, title="Work needs an owner",
                        summary=_safe_text(task.title, "Mission work"), created_at=created_at,
                        updated_at=task.updated_at, deep_link=f"/missions/{project_id}?tab=work&item={task_id}",
                        allowed_actions=("open", "claim_work") if public_owner_id is None and not closed else ("open",), receipts=receipts,
                    ))
        if role in {"owner", "admin"}:
            for approval_id, approval in approvals.items():
                source = f"approval:{approval_id}"
                result.append(_attention_item(
                    mission_id=project_id, human_id=human_id, kind="decision_required", source_event_id=source, subject_id=str(approval_id),
                    priority=10, actionable=approval.get("status") == "pending", title="Decision needed", summary="A Mission decision needs a human review.",
                    created_at=_safe_text(approval.get("created_at")), updated_at=_safe_text(approval.get("updated_at") or approval.get("created_at")),
                    deep_link=f"/missions/{project_id}?tab=conversation&approval={approval_id}",
                    allowed_actions=("open", "decide_checkpoint") if approval.get("status") == "pending" else ("open",), receipts=receipts,
                ))
            for run_id, run in runs.items():
                durable_failures = [
                    event for event in mission_events.values()
                    if isinstance(event, Mapping) and event.get("run_id") == run_id
                    and event.get("type") in {"agent_failed", "recovery_required"}
                ]
                if not durable_failures:
                    continue
                source = f"run:{run_id}"
                retry_action = "review_plan"
                if workspace_for_project is not None:
                    try:
                        from simulacra.operation_graph import OperationGraphStore
                        store = OperationGraphStore(workspace_for_project(project_id), tenant_id=tenant_id, project_id=project_id)
                        current = store.current_revision()
                        if current is not None:
                            store.require_approved_revision(current.revision_hash)
                            retry_action = "retry_work"
                    except Exception:
                        pass
                result.append(_attention_item(
                    mission_id=project_id, human_id=human_id, kind="retry_required", source_event_id=source, subject_id=str(run_id),
                    priority=8, actionable=run.get("status") == "failed", title="Mission work stopped", summary="Review this Mission work before continuing.",
                    created_at=_safe_text(min((str(event.get("timestamp", "")) for event in durable_failures), default=run.get("created_at", ""))), updated_at=_safe_text(run.get("updated_at") or run.get("created_at")),
                    deep_link=f"/missions/{project_id}?tab=work&run={run_id}", allowed_actions=("open", retry_action) if run.get("status") == "failed" else ("open",), receipts=receipts,
                ))
            if workspace_for_project is not None:
                try:
                    from simulacra.operation_graph import OperationGraphStore
                    store = OperationGraphStore(workspace_for_project(project_id), tenant_id=tenant_id, project_id=project_id)
                    current = store.current_revision()
                    # The immutable revision collection is retained product
                    # history.  `list_revisions` verifies each record before
                    # it can become an attention source.
                    revisions = store.list_revisions()
                    for revision in revisions:
                        # Approval is not a boolean marker: only an exact,
                        # retained approval record for this immutable revision
                        # closes the row.
                        try:
                            store.require_approved_revision(revision.revision_hash)
                            approved = True
                        except Exception:
                            approved = False
                        actionable = current is not None and revision.revision_hash == current.revision_hash and not approved
                        source = f"mission-plan:{project_id}:{revision.revision}"
                        result.append(_attention_item(
                            mission_id=project_id, human_id=human_id, kind="plan_approval", source_event_id=source,
                            subject_id=str(revision.revision), priority=5, actionable=actionable, title="Mission plan needs approval",
                            summary="Review and approve the current Mission plan.", created_at=revision.created_at,
                            updated_at=revision.updated_at, deep_link=f"/missions/{project_id}?tab=conversation&focus=plan-approval",
                            allowed_actions=("open", "approve_plan") if actionable else ("open",), receipts=receipts,
                        ))
                except Exception:
                    pass
        if human_id == mission.get("owner_id") or human_id in mission.get("verifier_ids", []):
            for deliverable_id, deliverable in deliverables.items():
                source = f"deliverable:{deliverable_id}"
                result.append(_attention_item(
                    mission_id=project_id, human_id=human_id, kind="output_verification", source_event_id=source, subject_id=str(deliverable_id),
                    priority=15, actionable=deliverable.get("state") == "awaiting_verification", title="Output ready to verify", summary=_safe_text(deliverable.get("name"), "Mission output"),
                    created_at=_safe_text(deliverable.get("created_at")), updated_at=_safe_text(deliverable.get("updated_at") or deliverable.get("created_at")),
                    deep_link=f"/missions/{project_id}?tab=files&output={deliverable_id}", allowed_actions=("open", "verify_output") if deliverable.get("state") == "awaiting_verification" else ("open",), receipts=receipts,
                ))
        # A room can change between descriptor discovery and the Mission
        # loads.  Linearize publication at the room lock: if membership has
        # been removed, discard this entire project's un-published rows.
        try:
            with collaboration_repository.room_lock(tenant_id, project_id) as current_room:
                current_member = collaboration_repository.visible_member(current_room, human_id)
                if current_member is None or current_member.role != role:
                    del result[project_row_start:]
                else:
                    current_names = {
                        member.actor_id: member.display_name
                        for member in collaboration_repository.visible_members(current_room)
                    }
                    current_member_ids = set(current_names)
                    task_by_id = {task.id: task for task in tasks}
                    for row in result[project_row_start:]:
                        actor_id = mention_actor_ids.get(str(row.get("id") or ""))
                        if actor_id is not None:
                            actor_name = _safe_text(current_names.get(actor_id, "")) or "A human"
                            row["title"] = f"{actor_name} mentioned you"
                        task = task_by_id.get(str(row.get("subject_id") or ""))
                        if task is not None and row.get("type") in {"assignment", "unassigned_work"}:
                            public_owner_id = task.owner_id if task.owner_id in current_member_ids else None
                            closed = str(task.state) in _TERMINAL_TASK_STATES
                            if row["type"] == "assignment":
                                actionable = bool(row.get("actionable")) and public_owner_id == human_id and not closed
                                row["actionable"] = actionable
                                row["allowed_actions"] = ["open", "update_work"] if actionable else ["open"]
                            else:
                                actionable = public_owner_id is None and not closed
                                row["actionable"] = actionable
                                row["allowed_actions"] = ["open", "claim_work"] if actionable else ["open"]
        except Exception:
            del result[project_row_start:]
    # Stable mixed-order contract: priority ascending, then newest creation and ID.
    ordered = sorted(result, key=lambda row: str(row["id"]), reverse=True)
    ordered = sorted(ordered, key=lambda row: str(row["created_at"]), reverse=True)
    return sorted(ordered, key=lambda row: int(row["priority"]))


def paginate_attention(
    rows: Sequence[Mapping[str, Any]], *, endpoint: str, scope: str, limit: int = 50, cursor: str | None = None, secret: bytes | str,
) -> tuple[list[Mapping[str, Any]], str | None]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be from 1 to 100")
    ordered = sorted(rows, key=lambda row: str(row.get("id", "")), reverse=True)
    ordered = sorted(ordered, key=lambda row: str(row.get("created_at", "")), reverse=True)
    ordered = sorted(ordered, key=lambda row: int(row.get("priority", 999)))
    start = 0
    if cursor:
        boundary = decode_cursor(cursor, endpoint=endpoint, scope=scope, secret=secret)
        for index, row in enumerate(ordered):
            current = (f"{int(row.get('priority', 999))}\x1f{row.get('created_at', '')}", str(row.get("id", "")))
            if current == boundary:
                start = index + 1
                break
        else:
            raise CursorInvalidError()
    page = ordered[start:start + limit]
    if start + limit >= len(ordered) or not page:
        return page, None
    last = page[-1]
    return page, encode_cursor(
        endpoint=endpoint, scope=scope, sort_key=f"{int(last.get('priority', 999))}\x1f{last.get('created_at', '')}",
        item_id=str(last.get("id", "")), secret=secret,
    )


class CursorInvalidError(ValueError):
    """A deliberately stable public cursor failure."""

    def __init__(self) -> None:
        super().__init__("cursor_invalid")


@dataclass(frozen=True, slots=True)
class MissionSummary:
    id: str
    title: str
    outcome_summary: str
    public_state: str
    updated_at: str
    human_count: int
    agent_count: int
    active_work_count: int
    needs_human_count: int
    verified_output_count: int
    current_human_permissions: list[str]


@dataclass(frozen=True, slots=True)
class WorkItem:
    source_type: str
    source_id: str
    mission_id: str
    revision: int
    title: str
    summary: str
    state: str
    assignee: dict[str, Any] | None
    created_at: str
    updated_at: str
    allowed_actions: list[str]
    action_targets: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class AttentionItem:
    id: str
    mission_id: str
    type: str
    title: str
    summary: str
    source_event_id: str
    subject_id: str
    priority: int
    actionable: bool
    read: bool
    revision: int
    created_at: str
    updated_at: str
    deep_link: str
    allowed_actions: list[str]


_WORK_FIELDS = tuple(WorkItem.__dataclass_fields__)
_ATTENTION_FIELDS = tuple(AttentionItem.__dataclass_fields__)
_ASSIGNEE_FIELDS = ("id", "display_name", "kind", "avatar_url")
_BASE64URL = re.compile(r"^[A-Za-z0-9_-]+$")


def _mapping(value: Mapping[str, Any] | Any) -> Mapping[str, Any]:
    if isinstance(value, Mapping):
        return value
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    raise TypeError("public projection must be a mapping or DTO")


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, str) and _public_scalar(item) is not None]


def _public_scalar(value: Any) -> str | int | float | bool | None:
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        try:
            clean_public_mapping({"value": value})
        except ValueError:
            return None
        return value
    return None


def serialize_mission_summary(value: MissionSummary | Mapping[str, Any]) -> dict[str, Any]:
    row = _mapping(value)
    result = {
        field: row.get(field) if field == "current_human_permissions" else _public_scalar(row.get(field))
        for field in MissionSummary.__dataclass_fields__
    }
    result["current_human_permissions"] = _string_list(result.get("current_human_permissions"))
    return result


def serialize_work_item(value: WorkItem | Mapping[str, Any]) -> dict[str, Any]:
    row = _mapping(value)
    # Older source rows name their identity simply `id`; never expose the
    # source record whole merely to support that compatibility spelling.
    result = {
        field: row.get(field) if field in {"assignee", "allowed_actions", "action_targets"} else _public_scalar(row.get(field))
        for field in _WORK_FIELDS
    }
    result["source_id"] = _public_scalar(row.get("source_id", row.get("id")))
    # V0 records call the durable Mission identity project_id and often use
    # objective as their concise work summary.  Keep that compatibility at
    # this explicit boundary rather than inventing empty public values.
    result["mission_id"] = _public_scalar(row.get("mission_id", row.get("project_id")))
    result["summary"] = _public_scalar(row.get("summary", row.get("objective")))
    assignee = result["assignee"]
    if isinstance(assignee, Mapping):
        result["assignee"] = {
            field: scalar for field in _ASSIGNEE_FIELDS
            if (scalar := _public_scalar(assignee.get(field))) is not None
        }
    elif assignee is not None:
        # The public union never forwards an arbitrary source object.
        result["assignee"] = None
    raw_actions = _string_list(result.get("allowed_actions"))
    raw_targets = result.get("action_targets")
    target_rows = raw_targets if isinstance(raw_targets, Mapping) else {}
    allowed_actions: list[str] = []
    action_targets: dict[str, dict[str, Any]] = {}
    for action in raw_actions:
        if action == "open":
            allowed_actions.append(action)
            continue
        expected_kind = _ACTION_TARGET_KINDS.get(action)
        raw_target = target_rows.get(action)
        if expected_kind is None or not isinstance(raw_target, Mapping):
            continue
        permitted_fields = {"kind", "id", "revision"}
        if action == "decide_checkpoint":
            permitted_fields.add("run_revision")
        elif action == "update_work":
            permitted_fields.add("next_states")
        elif action == "verify_output":
            permitted_fields.add("file_id")
        if set(raw_target) - permitted_fields or raw_target.get("kind") != expected_kind:
            continue
        run_revision = raw_target.get("run_revision")
        if (action == "decide_checkpoint") != (run_revision is not None):
            continue
        next_states = raw_target.get("next_states")
        if (action == "update_work") != (next_states is not None):
            continue
        file_id = raw_target.get("file_id")
        if (action == "verify_output") != (file_id is not None):
            continue
        target = _action_target(
            kind=expected_kind,
            source_id=raw_target.get("id"),
            revision=raw_target.get("revision"),
            run_revision=run_revision,
            next_states=next_states,
            file_id=file_id,
        )
        if target is None:
            continue
        allowed_actions.append(action)
        action_targets[action] = target
    result["allowed_actions"] = allowed_actions
    result["action_targets"] = action_targets
    return result


def serialize_attention_item(value: AttentionItem | Mapping[str, Any]) -> dict[str, Any]:
    row = _mapping(value)
    result = {
        field: row.get(field) if field == "allowed_actions" else _public_scalar(row.get(field))
        for field in _ATTENTION_FIELDS
    }
    result["allowed_actions"] = _string_list(result.get("allowed_actions"))
    return result


def _b64_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64_decode(value: str) -> bytes:
    if not isinstance(value, str) or not value or len(value) > 4096 or not _BASE64URL.fullmatch(value):
        raise CursorInvalidError()
    try:
        decoded = base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        if _b64_encode(decoded) != value:
            raise CursorInvalidError()
        return decoded
    except Exception as exc:
        raise CursorInvalidError() from exc


def _cursor_key(secret: bytes | str) -> bytes:
    if isinstance(secret, str):
        key = secret.encode("utf-8")
    elif isinstance(secret, bytes):
        key = secret
    else:
        raise CursorInvalidError()
    if not key or not key.strip():
        raise CursorInvalidError()
    return key


def encode_cursor(*, endpoint: str, scope: str, sort_key: str, item_id: str, secret: bytes | str) -> str:
    try:
        key = _cursor_key(secret)
    except CursorInvalidError as exc:
        raise ValueError("cursor secret is required") from exc
    payload = json.dumps({"v": 1, "e": endpoint, "s": scope, "k": sort_key, "i": item_id}, separators=(",", ":"), sort_keys=True).encode()
    signature = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{_b64_encode(payload)}.{_b64_encode(signature)}"


def decode_cursor(cursor: str, *, endpoint: str, scope: str, secret: bytes | str) -> tuple[str, str]:
    try:
        key = _cursor_key(secret)
        payload_part, signature_part = cursor.split(".", 1)
        payload, signature = _b64_decode(payload_part), _b64_decode(signature_part)
        expected = hmac.new(key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            raise CursorInvalidError()
        text = payload.decode("utf-8")
        row = json.loads(text)
        if (not isinstance(row, dict) or set(row) != {"v", "e", "s", "k", "i"}
                or type(row.get("v")) is not int or row.get("v") != 1
                or not all(isinstance(row.get(key), str) for key in ("e", "s", "k", "i"))
                or row.get("e") != endpoint or row.get("s") != scope):
            raise CursorInvalidError()
        canonical = json.dumps(row, separators=(",", ":"), sort_keys=True, ensure_ascii=True).encode("utf-8")
        if payload != canonical:
            raise CursorInvalidError()
        sort_key, item_id = row.get("k"), row.get("i")
        return sort_key, item_id
    except CursorInvalidError:
        raise
    except Exception as exc:
        raise CursorInvalidError() from exc


def _ordered(rows: Iterable[Mapping[str, Any]], sort_field: str) -> list[Mapping[str, Any]]:
    # ISO-8601 UTC text sorts chronologically.  The ID tiebreak is explicit
    # so pages remain deterministic even when multiple writes share a clock
    # tick.  The pagination boundary itself is included in the cursor.
    return sorted(rows, key=lambda row: (str(row.get(sort_field, "")), str(row.get("id", row.get("source_id", "")))), reverse=True)


def paginate(rows: Sequence[Mapping[str, Any]], *, endpoint: str, scope: str, limit: int = 50,
             cursor: str | None = None, secret: bytes | str, sort_field: str = "updated_at") -> tuple[list[Mapping[str, Any]], str | None]:
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be from 1 to 100")
    ordered = _ordered(rows, sort_field)
    start = 0
    if cursor:
        boundary = decode_cursor(cursor, endpoint=endpoint, scope=scope, secret=secret)
        for index, row in enumerate(ordered):
            current = (str(row.get(sort_field, "")), str(row.get("id", row.get("source_id", ""))))
            if current == boundary:
                start = index + 1
                break
        else:
            # A deleted boundary cannot safely be guessed: treating it as an
            # invalid cursor avoids accidental duplication or omission.
            raise CursorInvalidError()
    page = ordered[start:start + limit]
    if start + limit >= len(ordered) or not page:
        return page, None
    last = page[-1]
    return page, encode_cursor(endpoint=endpoint, scope=scope, sort_key=str(last.get(sort_field, "")), item_id=str(last.get("id", last.get("source_id", ""))), secret=secret)
