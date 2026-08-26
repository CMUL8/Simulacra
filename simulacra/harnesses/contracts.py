"""Frozen, provider-neutral contracts for CMUL8 builder harnesses.

These records intentionally contain provider configuration but never provider
credential values.  A credential may only be identified by its environment
variable name.
"""

from __future__ import annotations

import os
import re
import hashlib
import json
import math
from urllib.parse import urlsplit
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any


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


_CREDENTIAL_FRAGMENT = re.compile(r"(?:api[_-]?key|token|secret|password|credential|authorization|auth|bearer)", re.IGNORECASE)
_CREDENTIAL_VALUE = re.compile(r"(?:api[_-]?key|token|secret|password|credential|authorization)\s*[:=]", re.IGNORECASE)
_SAFE_EXTRA_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,127}")
_EXTRA_KEYS = frozenset({"request_timeout", "max_retries", "region", "api_version", "organization", "project", "deployment"})
_IDENTIFIER_EXTRA_KEYS = frozenset({"region", "api_version", "organization", "project", "deployment"})


def _contains_url_userinfo(value: str) -> bool:
    parsed = urlsplit(value)
    return bool(parsed.scheme and parsed.netloc and (parsed.username is not None or parsed.password is not None))


def _validate_endpoint(value: str) -> None:
    parsed = urlsplit(value)
    if _contains_url_userinfo(value) or parsed.query or parsed.fragment:
        raise ValueError("endpoint may not include credentials, query parameters, or fragments")
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("endpoint must be an http(s) URL without credentials")


def _safe_extra(extra: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(extra, Mapping):
        raise ValueError("provider extra must be a mapping of approved non-secret configuration")
    safe: dict[str, Any] = {}
    for key, value in extra.items():
        if not isinstance(key, str) or key not in _EXTRA_KEYS or _CREDENTIAL_FRAGMENT.search(key):
            raise ValueError("provider extra key is not an approved non-secret configuration key")
        if key == "request_timeout":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("request_timeout must be a finite positive number")
            try:
                valid_timeout = math.isfinite(value) and value > 0
            except OverflowError:
                valid_timeout = False
            if not valid_timeout:
                raise ValueError("request_timeout must be a finite positive number")
        elif key == "max_retries":
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("max_retries must be a non-negative integer")
        elif key in _IDENTIFIER_EXTRA_KEYS:
            if not isinstance(value, str) or not _SAFE_EXTRA_TEXT.fullmatch(value):
                raise ValueError(f"{key} must be a safe identifier string")
        else:  # _EXTRA_KEYS guards this, retained as a defense against future additions.
            raise ValueError("provider extra key is not explicitly typed")
        if isinstance(value, str) and (_CREDENTIAL_FRAGMENT.search(value) or _CREDENTIAL_VALUE.search(value) or _contains_url_userinfo(value)):
            raise ValueError("provider extra values may not contain credentials or URL userinfo")
        safe[key] = value
    return _readonly(safe)


@dataclass(frozen=True, slots=True)
class ModelCapability:
    model_id: str
    chat: bool = True
    tool_calling: bool = False
    structured_outputs: bool = False
    file_editing: bool = False
    patch_reliability: float | None = None
    streaming: bool = False
    context_window: int | None = None
    reasoning_controls: bool = False
    image_input: bool = False
    responses_api_compatible: bool = False
    approved_task_types: frozenset[TaskType] = field(default_factory=frozenset)
    # Compatibility aliases retained for the first adapter wave.
    architect: bool = False
    source_edit: bool = False
    network: bool = False
    structured_output: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "approved_task_types",
            frozenset(item if isinstance(item, TaskType) else TaskType(item) for item in self.approved_task_types),
        )
        if self.patch_reliability is not None and not 0 <= self.patch_reliability <= 1:
            raise ValueError("patch_reliability must be between 0 and 1")
        if self.context_window is not None and self.context_window <= 0:
            raise ValueError("context_window must be positive")
        # New registry names are canonical; aliases remain internally coherent.
        if self.source_edit and not self.file_editing:
            object.__setattr__(self, "file_editing", True)
        if self.file_editing and not self.source_edit:
            object.__setattr__(self, "source_edit", True)
        if self.structured_output and not self.structured_outputs:
            object.__setattr__(self, "structured_outputs", True)
        if self.structured_outputs and not self.structured_output:
            object.__setattr__(self, "structured_output", True)


@dataclass(frozen=True, slots=True)
class ProviderConfig:
    provider: str
    endpoint: str | None = None
    credential_env_var: str | None = None
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.provider not in _PROVIDERS:
            raise ValueError(f"Unsupported provider {self.provider!r}; expected one of {sorted(_PROVIDERS)}")
        if self.endpoint is not None and not isinstance(self.endpoint, str):
            raise ValueError("endpoint must be a string or None")
        if self.credential_env_var is not None and not isinstance(self.credential_env_var, str):
            raise ValueError("credential_env_var must be a string or None")
        if self.credential_env_var is not None and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", self.credential_env_var):
            raise ValueError("credential_env_var must be an environment-variable name")
        if self.endpoint is not None:
            _validate_endpoint(self.endpoint)
        object.__setattr__(self, "extra", _safe_extra(self.extra))

    def metadata(self) -> dict[str, Any]:
        """Safe diagnostic representation; does not dereference credentials."""
        return {
            "provider": self.provider,
            "endpoint": self.endpoint,
            "credential_env_var": self.credential_env_var,
            "extra": dict(self.extra),
        }


@dataclass(frozen=True, slots=True)
class HarnessConfig:
    harness: str = "codex"
    provider: ProviderConfig = field(default_factory=lambda: ProviderConfig("openai"))
    model: ModelCapability = field(default_factory=lambda: ModelCapability("default"))
    model_reasoning_effort: str | None = None
    codex_profile: str | None = None

    def __post_init__(self) -> None:
        if self.harness not in _HARNESSES:
            raise ValueError(f"Unsupported harness {self.harness!r}; expected one of {sorted(_HARNESSES)}")

    def with_model(self, model_id: str, *, reasoning_effort: str | None = None, codex_profile: str | None = None) -> "HarnessConfig":
        """Keep the machine-owned provider route while selecting a run model."""
        return HarnessConfig(
            harness=self.harness,
            provider=self.provider,
            model=ModelCapability(model_id),
            model_reasoning_effort=reasoning_effort,
            codex_profile=codex_profile,
        )

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "HarnessConfig":
        env = os.environ if environ is None else environ
        def setting(canonical: str, legacy: str | None, default: str = "") -> str:
            # Presence of a canonical variable is authoritative, even if it is
            # blank; aliases only support installations predating this contract.
            if canonical in env:
                return str(env[canonical]).strip() or default
            if legacy and legacy in env:
                return str(env[legacy]).strip() or default
            return default

        harness = setting("CMUL8_AGENT_HARNESS", None, "codex").lower()
        provider = setting("CMUL8_MODEL_PROVIDER", "CMUL8_AGENT_PROVIDER", "openai").lower()
        model = setting("CMUL8_MODEL", "CMUL8_AGENT_MODEL", "default")
        endpoint = setting("CMUL8_MODEL_BASE_URL", None) or None
        credential = setting("CMUL8_MODEL_API_KEY_ENV", "CMUL8_AGENT_CREDENTIAL_ENV") or None
        reasoning = setting("CMUL8_MODEL_REASONING_EFFORT", None) or None
        profile = setting("CMUL8_CODEX_PROFILE", None) or None
        # Environment configuration is production configuration. Alternate
        # harnesses remain constructible in-process for deterministic adapters.
        # An explicitly supplied mapping is likewise an internal test seam;
        # a process environment only admits it while pytest is executing.
        test_seam = environ is not None or bool(env.get("PYTEST_CURRENT_TEST"))
        if harness != "codex" and not test_seam:
            raise ValueError("production harness configuration supports the Codex runtime only")
        if provider not in {"openai", "custom"} and not test_seam:
            raise ValueError("production Codex provider must be openai or custom")
        if provider == "openai" and not test_seam:
            if endpoint not in {None, "https://api.openai.com/v1"} or credential not in {None, "OPENAI_API_KEY"}:
                raise ValueError("OpenAI provider routing is fixed to the official endpoint and credential")
        if provider == "custom" and not test_seam:
            if endpoint is None or not endpoint.startswith("https://"):
                raise ValueError("custom production model provider requires an HTTPS Responses endpoint")
            if credential not in {None, "CMUL8_MODEL_API_KEY"}:
                raise ValueError("custom provider credential must use CMUL8_MODEL_API_KEY")
        if provider == "openai":
            endpoint = endpoint or "https://api.openai.com/v1"
            credential = credential or "OPENAI_API_KEY"
        return cls(
            harness=harness,
            provider=ProviderConfig(provider, endpoint=endpoint, credential_env_var=credential),
            model=ModelCapability(model),
            model_reasoning_effort=reasoning,
            codex_profile=profile,
        )

    def metadata(self) -> dict[str, Any]:
        """Safe configuration metadata; credentials are never looked up or emitted."""
        return {
            "harness": self.harness,
            "provider": self.provider.metadata(),
            "model": self.model.model_id,
            "model_reasoning_effort": self.model_reasoning_effort,
            "codex_profile": self.codex_profile,
        }

    def configuration_identity(self) -> dict[str, Any]:
        """Canonical safe execution identity used to gate provider-thread reuse."""
        return {
            "harness": self.harness,
            "provider": self.provider.metadata(),
            "model": {
                "model_id": self.model.model_id,
                "chat": self.model.chat,
                "tool_calling": self.model.tool_calling,
                "structured_outputs": self.model.structured_outputs,
                "file_editing": self.model.file_editing,
                "patch_reliability": self.model.patch_reliability,
                "streaming": self.model.streaming,
                "context_window": self.model.context_window,
                "reasoning_controls": self.model.reasoning_controls,
                "image_input": self.model.image_input,
                "responses_api_compatible": self.model.responses_api_compatible,
                "approved_task_types": sorted(item.value for item in self.model.approved_task_types),
                "architect": self.model.architect,
                "source_edit": self.model.source_edit,
                "network": self.model.network,
                "structured_output": self.model.structured_output,
            },
            "model_reasoning_effort": self.model_reasoning_effort,
            "codex_profile": self.codex_profile,
        }

    def execution_fingerprint(self) -> str:
        payload = json.dumps(self.configuration_identity(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def persisted_identity(self) -> dict[str, Any]:
        """Safe audit identity; the credential-variable name exists only in the hash input."""
        identity = self.configuration_identity()
        identity["provider"] = {
            "provider": self.provider.provider,
            "endpoint": self.provider.endpoint,
            "extra": dict(self.provider.extra),
        }
        return identity


@dataclass(frozen=True, slots=True)
class AgentSession:
    session_id: str
    project_id: str
    role: str
    harness: str
    provider: str
    model_id: str
    environment_id: str = ""
    model_reasoning_effort: str | None = None
    codex_profile: str | None = None
    thread_id: str | None = None
    resumed: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    configuration_fingerprint: str = ""
    configuration_identity: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "configuration_identity", _readonly(self.configuration_identity))


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
    session_mode: str = "durable"
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
        if self.session_mode not in {"durable", "ephemeral"}:
            raise ValueError("session_mode must be durable or ephemeral")
        if self.session_mode == "ephemeral" and self.session_id is not None:
            raise ValueError("ephemeral requests cannot resume a durable session")


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
