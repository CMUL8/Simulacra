"""Deterministic harness for unit and CI coverage."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .base import AgentHarness
from .contracts import AgentRunRequest, AgentSession, ModelCapability, TerminalStatus


class FakeHarness(AgentHarness):
    name = "fake"

    async def _create_session(self, request: AgentRunRequest, existing: AgentSession | None) -> AgentSession:
        return AgentSession(existing.session_id if existing else str(uuid.uuid4()), request.project_id, request.role, self.name,
                            request.config.provider.provider, request.config.model.model_id,
                            request.environment_id, request.config.model_reasoning_effort, request.config.codex_profile,
                            configuration_fingerprint=request.config.execution_fingerprint(),
                            configuration_identity=request.config.persisted_identity(),
                            thread_id=existing.thread_id if existing else f"fake:{request.project_id}:{request.role}", resumed=existing is not None)

    async def _run_provider(self, request: AgentRunRequest, session: AgentSession) -> Mapping[str, Any]:
        fake = request.metadata.get("fake", {})
        fake = fake if isinstance(fake, Mapping) else {}
        mode = str(fake.get("mode", "success"))
        delay = float(fake.get("delay_seconds", 0))
        if delay:
            await asyncio.sleep(delay)
        if mode == "timeout":
            # Real timeout behaviour is exercised without sleeping for minutes.
            await asyncio.sleep(request.wall_timeout_seconds + 0.01)
        if mode == "cancelled":
            return {"cancelled": True, "events": [{"type": "fake_cancelled"}], "steps": 1}
        if mode == "error":
            return {"status": TerminalStatus.FAILED, "error": {"code": "fake_error", "message": "Deterministic fake provider error"}, "events": [{"type": "fake_error"}], "steps": 1}
        if mode == "no_artifact":
            return {"response": "Narration only", "events": [{"type": "fake_narration"}], "steps": 1}
        changes: list[Path] = []
        names = fake.get("changed_files") or ("fake-artifact.txt",)
        if mode == "success" and request.task_type.value in {"build_app", "build_workflow", "iterate"}:
            for item in names:
                path = Path(item)
                target = self._inside(request.workspace.resolve(), path, "Fake artifact escapes workspace")
                if not request.write_paths or not any(
                    self._within(target, self._inside(request.workspace.resolve(), root, "write path escapes workspace"))
                    for root in request.write_paths
                ):
                    raise PermissionError(f"Fake artifact is outside authorized write paths: {path}")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(str(fake.get("content", "fake durable artifact\n")), encoding="utf-8")
                changes.append(target)
        return {"status": TerminalStatus.SUCCEEDED, "response": str(fake.get("response", "fake response")),
                "structured_output": {"fake": True, "mode": mode}, "changed_files": changes,
                "events": [{"type": "fake_step", "status": "completed"}], "steps": int(fake.get("steps", 1))}

    async def healthcheck(self) -> Mapping[str, Any]:
        return {"harness": self.name, "status": "healthy", "available": True, "deterministic": True}

    def capabilities(self) -> tuple[ModelCapability, ...]:
        return (ModelCapability("fake", chat=True, architect=True, source_edit=True, network=False, structured_output=True),)
