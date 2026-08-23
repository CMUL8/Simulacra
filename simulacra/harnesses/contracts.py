"""Frozen, provider-neutral contracts for CMUL8 builder harnesses.

These records intentionally contain provider configuration but never provider
credential values.  A credential may only be identified by its environment
variable name.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Mapping


class TaskType(str, Enum):
    CHAT = "chat"
    ARCHITECT = "architect"
    BUILD_APP = "build_app"
    BUILD_WORKFLOW = "build_workflow"
    CONFIGURE_AGENT = "configure_agent"
    QA = "qa"
    RESEARCH = "research"
    ITERATE = "iterate"
    REPAIR = "repair"


class TerminalStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMED_OUT = "timed_out"


class NetworkPolicy(str, Enum):
    DENY = "deny"
    ALLOW = "allow"
    DECLARE_ONLY = "declare_only"


_PROVIDERS = frozenset({"openai", "ollama", "lmstudio", "custom"})
_HARNESSES = frozenset({"codex", "prime", "fake"})


def _readonly(values: Mapping[str, Any] | None) -> Mapping[str, Any]:
    return MappingProxyType(dict(values or {}))


@dataclass(frozen=True, slots=True)
class ModelCapability:
    model_id: str
    chat: bool = True
    architect: bool = False
    source_edit: bool = False
    network: bool = False
    structured_output: bool = False


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider: str
    endpoint: str | None = None
    credential_env_var: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.provider not in _PROVIDERS:
            raise ValueError(f"Unsupported provider {self.provider!r}; expected one of {sorted(_PROVIDERS)}")
        if self.credential_env_var and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.credential_env_var):
            raise ValueError("credential_env_var must be an environment-variable name")
        object.__setattr__(self, "extra", _readonly(self.extra))

    def metadata(self) -> dict[str, Any]:
        """Safe diagnostic representation; does not dereference credentials."""
        return {"provider": self.provider, "endpoint": self.endpoint, "credential_env_var": self.credential_env_var}


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    harness: str = "codex"
    provider: ProviderConfig = field(default_factory=lambda: ProviderConfig("openai"))
    model: ModelCapability = field(default_factory=lambda: ModelCapability("default"))

    def __post_init__(self) -> None:
        if self.harness not in _HARNESSES:
            raise ValueError(f"Unsupported harness {self.harness!r}; expected one of {sorted(_HARNESSES)}")

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "HarnessConfig":
        env = os.environ if environ is None else environ
        harness = env.get("CMUL8_AGENT_HARNESS", "codex").strip().lower() or "codex"
        provider = env.get("CMUL8_AGENT_PROVIDER", "openai").strip().lower() or "openai"
        model = env.get("CMUL8_AGENT_MODEL", "default").strip() or "default"
        credential = env.get("CMUL8_AGENT_CREDENTIAL_ENV", "").strip() or None
        return cls(harness=harness, provider=ProviderConfig(provider, credential_env_var=credential), model=ModelCapability(model))


@dataclass(frozen=True, slots=True)
class AgentSession:
    session_id: str
    project_id: str
    role: str
    harness: str
    provider: str
    model_id: str
    thread_id: str | None = None
    resumed: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True, slots=True)
class AgentRunRequest:
    project_id: str
    environment_id: str
    workspace: Path
    prompt: str
    role: str
    task_type: TaskType
    read_paths: tuple[Path, ...] = ()
    write_paths: tuple[Path, ...] = ()
    network_policy: NetworkPolicy = NetworkPolicy.DENY
    wall_timeout_seconds: float = 600.0
    step_budget: int = 100
    config: HarnessConfig = field(default_factory=HarnessConfig.from_env)
    trace_context: Mapping[str, str] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    session_id: str | None = None
    required_artifact_paths: tuple[Path, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "workspace", Path(self.workspace))
        if not isinstance(self.task_type, TaskType):
            object.__setattr__(self, "task_type", TaskType(self.task_type))
        if not isinstance(self.network_policy, NetworkPolicy):
            object.__setattr__(self, "network_policy", NetworkPolicy(self.network_policy))
        object.__setattr__(self, "read_paths", tuple(Path(path) for path in self.read_paths))
        object.__setattr__(self, "write_paths", tuple(Path(path) for path in self.write_paths))
        object.__setattr__(self, "required_artifact_paths", tuple(Path(path) for path in self.required_artifact_paths))
        object.__setattr__(self, "trace_context", _readonly(self.trace_context))
        object.__setattr__(self, "metadata", _readonly(self.metadata))
        if not self.prompt.strip():
            raise ValueError("prompt must not be empty")
        if self.wall_timeout_seconds <= 0:
            raise ValueError("wall_timeout_seconds must be positive")
        if self.step_budget <= 0:
            raise ValueError("step_budget must be positive")


@dataclass(frozen=True, slots=True)
class AgentEvent:
    id: str
    action: str
    result: str
    timestamp: datetime
    actor_type: str
    actor_id: str
    project_id: str
    environment_id: str
    correlation_id: str
    trace_id: str | None = None
    payload: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _readonly(self.payload))


@dataclass(frozen=True, slots=True)
class AgentRunResult:
    harness: str
    provider: str
    model_id: str
    session_id: str
    status: TerminalStatus
    response: str | None
    structured_output: Mapping[str, Any]
    changed_files: tuple[Path, ...]
    events: tuple[AgentEvent, ...]
    duration_seconds: float
    usage: Mapping[str, Any]
    error: Mapping[str, Any] | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "changed_files", tuple(Path(path) for path in self.changed_files))
        object.__setattr__(self, "structured_output", _readonly(self.structured_output))
        object.__setattr__(self, "usage", _readonly(self.usage))
        object.__setattr__(self, "error", _readonly(self.error) if self.error else None)
        object.__setattr__(self, "metadata", _readonly(self.metadata))
