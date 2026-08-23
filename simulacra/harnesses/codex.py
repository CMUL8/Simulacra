"""Codex adapter over an injected official SDK/app-server transport.

No command line is launched and no terminal output is parsed here.  Product code
must supply a transport backed by the official Codex SDK or app-server protocol.
"""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Mapping
from typing import Any, Iterable, Protocol, runtime_checkable

from .base import AgentHarness
from .contracts import AgentRunRequest, AgentSession, ModelCapability, TerminalStatus


@runtime_checkable
class CodexTransport(Protocol):
    """Minimal official-boundary protocol; implementations are integration-owned."""

    async def create_thread(self, *, request: AgentRunRequest, thread_id: str | None = None) -> str: ...
    async def run(self, *, request: AgentRunRequest, thread_id: str) -> Mapping[str, Any]: ...
    async def cancel(self, *, thread_id: str) -> None: ...


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
                                thread_id=existing.thread_id if existing else None, resumed=existing is not None)
        thread_id = await self.transport.create_thread(request=request, thread_id=existing.thread_id if existing else None)
        session = AgentSession(existing.session_id if existing else str(uuid.uuid4()), request.project_id, request.role, self.name,
                               request.config.provider.provider, request.config.model.model_id,
                               request.environment_id, request.config.model_reasoning_effort, request.config.codex_profile,
                               thread_id=thread_id, resumed=existing is not None)
        self._threads[session.session_id] = thread_id
        return session

    async def _run_provider(self, request: AgentRunRequest, session: AgentSession) -> Mapping[str, Any]:
        if self.transport is None or not session.thread_id:
            return {"status": TerminalStatus.FAILED, "error": {"code": "codex_transport_unavailable", "message": "No official Codex SDK/app-server transport is installed"}, "events": []}
        result = await self.transport.run(request=request, thread_id=session.thread_id)
        return dict(result)

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
