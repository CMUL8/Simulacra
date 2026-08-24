"""Codex adapter over the official ``codex app-server`` JSONL protocol.

The adapter intentionally launches the official app-server and parses its
structured JSONL protocol; it never treats terminal output as a product API.
"""

from __future__ import annotations

import inspect
import asyncio
import hashlib
import json
import os
import signal
import shutil
import uuid
import stat
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

from .base import AgentHarness
from .contracts import AgentRunRequest, AgentSession, ModelCapability, NetworkPolicy, TerminalStatus


_MISSION_SANDBOX_LAUNCHER = Path("/opt/cmul8/bin/cmul8-mission-sandbox")
_MAX_ISOLATION_MANIFEST_BYTES = 64 * 1024
_MAX_LAUNCHER_BYTES = 4 * 1024 * 1024
_ACTIVE_CODEX_GROUPS: set[int] = set()
_ACTIVE_CODEX_GROUPS_LOCK = threading.Lock()


def signal_active_codex_process_groups(sig: int) -> None:
    """Best-effort synchronous fail-safe for deploy-process shutdown."""
    if os.name != "posix":
        return
    try:
        current = os.getpgrp()
    except OSError:
        current = -1
    with _ACTIVE_CODEX_GROUPS_LOCK:
        groups = tuple(_ACTIVE_CODEX_GROUPS)
    for group in groups:
        if group == current:
            continue
        try:
            os.killpg(group, sig)
        except ProcessLookupError:
            pass


@dataclass(frozen=True, slots=True)
class _IsolatedFile:
    """An inode-and-content snapshot acquired without following symlinks."""

    _path: Path
    _device: int
    _inode: int
    _sha256: str


def _read_no_follow(path: Path, *, maximum: int) -> tuple[os.stat_result, bytes]:
    """Read a regular file while binding the bytes to its opened inode.

    The manifest and launcher are part of the security boundary, so a path check
    followed by a normal open is insufficient: it permits a replacement between
    validation and use.  O_NOFOLLOW plus fstat makes the opened file the object
    we validate and hash.
    """
    no_follow = getattr(os, "O_NOFOLLOW", None)
    if no_follow is None:
        raise RuntimeError("external sandbox requires O_NOFOLLOW support")
    flags = os.O_RDONLY | no_follow | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("external sandbox files unavailable") from exc
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode):
            raise RuntimeError("external sandbox file must be a regular file")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise RuntimeError("external sandbox file exceeds size limit")
            chunks.append(chunk)
        return info, b"".join(chunks)
    finally:
        os.close(descriptor)


def _canonical_path(workspace: Path, value: Path) -> str:
    candidate = value if value.is_absolute() else workspace / value
    return str(candidate.resolve(strict=False))


def _canonical_roots(workspace: Path, values: Iterable[Path]) -> list[str]:
    return sorted({_canonical_path(workspace, value) for value in values})


def _mission_project_override(workspace: Path) -> str:
    """Return a TOML-safe, session-flag project trust override."""
    rendered = str(workspace.resolve())
    if any(ord(character) < 32 for character in rendered):
        raise RuntimeError("unsafe Mission workspace path")
    # JSON strings are TOML basic strings, including the required escaping for
    # quotes and backslashes in a platform path.
    return f"projects={{{json.dumps(rendered)}={{trust_level=\"untrusted\"}}}}"


_SEMANTIC_TOOL_TYPES = frozenset({"commandExecution", "fileChange", "mcpToolCall"})


def _semantic_tool_item(notification: Mapping[str, Any]) -> tuple[str, Mapping[str, Any]] | None:
    """Extract a real Codex 0.148 tool lifecycle notification, if present."""
    params = notification.get("params")
    method = notification.get("method")
    if (
        method not in {"item/started", "item/completed"}
        or not isinstance(params, Mapping)
        or not isinstance(params.get("item"), Mapping)
        or params["item"].get("type") not in _SEMANTIC_TOOL_TYPES
    ):
        return None
    return method, params["item"]


class _SemanticStepCounter:
    """Count tool admission once, with completion-only fail-closed fallback.

    Codex normally emits ``item/started`` before a tool can act. Counting that
    event enforces the budget before the N+1 action. Some protocol versions or
    out-of-order streams can omit it; then a completed item consumes a step as
    a conservative fallback. Item ids prevent lifecycle double counting.
    """

    def __init__(self) -> None:
        self.steps = 0
        self._started_ids: set[str] = set()
        self._completed_ids: set[str] = set()
        self._anonymous_started_pending = 0

    def observe(self, notification: Mapping[str, Any]) -> bool:
        event = _semantic_tool_item(notification)
        if event is None:
            return False
        method, item = event
        item_id = item.get("id")
        identifier = item_id if isinstance(item_id, str) and item_id else None
        if method == "item/started":
            if identifier is not None:
                if identifier in self._started_ids:
                    return False
                self._started_ids.add(identifier)
                # A completion that arrived first already consumed this action.
                if identifier in self._completed_ids:
                    return False
            else:
                self._anonymous_started_pending += 1
            self.steps += 1
            return True
        if identifier is not None:
            if identifier in self._completed_ids:
                return False
            self._completed_ids.add(identifier)
            if identifier in self._started_ids:
                return False
        elif self._anonymous_started_pending:
            self._anonymous_started_pending -= 1
            return False
        self.steps += 1
        return True


def _semantic_step_count(notifications: Iterable[Mapping[str, Any]]) -> int:
    """Count tool admissions, never streamed protocol narration."""
    counter = _SemanticStepCounter()
    for item in notifications:
        counter.observe(item)
    return counter.steps


@dataclass(frozen=True, slots=True)
class CodexIsolationSpec:
    """Immutable external-sandbox launch material for exactly one Mission turn.

    This object is deliberately constructed from file descriptors, not merely
    file names.  Every use re-opens and re-hashes those same files, then binds
    the parsed manifest to the exact ``AgentRunRequest`` that is about to reach
    Codex.  Production accepts only the baked root-owned launcher; tests may
    opt into a temporary launcher explicitly.
    """

    _launcher: _IsolatedFile
    _manifest: _IsolatedFile
    _workspace: str
    _read_roots: tuple[str, ...]
    _write_roots: tuple[str, ...]
    _mission_id: str
    _run_id: str
    _agent_id: str
    _invocation_id: str | None
    _execution_binding_sha256: str | None

    @classmethod
    def from_files(
        cls,
        *,
        launcher: str | Path,
        manifest: str | Path,
        allow_test_launcher: bool = False,
    ) -> "CodexIsolationSpec":
        launcher_path, manifest_path = Path(launcher), Path(manifest)
        if not launcher_path.is_absolute() or not manifest_path.is_absolute():
            raise RuntimeError("external sandbox paths must be absolute")
        if not allow_test_launcher and launcher_path != _MISSION_SANDBOX_LAUNCHER:
            raise RuntimeError("external sandbox launcher must be the baked Mission launcher")
        launcher_info, launcher_bytes = _read_no_follow(launcher_path, maximum=_MAX_LAUNCHER_BYTES)
        manifest_info, manifest_bytes = _read_no_follow(manifest_path, maximum=_MAX_ISOLATION_MANIFEST_BYTES)
        if stat.S_IMODE(launcher_info.st_mode) != 0o555:
            raise RuntimeError("unsafe external sandbox launcher")
        expected_owner = os.getuid() if allow_test_launcher else 0
        if launcher_info.st_uid != expected_owner:
            raise RuntimeError("unsafe external sandbox launcher")
        if stat.S_IMODE(manifest_info.st_mode) != 0o600 or manifest_info.st_uid != os.getuid():
            raise RuntimeError("unsafe external sandbox manifest")
        try:
            payload = json.loads(manifest_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RuntimeError("external sandbox manifest must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise RuntimeError("external sandbox manifest must be a JSON object")
        def required_text(key: str) -> str:
            value = payload.get(key)
            if not isinstance(value, str) or not value:
                raise RuntimeError(f"external sandbox manifest requires {key}")
            return value

        def roots(key: str) -> tuple[str, ...]:
            value = payload.get(key)
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise RuntimeError(f"external sandbox manifest requires {key}")
            # Store immutable scalars only; later request validation also
            # verifies that this ordering is canonical for the workspace.
            return tuple(value)

        if payload.get("network") is not False:
            raise RuntimeError("external sandbox manifest must deny network")
        return cls(
            _IsolatedFile(launcher_path, launcher_info.st_dev, launcher_info.st_ino, hashlib.sha256(launcher_bytes).hexdigest()),
            _IsolatedFile(manifest_path, manifest_info.st_dev, manifest_info.st_ino, hashlib.sha256(manifest_bytes).hexdigest()),
            required_text("workspace"),
            roots("read_roots"),
            roots("write_roots"),
            required_text("mission_id"),
            required_text("run_id"),
            required_text("agent_id"),
            required_text("invocation_id") if "invocation_id" in payload else None,
            required_text("execution_binding_sha256") if "execution_binding_sha256" in payload else None,
        )

    def _validate_unchanged(self) -> None:
        for expected, maximum in ((self._launcher, _MAX_LAUNCHER_BYTES), (self._manifest, _MAX_ISOLATION_MANIFEST_BYTES)):
            info, content = _read_no_follow(expected._path, maximum=maximum)
            if (info.st_dev, info.st_ino, hashlib.sha256(content).hexdigest()) != (expected._device, expected._inode, expected._sha256):
                raise RuntimeError("external sandbox launch material changed after validation")

    @staticmethod
    def _required_metadata(request: AgentRunRequest) -> dict[str, str]:
        values: dict[str, str] = {}
        for key in ("mission_id", "run_id", "agent_id"):
            value = request.metadata.get(key)
            if not isinstance(value, str) or not value:
                raise RuntimeError(f"isolated Mission request requires metadata.{key}")
            values[key] = value
        return values

    def request_fingerprint(self, request: AgentRunRequest) -> str:
        """Validate the live request against the immutable manifest snapshot."""
        self._validate_unchanged()
        workspace = str(request.workspace.resolve(strict=False))
        reads, writes = _canonical_roots(request.workspace.resolve(strict=False), request.read_paths), _canonical_roots(request.workspace.resolve(strict=False), request.write_paths)
        metadata = self._required_metadata(request)
        if request.network_policy is not NetworkPolicy.DENY:
            raise RuntimeError("isolated Mission requests must deny network access")
        expected: dict[str, Any] = {"workspace": workspace, "read_roots": reads, "write_roots": writes, "network": False, **metadata}
        bound: dict[str, Any] = {
            "workspace": self._workspace,
            "read_roots": list(self._read_roots),
            "write_roots": list(self._write_roots),
            "network": False,
            "mission_id": self._mission_id,
            "run_id": self._run_id,
            "agent_id": self._agent_id,
        }
        for key, expected_value in (("invocation_id", self._invocation_id), ("execution_binding_sha256", self._execution_binding_sha256)):
            if expected_value is None:
                continue
            supplied = request.metadata.get(key)
            if not isinstance(supplied, str) or supplied != expected_value:
                raise RuntimeError(f"isolated Mission request requires matching metadata.{key}")
            expected[key] = supplied
            bound[key] = expected_value
        for key, value in expected.items():
            if bound[key] != value:
                raise RuntimeError(f"external sandbox manifest does not bind request {key}")
        # Reject non-canonical root input in the manifest. This makes the
        # manifest itself auditable and prevents equivalent-but-ambiguous scope.
        for key, roots in (("read_roots", self._read_roots), ("write_roots", self._write_roots)):
            if list(roots) != sorted(set(roots)):
                raise RuntimeError(f"external sandbox manifest {key} must be sorted canonical paths")
        scope = {**expected, "project_id": request.project_id, "environment_id": request.environment_id, "role": request.role, "task_type": request.task_type.value}
        return hashlib.sha256(json.dumps(scope, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def command_prefix(self) -> tuple[str, str, str]:
        """Return the fixed launcher invocation after re-validating its inode."""
        self._validate_unchanged()
        # The launcher receives this digest as an argv value and recomputes it
        # from its O_NOFOLLOW-opened descriptor.  That closes the interval
        # between this transport's check and the launcher's own policy check.
        return str(self._launcher._path), str(self._manifest._path), self._manifest._sha256

    def cleanup_manifest(self) -> None:
        """Delete only the exact manifest inode this spec created from."""
        try:
            info = os.lstat(self._manifest._path)
        except FileNotFoundError:
            return
        if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == (self._manifest._device, self._manifest._inode):
            self._manifest._path.unlink()


@runtime_checkable
class CodexTransport(Protocol):
    """Minimal official-boundary protocol; implementations are integration-owned."""

    async def create_thread(self, *, request: AgentRunRequest, thread_id: str | None = None) -> str: ...
    async def run(self, *, request: AgentRunRequest, thread_id: str) -> Mapping[str, Any]: ...
    async def cancel(self, *, thread_id: str) -> None: ...


class CodexAppServerTransport:
    """Official Codex app-server JSONL transport.

    One app-server process is shared by the harness instance. Calls are serialized
    because product jobs already provide project-level concurrency control, while
    cancellation may still write an interrupt request during an active turn.
    """

    def __init__(
        self,
        *,
        executable: str | None = None,
        isolation_spec: CodexIsolationSpec | None = None,
        isolation_launcher: str | None = None,
        isolation_manifest: str | None = None,
    ) -> None:
        if isolation_spec is not None and (isolation_launcher is not None or isolation_manifest is not None):
            raise ValueError("pass either isolation_spec or launcher/manifest, not both")
        if isolation_spec is None and (isolation_launcher is not None or isolation_manifest is not None):
            if not isolation_launcher or not isolation_manifest:
                raise RuntimeError("external sandbox requires launcher and manifest")
            isolation_spec = CodexIsolationSpec.from_files(launcher=isolation_launcher, manifest=isolation_manifest)
        self.executable = executable or os.environ.get("CMUL8_CODEX_BIN", "codex")
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._run_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._active_turns: dict[str, str] = {}
        self._isolation_spec = isolation_spec
        self._bound_request_fingerprint: str | None = None
        self._deadline: float | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._stderr_tail = bytearray()

    def _validate_isolated_request(self, request: AgentRunRequest) -> None:
        if self._isolation_spec is None:
            return
        fingerprint = self._isolation_spec.request_fingerprint(request)
        if self._bound_request_fingerprint is None:
            self._bound_request_fingerprint = fingerprint
        elif self._bound_request_fingerprint != fingerprint:
            raise RuntimeError("isolated Codex transport cannot be reused across Mission scopes")

    async def _start(self, request: AgentRunRequest) -> None:
        # This must precede the live-process early return: a session that has
        # already started is not authorization to send a different scope/turn.
        self._validate_isolated_request(request)
        if self._process and self._process.returncode is None:
            return
        if not (Path(self.executable).is_file() or shutil.which(self.executable)):
            raise RuntimeError(f"Codex app-server executable not found: {self.executable}")
        # These are supported app-server configuration overrides.  The Codex
        # server still receives OPENAI_API_KEY only to authenticate, while its
        # shell-tool environment inherits *none* of the server environment.
        # An empty, invocation-private CODEX_HOME plus an empty MCP map means
        # workspace/project config, plugins, skills, and MCP cannot introduce a
        # credential-bearing subprocess path.
        command = [self.executable,
            "--strict-config",
            "-c", 'shell_environment_policy.inherit="none"',
            "-c", "shell_environment_policy.ignore_default_excludes=false",
            "-c", "mcp_servers={}",
            "-c", _mission_project_override(request.workspace),
            "-c", 'model_provider="openai"',
            "-c", 'openai_base_url="https://api.openai.com/v1"',
            "-c", "project_doc_max_bytes=0",
            "-c", "agents.enabled=false",
            "-c", "allow_login_shell=false",
            "-c", "check_for_update_on_startup=false",
            "--disable", "plugins",
            "--disable", "remote_plugin",
            "--disable", "recommended_plugins",
            "--disable", "apps",
            "--disable", "hooks",
            "--disable", "multi_agent",
            "--disable", "skill_search",
            "--disable", "skill_mcp_dependency_install",
            "app-server",
            "--listen",
            "stdio://",
        ]
        if self._isolation_spec is not None:
            command = [*self._isolation_spec.command_prefix(), *command]
        try:
            self._process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
            if self._process.stderr is not None:
                self._stderr_task = asyncio.create_task(self._drain_stderr(self._process.stderr))
            with _ACTIVE_CODEX_GROUPS_LOCK:
                _ACTIVE_CODEX_GROUPS.add(self._process.pid)
            await self._rpc("initialize", {"clientInfo": {"name": "cmul8", "title": "CMUL8", "version": "0.1.0"}})
            await self._send({"method": "initialized", "params": {}})
            await self._verify_mission_config(request)
            await self._disable_loaded_skills(request)
        except Exception:
            await self.close(); raise

    async def _drain_stderr(self, stream: asyncio.StreamReader) -> None:
        """Drain continuously so a verbose app-server cannot deadlock on stderr."""
        try:
            while chunk := await stream.read(8192):
                self._stderr_tail.extend(chunk)
                if len(self._stderr_tail) > 8192:
                    del self._stderr_tail[:-8192]
        except (asyncio.CancelledError, OSError):
            return

    async def _verify_mission_config(self, request: AgentRunRequest) -> None:
        """Fail closed unless the private Mission server exposes no MCP surface.

        ``config/read`` is intentionally issued before ``thread/start`` so a
        future Codex config change cannot silently restore workspace config.
        The private, empty CODEX_HOME and untrusted thread config below are the
        other half of this check.
        """
        workspace = str(request.workspace.resolve())
        config, _ = await self._rpc("config/read", {"cwd": workspace, "includeLayers": True})
        status, _ = await self._rpc("mcpServerStatus/list", {"limit": 1, "detail": "toolsAndAuthOnly"})
        effective, origins, layers = config.get("config"), config.get("origins"), config.get("layers")
        if not isinstance(effective, Mapping) or not isinstance(origins, Mapping) or (layers is not None and not isinstance(layers, list)):
            raise RuntimeError("Codex Mission config verification failed")
        # A project .codex layer is agent-controlled input.  It must either be
        # absent or explicitly disabled; a project-origin effective setting is
        # never acceptable for a Mission process.
        for layer in layers or []:
            source = layer.get("name") if isinstance(layer, Mapping) else None
            if isinstance(source, Mapping) and source.get("type") == "project" and not layer.get("disabledReason"):
                raise RuntimeError("Codex Mission project config isolation verification failed")
        session_layers = [layer for layer in layers or [] if isinstance(layer, Mapping) and isinstance(layer.get("name"), Mapping) and layer["name"].get("type") == "sessionFlags"]
        for origin in origins.values():
            source = origin.get("name") if isinstance(origin, Mapping) else None
            if isinstance(source, Mapping) and source.get("type") == "project":
                raise RuntimeError("Codex Mission project config isolation verification failed")
        projects = effective.get("projects")
        if not isinstance(projects, Mapping) or not isinstance(projects.get(workspace), Mapping) or projects[workspace].get("trust_level") != "untrusted":
            raise RuntimeError("Codex Mission project trust verification failed")
        if effective.get("model_provider") != "openai" or effective.get("openai_base_url") != "https://api.openai.com/v1":
            raise RuntimeError("Codex Mission provider isolation verification failed")
        if effective.get("model_providers") != {} or effective.get("mcp_servers") != {}:
            raise RuntimeError("Codex Mission provider isolation verification failed")
        if not any(isinstance(layer.get("config"), Mapping) and layer["config"].get("model_provider") == "openai" and layer["config"].get("openai_base_url") == "https://api.openai.com/v1" and isinstance(layer["config"].get("projects"), Mapping) and layer["config"]["projects"].get(workspace, {}).get("trust_level") == "untrusted" for layer in session_layers):
            raise RuntimeError("Codex Mission session flag verification failed")
        for key in ("model_provider", "openai_base_url"):
            origin = origins.get(key)
            source = origin.get("name") if isinstance(origin, Mapping) else None
            if not isinstance(source, Mapping) or source.get("type") != "sessionFlags":
                raise RuntimeError("Codex Mission session flag verification failed")
        if not isinstance(status.get("data"), list) or status["data"] or status.get("nextCursor") is not None:
            raise RuntimeError("Codex Mission MCP isolation verification failed")

    async def _disable_loaded_skills(self, request: AgentRunRequest) -> None:
        """Disable every resolved Codex skill before a Mission thread exists.

        Codex 0.148 can inject bundled system skills even with skill-search
        disabled.  The app-server's supported skills/config/write endpoint is
        therefore used on each fresh private Mission server, followed by an
        authoritative force-reload and fail-closed assertion.
        """
        params = {"cwds": [str(request.workspace.resolve())], "forceReload": True}
        first, _ = await self._rpc("skills/list", params)
        entries = first.get("data")
        if not isinstance(entries, list):
            raise RuntimeError("Codex Mission skill inventory verification failed")
        for entry in entries:
            if not isinstance(entry, Mapping) or not isinstance(entry.get("skills"), list) or entry.get("errors"):
                raise RuntimeError("Codex Mission skill inventory verification failed")
            for skill in entry["skills"]:
                if not isinstance(skill, Mapping) or not isinstance(skill.get("enabled"), bool) or not isinstance(skill.get("path"), str):
                    raise RuntimeError("Codex Mission skill inventory verification failed")
                if not skill["enabled"]:
                    continue
                path = Path(skill["path"])
                if not path.is_absolute() or path.name != "SKILL.md":
                    raise RuntimeError("Codex Mission skill inventory contains unsafe path")
                result, _ = await self._rpc("skills/config/write", {"path": str(path), "enabled": False})
                if result.get("effectiveEnabled") is not False:
                    raise RuntimeError("Codex Mission skill disable failed")
        final, _ = await self._rpc("skills/list", params)
        final_entries = final.get("data")
        if not isinstance(final_entries, list):
            raise RuntimeError("Codex Mission skill inventory verification failed")
        for entry in final_entries:
            if not isinstance(entry, Mapping) or entry.get("errors") or not isinstance(entry.get("skills"), list):
                raise RuntimeError("Codex Mission skill inventory verification failed")
            if any(not isinstance(skill, Mapping) or skill.get("enabled") is not False for skill in entry["skills"]):
                raise RuntimeError("Codex Mission skill isolation verification failed")

    def _remaining(self, request: AgentRunRequest) -> float:
        now = time.monotonic()
        if self._deadline is None:
            self._deadline = now + max(0.001, float(request.wall_timeout_seconds))
        return self._deadline - now

    async def _within_budget(self, request: AgentRunRequest, awaitable):
        remaining = self._remaining(request)
        if remaining <= 0:
            await self.close()
            raise TimeoutError("Codex Mission wall timeout exceeded")
        try:
            return await asyncio.wait_for(awaitable, timeout=remaining)
        except asyncio.TimeoutError as exc:
            await self.close()
            raise TimeoutError("Codex Mission wall timeout exceeded") from exc

    async def _send(self, message: Mapping[str, Any]) -> None:
        if not self._process or not self._process.stdin:
            raise RuntimeError("Codex app-server is not running")
        async with self._write_lock:
            self._process.stdin.write((json.dumps(dict(message), separators=(",", ":")) + "\n").encode())
            await self._process.stdin.drain()

    async def _rpc(self, method: str, params: Mapping[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        if not self._process or not self._process.stdout:
            raise RuntimeError("Codex app-server is not running")
        self._request_id += 1
        request_id = self._request_id
        await self._send({"method": method, "id": request_id, "params": dict(params)})
        notifications: list[dict[str, Any]] = []
        while True:
            raw = await self._process.stdout.readline()
            if not raw:
                detail = bytes(self._stderr_tail).decode("utf-8", "replace")[-1000:]
                raise RuntimeError(f"Codex app-server exited unexpectedly: {detail}".strip())
            message = json.loads(raw)
            if message.get("id") != request_id:
                notifications.append(message)
                continue
            if message.get("error"):
                error = message["error"]
                raise RuntimeError(str(error.get("message") or error))
            return dict(message.get("result") or {}), notifications

    @staticmethod
    def _snapshot(workspace: Path) -> dict[Path, tuple[int, int]]:
        result: dict[Path, tuple[int, int]] = {}
        for path in workspace.rglob("*"):
            if not path.is_file() or ".cmul8" in path.parts:
                continue
            stat = path.stat()
            result[path.relative_to(workspace)] = (stat.st_size, stat.st_mtime_ns)
        return result

    async def create_thread(self, *, request: AgentRunRequest, thread_id: str | None = None) -> str:
        async with self._run_lock:
            async def operation() -> str:
                self._validate_isolated_request(request)
                await self._start(request)
                if thread_id:
                    if request.session_mode == "ephemeral":
                        raise ValueError("ephemeral requests cannot resume Codex threads")
                    result, _ = await self._rpc("thread/resume", {"threadId": thread_id})
                else:
                    params: dict[str, Any] = {
                        "cwd": str(request.workspace.resolve()), "approvalPolicy": "never",
                        "sandbox": "workspace-write" if request.write_paths else "read-only",
                        "serviceName": "cmul8", "ephemeral": request.session_mode == "ephemeral",
                        "environments": [], "dynamicTools": [], "selectedCapabilityRoots": [],
                        "runtimeWorkspaceRoots": [str(request.workspace.resolve())],
                        # Codex's supported per-thread configuration suppresses
                        # project-local .codex rules/config and AGENTS.md.
                        "config": {"project_doc_max_bytes": 0},
                    }
                    if request.config.model.model_id != "default": params["model"] = request.config.model.model_id
                    result, _ = await self._rpc("thread/start", params)
                resolved = str((result.get("thread") or {}).get("id") or "")
                if not resolved: raise RuntimeError("Codex app-server did not return a thread id")
                return resolved
            return await self._within_budget(request, operation())

    async def run(self, *, request: AgentRunRequest, thread_id: str) -> Mapping[str, Any]:
        async with self._run_lock:
            return await self._within_budget(request, self._run_turn(request, thread_id))

    async def _run_turn(self, request: AgentRunRequest, thread_id: str) -> Mapping[str, Any]:
            self._validate_isolated_request(request)
            await self._start(request)
            if not self._process or not self._process.stdout:
                raise RuntimeError("Codex app-server is not running")
            workspace = request.workspace.resolve()
            before = self._snapshot(workspace)
            sandbox: dict[str, Any]
            if request.write_paths:
                sandbox = {
                    "type": "workspaceWrite",
					"writableRoots": [
						str((workspace / root).resolve() if not root.is_absolute() else root.resolve())
						for root in request.write_paths
					],
                    "networkAccess": request.network_policy.value == "allow",
                }
            else:
                sandbox = {"type": "readOnly", "networkAccess": False}
            params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": request.prompt}],
                "cwd": str(workspace),
                "approvalPolicy": "never",
                "sandboxPolicy": sandbox,
                "environments": [], "runtimeWorkspaceRoots": [str(workspace)],
            }
            if request.config.model.model_id != "default":
                params["model"] = request.config.model.model_id
            if request.config.model_reasoning_effort:
                params["effort"] = request.config.model_reasoning_effort
            output_schema = request.metadata.get("output_schema")
            if isinstance(output_schema, Mapping):
                params["outputSchema"] = dict(output_schema)
            result, notifications = await self._rpc("turn/start", params)
            turn_id = str((result.get("turn") or {}).get("id") or "")
            if not turn_id:
                raise RuntimeError("Codex app-server did not return a turn id")
            self._active_turns[thread_id] = turn_id
            final_chunks: list[str] = []
            completed: dict[str, Any] | None = None
            semantic_counter = _SemanticStepCounter()
            for notification in notifications:
                semantic_counter.observe(notification)
            semantic_steps = semantic_counter.steps
            try:
                # ``turn/start`` normally returns before notifications, but do
                # not let an out-of-order server notification bypass the same
                # hard limit enforced below.
                if semantic_steps > request.step_budget:
                    await self._interrupt_and_terminate(thread_id, turn_id)
                    return self._step_budget_failure(
                        notifications, semantic_steps, request.step_budget,
                        self._changed_since(workspace, before),
                    )
                while completed is None:
                    raw = await self._process.stdout.readline()
                    if not raw:
                        raise RuntimeError("Codex app-server exited during a turn")
                    message = json.loads(raw)
                    notifications.append(message)
                    method = message.get("method")
                    payload = message.get("params") or {}
                    if semantic_counter.observe(message):
                        semantic_steps = semantic_counter.steps
                        # The N+1th tool start is the admission boundary. Stop
                        # before its completion so it cannot perform a second
                        # side effect. Completion-only streams remain bounded
                        # conservatively once their N+1 completion is observed.
                        if semantic_steps > request.step_budget:
                            await self._interrupt_and_terminate(thread_id, turn_id)
                            return self._step_budget_failure(
                                notifications, semantic_steps, request.step_budget,
                                self._changed_since(workspace, before),
                            )
                    if method == "item/agentMessage/delta" and payload.get("delta"):
                        final_chunks.append(str(payload["delta"]))
                    if method == "turn/completed" and str((payload.get("turn") or {}).get("id")) == turn_id:
                        completed = dict(payload.get("turn") or {})
            finally:
                self._active_turns.pop(thread_id, None)
            changed = self._changed_since(workspace, before)
            response = "".join(final_chunks).strip() or None
            structured: dict[str, Any] = {}
            if response:
                try:
                    parsed = json.loads(response)
                    if isinstance(parsed, Mapping):
                        structured = dict(parsed)
                except json.JSONDecodeError:
                    pass
            raw_status = str(completed.get("status") or "completed").lower()
            status = TerminalStatus.SUCCEEDED if raw_status in {"completed", "succeeded"} else TerminalStatus.FAILED
            events = [
                {"action": str(item.get("method") or "codex_event"), "result": "observed"}
                for item in notifications
                if item.get("method")
            ]
            return {
                "status": status,
                "response": response,
                "structured_output": structured,
                "changed_files": changed,
                "events": events,
                "steps": semantic_steps,
                "error": completed.get("error"),
            }

    @classmethod
    def _changed_since(cls, workspace: Path, before: Mapping[Path, tuple[int, int]]) -> list[Path]:
        """Snapshot after containment so partial tool writes remain reviewable."""
        after = cls._snapshot(workspace)
        return sorted(path for path, fingerprint in after.items() if before.get(path) != fingerprint)

    @staticmethod
    def _step_budget_failure(
        notifications: list[dict[str, Any]], steps: int, limit: int, changed_files: Iterable[Path],
    ) -> Mapping[str, Any]:
        """Return a normalized terminal failure without consuming more output."""
        return {
            "status": TerminalStatus.FAILED,
            "response": None,
            "structured_output": {},
            "changed_files": tuple(changed_files),
            "events": [
                {"action": str(item.get("method") or "codex_event"), "result": "observed"}
                for item in notifications if item.get("method")
            ],
            "steps": steps,
            "error": {
                "code": "step_budget_exceeded",
                "message": f"Codex used {steps} semantic tool actions; limit is {limit}",
            },
        }

    async def _interrupt_and_terminate(self, thread_id: str, turn_id: str) -> None:
        """Bound an interrupt write, then always terminate its process group."""
        self._request_id += 1
        try:
            await asyncio.wait_for(self._send({
                "method": "turn/interrupt", "id": self._request_id,
                "params": {"threadId": thread_id, "turnId": turn_id},
            }), timeout=1)
        except (asyncio.TimeoutError, BrokenPipeError, ConnectionError, RuntimeError, OSError):
            # A dead/busy server cannot be trusted to honor the interrupt; the
            # finally block is the authoritative containment mechanism.
            pass
        finally:
            if self._process is not None:
                await self._terminate_process_group(self._process)

    async def cancel(self, *, thread_id: str) -> None:
        turn_id = self._active_turns.get(thread_id)
        if not turn_id:
            return
        await self._interrupt_and_terminate(thread_id, turn_id)

    @staticmethod
    async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
        try:
            own_group = os.getpgrp()
        except OSError:
            own_group = -1
        group = getattr(process, "pid", None)
        if not isinstance(group, int):
            return
        if os.name == "posix" and group == own_group:
            return
        def exists() -> bool:
            if os.name != "posix": return process.returncode is None
            try:
                os.killpg(group, 0); return True
            except ProcessLookupError:
                return False
        try:
            if os.name == "posix":
                if not exists(): return
                os.killpg(group, signal.SIGTERM)
            else:
                if process.returncode is not None: return
                process.terminate()
        except ProcessLookupError:
            return
        deadline = time.monotonic() + 2
        while exists() and time.monotonic() < deadline:
            try:
                await asyncio.wait_for(process.wait(), timeout=min(0.05, max(0.001, deadline - time.monotonic())))
            except asyncio.TimeoutError:
                pass
            if exists(): await asyncio.sleep(0.01)
        if exists():
            try:
                if os.name == "posix": os.killpg(group, signal.SIGKILL)
                else: process.kill()
            except ProcessLookupError:
                return
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            pass

    async def close(self) -> None:
        """Terminate this short-lived app-server without discarding thread ids."""
        process, self._process = self._process, None
        self._active_turns.clear()
        if process is None:
            if self._stderr_task is not None:
                self._stderr_task.cancel(); self._stderr_task = None
            if self._isolation_spec is not None:
                self._isolation_spec.cleanup_manifest()
            return
        stdin = getattr(process, "stdin", None)
        if stdin is not None:
            stdin.close()
        await self._terminate_process_group(process)
        with _ACTIVE_CODEX_GROUPS_LOCK:
            _ACTIVE_CODEX_GROUPS.discard(getattr(process, "pid", -1))
        if self._stderr_task is not None:
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass
            self._stderr_task = None
        if self._isolation_spec is not None:
            self._isolation_spec.cleanup_manifest()

    async def healthcheck(self) -> Mapping[str, Any]:
        available = bool(Path(self.executable).is_file() or shutil.which(self.executable))
        return {
            "harness": "codex",
            "status": "ready" if available else "unavailable",
            "available": available,
            "transport": "official_app_server",
            "reason": None if available else f"Codex executable not found: {self.executable}",
        }


class CodexHarness(AgentHarness):
    name = "codex"

    def __init__(self, transport: CodexTransport | None = None, *, model_capabilities: Iterable[ModelCapability] = (), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.transport = transport
        self._model_capabilities = tuple(model_capabilities)
        self._threads: dict[str, str] = {}

    async def _create_session(self, request: AgentRunRequest, existing: AgentSession | None) -> AgentSession:
        # Persist a placeholder session even when unavailable so callers can show a
        # coherent health state; a thread id is only claimed when the protocol made it.
        if self.transport is None:
            return AgentSession(existing.session_id if existing else str(uuid.uuid4()), request.project_id, request.role, self.name,
                                request.config.provider.provider, request.config.model.model_id,
                                request.environment_id, request.config.model_reasoning_effort, request.config.codex_profile,
                                configuration_fingerprint=request.config.execution_fingerprint(),
                                configuration_identity=request.config.persisted_identity(),
                                thread_id=existing.thread_id if existing else None, resumed=existing is not None)
        thread_id = await self.transport.create_thread(request=request, thread_id=existing.thread_id if existing else None)
        session = AgentSession(existing.session_id if existing else str(uuid.uuid4()), request.project_id, request.role, self.name,
                               request.config.provider.provider, request.config.model.model_id,
                               request.environment_id, request.config.model_reasoning_effort, request.config.codex_profile,
                               configuration_fingerprint=request.config.execution_fingerprint(),
                               configuration_identity=request.config.persisted_identity(),
                               thread_id=thread_id, resumed=existing is not None)
        self._threads[session.session_id] = thread_id
        return session

    async def _run_provider(self, request: AgentRunRequest, session: AgentSession) -> Mapping[str, Any]:
        if self.transport is None or not session.thread_id:
            return {"status": TerminalStatus.FAILED, "error": {"code": "codex_transport_unavailable", "message": "No official Codex SDK/app-server transport is installed"}, "events": []}
        result = await self.transport.run(request=request, thread_id=session.thread_id)
        return dict(result)

    async def run(self, request: AgentRunRequest):  # type: ignore[override]
        """Close per-call transports while keeping the durable app-server thread id."""
        try:
            return await super().run(request)
        finally:
            close = getattr(self.transport, "close", None)
            if close is not None:
                value = close()
                if inspect.isawaitable(value):
                    await value

    async def _cancel_provider(self, session_id: str) -> None:
        if self.transport is None or session_id not in self._threads:
            return
        await self.transport.cancel(thread_id=self._threads[session_id])

    async def healthcheck(self) -> Mapping[str, Any]:
        if self.transport is None:
            return {"harness": self.name, "status": "unavailable", "available": False,
                    "reason": "official Codex SDK/app-server transport is not installed"}
        check = getattr(self.transport, "healthcheck", None)
        if check is None:
            return {"harness": self.name, "status": "ready", "available": True, "transport": "injected_official_boundary"}
        value = check()
        return await value if inspect.isawaitable(value) else value

    def capabilities(self) -> tuple[ModelCapability, ...]:
        # Source-edit/network capability is supplied only by an official transport
        # integration that explicitly declares it; it is never inferred here.
        if self._model_capabilities:
            return self._model_capabilities
        return (ModelCapability("configured", chat=self.transport is not None),)
