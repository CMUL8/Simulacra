"""Compatibility adapter for injected legacy Prime callables/interfaces."""

from __future__ import annotations

import inspect
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Iterable

from .base import AgentHarness
from .contracts import AgentRunRequest, AgentSession, ModelCapability, TerminalStatus


class PrimeHarness(AgentHarness):
    name = "prime"

    def __init__(self, runner: Callable[..., Any] | Any | None = None, *, model_capabilities: Iterable[ModelCapability] = (), **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.runner = runner
        self._model_capabilities = tuple(model_capabilities)

    async def _create_session(self, request: AgentRunRequest, existing: AgentSession | None) -> AgentSession:
        # Legacy runners own their own session mechanics; thread_id is an opaque
        # compatibility key persisted by project and specialist role.
        return AgentSession(existing.session_id if existing else str(uuid.uuid4()), request.project_id, request.role, self.name,
                            request.config.provider.provider, request.config.model.model_id,
                            request.environment_id, request.config.model_reasoning_effort, request.config.codex_profile,
                            configuration_fingerprint=request.config.execution_fingerprint(),
                            configuration_identity=request.config.persisted_identity(),
                            thread_id=existing.thread_id if existing else f"prime:{request.project_id}:{request.role}", resumed=existing is not None)

    async def _run_provider(self, request: AgentRunRequest, session: AgentSession) -> Mapping[str, Any]:
        if self.runner is None:
            return {"status": TerminalStatus.FAILED, "error": {"code": "prime_adapter_unavailable", "message": "No legacy Prime callable was injected"}}
        fn = getattr(self.runner, "run", self.runner)
        try:
            signature = inspect.signature(fn)
        except (TypeError, ValueError):
            # Some C-extension callables do not expose a signature. Invoke once,
            # without a fallback retry that could duplicate side effects.
            value = fn(request=request, session=session)
        else:
            try:
                signature.bind(request=request, session=session)
            except TypeError:
                # Common existing wrapper shape is (prompt, ...). Binding chooses
                # this before executing the callable, not after an internal error.
                signature.bind(request.prompt)
                value = fn(request.prompt)
            else:
                value = fn(request=request, session=session)
        if inspect.isawaitable(value):
            value = await value
        if isinstance(value, Mapping):
            return dict(value)
        return {
            "status": TerminalStatus.SUCCEEDED,
            "response": getattr(value, "text", None) if value is not None else None,
            "events": getattr(value, "events", ()),
            "changed_files": getattr(value, "changed_files", ()),
            "usage": {},
        }

    async def _cancel_provider(self, session_id: str) -> None:
        target = getattr(self.runner, "cancel", None) or getattr(self.runner, "abort", None)
        if target is None:
            return
        value = target(session_id)
        if inspect.isawaitable(value):
            await value

    async def healthcheck(self) -> Mapping[str, Any]:
        return {"harness": self.name, "status": "ready" if self.runner else "unavailable", "available": self.runner is not None,
                "reason": None if self.runner else "inject a legacy Prime callable/interface"}

    def capabilities(self) -> tuple[ModelCapability, ...]:
        if self._model_capabilities:
            return self._model_capabilities
        # A generic injected callable does not prove source-edit capability.
        return (ModelCapability("legacy-prime", chat=self.runner is not None),)
