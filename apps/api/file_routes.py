"""Opaque, authorized Mission file metadata and immutable byte reads."""

from __future__ import annotations

import hashlib
import hmac
import mimetypes
import os
import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any, Mapping
from urllib.parse import quote

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from apps.api.security import get_auth
from simulacra.collaboration import JsonCollaborationRepository
from simulacra.demo.identity import AuthContext
from simulacra.demo.paths import RUNS_DIR
from simulacra.demo.runs import project_dir
from simulacra.demo.sources import SourceFile, is_internal_source_name, list_source_files
from simulacra.missions import JsonMissionRepository
from simulacra.missions.artifacts import artifact_bytes


router = APIRouter(tags=["mission-files"])
_mission_root = RUNS_DIR / ".mission-control"
_collaboration_root = RUNS_DIR / ".cmul8-control"
_file_secret = os.environ.get("SIMULACRA_WORKPLACE_FILE_ID_SECRET", "simulacra-workplace-development-file-key").encode()
_SAFE_INLINE_MEDIA = frozenset({
    "text/plain", "text/markdown", "text/csv", "application/json", "application/pdf",
    "image/png", "image/jpeg", "image/webp", "image/gif",
})
_MAX_RANGE_BYTES = 4 * 1024 * 1024
_LEGACY_SOURCE_LIST = list_source_files
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_INTERNAL_PUBLIC_TEXT = re.compile(
    r"(?:artifact[_ -]?ref|source[_ -]?ref|(?:^|[_ .-])(?:path|host|runtime|provider|model|codex|mcp|graph|worker)(?:$|[_ .-]))",
    re.I,
)
_OUTPUT_STATES = frozenset({"draft", "validated", "awaiting_verification", "verified", "changes_requested", "published"})
_SOURCE_STATES = frozenset({"ready", "extractable", "skipped", "error"})


class _UnsafeSourceInventory(ValueError):
    pass


def _error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code, {"code": code, "message": message})


def _opaque_file_id(tenant_id: str, project_id: str, kind: str, source_id: str) -> str:
    digest = hmac.new(
        _file_secret, f"{tenant_id}\x1f{project_id}\x1f{kind}\x1f{source_id}".encode(), hashlib.sha256,
    ).hexdigest()[:40]
    return f"file_{digest}"


def output_file_id(deliverable_id: str, *, tenant_id: str, project_id: str) -> str:
    """Stable opaque ID for a durable output; never includes its file ref."""
    return _opaque_file_id(tenant_id, project_id, "output", deliverable_id)


def _source_file_id(tenant_id: str, project_id: str, name: str, digest: str) -> str:
    return _opaque_file_id(tenant_id, project_id, "source", f"{name}\x1f{digest}")


def _evidence_file_id(tenant_id: str, project_id: str, deliverable_id: str, index: int, digest: str) -> str:
    return _opaque_file_id(tenant_id, project_id, "evidence", f"{deliverable_id}\x1f{index}\x1f{digest}")


def _media_type(name: str) -> str:
    guessed = mimetypes.guess_type(name)[0]
    return guessed if isinstance(guessed, str) and len(guessed) <= 128 else "application/octet-stream"


def _safe_id(value: Any) -> str | None:
    return value if (
        isinstance(value, str) and _SAFE_ID.fullmatch(value) and not _INTERNAL_PUBLIC_TEXT.search(value)
    ) else None


def _safe_name(value: Any, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    candidate = value.strip()
    if (
        not candidate or len(candidate) > 200 or "/" in candidate or "\\" in candidate
        or candidate in {".", ".."} or ".." in Path(candidate).parts
        or any(ord(char) < 32 for char in candidate) or _INTERNAL_PUBLIC_TEXT.search(candidate)
    ):
        return fallback
    return candidate


def _safe_timestamp(value: Any) -> str | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _safe_version(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 1_000_000 else 1


def _safe_hash(value: Any) -> str:
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) else ""


def _sanitize_file_actions(
    raw_actions: Any, raw_targets: Any,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """Allow only the exact output-verification mutation contract."""
    if not isinstance(raw_actions, (list, tuple)) or not isinstance(raw_targets, Mapping):
        return [], {}
    if list(raw_actions) != ["verify_output"]:
        return [], {}
    target = raw_targets.get("verify_output")
    if not isinstance(target, Mapping) or set(target) != {"kind", "id", "revision"}:
        return [], {}
    source_id = _safe_id(target.get("id"))
    revision = target.get("revision")
    if (
        target.get("kind") != "output" or source_id is None
        or isinstance(revision, bool) or not isinstance(revision, int) or revision < 1
    ):
        return [], {}
    return ["verify_output"], {
        "verify_output": {"kind": "output", "id": source_id, "revision": revision},
    }


def _verification_action(
    tenant_id: str, project_id: str, value: Mapping[str, Any], human_id: str,
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    if value.get("kind") != "output" or value.get("state") != "awaiting_verification":
        return [], {}
    source_id = _safe_id(value.get("source_id"))
    producer_id = _safe_id(value.get("producer_id"))
    version = value.get("action_version")
    if (
        source_id is None or producer_id == human_id or not _safe_id(human_id)
        or isinstance(version, bool) or not isinstance(version, int) or version < 1
    ):
        return [], {}
    try:
        mission = JsonMissionRepository(_mission_root).get_mission(tenant_id, project_id)
    except Exception:
        return [], {}
    verifier_values = mission.get("verifier_ids")
    verifier_ids = {
        item for item in verifier_values if _safe_id(item) is not None
    } if isinstance(verifier_values, (list, tuple)) else set()
    if human_id != mission.get("owner_id") and human_id not in verifier_ids:
        return [], {}
    return _sanitize_file_actions(
        ["verify_output"],
        {"verify_output": {"kind": "output", "id": source_id, "revision": version}},
    )


def _mapping_rows(value: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _string_values(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _open_directory(parent_fd: int | None, name: str | Path) -> int:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        return os.open(name, flags) if parent_fd is None else os.open(str(name), flags, dir_fd=parent_fd)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise _UnsafeSourceInventory("source inventory unavailable") from exc


def _read_source_file(directory_fd: int, name: str) -> tuple[int, str]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size < 0 or info.st_size > 16 * 1024 * 1024:
                raise _UnsafeSourceInventory("source inventory unavailable")
            digest = hashlib.sha256()
            remaining = info.st_size
            while remaining:
                chunk = os.read(descriptor, min(65536, remaining))
                if not chunk:
                    raise _UnsafeSourceInventory("source inventory unavailable")
                digest.update(chunk)
                remaining -= len(chunk)
            if os.read(descriptor, 1):
                raise _UnsafeSourceInventory("source inventory unavailable")
            return info.st_size, digest.hexdigest()
        finally:
            os.close(descriptor)
    except _UnsafeSourceInventory:
        raise
    except OSError as exc:
        raise _UnsafeSourceInventory("source inventory unavailable") from exc


def _walk_sources(directory_fd: int, prefix: tuple[str, ...] = ()) -> list[SourceFile]:
    result: list[SourceFile] = []
    try:
        names = sorted(os.listdir(directory_fd))
    except OSError as exc:
        raise _UnsafeSourceInventory("source inventory unavailable") from exc
    for name in names:
        if not name or name in {".", ".."} or any(ord(char) < 32 for char in name):
            raise _UnsafeSourceInventory("source inventory unavailable")
        try:
            info = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise _UnsafeSourceInventory("source inventory unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise _UnsafeSourceInventory("source inventory unavailable")
        relative = (*prefix, name)
        if stat.S_ISDIR(info.st_mode):
            child = _open_directory(directory_fd, name)
            try:
                result.extend(_walk_sources(child, relative))
            finally:
                os.close(child)
            continue
        if not stat.S_ISREG(info.st_mode):
            raise _UnsafeSourceInventory("source inventory unavailable")
        if is_internal_source_name(name):
            continue
        size, digest = _read_source_file(directory_fd, name)
        public_name = "/".join(relative)
        result.append(SourceFile(
            name=public_name, size=size, type=Path(name).suffix.lstrip(".").lower(), sha256=digest,
            status="ready", detail="Ready", row_count=0,
        ))
    return result


def _source_inventory(project_id: str) -> list[SourceFile]:
    # Tests and legacy callers may inject a deterministic inventory provider;
    # production public reads never call the rglob-based legacy implementation.
    if list_source_files is not _LEGACY_SOURCE_LIST:
        return list(list_source_files(project_id))
    try:
        root_fd = _open_directory(None, project_dir(project_id))
    except FileNotFoundError as exc:
        raise _UnsafeSourceInventory("source inventory unavailable") from exc
    inputs_fd: int | None = None
    room_fd: int | None = None
    try:
        try:
            inputs_fd = _open_directory(root_fd, "inputs")
            room_fd = _open_directory(inputs_fd, "data-room")
        except FileNotFoundError:
            return []
        return _walk_sources(room_fd)
    finally:
        if room_fd is not None:
            os.close(room_fd)
        if inputs_fd is not None:
            os.close(inputs_fd)
        os.close(root_fd)


def _visible_member_snapshot(
    repository: JsonCollaborationRepository, tenant_id: str, project_id: str, human_id: str,
) -> Any | None:
    try:
        return repository.visible_member(repository.get_room(tenant_id, project_id), human_id)
    except Exception:
        return None


def _snapshot_still_present(room: Any, member: Any) -> bool:
    return member is not None and member in room.members


def _require_member(repository: JsonCollaborationRepository, tenant_id: str, project_id: str, human_id: str) -> None:
    member = _visible_member_snapshot(repository, tenant_id, project_id, human_id)
    try:
        with repository.room_lock(tenant_id, project_id) as room:
            if not _snapshot_still_present(room, member):
                raise ValueError
    except Exception as exc:
        raise _error(404, "file_unavailable", "This Mission file is unavailable.") from exc


def _source_ids(tenant_id: str, project_id: str) -> set[str]:
    return {
        _source_file_id(tenant_id, project_id, source.name, source.sha256)
        for source in _source_inventory(project_id)
    }


def _provenance(
    tenant_id: str, project_id: str, raw: Mapping[str, Any], evidence: Mapping[str, Any] | None = None,
) -> tuple[str | None, list[str]]:
    try:
        runs = JsonMissionRepository(_mission_root).list_collection(tenant_id, project_id, "runs")
    except Exception:
        runs = {}
    candidates: set[str] = set()
    source_ref = raw.get("source_ref")
    if isinstance(source_ref, str):
        match = re.fullmatch(
            r"mission/run/([A-Za-z0-9][A-Za-z0-9_.-]{0,127})(?:/failed-agent/[A-Za-z0-9][A-Za-z0-9_.-]{0,127})?",
            source_ref,
        )
        if match and match.group(1) in runs:
            candidates.add(match.group(1))
    evidence_rows = (evidence,) if evidence is not None else _mapping_rows(raw.get("validation_evidence"))
    for item in evidence_rows:
        run_id = item.get("run_id")
        if (
            isinstance(run_id, str)
            and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", run_id)
            and run_id in runs
        ):
            candidates.add(run_id)
    run_id = next(iter(candidates)) if len(candidates) == 1 else None
    allowed_sources = _source_ids(tenant_id, project_id)
    source_ids = sorted({
        item for row in evidence_rows for item in _string_values(row.get("source_ids"))
        if isinstance(item, str) and item in allowed_sources
    })
    return run_id, source_ids


def _resolve_file(tenant_id: str, project_id: str, file_id: str) -> dict[str, Any] | None:
    try:
        deliveries = JsonMissionRepository(_mission_root).list_collection(tenant_id, project_id, "deliverables")
    except Exception:
        deliveries = {}
    for deliverable_id, raw in deliveries.items():
        if not isinstance(raw, Mapping) or not hmac.compare_digest(
            output_file_id(str(deliverable_id), tenant_id=tenant_id, project_id=project_id), file_id,
        ):
            continue
        artifact_ref = raw.get("artifact_ref")
        if not isinstance(artifact_ref, str) or not artifact_ref:
            return None
        try:
            content = artifact_bytes(project_dir(project_id), artifact_ref)
        except ValueError:
            return None
        run_id, source_ids = _provenance(tenant_id, project_id, raw)
        return {
            "id": file_id,
            "kind": "output",
            "name": str(raw.get("name") or "Mission output")[:200],
            "media_type": _media_type(str(raw.get("name") or "")),
            "size": len(content),
            "sha256": raw.get("content_hash"),
            "artifact_ref": artifact_ref,
            "source_id": str(deliverable_id),
            "state": str(raw.get("state") or "draft"),
            "type": str(raw.get("type") or "report"),
            "version": raw.get("version", 1),
            "action_version": raw.get("version"),
            "producer_id": raw.get("producer_id"),
            "created_at": raw.get("created_at"),
            "updated_at": raw.get("updated_at"),
            "verified_by": raw.get("verified_by"),
            "run_id": run_id,
            "source_ids": source_ids,
            "introduced_by_message_id": _introduced_message_id(tenant_id, project_id, str(deliverable_id)),
            "parent_output_id": None,
        }
    for deliverable_id, raw in deliveries.items():
        if not isinstance(raw, Mapping):
            continue
        for index, evidence in enumerate(_mapping_rows(raw.get("validation_evidence"))):
            artifact_ref = evidence.get("evidence_ref") or evidence.get("artifact_ref")
            digest = evidence.get("sha256")
            if not isinstance(artifact_ref, str) or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                continue
            candidate = _evidence_file_id(tenant_id, project_id, str(deliverable_id), index, digest)
            if not hmac.compare_digest(candidate, file_id):
                continue
            try:
                content = artifact_bytes(project_dir(project_id), artifact_ref)
            except ValueError:
                return None
            run_id, source_ids = _provenance(tenant_id, project_id, raw, evidence)
            return {
                "id": file_id, "kind": "evidence", "name": evidence.get("name") or "Validation evidence",
                "media_type": _media_type(artifact_ref), "size": len(content), "sha256": digest,
                "artifact_ref": artifact_ref, "source_id": str(deliverable_id), "state": "recorded",
                "type": "evidence", "version": raw.get("version", 1), "producer_id": raw.get("producer_id"),
                "created_at": raw.get("created_at"), "updated_at": raw.get("updated_at"),
                "verified_by": raw.get("verified_by"), "run_id": run_id, "source_ids": source_ids,
                "introduced_by_message_id": _introduced_message_id(tenant_id, project_id, str(deliverable_id)),
                "parent_output_id": output_file_id(str(deliverable_id), tenant_id=tenant_id, project_id=project_id),
            }
    sources = _source_inventory(project_id)
    for source in sources:
        candidate = _source_file_id(tenant_id, project_id, source.name, source.sha256)
        if hmac.compare_digest(candidate, file_id):
            return {
                "id": file_id,
                "kind": "source",
                "name": Path(source.name).name[:200],
                "media_type": _media_type(source.name),
                "size": source.size,
                "sha256": source.sha256,
                "artifact_ref": str(Path("inputs") / "data-room" / source.name),
                "source_id": candidate,
                "state": source.status,
                "type": "source",
                "version": 1,
                "producer_id": None,
                "created_at": None,
                "updated_at": None,
                "verified_by": None,
                "run_id": None,
                "source_ids": [],
                "introduced_by_message_id": None,
                "parent_output_id": None,
            }
    return None


def _inline_previewable(value: Mapping[str, Any]) -> bool:
    if value.get("kind") == "output" and value.get("type") in {"code", "application"}:
        return False
    return value.get("media_type") in _SAFE_INLINE_MEDIA


def _public_previewable(value: Mapping[str, Any]) -> bool:
    if value.get("kind") == "output" and value.get("type") in {"code", "application"}:
        return value.get("state") == "verified" and value.get("media_type") == "text/html"
    return _inline_previewable(value)


def _attribution(
    tenant_id: str, project_id: str, *, locked_room: Any | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    try:
        agents = JsonMissionRepository(_mission_root).list_collection(tenant_id, project_id, "agents")
    except Exception:
        agents = {}
    collaboration = JsonCollaborationRepository(_collaboration_root)
    try:
        if locked_room is None:
            raw_room = collaboration.get_room(tenant_id, project_id)
            visible_members = collaboration.visible_members(raw_room)
            with collaboration.room_lock(tenant_id, project_id) as room:
                visible_members = [member for member in visible_members if member in room.members]
        else:
            # The caller computed visibility before acquiring this room lock.
            # Never take the tenant acceptance lock from inside the room lock.
            visible_members = []
    except Exception:
        visible_members = []
    agent_names = {
        str(agent_id): _safe_name(raw.get("name"), "Mission agent")
        for agent_id, raw in agents.items()
        if _safe_id(agent_id) is not None and isinstance(raw, Mapping)
    }
    human_names = {
        member.actor_id: _safe_name(member.display_name, "Mission human")
        for member in visible_members
        if _safe_id(member.actor_id) is not None
    }
    return agent_names, human_names


def _public_metadata(
    tenant_id: str, project_id: str, value: Mapping[str, Any], *, human_id: str,
    locked_room: Any | None = None, visible_humans: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    producer_id = _safe_id(value.get("producer_id"))
    verifier_id = _safe_id(value.get("verified_by"))
    agent_names, human_names = _attribution(tenant_id, project_id, locked_room=locked_room)
    if visible_humans is not None:
        human_names = dict(visible_humans)
    producer_name = agent_names.get(producer_id or "") or human_names.get(producer_id or "")
    public_verifier_id = verifier_id if verifier_id in human_names else None
    verifier_name = human_names.get(public_verifier_id or "")
    kind = value.get("kind") if value.get("kind") in {"source", "output", "evidence"} else "output"
    state_values = _SOURCE_STATES if kind == "source" else (_OUTPUT_STATES if kind == "output" else frozenset({"recorded"}))
    state = value.get("state") if value.get("state") in state_values else ("ready" if kind == "source" else "draft" if kind == "output" else "recorded")
    fallback_name = {"source": "Mission source", "output": "Mission output", "evidence": "Validation evidence"}[kind]
    allowed_actions, action_targets = _verification_action(tenant_id, project_id, value, human_id)
    return {
        "id": value["id"],
        "mission_id": project_id,
        "kind": kind,
        "name": _safe_name(value.get("name"), fallback_name),
        "media_type": value.get("media_type") if isinstance(value.get("media_type"), str) and len(value["media_type"]) <= 128 else "application/octet-stream",
        "size": value.get("size") if isinstance(value.get("size"), int) and not isinstance(value.get("size"), bool) and value["size"] >= 0 else 0,
        "sha256": _safe_hash(value.get("sha256")),
        "state": state,
        "version": _safe_version(value.get("version")),
        "producer_id": producer_id,
        "producer": ({"id": producer_id, **({"display_name": producer_name} if producer_name else {})} if producer_id else None),
        "verifier": ({"id": public_verifier_id, **({"display_name": verifier_name} if verifier_name else {})} if public_verifier_id else None),
        "run_id": _safe_id(value.get("run_id")),
        "parent_output_id": value.get("parent_output_id") if isinstance(value.get("parent_output_id"), str) and re.fullmatch(r"file_[0-9a-f]{40}", value["parent_output_id"]) else None,
        "source_ids": [item for item in _string_values(value.get("source_ids")) if re.fullmatch(r"file_[0-9a-f]{40}", item)],
        "introduced_by_message_id": _safe_id(value.get("introduced_by_message_id")),
        "created_at": _safe_timestamp(value.get("created_at")),
        "updated_at": _safe_timestamp(value.get("updated_at")),
        "previewable": _public_previewable(value),
        "downloadable": True,
        "allowed_actions": allowed_actions,
        "action_targets": action_targets,
    }


def _introduced_message_id(tenant_id: str, project_id: str, output_id: str) -> str | None:
    try:
        state = JsonCollaborationRepository(_collaboration_root).conversation_state(tenant_id, project_id)
    except Exception:
        return None
    messages = state.get("messages")
    if not isinstance(messages, Mapping):
        return None
    for message_id, message in messages.items():
        if isinstance(message, Mapping) and isinstance(message.get("links"), Mapping) and message["links"].get("output_id") == output_id:
            return str(message_id)
    return None


def _private_inventory(
    tenant_id: str, project_id: str, *, human_id: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    legacy: list[dict[str, Any]] = []
    sources = _source_inventory(project_id)
    for source in sources:
        legacy.append({
            "name": _safe_name(Path(source.name).name, "Mission source"), "size": source.size,
            "type": source.type if isinstance(source.type, str) and len(source.type) <= 32 and not _INTERNAL_PUBLIC_TEXT.search(source.type) else "file",
            "status": source.status if source.status in _SOURCE_STATES else "ready",
            "detail": _safe_name(source.detail, "Ready"),
            "row_count": source.row_count if isinstance(source.row_count, int) and not isinstance(source.row_count, bool) and source.row_count >= 0 else 0,
        })
        resolved = _resolve_file(
            tenant_id, project_id, _source_file_id(tenant_id, project_id, source.name, source.sha256),
        )
        if resolved is not None:
            items.append(_public_metadata(tenant_id, project_id, resolved, human_id=human_id))
    try:
        deliveries = JsonMissionRepository(_mission_root).list_collection(tenant_id, project_id, "deliverables")
    except Exception:
        deliveries = {}
    for deliverable_id, raw in deliveries.items():
        if not isinstance(raw, Mapping):
            continue
        output = _resolve_file(
            tenant_id, project_id,
            output_file_id(str(deliverable_id), tenant_id=tenant_id, project_id=project_id),
        )
        if output is not None:
            items.append(_public_metadata(tenant_id, project_id, output, human_id=human_id))
        for index, evidence in enumerate(_mapping_rows(raw.get("validation_evidence"))):
            if not isinstance(evidence.get("sha256"), str):
                continue
            candidate = _evidence_file_id(tenant_id, project_id, str(deliverable_id), index, str(evidence["sha256"]))
            resolved = _resolve_file(tenant_id, project_id, candidate)
            if resolved is not None:
                items.append(_public_metadata(tenant_id, project_id, resolved, human_id=human_id))
    ordered = sorted(items, key=lambda item: str(item["id"]), reverse=True)
    ordered = sorted(ordered, key=lambda item: str(item.get("updated_at") or ""), reverse=True)
    ordered = sorted(ordered, key=lambda item: str(item["kind"]))
    return ordered, legacy


def authorized_file_inventory(
    project_id: str, *, kind: str, ctx: AuthContext,
) -> dict[str, Any]:
    """Shared W4 list helper for the workplace and legacy main route owner."""
    if kind not in {"source", "output", "evidence", "all"}:
        raise _error(400, "file_filter_invalid", "Choose a valid file type.")
    repository = JsonCollaborationRepository(_collaboration_root)
    _require_member(repository, ctx.tenant_id, project_id, ctx.user.id)
    try:
        items, legacy = _private_inventory(ctx.tenant_id, project_id, human_id=ctx.user.id)
    except _UnsafeSourceInventory as exc:
        raise _error(404, "file_unavailable", "Mission files are unavailable.") from exc
    if kind != "all":
        items = [item for item in items if item["kind"] == kind]
    current_member = _visible_member_snapshot(repository, ctx.tenant_id, project_id, ctx.user.id)
    try:
        with repository.room_lock(ctx.tenant_id, project_id) as room:
            if not _snapshot_still_present(room, current_member):
                raise ValueError
            return {"items": items, "files": legacy}
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(404, "file_unavailable", "Mission files are unavailable.") from exc


@router.get("/projects/{project_id}/files")
def mission_files(
    project_id: str,
    kind: str = "all",
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict:
    return authorized_file_inventory(project_id, kind=kind, ctx=ctx)


@router.get("/projects/{project_id}/files/{file_id}")
def file_metadata(
    project_id: str,
    file_id: str,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> dict:
    repository = JsonCollaborationRepository(_collaboration_root)
    _require_member(repository, ctx.tenant_id, project_id, ctx.user.id)
    try:
        value = _resolve_file(ctx.tenant_id, project_id, file_id)
    except _UnsafeSourceInventory as exc:
        raise _error(404, "file_unavailable", "This Mission file is unavailable.") from exc
    if value is None:
        raise _error(404, "file_unavailable", "This Mission file is unavailable.")
    raw_room = repository.get_room(ctx.tenant_id, project_id)
    visible_members = repository.visible_members(raw_room)
    visible_humans = {
        member.actor_id: _safe_name(member.display_name, "Mission human") for member in visible_members
        if _safe_id(member.actor_id) is not None
    }
    current_member = next((member for member in visible_members if member.actor_id == ctx.user.id), None)
    try:
        with repository.room_lock(ctx.tenant_id, project_id) as room:
            if not _snapshot_still_present(room, current_member):
                raise ValueError
            return {"file": _public_metadata(
                ctx.tenant_id, project_id, value, human_id=ctx.user.id, locked_room=room,
                visible_humans={
                    actor_id: name for actor_id, name in visible_humans.items()
                    if any(member.actor_id == actor_id and member in room.members for member in visible_members)
                },
            )}
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(404, "file_unavailable", "This Mission file is unavailable.") from exc


def _disposition(value: str, filename: str) -> str:
    safe_ascii = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(filename).name).strip("._") or "mission-file"
    return f"{value}; filename=\"{safe_ascii[:120]}\"; filename*=UTF-8''{quote(Path(filename).name[:200], safe='')}"


def _range_unavailable(total: int | None = None) -> HTTPException:
    headers = {"Content-Range": f"bytes */{total}"} if isinstance(total, int) else None
    return HTTPException(
        416,
        {"code": "file_range_unavailable", "message": "This byte range is unavailable."},
        headers=headers,
    )


def _bounded_range(value: str, total: int) -> tuple[int, int]:
    match = re.fullmatch(r"bytes=([0-9]+)-([0-9]+)", value.strip())
    if not match:
        raise _range_unavailable(total)
    start, end = int(match.group(1)), int(match.group(2))
    if start > end or start >= total or end >= total or end - start + 1 > _MAX_RANGE_BYTES:
        raise _range_unavailable(total)
    return start, end


@router.get("/projects/{project_id}/files/{file_id}/content")
def file_content(
    project_id: str,
    file_id: str,
    disposition: str = "attachment",
    range_header: Annotated[str | None, Header(alias="Range")] = None,
    ctx: Annotated[AuthContext, Depends(get_auth)] = None,  # type: ignore[assignment]
) -> Response:
    if disposition not in {"inline", "attachment"}:
        raise _error(400, "file_request_invalid", "Choose preview or download.")
    repository = JsonCollaborationRepository(_collaboration_root)
    _require_member(repository, ctx.tenant_id, project_id, ctx.user.id)
    try:
        value = _resolve_file(ctx.tenant_id, project_id, file_id)
    except _UnsafeSourceInventory as exc:
        raise _error(404, "file_unavailable", "This Mission file is unavailable.") from exc
    if value is None:
        raise _error(404, "file_unavailable", "This Mission file is unavailable.")
    if disposition == "inline" and not _inline_previewable(value):
        raise _error(409, "file_preview_unavailable", "This file must be downloaded for review.")
    try:
        content = artifact_bytes(project_dir(project_id), str(value["artifact_ref"]))
    except ValueError as exc:
        raise _error(404, "file_unavailable", "This Mission file is unavailable.") from exc
    digest = hashlib.sha256(content).hexdigest()
    if not isinstance(value.get("sha256"), str) or not hmac.compare_digest(digest, str(value["sha256"])):
        raise _error(409, "file_changed", "This file changed. Refresh to review the latest version.")
    immutable = (
        value.get("kind") in {"output", "evidence"}
        and isinstance(value.get("version"), int)
        and not isinstance(value.get("version"), bool)
        and value["version"] > 0
    )
    headers = {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Disposition": _disposition(
            disposition,
            _safe_name(
                value.get("name"),
                {"source": "Mission source", "output": "Mission output", "evidence": "Validation evidence"}.get(
                    str(value.get("kind")), "Mission file",
                ),
            ),
        ),
        "Content-Security-Policy": "sandbox; default-src 'none'; form-action 'none'; base-uri 'none'",
    }
    status_code = 200
    response_content = content
    if immutable:
        headers["Accept-Ranges"] = "bytes"
    if range_header is not None:
        if not immutable:
            raise _range_unavailable(len(content))
        start, end = _bounded_range(range_header, len(content))
        response_content = content[start:end + 1]
        status_code = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{len(content)}"
        headers["Content-Length"] = str(len(response_content))
    media_type = str(value["media_type"]) if disposition == "inline" else "application/octet-stream"
    current_member = _visible_member_snapshot(repository, ctx.tenant_id, project_id, ctx.user.id)
    try:
        with repository.room_lock(ctx.tenant_id, project_id) as room:
            if not _snapshot_still_present(room, current_member):
                raise ValueError
            return Response(content=response_content, status_code=status_code, media_type=media_type, headers=headers)
    except HTTPException:
        raise
    except Exception as exc:
        raise _error(404, "file_unavailable", "This Mission file is unavailable.") from exc
