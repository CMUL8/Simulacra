"""Codex adapter over the official ``codex app-server`` JSONL protocol.

The adapter intentionally launches the official app-server and parses its
structured JSONL protocol; it never treats terminal output as a product API.
"""

from __future__ import annotations

import inspect
import asyncio
import json
import os
import shutil
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Iterable, Protocol, runtime_checkable

from .base import AgentHarness
from .contracts import AgentRunRequest, AgentSession, ModelCapability, TerminalStatus


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

    def __init__(self, *, executable: str | None = None) -> None:
        self.executable = executable or os.environ.get("CMUL8_CODEX_BIN", "codex")
        self._process: asyncio.subprocess.Process | None = None
        self._request_id = 0
        self._run_lock = asyncio.Lock()
        self._write_lock = asyncio.Lock()
        self._active_turns: dict[str, str] = {}

    async def _start(self) -> None:
        if self._process and self._process.returncode is None:
            return
        if not (Path(self.executable).is_file() or shutil.which(self.executable)):
            raise RuntimeError(f"Codex app-server executable not found: {self.executable}")
        self._process = await asyncio.create_subprocess_exec(
            self.executable,
            "app-server",
            "--listen",
            "stdio://",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await self._rpc(
            "initialize",
            {"clientInfo": {"name": "cmul8", "title": "CMUL8", "version": "0.1.0"}},
        )
        await self._send({"method": "initialized", "params": {}})

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
                detail = ""
                if self._process.stderr:
                    detail = (await self._process.stderr.read()).decode("utf-8", "replace")[-1000:]
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
            await self._start()
            if thread_id:
                if request.session_mode == "ephemeral":
                    raise ValueError("ephemeral requests cannot resume Codex threads")
                result, _ = await self._rpc("thread/resume", {"threadId": thread_id})
            else:
                params: dict[str, Any] = {
                    "cwd": str(request.workspace.resolve()),
                    "approvalPolicy": "never",
                    # Codex 0.148's thread/start schema deliberately uses the
                    # legacy dashed enum; turn/start below uses camelCase.
                    "sandbox": "workspace-write" if request.write_paths else "read-only",
                    "serviceName": "cmul8",
                    # Thread/session persistence is distinct from the sandbox.
                    # Read-only product chat must not create an app-server thread
                    # that can later be resumed as a durable session.
                    "ephemeral": request.session_mode == "ephemeral",
                }
                if request.config.model.model_id != "default":
                    params["model"] = request.config.model.model_id
                result, _ = await self._rpc("thread/start", params)
            resolved = str((result.get("thread") or {}).get("id") or "")
            if not resolved:
                raise RuntimeError("Codex app-server did not return a thread id")
            return resolved

    async def run(self, *, request: AgentRunRequest, thread_id: str) -> Mapping[str, Any]:
        async with self._run_lock:
            await self._start()
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
                sandbox = {"type": "readOnly", "access": {"type": "fullAccess"}}
            params: dict[str, Any] = {
                "threadId": thread_id,
                "input": [{"type": "text", "text": request.prompt}],
                "cwd": str(workspace),
                "approvalPolicy": "never",
                "sandboxPolicy": sandbox,
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
            try:
                while completed is None:
                    raw = await self._process.stdout.readline()
                    if not raw:
                        raise RuntimeError("Codex app-server exited during a turn")
                    message = json.loads(raw)
                    notifications.append(message)
                    method = message.get("method")
                    payload = message.get("params") or {}
                    if method == "item/agentMessage/delta" and payload.get("delta"):
                        final_chunks.append(str(payload["delta"]))
                    if method == "turn/completed" and str((payload.get("turn") or {}).get("id")) == turn_id:
                        completed = dict(payload.get("turn") or {})
            finally:
                self._active_turns.pop(thread_id, None)
            after = self._snapshot(workspace)
            changed = sorted(path for path, fingerprint in after.items() if before.get(path) != fingerprint)
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
                "steps": len(events),
                "error": completed.get("error"),
            }

    async def cancel(self, *, thread_id: str) -> None:
        turn_id = self._active_turns.get(thread_id)
        if not turn_id:
            return
        self._request_id += 1
        await self._send({
            "method": "turn/interrupt",
            "id": self._request_id,
            "params": {"threadId": thread_id, "turnId": turn_id},
        })

    async def close(self) -> None:
        """Terminate this short-lived app-server without discarding thread ids."""
        process, self._process = self._process, None
        self._active_turns.clear()
        if process is None or process.returncode is not None:
            return
        if process.stdin is not None:
            process.stdin.close()
        process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=2)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()

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
