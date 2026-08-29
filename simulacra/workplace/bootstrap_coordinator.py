"""Durable, recoverable creation of a Mission workspace."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping

from simulacra.collaboration import CollaborationService, JsonCollaborationRepository
from simulacra.collaboration.errors import ConflictError
from simulacra.demo.operation_graph_builder import (
    bootstrap_graph_candidate_hash,
    build_bootstrap_graph,
)
from simulacra.demo.runs import create_project, load_state, project_dir, file_hash
from simulacra.demo.sources import add_upload, sync_data_room
from simulacra.missions import JsonMissionRepository, MissionService
from simulacra.operation_graph import OperationGraphStore
from simulacra.operation_graph.errors import (
    GraphValidationError,
    RevisionConflictError,
    RevisionNotFoundError,
)
from simulacra.demo.paths import RUNS_DIR
from .source_staging import SourceStaging


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _canonical(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _safe_id(value: str, label: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 128 or any(x in value for x in ("/", "\\", "..")):
        raise ValueError(f"invalid {label}")
    return value


def _atomic_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, sort_keys=True, separators=(",", ":")); handle.flush(); os.fsync(handle.fileno())
        os.replace(temp, path)
        _fsync_dir(path.parent)
    finally:
        if os.path.exists(temp): os.unlink(temp)


def _fsync_dir(directory: Path) -> None:
    """Re-establish a directory-entry durability barrier before a replay."""
    try:
        mode = os.lstat(directory).st_mode
    except OSError as exc:
        raise ValueError("bootstrap storage unavailable") from exc
    if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
        raise ValueError("bootstrap storage unavailable")
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise ValueError("bootstrap storage unavailable")
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class BootstrapAbortedError(ValueError): pass


class WorkspaceBootstrapCoordinator:
    RETRY_AFTER_SECONDS = 2
    def __init__(self, *, runs_root: str | Path | None = None,
                 collaboration_root: str | Path | None = None, mission_root: str | Path | None = None) -> None:
        # ``resolve`` would silently walk a control-root symlink.  Bootstrap
        # journals decide whether a Mission is visible, so treat their whole
        # ancestor chain as an untrusted descriptor boundary.
        self.runs_root = Path(runs_root or RUNS_DIR).absolute()
        self._assert_absolute_ancestors(self.runs_root)
        control = self.runs_root / ".workplace-control"
        self.root = control / "bootstrap-transactions"; self._mkdir(self.root)
        self.sources = SourceStaging(control / "source-staging")
        self.collaboration = CollaborationService(JsonCollaborationRepository(collaboration_root or self.runs_root / ".cmul8-control"))
        self.missions = MissionService(JsonMissionRepository(mission_root or self.runs_root / ".mission-control"))

    @staticmethod
    def _assert_absolute_ancestors(path: Path) -> None:
        absolute = path.absolute()
        current = Path(absolute.anchor)
        for part in absolute.parts[1:]:
            current = current / part
            try:
                mode = os.lstat(current).st_mode
            except FileNotFoundError:
                return
            except OSError as exc:
                raise ValueError("bootstrap storage unavailable") from exc
            if stat.S_ISLNK(mode):
                raise ValueError("bootstrap storage unavailable")
            if current != absolute and not stat.S_ISDIR(mode):
                raise ValueError("bootstrap storage unavailable")

    def _assert_safe(self, path: Path, *, require_file: bool = False, require_dir: bool = False) -> None:
        self._assert_absolute_ancestors(self.root)
        try:
            path.absolute().relative_to(self.root)
        except ValueError as exc:
            raise ValueError("bootstrap storage unavailable") from exc
        current = self.root
        relative = path.absolute().relative_to(self.root)
        for part in (".", *relative.parts):
            if part != ".":
                current = current / part
            try:
                mode = os.lstat(current).st_mode
            except FileNotFoundError:
                break
            except OSError as exc:
                raise ValueError("bootstrap storage unavailable") from exc
            if stat.S_ISLNK(mode):
                raise ValueError("bootstrap storage unavailable")
            if current != path and not stat.S_ISDIR(mode):
                raise ValueError("bootstrap storage unavailable")
        try:
            mode = os.lstat(path).st_mode
        except FileNotFoundError:
            if require_file or require_dir:
                raise ValueError("bootstrap storage unavailable")
            return
        if stat.S_ISLNK(mode) or (require_file and not stat.S_ISREG(mode)) or (require_dir and not stat.S_ISDIR(mode)):
            raise ValueError("bootstrap storage unavailable")

    def _mkdir(self, path: Path) -> None:
        self._assert_absolute_ancestors(self.runs_root)
        # Only create checked components beneath the supplied runs root.
        try:
            path.absolute().relative_to(self.runs_root)
        except ValueError as exc:
            raise ValueError("bootstrap storage unavailable") from exc
        current = self.runs_root
        if not current.exists():
            current.mkdir(parents=True, exist_ok=True)
        self._assert_absolute_ancestors(current)
        for part in path.absolute().relative_to(self.runs_root).parts:
            current = current / part
            try:
                current.mkdir()
            except FileExistsError:
                pass
            self._assert_absolute_ancestors(current)
            mode = os.lstat(current).st_mode
            if stat.S_ISLNK(mode) or not stat.S_ISDIR(mode):
                raise ValueError("bootstrap storage unavailable")
        self._assert_safe(path, require_dir=True)

    def _path(self, tenant_id: str, actor_id: str, client_request_id: str) -> Path:
        return self.root / _safe_id(tenant_id, "tenant") / _safe_id(actor_id, "actor") / "workspace_bootstrap" / f"{_safe_id(client_request_id, 'client request id')}.json"

    @contextmanager
    def _locked(self, tenant_id: str):
        lock = self.root / _safe_id(tenant_id, "tenant") / ".coordinator.lock"; self._mkdir(lock.parent)
        self._assert_safe(lock)
        descriptor = os.open(lock, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("bootstrap storage unavailable")
            handle = os.fdopen(descriptor, "a+b")
        except Exception:
            os.close(descriptor)
            raise
        with handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _project_id(tenant_id: str, actor_id: str, request_id: str) -> str:
        return "proj_" + hashlib.sha256(f"{tenant_id}\0{actor_id}\0{request_id}".encode()).hexdigest()[:16]

    def _read(self, path: Path) -> dict[str, Any]:
        # A record that exists after a failed post-replace sync is not trusted
        # until this process has successfully synced the parent directory.
        self._assert_safe(path, require_file=True)
        _fsync_dir(path.parent)
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                raise ValueError("invalid bootstrap record")
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                value = json.load(handle)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise
        if not isinstance(value, dict): raise ValueError("invalid bootstrap record")
        try:
            relative = path.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("bootstrap record escaped control root") from exc
        if len(relative.parts) != 4 or relative.parts[2] != "workspace_bootstrap" or path.suffix != ".json":
            raise ValueError("bootstrap record path is invalid")
        tenant_id, actor_id, _operation, filename = relative.parts
        request_id = filename[:-5]
        for value_id, label in ((tenant_id, "tenant"), (actor_id, "actor"), (request_id, "client request id")):
            _safe_id(value_id, label)
        request = value.get("request")
        source_hashes = value.get("source_hashes")
        if (
            value.get("schema_version") != 1
            or value.get("tenant_id") != tenant_id
            or value.get("authenticated_human_actor_id") != actor_id
            or value.get("operation") != "workspace_bootstrap"
            or value.get("client_request_id") != request_id
            or not isinstance(request, dict)
            or not isinstance(source_hashes, list)
            or any(not isinstance(item, str) or not re.fullmatch(r"[0-9a-f]{64}", item) for item in source_hashes)
            or not isinstance(value.get("staged_source_refs"), list)
            or any(not isinstance(item, str) for item in value["staged_source_refs"])
            or value.get("state") not in {"PREPARED", "COMMIT_DECIDED", "STORES_DURABLE", "COMPLETE", "ABORTED"}
            or not isinstance(value.get("transaction_id"), str)
            or not re.fullmatch(r"bootstrap_[0-9a-f]{32}", value["transaction_id"])
            or value.get("reserved_project_id") != self._project_id(tenant_id, actor_id, request_id)
        ):
            raise ValueError("bootstrap record identity is invalid")
        payload = {key: request.get(key) for key in ("prompt", "goal", "design_brief", "artifact_kind", "staged_source_refs")}
        if payload.get("staged_source_refs") != value["staged_source_refs"]:
            raise ValueError("bootstrap source references are invalid")
        expected_hash = _canonical({"tenant_id": tenant_id, "actor_id": actor_id, "request": payload, "source_hashes": source_hashes})
        if value.get("canonical_request_hash") != expected_hash:
            raise ValueError("bootstrap request identity is invalid")
        intent = value.get("graph_build_intent")
        result = value.get("graph_result_revision")
        if intent is not None:
            if not isinstance(intent, dict) or (
                intent.get("tenant_id"), intent.get("project_id"), intent.get("owner_id"), intent.get("reservation_hash")
            ) != (tenant_id, value["reserved_project_id"], actor_id, expected_hash) or not isinstance(intent.get("canonical_graph_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", intent["canonical_graph_hash"]):
                raise ValueError("bootstrap graph intent is invalid")
        if result is not None:
            if intent is None or not isinstance(result, str) or not re.fullmatch(r"[0-9a-f]{64}", result) or result != intent["canonical_graph_hash"]:
                raise ValueError("bootstrap graph result is invalid")
        return value

    def _write(self, path: Path, record: dict[str, Any]) -> dict[str, Any]:
        # Never hand a caller a state that only existed in process memory.  A
        # retry/restart must observe exactly the same durable journal record.
        candidate = {**record, "updated_at": _now()}
        self._mkdir(path.parent)
        self._assert_safe(path)
        _atomic_json(path, candidate)
        return self._read(path)

    def _advance(self, path: Path, prior: dict[str, Any], **changes: Any) -> dict[str, Any]:
        """Best-effort state advance that never reports an uncertain write."""
        candidate = {**prior, **changes}
        try:
            return self._write(path, candidate)
        except OSError:
            # ``os.replace`` may already have happened, but its directory sync
            # did not.  Return exactly the prior durable state; the next retry
            # will fsync and validate the on-disk record before using it.
            return {**prior, "_durability_uncertain": True}

    @staticmethod
    def _project_contract(state: Any) -> dict[str, Any]:
        return {
            "id": state.id,
            "tenant_id": state.tenant_id,
            "prompt": state.prompt,
            "goal": state.goal,
            "design_brief": state.design_brief,
            "artifact_kind": state.artifact_kind,
        }

    def reserve(self, *, tenant_id: str, actor_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        request_id = _safe_id(str(request.get("client_request_id") or ""), "client request id")
        payload = {key: request.get(key) for key in ("prompt", "goal", "design_brief", "artifact_kind", "staged_source_refs")}
        if not isinstance(payload["prompt"], str) or len(payload["prompt"].strip()) < 3: raise ValueError("prompt is required")
        if not isinstance(payload["staged_source_refs"], list) or any(not isinstance(item, str) for item in payload["staged_source_refs"]): raise ValueError("staged source references are required")
        sources = self.sources.resolve_many(tenant_id=tenant_id, actor_id=actor_id, source_refs=payload["staged_source_refs"])
        digest = _canonical({"tenant_id": tenant_id, "actor_id": actor_id, "request": payload,
                             "source_hashes": [item.canonical_content_sha256 for item in sources]})
        path = self._path(tenant_id, actor_id, request_id)
        with self._locked(tenant_id):
            self._assert_safe(path)
            if path.exists():
                record = self._read(path)
                if record.get("canonical_request_hash") != digest: raise ValueError("idempotency_mismatch")
                return record
            # Resolve before publication: an unrelated actor cannot name a source.
            record = {
                "schema_version": 1, "transaction_id": f"bootstrap_{uuid.uuid4().hex}",
                "tenant_id": tenant_id, "authenticated_human_actor_id": actor_id,
                "operation": "workspace_bootstrap", "client_request_id": request_id,
                "canonical_request_hash": digest, "reserved_project_id": self._project_id(tenant_id, actor_id, request_id),
                "staged_source_refs": payload["staged_source_refs"], "source_hashes": [item.canonical_content_sha256 for item in sources], "request": payload,
                "graph_build_intent": None, "graph_result_revision": None, "state": "PREPARED",
                "created_at": _now(), "updated_at": _now(),
            }
            return self._write(path, record)

    def _adopt_sources(self, record: dict[str, Any]) -> None:
        tenant, actor, project = record["tenant_id"], record["authenticated_human_actor_id"], record["reserved_project_id"]
        try:
            sources = self.sources.resolve_many(tenant_id=tenant, actor_id=actor, source_refs=record["staged_source_refs"])
        except (KeyError, ValueError) as exc:
            # Before the decision boundary, a missing/tampered immutable source
            # cannot become a permanent "provisioning" spinner.  Preserve the
            # journal and terminate it with the stable public aborted state.
            raise BootstrapAbortedError("Mission sources are unavailable") from exc
        for source in sources:
            target = project_dir(project) / "inputs" / "data-room" / source.normalized_filename
            if target.exists():
                sync_data_room(project)
                if file_hash(target).removeprefix("sha256:") != source.canonical_content_sha256:
                    raise BootstrapAbortedError("source adoption conflict")
                continue
            add_upload(project, filename=source.normalized_filename, data=self.sources.blob_bytes(source), overwrite=False)

    def _verify_room(self, record: Mapping[str, Any]) -> None:
        room = self.collaboration.repository.get_room(record["tenant_id"], record["reserved_project_id"])
        owner = next((member for member in room.members if member.actor_id == record["authenticated_human_actor_id"]), None)
        if room.tenant_id != record["tenant_id"] or room.project_id != record["reserved_project_id"] or owner is None or owner.role != "owner":
            raise BootstrapAbortedError("Mission workspace ownership is inconsistent")

    def _recover_room(self, record: Mapping[str, Any]) -> None:
        try:
            self.collaboration.create_room(
                tenant_id=record["tenant_id"], project_id=record["reserved_project_id"],
                creator_id=record["authenticated_human_actor_id"], creator_role="owner",
            )
        except ConflictError as exc:
            # Only the exact replay collision can be recovered.  Treating any
            # room failure as a replay could attach a Mission to another owner.
            if str(exc) != "project room already exists":
                raise
        self._verify_room(record)

    def _verify_complete(self, record: Mapping[str, Any]) -> None:
        """Re-read every durable child before reporting a Mission ready."""
        project = record["reserved_project_id"]
        state = load_state(project)
        contract = record.get("project_contract")
        if (
            not isinstance(contract, Mapping)
            or self._project_contract(state) != dict(contract)
            or state.prime.get("bootstrap_request_hash") != record.get("canonical_request_hash")
            or state.prompt != record.get("request", {}).get("prompt")
            or state.goal != (record.get("request", {}).get("goal") or "")
        ):
            raise BootstrapAbortedError("Mission project is inconsistent")
        mission = self.missions.mission(record["tenant_id"], project)
        if mission.project_id != project or mission.tenant_id != record["tenant_id"] or mission.owner_id != record["authenticated_human_actor_id"]:
            raise BootstrapAbortedError("Mission record is inconsistent")
        self._verify_room(record)
        sources = self.sources.resolve_many(
            tenant_id=record["tenant_id"], actor_id=record["authenticated_human_actor_id"],
            source_refs=record["staged_source_refs"],
        )
        sync_data_room(project)
        if [source.canonical_content_sha256 for source in sources] != record.get("source_hashes"):
            raise BootstrapAbortedError("Mission sources are inconsistent")
        for source in sources:
            target = project_dir(project) / "inputs" / "data-room" / source.normalized_filename
            if not target.is_file() or target.is_symlink() or file_hash(target).removeprefix("sha256:") != source.canonical_content_sha256:
                raise BootstrapAbortedError("Mission sources are inconsistent")
        intent = record.get("graph_build_intent") or {}
        revision_hash = record.get("graph_result_revision")
        if not isinstance(revision_hash, str) or not isinstance(intent.get("canonical_graph_hash"), str):
            raise BootstrapAbortedError("Mission graph is incomplete")
        store = OperationGraphStore(project_dir(project), tenant_id=record["tenant_id"], project_id=project)
        try:
            revision = store.require_exact_current_revision_head(revision_hash)
        except Exception as exc:
            raise BootstrapAbortedError("Mission graph is inconsistent") from exc
        if (
            revision.tenant_id != record["tenant_id"]
            or revision.project_id != project
            or revision.revision_hash != intent["canonical_graph_hash"]
            or revision.graph.get("metadata", {}).get("tenant_id") != record["tenant_id"]
            or revision.graph.get("metadata", {}).get("project_id") != project
        ):
            raise BootstrapAbortedError("Mission graph is inconsistent")

    def recover(self, record: dict[str, Any]) -> dict[str, Any]:
        path = self._path(record["tenant_id"], record["authenticated_human_actor_id"], record["client_request_id"])
        with self._locked(record["tenant_id"]):
            record = self._read(path)
            if record["state"] == "COMPLETE":
                try:
                    self._verify_complete(record)
                except BootstrapAbortedError:
                    # Completion is immutable; public() independently gates
                    # visibility on reread validation and returns provisioning.
                    pass
                return record
            if record["state"] == "ABORTED": return record
            try:
                payload = record["request"]; project = record["reserved_project_id"]
                state = create_project(payload["prompt"], project_id=project, goal=payload.get("goal") or "",
                    design_brief=payload.get("design_brief"), tenant_id=record["tenant_id"], artifact_kind=payload.get("artifact_kind"),
                    bootstrap_request_hash=record["canonical_request_hash"])
                if record.get("project_contract") is None:
                    record = self._advance(path, record, project_contract=self._project_contract(state))
                    if record.get("project_contract") is None:
                        return record
                self._recover_room(record)
                self.missions.bootstrap(record["tenant_id"], project, record["authenticated_human_actor_id"], {
                    "title": state.app_config.title, "objective": payload.get("goal") or payload["prompt"],
                })
                self._adopt_sources(record)
                if record.get("graph_build_intent") is None:
                    graph_hash = bootstrap_graph_candidate_hash(state)
                    intent = {"project_id": project, "tenant_id": record["tenant_id"],
                        "owner_id": record["authenticated_human_actor_id"],
                        "canonical_graph_hash": graph_hash,
                        "reservation_hash": record["canonical_request_hash"]}
                    record = self._advance(path, record, graph_build_intent=intent)
                    if record.get("graph_build_intent") is None:
                        return record
                if not record.get("graph_result_revision") and record["state"] == "PREPARED":
                    intent = record["graph_build_intent"]
                    try:
                        revision, status = build_bootstrap_graph(
                            state, actor_id=record["authenticated_human_actor_id"],
                            expected_tenant_id=intent["tenant_id"], expected_project_id=intent["project_id"],
                            expected_graph_hash=intent["canonical_graph_hash"],
                        )
                    except (GraphValidationError, RevisionConflictError, RevisionNotFoundError, ValueError) as exc:
                        # These are deterministic reservation/graph conflicts.
                        # Before COMMIT_DECIDED they are safely terminal rather
                        # than an endless provisioning loop.
                        raise BootstrapAbortedError("Mission graph could not be finalized") from exc
                    if revision != intent["canonical_graph_hash"]:
                        raise BootstrapAbortedError("Mission graph could not be finalized")
                    record = self._advance(path, record, graph_result_revision=revision, graph_status=status)
                    if record.get("graph_result_revision") is None:
                        return record
                if record["state"] == "PREPARED":
                    # This complete read is the decision boundary.  It makes a
                    # retry after a crash finish the same reservation, never a
                    # new one.
                    self._verify_complete({**record, "state": "COMPLETE"})
                    record = self._advance(path, record, state="COMMIT_DECIDED")
                    if record["state"] != "COMMIT_DECIDED":
                        return record
                if record["state"] == "COMMIT_DECIDED":
                    self._verify_complete({**record, "state": "COMPLETE"})
                    record = self._advance(path, record, state="STORES_DURABLE")
                    if record["state"] != "STORES_DURABLE":
                        return record
                if record["state"] == "STORES_DURABLE":
                    self._verify_complete({**record, "state": "COMPLETE"})
                    return self._advance(path, record, state="COMPLETE")
                return record
            except BootstrapAbortedError:
                if record.get("state") == "PREPARED":
                    return self._advance(path, record, state="ABORTED")
                raise
            except Exception:
                # Any ordinary failure remains recoverable and is intentionally not public raw detail.
                try:
                    return self._read(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    # A corrupt or unavailable journal can never be promoted;
                    # callers map it to the public unavailable/provisioning path.
                    return {**record, "state": "PREPARED"}

    def begin(self, *, tenant_id: str, actor_id: str, request: Mapping[str, Any]) -> dict[str, Any]:
        return self.recover(self.reserve(tenant_id=tenant_id, actor_id=actor_id, request=request))

    def lookup(self, *, tenant_id: str, actor_id: str, transaction_id: str) -> dict[str, Any]:
        for path in self._path(tenant_id, actor_id, "x").parent.glob("*.json"):
            try:
                record = self._read(path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            if record.get("transaction_id") == transaction_id:
                # A corrupted terminal child must be hidden from the public
                # ready response, not turned into a raw route failure.  Its
                # immutable journal remains available for controlled repair.
                try:
                    return self.recover(record)
                except BootstrapAbortedError:
                    return record
        raise KeyError("bootstrap unavailable")

    def public(self, record: Mapping[str, Any]) -> tuple[int, dict[str, Any]]:
        required = ("transaction_id", "reserved_project_id", "state")
        if any(not isinstance(record.get(key), str) or not record.get(key) for key in required):
            return 404, {"code": "bootstrap_unavailable", "message": "Mission setup is unavailable."}
        if record.get("_durability_uncertain"):
            return 202, {"transaction_id": record["transaction_id"], "status": record["state"],
                "project_id": record["reserved_project_id"], "provisioning": True, "retry_after_seconds": self.RETRY_AFTER_SECONDS}
        try:
            durable = self._read(self._path(
                str(record.get("tenant_id") or ""), str(record.get("authenticated_human_actor_id") or ""),
                str(record.get("client_request_id") or ""),
            ))
        except (OSError, ValueError, json.JSONDecodeError):
            return 404, {"code": "bootstrap_unavailable", "message": "Mission setup is unavailable."}
        record = durable
        if record["state"] == "ABORTED": return 409, {"code": "bootstrap_aborted", "message": "Mission setup could not be completed."}
        if record["state"] != "COMPLETE": return 202, {"transaction_id": record["transaction_id"], "status": record["state"],
            "project_id": record["reserved_project_id"], "provisioning": True, "retry_after_seconds": self.RETRY_AFTER_SECONDS}
        try:
            self._verify_complete(record)
        except Exception:
            # A damaged completed journal never becomes a false-ready Mission.
            return 202, {"transaction_id": record["transaction_id"], "status": "STORES_DURABLE",
                "project_id": record["reserved_project_id"], "provisioning": True, "retry_after_seconds": self.RETRY_AFTER_SECONDS}
        state = load_state(record["reserved_project_id"])
        mission = self.missions.mission(record["tenant_id"], record["reserved_project_id"])
        return 200, {"transaction_id": record["transaction_id"], "status": "COMPLETE", "provisioning": False,
            "project": {"id": state.id, "prompt": state.prompt, "goal": state.goal, "artifact_kind": state.artifact_kind},
            "readiness": {"status": "ready_for_approval", "graph_revision": record["graph_result_revision"]},
            "recommended_crew": [], "permissions": {"can_invite": True},
            "workspace_state": {"mission_id": mission.id, "status": mission.status}}

    def project_is_public(self, *, tenant_id: str, project_id: str) -> bool:
        """Return whether a bootstrap-created Mission can enter normal views.

        A bootstrap marker is the authority boundary.  Once it exists, an
        absent, mismatched, corrupt, incomplete, or damaged journal must fail
        closed: it could be a recoverable half-created Mission.  Older
        Mission/room records have no marker (or no runnable-project state) and
        retain their historical visibility.
        """
        try:
            state = load_state(project_id)
        except Exception:
            return False
        reservation_hash = state.prime.get("bootstrap_request_hash") if isinstance(state.prime, dict) else None
        if not isinstance(reservation_hash, str) or not reservation_hash:
            return True
        try:
            self._assert_safe(self.root, require_dir=True)
            for path in self.root.glob("*/*/workspace_bootstrap/*.json"):
                try:
                    record = self._read(path)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if record.get("tenant_id") != tenant_id or record.get("reserved_project_id") != project_id:
                    continue
                if record.get("canonical_request_hash") != reservation_hash or record.get("state") != "COMPLETE":
                    return False
                self._verify_complete(record)
                return True
        except Exception:
            return False
        return False

    def recovery_tick(self, *, limit: int = 100) -> int:
        paths = sorted(self.root.glob("*/*/workspace_bootstrap/*.json"))
        recovered = 0
        protected_hashes: set[str] = set()
        for path in paths:
            try:
                record = self._read(path)
                if record.get("state") in {"COMPLETE", "ABORTED"}:
                    continue
                protected_hashes.update(hash_value for hash_value in record.get("source_hashes", []) if isinstance(hash_value, str))
                if recovered >= limit:
                    continue
                self.recover(record); recovered += 1
            except Exception: continue
        self.sources.gc_orphans(limit=limit, protected_hashes=protected_hashes)
        return recovered
