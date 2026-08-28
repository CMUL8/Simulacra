"""Dedicated-origin, capability-gated Mission app previews.

The control application never puts preview credentials in a URL.  It creates a
short-lived, one-use proof for an already authenticated human; the separate
preview host exchanges that proof for a host-only capability cookie.  Preview
assets then re-check both the capability and current Project Room membership.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import hmac
import json
import mimetypes
import os
import re
import secrets
import stat
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field

from apps.api.security import require_project_access
from simulacra.collaboration import JsonCollaborationRepository
from simulacra.demo.identity import AuthContext
from simulacra.demo.paths import RUNS_DIR
from simulacra.demo.runs import project_dir
from simulacra.missions import JsonMissionRepository, MissionService


router = APIRouter(tags=["mission-preview"])
_rooms_root = RUNS_DIR / ".cmul8-control"
_exchange_root = RUNS_DIR / ".workplace-control" / "preview-exchanges"
_EXCHANGE_TTL_SECONDS = 120
_CAPABILITY_TTL_SECONDS = 300
_MAX_PREVIEW_BYTES = 16 * 1024 * 1024
_NO_MEMBER_SNAPSHOT = object()


class _ExchangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _ConsumeExchangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exchange_id: str = Field(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    exchange_proof: str = Field(min_length=32, max_length=512, pattern=r"^[A-Za-z0-9_.-]+$")


@dataclass(frozen=True)
class PreviewOriginConfig:
    control_origin: str
    preview_origin: str
    registrable_domain: str
    secret: bytes

    @property
    def control_host(self) -> str:
        return str(urlparse(self.control_origin).hostname)

    @property
    def preview_host(self) -> str:
        return str(urlparse(self.preview_origin).hostname)


def _origin_error() -> HTTPException:
    return HTTPException(404, {"code": "preview_unavailable", "message": "Preview is unavailable."})


def _parse_origin(value: str | None) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlparse(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return None
        if parsed.username or parsed.password or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            return None
        # URL parsing lower-cases neither the scheme nor the hostname consistently
        # enough for policy comparison, so normalize the externally visible origin.
        host = parsed.hostname.lower()
        port = f":{parsed.port}" if parsed.port is not None else ""
        return f"{parsed.scheme.lower()}://{host}{port}"
    except ValueError:
        return None


def _valid_registrable_domain(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    labels = value.strip(".").lower().split(".")
    if len(labels) < 2 or any(not label or not label.replace("-", "").isalnum() for label in labels):
        return None
    return ".".join(labels)


def preview_origin_config() -> PreviewOriginConfig | None:
    """Resolve the server-only preview security contract, failing closed."""
    enabled = (
        os.environ.get("SIMULACRA_WORKPLACE_PREVIEW_ORIGIN_V1")
        or os.environ.get("CMUL8_WORKPLACE_PREVIEW_ORIGIN_V1", "")
    ).lower() in {"1", "true", "yes"}
    control = _parse_origin(os.environ.get("CONTROL_ORIGIN") or os.environ.get("CMUL8_CONTROL_ORIGIN"))
    preview = _parse_origin(os.environ.get("PREVIEW_ORIGIN") or os.environ.get("CMUL8_PREVIEW_ORIGIN"))
    domain = _valid_registrable_domain(os.environ.get("PREVIEW_REGISTRABLE_DOMAIN") or os.environ.get("CMUL8_PREVIEW_REGISTRABLE_DOMAIN"))
    secret = os.environ.get("SIMULACRA_PREVIEW_EXCHANGE_SECRET") or os.environ.get("CMUL8_PREVIEW_EXCHANGE_SECRET")
    if not enabled or not control or not preview or not domain or not secret:
        return None
    if urlparse(control).scheme != "https" or urlparse(preview).scheme != "https":
        return None
    control_host = str(urlparse(control).hostname)
    preview_host = str(urlparse(preview).hostname)
    if (
        control_host == preview_host
        or not control_host.endswith(f".{domain}")
        or not preview_host.endswith(f".{domain}")
    ):
        return None
    return PreviewOriginConfig(control, preview, domain, secret.encode("utf-8"))


def preview_origin_hostname() -> str | None:
    """Return a safely parsed configured preview host even when setup is off.

    This is deliberately independent of readiness: an incomplete deployment
    must still not let the configured preview host serve control-plane routes.
    """
    raw = os.environ.get("PREVIEW_ORIGIN") or os.environ.get("CMUL8_PREVIEW_ORIGIN")
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        candidate = raw.strip()
        # A missing scheme is not a valid origin, but a bare DNS hostname is
        # still safe enough to quarantine until the deployment is corrected.
        host = urlparse(candidate if "://" in candidate else f"//{candidate}").hostname
    except ValueError:
        return None
    if not isinstance(host, str):
        return None
    normalized = host.lower().rstrip(".")
    labels = normalized.split(".")
    if not normalized or any(
        not label or label.startswith("-") or label.endswith("-")
        or not re.fullmatch(r"[a-z0-9-]+", label)
        for label in labels
    ):
        return None
    return normalized


def _require_config() -> PreviewOriginConfig:
    config = preview_origin_config()
    if config is None:
        raise _origin_error()
    return config


def _host_matches(request: Request, expected_host: str) -> bool:
    host = request.url.hostname
    return isinstance(host, str) and hmac.compare_digest(host.lower(), expected_host.lower())


def _now() -> int:
    return int(time.time())


def _expiry(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _safe_reference(value: Any) -> tuple[str, ...] | None:
    if not isinstance(value, str) or not value or "\\" in value:
        return None
    path = Path(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} or any(ord(char) < 32 for char in part) for part in path.parts):
        return None
    return path.parts


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _sign(secret: bytes, payload: bytes) -> str:
    return _b64(hmac.new(secret, payload, hashlib.sha256).digest())


def _issue_signed(secret: bytes, claims: dict[str, Any]) -> str:
    payload = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode("utf-8"))
    return f"{payload}.{_sign(secret, payload.encode('ascii'))}"


def _read_signed(secret: bytes, token: str) -> dict[str, Any] | None:
    try:
        payload, signature = token.split(".", 1)
        if not hmac.compare_digest(_sign(secret, payload.encode("ascii")), signature):
            return None
        claims = json.loads(_unb64(payload))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return claims if isinstance(claims, dict) else None


_EXCHANGE_FIELDS = frozenset({"tenant_id", "project_id", "actor_id", "revision", "origin", "expires_at", "proof", "consumed"})


def _valid_exchange_row(exchange_id: Any, value: Any) -> bool:
    if not isinstance(exchange_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,128}", exchange_id):
        return False
    if not isinstance(value, dict) or set(value) != _EXCHANGE_FIELDS:
        return False
    if any(_safe_reference(value.get(key)) is None or len(_safe_reference(value.get(key)) or ()) != 1 for key in ("tenant_id", "project_id", "actor_id")):
        return False
    if not isinstance(value.get("revision"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["revision"]):
        return False
    if _parse_origin(value.get("origin")) is None:
        return False
    if not isinstance(value.get("expires_at"), int) or isinstance(value.get("expires_at"), bool):
        return False
    if not isinstance(value.get("proof"), str) or not re.fullmatch(r"[0-9a-f]{64}", value["proof"]):
        return False
    return isinstance(value.get("consumed"), bool)


def _exchange_directory_fd() -> int:
    """Create the private ledger directory one component at a time, nofollow."""
    try:
        relative = _exchange_root.relative_to(RUNS_DIR)
    except ValueError as exc:
        raise RuntimeError("preview exchange directory is outside the private run root") from exc
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(RUNS_DIR, flags)
    try:
        for part in relative.parts:
            try:
                os.mkdir(part, 0o700, dir_fd=descriptor)
                os.fsync(descriptor)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise RuntimeError("preview exchange ledger is unavailable") from exc
    except Exception:
        os.close(descriptor)
        raise


def _locked_exchange_state() -> tuple[int, int, dict[str, Any]]:
    """Open the durable exchange ledger under an advisory process lock."""
    directory = _exchange_directory_fd()
    try:
        descriptor = os.open("state.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=directory)
        fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError as exc:
        os.close(directory)
        raise RuntimeError("preview exchange ledger is unavailable") from exc
    try:
        state_fd = os.open("state.json", os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=directory)
        try:
            raw = json.loads(os.read(state_fd, 8 * 1024 * 1024).decode("utf-8"))
        finally:
            os.close(state_fd)
    except FileNotFoundError:
        raw = {}
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        _release_exchange_lock(directory, descriptor)
        raise RuntimeError("preview exchange ledger is unavailable") from exc
    if not isinstance(raw, dict) or any(not _valid_exchange_row(key, value) for key, value in raw.items()):
        _release_exchange_lock(directory, descriptor)
        raise RuntimeError("preview exchange ledger is unavailable")
    return directory, descriptor, raw


def _write_exchange_state(directory: int, state: dict[str, Any]) -> None:
    temporary = f".state-{secrets.token_hex(16)}.tmp"
    fd = os.open(
        temporary,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
        0o600,
        dir_fd=directory,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, sort_keys=True, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, "state.json", src_dir_fd=directory, dst_dir_fd=directory)
        os.fsync(directory)
    finally:
        try:
            os.unlink(temporary, dir_fd=directory)
        except FileNotFoundError:
            pass


def _release_exchange_lock(directory: int, descriptor: int) -> None:
    try:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)
        os.close(directory)


def _visible_member_snapshot(tenant_id: str, project_id: str, actor_id: str) -> Any | None:
    repository = JsonCollaborationRepository(_rooms_root)
    try:
        return repository.visible_member(repository.get_room(tenant_id, project_id), actor_id)
    except Exception:
        return None


def _is_current_member(tenant_id: str, project_id: str, actor_id: str) -> bool:
    repository = JsonCollaborationRepository(_rooms_root)
    member = _visible_member_snapshot(tenant_id, project_id, actor_id)
    try:
        with repository.room_lock(tenant_id, project_id) as room:
            return member is not None and member in room.members
    except Exception:
        return False


def _valid_verified_at(value: Any) -> bool:
    """Accept only an explicit, timezone-aware durable verification timestamp."""
    if not isinstance(value, str) or not value:
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _screened_human_verifier(
    room: Any, actor_id: Any, *, visible_member_snapshot: Any = _NO_MEMBER_SNAPSHOT,
) -> bool:
    """A public app requires a currently authorized human reviewer record."""
    actor_parts = _safe_reference(actor_id)
    if actor_parts is None or len(actor_parts) != 1:
        return False
    member = visible_member_snapshot
    if member is _NO_MEMBER_SNAPSHOT:
        try:
            member = JsonCollaborationRepository(_rooms_root).visible_member(room, actor_id)
        except Exception:
            return False
    return member is not None and member.role in {"owner", "admin", "reviewer", "approver"}


def _read_preview_file(workspace: Path, relative: str) -> tuple[bytes, str] | None:
    """Read only a regular file beneath canonical ``app/dist`` via no-follow FDs."""
    value = Path(relative)
    if (
        value.is_absolute() or not value.parts or "\\" in relative
        or any(part in {"", ".", ".."} or any(ord(char) < 32 for char in part) for part in value.parts)
    ):
        return None
    root = workspace.resolve(strict=True)
    root_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0))
    current_fd = root_fd
    try:
        directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        for part in ("app", "dist", *value.parts[:-1]):
            child = os.open(part, directory_flags, dir_fd=current_fd)
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = child
        fd = os.open(value.parts[-1], os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=current_fd)
        try:
            details = os.fstat(fd)
            if not stat.S_ISREG(details.st_mode) or details.st_size > _MAX_PREVIEW_BYTES:
                return None
            chunks: list[bytes] = []
            remaining = details.st_size
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    return None
                chunks.append(chunk)
                remaining -= len(chunk)
            if os.read(fd, 1):
                return None
            return b"".join(chunks), hashlib.sha256(b"".join(chunks)).hexdigest()
        finally:
            os.close(fd)
    except Exception:
        return None
    finally:
        if current_fd != root_fd:
            os.close(current_fd)
        os.close(root_fd)


def _promoted_manifest_from_snapshot(
    tenant_id: str, project_id: str, room: Any, visible_members: Mapping[str, Any],
) -> tuple[str, dict[str, str]] | None:
    """Return the exact human-verified ``app/dist`` manifest, or nothing.

    Merely having a built application is not enough.  Every public byte must
    correspond to a verified code deliverable whose approved target is under
    the fixed public root.  Staging paths and unverified outputs never enter
    this manifest.
    """
    try:
        workspace = project_dir(project_id)
        dist = workspace / "app" / "dist"
        if not dist.is_dir() or dist.is_symlink():
            return None
        missions = MissionService(JsonMissionRepository(RUNS_DIR / ".mission-control"))
        mission = missions.mission(tenant_id, project_id)
        if mission.tenant_id != tenant_id or mission.project_id != project_id:
            return None
        if room.tenant_id != tenant_id or room.project_id != project_id:
            return None
        deliverables = missions.deliverables(tenant_id, project_id)
        candidates: dict[str, tuple[str, int]] = {}
        for item in deliverables:
            if item.state != "verified" or item.verified_hash != item.content_hash:
                continue
            # A state label is not approval evidence. The durable verification
            # service records a human, timestamp, and Mission scope; all of
            # them must still be valid before any byte becomes previewable.
            if (
                item.tenant_id != tenant_id
                or item.project_id != project_id
                or item.mission_id != mission.id
                or not _valid_verified_at(item.verified_at)
                or not _screened_human_verifier(
                    room, item.verified_by, visible_member_snapshot=visible_members.get(item.verified_by),
                )
                or (item.verified_by != mission.owner_id and item.verified_by not in mission.verifier_ids)
            ):
                continue
            promotions = [
                evidence for evidence in item.validation_evidence
                if isinstance(evidence, dict)
                and "staged_artifact_ref" in evidence
                and "intended_target" in evidence
            ]
            if not promotions:
                continue
            # A deliverable has one exact promotion provenance record. Multiple
            # records would make the public target ambiguous and fail closed.
            if len(promotions) != 1:
                return None
            evidence = promotions[0]
            staged = evidence.get("staged_artifact_ref")
            target = evidence.get("intended_target")
            artifact_parts = _safe_reference(item.artifact_ref)
            staged_parts = _safe_reference(staged)
            target_parts = _safe_reference(target)
            if (
                artifact_parts is None or staged_parts is None or target_parts is None
                or tuple(artifact_parts) != tuple(staged_parts)
                or len(target_parts) < 3 or target_parts[:2] != ("app", "dist")
            ):
                return None
            relative = Path(*target_parts[2:]).as_posix()
            existing = candidates.get(relative)
            if existing is None or item.version > existing[1]:
                candidates[relative] = (item.verified_hash, item.version)
            elif item.version == existing[1] and not hmac.compare_digest(item.verified_hash, existing[0]):
                # Two equally current records disagreeing on a public path is
                # ambiguous; a preview must never choose one silently.
                return None
        manifest = {path: digest for path, (digest, _version) in candidates.items()}
        for relative, digest in manifest.items():
            read = _read_preview_file(workspace, relative)
            if read is None or not hmac.compare_digest(read[1], digest):
                return None
        if "index.html" not in manifest:
            return None
        entries = [(path, digest, version) for path, (digest, version) in candidates.items()]
        payload = json.dumps(sorted(entries), separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(payload).hexdigest(), manifest
    except Exception:
        return None


def _promoted_manifest(tenant_id: str, project_id: str) -> tuple[str, dict[str, str]] | None:
    collaboration = JsonCollaborationRepository(_rooms_root)
    try:
        raw_room = collaboration.get_room(tenant_id, project_id)
        snapshots = {member.actor_id: member for member in collaboration.visible_members(raw_room)}
        with collaboration.room_lock(tenant_id, project_id) as room:
            current = {
                actor_id: member for actor_id, member in snapshots.items() if member in room.members
            }
            return _promoted_manifest_from_snapshot(tenant_id, project_id, room, current)
    except Exception:
        return None


def _cors_headers(config: PreviewOriginConfig) -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": config.control_origin,
        "Access-Control-Allow-Credentials": "true",
        "Vary": "Origin",
    }


def _preview_headers(config: PreviewOriginConfig) -> dict[str, str]:
    return {
        "Cache-Control": "private, no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "form-action 'self'; base-uri 'none'; object-src 'none'; "
            f"frame-ancestors {config.control_origin}"
        ),
    }


def _capability_name(project_id: str) -> str:
    digest = hashlib.sha256(project_id.encode("utf-8")).hexdigest()[:20]
    return f"mission_preview_{digest}"


def _preview_unavailable() -> HTTPException:
    return HTTPException(404, {"code": "preview_unavailable", "message": "Preview is unavailable."})


def _room_visibility_snapshot(
    tenant_id: str, project_id: str,
) -> tuple[JsonCollaborationRepository, dict[str, Any]]:
    repository = JsonCollaborationRepository(_rooms_root)
    room = repository.get_room(tenant_id, project_id)
    return repository, {member.actor_id: member for member in repository.visible_members(room)}


@router.post("/projects/{project_id}/preview/exchanges")
def create_exchange(
    project_id: str,
    body: _ExchangeBody,
    request: Request,
    ctx: Annotated[AuthContext, Depends(require_project_access("project:read"))],
) -> dict[str, str]:
    del body
    config = _require_config()
    if not _host_matches(request, config.control_host):
        raise _origin_error()
    try:
        repository, snapshots = _room_visibility_snapshot(ctx.tenant_id, project_id)
        member = snapshots.get(ctx.user.id)
        with repository.room_lock(ctx.tenant_id, project_id) as room:
            if member is None or member not in room.members:
                raise _origin_error()
            current = {actor_id: item for actor_id, item in snapshots.items() if item in room.members}
            promoted = _promoted_manifest_from_snapshot(ctx.tenant_id, project_id, room, current)
            if promoted is None:
                raise _preview_unavailable()
            revision, _manifest = promoted
            exchange_id = secrets.token_urlsafe(24)
            claims = {
                "tenant_id": ctx.tenant_id, "project_id": project_id, "actor_id": ctx.user.id,
                "revision": revision, "origin": config.preview_origin,
                "expires_at": _now() + _EXCHANGE_TTL_SECONDS,
            }
            proof = _issue_signed(config.secret, {"exchange_id": exchange_id, **claims})
            directory, descriptor, state = _locked_exchange_state()
            try:
                state[exchange_id] = {
                    **claims, "proof": hashlib.sha256(proof.encode("utf-8")).hexdigest(), "consumed": False,
                }
                now = _now()
                state = {
                    key: value for key, value in state.items()
                    if isinstance(value, dict) and _expiry(value.get("expires_at")) > now
                }
                _write_exchange_state(directory, state)
            finally:
                _release_exchange_lock(directory, descriptor)
            return {"exchange_id": exchange_id, "exchange_proof": proof, "preview_origin": config.preview_origin}
    except HTTPException:
        raise
    except Exception as exc:
        raise _origin_error() from exc


@router.options("/preview/exchange", status_code=204)
def preview_exchange_preflight(request: Request) -> Response:
    config = _require_config()
    if not _host_matches(request, config.preview_host) or request.headers.get("origin") != config.control_origin:
        raise _origin_error()
    if request.headers.get("access-control-request-method") != "POST":
        raise _origin_error()
    requested_headers = request.headers.get("access-control-request-headers", "").lower().strip()
    if requested_headers not in {"", "content-type"}:
        raise _origin_error()
    return Response(status_code=204, headers={
        **_cors_headers(config),
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "content-type",
        "Access-Control-Max-Age": "300",
    })


@router.post("/preview/exchange", status_code=204)
def consume_exchange(body: _ConsumeExchangeBody, request: Request) -> Response:
    config = _require_config()
    if not _host_matches(request, config.preview_host) or request.headers.get("origin") != config.control_origin:
        raise _origin_error()
    signed = _read_signed(config.secret, body.exchange_proof)
    if not signed or signed.get("exchange_id") != body.exchange_id or signed.get("origin") != config.preview_origin:
        raise _origin_error()
    try:
        tenant_id, project_id, actor_id = (
            str(signed["tenant_id"]), str(signed["project_id"]), str(signed["actor_id"]),
        )
        repository, snapshots = _room_visibility_snapshot(tenant_id, project_id)
        member = snapshots.get(actor_id)
        with repository.room_lock(tenant_id, project_id) as room:
            if member is None or member not in room.members:
                raise _origin_error()
            current = {key: item for key, item in snapshots.items() if item in room.members}
            directory, descriptor, state = _locked_exchange_state()
            try:
                stored = state.get(body.exchange_id)
                now = _now()
                expected_proof = hashlib.sha256(body.exchange_proof.encode("utf-8")).hexdigest()
                if (
                    not isinstance(stored, dict) or stored.get("consumed") is not False
                    or not hmac.compare_digest(str(stored.get("proof", "")), expected_proof)
                    or _expiry(stored.get("expires_at")) <= now
                    or any(stored.get(key) != signed.get(key) for key in (
                        "tenant_id", "project_id", "actor_id", "revision", "origin", "expires_at",
                    ))
                ):
                    raise _origin_error()
                promoted = _promoted_manifest_from_snapshot(tenant_id, project_id, room, current)
                if promoted is None or promoted[0] != stored["revision"]:
                    raise _preview_unavailable()
                stored["consumed"] = True
                state[body.exchange_id] = stored
                _write_exchange_state(directory, state)
            finally:
                _release_exchange_lock(directory, descriptor)
            capability = _issue_signed(config.secret, {
                "tenant_id": stored["tenant_id"], "project_id": stored["project_id"],
                "actor_id": stored["actor_id"], "revision": stored["revision"],
                "expires_at": _now() + _CAPABILITY_TTL_SECONDS,
            })
            response = Response(status_code=204, headers=_cors_headers(config))
            response.set_cookie(
                key=_capability_name(str(stored["project_id"])), value=capability,
                max_age=_CAPABILITY_TTL_SECONDS, httponly=True, secure=True, samesite="none",
                path=f"/projects/{stored['project_id']}/preview",
            )
            return response
    except HTTPException:
        raise
    except Exception as exc:
        raise _origin_error() from exc


def _capability_claims(request: Request, project_id: str, config: PreviewOriginConfig) -> dict[str, Any]:
    if not _host_matches(request, config.preview_host):
        raise _origin_error()
    token = request.cookies.get(_capability_name(project_id))
    claims = _read_signed(config.secret, token) if isinstance(token, str) else None
    if (
        not claims or claims.get("project_id") != project_id or _expiry(claims.get("expires_at")) <= _now()
        or not isinstance(claims.get("tenant_id"), str) or not isinstance(claims.get("actor_id"), str)
        or not isinstance(claims.get("revision"), str)
    ):
        raise _origin_error()
    return claims


def _capability_for_request(request: Request, project_id: str, config: PreviewOriginConfig) -> tuple[dict[str, Any], dict[str, str]]:
    claims = _capability_claims(request, project_id, config)
    tenant_id, actor_id = str(claims["tenant_id"]), str(claims["actor_id"])
    repository, snapshots = _room_visibility_snapshot(tenant_id, project_id)
    member = snapshots.get(actor_id)
    with repository.room_lock(tenant_id, project_id) as room:
        if member is None or member not in room.members:
            raise _origin_error()
        current = {key: item for key, item in snapshots.items() if item in room.members}
        promoted = _promoted_manifest_from_snapshot(tenant_id, project_id, room, current)
        if promoted is None or promoted[0] != claims["revision"]:
            raise _preview_unavailable()
        return claims, promoted[1]


@router.get("/projects/{project_id}/preview")
@router.get("/projects/{project_id}/preview/")
@router.get("/projects/{project_id}/preview/{full_path:path}")
def serve_preview(project_id: str, request: Request, full_path: str = "") -> Response:
    config = _require_config()
    claims = _capability_claims(request, project_id, config)
    tenant_id, actor_id = str(claims["tenant_id"]), str(claims["actor_id"])
    try:
        repository, snapshots = _room_visibility_snapshot(tenant_id, project_id)
        member = snapshots.get(actor_id)
        workspace = project_dir(project_id)
    except Exception as exc:
        raise _preview_unavailable() from exc
    with repository.room_lock(tenant_id, project_id) as room:
        if member is None or member not in room.members:
            raise _origin_error()
        current = {key: item for key, item in snapshots.items() if item in room.members}
        promoted = _promoted_manifest_from_snapshot(tenant_id, project_id, room, current)
        if promoted is None or promoted[0] != claims["revision"]:
            raise _preview_unavailable()
        manifest = promoted[1]
        relative = (full_path or "index.html").lstrip("/")
        value = _read_preview_file(workspace, relative) if relative in manifest else None
        if value is None and not Path(relative).suffix:
            value = _read_preview_file(workspace, "index.html")
            relative = "index.html"
        if value is None or relative not in manifest or not hmac.compare_digest(value[1], manifest[relative]):
            raise _preview_unavailable()
        content, _digest = value
        media_type, _encoding = mimetypes.guess_type(relative)
        if relative.endswith(".html"):
            media_type = "text/html"
        return Response(
            content=content, media_type=media_type or "application/octet-stream",
            headers=_preview_headers(config),
        )
