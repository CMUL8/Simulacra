"""Interchangeable, deployment-owned Mission agent execution boundary."""

from __future__ import annotations

import os
import json
import re
import selectors
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

from simulacra.harnesses import (
    AgentRunRequest,
    AgentRunResult,
    HarnessConfig,
    ModelCapability,
    ProviderConfig,
    TerminalStatus,
)
from simulacra.harnesses.provider_route import ResponsesProviderRoute

if TYPE_CHECKING:
    from simulacra.harnesses.sessions import SessionRepository
    from .models import MissionRun


_EXECUTOR_ID = re.compile(r"[a-z][a-z0-9_-]{1,63}$")
_MAX_RESULT_BYTES = 2 * 1024 * 1024


class MissionAgentExecutor(ABC):
    """Certified executor contract behind durable Mission orchestration.

    Deployments may supply an implementation, but the executor never owns
    Mission state, permissions, approvals, verification, or public identity.
    It owns its process isolation and the one agent turn only.
    """

    name: str
    protocol = "mission-executor-json-v1"
    enforces_network_policy = False

    def runtime_root(self) -> "os.PathLike[str]":
        """Return the immutable, image-baked runtime owned by this adapter."""
        return Path("/opt/cmul8/executors") / self.name

    def executable_path(self) -> "os.PathLike[str]":
        return Path(self.runtime_root()) / "bin" / "mission-executor"

    @property
    def state_namespace(self) -> str:
        return f"{self.name}-state"

    def config_for(self, run: "MissionRun") -> HarnessConfig:
        persisted_route = run.execution_profile.get("model_route")
        complete_profile = (
            isinstance(run.execution_profile.get("runtime"), str)
            and isinstance(run.execution_profile.get("model"), str)
            and persisted_route is not None
        )
        base = None if complete_profile else HarnessConfig.from_env()
        backend = str(run.execution_profile.get("runtime") or base.harness)  # type: ignore[union-attr]
        provider: ProviderConfig = ResponsesProviderRoute.from_manifest(persisted_route).provider_config() if persisted_route is not None else base.provider  # type: ignore[union-attr]
        config = HarnessConfig(
            harness=backend,
            provider=provider,
            model=ModelCapability(str(run.execution_profile.get("model") or base.model.model_id or "default")),  # type: ignore[union-attr]
            model_reasoning_effort=run.execution_profile.get("reasoning_effort"),
            codex_profile=run.execution_profile.get("codex_profile"),
        )
        if config.harness != self.name:
            raise ValueError("the admitted Mission run belongs to a different execution backend")
        return config

    def route_binding(self, config: HarnessConfig) -> Mapping[str, str | None]:
        return ResponsesProviderRoute.from_config(config).to_manifest()

    def readiness_error(self, config: HarnessConfig, *, isolation_ready: bool) -> tuple[str, str] | None:
        if not self.enforces_network_policy:
            return "executor_uncertified", "The managed execution engine is not certified."
        route = ResponsesProviderRoute.from_config(config)
        if not route.credential_ready(os.environ):
            return "credential_unavailable", "The managed model route is not ready."
        if not isolation_ready:
            return "sandbox_unavailable", "Mission isolation is unavailable."
        return None

    @abstractmethod
    def execute(
        self,
        request: AgentRunRequest,
        *,
        isolation: Any,
        session_repository: "SessionRepository",
    ) -> AgentRunResult:
        """Execute one already-admitted agent turn and return normalized evidence."""


class JsonProcessMissionAgentExecutor(MissionAgentExecutor):
    """Certified provider-neutral executor using one bounded JSON stdio turn.

    The deployment image owns the executable. Missions owns admission,
    permissions, scope, time, verification, and persistence; the adapter only
    turns one immutable request into one normalized result.
    """

    protocol = "mission-executor-json-v1"
    # This adapter is only reachable through the source-controlled certified
    # registry. Certification requires the baked runtime to keep model traffic
    # inside the trusted adapter and deny network to model-invoked tools.
    enforces_network_policy = True

    def __init__(
        self,
        name: str,
        *,
        runtime_root: str | Path | None = None,
        executable: str | Path | None = None,
    ) -> None:
        if not _EXECUTOR_ID.fullmatch(name):
            raise ValueError("execution backend name is invalid")
        self.name = name
        self._runtime_root = Path(runtime_root) if runtime_root is not None else None
        self._executable = Path(executable) if executable is not None else None

    def runtime_root(self) -> Path:
        return self._runtime_root or Path(super().runtime_root())

    def executable_path(self) -> Path:
        return self._executable or Path(super().executable_path())

    @staticmethod
    def _result(
        request: AgentRunRequest,
        *,
        status: TerminalStatus,
        duration: float,
        session_id: str,
        response: str | None = None,
        structured_output: Mapping[str, Any] | None = None,
        changed_files: tuple[Path, ...] = (),
        usage: Mapping[str, Any] | None = None,
        error_code: str | None = None,
    ) -> AgentRunResult:
        return AgentRunResult(
            harness=request.config.harness,
            provider=request.config.provider.provider,
            model_id=request.config.model.model_id,
            session_id=session_id,
            status=status,
            response=response,
            structured_output=structured_output or {},
            changed_files=changed_files,
            events=(),
            duration_seconds=duration,
            usage=usage or {},
            error={"code": error_code, "message": "The managed agent turn did not complete."} if error_code else None,
            metadata={"execution_protocol": JsonProcessMissionAgentExecutor.protocol},
        )

    def execute(
        self,
        request: AgentRunRequest,
        *,
        isolation: Any,
        session_repository: "SessionRepository",
    ) -> AgentRunResult:
        del session_repository  # Durable Mission/session ownership remains outside the adapter.
        started_at = time.monotonic()
        fallback_session_id = request.session_id or f"{self.name}-turn"
        process: subprocess.Popen[bytes] | None = None
        try:
            request_fingerprint = isolation.spec.request_fingerprint(request)
            payload = {
                "schema_version": 1,
                "request_fingerprint": request_fingerprint,
                "project_id": request.project_id,
                "environment_id": request.environment_id,
                "workspace": str(request.workspace.resolve(strict=False)),
                "prompt": request.prompt,
                "role": request.role,
                "task_type": request.task_type.value,
                "read_paths": [str(path.resolve(strict=False)) for path in request.read_paths],
                "write_paths": [str(path.resolve(strict=False)) for path in request.write_paths],
                "network": request.network_policy.value,
                "wall_timeout_seconds": request.wall_timeout_seconds,
                "step_budget": request.step_budget,
                "session_id": request.session_id,
                "session_mode": request.session_mode,
                "metadata": dict(request.metadata),
                "model": {
                    "provider": request.config.provider.provider,
                    "endpoint": request.config.provider.endpoint,
                    "model_id": request.config.model.model_id,
                    "reasoning_effort": request.config.model_reasoning_effort,
                },
            }
            encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
            command = (
                *isolation.spec.command_prefix(),
                isolation.executable,
                "mission-executor",
                "--stdio",
            )
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("managed executor stdio is unavailable")
            input_fd, output_fd = process.stdin.fileno(), process.stdout.fileno()
            os.set_blocking(input_fd, False)
            os.set_blocking(output_fd, False)
            selector = selectors.DefaultSelector()
            selector.register(input_fd, selectors.EVENT_WRITE, "input")
            selector.register(output_fd, selectors.EVENT_READ, "output")
            pending = bytearray(encoded)
            input_registered = True
            input_open = True
            close_input_after_flush = False
            received = 0
            line_buffer = bytearray()
            result_value: dict[str, Any] | None = None
            admitted_action_ids: set[str] = set()
            output_open = True
            deadline = started_at + request.wall_timeout_seconds

            def accept_line(raw_line: bytes) -> None:
                nonlocal result_value, input_registered, input_open, close_input_after_flush
                if not raw_line:
                    return
                message = json.loads(raw_line.decode("utf-8"))
                if not isinstance(message, dict):
                    raise ValueError("managed executor message must be an object")
                message_type = message.get("type")
                if message_type == "action_request":
                    identifier = message.get("id")
                    if (
                        result_value is not None
                        or not isinstance(identifier, str)
                        or not identifier
                        or identifier in admitted_action_ids
                    ):
                        raise ValueError("managed executor action request is invalid")
                    if len(admitted_action_ids) >= request.step_budget:
                        raise ValueError("managed executor exceeded its admitted step budget")
                    admitted_action_ids.add(identifier)
                    pending.extend(json.dumps(
                        {"type": "action_admitted", "id": identifier},
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8") + b"\n")
                    if not input_registered:
                        selector.register(input_fd, selectors.EVENT_WRITE, "input")
                        input_registered = True
                    return
                if message_type == "result" and result_value is None:
                    result_value = message
                    close_input_after_flush = True
                    if not pending and not input_registered and input_open:
                        process.stdin.close()
                        input_open = False
                    return
                raise ValueError("managed executor protocol message is invalid")

            try:
                while output_open or process.poll() is None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise subprocess.TimeoutExpired(command, request.wall_timeout_seconds)
                    for key, event in selector.select(min(0.1, remaining)):
                        if key.data == "input" and event & selectors.EVENT_WRITE:
                            try:
                                sent = os.write(input_fd, pending[:65536]) if pending else 0
                            except (BlockingIOError, BrokenPipeError):
                                sent = 0
                            del pending[:sent]
                            if not pending:
                                selector.unregister(input_fd)
                                input_registered = False
                                if close_input_after_flush and input_open:
                                    process.stdin.close()
                                    input_open = False
                        elif key.data == "output" and event & selectors.EVENT_READ:
                            try:
                                chunk = os.read(output_fd, 65536)
                            except BlockingIOError:
                                continue
                            if not chunk:
                                selector.unregister(output_fd)
                                process.stdout.close()
                                output_open = False
                                if line_buffer:
                                    accept_line(bytes(line_buffer))
                                    line_buffer.clear()
                                continue
                            received += len(chunk)
                            if received > _MAX_RESULT_BYTES:
                                raise ValueError("managed executor output exceeded its limit")
                            line_buffer.extend(chunk)
                            while b"\n" in line_buffer:
                                raw_line, _, remainder = line_buffer.partition(b"\n")
                                line_buffer[:] = remainder
                                accept_line(bytes(raw_line))
                return_code = process.wait(timeout=max(0.01, deadline - time.monotonic()))
            finally:
                selector.close()
                if input_open:
                    process.stdin.close()
            if return_code != 0 or result_value is None:
                raise RuntimeError("managed executor failed")
            value = result_value
            status = TerminalStatus(value.get("status"))
            harness = value.get("harness")
            provider = value.get("provider")
            model_id = value.get("model_id")
            session_id = value.get("session_id")
            response = value.get("response")
            structured_output = value.get("structured_output", {})
            changed_files = value.get("changed_files", [])
            usage = value.get("usage", {})
            if (
                not all(isinstance(item, str) and item for item in (harness, provider, model_id, session_id))
                or response is not None and not isinstance(response, str)
                or not isinstance(structured_output, dict)
                or not isinstance(changed_files, list)
                or len(changed_files) > 1024
                or any(not isinstance(item, str) for item in changed_files)
                or not isinstance(usage, dict)
            ):
                raise ValueError("managed executor returned an invalid result")
            steps = usage.get("steps", 0)
            if not isinstance(steps, int) or isinstance(steps, bool) or steps < 0 or steps > request.step_budget:
                raise ValueError("managed executor exceeded its admitted step budget")
            if steps != len(admitted_action_ids):
                raise ValueError("managed executor action admissions do not match usage")
            # Preserve returned identity so MissionWorker can independently
            # reject a result that does not match admission.
            return AgentRunResult(
                harness=harness,
                provider=provider,
                model_id=model_id,
                session_id=session_id,
                status=status,
                response=response,
                structured_output=structured_output,
                changed_files=tuple(Path(item) for item in changed_files),
                events=(),
                duration_seconds=time.monotonic() - started_at,
                usage=usage,
                metadata={"execution_protocol": self.protocol},
            )
        except subprocess.TimeoutExpired:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            return self._result(
                request,
                status=TerminalStatus.TIMED_OUT,
                duration=time.monotonic() - started_at,
                session_id=fallback_session_id,
                error_code="executor_timed_out",
            )
        except Exception:
            if process is not None and process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
            return self._result(
                request,
                status=TerminalStatus.FAILED,
                duration=time.monotonic() - started_at,
                session_id=fallback_session_id,
                error_code="executor_failed",
            )
