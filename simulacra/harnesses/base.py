"""Boundary enforcement and event/result normalization for harness adapters."""

from __future__ import annotations

import asyncio
import inspect
import time
import uuid
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .contracts import (
    AgentEvent,
    AgentRunRequest,
    AgentRunResult,
    AgentSession,
    ModelCapability,
    TerminalStatus,
    TaskType,
)
from .sessions import JsonSessionRepository, SessionRepository


class AgentHarness(ABC):
    """Provider-neutral asynchronous harness contract.

    Adapters return plain mappings internally; this class converts them to frozen
    public records and applies containment, budget, cancellation and artifact rules.
    """

    name: str

    def __init__(self, *, session_repository: SessionRepository | None = None) -> None:
        self._session_repository = session_repository
        self._events: dict[str, list[AgentEvent]] = {}
        self._cancelled: set[str] = set()
        self._active: set[str] = set()

    async def create_session(self, request: AgentRunRequest) -> AgentSession:
        self._validate_request(request)
        if request.session_mode == "ephemeral":
            raise ValueError("ephemeral requests cannot create durable sessions")
        session = await self._create_session(request, None)
        self._repository(request).save(session)
        return session

    async def resume_session(self, request: AgentRunRequest) -> AgentSession:
        self._validate_request(request)
        if request.session_mode == "ephemeral":
            raise ValueError("ephemeral requests cannot resume durable sessions")
        persisted = self._repository(request).get(request.project_id, request.role)
        if persisted is None:
            raise LookupError(f"No session exists for project={request.project_id!r}, role={request.role!r}")
        if persisted.harness != self.name:
            raise ValueError(f"Session belongs to {persisted.harness!r}, not selected harness {self.name!r}")
        self._verify_session_identity(request, persisted)
        session = await self._create_session(request, persisted)
        self._repository(request).save(session)
        return session

    async def run(self, request: AgentRunRequest) -> AgentRunResult:
        started = time.monotonic()
        try:
            self._validate_request(request)
        except (ValueError, PermissionError) as exc:
            session = self._ephemeral_session(request)
            return self._failure(request, session, "policy_denied", str(exc), started)

        try:
            session = await self._session_for_run(request)
        except Exception as exc:  # adapter session failures must be normalized
            session = self._ephemeral_session(request)
            return self._failure(request, session, "session_unavailable", str(exc), started)
        # Cancellation is scoped to an active invocation. A persisted session is
        # reusable after a cancelled run, so stale markers cannot carry forward.
        self._cancelled.discard(session.session_id)
        self._active.add(session.session_id)
        events = [self._event(request, session, "run_started", "started", {"selection": self._selection(request)})]
        try:
            raw = await asyncio.wait_for(self._run_provider(request, session), timeout=request.wall_timeout_seconds)
        except asyncio.TimeoutError:
            self._cancelled.add(session.session_id)
            await self._safe_cancel_provider(session)
            events.append(self._event(request, session, "run_finished", "timed_out", {}))
            return self._result(request, session, TerminalStatus.TIMED_OUT, None, {}, (), events, started,
                                error={"code": "wall_timeout", "message": "Harness wall timeout exceeded"})
        except asyncio.CancelledError:
            self._cancelled.add(session.session_id)
            await self._safe_cancel_provider(session)
            events.append(self._event(request, session, "run_finished", "cancelled", {}))
            return self._result(request, session, TerminalStatus.CANCELLED, None, {}, (), events, started,
                                error={"code": "cancelled", "message": "Run task was cancelled"})
        except Exception as exc:  # adapters must not leak provider-specific exceptions
            events.append(self._event(request, session, "run_finished", "failed", {}))
            return self._result(request, session, TerminalStatus.FAILED, None, {}, (), events, started,
                                error={"code": "provider_error", "message": str(exc)})
        finally:
            self._active.discard(session.session_id)

        raw_events = raw.get("events", ()) if isinstance(raw, Mapping) else ()
        events.extend(self._normalize_events(request, session, raw_events))
        if session.session_id in self._cancelled or raw.get("cancelled"):
            events.append(self._event(request, session, "run_finished", "cancelled", {}))
            return self._result(request, session, TerminalStatus.CANCELLED, raw.get("response"), raw.get("structured_output", {}), (), events, started,
                                error={"code": "cancelled", "message": "Run was cancelled"})
        steps = int(raw.get("steps", 0))
        if steps > request.step_budget:
            events.append(self._event(request, session, "budget_enforced", "failed", {"steps": steps, "limit": request.step_budget}))
            return self._result(request, session, TerminalStatus.FAILED, raw.get("response"), raw.get("structured_output", {}), (), events, started,
                                error={"code": "step_budget_exceeded", "message": f"Provider used {steps} steps; limit is {request.step_budget}"})
        status = TerminalStatus(raw.get("status", TerminalStatus.SUCCEEDED))
        changed = tuple(Path(value) for value in raw.get("changed_files", ()))
        try:
            self._validate_changed_files(request, changed)
            if status is TerminalStatus.SUCCEEDED:
                self._validate_artifacts(request, changed)
        except PermissionError as exc:
            events.append(self._event(request, session, "artifact_validation", "failed", {}))
            return self._result(request, session, TerminalStatus.FAILED, raw.get("response"), raw.get("structured_output", {}), changed, events, started,
                                error={"code": "artifact_validation", "message": str(exc)})
        error = raw.get("error")
        if status is TerminalStatus.SUCCEEDED:
            events.append(self._event(request, session, "run_finished", "succeeded", {"changed_files": len(changed)}))
        else:
            events.append(self._event(request, session, "run_finished", status.value, {}))
        return self._result(request, session, status, raw.get("response"), raw.get("structured_output", {}), changed, events, started,
                            usage={"steps": steps, **dict(raw.get("usage", {}))}, error=error)

    async def cancel(self, session_id: str) -> bool:
        if session_id not in self._active:
            return False
        self._cancelled.add(session_id)
        await self._safe_cancel_provider_id(session_id)
        return True

    async def stream_events(self, session_id: str) -> AsyncIterator[AgentEvent]:
        # Completed events are intentionally retained in-process for callers that
        # attach after a run; durable event projection remains integration-owned.
        for event in tuple(self._events.get(session_id, ())):
            yield event

    async def healthcheck(self) -> Mapping[str, Any]:
        return {"harness": self.name, "status": "healthy", "available": True}

    def capabilities(self) -> tuple[ModelCapability, ...]:
        return ()

    @abstractmethod
    async def _create_session(self, request: AgentRunRequest, existing: AgentSession | None) -> AgentSession: ...

    @abstractmethod
    async def _run_provider(self, request: AgentRunRequest, session: AgentSession) -> Mapping[str, Any]: ...

    def _repository(self, request: AgentRunRequest) -> SessionRepository:
        return self._session_repository or JsonSessionRepository(request.workspace)

    def _ephemeral_session(self, request: AgentRunRequest) -> AgentSession:
        return AgentSession(
            str(uuid.uuid4()), request.project_id, request.role, self.name,
            request.config.provider.provider, request.config.model.model_id,
            request.environment_id, request.config.model_reasoning_effort, request.config.codex_profile,
            configuration_fingerprint=request.config.execution_fingerprint(),
            configuration_identity=request.config.persisted_identity(),
        )

    async def _session_for_run(self, request: AgentRunRequest) -> AgentSession:
        if request.session_mode == "ephemeral":
            return await self._create_session(request, None)
        if request.session_id:
            stored = self._repository(request).get(request.project_id, request.role)
            if stored and stored.session_id == request.session_id:
                self._verify_session_identity(request, stored)
                return await self._create_session(request, stored)
            raise LookupError("Requested session_id is not the persisted project/role session")
        stored = self._repository(request).get(request.project_id, request.role)
        if stored:
            self._verify_session_identity(request, stored)
            return await self._create_session(request, stored)
        return await self.create_session(request)

    def _verify_session_identity(self, request: AgentRunRequest, session: AgentSession) -> None:
        expected = (
            self.name,
            request.config.provider.provider,
            request.config.model.model_id,
            request.environment_id,
            request.config.model_reasoning_effort,
            request.config.codex_profile,
            request.config.execution_fingerprint(),
        )
        actual = (
            session.harness,
            session.provider,
            session.model_id,
            session.environment_id,
            session.model_reasoning_effort,
            session.codex_profile,
            session.configuration_fingerprint,
        )
        if actual != expected:
            raise ValueError("Persisted session configuration identity does not match the requested run")

    def _validate_request(self, request: AgentRunRequest) -> None:
        if request.config.harness != self.name:
            raise ValueError(f"Selected harness is {request.config.harness!r}; this adapter is {self.name!r}. No fallback is attempted.")
        workspace = request.workspace.resolve()
        if not workspace.exists() or not workspace.is_dir():
            raise ValueError("workspace must be an existing directory")
        if request.network_policy.value not in {"deny", "allow", "declare_only"}:
            raise ValueError("network_policy must be explicitly declared")
        for path in (*request.read_paths, *request.write_paths, *request.required_artifact_paths):
            self._inside(workspace, path, "Path escapes workspace")
        if request.metadata.get("actor_type") == "runtime_agent" and request.write_paths:
            raise PermissionError("runtime agents cannot receive source-edit capability")

    def _validate_changed_files(self, request: AgentRunRequest, changed: tuple[Path, ...]) -> None:
        workspace = request.workspace.resolve()
        for path in changed:
            resolved = self._inside(workspace, path, "Provider reported a path outside workspace")
            if not request.write_paths:
                raise PermissionError("Provider reported a write but no write_paths were authorized")
            if not any(self._within(resolved, self._inside(workspace, root, "write path escapes workspace")) for root in request.write_paths):
                raise PermissionError(f"Provider wrote outside authorized write paths: {path}")

    def _validate_artifacts(self, request: AgentRunRequest, changed: tuple[Path, ...]) -> None:
        if request.task_type not in {TaskType.BUILD_APP, TaskType.BUILD_WORKFLOW, TaskType.ITERATE}:
            return
        if not changed:
            raise PermissionError("Build/iterate success requires durable artifact changes; narration alone is not success")
        workspace = request.workspace.resolve()
        for path in changed:
            if not self._inside(workspace, path, "changed artifact escapes workspace").is_file():
                raise PermissionError(f"Changed artifact is not durable: {path}")
        for artifact in request.required_artifact_paths:
            full = self._inside(workspace, artifact, "required artifact escapes workspace")
            if not full.is_file() or not any(self._inside(workspace, item, "changed artifact escapes workspace") == full for item in changed):
                raise PermissionError(f"Required artifact was not changed: {artifact}")

    @staticmethod
    def _within(path: Path, root: Path) -> bool:
        return path == root or root in path.parents

    @staticmethod
    def _inside(workspace: Path, path: Path, message: str) -> Path:
        candidate = (workspace / path).resolve() if not path.is_absolute() else path.resolve()
        if candidate != workspace and workspace not in candidate.parents:
            raise PermissionError(f"{message}: {path}")
        return candidate

    def _selection(self, request: AgentRunRequest) -> dict[str, str]:
        return {"selected_harness": self.name, "provider": request.config.provider.provider, "model": request.config.model.model_id, "fallback": "none"}

    def _event(self, request: AgentRunRequest, session: AgentSession, action: str, result: str, payload: Mapping[str, Any]) -> AgentEvent:
        event = AgentEvent(
            id=f"evt_{uuid.uuid4().hex}", action=action, result=result, timestamp=datetime.now(timezone.utc),
            actor_type="builder_agent", actor_id=session.session_id, project_id=request.project_id,
            environment_id=request.environment_id, correlation_id=request.trace_context.get("correlation_id", session.session_id),
            trace_id=request.trace_context.get("trace_id"), payload=payload,
        )
        self._events.setdefault(session.session_id, []).append(event)
        return event

    def _normalize_events(self, request: AgentRunRequest, session: AgentSession, raw_events: Any) -> list[AgentEvent]:
        if not isinstance(raw_events, (list, tuple)):
            return []
        normalized: list[AgentEvent] = []
        for raw in raw_events:
            if not isinstance(raw, Mapping):
                continue
            action = str(raw.get("action") or raw.get("type") or "provider_event")
            result = str(raw.get("result") or raw.get("status") or "observed")
            payload = {str(key): value for key, value in raw.items() if key not in {"action", "type", "result", "status"}}
            normalized.append(self._event(request, session, action, result, payload))
        return normalized

    def _failure(self, request: AgentRunRequest, session: AgentSession, code: str, message: str, started: float) -> AgentRunResult:
        event = self._event(request, session, "run_finished", "failed", {"code": code})
        return self._result(request, session, TerminalStatus.FAILED, None, {}, (), [event], started, error={"code": code, "message": message})

    def _result(self, request: AgentRunRequest, session: AgentSession, status: TerminalStatus, response: str | None,
                structured: Mapping[str, Any], changed: tuple[Path, ...], events: list[AgentEvent], started: float,
                usage: Mapping[str, Any] | None = None, error: Mapping[str, Any] | None = None) -> AgentRunResult:
        return AgentRunResult(self.name, request.config.provider.provider, request.config.model.model_id, session.session_id,
                              status, response, structured, changed, tuple(events), time.monotonic() - started,
                              dict(usage or {}), error, self._selection(request))

    async def _safe_cancel_provider(self, session: AgentSession) -> None:
        await self._safe_cancel_provider_id(session.session_id)

    async def _safe_cancel_provider_id(self, session_id: str) -> None:
        candidate = getattr(self, "_cancel_provider", None)
        if candidate is None:
            return
        value = candidate(session_id)
        if inspect.isawaitable(value):
            await value
